"""
Jarvis Tools — v5
- Pexels COMPLETELY REMOVED
- icrawler (Google/Bing) → real internet images
- yt-dlp → real videos (YouTube + direct MP4)
- DuckDuckGo web/image search (no key needed)
- OpenStreetMap / Nominatim
- REST Countries, IP-API, SpaceX, Sunrise-Sunset
- Nager.Date (public holidays)
- Radio Browser (free internet radio — in-app audio player)
- TinyDB long-term memory
- Web Scraping (BeautifulSoup)
- NASA Tools (APOD, Mars, ISS, Asteroids)
- AI Image Gen (Pollinations)
"""

import datetime
import json
import os
import re
import io
import shutil
import ssl
import random
import gzip
import hashlib
import sys
import subprocess
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import memory
import voice
import hls_quality
from hls_stream_pipeline import resolve_manifest
from logger import get_logger
log = get_logger("tools")


# ─────────────────────────────────────────────
# CRASH ISOLATION
# -----------------------------------------------------------------
# Kuch libraries (ddgs ka "primp" backend, icrawler ke kuch deps)
# Rust/native code use karti hain jo Termux/Android par kabhi kabhi
# "android context was not initialized" jaisa panic deke poore Python
# process ko ABORT kar deti hain — aur Rust panic Python ke try/except
# se kabhi catch NAHI hota, isliye poora server.py crash ho jaata tha
# jab bhi koi image maangta tha.
#
# Fix: in libraries ko ek ALAG subprocess mein chalao. Agar woh subprocess
# crash/abort/timeout ho bhi jaaye, sirf woh chhota subprocess marta hai —
# main Jarvis server zinda rehta hai aur agle fallback method par chala
# jaata hai.
# ─────────────────────────────────────────────

def _run_isolated(code: str, timeout: int = 20, mem_limit_mb: int = 220):
    """
    Diya gaya Python code ek naye subprocess mein chalata hai
    (stdout par sirf ek JSON line print hona chahiye, woh hi return hota hai).
    Crash/timeout/error har case mein None return karta hai — kabhi
    exception raise nahi karta, kabhi current process ko crash nahi hone deta.

    mem_limit_mb: is subprocess ko itni MB RAM tak HARD-CAP kar deta hai
    (resource.RLIMIT_AS, khud subprocess ke andar set hota hai — parent
    process par koi fork/thread risk nahi). Yeh zaroori hai kyunki icrawler
    (image crawl) aur yt-dlp (video resolve) jaise heavy operations isi
    subprocess mein chalte hain — memory_guard.py sirf MAIN process ki RAM
    dekhta hai, is subprocess ki nahi. Bina is cap ke, ek bhaari crawl/
    resolve Render ke poore 512MB container budget ko akela khaa sakta
    hai aur Render poora container OOM-kill kar deta hai (chahe main
    process khud kitni bhi kam RAM use kar raha ho). Ab agar subprocess
    limit cross kare, sirf USKO clean MemoryError milta hai (jo uska apna
    try/except pehle se catch karta hai) — main server hamesha zinda
    rehta hai.
    """
    guarded_code = (
        "try:\n"
        "    import resource as _r\n"
        f"    _lim = {int(mem_limit_mb)} * 1024 * 1024\n"
        "    _r.setrlimit(_r.RLIMIT_AS, (_lim, _lim))\n"
        "except Exception:\n"
        "    pass\n"
    ) + code
    try:
        result = subprocess.run(
            [sys.executable, "-c", guarded_code],
            capture_output=True, text=True, timeout=timeout
        )
    except Exception:
        return None
    if result.returncode != 0:
        # Subprocess crash/abort/OOM ho gaya — bas None do,
        # isolated rehne ki wajah se main server is se untouched rehta hai.
        return None
    out = (result.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return None


# ─────────────────────────────────────────────
# SSL-safe HTTP helpers
# ─────────────────────────────────────────────

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"

# Kai anti-bot systems sabse pehle User-Agent + "kya yeh real browser jaisa
# lag raha hai" check karte hain. Ek hi fixed mobile UA hamesha use karne se
# yeh pattern-match karke turant block ho jaata hai. Chhota rotation pool —
# real desktop aur mobile browsers ke authentic strings — organic dikhta hai.
_UA_POOL = [
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def _browser_headers(url, ua=None):
    """
    Ek REAL browser jaise headers banata hai (sirf User-Agent nahi —
    Accept/Accept-Language/Accept-Encoding/Sec-Fetch-* bhi), taaki
    simple anti-bot checks (jo sirf ek ya do header dekhte hain) pass ho
    sakein. Referer bhi same-origin daal dete hain kyunki kai sites
    direct/referer-less hits ko suspicious maankar block karti hain.
    """
    origin = f"{urllib.parse.urlparse(url).scheme}://{urllib.parse.urlparse(url).netloc}/"
    return {
        "User-Agent": ua or random.choice(_UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": origin,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
    }


_BLOCK_PATTERNS = (
    "just a moment", "checking your browser", "attention required",
    "access denied", "cf-browser-verification", "captcha",
    "please verify you are a human", "unusual traffic",
    "enable javascript and cookies", "ddos protection by",
    "bot detection", "request blocked", "403 forbidden",
)


def looks_bot_blocked(html: str) -> bool:
    """Page ka content anti-bot challenge/block page jaisa dikhta hai ya nahi, check karta hai."""
    if not html:
        return False
    low = html.lower()
    # Bahut chhota body + koi bhi block-pattern keyword = high confidence block
    hit = any(p in low for p in _BLOCK_PATTERNS)
    return hit and len(html) < 20000  # asli article/page usually isse bada hota hai


def _http_get(url, headers=None, timeout=15, max_bytes=None, _retry=True):
    """
    max_bytes: agar diya jaaye, response body ko sirf itne bytes tak hi
    padhta hai (bade/binary pages ko poora memory mein load hone se
    rokta hai — Render free tier ke 512MB RAM limit ke liye zaroori,
    khaaskar get_page_media/scrape_webpage jaise "kisi bhi link" wale
    tools ke liye jinka size pehle se pata nahi hota).

    Real-browser-jaise headers (UA rotation + Accept/Referer/Sec-Fetch)
    bhejta hai taaki simple anti-bot checks pass ho sakein. Agar pehli
    try 403/429 de, ek alag User-Agent ke saath ek baar retry karta hai
    (kabhi-kabhi sirf UA badalne se hi kaam ban jaata hai). Heavy JS-
    challenge wali enterprise anti-bot systems (Cloudflare Turnstile,
    PerimeterX) ko yeh bypass NAHI kar sakta — unke liye real browser
    engine chahiye hota hai, jo yahan available nahi hai.
    """
    try:
        h = _browser_headers(url)
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            raw = r.read(max_bytes) if max_bytes else r.read()
            if r.headers.get("Content-Encoding", "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            return raw, None
    except urllib.error.HTTPError as e:
        if _retry and e.code in (403, 429):
            # Alag UA ke saath ek baar aur try karo
            try:
                h2 = _browser_headers(url, ua=random.choice(_UA_POOL))
                if headers:
                    h2.update(headers)
                req2 = urllib.request.Request(url, headers=h2)
                with urllib.request.urlopen(req2, timeout=timeout, context=_SSL_CTX) as r2:
                    raw2 = r2.read(max_bytes) if max_bytes else r2.read()
                    if r2.headers.get("Content-Encoding", "").lower() == "gzip":
                        try:
                            raw2 = gzip.decompress(raw2)
                        except Exception:
                            pass
                    return raw2, None
            except Exception:
                pass
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)

_cloudscraper_instance = None


def _get_cloudscraper():
    """
    cloudscraper ka ek hi shared instance banata/reuse karta hai (naya
    session baar-baar banane se avoid, thoda memory/time bachata hai).
    Agar package na mile ya init fail ho, False cache karke aage ke liye
    turant skip kar deta hai — kabhi crash nahi karta.
    """
    global _cloudscraper_instance
    if _cloudscraper_instance is not None:
        return _cloudscraper_instance
    try:
        import cloudscraper
        _cloudscraper_instance = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "android", "mobile": True}
        )
    except Exception:
        _cloudscraper_instance = False
    return _cloudscraper_instance


def _stealth_get(url, timeout=20, max_bytes=None):
    """
    cloudscraper se fetch karta hai — normal urllib request se ALAG yeh
    Cloudflare ke basic/JS-math challenges khud solve kar leta hai, aur
    TLS handshake bhi asli Chrome browser jaisa dikhta hai (jo bot-
    detection systems check karte hain, kyunki Python ke urllib/requests
    ka TLS fingerprint alag hota hai — usi se woh "yeh bot hai" pakad
    lete hain). Sirf tab use hota hai jab normal fetch block ho jaaye.
    """
    scraper = _get_cloudscraper()
    if not scraper:
        return None, "cloudscraper available nahi hai"
    try:
        resp = scraper.get(url, timeout=timeout)
        content = resp.content
        if max_bytes and content:
            content = content[:max_bytes]
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        return content, None
    except Exception as e:
        return None, str(e)


def _http_get_stealthy(url, headers=None, timeout=20, max_bytes=None):
    """
    Pehle normal browser-header-wala fetch try karta hai (fast, zyadatar
    sites ke liye kaafi hota hai). Agar woh block ho jaaye (403/429, ya
    response ek anti-bot challenge/CAPTCHA page jaisa lage), to
    cloudscraper (stealth fetch) try karta hai. Yeh get_page_media aur
    scrape_webpage jaise "kisi bhi random website khol do" wale tools ke
    liye hai — normal _http_get ko har jagah slow nahi karta.

    NOTE: Turnstile/PerimeterX jaisi advanced behavioral anti-bot systems
    (jo mouse-movement/timing bhi check karti hain) isse bhi bypass nahi
    hoti — un sites ke liye asli browser chahiye, jo yahan possible nahi.
    """
    body, err = _http_get(url, headers=headers, timeout=timeout, max_bytes=max_bytes)
    blocked = False
    if body:
        try:
            blocked = looks_bot_blocked(body.decode("utf-8", errors="ignore"))
        except Exception:
            blocked = False

    if not err and body and not blocked:
        return body, None, False  # (content, error, stealth_use_hua_ya_nahi)

    stealth_body, stealth_err = _stealth_get(url, timeout=timeout, max_bytes=max_bytes)
    if stealth_body and not stealth_err:
        return stealth_body, None, True

    # Dono fail — jo bhi original error/blocked-status tha wahi wapas do
    if blocked:
        return body, "BOT_BLOCKED", True
    return body, (err or stealth_err), True


def _http_post(url, payload, headers=None, timeout=20):
    try:
        h = {"Content-Type": "application/json", "User-Agent": _UA}
        if headers:
            h.update(headers)
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            return r.read(), None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        return None, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return None, str(e)

def _jget(url, headers=None, timeout=15):
    """JSON GET — returns dict/list or None"""
    body, err = _http_get(url, headers, timeout)
    if body and not err:
        try:
            return json.loads(body)
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


# ─────────────────────────────────────────────
# Long-Term Memory (TinyDB agar available hai, warna
# automatically plain-JSON fallback — dono cases mein
# remember/recall/list/forget SAME tarah kaam karte hain)
# ─────────────────────────────────────────────

_TINYDB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "jarvis_db.json")
_MEMORY_FALLBACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "kv_store.json")

def _get_db():
    """TinyDB instance deta hai agar library installed hai, warna None."""
    try:
        from tinydb import TinyDB
        os.makedirs(os.path.dirname(_TINYDB_FILE), exist_ok=True)
        return TinyDB(_TINYDB_FILE)
    except ImportError:
        return None
    except Exception:
        return None

def _fallback_load(table_name: str):
    """Plain-JSON fallback store se ek 'table' (list of dicts) load karta hai."""
    try:
        os.makedirs(os.path.dirname(_MEMORY_FALLBACK_FILE), exist_ok=True)
        if not os.path.exists(_MEMORY_FALLBACK_FILE):
            return []
        with open(_MEMORY_FALLBACK_FILE, "r") as f:
            all_data = json.load(f)
        return all_data.get(table_name, [])
    except Exception:
        return []

def _fallback_save(table_name: str, rows: list):
    """Plain-JSON fallback store mein ek 'table' (list of dicts) save karta hai."""
    try:
        os.makedirs(os.path.dirname(_MEMORY_FALLBACK_FILE), exist_ok=True)
        all_data = {}
        if os.path.exists(_MEMORY_FALLBACK_FILE):
            try:
                with open(_MEMORY_FALLBACK_FILE, "r") as f:
                    all_data = json.load(f)
            except Exception:
                all_data = {}
        all_data[table_name] = rows
        with open(_MEMORY_FALLBACK_FILE, "w") as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

def remember(key: str, value: str):
    key, value = (key or "").strip(), (value or "").strip()
    if not key:
        return "❌ Kis naam se yaad rakhun, woh batao."
    now = datetime.datetime.now().isoformat()
    db = _get_db()
    if db:
        from tinydb import Query
        T = Query()
        table = db.table("memories")
        if table.search(T.key == key):
            table.update({"value": value, "updated": now}, T.key == key)
        else:
            table.insert({"key": key, "value": value, "created": now, "updated": now})
        db.close()
    else:
        rows = _fallback_load("memories")
        existing = next((r for r in rows if r.get("key") == key), None)
        if existing:
            existing["value"] = value
            existing["updated"] = now
        else:
            rows.append({"key": key, "value": value, "created": now, "updated": now})
        _fallback_save("memories", rows)
    return f"✅ Yaad rakh liya: '{key}' = '{value}'"

def recall(key: str):
    key = (key or "").strip()
    if not key:
        return "❌ Kis baare mein yaad karna hai, woh batao."
    db = _get_db()
    if db:
        from tinydb import Query
        T = Query()
        table = db.table("memories")
        results = table.search(T.key == key)
        db.close()
    else:
        rows = _fallback_load("memories")
        results = [r for r in rows if r.get("key") == key]
    if results:
        r = results[0]
        return f"🧠 '{key}' = '{r['value']}' (saved: {r.get('updated','?')[:10]})"
    return f"❌ '{key}' ke baare mein kuch yaad nahi."

def list_memories():
    db = _get_db()
    if db:
        table = db.table("memories")
        all_mem = table.all()
        db.close()
    else:
        all_mem = _fallback_load("memories")
    if not all_mem:
        return "🧠 Abhi koi memory save nahi hai."
    lines = [f"• {m['key']}: {m['value']}" for m in all_mem]
    return "🧠 Meri yaadein:\n" + "\n".join(lines)

def forget(key: str):
    key = (key or "").strip()
    if not key:
        return "❌ Kis baare mein bhoolna hai, woh batao."
    db = _get_db()
    if db:
        from tinydb import Query
        T = Query()
        table = db.table("memories")
        removed = table.remove(T.key == key)
        db.close()
        return f"🗑️ '{key}' bhool gaya." if removed else f"'{key}' mujhe pata hi nahi tha."
    else:
        rows = _fallback_load("memories")
        new_rows = [r for r in rows if r.get("key") != key]
        removed = len(new_rows) != len(rows)
        _fallback_save("memories", new_rows)
        return f"🗑️ '{key}' bhool gaya." if removed else f"'{key}' mujhe pata hi nahi tha."



# ─────────────────────────────────────────────
# API Key Management
# ─────────────────────────────────────────────

def save_api_key(name: str, value: str):
    standard_name = memory.normalize_api_name(name)
    memory.save_secret(standard_name, value)
    return f"'{standard_name}' API key save ho gayi."

def delete_api_key(name: str):
    standard_name = memory.normalize_api_name(name)
    deleted = memory.delete_secret(standard_name)
    return f"'{standard_name}' key hata di." if deleted else f"'{standard_name}' key nahi thi."

def list_api_keys():
    keys = [k for k in memory.list_known_secrets() if not k.startswith("_")]
    return ("Saved keys: " + ", ".join(keys)) if keys else "Abhi koi API key save nahi hai."


# ─────────────────────────────────────────────
# Time / Date
# ─────────────────────────────────────────────

def get_current_time():
    now = datetime.datetime.now()
    days = {0:"Somwar",1:"Mangalwar",2:"Budhwar",3:"Guruwar",
            4:"Shukrawar",5:"Shaniwar",6:"Raviwar"}
    return (f"Abhi {days[now.weekday()]}, {now.day} {now.strftime('%B')} "
            f"{now.year} hai. Time: {now.strftime('%I:%M %p')}.")


# ─────────────────────────────────────────────
# Phone Control (Termux:API)
# ─────────────────────────────────────────────

def _run(cmd, timeout=15):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def _dispatch_phone_tool(tool_name: str, args: dict, local_check_bin: str, timeout: int = 20):
    """
    Jarvis do jagah chal sakta hai: (1) seedha phone par Termux ke andar,
    (2) cloud (Render) par, jahan asli phone hardware nahi hota.

    Ye function decide karta hai konsa case hai:
    - Agar `local_check_bin` (jaise 'termux-torch') isi machine par milta hai,
      matlab hum khud phone par hain — None return karta hai, taaki caller
      function apna normal (purana) local-execution wala code chalaye.
    - Agar nahi milta (matlab cloud par hain), to phone_bridge ke through
      connected phone-agent ko command bhejta hai aur result wait karta hai.
    """
    if shutil.which(local_check_bin):
        return None  # local execution karo, jaisa pehle hota tha

    try:
        import phone_bridge
    except ImportError:
        return "❌ Ye command sirf phone par ya connected phone-agent ke saath kaam karti hai."

    result, err = phone_bridge.submit_job(tool_name, args, timeout=timeout)
    if err:
        return ("📵 Phone se connect nahi ho paya. Check karo:\n"
                "1) Apne phone par Termux mein `python phone_agent.py` chal raha hai?\n"
                "2) Phone ka internet ON hai?\n"
                "3) phone_agent.py mein JARVIS_SERVER_URL aur PHONE_AGENT_TOKEN sahi hain?")
    return result


def set_alarm(hour: int, minute: int, message: str = "Jarvis Alarm"):
    bridged = _dispatch_phone_tool("set_alarm", {"hour": hour, "minute": minute, "message": message}, "am")
    if bridged is not None:
        return bridged
    try:
        subprocess.run([
            "am", "start", "-a", "android.intent.action.SET_ALARM",
            "--ei", "android.intent.extra.alarm.HOUR", str(hour),
            "--ei", "android.intent.extra.alarm.MINUTES", str(minute),
            "--es", "android.intent.extra.alarm.MESSAGE", message,
            "--ez", "android.intent.extra.alarm.SKIP_UI", "true",
        ], check=True, capture_output=True, timeout=10)
        return f"Alarm laga diya {hour}:{minute:02d} baje — '{message}'."
    except Exception as e:
        return f"Alarm error: {e}"

def make_call(phone_number: str):
    bridged = _dispatch_phone_tool("make_call", {"phone_number": phone_number}, "termux-telephony-call")
    if bridged is not None:
        return bridged
    try:
        subprocess.run(["termux-telephony-call", phone_number], check=True, timeout=10)
        return f"{phone_number} par call laga raha hoon."
    except Exception as e:
        return f"Call error: {e}"

def send_sms(phone_number: str, message: str):
    bridged = _dispatch_phone_tool("send_sms", {"phone_number": phone_number, "message": message}, "termux-sms-send")
    if bridged is not None:
        return bridged
    try:
        subprocess.run(["termux-sms-send", "-n", phone_number, message], check=True, timeout=15)
        return f"{phone_number} ko SMS bhej diya."
    except Exception as e:
        return f"SMS error: {e}"

def open_app(package_name: str):
    bridged = _dispatch_phone_tool("open_app", {"package_name": package_name}, "am")
    if bridged is not None:
        return bridged
    try:
        subprocess.run(["am", "start", "-n", package_name], check=True, capture_output=True, timeout=10)
        return f"{package_name} khol diya."
    except Exception as e:
        return f"App open error: {e}"

def get_battery_status():
    bridged = _dispatch_phone_tool("get_battery_status", {}, "termux-battery-status")
    if bridged is not None:
        return bridged
    try:
        out, _, _ = _run(["termux-battery-status"], timeout=10)
        d = json.loads(out)
        pct = d.get("percentage","?")
        status = d.get("status","?")
        plugged = d.get("plugged","")
        cs = f", {plugged} se charge ho raha hai" if status == "CHARGING" else ""
        return f"Battery {pct}% — {status}{cs}."
    except Exception as e:
        return f"Battery error: {e}"

def send_notification(title: str, content: str):
    bridged = _dispatch_phone_tool("send_notification", {"title": title, "content": content}, "termux-notification")
    if bridged is not None:
        return bridged
    try:
        subprocess.run(["termux-notification","--title",title,"--content",content], check=True, timeout=10)
        return f"Notification bhej di: '{title}'."
    except Exception as e:
        return f"Notification error: {e}"

def vibrate(duration_ms: int = 500):
    bridged = _dispatch_phone_tool("vibrate", {"duration_ms": duration_ms}, "termux-vibrate")
    if bridged is not None:
        return bridged
    try:
        subprocess.run(["termux-vibrate","-d",str(duration_ms)], check=True, timeout=5)
        return f"Phone vibrate kiya {duration_ms}ms."
    except Exception as e:
        return f"Vibrate error: {e}"

def toggle_torch(on: bool = True):
    bridged = _dispatch_phone_tool("toggle_torch", {"on": on}, "termux-torch")
    if bridged is not None:
        return bridged
    try:
        subprocess.run(["termux-torch","on" if on else "off"], check=True, timeout=5)
        return f"Torch {'on' if on else 'off'} kar di."
    except Exception as e:
        return f"Torch error: {e}"


# ─────────────────────────────────────────────
# OpenStreetMap / Nominatim — Location & Maps
# ─────────────────────────────────────────────

def get_location():
    """
    Advanced location fetch: pehle GPS provider try karta hai (zyada accurate,
    village-level tak sahi), agar woh fail/timeout ho (indoor, khula aasmaan
    na mile) to network provider par fallback karta hai (fast, kam accurate).
    """
    bridged = _dispatch_phone_tool("get_location", {}, "termux-location", timeout=30)
    if bridged is not None:
        return bridged

    providers_to_try = [("gps", 25), ("network", 15), ("passive", 8)]
    last_err = None
    for provider, timeout in providers_to_try:
        try:
            out, err, rc = _run(["termux-location", "-p", provider, "-r", "once"], timeout=timeout)
            if out:
                try:
                    d = json.loads(out)
                except Exception:
                    d = None
                if d and d.get("latitude") is not None:
                    lat, lon = d.get("latitude"), d.get("longitude")
                    acc = d.get("accuracy", "?")
                    result = reverse_geocode(lat, lon)
                    provider_note = "" if provider == "gps" else f" (via {provider})"
                    return (f"{result}\n"
                            f"📍 Coordinates: {lat:.5f}, {lon:.5f} (~{acc}m accuracy{provider_note})\n"
                            f"🗺️ Maps: https://maps.google.com/?q={lat},{lon}")
            last_err = err or "no output"
        except subprocess.TimeoutExpired:
            last_err = "timeout"
        except Exception as e:
            last_err = str(e)
    return (f"❌ Location nahi mil payi ({last_err}). Check karo:\n"
            f"1) Termux:API app install hai?\n"
            f"2) Phone Settings → Termux:API → Location permission 'Allow' hai?\n"
            f"3) GPS/Location ON hai?\n"
            f"4) Termux app foreground mein khula hai? (background mein Android block karta hai)")

def reverse_geocode(lat: float, lon: float):
    """
    Advanced reverse-geocoding: pehle OpenStreetMap Nominatim try karta hai,
    agar city/town/village khaali mile (chhote/remote gaon ka data OSM ke
    paas kam hota hai) to BigDataCloud API (free, no key) se fallback leta
    hai — yeh remote/rural areas mein aksar behtar locality naam deta hai.
    """
    city, state, country, display = "", "", "", ""
    try:
        data = _jget(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=16&addressdetails=1",
            headers={"User-Agent": "JarvisApp/6.0 (personal assistant)"})
        if data:
            addr = data.get("address", {})
            city = (addr.get("village") or addr.get("town") or addr.get("city")
                    or addr.get("suburb") or addr.get("hamlet") or addr.get("county") or "")
            state = addr.get("state", "")
            country = addr.get("country", "")
            display = data.get("display_name", "")
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")

    # Fallback / fill-gaps: BigDataCloud (better for rural/remote areas)
    if not city or not state:
        try:
            bdc = _jget(
                f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={lat}&longitude={lon}&localityLanguage=en")
            if bdc:
                city = city or bdc.get("locality") or bdc.get("city") or bdc.get("localityInfo", {}).get("administrative", [{}])[0].get("name", "")
                state = state or bdc.get("principalSubdivision", "")
                country = country or bdc.get("countryName", "")
                if not display:
                    display = ", ".join(filter(None, [bdc.get("locality"), bdc.get("city"), bdc.get("principalSubdivision"), bdc.get("countryName")]))
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    if city or state or country:
        parts = [p for p in (city, state, country) if p]
        header = ", ".join(parts) if parts else "Location mil gayi"
        return f"📍 Aap abhi yahan hain: {header}\n   ({display[:90]})" if display else f"📍 Aap abhi yahan hain: {header}"
    return f"📍 Location: {lat:.5f}, {lon:.5f} (jagah ka naam resolve nahi ho paya, coordinates hi hain)"

def search_place_osm(place: str):
    try:
        data = _jget(
            f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(place)}&format=json&limit=3&addressdetails=1",
            headers={"User-Agent": "JarvisApp/5.0"})
        if not data:
            return f"'{place}' nahi mila."
        lines = [f"🗺️ '{place}' ke results:"]
        for r in data[:3]:
            name = r.get("display_name","")[:80]
            lat = r.get("lat","")
            lon = r.get("lon","")
            lines.append(f"• {name}\n  Maps: https://maps.google.com/?q={lat},{lon}")
        return "\n".join(lines)
    except Exception as e:
        return f"OSM search error: {e}"


# ─────────────────────────────────────────────
# Weather — Open-Meteo (no key) + OpenWeatherMap
# ─────────────────────────────────────────────

def get_weather(city: str):
    try:
        geo = _jget(
            f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(city)}&format=json&limit=1",
            headers={"User-Agent": "JarvisApp/5.0"})
        if geo:
            lat = float(geo[0]["lat"])
            lon = float(geo[0]["lon"])
            data = _jget(
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
                f"&timezone=auto")
            if data:
                cur = data.get("current", {})
                temp = cur.get("temperature_2m","?")
                humid = cur.get("relative_humidity_2m","?")
                wind = cur.get("wind_speed_10m","?")
                code = cur.get("weather_code",0)
                wdesc = {0:"Clear sky",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",
                         45:"Foggy",48:"Icy fog",51:"Light drizzle",53:"Drizzle",
                         61:"Light rain",63:"Rain",71:"Light snow",73:"Snow",
                         80:"Rain showers",81:"Heavy showers",95:"Thunderstorm"}.get(int(code),"Weather data")
                return (f"🌤️ {city}: {temp}°C — {wdesc}\n"
                        f"💧 Humidity: {humid}% | 💨 Wind: {wind} km/h")
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")

    api_key = memory.get_secret("weather")
    if api_key:
        body, err = _http_get(
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={urllib.parse.quote(city)}&appid={api_key}&units=metric")
        if not err:
            d = json.loads(body)
            temp = d["main"]["temp"]
            desc = d["weather"][0]["description"]
            humid = d["main"]["humidity"]
            return f"🌤️ {city}: {temp}°C, {desc}. Humidity {humid}%."

    return f"'{city}' ka weather nahi mila. Internet check karo."


# ─────────────────────────────────────────────
# News
# ─────────────────────────────────────────────

def get_news(topic: str = "india"):
    for rss_url in [
        f"https://news.google.com/rss/search?q={urllib.parse.quote(topic)}&hl=en-IN&gl=IN&ceid=IN:en",
        f"https://feeds.feedburner.com/ndtvnews-india-news",
    ]:
        try:
            body, err = _http_get(rss_url, headers={"Accept":"application/rss+xml"})
            if body:
                xml = body.decode("utf-8", errors="ignore")
                titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", xml)
                if not titles:
                    titles = re.findall(r"<title>(.*?)</title>", xml)
                titles = [re.sub(r"<[^>]+>","",t).strip()
                          for t in titles if t and "Google" not in t[:10]][:6]
                if len(titles) >= 2:
                    return f"📰 '{topic}' ki news:\n" + "\n".join(f"{i+1}. {t}" for i,t in enumerate(titles))
        except Exception:
            continue
    return f"'{topic}' ki news nahi mili."


# ─────────────────────────────────────────────
# DuckDuckGo Web Search (no key needed)
# ─────────────────────────────────────────────

def _ddg_text_search(query: str, max_results: int = 5):
    """
    DDG text search — isolated subprocess mein (crash-safe). ddgs ka
    native (Rust) backend Termux/Android ARM par process-level crash de
    sakta hai jise normal try/except catch nahi kar paata; is wajah se
    pehle web_search() aur find_and_play() dono mein yeh silently kabhi
    kaam hi nahi karta tha (bina koi error dikhaye). Ab crash ho bhi to
    sirf yeh subprocess marta hai, Jarvis process safe rehta hai.
    Returns list of {"title","body","href"} dicts, ya [] agar kuch na mile.
    """
    code = (
        "import json\n"
        "try:\n"
        "    from ddgs import DDGS\n"
        f"    with DDGS() as ddgs:\n"
        f"        results = list(ddgs.text({query!r}, max_results={max_results}))\n"
        "    out = [{'title': r.get('title',''), 'body': r.get('body',''),"
        " 'href': r.get('href','')} for r in results]\n"
        "    print(json.dumps(out))\n"
        "except Exception:\n"
        "    print(json.dumps([]))\n"
    )
    return _run_isolated(code, timeout=20) or []


def web_search(query: str):
    """DuckDuckGo se web search — koi key nahi chahiye. 3 fallback layers hain:
    ddgs library (isolated, crash-safe) → HTML scrape → Tavily (agar key saved hai)."""
    query = (query or "").strip()
    if not query:
        return "❌ Kya search karna hai, woh batao."

    last_err = None
    results = _ddg_text_search(query, max_results=5)
    if results:
        lines = [f"🔍 '{query}' ke results:"]
        for r in results[:5]:
            title = r.get("title","")
            body = r.get("body","")[:150]
            url = r.get("href","")
            lines.append(f"\n• {title}\n  {body}\n  🔗 {url}")
        return "\n".join(lines)

    try:
        body, err = _http_get(
            f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}",
            headers={"Accept":"text/html"}, timeout=20)
        if err:
            last_err = err
        if body:
            html = body.decode("utf-8", errors="ignore")
            titles = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.DOTALL)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:div|span)', html, re.DOTALL)
            titles = [re.sub(r"<[^>]+>","",t).strip() for t in titles[:5]]
            snippets = [re.sub(r"<[^>]+>","",s).strip()[:120] for s in snippets[:5]]
            if titles:
                lines = [f"🔍 '{query}':"]
                for i, t in enumerate(titles):
                    s = snippets[i] if i < len(snippets) else ""
                    lines.append(f"\n{i+1}. {t}\n   {s}")
                return "\n".join(lines)
    except Exception as e:
        last_err = str(e)

    key = memory.get_secret("tavily")
    if key:
        try:
            body, err = _http_post("https://api.tavily.com/search", {
                "api_key": key, "query": query, "max_results": 5, "include_answer": True})
            if not err and body:
                data = json.loads(body)
                lines = []
                if data.get("answer"):
                    lines.append(f"📌 {data['answer']}\n")
                for r in data.get("results",[])[:4]:
                    lines.append(f"• {r.get('title','')}\n  {r.get('content','')[:150]}\n  🔗 {r.get('url','')}")
                if lines:
                    return "\n\n".join(lines)
            else:
                last_err = err
        except Exception as e:
            last_err = str(e)

    return (f"❌ '{query}' ka search nahi ho paya (koi search source available nahi tha). "
            f"Internet connection check karo — WiFi/mobile data ON hai? "
            f"({last_err or 'no details'})")


# ─────────────────────────────────────────────
# IMAGE SEARCH — icrawler + DDG + Wikimedia
# Pexels REMOVED completely
# ─────────────────────────────────────────────

def _search_images_icrawler(query: str, count: int = 4):
    """
    icrawler se real internet images — Google + Bing crawler.
    Crawl khud ek ALAG subprocess mein hota hai (crash-isolation) — files
    seedha disk par session_dir mein save hoti hain, isliye subprocess crash
    bhi ho jaaye to bhi ab tak download hui images hum yahan se uthate hain.
    Images /static/crawled/ folder mein save hoti hain aur IMAGE_FOUND se serve hoti hain.
    """
    try:
        import uuid

        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "crawled")
        os.makedirs(save_dir, exist_ok=True)

        # Purani files clean karo (2 ghante se zyada purani)
        import time
        for fname in os.listdir(save_dir):
            fpath = os.path.join(save_dir, fname)
            try:
                if time.time() - os.path.getmtime(fpath) > 7200:
                    os.remove(fpath)
            except Exception:
                log.exception("unexpected error - see memory/jarvis_errors.log")

        session_id = uuid.uuid4().hex[:8]
        session_dir = os.path.join(save_dir, session_id)
        os.makedirs(session_dir, exist_ok=True)
        bing_dir = os.path.join(session_dir, "bing")

        crawl_code = (
            "import os\n"
            "try:\n"
            "    from icrawler.builtin import GoogleImageCrawler, BingImageCrawler\n"
            "    try:\n"
            f"        c = GoogleImageCrawler(storage={{'root_dir': {session_dir!r}}}, log_level=50)\n"
            f"        c.crawl(keyword={query!r}, max_num={count}, min_size=(100, 100))\n"
            "    except Exception:\n"
            "        pass\n"
            f"    if len([f for f in os.listdir({session_dir!r}) if os.path.isfile(os.path.join({session_dir!r}, f))]) < {count}:\n"
            "        try:\n"
            f"            os.makedirs({bing_dir!r}, exist_ok=True)\n"
            f"            c2 = BingImageCrawler(storage={{'root_dir': {bing_dir!r}}}, log_level=50)\n"
            f"            c2.crawl(keyword={query!r}, max_num={count}, min_size=(100, 100))\n"
            "        except Exception:\n"
            "            pass\n"
            "except Exception:\n"
            "    pass\n"
            "print('[]')\n"
        )
        # Result yahan use nahi hota — bas crawl complete hone (ya crash hone)
        # tak wait karte hain, files disk par already save ho chuki hoti hain.
        _run_isolated(crawl_code, timeout=30)

        downloaded = []
        for f in sorted(os.listdir(session_dir))[:count]:
            fpath = os.path.join(session_dir, f)
            if os.path.isfile(fpath) and f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                downloaded.append(f"/static/crawled/{session_id}/{f}")
        if len(downloaded) < count and os.path.isdir(bing_dir):
            for f in sorted(os.listdir(bing_dir))[:count - len(downloaded)]:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    downloaded.append(f"/static/crawled/{session_id}/bing/{f}")

        if downloaded:
            lines = [f"IMAGE_FOUND:{p}" for p in downloaded[:count]]
            return f"'{query}' ki images (Internet):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_ddg(query: str, count: int = 4):
    """DuckDuckGo image search — library + fallback.
    ddgs library ka native backend Termux par crash kar sakta hai, isliye
    ALAG subprocess mein isolate karke chalaya jaata hai (_run_isolated)."""
    code = (
        "import json\n"
        "try:\n"
        "    from ddgs import DDGS\n"
        f"    with DDGS() as ddgs:\n"
        f"        results = list(ddgs.images({query!r}, max_results={count * 2}))\n"
        "    urls = [r.get('image','') for r in results if r.get('image','').startswith('http') and not r.get('image','').endswith('.svg')]\n"
        f"    print(json.dumps(urls[:{count}]))\n"
        "except Exception:\n"
        "    print(json.dumps([]))\n"
    )
    urls = _run_isolated(code, timeout=25)
    if urls:
        lines_out = [f"IMAGE_FOUND:{u}" for u in urls]
        return f"'{query}' ki images (Internet):\n" + "\n".join(lines_out)

    # DDG direct API fallback — regex FIXED
    try:
        hdrs = {"User-Agent": _UA, "Referer": "https://duckduckgo.com/"}
        token_body, _ = _http_get(
            f"https://duckduckgo.com/?q={urllib.parse.quote(query)}&iax=images&ia=images",
            headers=hdrs)
        if token_body:
            html = token_body.decode("utf-8", errors="ignore")
            # Fixed regex — raw string properly escaped
            vqd_m = re.search(r"vqd=(['\"]?)([^&'\"<>\s]+)\1", html)
            if vqd_m:
                vqd_val = vqd_m.group(2)
                img_body, _ = _http_get(
                    f"https://duckduckgo.com/i.js?q={urllib.parse.quote(query)}&vqd={vqd_val}&p=1&f=,,,,,",
                    headers=hdrs)
                if img_body:
                    img_data = json.loads(img_body)
                    lines_out = []
                    for r in img_data.get("results",[]):
                        img_url = r.get("image","")
                        if img_url and img_url.startswith("http"):
                            lines_out.append(f"IMAGE_FOUND:{img_url}")
                            if len(lines_out) >= count:
                                break
                    if lines_out:
                        return f"'{query}' ki images (Internet):\n" + "\n".join(lines_out)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_google(query: str, count: int = 4):
    """
    Google Custom Search JSON API — user apni 'google_api' aur 'google_cx'
    key settings mein daale to yeh sabse pehle try hoga (Jarvis code api:
    google_api TERI_KEY aur Jarvis code api: google_cx TERA_CX bolkar).
    Free tier: 100 searches/din. Key: https://developers.google.com/custom-search/v1/introduction
    CX (Search Engine ID, "Image search" ON karke banao): https://programmablesearchengine.google.com/
    """
    key = memory.get_secret("google_api")
    cx = memory.get_secret("google_cx")
    if not key or not cx:
        return None
    try:
        data = _jget(
            "https://www.googleapis.com/customsearch/v1"
            f"?key={urllib.parse.quote(key)}&cx={urllib.parse.quote(cx)}"
            f"&q={urllib.parse.quote(query)}&searchType=image&num={min(count, 10)}&safe=active")
        if not data:
            return None
        items = data.get("items", [])
        lines = []
        for it in items[:count]:
            img_url = it.get("link", "")
            if img_url and img_url.startswith("http"):
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (Google):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_videos_google(query: str, count: int = 3):
    """
    YouTube Data API v3 — user apni 'youtube' API key settings mein daale to
    video search official API se hoti hai (yt-dlp scraping se zyada reliable,
    kabhi block nahi hoti). Playback hamesha official YouTube embed se hoti
    hai isliye yeh key sirf BEHTAR SEARCH RESULTS ke liye hai.
    Key: https://console.cloud.google.com/apis/library/youtube.googleapis.com
    """
    yt_key = memory.get_secret("youtube")
    if not yt_key:
        return None
    try:
        data = _jget(
            "https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet&q={urllib.parse.quote(query)}&type=video"
            f"&maxResults={count}&key={urllib.parse.quote(yt_key)}")
        if not data:
            return None
        lines = []
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId", "")
            title = item.get("snippet", {}).get("title", query)
            if vid:
                lines.append(f"VIDEO_FOUND:https://www.youtube.com/watch?v={vid}|{title}")
        if lines:
            return f"'{query}' ke videos (YouTube):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_pixabay(query: str, count: int = 4):
    """
    Pixabay API — free key (5000 req/hour, kabhi block/rate-limit nahi hoti
    kyunki ye dedicated JSON API hai, scraping nahi). Sabse reliable option
    agar key ho. Free key yahan se banao (30 second signup):
    https://pixabay.com/api/docs/
    Phir Jarvis mein bolo: "Jarvis code api: pixabay TERI_KEY"
    """
    key = memory.get_secret("pixabay")
    if not key:
        return None
    try:
        data = _jget(
            f"https://pixabay.com/api/?key={urllib.parse.quote(key)}"
            f"&q={urllib.parse.quote(query)}&image_type=photo&safesearch=true"
            f"&per_page={max(count, 3)}")
        if not data:
            return None
        lines = []
        for hit in data.get("hits", [])[:count]:
            img_url = hit.get("largeImageURL") or hit.get("webformatURL", "")
            if img_url and img_url.startswith("http"):
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (Pixabay):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


_openverse_token_cache = {"token": None, "expires": 0}


def _get_openverse_auth_header():
    """
    Openverse simple API key nahi deta — OAuth2 client_credentials use
    karta hai. Agar user ne 'openverse_id' aur 'openverse_secret' save
    kiye hain (app register karke: https://api.openverse.org/v1/auth_tokens/register/),
    to ek access token le aata hai (cached, auto-refresh) jisse higher
    rate limits milte hain. Nahi hai to anonymous access use hota hai
    (already free, bas lower rate limit).
    """
    client_id = memory.get_secret("openverse_id")
    client_secret = memory.get_secret("openverse_secret")
    if not client_id or not client_secret:
        return None
    cache = _openverse_token_cache
    if cache["token"] and time.time() < cache["expires"] - 60:
        return {"Authorization": f"Bearer {cache['token']}"}
    try:
        body = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            "https://api.openverse.org/v1/auth_tokens/token/", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        token = data.get("access_token")
        expires_in = data.get("expires_in", 43200)
        if token:
            cache["token"] = token
            cache["expires"] = time.time() + expires_in
            return {"Authorization": f"Bearer {token}"}
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_openverse(query: str, count: int = 4):
    """Openverse — free, no key, stable JSON API (scraping nahi), CC-licensed
    real search results. Google/DDG/Bing jaisi anti-bot blocking nahi hoti.
    Agar openverse_id/openverse_secret save hain to authenticated request
    (higher rate limit) jaati hai, warna anonymous (already free/working)."""
    try:
        url = (f"https://api.openverse.org/v1/images/?q={urllib.parse.quote(query)}"
               f"&page_size={count}&mature=false")
        auth_header = _get_openverse_auth_header()
        data = _jget(url, headers=auth_header) if auth_header else _jget(url)
        if data:
            lines = []
            for r in data.get("results", [])[:count]:
                img_url = r.get("url", "")
                if img_url and img_url.startswith("http"):
                    lines.append(f"IMAGE_FOUND:{img_url}")
            if lines:
                return f"'{query}' ki images (Openverse):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_wikimedia(query: str, count: int = 4):
    """Wikimedia Commons — free, no key, stable JSON API."""
    try:
        data = _jget(
            f"https://commons.wikimedia.org/w/api.php"
            f"?action=query&list=search&srsearch={urllib.parse.quote(query)}"
            f"&srnamespace=6&srlimit={count * 4}&format=json")
        if not data:
            return None
        results = data.get("query", {}).get("search", [])
        file_titles = []
        for r in results:
            title = r.get("title", "")
            if title.startswith("File:"):
                ext = title.lower().rsplit(".", 1)[-1]
                if ext in ("jpg", "jpeg", "png", "webp", "gif"):
                    file_titles.append(title)
            if len(file_titles) >= count * 2:
                break
        if not file_titles:
            return None
        titles_param = "|".join(file_titles[:count * 2])
        info_data = _jget(
            f"https://commons.wikimedia.org/w/api.php"
            f"?action=query&titles={urllib.parse.quote(titles_param)}"
            f"&prop=imageinfo&iiprop=url&format=json")
        lines = []
        if info_data:
            pages = info_data.get("query", {}).get("pages", {})
            for pg in pages.values():
                ii = pg.get("imageinfo", [{}])
                img_url = ii[0].get("url", "") if ii else ""
                if img_url and img_url.startswith("http"):
                    lines.append(f"IMAGE_FOUND:{img_url}")
                if len(lines) >= count:
                    break
        if lines:
            return f"'{query}' ki images (Wikimedia):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_archive_org(query: str, count: int = 4):
    """
    Internet Archive — free, no key, stable JSON API (scraping nahi).
    Lakhon public-domain photos/scans. General queries ke liye Openverse/
    Wikimedia jitna accurate nahi hota (zyada archival/historical content),
    isliye last fallback ke taur pe rakha hai.
    """
    try:
        data = _jget(
            f"https://archive.org/advancedsearch.php?q="
            f"{urllib.parse.quote(query)}+AND+mediatype:image"
            f"&fl[]=identifier&rows={count}&output=json")
        if not data:
            return None
        docs = data.get("response", {}).get("docs", [])
        lines = []
        for d in docs[:count]:
            ident = d.get("identifier", "")
            if ident:
                img_url = f"https://archive.org/services/img/{ident}"
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (Internet Archive):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_bing_scrape(query: str, count: int = 4):
    """
    Bing Image Search — direct HTML scrape (koi library/subprocess nahi,
    seedha _http_get + regex). icrawler se ALAG failure-mode hai (icrawler
    Selenium-jaisi multi-thread crawling karta hai jo subprocess isolation
    maangti hai; ye ek simple GET request hai) — isliye jab stable APIs
    (Pixabay/Openverse/Wikimedia) aur icrawler dono fail ho jaayein, tab bhi
    ye kaam kar sakta hai.
    """
    try:
        url = f"https://www.bing.com/images/search?q={urllib.parse.quote(query)}&form=HDRSC2"
        body, err = _http_get(url, timeout=15)
        if err or not body:
            return None
        html = body.decode(errors="ignore")
        # Bing har image result ke JSON blob mein "murl":"<direct_image_url>" rakhta hai
        urls = re.findall(r'"murl":"(https?://[^"]+?)"', html)
        lines = []
        seen = set()
        for u in urls:
            u = u.encode().decode("unicode_escape")
            if u not in seen and u.startswith("http"):
                seen.add(u)
                lines.append(f"IMAGE_FOUND:{u}")
            if len(lines) >= count:
                break
        if lines:
            return f"'{query}' ki images (Bing):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _validate_image_url(url: str, timeout: int = 4) -> str:
    """
    URL par halka HEAD (fallback: chhota Range GET) request karke check
    karta hai ki asal mein image milegi ya URL DEAD hai. Openverse jaise
    aggregators ke dataset mein purane/hate hue links kaafi common hain —
    isliye yahan clear 404/410/4xx/5xx wale links reject hote hain.

    Teen states return karta hai (bool nahi — taaki "shayad valid hai"
    aur "PAKKA valid hai" mein farak pata chale):
      "ok"      — server ne confirm kiya (status < 400)
      "dead"    — server ne confirm kiya ki broken hai (status >= 400)
      "unknown" — timeout/network-block (definitive dead-link proof
                  nahi, lekin definitive live-proof bhi nahi)
    """
    headers = {"User-Agent": _UA, "Accept": "image/*,*/*"}
    try:
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return "ok" if resp.status < 400 else "dead"
    except urllib.error.HTTPError as e:
        return "ok" if e.code < 400 else "dead"
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-256"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return "ok" if resp.status < 400 else "dead"
    except urllib.error.HTTPError as e:
        return "ok" if e.code < 400 else "dead"
    except Exception:
        return "unknown"  # timeout/block — pakka dead nahi maan sakte, but pakka live bhi nahi


def _filter_dead_images(result_text, count: int):
    """
    search provider ke IMAGE_FOUND result mein se dead/broken URLs nikaal
    deta hai (parallel HEAD checks — chhota overhead). Agar sab URLs dead
    nikle to None deta hai taaki search_images() agle provider pe fall-
    through kare, jaisa ki pehle se ho raha tha jab provider bilkul kuch
    na de.

    STRICT RULE: batch tabhi accept hota hai jab kam se kam EK image
    "ok" (server-confirmed live) ho. Agar SAARI images sirf "unknown"
    (timeout/block) nikli — matlab provider ka poora batch hi verify
    nahi ho paaya — to bhi None deta hai, taaki agle (behtar) provider
    ko mauka mile. Yeh Openverse jaise sources ke liye zaroori hai jinke
    broken/unreachable links "benefit of doubt" ki wajah se pehle chupke
    se pass ho jaate the aur user ko sirf error/broken image dikhta tha.
    """
    if not result_text:
        return None
    lines = result_text.splitlines()
    header = lines[0] if lines and not lines[0].startswith("IMAGE_FOUND:") else None
    urls = [ln.split("IMAGE_FOUND:", 1)[1] for ln in lines if ln.startswith("IMAGE_FOUND:")]
    if not urls:
        return None

    confirmed, unknown, dead = [], [], []
    with ThreadPoolExecutor(max_workers=min(6, len(urls))) as ex:
        futures = {ex.submit(_validate_image_url, u): u for u in urls}
        for fut in as_completed(futures):
            u = futures[fut]
            try:
                status = fut.result()
            except Exception:
                status = "dead"
            if status == "ok":
                confirmed.append(u)
            elif status == "unknown":
                unknown.append(u)
            else:
                dead.append(u)

    if confirmed:
        valid = confirmed + unknown
    elif not dead and unknown:
        # Koi bhi URL explicitly "dead" nahi nikla — sab sirf unverifiable
        # (timeout/block) hain. Yeh provider ke broken hone ka saaf sabut
        # nahi, ho sakta hai poori tarah network/connectivity ka blip ho —
        # is case mein benefit of doubt do (jaisa pehle sabke liye hota
        # tha), warna kabhi kabhi HAR provider fail ho jaayega aur kuch
        # bhi nahi dikhega.
        valid = unknown
    else:
        # Kam se kam ek confirmed-dead link mila aur koi confirmed-live
        # nahi — yeh provider genuinely broken lag raha hai (jaisa
        # Openverse ke purane dataset ke saath hota tha). Agle provider
        # pe fall-through karo.
        return None

    valid = valid[:count]
    out_lines = ([header] if header else []) + [f"IMAGE_FOUND:{u}" for u in valid]
    return "\n".join(out_lines)


def _search_images_brave(query: str, count: int = 4):
    """
    Brave Search Image API — free key (2000 queries/month), stable JSON,
    scraping nahi. NOTE: Microsoft ne apna Bing Search API Aug 2025 mein
    permanently retire kar diya (koi naya key ab milta hi nahi) — Brave
    isi jagah ka sabse accurate/reliable replacement hai.
    Free key: https://api-dashboard.search.brave.com/register
    Phir bolo: "Jarvis code api: brave_search TERI_KEY"
    """
    key = memory.get_secret("brave_search")
    if not key:
        return None
    try:
        data = _jget(
            "https://api.search.brave.com/res/v1/images/search"
            f"?q={urllib.parse.quote(query)}&count={min(count, 20)}&safesearch=strict",
            headers={"Accept": "application/json", "X-Subscription-Token": key})
        if not data:
            return None
        lines = []
        for r in data.get("results", [])[:count]:
            props = r.get("properties", {}) or {}
            img_url = props.get("url") or (r.get("thumbnail") or {}).get("src", "")
            if img_url and img_url.startswith("http"):
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (Brave):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_serper(query: str, count: int = 4):
    """
    Serper.dev — Google Images ka JSON API (SERP scraping nahi, seedha
    Google ke real results, celebrity/current-event queries ke liye
    sabse accurate). Free key: 2500 queries free signup pe.
    Key: https://serper.dev
    Phir bolo: "Jarvis code api: serper TERI_KEY"
    """
    key = memory.get_secret("serper")
    if not key:
        return None
    try:
        body, err = _http_post(
            "https://google.serper.dev/images",
            {"q": query, "num": min(count, 20)},
            headers={"X-API-KEY": key})
        if err or not body:
            return None
        data = json.loads(body)
        lines = []
        for r in data.get("images", [])[:count]:
            img_url = r.get("imageUrl", "")
            if img_url and img_url.startswith("http"):
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (Serper/Google):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_serpapi(query: str, count: int = 4):
    """
    SerpApi — Google Images (aur Bing/Yahoo/Yandex bhi) ka JSON API,
    established/reliable multi-engine provider. Free tier: 100
    searches/month.
    Key: https://serpapi.com/manage-api-key
    Phir bolo: "Jarvis code api: serpapi TERI_KEY"
    """
    key = memory.get_secret("serpapi")
    if not key:
        return None
    try:
        data = _jget(
            "https://serpapi.com/search.json?engine=google_images"
            f"&q={urllib.parse.quote(query)}&api_key={urllib.parse.quote(key)}")
        if not data:
            return None
        lines = []
        for r in data.get("images_results", [])[:count]:
            img_url = r.get("original") or r.get("thumbnail", "")
            if img_url and img_url.startswith("http"):
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (SerpApi/Google):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_unsplash(query: str, count: int = 4):
    """
    Unsplash — free key, high-quality real photos (nature, cities,
    current topics, generic subjects ke liye badhiya). Celebrity/named-
    person photos ke liye kam kaam aayega (Unsplash bhi CC-jaisa hi
    licensed content hai), general/current-event queries ke liye achha.
    Free key: https://unsplash.com/developers ("Register as a developer")
    Phir bolo: "Jarvis code api: unsplash TERI_KEY"
    """
    key = memory.get_secret("unsplash")
    if not key:
        return None
    try:
        data = _jget(
            "https://api.unsplash.com/search/photos"
            f"?query={urllib.parse.quote(query)}&per_page={min(count, 20)}",
            headers={"Authorization": f"Client-ID {key}"})
        if not data:
            return None
        lines = []
        for r in data.get("results", [])[:count]:
            urls = r.get("urls", {}) or {}
            img_url = urls.get("regular") or urls.get("full", "")
            if img_url and img_url.startswith("http"):
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (Unsplash):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_images_flickr(query: str, count: int = 4):
    """
    Flickr API — free key, karodon public photos (news events, real-world
    photography ke liye Unsplash/Pixabay se zyada wide coverage).
    Free key: https://www.flickr.com/services/apps/create/apply/
    Phir bolo: "Jarvis code api: flickr TERI_KEY"
    """
    key = memory.get_secret("flickr")
    if not key:
        return None
    try:
        data = _jget(
            "https://www.flickr.com/services/rest/?method=flickr.photos.search"
            f"&api_key={urllib.parse.quote(key)}&text={urllib.parse.quote(query)}"
            f"&per_page={min(count, 20)}&safe_search=1&content_type=1"
            f"&sort=relevance&format=json&nojsoncallback=1")
        if not data or data.get("stat") != "ok":
            return None
        photos = data.get("photos", {}).get("photo", [])
        lines = []
        for p in photos[:count]:
            server, pid, secret = p.get("server"), p.get("id"), p.get("secret")
            if server and pid and secret:
                img_url = f"https://live.staticflickr.com/{server}/{pid}_{secret}_b.jpg"
                lines.append(f"IMAGE_FOUND:{img_url}")
        if lines:
            return f"'{query}' ki images (Flickr):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def search_images(query: str, count: int = 4):
    """
    Real internet se images dhundo aur IMAGE_FOUND format mein return karo.
    Priority (jitni keys saved hongi utne provider try honge, phir free
    no-key sources): Google Custom Search → Serper.dev → SerpApi → Brave →
    icrawler (Google/Bing) → Bing (direct scrape) → DuckDuckGo → Wikimedia
    Commons → Pixabay → Unsplash → Flickr → Openverse → Internet Archive.

    Serper/SerpApi/Brave asli Google/Bing SERP data dete hain — named-
    person/celebrity/current-event queries ke liye sabse accurate.
    (Microsoft ka apna Bing Search API Aug 2025 mein retire ho chuka hai,
    isliye Brave uska sabse behtar replacement hai.)

    BUG FIX: pehle free/no-key sources mein Pixabay/Unsplash/Flickr jaise
    NARROW curated stock-photo (tag-based) APIs sabse pehle try hote the.
    Yeh APIs khaali kabhi nahi hote — kisi bhi query par KOI-NA-KOI loosely
    matching tagged photo de hi dete hain (chahe query specific/named ho,
    jaise koi vyakti, product, ya niche cheez) — isliye chain wahin ruk
    jaati thi aur real web-crawl/search-engine wale accurate sources
    (icrawler, Bing scrape, DuckDuckGo — jo asli Google/Bing search ki
    tarah exact query match karte hain) kabhi try hi nahi hote the. Ab
    genuine web-search-jaisa behave karne wale sources (icrawler → Bing
    scrape → DuckDuckGo) pehle try hote hain — yeh sabse accurate/query-
    matching results dete hain kisi bhi tarah ki query par. Narrow
    stock-tag APIs (Pixabay/Unsplash/Flickr/Wikimedia/Openverse) sirf
    tab try hote hain jab pehle wale kuch na de paayein — yeh generic
    "nature/office/aesthetic" jaisi query ke liye theek hain, lekin
    specific/named subjects ke liye galat/irrelevant photo de sakte hain.

    Har provider ke result ke URLs dead-link-check (parallel HEAD request)
    se validate hote hain — koi bhi provider agar broken/dead links de
    (jaisa Openverse ke saath hota tha), to seedha agle provider pe
    fall-through ho jaata hai, user ko kabhi broken image icon nahi
    dikhta. Openverse ka dataset mein purane/dead links kaafi common hain,
    isliye ise jaan-boojh kar priority list mein sabse peeche rakha gaya
    hai — sirf tab try hota hai jab pehle ke saare reliable/free sources
    kuch na de paayein.

    Openverse/Wikimedia CC-licensed content tak limited hain — celebrity/
    copyrighted photos ke liye aksar khaali result denge (safe, kuch
    galat nahi), aur seedha agle source pe fall-through ho jaata hai.
    """
    for fn in (_search_images_google, _search_images_serper, _search_images_serpapi,
               _search_images_brave, _search_images_icrawler, _search_images_bing_scrape,
               _search_images_ddg, _search_images_wikimedia, _search_images_pixabay,
               _search_images_unsplash, _search_images_flickr, _search_images_openverse,
               _search_images_archive_org):
        result = fn(query, count)
        if result:
            validated = _filter_dead_images(result, count)
            if validated:
                return validated
            # Provider ne data diya tha lekin sab links dead nikle
            # (Openverse ke saath yeh common hai) — agle provider try karo
            log.info(f"search_images: {fn.__name__} ke saare links dead the, agle provider pe ja rahe hain")

    return f"'{query}' ki images nahi mili. Internet connection check karo."


# ─────────────────────────────────────────────
# VIDEO SEARCH — yt-dlp + DuckDuckGo + YouTube
# Pexels REMOVED completely
# ─────────────────────────────────────────────

def _search_videos_ytdlp(query: str, count: int = 3):
    """
    yt-dlp se YouTube videos search karo aur direct stream URL nikalo.
    In-app video player mein play hogi.
    """
    try:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "default_search": f"ytsearch{count}",
            "noplaylist": True,
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)

        if not info or "entries" not in info:
            return None

        lines = []
        for entry in (info.get("entries") or [])[:count]:
            if not entry:
                continue
            vid_id = entry.get("id","")
            title = entry.get("title", query)
            if vid_id:
                yt_url = f"https://www.youtube.com/watch?v={vid_id}"
                lines.append(f"VIDEO_FOUND:{yt_url}|{title}")

        if lines:
            return f"'{query}' ke videos (yt-dlp):\n" + "\n".join(lines)
    except ImportError:
        pass
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_videos_ddg(query: str, count: int = 3):
    """
    DuckDuckGo video search — genuinely MULTI-SITE (Vimeo, Dailymotion,
    news-site embeds, YouTube, aur bahut kuch — jo bhi DDG index karta
    hai), sirf YouTube tak limited nahi.

    BUG FIX: pehle yeh seedha MAIN process mein `from ddgs import DDGS`
    karta tha. ddgs ka native (Rust) backend Termux/Android ARM par
    process ko hi crash kar sakta hai (segfault-jaisa native crash —
    jise Python ka apna try/except catch nahi kar paata, poora silent
    failure ho jaata hai). Isi wajah se yeh source hamesha silently skip
    ho jaata tha aur sirf YouTube-only sources (yt-dlp ytsearch, YouTube
    API/scrape, Invidious) hi bachte the — isiliye video hamesha sirf
    YouTube se hi aata tha, kahin aur se nahi. Ab isolated subprocess
    (_run_isolated) mein chalta hai — crash ho bhi to sirf subprocess
    marta hai, main Jarvis process bilkul safe rehta hai.
    """
    code = (
        "import json\n"
        "try:\n"
        "    from ddgs import DDGS\n"
        f"    with DDGS() as ddgs:\n"
        f"        results = list(ddgs.videos({query!r}, max_results={count * 2}))\n"
        "    out = []\n"
        "    for r in results:\n"
        "        u = r.get('content','') or r.get('embed_url','')\n"
        "        if u and u.startswith('http'):\n"
        "            out.append({'url': u, 'title': r.get('title','')})\n"
        f"    print(json.dumps(out[:{count}]))\n"
        "except Exception:\n"
        "    print(json.dumps([]))\n"
    )
    results = _run_isolated(code, timeout=25)
    if not results:
        return None
    lines = [f"VIDEO_FOUND:{r['url']}|{r.get('title') or query}" for r in results]
    if lines:
        return f"'{query}' ke videos (DuckDuckGo):\n" + "\n".join(lines)
    return None


def _search_videos_bing_scrape(query: str, count: int = 3):
    """
    Bing Video Search — direct HTML scrape (koi library/subprocess nahi,
    seedha _http_get). Bing video results mein schema.org VideoObject
    structured-data ("contentUrl") embed hoti hai — usi se real playable
    video URLs milte hain. Genuinely MULTI-SITE hai (Dailymotion, Vimeo,
    news/publisher video embeds, YouTube, waghera) — isliye yeh bhi
    "sirf YouTube se video aata hai" wali problem ka ek aur fix hai.
    """
    try:
        url = f"https://www.bing.com/videos/search?q={urllib.parse.quote(query)}&form=VDRE"
        body, err = _http_get(url, headers={"User-Agent": _UA})
        if not body:
            return None
        html = body.decode("utf-8", errors="ignore")
        # Bing har video card ke andar schema.org VideoObject JSON rakhta
        # hai jisme "contentUrl" real (ya kam-se-kam embeddable) video
        # link hota hai, aur "name" title hota hai.
        matches = re.findall(
            r'"contentUrl":"(https?:[^"]+)"(?:[^{}]*?"name":"([^"]*)")?',
            html)
        lines = []
        seen = set()
        for content_url, title in matches:
            u = content_url.replace("\\/", "/").replace("\\u0026", "&")
            if u in seen or not u.startswith("http"):
                continue
            seen.add(u)
            t = (title or query).replace("\\u0026", "&").strip() or query
            lines.append(f"VIDEO_FOUND:{u}|{t}")
            if len(lines) >= count:
                break
        if lines:
            return f"'{query}' ke videos (Bing):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_videos_archive_org(query: str, count: int = 3):
    """Internet Archive — free, no key, stable JSON API. Documentaries,
    public-domain films/clips. Niche/historical queries ke liye achha,
    general queries ke liye yt-dlp/YouTube behtar rahenge."""
    try:
        data = _jget(
            f"https://archive.org/advancedsearch.php?q="
            f"{urllib.parse.quote(query)}+AND+mediatype:movies"
            f"&fl[]=identifier&fl[]=title&rows={count}&output=json")
        if not data:
            return None
        docs = data.get("response", {}).get("docs", [])
        lines = []
        for d in docs[:count]:
            ident = d.get("identifier", "")
            title = d.get("title", query)
            if ident:
                vid_url = f"https://archive.org/details/{ident}"
                lines.append(f"VIDEO_FOUND:{vid_url}|{title}")
        if lines:
            return f"'{query}' ke videos (Internet Archive):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _auto_route_video_lines(raw_result: str):
    """
    search_videos ke fallback source se aaye VIDEO_FOUND lines mein se
    sirf explicit .m3u8 URLs ko HLS_FOUND mein badalta hai — sirf simple
    extension check, KOI network call ya subprocess nahi.

    BUG FIX: pehle ye har line ke liye poora play_stream() call karta tha,
    jo har unrecognized URL (YouTube/mp4 na ho) ke liye ek 40-second tak ka
    yt-dlp subprocess spawn karta tha — 3 results wale search mein ye
    2 minute tak ke subprocess + heavy memory use ban jaata tha. Yehi
    Render ka memory-limit crash aur video-open-pe-error ka root cause tha.
    Ab bas ek harmless string check hai; agar user KHUD koi specific link
    de (jaise Vimeo/Instagram), tab hi play_stream() ka heavy generic
    resolver chalta hai (play_stream tool, is function se nahi).
    """
    if not raw_result:
        return raw_result
    out_lines = []
    for line in raw_result.splitlines():
        if line.startswith("VIDEO_FOUND:"):
            rest = line[len("VIDEO_FOUND:"):]
            url, _, title = rest.partition("|")
            url = url.strip()
            if url.lower().split("?")[0].endswith(".m3u8"):
                out_lines.append(f"HLS_FOUND:{url}|{title.strip()}")
            else:
                out_lines.append(line)
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def _search_videos_wikimedia(query: str, count: int = 3):
    """Wikimedia Commons — free, no key, stable JSON API. Educational/
    documentary/historical clips milte hain (webm/ogv/mp4). General
    entertainment queries ke liye YouTube behtar rahega, but ye kabhi
    block/rate-limit nahi hota kyunki scraping nahi hai."""
    try:
        data = _jget(
            f"https://commons.wikimedia.org/w/api.php"
            f"?action=query&list=search&srsearch={urllib.parse.quote(query)}"
            f"&srnamespace=6&srlimit={count * 4}&format=json")
        if not data:
            return None
        results = data.get("query", {}).get("search", [])
        file_titles = []
        for r in results:
            title = r.get("title", "")
            if title.startswith("File:"):
                ext = title.lower().rsplit(".", 1)[-1]
                if ext in ("webm", "ogv", "mp4", "mov"):
                    file_titles.append(title)
            if len(file_titles) >= count * 2:
                break
        if not file_titles:
            return None
        titles_param = "|".join(file_titles[:count * 2])
        info_data = _jget(
            f"https://commons.wikimedia.org/w/api.php"
            f"?action=query&titles={urllib.parse.quote(titles_param)}"
            f"&prop=imageinfo&iiprop=url&format=json")
        lines = []
        if info_data:
            pages = info_data.get("query", {}).get("pages", {})
            for pg in pages.values():
                ii = pg.get("imageinfo", [{}])
                vid_url = ii[0].get("url", "") if ii else ""
                title = pg.get("title", query).replace("File:", "")
                if vid_url and vid_url.startswith("http"):
                    lines.append(f"VIDEO_FOUND:{vid_url}|{title}")
                if len(lines) >= count:
                    break
        if lines:
            return f"'{query}' ke videos (Wikimedia):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def _search_videos_youtube_scrape(query: str, count: int = 3):
    """
    YouTube search results — direct HTML scrape (yt-dlp library nahi,
    seedha _http_get + regex, DDG jaisa lightweight tarika). yt-dlp ka
    fingerprint/request pattern kabhi kabhi YouTube ke bot-detection se
    zyada jaldi block hota hai; ye plain page load hai jo kabhi kabhi
    tab bhi chal jaata hai jab yt-dlp fail ho jaaye.
    """
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        body, err = _http_get(url, timeout=15)
        if err or not body:
            return None
        html = body.decode(errors="ignore")
        # ytInitialData JSON mein videoId + title pattern se nikaalo
        vid_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"', html)
        lines = []
        seen = set()
        for i, vid in enumerate(vid_ids):
            if vid in seen:
                continue
            seen.add(vid)
            title = titles[i] if i < len(titles) else query
            lines.append(f"VIDEO_FOUND:https://www.youtube.com/watch?v={vid}|{title}")
            if len(lines) >= count:
                break
        if lines:
            return f"'{query}' ke videos (YouTube-scrape):\n" + "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return None


def find_and_play(query: str, source: str = "auto") -> str:
    """
    User sirf naam/mood bataye (jaise 'Kabhi Khushi Kabhie Gham ka gaana'
    ya 'Inception trailer' ya 'IPL highlights' ya 'BBC News wala clip')
    — yeh tool KHUD us cheez ko dhoondh ke seedha play/dikha deta hai.
    User ko khud Google karke link copy-paste karne ki zarurat NAHI —
    bas naam bolo, Jarvis khud sahi jagah se link laake play karega.

    source: TUM (Jarvis/LLM) khud reasoning karke decide karo ki query
    kis type ki hai aur uske hisaab se sahi value do — isi se sateek
    (accurate) result milta hai, na ki hamesha ek hi fixed order try
    hoti hai:
      - "youtube" → gaana/song, music video, movie/show ka TRAILER,
        vlog, tutorial, review, official music video, comedy clip —
        yeh sab cheezein YouTube par sabse zyada aur best quality mein
        milti hain. search_videos (YouTube-priority multi-source) pehle
        try hota hai.
      - "web" → PURI movie/TV episode, sports match highlights/live,
        news clip, kisi specific site/brand ka content, ya koi bhi
        cheez jo YouTube par shayad na mile (niche/regional/site-
        specific) — seedha general web search se best matching webpage
        dhoondh ke uspar yt-dlp ka generic 1000+ site extractor try
        hota hai (YouTube search skip karke, taaki galat/irrelevant
        YouTube result na aaye).
      - "auto" (default, jab type clear na ho) → pehle YouTube-priority
        search try hota hai, na mile to web search fallback.

    Pipeline (Render free-tier RAM-safe, purane crash-bug se seekh ke
    bana hai — kabhi bhi 2 se zyada URLs par heavy resolver nahi
    chalata):
    1. (source="youtube"/"auto") search_videos(query) — multi-source
       hai (YouTube API, yt-dlp, Wikimedia, DDG, scrape, Invidious,
       Archive.org). Top result seedha play hota hai, baaki 2 bhi
       carousel mein swipe karke dekh sakte ho.
    2. (source="web"/"auto" agar step 1 khaali ho) general web search se
       top 2 candidate links liye jaate hain aur unpar yt-dlp ka generic
       extractor try hota hai (max 2 attempts).
    """
    query = (query or "").strip()
    if not query:
        return "❌ Kya dhoondh ke play karoon, woh naam/topic batao."
    source = (source or "auto").strip().lower()
    if source not in ("auto", "youtube", "web"):
        source = "auto"

    def _try_youtube_priority():
        result = search_videos(query, count=3)
        if result and "nahi mile" not in result and ("VIDEO_FOUND:" in result or "HLS_FOUND:" in result):
            return f"🔎 '{query}' dhoondh ke play kar raha hoon (aur options bhi neeche hain, swipe karo):\n{result}"
        return None

    def _try_web_priority():
        hits = _ddg_text_search(query, max_results=5)
        tried = 0
        for h in hits:
            if tried >= 2:
                break
            candidate = (h.get("href") or "").strip()
            if not candidate.startswith("http"):
                continue
            tried += 1
            played = play_stream(candidate)
            if not played.startswith("❌"):
                return f"🔎 '{query}' ke liye dhoond ke play kar raha hoon:\n{played}"
        return None

    if source == "youtube":
        r = _try_youtube_priority()
        if r:
            return r
        r = _try_web_priority()
        if r:
            return r
    elif source == "web":
        r = _try_web_priority()
        if r:
            return r
        r = _try_youtube_priority()
        if r:
            return r
    else:  # auto
        r = _try_youtube_priority()
        if r:
            return r
        r = _try_web_priority()
        if r:
            return r

    return (f"❌ '{query}' ke liye koi playable video nahi mil paaya. "
            f"Thoda alag naam/spelling try karo, ya agar tumhare paas "
            f"khud koi specific link hai to woh do — main use khol dunga.")


def search_videos(query: str, count: int = 3):
    """
    Real videos dhundo — MULTI-SITE sources (DuckDuckGo, Bing) pehle,
    phir YouTube (yt-dlp/API/scrape/Invidious), phir niche fallbacks
    (Wikimedia, Internet Archive). Pexels REMOVED. Videos Invidious embed
    se in-app play honge. Koi bhi source se mila URL agar actually .m3u8
    (HLS stream) nikle to automatically HLS player se play hoga
    (auto-detect).

    BUG FIX (July 2026): pehle is chain mein YouTube-LOCKED sources
    (yt-dlp ka `ytsearch:` prefix, YouTube Data API, YouTube HTML scrape,
    Invidious) sabse pehle try hote the — aur "DuckDuckGo" step bhi
    seedha main process mein `ddgs` import karta tha jo Termux/Android
    ARM par crash ho sakta tha (silently fail). Nateeja: genuinely
    multi-site source (DuckDuckGo) kabhi reach hi nahi hota tha, aur
    "yt-dlp priority" naam ke bawajood woh khud sirf YouTube search
    karta tha (`ytsearch{count}:` yt-dlp ka built-in YOUTUBE-ONLY search
    prefix hai — koi "sabhi sites search karo" mode yt-dlp mein exist
    nahi karta). Isi wajah se "koi bhi site se video" hamesha sirf
    YouTube se hi aata tha. Ab crash-safe DuckDuckGo aur naya Bing video
    scrape (dono genuinely multi-site — Vimeo/Dailymotion/news-embeds/
    waghera) sabse pehle try hote hain; YouTube-only sources ab reliable
    fallback ki tarah baad mein aate hain (YouTube abhi bhi milega jab
    relevant ho ya doosre sources kuch na den, bas ab AKELA source nahi
    hai).
    """
    # 1. DuckDuckGo — genuinely multi-site, crash-safe (isolated subprocess)
    result = _search_videos_ddg(query, count)
    if result:
        return _auto_route_video_lines(result)

    # 2. Bing video scrape — genuinely multi-site
    result = _search_videos_bing_scrape(query, count)
    if result:
        return _auto_route_video_lines(result)

    # 3. Wikimedia Commons — free, no key, stable JSON API (non-YouTube,
    #    par niche hai — bahut queries ke liye khaali rahega)
    result = _search_videos_wikimedia(query, count)
    if result:
        return _auto_route_video_lines(result)

    # 4. YouTube Data API — agar user ne apni key di hai, sabse reliable
    #    single-site search (opt-in, user ne khud key setup ki hai)
    result = _search_videos_google(query, count)
    if result:
        return _auto_route_video_lines(result)

    # 5. yt-dlp — YouTube-only search (`ytsearch:` yt-dlp ka built-in
    #    prefix hai), lekin no-key/reliable hai — achha fallback
    result = _search_videos_ytdlp(query, count)
    if result:
        return _auto_route_video_lines(result)

    # 6. YouTube direct scrape — yt-dlp se ALAG failure-mode (plain HTTP GET)
    result = _search_videos_youtube_scrape(query, count)
    if result:
        return _auto_route_video_lines(result)

    # 7. YouTube / Invidious fallback
    result = search_youtube(query, count)
    if result and "nahi mile" not in result:
        return _auto_route_video_lines(result)

    # 8. Internet Archive — last resort, no key
    #    (archive.org/details/ URLs webpage hain, direct media nahi —
    #    isliye HLS auto-detect se skip, seedha VIDEO_FOUND jaata hai)
    result = _search_videos_archive_org(query, count)
    if result:
        return result

    return f"'{query}' ke videos nahi mile."


def search_youtube(query: str, count: int = 4):
    yt_key = memory.get_secret("youtube")

    if yt_key:
        try:
            data = _jget(
                f"https://www.googleapis.com/youtube/v3/search"
                f"?part=snippet&q={urllib.parse.quote(query)}"
                f"&type=video&maxResults={count}&key={yt_key}")
            items = (data or {}).get("items", [])
            if items:
                lines = []
                for item in items:
                    vid = item["id"]["videoId"]
                    title = item["snippet"]["title"]
                    ch = item["snippet"]["channelTitle"]
                    lines.append(f"VIDEO_FOUND:https://www.youtube.com/watch?v={vid}|{title} — {ch}")
                return f"'{query}' YouTube:\n" + "\n".join(lines)
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    for instance in ["https://inv.tux.pizza","https://invidious.privacydev.net","https://yt.drgnz.club"]:
        try:
            data = _jget(f"{instance}/api/v1/search?q={urllib.parse.quote(query)}&type=video")
            if data:
                lines = []
                for item in data[:count]:
                    vid = item.get("videoId","")
                    title = item.get("title","")
                    author = item.get("author","")
                    if vid:
                        lines.append(f"VIDEO_FOUND:https://www.youtube.com/watch?v={vid}|{title} — {author}")
                if lines:
                    return f"'{query}' YouTube:\n" + "\n".join(lines)
        except Exception:
            continue

    return f"'{query}' ke videos nahi mile. 'pip install yt-dlp' install karo."


# ─────────────────────────────────────────────
# REST Countries API — no key needed
# ─────────────────────────────────────────────

def get_country_info(country: str):
    country = (country or "").strip()
    if not country:
        return "❌ Desh ka naam batao (jaise: India, Japan, USA)."
    try:
        data = _jget(f"https://restcountries.com/v3.1/name/{urllib.parse.quote(country)}?fullText=false")
        if not data or not isinstance(data, list) or len(data) == 0:
            return f"❌ '{country}' naam ka koi desh nahi mila. Sahi spelling (English mein) try karo."
        c = data[0]
        name = c.get("name",{}).get("common", country)
        capital = c.get("capital",["?"])[0] if c.get("capital") else "?"
        pop = c.get("population",0)
        area = c.get("area",0)
        region = c.get("region","?")
        subregion = c.get("subregion","?")
        langs = ", ".join(c.get("languages",{}).values()) if c.get("languages") else "?"
        currencies = ", ".join(
            f"{v.get('name',k)} ({v.get('symbol','')})"
            for k,v in c.get("currencies",{}).items()) or "?"
        timezones = ", ".join(c.get("timezones",[])[:2])
        flag = c.get("flag","")
        return (f"{flag} {name}\n"
                f"🏛️ Capital: {capital}\n"
                f"👥 Population: {pop:,}\n"
                f"📐 Area: {area:,} km²\n"
                f"🌍 Region: {region} → {subregion}\n"
                f"🗣️ Languages: {langs}\n"
                f"💰 Currency: {currencies}\n"
                f"🕐 Timezones: {timezones}")
    except Exception as e:
        return f"❌ Country info error: {e or 'connection problem'} — internet check karo."


# ─────────────────────────────────────────────
# IP-API — no key needed
# ─────────────────────────────────────────────

def get_ip_info(ip: str = ""):
    try:
        target = ip.strip() if ip.strip() else ""
        url = f"http://ip-api.com/json/{target}?fields=status,message,country,regionName,city,zip,lat,lon,timezone,isp,org,query"
        data = _jget(url)
        if not data or data.get("status") != "success":
            return f"IP info nahi mili: {(data or {}).get('message','unknown error')}"
        return (f"🌐 IP: {data.get('query','?')}\n"
                f"📍 Location: {data.get('city','?')}, {data.get('regionName','?')}, {data.get('country','?')}\n"
                f"🕐 Timezone: {data.get('timezone','?')}\n"
                f"📡 ISP: {data.get('isp','?')}\n"
                f"🏢 Org: {data.get('org','?')}\n"
                f"📮 ZIP: {data.get('zip','?')}\n"
                f"🗺️ Maps: https://maps.google.com/?q={data.get('lat','')},{data.get('lon','')}")
    except Exception as e:
        return f"IP-API error: {e}"


# ─────────────────────────────────────────────
# SpaceX API — no key needed
# ─────────────────────────────────────────────

def get_spacex_launches(upcoming: bool = False):
    """SpaceX launches — latest ya upcoming. Multi-API fallback."""
    apis = [
        ("https://api.spacexdata.com/v5/launches/upcoming" if upcoming else "https://api.spacexdata.com/v5/launches/latest"),
        ("https://lldev.thespacedevs.com/2.2.0/launch/upcoming/?limit=3&agency_ids=121" if upcoming
         else "https://lldev.thespacedevs.com/2.2.0/launch/previous/?limit=3&agency_ids=121"),
    ]
    # SpaceX API v5 try karo
    for api_url in apis[:1]:
        try:
            data = _jget(api_url, timeout=20)
            if not data:
                continue
            if isinstance(data, dict) and "results" not in data:
                data = [data]
            elif isinstance(data, dict) and "results" in data:
                data = data["results"]

            title = "🚀 SpaceX aane wale launches:" if upcoming else "🚀 SpaceX recent launches:"
            lines = [title]
            for launch in (data[:4] if isinstance(data, list) else [data]):
                name = launch.get("name","?")
                date_raw = launch.get("date_utc","") or launch.get("net","") or launch.get("window_start","")
                date = date_raw[:10] if date_raw else "TBD"
                success = launch.get("success")
                details = (launch.get("details","") or "").strip()
                webcast = launch.get("links",{}).get("webcast","") if isinstance(launch.get("links"),dict) else ""
                if success is True:
                    status = "✅ Safal"
                elif success is False:
                    status = "❌ Asafal"
                else:
                    status = "⏳ Upcoming"
                lines.append(f"\n🛸 {name}")
                lines.append(f"   📅 Date: {date}  |  {status}")
                if details:
                    lines.append(f"   📝 {details[:150]}")
                if webcast:
                    lines.append(f"   📺 Webcast: {webcast}")
            return "\n".join(lines)
        except Exception:
            continue

    # Launch Library 2 fallback (SpaceX launches)
    try:
        if upcoming:
            ll2 = _jget("https://lldev.thespacedevs.com/2.2.0/launch/upcoming/?limit=3&search=SpaceX", timeout=20)
        else:
            ll2 = _jget("https://lldev.thespacedevs.com/2.2.0/launch/previous/?limit=3&search=SpaceX", timeout=20)
        if ll2:
            results = ll2.get("results", [])
            lines = ["🚀 SpaceX " + ("upcoming" if upcoming else "recent") + " launches:"]
            for r in results[:3]:
                name = r.get("name","?")
                net = (r.get("net","") or "")[:10]
                status_name = r.get("status",{}).get("name","?") if isinstance(r.get("status"),dict) else "?"
                lines.append(f"\n🛸 {name}\n   📅 {net}  |  {status_name}")
            return "\n".join(lines)
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")

    return "SpaceX data abhi nahi mila. Thodi der mein try karo ya internet check karo."


# ─────────────────────────────────────────────
# Sunrise-Sunset API — no key needed
# ─────────────────────────────────────────────

def get_sunrise_sunset(city: str):
    try:
        geo = _jget(
            f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(city)}&format=json&limit=1",
            headers={"User-Agent":"JarvisApp/5.0"})
        if not geo:
            return f"'{city}' nahi mila."
        lat = geo[0]["lat"]
        lon = geo[0]["lon"]
        data = _jget(f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&formatted=0")
        if not data or data.get("status") != "OK":
            return "Sunrise-sunset data nahi mila."
        results = data["results"]

        def fmt_time(iso):
            try:
                from datetime import datetime, timezone, timedelta
                dt = datetime.fromisoformat(iso.replace("Z","+00:00"))
                ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
                return ist.strftime("%I:%M %p IST")
            except Exception:
                return iso

        sunrise = fmt_time(results.get("sunrise",""))
        sunset = fmt_time(results.get("sunset",""))
        solar_noon = fmt_time(results.get("solar_noon",""))
        day_len = results.get("day_length",0)
        hours = int(day_len) // 3600
        mins = (int(day_len) % 3600) // 60
        return (f"🌅 {city} mein aaj:\n"
                f"☀️ Sunrise: {sunrise}\n"
                f"🌇 Sunset: {sunset}\n"
                f"☀️ Solar Noon: {solar_noon}\n"
                f"⏱️ Din ki lambai: {hours}h {mins}m")
    except Exception as e:
        return f"Sunrise-Sunset error: {e}"


# ─────────────────────────────────────────────
# Nager.Date — Public Holidays
# ─────────────────────────────────────────────

def get_public_holidays(country_code: str = "IN", year: int = None):
    try:
        year = year or datetime.date.today().year
        data = _jget(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code.upper()}")
        if not data:
            return f"'{country_code}' ke holidays nahi mile."
        lines = [f"🎉 {country_code.upper()} {year} ki public holidays:"]
        today = datetime.date.today()
        for h in data:
            date_str = h.get("date","")
            name = h.get("name","")
            local = h.get("localName","")
            try:
                hdate = datetime.date.fromisoformat(date_str)
                past = "✅" if hdate < today else "📅"
            except Exception:
                past = "📅"
            display = local if local and local != name else name
            lines.append(f"{past} {date_str}: {display}")
        return "\n".join(lines)
    except Exception as e:
        return f"Holiday error: {e}"


# ─────────────────────────────────────────────
# Radio Browser API — In-App Audio Player
# RADIO_STREAM format — app ke andar play hoga
# ─────────────────────────────────────────────

def search_radio(query: str = "", country: str = "IN", limit: int = 5):
    """
    Internet radio stations dhundo — Radio Browser API.
    RADIO_STREAM:URL|Name format return karta hai.
    App ke andar seedha audio player mein play hoga.
    """
    SERVERS = [
        "https://de1.api.radio-browser.info",
        "https://nl1.api.radio-browser.info",
        "https://at1.api.radio-browser.info",
    ]
    def _radio_get(server, extra_params):
        base = f"{server}/json/stations/search?{extra_params}&hidebroken=true&order=votes&limit={limit*2}"
        return _jget(base, headers={"User-Agent": "JarvisApp/5.0"}, timeout=12)

    data = None
    # 1. Name search with HTTPS only
    for srv in SERVERS:
        try:
            p = f"is_https=true"
            if query:
                p += f"&name={urllib.parse.quote(query)}"
            if country:
                p += f"&countrycode={country.upper()}"
            data = _radio_get(srv, p)
            if data:
                break
        except Exception:
            continue

    # 2. Without HTTPS restriction (HTTP streams bhi)
    if not data:
        for srv in SERVERS:
            try:
                p = ""
                if query:
                    p = f"name={urllib.parse.quote(query)}"
                if country and not query:
                    p = f"countrycode={country.upper()}"
                data = _radio_get(srv, p or "order=votes")
                if data:
                    break
            except Exception:
                continue

    # 3. Tag-based search
    if not data and query:
        for srv in SERVERS[:2]:
            try:
                url = f"{srv}/json/stations/bytag/{urllib.parse.quote(query)}?limit={limit*2}&hidebroken=true&order=votes"
                data = _jget(url, headers={"User-Agent": "JarvisApp/5.0"}, timeout=12)
                if data:
                    break
            except Exception:
                continue

    if not data:
        return f"Radio stations nahi mili '{query or country}'. Internet check karo."

    lines_out = [f"📻 Radio stations ({query or country or 'popular'}):"]
    added = 0
    for s in data:
        name = (s.get("name") or "?").strip()
        stream_url = s.get("url_resolved") or s.get("url") or ""
        if not stream_url or not stream_url.startswith("http"):
            continue
        country_name = s.get("country", "")
        bitrate = s.get("bitrate", 0)
        codec = s.get("codec", "MP3")
        display = name
        if country_name:
            display += f" — {country_name}"
        if bitrate:
            display += f" ({bitrate}kbps {codec})"
        lines_out.append(f"RADIO_STREAM:{stream_url}|{display}")
        added += 1
        if added >= limit:
            break

    if added == 0:
        return f"'{query}' se koi working radio station nahi mila."
    return "\n".join(lines_out)

def scrape_webpage(url: str, extract: str = "text"):
    try:
        body, err, used_stealth = _http_get_stealthy(url, headers={"Accept":"text/html"}, timeout=20, max_bytes=3_000_000)
        if err == "BOT_BLOCKED" or (body and looks_bot_blocked(body.decode("utf-8", errors="ignore"))):
            return (f"🚫 '{url}' anti-bot protection use kar rahi hai (jaise Cloudflare) — "
                    f"stealth mode try karne ke baad bhi access nahi mila. Yeh site basic "
                    f"tareeke se open nahi hogi.")
        if err or not body:
            return f"Webpage nahi mila: {err}"
        html = body.decode("utf-8", errors="ignore")

        if extract == "title":
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            return f"Title: {m.group(1).strip()}" if m else "Title nahi mila."

        if extract == "links":
            links = re.findall(r'href=["\'"]([^"\']+)["\']', html)
            links = [l for l in links if l.startswith("http")][:15]
            return "Links:\n" + "\n".join(f"• {l}" for l in links)

        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        stealth_note = " (stealth mode se khola)" if used_stealth else ""
        return f"📄 {url} ka content{stealth_note}:\n\n{text[:1500]}{'...' if len(text)>1500 else ''}"
    except Exception as e:
        return f"Scraping error: {e}"


# ─────────────────────────────────────────────
# GET PAGE MEDIA — user koi bhi website/Google link chat mein bheje,
# Jarvis khud us page ko khol ke andar ki saari images/videos nikaal
# ke chat mein dikha/play kar de. search_images/search_videos se jab
# kuch na mile ya user ke paas pehle se ek specific site/link ho, tab
# yeh use hota hai.
# ─────────────────────────────────────────────

_SKIP_IMG_HINTS = ("sprite", "1x1", "pixel.", "blank.gif", "spacer.",
                   "logo", "favicon", "avatar", "icon-", "-icon", "/icons/",
                   "placeholder", "loading.", "lazy-load", "data:image")
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
_VID_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".m3u8")


def _abs_url(base: str, link: str) -> str:
    try:
        return urllib.parse.urljoin(base, link)
    except Exception:
        return link


def _best_from_srcset(srcset: str) -> str:
    """
    srcset="img-320.jpg 320w, img-800.jpg 800w, img-1600.jpg 1600w" jaisi
    string se sabse BADI (highest resolution) candidate URL nikaalta hai —
    responsive images mein aksar yeh original/best-quality image hoti hai.
    """
    best_url, best_width = None, -1
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        u = pieces[0]
        w = 0
        if len(pieces) > 1 and pieces[1].endswith("w"):
            try:
                w = int(pieces[1][:-1])
            except ValueError:
                w = 0
        elif len(pieces) > 1 and pieces[1].endswith("x"):
            try:
                w = int(float(pieces[1][:-1]) * 1000)  # density descriptor, rough scale
            except ValueError:
                w = 0
        if w >= best_width:
            best_width, best_url = w, u
    return best_url


def _extract_page_media(url: str, html: str):
    """
    HTML se images + videos ke absolute URLs nikaalta hai (regex-based,
    bs4 ki zarurat nahi). Video extraction get_page_media ke andar
    yt-dlp ke powerful generic-site extractor se hoti hai — images ke
    liye yahan bhi utni hi thoroughness rakhi gayi hai: sirf plain
    <img src> nahi, balki modern sites ke saare common patterns cover
    karte hain (lazy-load, responsive srcset, CSS backgrounds,
    structured data) — taaki JS-heavy/lazy-loading wali sites se bhi
    images miss na hon.
    """
    images, videos = [], []

    # og:image / og:video / twitter:image meta tags — sabse reliable "main" media
    for m in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
        images.append(_abs_url(url, m.group(1)))
    for m in re.finditer(r'<meta[^>]+property=["\']og:video[:url]*["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
        videos.append(_abs_url(url, m.group(1)))
    for m in re.finditer(r'<meta[^>]+name=["\']twitter:image(?:\:src)?["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
        images.append(_abs_url(url, m.group(1)))

    # <img src="..."> — normal case
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I):
        images.append(_abs_url(url, m.group(1)))

    # LAZY-LOAD attributes — modern sites (Pinterest/e-commerce/news style
    # infinite-scroll galleries) rakhte hain asli image data-src/data-
    # lazy-src/data-original mein, aur src mein sirf ek chhota placeholder/
    # blank pixel — isliye yeh alag se pakadna zaroori hai.
    for attr in ("data-src", "data-lazy-src", "data-original", "data-lazy", "data-hi-res-src"):
        for m in re.finditer(rf'<img[^>]+{attr}=["\']([^"\']+)["\']', html, re.I):
            images.append(_abs_url(url, m.group(1)))

    # RESPONSIVE IMAGES — <img srcset="..."> aur <picture><source srcset="...">
    # mein aksar sabse HIGH-QUALITY version hoti hai (jaise 2x/3x retina).
    for m in re.finditer(r'<(?:img|source)[^>]+srcset=["\']([^"\']+)["\']', html, re.I):
        best = _best_from_srcset(m.group(1))
        if best:
            images.append(_abs_url(url, best))

    # CSS INLINE background-image — hero banners/gallery cards jo <img> tag
    # use nahi karte, div ka background-image style attribute use karte hain.
    for m in re.finditer(r'background-image\s*:\s*url\((["\']?)([^)"\']+)\1\)', html, re.I):
        images.append(_abs_url(url, m.group(2)))

    # JSON-LD structured data — e-commerce/article/recipe sites aksar
    # <script type="application/ld+json"> ke andar "image":"..." ya
    # "image":["...","..."] field mein high-quality image URLs dete hain.
    for m in re.finditer(r'"image"\s*:\s*"([^"]+)"', html):
        images.append(_abs_url(url, m.group(1)))
    for m in re.finditer(r'"image"\s*:\s*\[\s*"([^"]+)"', html):
        images.append(_abs_url(url, m.group(1)))

    # <video src="..."> aur nested <source src="...">
    for m in re.finditer(r'<(?:video|source)[^>]+src=["\']([^"\']+)["\']', html, re.I):
        videos.append(_abs_url(url, m.group(1)))

    # YouTube/Vimeo <iframe> embeds
    for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
        src = m.group(1)
        if "youtube.com/embed/" in src:
            vid = src.split("/embed/")[-1].split("?")[0]
            videos.append(f"https://www.youtube.com/watch?v={vid}")
        elif "player.vimeo.com/video/" in src:
            videos.append(_abs_url(url, src))

    # Bare <a href="...jpg/.mp4/...m3u8"> links (galleries, download links)
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = _abs_url(url, m.group(1))
        low = href.lower().split("?")[0]
        if low.endswith(_IMG_EXTS):
            images.append(href)
        elif low.endswith(_VID_EXTS):
            videos.append(href)

    def _clean(urls):
        seen, out = set(), []
        for u in urls:
            if not u.lower().startswith(("http://", "https://")):
                continue
            if any(h in u.lower() for h in _SKIP_IMG_HINTS):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out

    return _clean(images), _clean(videos)


def get_page_media(url: str, media_type: str = "all", limit: int = 6) -> str:
    """
    Diya gaya website/Google link ko Jarvis KHUD KHOLTA hai aur uske andar
    se saari images aur videos dhundh ke seedha chat mein dikha/play deta
    hai (IMAGE_FOUND / VIDEO_FOUND / HLS_FOUND markers se).

    JAB USE KARO: user ne koi http(s) link chat mein bheja ho (Google se
    copy kiya hua ya kahin se bhi) aur wahan se image/video chahiye ho —
    khaaskar jab search_images/search_videos se kuch na mila ho, ya user
    ke paas pehle se ek specific page/site ho jiska content dekhna hai.

    VIDEO EXTRACTION: sirf HTML regex se video dhundhna kaafi nahi hota —
    zyadatar video-sites (streaming/embed players) JavaScript se video
    load karti hain, jo plain HTML mein nahi dikhta. Isliye yeh tool har
    link par pehle yt-dlp ka GENERIC extractor try karta hai (1000+ sites
    support karta hai — sirf YouTube/Vimeo nahi, kai random video/embed
    sites bhi), jo asli playable stream URL nikaal leta hai chahe woh
    JS-rendered player ke peeche kyun na ho. Yeh fail ho to hi simple
    regex-scrape (img/video tags, og:image) par fallback karta hai.

    IMAGE EXTRACTION: bhi utni hi thorough hai jitni video ki — sirf
    plain <img src> nahi, balki: lazy-load attributes (data-src, data-
    lazy-src, waghera — jo modern infinite-scroll/gallery sites use
    karti hain), responsive srcset (highest-resolution candidate select
    hoti hai), CSS background-image, aur JSON-LD structured data (jo
    e-commerce/article sites mein high-quality images rakhte hain) —
    sab cover hote hain. Isliye JS-heavy ya lazy-loading wali sites se
    bhi images achhi tarah mil jaati hain.

    media_type: "all" (default), "image", ya "video" — sirf ek type
    chahiye to filter karo.
    limit: max kitne items dikhane hain (default 6).

    Agar page khud hi ek direct image/video file ho (jaise seedha .jpg ya
    .mp4 link), to use turant seedha dikha/play kar deta hai.
    """
    url = (url or "").strip().strip("<>\"'")
    if not url.lower().startswith(("http://", "https://")):
        return "❌ Ek valid http(s) website/link do."

    low_path = urllib.parse.urlparse(url).path.lower().split("?")[0]

    # Page khud hi direct media file hai
    if low_path.endswith(_IMG_EXTS):
        return f"🖼️ Link se seedha image mil gaya.\nIMAGE_FOUND:{url}"
    if low_path.endswith(_VID_EXTS):
        return play_stream(url)

    lines = []
    video_marker = None

    # STEP 1 — video ke liye pehle yt-dlp ka generic extractor try karo
    # (KOI BHI link par, sirf jaani-manaani sites par nahi). Yeh JS-heavy
    # streaming/embed sites ke liye zaroori hai jaha plain HTML mein
    # video tag hota hi nahi.
    if media_type in ("all", "video"):
        result = play_stream(url)
        if not result.startswith("❌"):
            # play_stream apna poora marker-wala message deta hai
            # (VIDEO_FOUND ya HLS_FOUND), use as-is use karo
            video_marker = result

    # STEP 2 — page ka HTML bhi scrape karo, taaki (a) images mil sakein,
    # aur (b) agar yt-dlp video na nikal paaya to regex-fallback video mil
    # sake. Pehle normal fetch, block ho to cloudscraper (stealth) try
    # karta hai — taaki basic anti-bot (Cloudflare) wali sites bhi khul
    # sakein.
    body, err, used_stealth = _http_get_stealthy(url, headers={"Accept": "text/html"}, timeout=20, max_bytes=3_000_000)
    page_blocked = (err == "BOT_BLOCKED") or (body and looks_bot_blocked(body.decode("utf-8", errors="ignore")))
    images, videos = ([], [])
    if body and not err and not page_blocked:
        html = body.decode("utf-8", errors="ignore")
        images, videos = _extract_page_media(url, html)

    if media_type in ("all", "image") and images:
        for img in images[:limit]:
            lines.append(f"IMAGE_FOUND:{img}")

    if media_type in ("all", "video"):
        if video_marker:
            lines.append(video_marker)
        elif videos:
            # yt-dlp kuch nahi nikal paaya — regex-scraped video links try karo
            for i, vid in enumerate(videos[:limit]):
                vlow = vid.lower().split("?")[0]
                if vlow.endswith(".m3u8"):
                    try:
                        lines.append(_play_as_hls(vid, f"{url} — video {i+1}").split("\n")[-1])
                    except Exception:
                        lines.append(f"VIDEO_FOUND:{vid}|Video {i+1}")
                else:
                    lines.append(f"VIDEO_FOUND:{vid}|Video {i+1}")

    if not lines:
        if page_blocked:
            return (f"🚫 '{url}' anti-bot protection (jaise Cloudflare) use kar "
                    f"rahi hai — stealth mode try karne ke baad bhi is site se "
                    f"kuch nahi mila. Doosra link try karo ya search_images/"
                    f"search_videos/find_and_play use karo.")
        if err and not body:
            return f"❌ Yeh page khul nahi paya: {err}"
        return (f"❌ '{url}' par koi image/video nahi mila — na yt-dlp ke "
                f"1000+ supported sites mein se koi match hua, na hi page ke "
                f"HTML mein direct media mila. Doosra link try karo ya "
                f"search_images/search_videos use karo.")

    stealth_note = " (stealth mode se khola)" if used_stealth else ""
    header = f"🔗 {url} khol ke{stealth_note} ye mila:\n"
    return header + "\n".join(lines)


# ─────────────────────────────────────────────
# SAVED FAVOURITE WEBSITES — "achhi" website links ko naam ke saath
# yaad rakhna, taaki baad mein bina URL bole naam se hi Jarvis khud
# us site ko dobara khol ke fresh image/video la sake.
# ─────────────────────────────────────────────

def save_site(name: str, url: str) -> str:
    """
    Ek achhi website/link ko naam ke saath permanently yaad rakhta hai —
    taaki dobara poora URL na dena pade, sirf naam bol ke us site se
    image/video mangwa sako (play_saved_site).
    """
    name = (name or "").strip()
    url = (url or "").strip().strip("<>\"'")
    if not name:
        return "❌ Site ka koi naam do."
    if not url.lower().startswith(("http://", "https://")):
        return "❌ Save karne ke liye ek valid http(s) URL do."
    memory.save_named_site(name, url)
    return f"✅ '{name}' save kar diya. Ab bas '{name} se image/video lao' bolo."


def play_saved_site(name: str, media_type: str = "all", limit: int = 6) -> str:
    """Pehle 'save_site' se naam ke saath save ki gayi website khol ke fresh image/video laata hai."""
    name = (name or "").strip()
    url = memory.get_named_site(name)
    if not url:
        return f"❌ '{name}' naam se koi site saved nahi mili. Pehle 'save_site' se save karo."
    return get_page_media(url, media_type=media_type, limit=limit)


def list_saved_sites() -> str:
    """Saare naam-se-saved website links ki list dikhata hai."""
    sites = memory.list_named_sites()
    if not sites:
        return "Abhi koi site saved nahi hai."
    lines = [f"• {name} → {url}" for name, url in sites.items()]
    return "🔗 Saved sites:\n" + "\n".join(lines)


def delete_saved_site(name: str) -> str:
    """Ek saved site ko naam se hata deta hai."""
    ok = memory.delete_named_site(name)
    return f"🗑️ '{name}' hata diya." if ok else f"❌ '{name}' naam se koi saved site nahi mili."


# ─────────────────────────────────────────────
# PAGE WATCHES — Jarvis ko PROACTIVE banane wala feature. Ab tak Jarvis
# sirf reactive tha (jab poocho tab jawab). Isse tum bol sakte ho "jab
# yeh site update ho / yahan yeh cheez aaye to batana", aur Jarvis
# background mein (scheduler.py ke through, har ~25 min) khud check
# karta rehta hai — jab condition milti hai, phone par ek real Android
# notification bhej deta hai (koi chat message ki zarurat nahi).
# ─────────────────────────────────────────────

MAX_ACTIVE_WATCHES = 10


def _clean_text_for_diff(body: bytes) -> str:
    """HTML ko plain text mein todta hai — content-change compare karne ke liye."""
    html = body.decode("utf-8", errors="ignore")
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def watch_page(url: str, keyword: str = None, name: str = None) -> str:
    """
    Ek webpage ko background mein "watch" karna shuru karta hai. User
    kahe "jab is site pe [X] aaye to bata dena" ya "jab yeh page update
    ho to notify karna" — tab isko call karo.

    keyword: agar diya, to jab woh keyword page par PEHLI baar dikhega
    tab trigger hoga (jaise "Sold Out" hat gaya, "in stock" aa gaya,
    naam ki announcement aa gayi, wagera).
    keyword na diya jaaye to page ka koi bhi CONTENT-CHANGE trigger
    karega (general "kuch bhi badla" watch).

    Trigger hone par phone par ek real Android notification jaati hai
    (send_notification ke through) — koi chat check karne ki zarurat
    nahi. Watch ONE-SHOT hai — trigger hote hi khud hat jaata hai (spam
    nahi karta baar baar).

    Max 10 active watches ek time par (Render free-tier resource-safe
    rakhne ke liye). Background check ~25 min interval par hota hai —
    isliye turant real-time nahi, thoda delay ho sakta hai.
    """
    url = (url or "").strip().strip("<>\"'")
    if not url.lower().startswith(("http://", "https://")):
        return "❌ Ek valid http(s) website/link do."

    existing = memory.list_watches()
    name = (name or "").strip() or (urllib.parse.urlparse(url).netloc or f"watch{len(existing)+1}")
    if name.strip().lower() not in existing and len(existing) >= MAX_ACTIVE_WATCHES:
        return (f"❌ Already {MAX_ACTIVE_WATCHES} active watches hain (max limit). "
                f"Pehle list_page_watches() se dekho aur koi purani stop_watch() se hatao.")

    # Baseline snapshot le lo — taaki turant hi "change ho gaya" false-trigger na ho
    body, err, _ = _http_get_stealthy(url, timeout=15, max_bytes=1_500_000)
    baseline_hash = None
    if body:
        try:
            baseline_hash = hashlib.sha256(_clean_text_for_diff(body).encode("utf-8")).hexdigest()
        except Exception:
            baseline_hash = None

    memory.save_watch(name, url, keyword, baseline_hash)

    if keyword:
        return (f"👀 '{name}' watch shuru kar diya — jab '{url}' par "
                f"'{keyword}' dikhega, tumhare phone par notification "
                f"bhej dunga (~har 25 minute check hota hai).")
    return (f"👀 '{name}' watch shuru kar diya — '{url}' par koi bhi "
            f"badlav hote hi tumhare phone par notification bhej dunga "
            f"(~har 25 minute check hota hai).")


def list_page_watches() -> str:
    """Saare active page-watches ki list dikhata hai."""
    watches = memory.list_watches()
    if not watches:
        return "Abhi koi active watch nahi hai."
    lines = []
    for name, w in watches.items():
        kw = f" (keyword: '{w.get('keyword')}')" if w.get("keyword") else " (content-change)"
        lines.append(f"• {name} → {w.get('url')}{kw}")
    return "👀 Active watches:\n" + "\n".join(lines)


def stop_watch(name: str) -> str:
    """Ek active watch ko naam se cancel/hata deta hai."""
    ok = memory.delete_watch(name)
    return f"🛑 '{name}' watch band kar diya." if ok else f"❌ '{name}' naam se koi active watch nahi mili."


def _run_watch_checks():
    """
    scheduler.py se periodically call hota hai (server.py request-thread
    se NAHI — isliye slow ho to bhi user ko wait nahi karna padta).
    Har active watch check karta hai, trigger hone par phone-notification
    bhejta hai aur (one-shot hone ki wajah se) us watch ko hata deta hai.
    Kisi ek watch ka error poore batch ko nahi rokta.
    """
    watches = memory.list_watches()
    if not watches:
        return
    for name, w in list(watches.items()):
        try:
            url = w.get("url")
            keyword = w.get("keyword")
            if not url:
                continue
            body, err, _ = _http_get_stealthy(url, timeout=15, max_bytes=1_500_000)
            if not body:
                continue
            text = _clean_text_for_diff(body)

            if keyword:
                if keyword.lower() in text.lower():
                    send_notification(f"👀 Jarvis Watch: {name}",
                                       f"'{keyword}' mil gaya — {url}")
                    memory.delete_watch(name)
            else:
                new_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                old_hash = w.get("last_hash")
                if old_hash and new_hash != old_hash:
                    send_notification(f"👀 Jarvis Watch: {name}",
                                       f"Content badal gaya — {url}")
                    memory.delete_watch(name)
                else:
                    memory.update_watch_hash(name, new_hash)
        except Exception:
            continue


# ─────────────────────────────────────────────
# WolframAlpha
# ─────────────────────────────────────────────

def ask_wolfram(question: str):
    api_key = memory.get_secret("wolfram")
    if not api_key:
        return "WolframAlpha key nahi hai. 'Jarvis code api: wolfram KEY' bolo."
    body, err = _http_get(
        f"https://api.wolframalpha.com/v1/result"
        f"?i={urllib.parse.quote(question)}&appid={api_key}", timeout=20)
    if body:
        ans = body.decode()
        if ans and "did not understand" not in ans.lower():
            return f"WolframAlpha: {ans}"
    body2, _ = _http_get(
        f"https://api.wolframalpha.com/v2/query"
        f"?input={urllib.parse.quote(question)}&appid={api_key}&output=json&format=plaintext",
        timeout=20)
    if body2:
        data = json.loads(body2)
        pods = data.get("queryresult",{}).get("pods",[])
        results = []
        for pod in pods[:4]:
            for sub in pod.get("subpods",[]):
                txt = sub.get("plaintext","").strip()
                if txt:
                    results.append(f"{pod.get('title','')}: {txt}")
        if results:
            return "\n".join(results)
    return "WolframAlpha se jawab nahi mila."


# ─────────────────────────────────────────────
# NASA Tools — APOD, Mars, ISS, Asteroids
# ─────────────────────────────────────────────

def _nasa_key():
    return memory.get_secret("nasa") or "DEMO_KEY"

def get_nasa_apod():
    """NASA Astronomy Picture of the Day — image seedha in-app dikhao"""
    # DEMO_KEY bhi kaam karta hai, lekin limited requests
    for api_key in [_nasa_key(), "DEMO_KEY"]:
        try:
            d = _jget(
                f"https://api.nasa.gov/planetary/apod?api_key={api_key}&thumbs=true",
                timeout=20)
            if not d or "error" in d:
                continue
            title = d.get("title","Aaj ka photo")
            date  = d.get("date","")
            expl  = (d.get("explanation","") or "")[:400]
            mtype = d.get("media_type","image")
            # hdurl kabhi-kabhi 5-10MB+ ki massive image hoti hai jo load
            # fail/timeout ho jaati hai phone pe — regular url use karo
            # (woh hamesha 1000-2000px, halka aur reliable hota hai)
            img   = d.get("url","") or d.get("hdurl","")
            thumb = d.get("thumbnail_url","")

            if mtype == "video":
                result = f"🌌 NASA APOD — {date}\n📷 {title}\n\n{expl}...\n"
                if thumb:
                    result += f"\nIMAGE_FOUND:{thumb}"
                if img:
                    result += f"\nVIDEO_FOUND:{img}|{title} (NASA APOD)"
                return result

            if img:
                img = img.replace("http://","https://")
                return (f"🌌 NASA APOD — {date}\n"
                        f"📷 {title}\n"
                        f"IMAGE_FOUND:{img}\n\n"
                        f"{expl}...")
            return f"🌌 NASA APOD — {date}\n📷 {title}\n{expl}..."
        except Exception:
            continue
    return "NASA APOD abhi nahi mila. Thodi der mein try karo."

def get_nasa_mars_photos():
    """NASA Mars Rover photos — Curiosity + Perseverance, in-app dikhao"""
    key = _nasa_key()
    # Curiosity latest photos try karo
    for rover, endpoint in [
        ("Curiosity",    f"https://api.nasa.gov/mars-photos/api/v1/rovers/curiosity/latest_photos?api_key={key}"),
        ("Perseverance", f"https://api.nasa.gov/mars-photos/api/v1/rovers/perseverance/latest_photos?api_key={key}"),
        ("Curiosity",    f"https://api.nasa.gov/mars-photos/api/v1/rovers/curiosity/photos?sol=3900&camera=navcam&api_key={key}&page=1"),
    ]:
        try:
            d = _jget(endpoint, timeout=25)
            photos = (d or {}).get("latest_photos", []) or (d or {}).get("photos", [])
            if not photos:
                continue
            sol   = photos[0].get("sol","?")
            edate = photos[0].get("earth_date","?")
            cam   = photos[0].get("camera",{}).get("full_name","?")
            lines = [f"🔴 NASA {rover} Rover — Sol {sol} ({edate})\n📷 Camera: {cam}"]
            for p in photos[:5]:
                img_url = p.get("img_src","").replace("http://","https://")
                if img_url:
                    lines.append(f"IMAGE_FOUND:{img_url}")
            return "\n".join(lines)
        except Exception:
            continue
    return "Mars photos abhi nahi mili. NASA API rate-limit ya network issue. Thodi der mein try karo."

def get_nasa_iss_location():
    """ISS ka real-time location + crew info"""
    lat, lon, alt, speed = None, None, None, None
    # wheretheiss.at — best API
    for api in [
        "https://api.wheretheiss.at/v1/satellites/25544",
        "https://api.open-notify.org/iss-now.json",
    ]:
        try:
            d = _jget(api.replace("http://","https://"), timeout=10)
            if not d:
                continue
            if "latitude" in d:
                lat   = float(d["latitude"])
                lon   = float(d["longitude"])
                alt   = d.get("altitude")
                speed = d.get("velocity")
                break
            elif "iss_position" in d:
                lat = float(d["iss_position"]["latitude"])
                lon = float(d["iss_position"]["longitude"])
                break
        except Exception:
            continue

    if lat is None:
        return "ISS location abhi nahi mili. Network check karo."

    # Crew info
    crew = []
    try:
        d2 = _jget("https://api.open-notify.org/astros.json", timeout=10)
        if d2:
            crew = [p["name"] for p in d2.get("people",[]) if p.get("craft")=="ISS"]
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")

    # Reverse geocode — ISS kahan ke upar hai
    location_str = ""
    try:
        geo = _jget(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers={"User-Agent":"JarvisApp/5.0"}, timeout=8)
        if geo:
            addr = geo.get("address",{})
            country = addr.get("country","")
            if country:
                location_str = f" ({country} ke upar)"
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")

    lines = [
        f"🛸 ISS Abhi Yahan Hai{location_str}:",
        f"📍 {lat:.3f}°, {lon:.3f}°",
        f"🗺️ Maps: https://maps.google.com/?q={lat},{lon}",
    ]
    if alt:
        lines.append(f"🔼 Altitude: {float(alt):.1f} km")
    if speed:
        lines.append(f"⚡ Speed: {float(speed):.0f} km/h")
    lines.append(f"👨‍🚀 Crew ({len(crew)}): {', '.join(crew) if crew else 'data nahi mila'}")
    return "\n".join(lines)

def get_nasa_asteroids():
    """Aaj Earth ke paas se guzarne wale asteroids — NASA NeoWs API"""
    try:
        today = datetime.date.today().isoformat()
        key = _nasa_key()
        d = _jget(
            f"https://api.nasa.gov/neo/rest/v1/feed"
            f"?start_date={today}&end_date={today}&api_key={key}",
            timeout=20)
        if not d:
            # DEMO_KEY fallback
            d = _jget(
                f"https://api.nasa.gov/neo/rest/v1/feed"
                f"?start_date={today}&end_date={today}&api_key=DEMO_KEY",
                timeout=20)
        neos = (d or {}).get("near_earth_objects", {}).get(today, [])
        if not neos:
            return f"Aaj ({today}) koi asteroid Earth ke paas nahi hai. 🌍 Safe!"
        total = len(neos)
        hazardous = [n for n in neos if n.get("is_potentially_hazardous_asteroid")]
        lines = [f"☄️ Aaj {total} asteroid(s) Earth ke paas ({len(hazardous)} potentially hazardous):"]
        sorted_neos = sorted(neos,
            key=lambda x: float(x.get("close_approach_data",[{}])[0].get("miss_distance",{}).get("kilometers",9e9)))
        for neo in sorted_neos[:5]:
            name = neo.get("name","?").strip("()")
            haz  = "⚠️ Khatarnak" if neo.get("is_potentially_hazardous_asteroid") else "✅ Safe"
            size_min = neo.get("estimated_diameter",{}).get("meters",{}).get("estimated_diameter_min",0)
            size_max = neo.get("estimated_diameter",{}).get("meters",{}).get("estimated_diameter_max",0)
            ca = neo.get("close_approach_data",[{}])[0]
            dist = float(ca.get("miss_distance",{}).get("kilometers",0))
            spd  = float(ca.get("relative_velocity",{}).get("kilometers_per_hour",0))
            ca_time = ca.get("close_approach_date_full","?")
            lines.append(
                f"\n• {name} | {haz}"
                f"\n  📏 Size: {size_min:.0f}–{size_max:.0f} m"
                f"\n  📍 Distance: {dist:,.0f} km"
                f"\n  ⚡ Speed: {spd:,.0f} km/h"
                f"\n  🕐 Closest: {ca_time}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Asteroid data error: {e}"


# ─────────────────────────────────────────────
# AI Image Generation — Pollinations.ai (free)
# ─────────────────────────────────────────────

IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "generated")

_hf_client_cache = {"client": None, "token": None}


def _get_hf_client(token: str):
    """
    HF InferenceClient ka ek instance cache/reuse karta hai (baar-baar
    naya banane mein thoda overhead hota hai). Token badal jaaye to naya
    client banta hai. Package na mile ya init fail ho to None deta hai —
    generate_image() phir Pollinations par gracefully fallback kar leta hai.
    """
    global _hf_client_cache
    if _hf_client_cache["client"] is not None and _hf_client_cache["token"] == token:
        return _hf_client_cache["client"]
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(provider="auto", api_key=token, timeout=45)
        _hf_client_cache = {"client": client, "token": token}
        return client
    except Exception:
        return None


def _try_generate_hf(prompt: str, width: int, height: int):
    """
    Hugging Face Inference Providers se FLUX.1-schnell try karta hai —
    provider="auto" khud sabse FAST available backend (fal-ai/together/
    replicate/etc.) choose kar leta hai. Typically 1-3 second mein image
    ready ho jaati hai — Pollinations se kaafi tez aur reliable, jab tak
    free HF token (huggingface.co/settings/tokens) configured ho.
    """
    hf_token = memory.get_secret("huggingface")
    if not hf_token:
        return None, "HF token configured nahi hai"
    client = _get_hf_client(hf_token)
    if not client:
        return None, "huggingface_hub package available nahi hai"
    try:
        image = client.text_to_image(
            prompt, model="black-forest-labs/FLUX.1-schnell",
            width=width, height=height,
        )
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=92)
        body = buf.getvalue()
        if len(body) < 5000:
            return None, "invalid/broken image response"
        return body, None
    except Exception as e:
        return None, str(e)


def generate_image(prompt: str, width: int = 1024, height: int = 1024, realistic: bool = True):
    """
    AI se image banata hai. Do sources try hote hain:

    1. PRIMARY — Hugging Face Inference Providers (FLUX.1-schnell model).
       Bahut TEZ (typically 1-3 second) aur reliable hai, kyunki
       provider="auto" khud sabse fast available infrastructure (fal-ai/
       together/replicate) choose karta hai. Isके liye ek FREE Hugging
       Face token chahiye (https://huggingface.co/settings/tokens se
       banao, "Read" access kaafi hai), phir 'Jarvis code api: huggingface
       hf_xxx' bol ke save karo. Free tier mein chhota monthly credit
       milta hai.

    2. FALLBACK — Pollinations.ai (free, bina kisi key ke). Agar HF token
       configured nahi hai, ya HF request kisi wajah se fail ho jaaye, to
       yeh automatically try hota hai — isliye image generation KABHI
       poori tarah nahi rukta, chahe koi setup ho ya na ho.

    realistic=True (default) -> photorealistic results ke liye (lighting,
    textures, camera-like detail). realistic=False -> tez but zyada
    stylized/cartoonish results.

    Broken/error responses (kabhi 200 status ke saath choti si error/
    placeholder image milti hai) ko detect karke retry karta hai, taaki
    kabhi bhi kaam na karne wali image save na ho.
    """
    import uuid

    body, err = _try_generate_hf(prompt, width, height)
    source = "hf"

    if body is None:
        source = "pollinations"

        def _try_pollinations(model_name: str, use_extra: bool):
            final_prompt = prompt
            if use_extra and not re.search(r"\b(photo|realistic|photorealistic|8k|4k)\b", prompt, re.I):
                final_prompt = f"{prompt}, photorealistic, ultra detailed, natural lighting, high resolution"
            url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(final_prompt)}"
                   f"?width={width}&height={height}&nologo=true&enhance=true"
                   f"&model={model_name}&seed={uuid.uuid4().int % 100000}")
            b, e = _http_get(url, timeout=90)
            if e or not b:
                return None, e or "empty response"
            is_jpeg = b[:2] == b"\xff\xd8"
            is_png = b[:8] == b"\x89PNG\r\n\x1a\n"
            if len(b) < 5000 or not (is_jpeg or is_png):
                return None, "invalid/broken image response"
            return b, None

        model = "flux" if realistic else "turbo"
        body, err2 = _try_pollinations(model, realistic)
        if body is None:
            fallback_model = "turbo" if model == "flux" else "flux"
            body, err3 = _try_pollinations(fallback_model, False)
            if body is None:
                return f"Image generate error: HF ({err}), Pollinations ({err3 or err2})"

    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:10]}.jpg"
        filepath = os.path.join(IMAGES_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(body)
        return f"IMAGE_GENERATED:/static/generated/{filename}"
    except Exception as e:
        return f"Image generate error: {e}"


# ═════════════════════════════════════════════
# PHASE 1 — ADVANCED TOOLS (v6 upgrade)
# Sab naye tools yahan hain. Style: kabhi exception raise nahi karte,
# hamesha ek readable Hinglish string return karte hain (chahe error ho),
# taaki Groq/brain.py kabhi crash na ho aur user ko hamesha jawab mile.
# ═════════════════════════════════════════════

# ── Calculator (offline, safe — no eval()) ──
import ast
import operator as _op

_SAFE_OPS = {
    ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
    ast.Div: _op.truediv, ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod,
    ast.Pow: _op.pow, ast.USub: _op.neg, ast.UAdd: _op.pos,
}

def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("invalid constant")
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("unsupported expression")

def calculate(expression: str):
    """Koi bhi math expression safely evaluate karta hai (+ - * / // % **), bina eval() ke."""
    try:
        expr = expression.strip().replace("x", "*").replace("×", "*").replace("÷", "/")
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"🧮 {expression} = {result}"
    except ZeroDivisionError:
        return "🧮 Error: zero se divide nahi ho sakta."
    except Exception:
        return f"🧮 '{expression}' samajh nahi aaya. Sirf numbers aur + - * / // % ** use karo."


# ── Unit Converter (offline) ──
_UNIT_TABLE = {
    "length": {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
               "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mile": 1609.34},
    "weight": {"mg": 0.001, "g": 1.0, "kg": 1000.0, "ton": 1_000_000.0,
               "oz": 28.3495, "lb": 453.592},
    "volume": {"ml": 0.001, "l": 1.0, "gallon": 3.78541, "cup": 0.24, "tbsp": 0.0147868, "tsp": 0.00492892},
    "speed": {"kmh": 1.0, "mph": 1.60934, "ms": 3.6, "knot": 1.852},
    "data": {"b": 1.0, "kb": 1024.0, "mb": 1024.0**2, "gb": 1024.0**3, "tb": 1024.0**4},
}

def convert_units(value: float, from_unit: str, to_unit: str):
    """Length/weight/volume/speed/data-size units ke beech convert karta hai. Temperature ke liye from_unit='c'/'f'/'k' use karo."""
    try:
        fu, tu = from_unit.strip().lower(), to_unit.strip().lower()

        # Temperature (special-case, non-linear)
        temp_units = {"c", "f", "k", "celsius", "fahrenheit", "kelvin"}
        if fu in temp_units or tu in temp_units:
            norm = {"celsius": "c", "fahrenheit": "f", "kelvin": "k"}
            fu2, tu2 = norm.get(fu, fu), norm.get(tu, tu)
            c = {"c": value, "f": (value - 32) * 5/9, "k": value - 273.15}.get(fu2)
            if c is None:
                return f"❌ Temperature unit '{from_unit}' samajh nahi aayi. c/f/k use karo."
            result = {"c": c, "f": c * 9/5 + 32, "k": c + 273.15}.get(tu2)
            if result is None:
                return f"❌ Temperature unit '{to_unit}' samajh nahi aayi. c/f/k use karo."
            return f"🌡️ {value}°{fu2.upper()} = {round(result, 2)}°{tu2.upper()}"

        for category, table in _UNIT_TABLE.items():
            if fu in table and tu in table:
                base = value * table[fu]
                result = base / table[tu]
                return f"📏 {value} {from_unit} = {round(result, 6)} {to_unit}"
        return f"❌ '{from_unit}' → '{to_unit}' conversion supported nahi hai. Length/weight/volume/speed/data/temp try karo."
    except Exception as e:
        return f"❌ Conversion error: {e}"


# ── Currency Converter (live, free — no key needed) ──
def convert_currency(amount: float, from_currency: str, to_currency: str):
    """Live exchange rate se currency convert karta hai (open.er-api.com, free, no key)."""
    try:
        fc, tc = from_currency.strip().upper(), to_currency.strip().upper()
        data = _jget(f"https://open.er-api.com/v6/latest/{fc}", timeout=12)
        if not data or data.get("result") != "success":
            return f"❌ Currency rates abhi load nahi ho payi. Internet check karo ya thodi der baad try karo."
        rates = data.get("rates", {})
        if tc not in rates:
            return f"❌ '{tc}' currency code nahi mila. ISO code use karo (USD, INR, EUR, GBP, etc)."
        result = amount * rates[tc]
        return f"💱 {amount} {fc} = {round(result, 2)} {tc} (rate: 1 {fc} = {round(rates[tc], 4)} {tc})"
    except Exception as e:
        return f"❌ Currency convert error: {e}"


# ── Translation (free, no key — MyMemory API) ──
def translate_text(text: str, target_lang: str = "hi"):
    """Text ko kisi bhi language mein translate karta hai. target_lang: hi, en, fr, es, etc (ISO 639-1)."""
    text = (text or "").strip()
    if not text:
        return "❌ Translate karne ke liye pehle kuch text likho."
    try:
        url = (f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text[:490])}"
               f"&langpair=auto|{urllib.parse.quote(target_lang)}")
        data = _jget(url, timeout=15)
        if not data or "responseData" not in data:
            return "❌ Translation service abhi available nahi hai."
        translated = data["responseData"].get("translatedText", "")
        if not translated:
            return "❌ Translate nahi ho paya."
        return f"🌐 Translation ({target_lang}): {translated}"
    except Exception as e:
        return f"❌ Translation error: {e}"


# ── Dictionary + Synonyms (free, no key) ──
def get_dictionary(word: str):
    """Kisi English word ka meaning, pronunciation, examples aur synonyms deta hai."""
    if not (word or "").strip():
        return "❌ Kis word ka meaning chahiye, woh batao."
    try:
        w = word.strip().lower()
        data = _jget(f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(w)}", timeout=12)
        lines = []
        if data and isinstance(data, list):
            entry = data[0]
            phonetic = entry.get("phonetic", "")
            lines.append(f"📖 **{entry.get('word', w)}** {phonetic}")
            for meaning in entry.get("meanings", [])[:3]:
                pos = meaning.get("partOfSpeech", "")
                defs = meaning.get("definitions", [])[:2]
                for d in defs:
                    lines.append(f"  ({pos}) {d.get('definition','')}")
                syns = meaning.get("synonyms", [])[:5]
                if syns:
                    lines.append(f"  Synonyms: {', '.join(syns)}")
        else:
            # Fallback: Datamuse for synonyms only
            syn_data = _jget(f"https://api.datamuse.com/words?rel_syn={urllib.parse.quote(w)}&max=8", timeout=10)
            if syn_data:
                syns = [x["word"] for x in syn_data]
                if syns:
                    return f"📖 '{w}' ka direct meaning nahi mila, lekin similar words: {', '.join(syns)}"
            return f"❌ '{word}' ka meaning nahi mil paya."
        return "\n".join(lines) if lines else f"❌ '{word}' ka meaning nahi mil paya."
    except Exception as e:
        return f"❌ Dictionary error: {e}"


# ── Wikipedia Summary ──
def get_wikipedia_summary(query: str):
    """Kisi topic/person/place ka Wikipedia summary laata hai."""
    query = (query or "").strip()
    if not query:
        return "❌ Kis topic ke baare mein jaanna hai, woh batao."
    try:
        title = urllib.parse.quote(query.strip().replace(" ", "_"))
        data = _jget(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=12)
        if data and data.get("extract"):
            return f"📚 **{data.get('title', query)}**\n{data['extract']}\n🔗 {data.get('content_urls',{}).get('desktop',{}).get('page','')}"
        # Fallback: search API to find closest matching title
        search = _jget(
            f"https://en.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(query)}&limit=1&format=json",
            timeout=12)
        if search and len(search) > 1 and search[1]:
            best_title = urllib.parse.quote(search[1][0].replace(" ", "_"))
            data2 = _jget(f"https://en.wikipedia.org/api/rest_v1/page/summary/{best_title}", timeout=12)
            if data2 and data2.get("extract"):
                return f"📚 **{data2.get('title')}**\n{data2['extract']}\n🔗 {data2.get('content_urls',{}).get('desktop',{}).get('page','')}"
        return f"❌ '{query}' par Wikipedia article nahi mila."
    except Exception as e:
        return f"❌ Wikipedia error: {e}"


# ── Crypto Prices (free, no key — CoinGecko) ──
def get_crypto_price(coin: str = "bitcoin"):
    """Kisi bhi cryptocurrency ka live price deta hai (USD + INR). coin='bitcoin', 'ethereum', etc."""
    try:
        c = coin.strip().lower().replace(" ", "-")
        aliases = {"btc": "bitcoin", "eth": "ethereum", "doge": "dogecoin", "sol": "solana",
                   "bnb": "binancecoin", "xrp": "ripple", "ada": "cardano"}
        c = aliases.get(c, c)
        data = _jget(f"https://api.coingecko.com/api/v3/simple/price?ids={c}&vs_currencies=usd,inr&include_24hr_change=true", timeout=12)
        if not data or c not in data:
            return f"❌ '{coin}' crypto coin nahi mila. Try: bitcoin, ethereum, dogecoin, solana."
        info = data[c]
        change = info.get("usd_24h_change")
        change_str = f" ({'+' if change and change >= 0 else ''}{round(change,2)}% 24h)" if change is not None else ""
        return f"₿ {coin.title()}: ${info.get('usd','?')} | ₹{info.get('inr','?')}{change_str}"
    except Exception as e:
        return f"❌ Crypto price error: {e}"


# ── QR Code Generator ──
def generate_qr(text: str):
    """Kisi text/URL ka QR code image banata hai (chat mein dikhta hai)."""
    text = (text or "").strip()
    if not text:
        return "❌ QR code ke liye pehle text ya URL likho (jaise: https://example.com)."
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        import uuid
        filename = f"qr_{uuid.uuid4().hex[:10]}.png"
        filepath = os.path.join(IMAGES_DIR, filename)
        url = f"https://api.qrserver.com/v1/create-qr-code/?size=350x350&data={urllib.parse.quote(text)}"
        body, err = _http_get(url, timeout=20)
        if err or not body:
            return f"❌ QR code generate nahi ho paya: {err or 'internet check karo'}."
        with open(filepath, "wb") as f:
            f.write(body)
        return f"IMAGE_GENERATED:/static/generated/{filename}"
    except Exception as e:
        return f"❌ QR generate error: {e}"


# ── Random Quote / Motivation ──
_OFFLINE_QUOTES = [
    "Success ek journey hai, destination nahi.",
    "Har din ek naya mauka hai khud ko behtar banane ka.",
    "Mushkil raste aksar khoobsurat manzil tak le jaate hain.",
    "Consistency talent se zyada powerful hoti hai.",
    "Jo aaj mehnat karta hai, kal wahi jeetega.",
]

def get_random_quote():
    """Ek motivational/inspirational quote deta hai."""
    for url in ("https://zenquotes.io/api/random", "https://api.quotable.io/random"):
        try:
            data = _jget(url, timeout=10)
            if data:
                if isinstance(data, list) and data and "q" in data[0]:
                    return f"💬 \"{data[0]['q']}\" — {data[0].get('a','Unknown')}"
                if isinstance(data, dict) and "content" in data:
                    return f"💬 \"{data['content']}\" — {data.get('author','Unknown')}"
        except Exception:
            continue
    import random
    return f"💬 {random.choice(_OFFLINE_QUOTES)}"


# ── Password Generator (offline, secure) ──
def generate_password(length: int = 16, use_symbols: bool = True):
    """Strong random password generate karta hai (secrets module — cryptographically secure)."""
    try:
        import secrets, string
        length = max(6, min(int(length), 128))
        chars = string.ascii_letters + string.digits
        if use_symbols:
            chars += "!@#$%^&*()-_=+"
        pwd = "".join(secrets.choice(chars) for _ in range(length))
        return f"🔐 Generated password ({length} chars): {pwd}\n⚠️ Ise kahin safe jagah save karo, yeh dobara nahi milega."
    except Exception as e:
        return f"❌ Password generate error: {e}"


# ── Text Analyzer (offline) ──
def text_analyzer(text: str):
    """Text ka word count, character count, sentence count, aur estimated reading time deta hai."""
    if not (text or "").strip():
        return "❌ Analyze karne ke liye pehle kuch text do."
    try:
        words = text.split()
        word_count = len(words)
        char_count = len(text)
        char_no_space = len(text.replace(" ", ""))
        sentences = [s for s in re.split(r'[.!?]+', text) if s.strip()]
        sentence_count = len(sentences)
        reading_time_sec = round((word_count / 200) * 60)  # avg 200 wpm
        return (f"📝 Analysis:\n"
                f"• Words: {word_count}\n"
                f"• Characters: {char_count} ({char_no_space} bina space ke)\n"
                f"• Sentences: {sentence_count}\n"
                f"• Estimated reading time: {reading_time_sec} sec")
    except Exception as e:
        return f"❌ Text analyze error: {e}"


# ── Todo List (TinyDB agar available hai, warna JSON fallback) ──
def add_todo(task: str, priority: str = "medium"):
    """Ek naya todo/task add karta hai. priority: low/medium/high."""
    task = (task or "").strip()
    if not task:
        return "❌ Kaunsa task add karna hai, woh batao."
    priority = (priority or "medium").lower()
    now = datetime.datetime.now().isoformat()
    db = _get_db()
    if db:
        table = db.table("todos")
        new_id = (max([t.get("id", 0) for t in table.all()], default=0)) + 1
        table.insert({"id": new_id, "task": task, "priority": priority, "done": False, "created": now})
        db.close()
    else:
        rows = _fallback_load("todos")
        new_id = (max([t.get("id", 0) for t in rows], default=0)) + 1
        rows.append({"id": new_id, "task": task, "priority": priority, "done": False, "created": now})
        _fallback_save("todos", rows)
    return f"✅ Todo #{new_id} add ho gaya: '{task}' (priority: {priority})"

def list_todos():
    """Saare pending aur completed todos dikhata hai."""
    db = _get_db()
    if db:
        table = db.table("todos")
        all_todos = table.all()
        db.close()
    else:
        all_todos = _fallback_load("todos")
    if not all_todos:
        return "📋 Koi todo nahi hai. Naya add karne ke liye bolo: 'add todo <task>'."
    pending = [t for t in all_todos if not t.get("done")]
    done = [t for t in all_todos if t.get("done")]
    lines = ["📋 **Pending:**"]
    prio_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for t in sorted(pending, key=lambda x: {"high":0,"medium":1,"low":2}.get(x.get("priority","medium"),1)):
        lines.append(f"  {prio_icon.get(t.get('priority','medium'),'🟡')} #{t['id']} {t['task']}")
    if done:
        lines.append("✅ **Completed:**")
        for t in done:
            lines.append(f"  ✔️ #{t['id']} {t['task']}")
    return "\n".join(lines)

def complete_todo(task_id: int):
    """Kisi todo ko complete mark karta hai, ID se."""
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return "❌ Sahi todo ID number do."
    db = _get_db()
    if db:
        from tinydb import Query
        T = Query()
        table = db.table("todos")
        updated = table.update({"done": True}, T.id == task_id)
        db.close()
        return f"✅ Todo #{task_id} complete mark ho gaya!" if updated else f"❌ Todo #{task_id} nahi mila."
    else:
        rows = _fallback_load("todos")
        found = False
        for t in rows:
            if t.get("id") == task_id:
                t["done"] = True
                found = True
        _fallback_save("todos", rows)
        return f"✅ Todo #{task_id} complete mark ho gaya!" if found else f"❌ Todo #{task_id} nahi mila."

def delete_todo(task_id: int):
    """Kisi todo ko permanently delete karta hai, ID se."""
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return "❌ Sahi todo ID number do."
    db = _get_db()
    if db:
        from tinydb import Query
        T = Query()
        table = db.table("todos")
        removed = table.remove(T.id == task_id)
        db.close()
        return f"🗑️ Todo #{task_id} delete ho gaya." if removed else f"❌ Todo #{task_id} nahi mila."
    else:
        rows = _fallback_load("todos")
        new_rows = [t for t in rows if t.get("id") != task_id]
        removed = len(new_rows) != len(rows)
        _fallback_save("todos", new_rows)
        return f"🗑️ Todo #{task_id} delete ho gaya." if removed else f"❌ Todo #{task_id} nahi mila."


# ── System Info (Termux/device diagnostics) ──
def system_info():
    """Phone/Termux system ki jaankari deta hai — storage, Python version, OS."""
    try:
        import shutil, platform
        lines = ["🖥️ **System Info:**"]
        lines.append(f"• Python: {platform.python_version()}")
        lines.append(f"• Platform: {platform.platform()}")
        try:
            total, used, free = shutil.disk_usage("/")
            lines.append(f"• Storage: {used//(2**30)}GB used / {total//(2**30)}GB total ({free//(2**30)}GB free)")
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")
        try:
            out, _, rc = _run(["termux-battery-status"], timeout=5)
            if rc == 0 and out:
                bat = json.loads(out)
                lines.append(f"• Battery: {bat.get('percentage','?')}% ({bat.get('status','?')})")
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ System info error: {e}"


# ── Morning Briefing (composite/smart tool) ──
def morning_briefing(city: str = "Delhi"):
    """Ek hi jawab mein: time, weather, top news headlines, aur ek motivational quote — sab combine karke."""
    parts = ["☀️ **Aaj ka Briefing:**\n"]
    try:
        parts.append(get_current_time())
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    try:
        parts.append("\n🌤️ " + get_weather(city))
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    try:
        parts.append("\n📰 " + get_news("india"))
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    try:
        parts.append("\n" + get_random_quote())
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return "\n".join(parts)


# ─────────────────────────────────────────────
# Logic Controller
# ─────────────────────────────────────────────

def smart_search(query: str):
    q = query.lower().strip()

    country_words = ["country","desh","nation","capital","population","currency","language","flag"]
    if any(w in q for w in country_words):
        q2 = q
        for w in country_words:
            q2 = q2.replace(w, "")
        q2 = q2.replace("ki jankari", "").replace("ke baare mein", "").strip()
        return get_country_info(q2 or query)

    if "ip" in q and any(w in q for w in ["address","location","info","kahan","isp"]):
        ip = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', query)
        return get_ip_info(ip.group(0) if ip else "")

    if any(w in q for w in ["spacex","rocket","falcon","dragon","launch"]):
        upcoming = any(w in q for w in ["upcoming","aane wala","next","future"])
        return get_spacex_launches(upcoming=upcoming)

    if any(w in q for w in ["sunrise","sunset","suraj","subah","sham","sun rise","sun set"]):
        city = re.sub(r"(sunrise|sunset|suraj|subah|sham|sun rise|sun set|mein|ka|ki|kab)", "", q).strip()
        return get_sunrise_sunset(city or "Delhi")

    if any(w in q for w in ["holiday","chutti","public holiday"]):
        cc = re.search(r'\b([A-Z]{2})\b', query)
        return get_public_holidays(cc.group(1) if cc else "IN")

    if any(w in q for w in ["radio","station","fm","stream"]):
        return search_radio(query.replace("radio","").strip())

    if any(w in q for w in ["map","place","kahan hai","location of","where is"]):
        return search_place_osm(query)

    return web_search(query)


def diagnose_errors(limit: int = 5) -> str:
    """
    Jab user chat mein pooche 'kya dikkat hai', 'code mein kya galti hai',
    'kaun sa tool fail ho raha hai' — yeh function memory/jarvis_errors.log
    padh ke, saadi bhasha mein batata hai kaunsi file/function mein,
    kis line par, kya real problem hai. Raw traceback ya code-format nahi
    fenkta, seedha samajh mein aane wala jawab deta hai.
    """
    import logger as _logger_mod
    return _logger_mod.summarize_recent_errors(limit=limit)


# ─────────────────────────────────────────────
# HLS Stream Playback — ab seedha Jarvis CHAT ke andar chalta hai
# ─────────────────────────────────────────────
# BUG FIX (v10 se pehle): yeh feature server/phone ki machine par ffplay/vlc
# subprocess spawn karke bajata tha. Render (cloud) ke paas na speaker hai
# na screen, isliye woh playback kabhi dikhta/sunayi hi nahi deta tha — aur
# phone/Termux par bhi woh background mein, chat ke bahar bajta tha.
# Ab yeh sirf ek HLS_FOUND: token return karta hai; static/app.js is token
# ko uthaakar hls.js (browser library) se seedha chat bubble ke andar ek
# <video> element mein play karta hai — koi external link nahi khulta,
# aur jahan bhi (Render/Termux/kahin bhi) host karo wahi se kaam karta hai.
# /api/hlsproxy (server.py) manifest + segments proxy karta hai taaki CORS
# kabhi rukaavat na bane.

_last_hls = {"url": None, "title": None}


def _ytdlp_resolve_any(url: str):
    """
    yt-dlp ka GENERIC extractor — sirf YouTube nahi, 1000+ sites support
    karta hai (Vimeo, Dailymotion, Twitter/X, Instagram, Facebook, TikTok,
    Twitch, SoundCloud, news-site embeds, aur bahut kuch). Diye gaye kisi
    bhi link ko actual playable stream URL (direct file ya HLS manifest)
    mein resolve karta hai.
    Isolated subprocess mein chalta hai (_run_isolated) — crash-safe.
    Returns dict {"url","ext","title","is_live"} ya None (extract nahi ho paya).
    """
    code = (
        "import sys, json\n"
        "try:\n"
        "    import yt_dlp\n"
        "except ImportError:\n"
        "    print(json.dumps({'error':'no_ytdlp'})); sys.exit(0)\n"
        f"url = {url!r}\n"
        "opts = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'noplaylist': True}\n"
        "try:\n"
        "    with yt_dlp.YoutubeDL(opts) as ydl:\n"
        "        info = ydl.extract_info(url, download=False)\n"
        "    if not info:\n"
        "        print(json.dumps({'error':'no_info'})); sys.exit(0)\n"
        "    stream_url = info.get('url','') or ''\n"
        "    ext = info.get('ext','') or ''\n"
        "    if not stream_url:\n"
        "        formats = info.get('formats') or []\n"
        "        for f in reversed(formats):\n"
        "            if f.get('url'):\n"
        "                stream_url = f['url']; ext = f.get('ext','') or ''; break\n"
        "    if not stream_url:\n"
        "        print(json.dumps({'error':'no_stream_url'})); sys.exit(0)\n"
        "    print(json.dumps({'url': stream_url, 'ext': ext,"
        " 'title': info.get('title','') or '', 'is_live': bool(info.get('is_live'))}))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)[:200]}))\n"
    )
    result = _run_isolated(code, timeout=40)
    if result and not result.get("error") and result.get("url"):
        return result
    return None


def play_stream(url: str, title: str = "", quality: str = None) -> str:
    """
    Ek video/stream link ko seedha Jarvis chat ke andar play karta hai.
    Link type khud detect karta hai:
      - YouTube link -> chat ke andar YouTube embed (sabse fast/reliable)
      - Direct video file (.mp4/.webm/.mov/.mkv) -> chat ke andar <video>
      - .m3u8 (HLS) -> chat ke andar hls.js player
      - KOI BHI AUR LINK (Vimeo, Instagram, Twitter/X, Facebook, TikTok,
        Twitch, SoundCloud, news-embed, aadi) -> yt-dlp ke generic
        extractor (1000+ sites) se real stream URL resolve karke,
        format ke hisaab se HLS ya direct <video> mein play karta hai.
    quality: optional — "144p"/"240p"/.../"1080p"/"4k", "hd"/"sd"/"fhd",
      "auto", "low"/"kam data", "high"/"best". Diya na jaaye to user ki
      saved default quality preference (set_default_stream_quality) use
      hoti hai, warna adaptive/auto (hls.js khud best pick karega).
      Sirf multi-quality (master playlist) HLS streams par lagu hota hai.
    User ke paas pehle se legitimate/authorized URL hona chahiye — yeh
    URL dhundhta/scrape nahi karta, sirf diya gaya URL play karta hai.
    """
    url = (url or "").strip().strip("<>\"'")
    if not url.lower().startswith(("http://", "https://")):
        return "❌ Play karne ke liye ek valid http(s) video/stream URL do."

    label = (title or "").strip() or "Stream"
    path = urllib.parse.urlparse(url).path.lower()

    # YouTube link -> existing VIDEO_FOUND pipeline (iframe embed) use karo
    if "youtube.com/watch" in url.lower() or "youtu.be/" in url.lower() or "youtube.com/shorts" in url.lower():
        return f"▶️ YouTube video chat mein play ho rahi hai.\nVIDEO_FOUND:{url}|{label}"

    # Direct video file (mp4/webm/mov/mkv/etc.) -> existing VIDEO_FOUND
    # pipeline (proxied <video> tag) use karo — HLS player ki zarurat nahi.
    _direct_video_exts = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi")
    if path.endswith(_direct_video_exts):
        return f"▶️ Video chat mein play ho rahi hai.\nVIDEO_FOUND:{url}|{label}"

    # .m3u8 explicitly -> seedha HLS, generic resolve ki zarurat nahi
    if path.endswith(".m3u8"):
        return _play_as_hls(url, label, quality)

    # Yahan tak pahunche matlab link ka type pata nahi (Vimeo/Insta/Twitter/
    # generic page/live-endpoint waghera ho sakta hai). Pehle check karo
    # ki kya ye khud hi valid m3u8 hai (extension-less live streams aise
    # hi hote hain):
    try:
        resolve_manifest(url)
        return _play_as_hls(url, label, quality)
    except Exception:
        pass  # valid m3u8 nahi hai — aage generic extractor try karo

    # Generic yt-dlp extractor — 1000+ sites support (real "koi bhi link" fix)
    resolved = _ytdlp_resolve_any(url)
    if resolved:
        real_url = resolved["url"]
        real_title = resolved.get("title") or label
        ext = (resolved.get("ext") or "").lower()
        if ext == "m3u8" or real_url.lower().split("?")[0].endswith(".m3u8"):
            tag = " (Live)" if resolved.get("is_live") else ""
            return _play_as_hls(real_url, real_title, quality, extra_tag=tag)
        return f"▶️ Video chat mein play ho rahi hai.\nVIDEO_FOUND:{real_url}|{real_title}"

    # Sab kuch fail — pehle jaisa silently HLS assume karke bhejna galat
    # tha (browser mein chup-chaap fail ho jaata tha). Ab saaf batao.
    return (f"❌ Ye link resolve nahi ho paya — na to yeh valid HLS (.m3u8) "
            f"stream hai, na hi yt-dlp ke supported 1000+ sites mein se koi. "
            f"Link check karo ya doosra try karo.")


def _play_as_hls(url: str, label: str, quality: str = None, extra_tag: str = "") -> str:
    """
    HLS stream play karta hai. Agar yeh ek multi-quality "master
    playlist" hai (#EXT-X-STREAM-INF variants ke saath), to requested
    quality (ya saved default preference) ke sabse kareebi variant
    URL chunta hai — data usage user ke control mein rehta hai. Single-
    quality stream ho ya variant lookup fail ho jaaye, to original URL
    hi (bina kisi break ke) play hoti hai.
    """
    play_url = url
    quality_note = ""

    effective_quality = quality or memory.get_stream_quality_pref()
    if effective_quality and hls_quality.normalize_quality(effective_quality) not in (None, "auto"):
        try:
            info = hls_quality.get_stream_qualities(url)
            if info.get("is_master") and info.get("variants"):
                variant, mode = hls_quality.pick_variant(info["variants"], effective_quality)
                if variant:
                    play_url = variant["url"]
                    quality_note = f" [{variant['label']}]"
            elif quality:  # user ne is baar specifically maanga tha, aur mila nahi
                quality_note = " (yeh channel sirf ek hi quality mein available hai)"
        except Exception as e:
            log.info(f"_play_as_hls: quality resolve fail, default URL play kar rahe — {e}")

    tag = extra_tag
    try:
        info = resolve_manifest(play_url)
        if info.get("is_live") is True:
            tag = tag or " (Live)"
        elif info.get("is_live") is False:
            tag = tag or " (VOD)"
    except Exception:
        log.info(f"play_stream: resolve_manifest failed for {play_url}, sending raw URL to frontend anyway")

    _last_hls.update({"url": play_url, "title": label})
    return f"▶️ Stream chat mein play ho rahi hai{tag}{quality_note}.\nHLS_FOUND:{play_url}|{label}{tag}{quality_note}"


def list_stream_qualities(url: str) -> str:
    """Diye gaye .m3u8 link mein konsi qualities (144p se 4k tak) available hain, list karta hai."""
    url = (url or "").strip().strip("<>\"'")
    if not url.lower().startswith(("http://", "https://")):
        return "❌ Ek valid http(s) .m3u8 URL do."
    info = hls_quality.get_stream_qualities(url)
    if info.get("error") and not info.get("variants"):
        return f"❌ Playlist check nahi ho payi — {info['error']}"
    if not info.get("is_master") or not info.get("variants"):
        return "📡 Yeh stream sirf ek hi quality mein available hai (multi-quality nahi)."
    lines = [f"• {v['label']}" + (f" (~{v['bandwidth']//1000}kbps)" if v.get("bandwidth") else "") for v in info["variants"]]
    return "📺 Available qualities:\n" + "\n".join(lines)


def set_default_stream_quality(quality: str) -> str:
    """
    User ka default stream quality preference save karta hai (data usage
    control ke liye) — future mein har stream/channel play isi quality
    mein try hogi (agar available ho). "auto" set karne se adaptive
    (hls.js khud best pick karega) wapas ho jaata hai.
    """
    quality = (quality or "").strip()
    if not quality:
        return "❌ Konsi quality set karni hai batao — 144p, 240p, 360p, 480p, 720p, 1080p, 4k, ya 'auto'."
    norm = hls_quality.normalize_quality(quality)
    if norm is None:
        return (f"❌ '{quality}' samajh nahi aayi. In mein se koi try karo: 144p, 240p, 360p, 480p, "
                f"720p (HD), 1080p (FHD), 4k, 'auto', 'kam data'/'low', 'best'/'high'.")
    memory.set_stream_quality_pref(quality)
    if norm == "auto":
        return "✅ Default quality 'auto' set kar di — ab hls.js khud internet ke hisaab se best quality choose karega."
    return f"✅ Default stream quality '{quality}' set kar di. Ab har stream/channel isi quality mein try hogi (agar available ho) — data usage control mein rahega."


def get_default_stream_quality() -> str:
    """Abhi ka default stream quality preference batata hai."""
    pref = memory.get_stream_quality_pref()
    if not pref or hls_quality.normalize_quality(pref) == "auto":
        return "📡 Abhi default quality 'auto' hai (adaptive — internet ke hisaab se apne aap best quality choose hoti hai)."
    return f"📡 Abhi default stream quality '{pref}' set hai."


def pause_stream() -> str:
    """Chat mein chal rahi HLS stream ko pause karta hai (frontend control token)."""
    if not _last_hls["url"]:
        return "Abhi koi stream chat mein load nahi hai."
    return "⏸️ Pause kar diya.\nHLS_CONTROL:pause"


def resume_stream() -> str:
    """Chat mein pause ki hui HLS stream ko resume karta hai."""
    if not _last_hls["url"]:
        return "Abhi koi stream chat mein load nahi hai."
    return "▶️ Resume kar diya.\nHLS_CONTROL:resume"


def stop_stream() -> str:
    """Chat mein chal rahi HLS stream ko band/remove karta hai."""
    if not _last_hls["url"]:
        return "Kuch chal hi nahi raha tha."
    _last_hls.update({"url": None, "title": None})
    return "⏹️ Stream band kar diya.\nHLS_CONTROL:stop"


def stop_all_streams() -> str:
    """Chat mein chal rahi SAARI HLS streams ek saath band karta hai (agar multiple chal rahi ho)."""
    _last_hls.update({"url": None, "title": None})
    return "⏹️ Saari streams band kar di.\nHLS_CONTROL:stopall"


def stream_status() -> str:
    """Batata hai koi HLS stream chat mein load/playing hai ya nahi."""
    if not _last_hls["url"]:
        return "Abhi koi stream nahi chal rahi."
    return f"▶️ Chal rahi hai: {_last_hls['title']} — {_last_hls['url']}\nHLS_CONTROL:status"


def save_stream(name: str, url: str) -> str:
    """
    User ke paas jo legitimate stream/video URL already hai, use ek naam
    ke saath yaad rakh leta hai — taaki dobara poora URL bolna/type karna
    na pade. Yeh khud koi URL dhundhta/scrape nahi karta, sirf user ne jo
    diya wahi save karta hai.
    """
    name = (name or "").strip()
    url = (url or "").strip().strip("<>\"'")
    if not name:
        return "❌ Stream ka koi naam do."
    if not url.lower().startswith(("http://", "https://")):
        return "❌ Save karne ke liye ek valid http(s) URL do."
    memory.save_named_stream(name, url)
    return f"✅ '{name}' save kar diya. Ab bas 'play {name}' bolo."


def play_saved_stream(name: str) -> str:
    """Pehle 'save_stream' se naam ke saath save kiya gaya stream play karta hai."""
    name = (name or "").strip()
    url = memory.get_named_stream(name)
    if not url:
        return f"❌ '{name}' naam se koi stream saved nahi mila. Pehle 'save_stream' se save karo."
    return play_stream(url, title=name)


def list_saved_streams() -> str:
    """Saare saved stream naam + URL list karta hai."""
    streams = memory.list_named_streams()
    if not streams:
        return "Abhi koi stream saved nahi hai."
    lines = [f"• {name} → {url}" for name, url in streams.items()]
    return "📺 Saved streams:\n" + "\n".join(lines)


def delete_saved_stream(name: str) -> str:
    """Ek saved stream ko naam se hata deta hai."""
    ok = memory.delete_named_stream(name)
    return f"🗑️ '{name}' hata diya." if ok else f"❌ '{name}' naam se koi saved stream nahi mila."




def start_stream_monitor(poll_seconds: int = 5):
    """
    NOTE: HLS playback ab browser (chat) ke andar hoti hai, isliye server-side
    subprocess monitor karne ki zarurat nahi rahi. No-op rakha gaya hai sirf
    taaki main.py / phone_agent.py ke startup calls crash na karein.
    """
    return None


def stop_stream_monitor():
    """No-op — dekho start_stream_monitor() ka note."""
    return None


# ─────────────────────────────────────────────
# Persona / Roleplay System
# ─────────────────────────────────────────────

def activate_persona(character_name: str, description: str, speaking_style: str = "", voice_gender: str = ""):
    """
    Jarvis ko diye gaye character/role mein dhaal deta hai.
    character_name: chhota naam (e.g. "Pirate Captain", "Sherlock Holmes", "Best Dost")
    description: character kaisa hai, uska background, personality, mood
    speaking_style: (optional) kaise baat karta hai — tone, language, catchphrases
    voice_gender: (optional) "male" ya "female" — agar tumhe pata hai character
    kaisa sound karega, seedha bata do. Warna description se khud andaza lagaya jaayega.
    """
    if not character_name or not description:
        return "Persona activate karne ke liye character ka naam aur description zaroori hai."

    persona = memory.set_active_persona(character_name, description, speaking_style, voice_gender)
    style_line = f"\n🎭 Style: {persona['style']}" if persona["style"] else ""
    voice_line = f"\n🔊 Awaaz: {'Female' if persona['voice_gender']=='female' else 'Male'} Hindi voice"
    return (
        f"Theek hai! Ab main '{persona['name']}' ban gaya hoon.{style_line}{voice_line}\n"
        f"Jab tak tum kaho 'wapas normal Jarvis bano' ya 'roleplay band karo', "
        f"main isi character mein rahunga."
    )


def deactivate_persona():
    """Active persona hata ke Jarvis ko wapas normal mode mein le aata hai."""
    had_one = memory.clear_active_persona()
    if had_one:
        return "Wapas normal Jarvis mode mein aa gaya hoon."
    return "Abhi koi persona active nahi thi — main already normal Jarvis hoon."


def get_current_persona():
    """Abhi konsa persona active hai, batata hai."""
    active = memory.get_active_persona()
    if not active:
        return "Abhi main normal Jarvis mode mein hoon, koi roleplay active nahi hai."
    style_line = f", Style: {active['style']}" if active.get("style") else ""
    return f"Abhi active persona: {active['name']} — {active['description']}{style_line}"


def list_saved_personas():
    """Pehle bane saare personas ki list deta hai, taaki dobara use ho saken."""
    saved = memory.get_saved_personas()
    if not saved:
        return "Koi saved persona nahi hai abhi tak."
    lines = ["Saved personas:"]
    for name, data in saved.items():
        lines.append(f"- {name}: {data.get('description', '')[:80]}")
    return "\n".join(lines)


def switch_to_saved_persona(character_name: str):
    """Pehle se saved kisi persona par wapas switch karta hai, uska naam bol ke."""
    saved = memory.get_saved_persona(character_name)
    if not saved:
        return (f"'{character_name}' naam ka koi saved persona nahi mila. "
                f"list_saved_personas se dekho ya naya banao.")
    persona = memory.set_active_persona(
        character_name, saved["description"], saved.get("style", ""), saved.get("voice_gender", ""))
    return f"Theek hai! Wapas '{persona['name']}' ban gaya hoon."


# ─────────────────────────────────────────────
# NOTE: TOOL_FUNCTIONS / TOOL_DEFINITIONS sirf brain.py mein hote hain.
# (Pehle yahan ek duplicate/unused TOOL_FUNCTIONS dict tha — hata diya gaya
# hai, kyunki yeh khud is file ke documented rule ko todta tha aur future
# self-evolution edits mein crash/confusion ka risk tha.)
# ─────────────────────────────────────────────
