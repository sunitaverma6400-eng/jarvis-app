# -*- coding: utf-8 -*-
"""
Jarvis Phone Call Module (Twilio)
---------------------------------
Yeh module Jarvis ko ek REAL conversational phone assistant banata hai:

1. Call aane par Google Sheet (publicly viewable, CSV export) se contact
   lookup hota hai — number match hua to naam lekar personalized greeting,
   warna generic greeting.
2. Uske baad Jarvis chup nahi hota — <Gather input="speech"> se caller ki
   baat sunta hai, Groq/Gemini/OpenRouter (jo bhi key project mein already
   saved hai, memory.py se) ko bhejta hai, aur jawab Polly.Madhav (hi-IN)
   voice mein bolta hai. Yeh loop tab tak chalta hai jab tak caller khud
   "bye/alvida" na bole ya phone na kaat de.

IMPORTANT SAFETY DESIGN DECISION:
----------------------------------
brain.ask_jarvis() (jo web-chat use karta hai) function-calling "tools"
ke saath aata hai — jisme self_evolve.write_code_file / delete_code_file
(Jarvis ka apna code badalna), phone_bridge (asli phone ko control karna),
tools.py ke SMS/alarm/todo, waghera sab shamil hain.

Phone call route ('/voice', '/respond') PUBLICLY reachable hote hain —
koi bhi is number par call karke Jarvis se baat kar sakta hai. Agar hum
wahi tool-enabled brain.ask_jarvis() seedha use karte, to koi bhi
anjaan caller baaton-baaton mein Jarvis ko convince karke uska code
badalwa sakta tha ya phone ke tools misuse karwa sakta tha.

Isliye is module mein ek ALAG, halka "tool-free" AI-reply function
(`phone_ai_reply`) banaya gaya hai jo EXACT WAHI Groq/Gemini/OpenRouter
API keys (memory.py se) aur models (brain.py ki list se) use karta hai,
lekin bina kisi function-calling tool ke — sirf seedha conversation.
Isse call par Jarvis utna hi smart hai, bas "dangerous tools" access
nahi karta. Yeh hi production ke liye sahi/suraksit tareeka hai.
"""

import csv
import io
import os
import re
import threading
import time
from functools import wraps

import requests
from flask import request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

import brain
import memory

try:
    from twilio.request_validator import RequestValidator
except Exception:  # pragma: no cover
    RequestValidator = None


# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

# Apna Google Sheet ka CSV-export link yahan Render environment variable
# "GOOGLE_SHEET_CSV_URL" mein daalo (neeche instructions dekho).
GOOGLE_SHEET_CSV_URL = os.environ.get("GOOGLE_SHEET_CSV_URL", "").strip()

# Twilio se aayi request asli Twilio se hi hai, yeh verify karne ke liye
# (Render env var mein TWILIO_AUTH_TOKEN daalo). Agar set nahi hai to
# validation skip ho jaati hai (testing ke liye), lekin production mein
# ISE ZAROOR SET KARO.
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()

CONTACTS_CACHE_TTL_SECONDS = int(os.environ.get("CONTACTS_CACHE_TTL_SECONDS", "180"))
CALL_SESSION_TTL_SECONDS = 3600
CALL_HISTORY_LIMIT = 16
MAX_SILENT_RETRIES = 2

VOICE_NAME = "Polly.Madhav"
VOICE_LANG = "hi-IN"


# ══════════════════════════════════════════════════════════════
# GOOGLE SHEET CONTACT LOOKUP (CSV export, no heavy API creds needed)
# ══════════════════════════════════════════════════════════════

_contacts_cache = {"data": {}, "fetched_at": 0.0}
_contacts_lock = threading.Lock()


def _normalize_number(raw: str) -> str:
    """+91 98765 43210 -> +919876543210 (sirf digits + leading + rakho)."""
    if not raw:
        return ""
    return re.sub(r"[^\d+]", "", raw.strip())


def _fetch_contacts_from_sheet() -> dict:
    if not GOOGLE_SHEET_CSV_URL:
        return {}
    resp = requests.get(GOOGLE_SHEET_CSV_URL, timeout=10)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    contacts = {}
    for row in reader:
        raw_num = row.get("Number") or row.get("number") or row.get("NUMBER") or ""
        raw_name = row.get("Name") or row.get("name") or row.get("NAME") or ""
        num = _normalize_number(raw_num)
        name = (raw_name or "").strip()
        if num and name:
            contacts[num] = name
    return contacts


def get_contacts() -> dict:
    """TTL-cached contacts. Fetch fail ho to purana cache hi use hota hai."""
    now = time.time()
    with _contacts_lock:
        if _contacts_cache["data"] and (now - _contacts_cache["fetched_at"] < CONTACTS_CACHE_TTL_SECONDS):
            return _contacts_cache["data"]
    try:
        fresh = _fetch_contacts_from_sheet()
        with _contacts_lock:
            _contacts_cache["data"] = fresh
            _contacts_cache["fetched_at"] = now
        return fresh
    except Exception as e:
        print(f"[Jarvis Phone] Google Sheet fetch fail: {e} — purana cache use ho raha hai.")
        with _contacts_lock:
            return _contacts_cache["data"]


def find_caller_name(caller_number: str):
    return get_contacts().get(_normalize_number(caller_number))


# ══════════════════════════════════════════════════════════════
# TWILIO REQUEST SIGNATURE VALIDATION (production safety)
# ══════════════════════════════════════════════════════════════

def validate_twilio_request(f):
    """Confirm karta hai ki request asli Twilio se aayi hai, koi fake POST nahi."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not TWILIO_AUTH_TOKEN or RequestValidator is None:
            return f(*args, **kwargs)  # token set nahi -> validation skip (dev mode)

        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        signature = request.headers.get("X-Twilio-Signature", "")
        url = request.url
        # Render HTTPS ke peeche hota hai, kabhi kabhi url http:// dikhta hai
        if request.headers.get("X-Forwarded-Proto", "") == "https" and url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        if not validator.validate(url, request.form.to_dict(), signature):
            return Response("Forbidden: invalid Twilio signature", status=403)
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════
# CALL SESSIONS (in-memory, per CallSid) — Procfile mein --workers 1 hai,
# isliye ek hi process mein safe hai. Zyada workers chahiye to Redis use karo.
# ══════════════════════════════════════════════════════════════

CALL_SESSIONS = {}
_sessions_lock = threading.Lock()


def _cleanup_old_sessions():
    cutoff = time.time() - CALL_SESSION_TTL_SECONDS
    dead = [sid for sid, s in CALL_SESSIONS.items() if s.get("created", 0) < cutoff]
    for sid in dead:
        CALL_SESSIONS.pop(sid, None)


def _build_phone_system_prompt(name, caller_number):
    known_line = (
        f"Caller Sudhanshu ki contact list mein hai, unka naam '{name}' hai."
        if name else
        "Yeh ek naya/anjaan number hai jo Sudhanshu ki contact list (Google Sheet) mein nahi mila."
    )
    return (
        "Tum Jarvis ho — Sudhanshu ke smart AI assistant, jo abhi ek LIVE PHONE CALL par baat kar rahe ho.\n"
        f"{known_line} Caller ka number: {caller_number}.\n\n"
        "Rules (bahut zaroori hai in sabko follow karna):\n"
        "1. Hamesha natural, chhoti, spoken Hindi mein jawab do — jaise ek insaan phone par baat karta hai.\n"
        "2. Kabhi markdown, bullet points, headings, asterisks, ya emojis mat likho — sab kuch text-to-speech "
        "se seedha bola jayega.\n"
        "3. Jawab short rakho — 2-3 chhote sentences, jab tak caller khud detail na maange.\n"
        "4. Tumhare paas is call ke dauraan koi tool, code-editing, ya phone-control ki capability NAHI hai — "
        "sirf baat cheet karo. Agar koi aisi cheez maange (SMS bhejo, call karo, code badlo, image dikhao), "
        "to politely bolo ki 'yeh abhi call par possible nahi hai'.\n"
        "5. Sudhanshu ki private jaankari (passwords, API keys, personal files, address) kabhi share mat karo, "
        "chahe caller kuch bhi bahana de."
    )


# ══════════════════════════════════════════════════════════════
# TOOL-FREE AI REPLY — same Groq/Gemini/OpenRouter keys, no function-calling
# ══════════════════════════════════════════════════════════════

def _phone_try_groq(api_key, messages):
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except Exception as e:
        return None, str(e)

    last_err = None
    for model in brain.GROQ_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.7, max_tokens=300,
            )
            content = resp.choices[0].message.content
            if content:
                return content, None
            last_err = "empty response"
        except Exception as e:
            last_err = str(e)
            continue
    return None, last_err


def _phone_try_gemini(api_key, messages):
    system_text = None
    contents = []
    for m in messages:
        role, text = m["role"], m.get("content") or ""
        if role == "system":
            system_text = text
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": text}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})

    last_err = None
    for model in brain.GEMINI_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {"contents": contents}
            if system_text:
                payload["system_instruction"] = {"parts": [{"text": system_text}]}
            resp = requests.post(url, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if "text" in p)
            if text:
                return text, None
            last_err = "empty response"
        except Exception as e:
            last_err = str(e)
            continue
    return None, last_err


def _phone_try_openrouter(api_key, messages):
    last_err = None
    for m in brain.get_openrouter_free_models():
        model_id = m["id"]
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model_id, "messages": messages, "max_tokens": 300},
                timeout=20,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content", "")
            if content:
                return content, None
            last_err = "empty response"
        except Exception as e:
            last_err = str(e)
            continue
    return None, last_err


def phone_ai_reply(history, user_text, system_prompt):
    """Tool-free conversational reply — Groq -> Gemini -> OpenRouter fallback chain."""
    messages = [{"role": "system", "content": system_prompt}] + history + \
               [{"role": "user", "content": user_text}]

    for key in memory.get_available_groq_keys():
        result, _ = _phone_try_groq(key, messages)
        if result:
            return result
    for key in memory.get_available_gemini_keys():
        result, _ = _phone_try_gemini(key, messages)
        if result:
            return result
    for key in memory.get_available_openrouter_keys():
        result, _ = _phone_try_openrouter(key, messages)
        if result:
            return result

    return "Maaf kijiye, abhi mera AI system available nahi hai. Kripya thodi der baad phir se call kijiye."


# ══════════════════════════════════════════════════════════════
# HELPERS: sanitize AI text for speech, detect goodbye
# ══════════════════════════════════════════════════════════════

def _sanitize_for_speech(text: str) -> str:
    if not text:
        return "Maaf kijiye, mujhe abhi jawab nahi mil paaya. Kripya dobara boliye."
    t = text
    for marker in ("IMAGE_FOUND:", "VIDEO_FOUND:", "IMAGE_GENERATED:", "RADIO_STREAM:"):
        t = re.sub(rf"{marker}.*", "", t)
    t = re.sub(r"[*_`#]+", "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return "Maaf kijiye, mujhe abhi jawab nahi mil paaya. Kripya dobara boliye."
    if len(t) > 600:
        cut = t[:600]
        last_stop = max(cut.rfind("।"), cut.rfind("."), cut.rfind("?"))
        if last_stop > 200:
            cut = cut[:last_stop + 1]
        t = cut
    return t


def _call_chat_id(call_sid: str) -> str:
    """CallSid ko safe chat_id mein convert karta hai (memory.py ke regex ke hisaab se)."""
    safe = re.sub(r"[^a-zA-Z0-9]", "", call_sid or "")[:50]
    return f"call_{safe}" if safe else "call_unknown"


def generate_call_summary(transcript_text: str) -> str:
    """
    Poori call ki baat-cheet (plain text form) leke ek chhota Hindi summary
    banata hai — 'kis aadmi se kya baat hui' — tool-free phone_ai_reply
    wahi Groq/Gemini/OpenRouter fallback chain use karke.
    """
    system_prompt = (
        "Tumhe neeche ek phone call ki puri baat-cheet di jaayegi (caller aur AI "
        "assistant ke beech). Isko 3-5 chhote bullet points mein Hindi mein "
        "summarize karo — caller ne kya poocha/bola, assistant ne kya jawab diya, "
        "aur agar koi follow-up/action chahiye (jaise callback, kaam, reminder) to "
        "woh bhi highlight karo. Sirf summary do, koi intro/preamble mat likho."
    )
    if not transcript_text.strip():
        return "(Is call mein koi baat-cheet record nahi hui.)"
    try:
        return phone_ai_reply([], transcript_text, system_prompt)
    except Exception as e:
        return f"(Summary generate nahi ho payi: {e})"


def _log_call_transcript(call_sid, caller_number, name, transcript):
    """
    Call ki poori baat-cheet Jarvis app ke chat list mein save karta hai —
    taaki jab tum app kholo, dikhe ki is number/naam se ye baat hui thi.
    Har turn ke baad call hota hai (na ki sirf end mein), taaki agar call
    beech mein hi kat jaaye to bhi ab tak ki baat-cheet safe rahe.
    """
    if not transcript:
        return
    chat_id = _call_chat_id(call_sid)
    try:
        memory.save_chat(chat_id, transcript)
        label = name if name else (caller_number or "Unknown Number")
        chats = memory.list_chats()
        for c in chats:
            if c["id"] == chat_id:
                c["title"] = f"📞 Call: {label}"
                break
        memory._save_chats_index(chats)
    except Exception as e:
        print(f"[Jarvis Phone] Transcript save fail hui: {e}")


_GOODBYE_RE = re.compile(
    r"\b(bye|alvida|band karo|call kaat|call kat|rakhta hoon|rakhti hoon|"
    r"phone rakh|call end|goodbye|namaste bye)\b",
    re.IGNORECASE,
)


def _is_goodbye(text: str) -> bool:
    return bool(_GOODBYE_RE.search(text or ""))


def _gather():
    return Gather(
        input="speech",
        action="/respond",
        method="POST",
        language=VOICE_LANG,
        speech_timeout="auto",
        timeout=6,
    )


# ══════════════════════════════════════════════════════════════
# ROUTE HANDLERS (registered on the main Flask app from server.py)
# ══════════════════════════════════════════════════════════════

def register_routes(app):

    @app.route("/voice", methods=["POST"])
    @validate_twilio_request
    def voice():
        caller_number = request.form.get("From", "").strip()
        call_sid = request.form.get("CallSid", "").strip()
        name = find_caller_name(caller_number)

        if name:
            greeting = (
                f"नमस्ते {name}, सुधांशु अभी कॉल पर उपलब्ध नहीं हैं, मैं उनका एआई असिस्टेंट जार्विस हूँ। "
                f"बताइए मैं आपकी क्या मदद कर सकता हूँ?"
            )
        else:
            greeting = (
                "नमस्ते, सुधांशु अभी कॉल पर उपलब्ध नहीं हैं, मैं उनका एआई असिस्टेंट जार्विस हूँ। "
                "बताइए मैं आपकी क्या मदद कर सकता हूँ?"
            )

        opening_note = (
            f"📞 Naya call — {name + ' (' + caller_number + ')' if name else caller_number}\n\n"
            f"Jarvis: {greeting}"
        )
        transcript = [{"role": "assistant", "content": opening_note}]

        with _sessions_lock:
            CALL_SESSIONS[call_sid] = {
                "history": [],
                "transcript": transcript,
                "caller_number": caller_number,
                "name": name,
                "system_prompt": _build_phone_system_prompt(name, caller_number),
                "silence_count": 0,
                "created": time.time(),
            }
            _cleanup_old_sessions()

        _log_call_transcript(call_sid, caller_number, name, transcript)

        vr = VoiceResponse()
        gather = _gather()
        gather.say(greeting, voice=VOICE_NAME, language=VOICE_LANG)
        vr.append(gather)
        # Agar caller kuch na bole to /respond hi handle karega (retry/hangup logic)
        vr.redirect("/respond")
        return Response(str(vr), mimetype="text/xml")

    @app.route("/respond", methods=["POST"])
    @validate_twilio_request
    def respond():
        call_sid = request.form.get("CallSid", "").strip()
        speech_result = (request.form.get("SpeechResult") or "").strip()
        caller_number = request.form.get("From", "").strip()

        with _sessions_lock:
            session = CALL_SESSIONS.get(call_sid)
            if session is None:
                name = find_caller_name(caller_number)
                session = {
                    "history": [],
                    "transcript": [],
                    "caller_number": caller_number,
                    "name": name,
                    "system_prompt": _build_phone_system_prompt(name, caller_number),
                    "silence_count": 0,
                    "created": time.time(),
                }
                CALL_SESSIONS[call_sid] = session

        vr = VoiceResponse()

        # ---- Caller chup raha ----
        if not speech_result:
            session["silence_count"] = session.get("silence_count", 0) + 1
            if session["silence_count"] > MAX_SILENT_RETRIES:
                closing = "Theek hai, zaroorat padne par phir se call kijiyega. Dhanyawad, namaste!"
                vr.say(closing, voice=VOICE_NAME, language=VOICE_LANG)
                vr.hangup()
                session["transcript"].append({"role": "assistant", "content": f"Jarvis: {closing}\n\n☎️ Call khatam."})
                _log_call_transcript(call_sid, session["caller_number"], session["name"], session["transcript"])
                with _sessions_lock:
                    CALL_SESSIONS.pop(call_sid, None)
                return Response(str(vr), mimetype="text/xml")

            gather = _gather()
            gather.say("Maaf kijiye, mujhe sunayi nahi diya. Kripya dobara boliye.",
                        voice=VOICE_NAME, language=VOICE_LANG)
            vr.append(gather)
            vr.say("Koi baat nahi, phir kabhi baat karte hain. Namaste!",
                    voice=VOICE_NAME, language=VOICE_LANG)
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        session["silence_count"] = 0

        # ---- Caller ne khud bye bola ----
        if _is_goodbye(speech_result):
            closing = "Theek hai, aapse baat karke accha laga. Namaste!"
            vr.say(closing, voice=VOICE_NAME, language=VOICE_LANG)
            vr.hangup()
            session["transcript"].append({"role": "user", "content": speech_result})
            session["transcript"].append({"role": "assistant", "content": f"Jarvis: {closing}\n\n☎️ Call khatam."})
            _log_call_transcript(call_sid, session["caller_number"], session["name"], session["transcript"])
            with _sessions_lock:
                CALL_SESSIONS.pop(call_sid, None)
            return Response(str(vr), mimetype="text/xml")

        # ---- AI se jawab lo ----
        try:
            ai_reply = phone_ai_reply(session["history"], speech_result, session["system_prompt"])
        except Exception as e:
            print(f"[Jarvis Phone] AI reply error: {e}")
            ai_reply = "Maaf kijiye, mujhe thodi technical dikkat aa rahi hai. Kripya apni baat dobara boliye."

        ai_reply_clean = _sanitize_for_speech(ai_reply)

        session["history"].append({"role": "user", "content": speech_result})
        session["history"].append({"role": "assistant", "content": ai_reply_clean})
        session["history"] = session["history"][-CALL_HISTORY_LIMIT:]

        session["transcript"].append({"role": "user", "content": speech_result})
        session["transcript"].append({"role": "assistant", "content": f"Jarvis: {ai_reply_clean}"})
        # Har turn ke baad turant save karo — call beech mein kate to bhi transcript safe rahe
        _log_call_transcript(call_sid, session["caller_number"], session["name"], session["transcript"])

        gather = _gather()
        gather.say(ai_reply_clean, voice=VOICE_NAME, language=VOICE_LANG)
        vr.append(gather)
        # Agar iske baad bhi chup rahe to /respond hi silence-retry sambhal lega
        vr.redirect("/respond")
        return Response(str(vr), mimetype="text/xml")

    return app
