"""
Jarvis Phone Bridge
--------------------
Jab Jarvis cloud (Render) par chal raha ho, uske paas seedha phone hardware
(SIM, battery, torch, GPS, vibration motor) tak access nahi hota. Ye module
ek chhota "job queue" banata hai:

1. Cloud brain (tools.py ke andar) ek phone-command "submit" karta hai
   (e.g. "make_call", {"phone_number": "..."}) aur result ka wait karta hai.
2. Phone par chal raha `phone_agent.py` (Termux) har 2 second mein server ko
   poll karta hai — koi pending job ho to use utha ke, isi phone par asli
   Termux:API command chalata hai, aur result wapas bhejta hai.
3. Cloud brain ka wait khatam hota hai, result user ko mil jaata hai.

IMPORTANT: Ye in-memory queue hai — isiliye Render par gunicorn hamesha
`--workers 1` ke saath chalana zaroori hai (Procfile/render.yaml mein pehle
se set hai), warna alag worker processes ek doosre ka data nahi dekh payenge.
"""

import threading
import time
import uuid

_lock = threading.Lock()
_jobs = {}  # job_id -> {"tool": str, "args": dict, "event": Event, "result": any, "claimed": bool, "created": float}


def submit_job(tool_name: str, args: dict, timeout: int = 20):
    """
    Cloud brain (tools.py) yahan se call karta hai. Job queue mein daalta hai
    aur phone-agent ke result bhejne tak (ya timeout tak) wait karta hai.
    Returns: (result, error) — dono mein se ek hamesha None hoga.
    """
    job_id = uuid.uuid4().hex
    event = threading.Event()
    with _lock:
        _jobs[job_id] = {
            "tool": tool_name,
            "args": args,
            "event": event,
            "result": None,
            "claimed": False,
            "created": time.time(),
        }

    got_result = event.wait(timeout)

    with _lock:
        job = _jobs.pop(job_id, None)

    if not got_result or job is None:
        return None, "timeout"
    return job["result"], None


def get_pending_job():
    """
    Phone agent yahan se poll karta hai. Sabse purana unclaimed job deta hai,
    aur usse turant 'claimed' mark kar deta hai taaki dusra poll usi job ko
    dobara na utha le.
    """
    with _lock:
        oldest_id, oldest_job = None, None
        for job_id, job in _jobs.items():
            if job.get("claimed"):
                continue
            if oldest_job is None or job["created"] < oldest_job["created"]:
                oldest_id, oldest_job = job_id, job
        if oldest_id is None:
            return None
        oldest_job["claimed"] = True
        return {"id": oldest_id, "tool": oldest_job["tool"], "args": oldest_job["args"]}


def report_result(job_id: str, result):
    """Phone agent yahan se result post karta hai, jo waiting submit_job() ko wake karta hai."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return False
        job["result"] = result
        job["event"].set()
    return True


def agent_connected_recently(within_seconds: int = 30) -> bool:
    """
    Simple heuristic: agar pichle N second mein koi poll aaya hai to agent
    'online' maana jaata hai. (/api/phone/status endpoint ke liye)
    """
    with _lock:
        return _last_poll_ts[0] > 0 and (time.time() - _last_poll_ts[0]) < within_seconds


_last_poll_ts = [0.0]


def mark_agent_poll():
    _last_poll_ts[0] = time.time()
