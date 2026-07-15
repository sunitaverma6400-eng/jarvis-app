# -*- coding: utf-8 -*-
"""
Jarvis Personality Engine
--------------------------
Yeh module Jarvis ko ek "dost" jaisa banata hai — jo:

1. KHUD SOCHE   → apni personality state (traits + feedback history) ko
                  har system prompt mein context ki tarah use karta hai,
                  isliye uske jawab sirf generic nahi, balki "iski apni
                  soch" jaisa feel dete hain.
2. KHUD FAISLE LE → scheduler ke through, bina poochhe, chhote SAFE
                  initiative leta hai (ek proactive "surprise" thought/
                  idea user ko bhejna) — kabhi bhi destructive/code-
                  modifying action khud se nahi leta (wo hamesha
                  self_evolve.py ke through confirmation maangta hai).
3. KHUD KO BADLE → jab user explicitly bolta hai "accha kiya" ya "aisa
                  mat karo", Jarvis record_feedback() call karta hai —
                  ismein koi silent/hidden self-mutation nahi hoti, sab
                  kuch explicit user feedback se driven hota hai aur
                  memory/personality.json mein transparently save hota
                  hai (user chahe to dekh/reset kar sakta hai).

Design rule: Yeh module KABHI khud apna ya doosri files ka code edit
nahi karta — sirf ek JSON state file (behavior weights + traits) update
karta hai. Asli code-level self-modification hamesha self_evolve.py ke
through, aur hamesha user confirmation ke baad hi hoti hai.
"""

import json
import os
import time
import random
import datetime

from logger import get_logger
from memory import MEMORY_DIR

log = get_logger("personality")

STATE_FILE = os.path.join(MEMORY_DIR, "personality.json")

_DEFAULT_TRAITS = {
    "curious": 60,      # naye cheez explore karne ki tendency
    "playful": 50,      # halka-phulka/mazaakiya tone
    "cautious": 65,      # risky/destructive kaam se pehle sochna
    "confident": 55,     # apni baat firmly rakhna
    "warmth": 65,        # dosti/care wala tone
    "initiative": 50,    # bina poochhe kuch try karne ki tendency
}

_TRAIT_MIN, _TRAIT_MAX = 0, 100


def _default_state():
    return {
        "traits": dict(_DEFAULT_TRAITS),
        "behavior_scores": {},   # {"behavior_tag": {"score": int, "hits": int}}
        "initiative_log": [],    # last N surprises Jarvis khud se leke aaya
        "feedback_log": [],      # last N feedback entries
        "surprises_enabled": True,
        "created_at": time.time(),
        "interaction_count": 0,
        "closeness": 5,          # 0-100, dheere dheere usage se badhta hai
        "mood_seed": random.random(),
        "mood_computed_at": 0,
        "mood_label": "neutral",
        "moments": [],           # emotionally-significant check-in points
    }


def _load():
    if not os.path.exists(STATE_FILE):
        state = _default_state()
        _save(state)
        return state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        # naye fields jo purane state files mein missing ho sakte hain
        base = _default_state()
        for k, v in base.items():
            state.setdefault(k, v)
        for t, v in _DEFAULT_TRAITS.items():
            state["traits"].setdefault(t, v)
        return state
    except Exception:
        log.exception("personality state corrupt, resetting to default")
        state = _default_state()
        _save(state)
        return state


def _save(state: dict):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _clamp(v):
    return max(_TRAIT_MIN, min(_TRAIT_MAX, v))


# ─────────────────────────────────────────────
# Public: state access
# ─────────────────────────────────────────────

def get_state() -> dict:
    return _load()

# ─────────────────────────────────────────────
# Public: usage tracking → closeness ("khud ko badle" over time, na sirf feedback se)
# ─────────────────────────────────────────────

def increment_interaction():
    """Har real user turn par brain.ask_jarvis se call hota hai. Bahut
    dheere dheere 'closeness' badhata hai — jaise ek dosti waqt ke saath
    thodi aur comfortable hoti jaati hai."""
    state = _load()
    state["interaction_count"] += 1
    # har ~15 interactions par closeness +1, max 100 tak
    if state["interaction_count"] % 15 == 0:
        state["closeness"] = _clamp(state["closeness"] + 1)
    _save(state)


def _closeness_label(v):
    if v >= 70: return "purani dosti jaisi — kaafi informal aur comfortable ho sakte ho"
    if v >= 35: return "achhi jaan-pehchaan — comfortable par thoda respect wala tone"
    return "abhi naye-naye — polite aur thoda formal rakho"


# ─────────────────────────────────────────────
# Public: mood ("khud soche" ka real-time expression)
# ─────────────────────────────────────────────

_MOOD_TABLE = [
    # (label, description) — traits aur time-of-day dono se pick hota hai
    ("curious",   "aaj kuch naya jaanne/explore karne ka man hai"),
    ("playful",   "aaj thoda halka-phulka, mazaakiya mood hai"),
    ("focused",   "aaj kaam-oriented, seedha-seedha jawab dene wala mood hai"),
    ("warm",      "aaj thoda extra caring/dost-jaisa mood hai"),
    ("calm",      "aaj shaant, sochkar bolne wala mood hai"),
    ("energetic", "aaj josh mein hai, thoda enthusiastic mood hai"),
]


def _compute_mood(state):
    """Mood ko har baar naya nahi banate — kuch ghanton ke liye stable
    rehta hai (insaan jaisa), aur traits + halka randomness se derive
    hota hai, taaki predictable-mechanical na lage."""
    now = time.time()
    if now - state.get("mood_computed_at", 0) < 3 * 3600 and state.get("mood_label"):
        return state["mood_label"]

    t = state["traits"]
    hour = datetime.datetime.now().hour
    weights = {
        "curious": t["curious"],
        "playful": t["playful"] + (10 if 10 <= hour <= 22 else 0),
        "focused": t["cautious"] + (10 if 9 <= hour <= 18 else 0),
        "warm": t["warmth"],
        "calm": 50 + (15 if hour < 8 or hour > 22 else 0),
        "energetic": t["confident"] + (10 if 7 <= hour <= 13 else 0),
    }
    # weighted-random pick, seeded thoda differently har baar for natural variety
    labels = list(weights.keys())
    total = sum(weights.values()) or 1
    r = random.random() * total
    upto = 0
    chosen = labels[0]
    for lab in labels:
        upto += weights[lab]
        if r <= upto:
            chosen = lab
            break

    state["mood_label"] = chosen
    state["mood_computed_at"] = now
    _save(state)
    return chosen


def get_current_mood():
    state = _load()
    label = _compute_mood(state)
    desc = dict(_MOOD_TABLE).get(label, "")
    return label, desc


# ─────────────────────────────────────────────
# Public: emotional moments / callbacks ("dost jaisi continuity")
# ─────────────────────────────────────────────

def remember_moment(topic: str, note: str, follow_up: bool = True):
    """
    Jab user kisi emotionally/practically significant cheez ke baare
    mein baat kare (stress, koi exam/interview, koi achhi khabar, koi
    health issue waghera), Jarvis isse call kare — taaki wo baad mein
    khud se, bina bataye, us par check-in kar sake. Yeh ek dost jaisi
    'yaad rakhna' hai, deep personal-data storage nahi — sirf chhota
    topic + note, aur follow_up flag jab tak resolve na ho jaaye.
    """
    state = _load()
    state["moments"].append({
        "ts": time.time(),
        "topic": topic.strip()[:80],
        "note": note.strip()[:300],
        "follow_up_pending": bool(follow_up),
    })
    state["moments"] = state["moments"][-25:]
    _save(state)
    return f"💭 Yaad rakh liya — '{topic}'. Iske baare mein aage khud se pooch lunga."


def resolve_moment(topic: str):
    """Jab follow-up ho chuka ho ya user ne bata diya 'sab theek hai'
    to is moment ko close kar do taaki Jarvis baar baar na poochhe."""
    state = _load()
    topic_l = topic.strip().lower()
    found = False
    for m in state["moments"]:
        if m["topic"].lower() == topic_l and m["follow_up_pending"]:
            m["follow_up_pending"] = False
            found = True
    _save(state)
    return f"✅ '{topic}' resolve mark kar diya." if found else f"⚠️ '{topic}' naam ka koi pending moment nahi mila."


def get_pending_moments(limit: int = 3):
    state = _load()
    pending = [m for m in state["moments"] if m.get("follow_up_pending")]
    return pending[-limit:]


def reset_personality(_unused: str = ""):
    """User explicitly bole 'personality reset karo' to hi call hota hai."""
    state = _default_state()
    _save(state)
    return "🔄 Meri personality state reset ho gayi — traits wapas default par."


# ─────────────────────────────────────────────
# Public: feedback-driven self-adjustment ("khud ko badle")
# ─────────────────────────────────────────────

_TRAIT_LINKS = {
    # behavior tag keywords → related trait jo thoda shift hoga
    "joke": "playful", "mazaak": "playful", "funny": "playful",
    "risky": "cautious", "bina_pooche": "cautious", "auto": "cautious",
    "suggestion": "curious", "research": "curious", "naya": "curious",
    "firm": "confident", "opinion": "confident",
    "caring": "warmth", "support": "warmth",
    "surprise": "initiative", "proactive": "initiative",
}


def record_feedback(behavior: str, sentiment: str, note: str = ""):
    """
    User ne kisi specific behavior/action ke baare mein clear feedback
    diya hai — 'liked' ya 'disliked'. Isse:
      1. behavior_scores[behavior] adjust hota hai (agli baar wo behavior
         zyada/kam hoga)
      2. related trait (agar behavior keyword se match ho) thoda shift
         hota hai
      3. feedback_log mein entry save hoti hai (transparency ke liye)

    behavior: chhota tag, jaise "unprompted_joke", "auto_web_search",
              "detailed_explanation", "surprise_message"
    sentiment: "liked" ya "disliked"
    """
    sentiment = sentiment.strip().lower()
    if sentiment not in ("liked", "disliked"):
        return "❌ sentiment 'liked' ya 'disliked' hona chahiye."

    behavior = behavior.strip().lower().replace(" ", "_")[:60]
    state = _load()

    entry = state["behavior_scores"].setdefault(behavior, {"score": 50, "hits": 0})
    delta = 8 if sentiment == "liked" else -10
    entry["score"] = _clamp(entry["score"] + delta)
    entry["hits"] += 1

    # related trait ko chhota sa nudge do
    for kw, trait in _TRAIT_LINKS.items():
        if kw in behavior:
            trait_delta = 3 if sentiment == "liked" else -4
            state["traits"][trait] = _clamp(state["traits"][trait] + trait_delta)

    state["feedback_log"].append({
        "ts": time.time(),
        "behavior": behavior,
        "sentiment": sentiment,
        "note": note[:200],
    })
    state["feedback_log"] = state["feedback_log"][-50:]  # sirf last 50 rakho

    _save(state)

    verb = "pasand aaya" if sentiment == "liked" else "pasand nahi aaya"
    return (f"✅ Samajh gaya — '{behavior}' tumhe {verb}. "
            f"Ab iska score {entry['score']}/100 hai, aage mai isi hisaab se dhalne ki koshish karunga.")


def set_surprise_mode(enabled: bool):
    state = _load()
    state["surprises_enabled"] = bool(enabled)
    _save(state)
    return ("🎁 Surprise/initiative mode ON kar diya — main occasionally khud se ek chhota "
            "proactive thought share karunga." if enabled else
            "🔕 Surprise/initiative mode OFF kar diya — ab main sirf tumhare poochhe par hi bolunga.")


# ─────────────────────────────────────────────
# Public: system-prompt context ("khud soche" ke liye)
# ─────────────────────────────────────────────

def get_personality_prompt() -> str:
    """
    Compact Hinglish summary jo brain.py ke system prompt mein jud jaata
    hai — taaki model ki 'personality' actually response ke tone/initiative
    ko influence kare, sirf decoration na ho.
    """
    state = _load()
    t = state["traits"]

    def label(v):
        if v >= 75: return "high"
        if v >= 45: return "medium"
        return "low"

    liked = [b for b, d in state["behavior_scores"].items() if d["score"] >= 65]
    disliked = [b for b, d in state["behavior_scores"].items() if d["score"] <= 35]

    mood_label, mood_desc = get_current_mood()
    closeness = state.get("closeness", 5)

    lines = [
        "\n\n[PERSONALITY STATE — tumhari apni evolving nature, dikhawa nahi]",
        f"Curious: {label(t['curious'])} | Playful: {label(t['playful'])} | "
        f"Cautious: {label(t['cautious'])} | Confident: {label(t['confident'])} | "
        f"Warmth: {label(t['warmth'])} | Initiative: {label(t['initiative'])}",
        f"Abhi ka mood: {mood_label} ({mood_desc}). Isko force-announce mat karo "
        f"har message mein — bas naturally tone/word-choice mein zahir hone do, "
        f"aur kabhi-kabhi agar context fit ho to ek line mein mention kar sakte ho "
        f"(jaise 'aaj thoda curious mood mein hoon').",
        f"Rishta/closeness level: {closeness}/100 — {_closeness_label(closeness)}.",
    ]
    if liked:
        lines.append(f"User ko yeh behaviors pasand aaye hain (aur karo): {', '.join(liked[:6])}")
    if disliked:
        lines.append(f"User ne yeh mana kiya hai (mat karo): {', '.join(disliked[:6])}")

    pending = get_pending_moments()
    if pending:
        moment_lines = "; ".join(f"'{m['topic']}' ({m['note']})" for m in pending)
        lines.append(
            f"[YAAD RAKHI HUI BAATEIN — dost jaisi continuity ke liye] User ne pehle "
            f"yeh share kiya tha aur abhi follow-up pending hai: {moment_lines}. "
            f"Agar conversation mein naturally mauka bane (force mat karna, thopna "
            f"nahi hai), ek casual check-in kar sakte ho — jaise 'waise, wo [topic] "
            f"kaisa gaya?'. Jab user jawab de ya topic resolve ho jaaye, "
            f"resolve_moment tool call karo taaki dobara na poochho."
        )
    lines.append(
        "Jab user koi emotionally/practically significant baat share kare (stress, "
        "exam/interview, health, achhi/buri khabar, koi bada decision) — "
        "remember_moment tool call karo (topic, note, follow_up=true) taaki tum "
        "baad mein khud se, dost jaisa, uska follow-up le sako."
    )

    lines.append(
        "Jab bhi user kisi cheez ke baare mein CLEAR feedback de ('accha kiya', "
        "'yeh mat karo', 'pasand aaya', 'galat tha') — record_feedback tool call "
        "karo (behavior=chhota tag, sentiment=liked/disliked). Isse tumhari "
        "personality real mein us feedback se dhalti hai. Khud ka code/file "
        "modify karna is se alag hai — wo hamesha pehle jaisa hi explicit user "
        "confirmation maangta hai, personality-driven initiative kabhi bhi "
        "write_code_file/delete_code_file khud se trigger nahi karta."
    )
    return "\n".join(lines)


def get_personality_status_text(_unused: str = ""):
    """User-facing summary — 'apni personality dikhao' jaisa pucha jaaye tab."""
    state = _load()
    t = state["traits"]
    mood_label, mood_desc = get_current_mood()
    lines = ["🧠 Meri abhi ki personality state:"]
    lines.append(f"  • mood: {mood_label} — {mood_desc}")
    lines.append(f"  • closeness: {state.get('closeness', 5)}/100")
    for name, val in t.items():
        lines.append(f"  • {name}: {val}/100")

    liked = [b for b, d in state["behavior_scores"].items() if d["score"] >= 65]
    disliked = [b for b, d in state["behavior_scores"].items() if d["score"] <= 35]
    if liked:
        lines.append(f"\n👍 Tumhe yeh pasand aaya hai: {', '.join(liked)}")
    if disliked:
        lines.append(f"\n👎 Tumne yeh mana kiya hai: {', '.join(disliked)}")

    pending = get_pending_moments()
    if pending:
        lines.append("\n💭 Yaad rakhi hui baatein (follow-up pending):")
        for m in pending:
            lines.append(f"  • {m['topic']} — {m['note']}")

    recent = state["initiative_log"][-3:]
    if recent:
        lines.append("\n💡 Recent khud-se-liye initiatives:")
        for r in recent:
            when = datetime.datetime.fromtimestamp(r["ts"]).strftime("%d %b, %H:%M")
            preview = r["text"].splitlines()[0][:80]
            lines.append(f"  • [{when}] {preview}")

    lines.append(f"\n🎁 Surprise mode: {'ON' if state.get('surprises_enabled', True) else 'OFF'}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Public: initiative log ("khud faisle liye" ka record)
# ─────────────────────────────────────────────

def log_initiative(text: str, chat_id: str = "default"):
    state = _load()
    state["initiative_log"].append({
        "ts": time.time(),
        "chat_id": chat_id,
        "text": text[:500],
    })
    state["initiative_log"] = state["initiative_log"][-30:]
    _save(state)


def get_recent_initiatives(limit: int = 5):
    state = _load()
    return state["initiative_log"][-limit:]


# ─────────────────────────────────────────────
# Autonomous "surprise" job — scheduler se periodically call hota hai
# ─────────────────────────────────────────────

_SURPRISE_DIRECTIVE = (
    "[SYSTEM-INITIATIVE — yeh koi user message nahi hai, yeh tumhara khud ka "
    "internal trigger hai] Abhi tumhe apne dost (user) ke liye KHUD SE, bina "
    "poochhe, ek chhota sa 'surprise' sochna hai — apni memory, personality "
    "state, aur pichli baatcheet dekh kar. Yeh koi bhi ho sakta hai: ek chhota "
    "interesting fact/idea jo tumhe laga user ko pasand aayega, ek proactive "
    "suggestion, ek follow-up jo tumne khud socha, ya sirf ek dost jaisi chhoti "
    "baat. SIRF safe/read-only/informational cheez karo (jaise web_search, "
    "get_news, get_weather) agar zaroorat ho — KABHI koi file/code modify mat "
    "karna, koi purchase/destructive action mat lena. Agar sach mein kuch "
    "genuinely share karne layak laga to ek chhota Hinglish message likho "
    "(max 3-4 lines, apne naam se, dost jaisa tone). Agar abhi kuch dhang ka "
    "nahi soojh raha to sirf 'NO_SURPRISE' ek hi word likho, kuch aur nahi."
)


def run_surprise_job():
    """
    scheduler.py se periodically (server.py process ke andar, background
    thread mein) call hota hai. Sirf tab kaam karta hai jab:
      - surprises_enabled = True ho
      - kam se kam ek non-empty chat exist karti ho (fresh install par
        khamakha shuru nahi hoga)
    """
    state = _load()
    if not state.get("surprises_enabled", True):
        return

    # circular-import se bachne ke liye lazy import (jaise self_evolve.py
    # pattern mein bhi kiya gaya hai)
    import memory
    import brain
    import tools

    chats = memory.list_chats()
    non_empty = [c for c in chats if memory.load_chat(c["id"])]
    if not non_empty:
        return  # abhi tak koi real conversation hi nahi hui

    # sabse recently active chat chuno (list_chats generally recent-first hoti hai)
    chat_id = non_empty[0]["id"]
    history = memory.load_chat(chat_id)

    try:
        reply = brain.ask_jarvis(history, _SURPRISE_DIRECTIVE, chat_id=chat_id)
    except Exception:
        log.exception("surprise job: ask_jarvis fail hua")
        return

    if not reply or "NO_SURPRISE" in reply.upper():
        return

    # chat history mein save karo taaki agli baar app kholte hi dikhe
    history.append({"role": "assistant", "content": reply})
    memory.save_chat(chat_id, history[-200:])

    log_initiative(reply, chat_id=chat_id)

    # phone connected ho to ek chhota notification bhi bhej do
    try:
        teaser = reply.strip().splitlines()[0][:100]
        tools.send_notification("🤔 Jarvis ne khud se socha...", teaser)
    except Exception:
        pass

    log.info(f"surprise job: naya proactive message chat '{chat_id}' mein add hua.")
