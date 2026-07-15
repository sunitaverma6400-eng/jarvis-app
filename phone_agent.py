"""
Jarvis Phone Agent
-------------------
Ye script apne PHONE par (Termux ke andar) chalao — Jarvis chahe Render
(cloud) par host ho, ye script us cloud-Jarvis ko is phone ke hardware
(call, SMS, torch, battery, vibrate, notification, alarm, GPS location)
tak access deta hai.

Kaam kaise karta hai:
  1. Har 2 second mein Render server ko poll karta hai: "koi phone-command
     pending hai kya?"
  2. Job mile to (jaise "torch on karo") isi phone par Termux:API se
     asli command chalata hai (isi tools.py ki functions reuse karke).
  3. Result wapas Render ko bhej deta hai — jo user ko turant dikh jaata hai.

Zaroori setup (ek baar):
  1. Render dashboard → Environment → naya variable add karo:
       PHONE_AGENT_TOKEN = koi bhi lambi random secret string
     (jaise: openssl rand -hex 16 se bana sakte ho, ya bas kuch random type karo)
  2. Wahi exact value + apne Render app ka URL yahan neeche env vars mein set karo:

       export JARVIS_SERVER_URL="https://your-app.onrender.com"
       export PHONE_AGENT_TOKEN="wahi-secret-jo-render-mein-daala-tha"
       python phone_agent.py

  Chahe to permanent banane ke liye Termux ke ~/.bashrc mein bhi daal sakte ho.

Rokne ke liye: Ctrl+C
"""

import os
import sys
import time
import json
import urllib.request
import urllib.error

import tools  # yahi purani tools.py — is phone par Termux binaries milengi, isliye seedha kaam karega

SERVER_URL = os.environ.get("JARVIS_SERVER_URL", "").rstrip("/")
TOKEN = os.environ.get("PHONE_AGENT_TOKEN", "")
POLL_INTERVAL = 2  # seconds


def _request(path, method="GET", payload=None, timeout=15):
    url = f"{SERVER_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-Agent-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_job(job: dict):
    tool_name = job.get("tool", "")
    args = job.get("args", {}) or {}
    fn = getattr(tools, tool_name, None)
    if fn is None:
        return f"Agent error: unknown tool '{tool_name}'"
    try:
        return fn(**args)
    except Exception as e:
        return f"Agent error while running {tool_name}: {e}"


def main():
    if not SERVER_URL or not TOKEN:
        print("❌ JARVIS_SERVER_URL aur PHONE_AGENT_TOKEN dono environment variables set karo, phir dobara chalao.")
        print('   export JARVIS_SERVER_URL="https://your-app.onrender.com"')
        print('   export PHONE_AGENT_TOKEN="apna-secret"')
        sys.exit(1)

    print(f"🤖 Jarvis Phone Agent shuru — {SERVER_URL} se juda hoon.")
    print("   (Ctrl+C dabao band karne ke liye)\n")

    # Stream ended/disconnect hone par TTS se batayega (isi phone par chalta hai)
    tools.start_stream_monitor()

    consecutive_errors = 0
    while True:
        try:
            resp = _request("/api/phone/poll")
            consecutive_errors = 0
            job = resp.get("job")
            if job:
                print(f"📥 Job mila: {job['tool']}({job.get('args', {})})")
                result = run_job(job)
                _request("/api/phone/result", method="POST",
                          payload={"job_id": job["id"], "result": result})
                print(f"📤 Result bhej diya: {result}\n")
            else:
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n👋 Agent band ho raha hai. Alvida!")
            break
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            consecutive_errors += 1
            wait = min(POLL_INTERVAL * consecutive_errors, 30)
            print(f"⚠️ Server se connect nahi ho paya ({e}). {wait}s mein retry...")
            time.sleep(wait)
        except Exception as e:
            print(f"⚠️ Unexpected error: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
