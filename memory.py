"""
Jarvis Memory System
---------------------
Yeh module Jarvis ki "yaad rakhne wali" cheez hai.
Isme API keys aur baaki settings ek JSON file (memory/secrets.json) mein
save hoti hain, taaki tumhe baar baar nano kholke file edit na karni pade.

Use karne ka tarika (conversation ke andar):
  "Jarvis code api: groq gsk_abcd1234"
  "Jarvis code api: weather 9f8e7d6c"

Jarvis isko automatically samajh ke memory mein save kar dega.
"""

import json
import os
import re
import time

MEMORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
SECRETS_FILE = os.path.join(MEMORY_DIR, "secrets.json")
NOTES_FILE = os.path.join(MEMORY_DIR, "notes.json")
CHATS_DIR = os.path.join(MEMORY_DIR, "chats")
CHATS_INDEX_FILE = os.path.join(MEMORY_DIR, "chats_index.json")

# Jarvis konse naam se kis API ko pehchanega
# Naya API add karna ho to bas yahan ek line add karo
KNOWN_APIS = {
    "groq": ["groq", "groq 1", "groq1"],
    "gemini": ["gemini", "gemini 1", "gemini1"],
    "gemini_2": ["gemini 2", "gemini2", "gemini dusra"],
    "gemini_3": ["gemini 3", "gemini3", "gemini teesra"],
    "gemini_4": ["gemini 4", "gemini4", "gemini chautha"],
    "gemini_5": ["gemini 5", "gemini5", "gemini paanchwa"],
    "groq_2": ["groq 2", "groq2", "groq dusra", "groq second"],
    "groq_3": ["groq 3", "groq3", "groq teesra", "groq third"],
    "groq_4": ["groq 4", "groq4", "groq chautha", "groq fourth"],
    "groq_5": ["groq 5", "groq5", "groq paanchwa", "groq fifth"],
    "groq_6": ["groq 6", "groq6", "groq chhata", "groq sixth"],
    "groq_7": ["groq 7", "groq7", "groq saatwa", "groq seventh"],

    "openrouter": ["openrouter", "open router", "openrouter 1"],
    "openrouter_2": ["openrouter 2", "openrouter2", "open router 2"],
    "openrouter_3": ["openrouter 3", "openrouter3", "open router 3"],
    "openrouter_4": ["openrouter 4", "openrouter4", "open router 4"],
    "openrouter_5": ["openrouter 5", "openrouter5", "open router 5"],
    "openai": ["openai", "gpt"],
    "claude": ["claude", "anthropic"],
    "tavily": ["tavily"],
    "news": ["news", "newsapi"],
    "gnews": ["gnews", "gnews api", "google news api"],
    "wolfram": ["wolfram", "wolframalpha"],
    "weather": ["weather", "openweather", "openweathermap"],
    "nasa": ["nasa"],
    "youtube": ["youtube", "yt", "youtube api", "youtube key"],
    "pexels": ["pexels", "pexels api", "pexels key"],
    "pixabay": ["pixabay", "pixabay api", "pixabay key"],
    "openverse_id": ["openverse id", "openverse client id", "openverse_id"],
    "openverse_secret": ["openverse secret", "openverse client secret", "openverse_secret"],
}

# Groq keys jis order mein try ki jaayengi (rate-limit hone par agli pe switch)
GROQ_KEY_NAMES = ["groq", "groq_2", "groq_3", "groq_4", "groq_5", "groq_6", "groq_7"]
GEMINI_KEY_NAMES = ["gemini", "gemini_2", "gemini_3", "gemini_4", "gemini_5"]

# OpenRouter keys jis order mein try ki jaayengi (Groq fail hone par fallback)
OPENROUTER_KEY_NAMES = ["openrouter", "openrouter_2", "openrouter_3", "openrouter_4", "openrouter_5"]


def _ensure_files():
    os.makedirs(MEMORY_DIR, exist_ok=True)
    os.makedirs(CHATS_DIR, exist_ok=True)
    if not os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "w") as f:
            json.dump({}, f, indent=2)
    if not os.path.exists(NOTES_FILE):
        with open(NOTES_FILE, "w") as f:
            json.dump([], f, indent=2)
    if not os.path.exists(CHATS_INDEX_FILE):
        with open(CHATS_INDEX_FILE, "w") as f:
            json.dump([], f, indent=2)


def load_secrets():
    """
    File (memory/secrets.json) se secrets load karta hai, aur jo key file
    mein nahi hai uske liye environment variable (UPPERCASE naam) check
    karta hai. Isse Render jaisi hosting par (jahan disk restart/redeploy
    par reset ho sakti hai) API keys Dashboard > Environment Variables mein
    daal ke permanently safe rakhi ja sakti hain.

    BUG FIX: agar secrets.json kisi wajah se corrupt ho jaaye (jaise ek
    crash/OOM-restart beech write ke ho jaaye), pehle poora load_secrets()
    crash ho jaata tha — jiska matlab Groq, Gemini, OpenRouter, SAARE
    providers ek saath "no keys available" dikhne lagte the, chahe
    unki keys bilkul theek hon. Ab corrupt file mile to backup se
    recover karta hai, warna khaali dict se fresh shuru karta hai
    (environment variables se keys phir bhi mil jaayengi) — kabhi crash
    nahi hota.
    """
    _ensure_files()
    try:
        with open(SECRETS_FILE, "r") as f:
            secrets = json.load(f)
    except (json.JSONDecodeError, OSError):
        backup_file = SECRETS_FILE + ".bak"
        secrets = None
        if os.path.exists(backup_file):
            try:
                with open(backup_file, "r") as f:
                    secrets = json.load(f)
            except Exception:
                secrets = None
        if secrets is None:
            secrets = {}
            try:
                with open(SECRETS_FILE, "w") as f:
                    json.dump(secrets, f, indent=2)
            except Exception:
                pass
    for key_name in KNOWN_APIS.keys():
        if not secrets.get(key_name):
            env_val = os.environ.get(key_name.upper())
            if env_val:
                secrets[key_name] = env_val
    return secrets


def _write_secrets(secrets: dict):
    """Har jagah se yahi function use hota hai — save ke saath ek .bak copy
    bhi rakhta hai taaki agar main file kabhi corrupt ho (crash beech write
    ke), load_secrets() usse recover kar sake."""
    with open(SECRETS_FILE, "w") as f:
        json.dump(secrets, f, indent=2, ensure_ascii=False)
    try:
        with open(SECRETS_FILE + ".bak", "w") as f:
            json.dump(secrets, f, indent=2, ensure_ascii=False)
    except Exception:
        pass  # backup best-effort hai, iske fail hone se save nahi rukna chahiye


def save_secret(key_name: str, value: str):
    """Ek API key ko memory mein save karta hai."""
    _ensure_files()
    secrets = load_secrets()
    secrets[key_name] = value
    _write_secrets(secrets)


def get_secret(key_name: str, default=None):
    secrets = load_secrets()
    return secrets.get(key_name, default)


def get_available_groq_keys():
    """Saari save hui Groq keys ki list deta hai (jis order mein try karni hain)."""
    secrets = load_secrets()
    return [secrets[name] for name in GROQ_KEY_NAMES if secrets.get(name)]


def get_available_gemini_keys():
    """Saari saved Gemini keys list karta hai."""
    secrets = load_secrets()
    return [secrets[name] for name in GEMINI_KEY_NAMES if secrets.get(name)]


def get_available_openrouter_keys():
    """Saari save hui OpenRouter keys ki list deta hai (jis order mein try karni hain)."""
    secrets = load_secrets()
    return [secrets[name] for name in OPENROUTER_KEY_NAMES if secrets.get(name)]


SELECTED_MODEL_FILE_KEY = "_selected_model"  # secrets.json ke andar hi ek special entry


def get_selected_model():
    """
    User ne manually jo model chuna hai (settings se), woh deta hai.
    Agar kuch nahi chuna, to 'auto' deta hai (matlab automatic rotation chalegi).
    """
    return get_secret(SELECTED_MODEL_FILE_KEY, "auto")


def set_selected_model(model_id: str):
    """User manually ek specific model select karta hai (ya 'auto' wapas set karta hai)."""
    save_secret(SELECTED_MODEL_FILE_KEY, model_id)


def normalize_api_name(spoken_name: str):
    """
    User ne jo bola/likha (jaise 'weather' ya 'groq' ya 'news') usse
    humare standard key name (jaise 'weather') mein convert karta hai.
    Spaces aur underscores dono ko same treat karta hai matching ke waqt.
    """
    raw = spoken_name.strip().lower()
    normalized_for_match = raw.replace("_", " ")

    for standard_name, aliases in KNOWN_APIS.items():
        alias_set = {a.replace("_", " ") for a in aliases} | {standard_name.replace("_", " ")}
        if normalized_for_match in alias_set:
            return standard_name
    # agar bilkul naya naam hai, to usi ko use kar lo (spaces ko _ se replace)
    return raw.replace(" ", "_")


# Pattern: "jarvis code api: <name> <value>"  (case-insensitive)
CODE_API_PATTERN = re.compile(
    r"jarvis\s+code\s+api\s*:\s*([a-zA-Z0-9_ ]+?)\s+(\S+)\s*$",
    re.IGNORECASE,
)


def try_extract_api_command(text: str):
    """
    Agar user ke message mein 'Jarvis code api: <naam> <key>' pattern hai,
    to (standard_name, value) return karta hai, warna None.
    """
    match = CODE_API_PATTERN.search(text)
    if not match:
        return None
    raw_name, value = match.groups()
    standard_name = normalize_api_name(raw_name)
    return standard_name, value


def delete_secret(key_name: str):
    """Ek API key ko memory se hata deta hai. True return karta hai agar mili aur delete hui."""
    _ensure_files()
    secrets = load_secrets()
    standard_name = normalize_api_name(key_name)
    if standard_name in secrets:
        del secrets[standard_name]
        _write_secrets(secrets)
        return True
    return False


# Pattern: "jarvis delete api: <name>"  (case-insensitive)
DELETE_API_PATTERN = re.compile(
    r"jarvis\s+delete\s+api\s*:\s*([a-zA-Z0-9_ ]+?)\s*$",
    re.IGNORECASE,
)


def try_extract_delete_command(text: str):
    """
    Agar user ke message mein 'Jarvis delete api: <naam>' pattern hai,
    to standard_name return karta hai, warna None.
    """
    match = DELETE_API_PATTERN.search(text)
    if not match:
        return None
    raw_name = match.group(1)
    return normalize_api_name(raw_name)


_CHAT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def _safe_chat_id(chat_id: str) -> str:
    """chat_id ko sanitize karta hai — path traversal (../, /, \\) block karta hai.
    Agar invalid ho to ek safe fallback id deta hai (kabhi bhi exception nahi
    deta, taaki caller har jagah bina extra check ke use kar sake)."""
    chat_id = (chat_id or "").strip()
    if not _CHAT_ID_RE.match(chat_id):
        return "invalid_chat_id"
    return chat_id


def _chat_file(chat_id: str):
    return os.path.join(CHATS_DIR, f"{_safe_chat_id(chat_id)}.json")


def list_chats():
    """Saari chats ki list deta hai (naya pehle), har ek mein id, title, updated_at."""
    _ensure_files()
    with open(CHATS_INDEX_FILE, "r") as f:
        chats = json.load(f)
    return sorted(chats, key=lambda c: c.get("updated_at", ""), reverse=True)


def _save_chats_index(chats: list):
    with open(CHATS_INDEX_FILE, "w") as f:
        json.dump(chats, f, indent=2, ensure_ascii=False)


def create_chat():
    """Naya khali chat banata hai aur uska id return karta hai."""
    import uuid
    from datetime import datetime

    _ensure_files()
    chat_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()

    chats = list_chats()
    chats.insert(0, {"id": chat_id, "title": "Nayi Baat-cheet", "updated_at": now})
    _save_chats_index(chats)

    with open(_chat_file(chat_id), "w") as f:
        json.dump([], f, indent=2)

    return chat_id


def load_chat(chat_id: str):
    """Ek specific chat ki poori message-history deta hai."""
    _ensure_files()
    path = _chat_file(chat_id)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def save_chat(chat_id: str, messages: list):
    """Ek specific chat ki history save karta hai, aur index mein uska title/time update karta hai."""
    _ensure_files()
    chat_id = _safe_chat_id(chat_id)
    with open(_chat_file(chat_id), "w") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)

    from datetime import datetime
    chats = list_chats()
    title = _make_title(messages)
    found = False
    for c in chats:
        if c["id"] == chat_id:
            c["title"] = title
            c["updated_at"] = datetime.now().isoformat()
            found = True
            break
    if not found:
        chats.insert(0, {"id": chat_id, "title": title, "updated_at": datetime.now().isoformat()})
    _save_chats_index(chats)


def delete_chat(chat_id: str):
    """Ek chat ko poori tarah delete kar deta hai (file + index entry)."""
    _ensure_files()
    chat_id = _safe_chat_id(chat_id)
    path = _chat_file(chat_id)
    if os.path.exists(path):
        os.remove(path)
    chats = [c for c in list_chats() if c["id"] != chat_id]
    _save_chats_index(chats)


def _make_title(messages: list, max_len: int = 40):
    """Pehle user message se ek chhota title bana deta hai."""
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            text = m["content"].strip()
            return text[:max_len] + ("…" if len(text) > max_len else "")
    return "Nayi Baat-cheet"


def remember_note(note: str):
    """General yaad rakhne wali baatein (API ke alawa bhi)."""
    _ensure_files()
    with open(NOTES_FILE, "r") as f:
        notes = json.load(f)
    notes.append(note)
    with open(NOTES_FILE, "w") as f:
        json.dump(notes, f, indent=2)


def list_known_secrets():
    """Konsi API keys save hain (values nahi, sirf names) — safe display ke liye."""
    secrets = load_secrets()
    return list(secrets.keys())


# ══════════════════════════════════════════════════════════════
# NAMED STREAM REGISTRY
# ══════════════════════════════════════════════════════════════
# User jab bhi kahin se ek stream/video URL discover kare (jo unke
# paas already legitimate/authorized ho), woh ek naam ke saath save
# kar sakta hai — taaki dobara har baar poora URL type na karna pade,
# bas naam bol ke "play <naam>" kaha ja sake. Yeh module sirf
# user-supplied URLs store karta hai; khud kahin se URL discover/
# scrape nahi karta — woh iska scope nahi hai (dekho hls_player.py
# aur tools.play_stream() ke docstrings).

SAVED_STREAMS_KEY = "_saved_streams"  # {name: url}


def save_named_stream(name: str, url: str):
    """Ek naam ke saath ek stream URL save karta hai (user-supplied URL hi)."""
    _ensure_files()
    secrets = load_secrets()
    streams = secrets.get(SAVED_STREAMS_KEY, {})
    streams[name.strip().lower()] = url.strip()
    secrets[SAVED_STREAMS_KEY] = streams
    _write_secrets(secrets)


def get_named_stream(name: str):
    secrets = load_secrets()
    streams = secrets.get(SAVED_STREAMS_KEY, {})
    return streams.get(name.strip().lower())


def list_named_streams():
    """{naam: url} ka poora dict deta hai."""
    secrets = load_secrets()
    return secrets.get(SAVED_STREAMS_KEY, {})


def delete_named_stream(name: str) -> bool:
    _ensure_files()
    secrets = load_secrets()
    streams = secrets.get(SAVED_STREAMS_KEY, {})
    key = name.strip().lower()
    if key in streams:
        del streams[key]
        secrets[SAVED_STREAMS_KEY] = streams
        _write_secrets(secrets)
        return True
    return False


# ─────────────────────────────────────────────
# SAVED FAVOURITE WEBSITES — user "achhi website"
# link ko naam ke saath yaad rakhna chahta hai, taaki baad mein sirf
# naam bol ke Jarvis khud us page ko dobara khol ke fresh image/video
# nikaal ke de sake (tools.get_page_media / play_saved_site).
# ─────────────────────────────────────────────

SAVED_SITES_KEY = "_saved_sites"  # {name: url}


def save_named_site(name: str, url: str):
    """Ek website link ko naam ke saath save karta hai (user-supplied URL hi)."""
    _ensure_files()
    secrets = load_secrets()
    sites = secrets.get(SAVED_SITES_KEY, {})
    sites[name.strip().lower()] = url.strip()
    secrets[SAVED_SITES_KEY] = sites
    _write_secrets(secrets)


def get_named_site(name: str):
    secrets = load_secrets()
    sites = secrets.get(SAVED_SITES_KEY, {})
    return sites.get(name.strip().lower())


def list_named_sites():
    """{naam: url} ka poora dict deta hai."""
    secrets = load_secrets()
    return secrets.get(SAVED_SITES_KEY, {})


def delete_named_site(name: str) -> bool:
    _ensure_files()
    secrets = load_secrets()
    sites = secrets.get(SAVED_SITES_KEY, {})
    key = name.strip().lower()
    if key in sites:
        del sites[key]
        secrets[SAVED_SITES_KEY] = sites
        _write_secrets(secrets)
        return True
    return False


# ─────────────────────────────────────────────
# PAGE WATCHES — "jab yeh site update ho / jab yahan yeh keyword aaye
# to bata dena" wale proactive background-monitoring watches. Ek
# background scheduler job (scheduler.py: check_page_watches) periodically
# in sabko check karta hai — Jarvis is se PURELY reactive (sirf poochne
# par jawab) se ek kadam aage, PROACTIVE ban jaata hai.
# ─────────────────────────────────────────────

WATCHES_KEY = "_page_watches"  # {name: {"url":..., "keyword":..., "last_hash":..., "created":...}}


def save_watch(name: str, url: str, keyword: str = None, last_hash: str = None):
    _ensure_files()
    secrets = load_secrets()
    watches = secrets.get(WATCHES_KEY, {})
    watches[name.strip().lower()] = {
        "url": url.strip(),
        "keyword": (keyword or "").strip() or None,
        "last_hash": last_hash,
        "created": time.time(),
    }
    secrets[WATCHES_KEY] = watches
    _write_secrets(secrets)


def update_watch_hash(name: str, new_hash: str):
    _ensure_files()
    secrets = load_secrets()
    watches = secrets.get(WATCHES_KEY, {})
    key = name.strip().lower()
    if key in watches:
        watches[key]["last_hash"] = new_hash
        secrets[WATCHES_KEY] = watches
        _write_secrets(secrets)


def list_watches():
    """{naam: {url, keyword, last_hash, created}} ka poora dict deta hai."""
    secrets = load_secrets()
    return secrets.get(WATCHES_KEY, {})


def delete_watch(name: str) -> bool:
    _ensure_files()
    secrets = load_secrets()
    watches = secrets.get(WATCHES_KEY, {})
    key = name.strip().lower()
    if key in watches:
        del watches[key]
        secrets[WATCHES_KEY] = watches
        _write_secrets(secrets)
        return True
    return False


# ══════════════════════════════════════════════════════════════
# STREAM QUALITY PREFERENCE
# ══════════════════════════════════════════════════════════════
# User apna default data-usage/quality preference ek baar set kar sakta
# hai (e.g. "144p" mobile data bachane ke liye, "1080p" WiFi par) —
# uske baad har stream/channel isi quality mein play karne ki koshish
# karega (agar us stream mein woh quality available ho).

QUALITY_PREF_KEY = "_stream_quality_pref"


def set_stream_quality_pref(quality: str):
    _ensure_files()
    secrets = load_secrets()
    secrets[QUALITY_PREF_KEY] = quality.strip().lower()
    _write_secrets(secrets)


def get_stream_quality_pref():
    """Saved default quality preference deta hai, ya None agar set nahi ki gayi (matlab auto/adaptive)."""
    secrets = load_secrets()
    return secrets.get(QUALITY_PREF_KEY)


# ══════════════════════════════════════════════════════════════
# PERSONA / ROLEPLAY SYSTEM
# ══════════════════════════════════════════════════════════════
# User jab chahe Jarvis ko kisi bhi character/role mein dhaal sake
# (e.g. "tum ab ek pirate captain bano", "Sherlock Holmes ban jao",
# "mera dost bankar baat karo"). Active persona secrets.json ke
# andar ek special key mein store hoti hai, aur saved presets
# alag dict mein — taaki dobara "wapas X bano" bolne par turant
# switch ho sake.

ACTIVE_PERSONA_KEY = "_active_persona"      # {"name":..., "description":..., "style":...} ya None
SAVED_PERSONAS_KEY = "_saved_personas"      # {name: {"description":..., "style":...}}


_FEMALE_HINTS = ["actress", "she", "her", "ladki", "aurat", "mahila", "girl",
                 "woman", "heroine", "queen", "didi", "behen", "maa", "mother",
                 "female"]
_MALE_HINTS = ["actor", "he ", "his ", "ladka", "aadmi", "purush", "boy",
               "man", "hero", "king", "bhai", "papa", "father", "male"]


def _infer_voice_gender(description: str, style: str) -> str:
    """
    Persona ki description/style se andaza lagata hai ki voice male honi
    chahiye ya female — taaki TTS khud sahi awaaz (Hindi hi-IN-MadhurNeural
    ya hi-IN-SwaraNeural) choose kar sake, bina user ko manually voice
    badalni pade. Yeh sirf gender-appropriate synthetic Hindi voice choose
    karta hai — kisi bhi REAL insaan ki awaaz clone nahi karta.
    """
    text = f"{description} {style}".lower()
    female_score = sum(1 for w in _FEMALE_HINTS if w in text)
    male_score = sum(1 for w in _MALE_HINTS if w in text)
    if female_score > male_score:
        return "female"
    if male_score > female_score:
        return "male"
    return "male"  # default


def set_active_persona(name: str, description: str, style: str = "", voice_gender: str = ""):
    """Ek naya persona activate karta hai aur use future ke liye bhi save kar leta hai."""
    _ensure_files()
    secrets = load_secrets()
    inferred_gender = voice_gender.strip().lower() if voice_gender else _infer_voice_gender(description, style)
    if inferred_gender not in ("male", "female"):
        inferred_gender = _infer_voice_gender(description, style)

    persona = {
        "name": name.strip(),
        "description": description.strip(),
        "style": (style or "").strip(),
        "voice_gender": inferred_gender,
    }
    secrets[ACTIVE_PERSONA_KEY] = persona

    saved = secrets.get(SAVED_PERSONAS_KEY, {})
    saved[persona["name"].lower()] = {
        "description": persona["description"],
        "style": persona["style"],
        "voice_gender": persona["voice_gender"],
    }
    secrets[SAVED_PERSONAS_KEY] = saved

    _write_secrets(secrets)
    return persona


def get_active_persona():
    """Abhi jo persona active hai woh dict deta hai, ya None agar normal Jarvis mode hai."""
    secrets = load_secrets()
    return secrets.get(ACTIVE_PERSONA_KEY) or None


def clear_active_persona():
    """Persona hata ke Jarvis ko wapas normal mode mein le aata hai."""
    _ensure_files()
    secrets = load_secrets()
    had_one = ACTIVE_PERSONA_KEY in secrets and secrets[ACTIVE_PERSONA_KEY]
    secrets[ACTIVE_PERSONA_KEY] = None
    _write_secrets(secrets)
    return bool(had_one)


def get_saved_personas():
    """Saare pehle bane personas ki list deta hai (naam → description/style)."""
    secrets = load_secrets()
    return secrets.get(SAVED_PERSONAS_KEY, {})


def get_saved_persona(name: str):
    saved = get_saved_personas()
    return saved.get(name.strip().lower())


def delete_saved_persona(name: str):
    _ensure_files()
    secrets = load_secrets()
    saved = secrets.get(SAVED_PERSONAS_KEY, {})
    key = name.strip().lower()
    if key in saved:
        del saved[key]
        secrets[SAVED_PERSONAS_KEY] = saved
        _write_secrets(secrets)
        return True
    return False
