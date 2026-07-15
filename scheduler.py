# -*- coding: utf-8 -*-
"""
Jarvis Scheduler (APScheduler)
-------------------------------
Background/recurring tasks ke liye. Celery ki tarah powerful nahi hai
(Celery ko Redis/broker chahiye — extra hosting cost), lekin APScheduler
same process ke andar chalta hai, zero extra setup, Render free-tier ke
liye perfect.

Kaise use karo (naya scheduled task add karna ho to):
    import scheduler

    def my_task():
        ...

    scheduler.add_job(my_task, "interval", minutes=30, job_id="my_task")

Ya specific time par roz:
    scheduler.add_job(my_task, "cron", hour=7, minute=0, job_id="daily_briefing")

server.py already do built-in jobs register karta hai:
    1. memory_cleanup   -> purani/khaali chats aur error-log ko trim karta hai
    2. log_rotation_check -> jarvis_errors.log zyada bada na ho jaaye, check karta hai
"""

import os
import time
import tempfile

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from logger import get_logger

log = get_logger("scheduler")

_scheduler = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True, timezone="Asia/Kolkata")
    return _scheduler


def add_job(func, trigger: str, job_id: str, replace_existing: bool = True, **trigger_args):
    """
    Ek job register karta hai.
    trigger: "interval" (minutes=/hours=/seconds=) ya "cron" (hour=/minute=/day_of_week=)
    Agar job crash ho jaaye, poora process nahi girta — sirf error log hoti hai
    (APScheduler khud isko handle karta hai, agla run scheduled rehta hai).
    """
    sched = get_scheduler()
    try:
        if trigger == "interval":
            trig = IntervalTrigger(**trigger_args)
        elif trigger == "cron":
            trig = CronTrigger(**trigger_args)
        else:
            raise ValueError(f"Unknown trigger type: {trigger}")

        sched.add_job(
            _safe_wrap(func),
            trig,
            id=job_id,
            replace_existing=replace_existing,
        )
        log.info(f"Scheduled job registered: {job_id} ({trigger} {trigger_args})")
    except Exception:
        log.exception(f"Failed to register scheduled job: {job_id}")


def _safe_wrap(func):
    """Har job ko wrap karta hai taaki ek job ka crash poore scheduler ko na girade."""
    def wrapped():
        try:
            func()
        except Exception:
            log.exception(f"Scheduled job '{func.__name__}' crashed during run")
    wrapped.__name__ = getattr(func, "__name__", "job")
    return wrapped


_started = False


def start():
    """Scheduler ko start karta hai — server.py se ek hi baar call hota hai."""
    global _started
    if _started:
        return
    sched = get_scheduler()
    sched.start()
    _started = True
    log.info("APScheduler started.")


def list_jobs():
    """Debug ke liye — abhi kaunse jobs scheduled hain."""
    sched = get_scheduler()
    return [
        {"id": j.id, "next_run": str(j.next_run_time), "trigger": str(j.trigger)}
        for j in sched.get_jobs()
    ]


# ---------------------------------------------------------------------------
# Built-in maintenance jobs
# ---------------------------------------------------------------------------

def register_default_jobs():
    """
    Jarvis ke apne maintenance jobs register karta hai. server.py startup
    ke waqt ek baar call karta hai.
    """
    import memory as _memory

    def memory_cleanup():
        """
        Purani, khaali (0-message) chats ko hata deta hai taaki chats_index.json
        aur disk clutter na ho. Actual meaningful chats kabhi nahi chhedta.
        """
        removed = 0
        for chat in _memory.list_chats():
            messages = _memory.load_chat(chat["id"])
            if not messages:
                _memory.delete_chat(chat["id"])
                removed += 1
        if removed:
            log.info(f"memory_cleanup: {removed} khaali chats hata di gayi.")

    def log_rotation_check():
        """jarvis_errors.log size check karta hai (RotatingFileHandler already
        isko handle karta hai, yeh sirf ek extra safety-net hai)."""
        from logger import LOG_FILE
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
            log.warning("jarvis_errors.log 5MB se bada ho gaya — check karo rotation sahi kaam kar raha hai.")

    def backup_cleanup():
        """
        Self-evolution engine ke purane backups (30+ din se purane) delete
        karta hai, disk space bachane ke liye. Kam se kam 10 sabse naye
        backups hamesha safe rakhe jaate hain — rollback option kabhi
        khatam nahi hota.
        """
        try:
            import self_evolve as _se
            removed = _se.cleanup_old_backups(keep_days=30, keep_min=10)
            if removed:
                log.info(f"backup_cleanup: {removed} purane backup(s) delete kiye.")
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    def memory_summarization():
        """
        Long-term memory summarization: har chat ke RAG store mein agar
        purani entries (60+) jama ho gayi hain, unme se sabse purani batch
        ko ek compact summary mein badal deta hai. Isse RAG store chhota
        rehta hai aur context bhi zinda rehta hai (purani baatein bhoolti
        nahi, sirf compress ho jaati hain).
        """
        try:
            import rag as _rag
            import brain as _brain
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")
            return

        try:
            chat_ids = _rag.get_all_chat_ids()
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")
            return

        summarized = 0
        for chat_id in chat_ids:
            if chat_id.startswith("__"):
                continue  # internal/system chats skip karo
            try:
                if _rag.summarize_old_turns(chat_id, _brain.summarize_text):
                    summarized += 1
            except Exception:
                log.exception("unexpected error - see memory/jarvis_errors.log")

        if summarized:
            log.info(f"memory_summarization: {summarized} chat(s) ki purani memory summarize hui.")

    def tts_cache_cleanup():
        """
        TTS cache (server.py ka /api/tts response cache) ko bahut bada
        hone se rokta hai — 7 din se purani cached MP3 files delete
        karta hai. Cache khud fresh ban jaayegi agli baar zaroorat pe.
        """
        try:
            cache_dir = os.path.join(tempfile.gettempdir(), "jarvis_tts_cache")
            if not os.path.isdir(cache_dir):
                return
            cutoff = time.time() - (7 * 24 * 3600)
            removed = 0
            for fname in os.listdir(cache_dir):
                fpath = os.path.join(cache_dir, fname)
                try:
                    if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        removed += 1
                except Exception:
                    continue
            if removed:
                log.info(f"tts_cache_cleanup: {removed} purani cached TTS file(s) hataayi.")
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    def check_page_watches():
        """
        User ke "jab X ho jaaye to batana" wale page-watches check karta
        hai (tools.watch_page se register hote hain). Yahi cheez Jarvis
        ko PURELY REACTIVE se ek kadam aage, PROACTIVE banati hai —
        trigger hone par phone par real notification jaati hai, bina
        user ke kuch poochhe. Render free-tier ko warm rakhne ke liye
        keepalive.py already zaroori hai — tabhi yeh job reliably chalta
        rahega.
        """
        try:
            import tools as _tools
            _tools._run_watch_checks()
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    def jarvis_initiative():
        """
        Jarvis ki 'khud faisle le' capability — periodically (background
        thread mein, user ko wait nahi karna padta) personality.py ka
        surprise-job trigger karta hai. Wahi decide karta hai ki kuch
        share karne layak hai ya nahi, aur sirf safe/read-only actions
        leta hai (kabhi bhi khud se code/file modify nahi karta).
        """
        try:
            import personality as _personality
            _personality.run_surprise_job()
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    def jarvis_code_review():
        """
        Jarvis ki 'khud faisle le, par khud apply na kare' capability —
        periodically apna khud ka code review karta hai aur real
        improvements sochta hai, lekin unhe sirf queue karta hai
        (self_evolve.queue_suggestion) — kabhi bhi khud se apply nahi
        karta. User agli baar chat kholega to Jarvis in suggestions ko
        naturally mention kar sakta hai, aur ek confirmation se saari
        apply ho sakti hain.
        """
        try:
            import self_evolve as _se
            _se.run_code_review_job()
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    add_job(memory_cleanup, "interval", job_id="memory_cleanup", hours=6)
    add_job(log_rotation_check, "interval", job_id="log_rotation_check", hours=1)
    add_job(memory_summarization, "interval", job_id="memory_summarization", hours=12)
    add_job(backup_cleanup, "interval", job_id="backup_cleanup", hours=24)
    add_job(tts_cache_cleanup, "interval", job_id="tts_cache_cleanup", hours=24)
    add_job(check_page_watches, "interval", job_id="check_page_watches", minutes=25)
    add_job(jarvis_initiative, "interval", job_id="jarvis_initiative", hours=8)
    add_job(jarvis_code_review, "interval", job_id="jarvis_code_review", hours=24)
