"""
Jarvis Logger
-------------
Sab modules (tools.py, server.py, brain.py, etc.) yahan se logger lete hain.
Pehle sab jagah `except Exception: pass` tha — matlab jab koi tool fail hota
tha, koi trace hi nahi milta tha ki kya hua. Ab har error yahan file mein
save hota hai, WITH context (kaunsa function, kaunsi file, kya exception).

Use karne ka tarika (kisi bhi file mein):
    from logger import get_logger
    log = get_logger(__name__)

    try:
        risky_thing()
    except Exception:
        log.exception("weather API call failed")   # traceback + message save hota hai

Jarvis ke chat mein "kya galti ho rahi hai" jaisa sawaal aane par
`get_recent_errors()` / `summarize_recent_errors()` use hota hai (tools.py
mein `diagnose_errors` tool ke through) taaki Jarvis raw traceback na fenke,
balki simple bhasha mein bataye: "geo_weather() mein line 142 par timeout ho
raha hai kyunki API 5 second mein respond nahi kar rahi."
"""

import logging
import os
import re
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
LOG_FILE = os.path.join(LOG_DIR, "jarvis_errors.log")

os.makedirs(LOG_DIR, exist_ok=True)

_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"

_root_configured = False


def _configure_root():
    global _root_configured
    if _root_configured:
        return
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.setLevel(logging.INFO)

    root = logging.getLogger("jarvis")
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Terminal mein bhi dikhe (dev ke waqt useful), lekin file hi source of truth hai
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    console.setLevel(logging.WARNING)
    root.addHandler(console)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """Har module `get_logger(__name__)` call karke apna logger le."""
    _configure_root()
    return logging.getLogger(f"jarvis.{name}")


# ---------------------------------------------------------------------------
# Chat ke andar se errors "diagnose" karne ke liye (tools.py -> diagnose_errors)
# ---------------------------------------------------------------------------

_LOG_LINE_RE = re.compile(
    r"^(?P<time>\S+ \S+) \| (?P<level>\w+) \| (?P<logger>\S+) \| "
    r"(?P<func>\w+):(?P<line>\d+) \| (?P<msg>.*)$"
)


def get_recent_errors(limit: int = 10):
    """
    Log file se aakhri `limit` ERROR/WARNING entries nikaal ke structured
    list deta hai: [{time, level, module, function, line, message}, ...]
    Traceback ki extra lines bhi 'message' ke saath jud jaati hain.
    """
    if not os.path.exists(LOG_FILE):
        return []

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    entries = []
    current = None
    for line in lines:
        line = line.rstrip("\n")
        m = _LOG_LINE_RE.match(line)
        if m and m.group("level") in ("ERROR", "WARNING"):
            if current:
                entries.append(current)
            current = {
                "time": m.group("time"),
                "level": m.group("level"),
                "module": m.group("logger"),
                "function": m.group("func"),
                "line": m.group("line"),
                "message": m.group("msg"),
            }
        elif m:
            # naya valid log-line but INFO — pichla error entry complete ho gaya
            if current:
                entries.append(current)
            current = None
        elif current is not None:
            # traceback continuation line
            current["message"] += "\n" + line

    if current:
        entries.append(current)

    return entries[-limit:]


def summarize_recent_errors(limit: int = 5) -> str:
    """
    Chat mein dikhaane layak, saadi bhasha mein error summary banata hai.
    Raw traceback nahi dikhata — bas: kaunsi file/function, kya dikkat.
    """
    entries = get_recent_errors(limit=limit)
    if not entries:
        return "Abhi tak koi error log nahi hui — sab theek chal raha hai."

    lines = ["Yeh recent problems mili hain:\n"]
    for e in reversed(entries):
        first_line = e["message"].splitlines()[0]
        lines.append(
            f"- `{e['module']}` mein `{e['function']}()` (line {e['line']}) — {first_line}  [{e['time']}]"
        )
    return "\n".join(lines)
