"""
Jarvis Voice
------------
Bolna: edge-tts (Microsoft Edge TTS) — free, no API key, natural Hindi voice.
Sunna: Groq Whisper API — already tumhare paas Groq key hai, wahi use hogi.

Phone ke audio record/play ke liye Termux:API commands use ho rahe hain,
isliye `termux-api` package aur Termux:API app dono install honi chahiye.
"""

import os
import asyncio
import subprocess
import tempfile
import time
import urllib.request
import memory
from logger import get_logger
log = get_logger("voice")

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

# Hindi voices - Jarvis (Iron Man) jaisi deep, composed male voice default hai
VOICE_MALE = "hi-IN-MadhurNeural"      # default — deep, sophisticated, Jarvis-jaisa tone
VOICE_FEMALE = "hi-IN-SwaraNeural"     # alternate — agar female chahiye
VOICE_FALLBACK = "hi-IN-PrabhatNeural"


def detect_emotion_prosody(text: str):
    """
    Text ke content/punctuation se mood guess karke edge-tts ke liye
    rate/pitch prosody values return karta hai — taaki Jarvis (ya active
    persona) jaisa insaan bolta hai waisa hi, mood ke hisaab se, bole.
    Yeh ek halka heuristic hai, perfect nahi hoga, lekin flat/robotic
    monotone se kaafi behtar sunega.

    Return: (rate_str, pitch_str) — edge_tts.Communicate() ke params jaisa,
    e.g. ("+8%", "+15Hz")
    """
    t = text.strip()
    lower = t.lower()

    excited_words = ["wah", "zabardast", "shaandaar", "amazing", "yay", "मस्त",
                      "बधाई", "congratulations", "great", "बहुत खूब"]
    sad_words = ["afsos", "dukhi", "sorry", "maaf", "दुख", "sad", "खेद", "gam"]
    angry_words = ["gussa", "naraz", "angry", "bakwas", "गुस्सा"]
    calm_words = ["shaanti", "aaram", "relax", "shaant", "शांत"]

    exclam_count = t.count("!")
    question_count = t.count("?")

    if exclam_count >= 2 or any(w in lower for w in excited_words):
        return "+15%", "+25Hz"          # excited/happy — tez aur unchi
    if any(w in lower for w in angry_words):
        return "+10%", "-10Hz"          # angry — tez lekin gehri
    if any(w in lower for w in sad_words):
        return "-12%", "-20Hz"          # sad — dheemi aur neechi
    if any(w in lower for w in calm_words):
        return "-8%", "0Hz"             # calm — thodi dheemi, neutral pitch
    if question_count >= 1 and len(t) < 80:
        return "+3%", "+10Hz"           # question — halki si upward inflection
    return "+0%", "+0Hz"                # normal/neutral baat-cheet


async def _edge_speak(text: str, output_path: str, voice_name: str, rate: str = "+0%", pitch: str = "+0Hz"):
    try:
        await edge_tts.Communicate(text, voice_name, rate=rate, pitch=pitch).save(output_path)
    except Exception:
        try:
            await edge_tts.Communicate(text, voice_name).save(output_path)
        except Exception:
            await edge_tts.Communicate(text, VOICE_FALLBACK).save(output_path)


def generate_speech_file(text: str, gender: str = "male", auto_emotion: bool = True) -> str:
    """
    Web app ke liye: audio file banata hai aur uska path return karta hai
    (Termux speaker pe khud nahi bajata — browser ko bhejne ke liye).
    auto_emotion=True hone par text ke mood ke hisaab se rate/pitch khud
    adjust ho jaate hain (full emotion ke saath bolne jaisa).
    """
    if not EDGE_TTS_AVAILABLE:
        raise RuntimeError("edge-tts installed nahi hai. 'pip install edge-tts' chalao.")

    voice_name = VOICE_FEMALE if gender == "female" else VOICE_MALE
    rate, pitch = detect_emotion_prosody(text) if auto_emotion else ("+0%", "+0Hz")
    output_path = os.path.join(tempfile.gettempdir(), f"jarvis_tts_{os.getpid()}_{abs(hash(text)) % 100000}.mp3")
    asyncio.run(_edge_speak(text, output_path, voice_name, rate=rate, pitch=pitch))
    return output_path


def speak(text: str, lang: str = "hi"):
    """Jarvis ko bolwata hai (Hindi male voice mein) — terminal-mode (main.py) ke liye."""
    if not EDGE_TTS_AVAILABLE:
        print("[Jarvis bol nahi sakta: edge-tts install nahi hai. 'pip install edge-tts' chalao]")
        return

    output_path = os.path.join(tempfile.gettempdir(), "jarvis_response.mp3")

    try:
        asyncio.run(_edge_speak(text, output_path, VOICE_MALE))
        subprocess.run(["termux-media-player", "play", output_path], check=False)

        # Audio khatam hone tak wait karo taaki agla line turant na bol jaaye
        if PYDUB_AVAILABLE:
            try:
                audio = AudioSegment.from_mp3(output_path)
                time.sleep(audio.duration_seconds + 0.3)
            except Exception:
                log.exception("unexpected error - see memory/jarvis_errors.log")
    except Exception as e:
        print(f"[Bolne mein error: {e}]")


def listen_from_mic(duration: int = 5):
    """
    Termux:API se mic se record karta hai, fir Groq Whisper se text mein convert karta hai.
    duration: kitne seconds record karna hai
    """
    api_key = memory.get_secret("groq")
    if not api_key:
        return None, "Groq API key nahi mili, isliye sun nahi sakta."

    record_path = os.path.join(tempfile.gettempdir(), "jarvis_input.wav")

    try:
        # Termux:API se recording
        subprocess.run(
            ["termux-microphone-record", "-f", record_path, "-l", str(duration)],
            check=True,
        )
        subprocess.run(["sleep", str(duration + 1)])
        subprocess.run(["termux-microphone-record", "-q"], check=False)
    except Exception as e:
        return None, f"Recording mein error: {e}"

    # Ab Groq Whisper ko bhejna hai transcription ke liye
    try:
        text = _transcribe_with_groq(record_path, api_key)
        return text, None
    except Exception as e:
        return None, f"Sunne (transcription) mein error: {e}"


def _transcribe_with_groq(audio_path: str, api_key: str) -> str:
    """Groq Whisper API ko multipart request bhejta hai audio file ke saath."""
    import uuid

    boundary = uuid.uuid4().hex
    url = "https://api.groq.com/openai/v1/audio/transcriptions"

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    body = []
    body.append(f"--{boundary}".encode())
    body.append(b'Content-Disposition: form-data; name="model"')
    body.append(b"")
    body.append(b"whisper-large-v3")
    body.append(f"--{boundary}".encode())
    body.append(b'Content-Disposition: form-data; name="file"; filename="audio.wav"')
    body.append(b"Content-Type: audio/wav")
    body.append(b"")
    body.append(audio_data)
    body.append(f"--{boundary}--".encode())

    payload = b"\r\n".join(body)

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        import json
        result = json.loads(resp.read().decode("utf-8"))
        return result.get("text", "")
