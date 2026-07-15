# -*- coding: utf-8 -*-
"""
Jarvis Keep-Alive Module
------------------------
Render ka FREE plan agar 15 minute tak koi external HTTP request na aaye to
service ko "sleep" kar deta hai. Agla request aane par usko phir se
"wake up" hone mein 30-60 second lag jaate hain — aur agar us waqt koi call
aa jaaye, MacroDroid ka /api/voice_chat request timeout ho sakta hai, matlab
AI call receive nahi kar payega.

Isko rokne ke liye ek background daemon thread chalu karte hain jo har
~10 minute (15 se kam, taaki inactivity-timer kabhi reset hi na ho) mein
apni khud ki public URL (/api/ping) ko HTTP GET request bhejta hai.
Yeh request Render ke load-balancer se hoke aata/jaata hai, isliye
"external activity" maani jaati hai aur service ko jagaaye rakhti hai.

Kaise pata chalta hai apni public URL?
- Render automatically RENDER_EXTERNAL_URL environment variable set karta
  hai (e.g. "https://jarvis-app-4-g1gu.onrender.com") har web-service ke liye.
  Isiliye kuch extra config karne ki zaroorat nahi — bas Render par deploy
  karo, yeh khud-ba-khud kaam karega.
- Agar RENDER_EXTERNAL_URL na mile (local testing, ya doosra host), to
  "SELF_PING_URL" env var se manually URL diya ja sakta hai. Agar dono na
  hon, to thread chup-chaap kuch nahi karta (local dev mein zaroorat nahi).
"""

import os
import threading
import time

import requests

PING_INTERVAL_SECONDS = int(os.environ.get("SELF_PING_INTERVAL_SECONDS", "600"))  # 10 min

_started = False
_lock = threading.Lock()


def _resolve_self_url() -> str:
    url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not url:
        url = os.environ.get("SELF_PING_URL", "").strip()
    return url.rstrip("/")


def _ping_loop(base_url: str):
    ping_url = f"{base_url}/api/ping"
    print(f"[Jarvis Keep-Alive] Chalu ho gaya — har {PING_INTERVAL_SECONDS}s mein '{ping_url}' ping hoga.")
    while True:
        time.sleep(PING_INTERVAL_SECONDS)
        try:
            resp = requests.get(ping_url, timeout=20)
            print(f"[Jarvis Keep-Alive] Ping OK ({resp.status_code})")
        except Exception as e:
            # Sirf log karo, thread ko kabhi crash mat hone do
            print(f"[Jarvis Keep-Alive] Ping fail hui (ignore, agla try {PING_INTERVAL_SECONDS}s baad): {e}")


def start_keepalive_thread():
    """
    Ek hi baar background daemon thread start karta hai. Multiple baar call
    karne par bhi (e.g. gunicorn reload) sirf ek hi thread chalega.
    """
    global _started
    with _lock:
        if _started:
            return
        base_url = _resolve_self_url()
        if not base_url:
            print("[Jarvis Keep-Alive] RENDER_EXTERNAL_URL/SELF_PING_URL nahi mila — "
                  "keep-alive skip kar rahe hain (local dev mein normal hai).")
            _started = True
            return
        t = threading.Thread(target=_ping_loop, args=(base_url,), daemon=True)
        t.start()
        _started = True
