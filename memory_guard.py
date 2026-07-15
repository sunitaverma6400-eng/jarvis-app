# -*- coding: utf-8 -*-
"""
Jarvis Memory Guard
--------------------
Render ka FREE plan sirf ~512MB RAM deta hai. Agar Jarvis process isse
zyada memory use kar le, Render poore container ko ABRUPTLY OOM-kill kar
deta hai — koi warning nahi, koi graceful shutdown nahi, seedha crash.
Kabhi-kabhi yeh ek "crash-loop" bhi bana deta hai (restart → memory phir
badhi → phir crash).

Fix: ek chhota background watchdog thread jo periodically apni process ki
memory (RSS) check karta hai aur do stage mein react karta hai:

  1. SOFT limit paar → sirf gc.collect() chalao (Python ka garbage
     collector aksar khud hi kaafi memory free kar deta hai — koi
     downtime nahi, user ko pata bhi nahi chalta).

  2. HARD limit paar → khud ko GRACEFULLY restart karo (apne aap ko
     SIGTERM bhejo). gunicorn ka master process isko normal graceful
     worker-exit samajhta hai aur turant ek naya worker spawn kar deta
     hai — restart milliseconds mein ho jaata hai. Yeh Render ke apne
     abrupt OOM-kill se HAMESHA behtar hai (jo poore container ko
     unpredictable tareeke se marta hai aur kabhi-kabhi 30-60s tak
     wapas nahi aata).

Koi extra pip dependency nahi — sirf Python stdlib (resource/gc/signal)
use hota hai, isliye Render build par koi extra weight nahi padta.

Env vars se tune kar sakte ho (defaults Render free 512MB ke liye safe hain):
  MEMORY_GUARD_SOFT_MB           (default 350)
  MEMORY_GUARD_HARD_MB           (default 440)
  MEMORY_GUARD_INTERVAL_SECONDS  (default 20)
  MEMORY_GUARD_DISABLE           ("1" set karo to poora guard band karne ke liye)
"""

import gc
import os
import threading
import time

from logger import get_logger
log = get_logger("memory_guard")

try:
    import resource  # POSIX-only (Linux/Render/Termux) — Windows par nahi hota
    import signal
    _SUPPORTED = True
except ImportError:
    _SUPPORTED = False

SOFT_MB = int(os.environ.get("MEMORY_GUARD_SOFT_MB", "350"))
HARD_MB = int(os.environ.get("MEMORY_GUARD_HARD_MB", "440"))
INTERVAL = int(os.environ.get("MEMORY_GUARD_INTERVAL_SECONDS", "20"))
DISABLED = os.environ.get("MEMORY_GUARD_DISABLE", "").strip() == "1"

_started = False
_lock = threading.Lock()
_last_restart_ts = 0.0
_MIN_RESTART_GAP = 60.0  # restart-loop se bachne ke liye — kam se kam 60s gap


def _current_rss_mb() -> float:
    """Process ki peak RSS memory MB mein deta hai (Linux par ru_maxrss KB mein hota hai)."""
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb / 1024.0


def _guard_loop():
    global _last_restart_ts
    log.info(f"[Memory Guard] Chalu — soft={SOFT_MB}MB hard={HARD_MB}MB, har {INTERVAL}s check hoga.")
    while True:
        time.sleep(INTERVAL)
        try:
            rss = _current_rss_mb()

            if rss >= HARD_MB:
                now = time.time()
                if now - _last_restart_ts < _MIN_RESTART_GAP:
                    log.warning(
                        f"[Memory Guard] {rss:.0f}MB HARD limit ({HARD_MB}MB) paar, "
                        f"lekin abhi thodi der pehle hi restart hua tha — skip kar rahe hain."
                    )
                    continue
                log.warning(
                    f"[Memory Guard] {rss:.0f}MB >= HARD limit {HARD_MB}MB — "
                    f"proactive GRACEFUL self-restart kar rahe hain (Render ke "
                    f"abrupt OOM-kill se bachne ke liye)."
                )
                _last_restart_ts = now
                os.kill(os.getpid(), signal.SIGTERM)
                return  # process ab shutdown ho raha hai, is thread ka kaam khatam

            if rss >= SOFT_MB:
                freed = gc.collect()
                log.info(f"[Memory Guard] {rss:.0f}MB soft limit ({SOFT_MB}MB) paar — gc.collect() chalaya ({freed} objects free).")

        except Exception as e:
            # Watchdog khud kabhi crash nahi hona chahiye
            log.info(f"[Memory Guard] check fail hua (ignore, agla try {INTERVAL}s baad): {e}")


def start_memory_guard():
    """
    Background daemon thread ek hi baar start karta hai. Multiple baar call
    karne par bhi (e.g. gunicorn reload) sirf ek hi thread chalega.
    Windows ya kisi non-POSIX environment mein (resource module na ho)
    chup-chaap skip ho jaata hai — Render/Termux dono Linux-based hain
    isliye production mein hamesha active rehta hai.
    """
    global _started
    with _lock:
        if _started:
            return
        if DISABLED:
            log.info("[Memory Guard] MEMORY_GUARD_DISABLE=1 set hai — guard skip kar rahe hain.")
            _started = True
            return
        if not _SUPPORTED:
            log.info("[Memory Guard] Is platform par 'resource' module available nahi — guard skip kar rahe hain.")
            _started = True
            return
        t = threading.Thread(target=_guard_loop, daemon=True)
        t.start()
        _started = True
