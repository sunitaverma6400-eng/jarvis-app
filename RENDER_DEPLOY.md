# Jarvis ko Render.com par deploy karna

## Kya-kya badla gaya hai (Render-ready banane ke liye)
1. `server.py` — ab hardcoded port `5000` ki jagah Render ka `$PORT` env var use karta hai.
2. `memory.py` — API keys ab file (`memory/secrets.json`) ke saath-saath **environment variables**
   se bhi mil sakti hain (naam UPPERCASE mein: `GROQ`, `GEMINI`, `OPENROUTER`, waghera).
   Isse Render restart/redeploy karne par bhi keys delete nahi hongi.
3. `requirements.txt` — `gunicorn` (production server) aur `docx2txt` add kiya.
4. `Procfile` + `render.yaml` — Render ko batate hain app kaise start karni hai.
5. `.gitignore` — local `memory/` folder (jisme secrets/chats save hote hain) GitHub par push
   nahi hoga.

Baaki poora code (brain.py, tools.py, rag.py, waghera) waise ka waisa hai — koi function nahi
tootha, sirf hosting-compatibility ke liye zaroori cheezein add ki hain. Termux-specific tools
(call, SMS, battery, torch, location, vibrate) already try/except mein hain — Render par ye bas
"nahi ho paya" jaisa friendly error denge, server crash nahi karenge.

## Deploy steps

### 1. GitHub par push karo
```bash
cd jarvis_v6_advanced
git init
git add .
git commit -m "Jarvis v6 - Render ready"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

### 2. Render par service banao
1. https://render.com par (GitHub se) sign up/login karo
2. **New +** → **Web Service**
3. Apna GitHub repo select karo — Render `render.yaml` khud detect kar lega
   (agar detect na ho to manually: Build command = `pip install -r requirements.txt`,
   Start command = `gunicorn server:app --bind 0.0.0.0:$PORT`)
4. Plan: **Free** select karo

### 3. API keys add karo (zaroori step)
Render dashboard → apni service → **Environment** tab → **Add Environment Variable**:
```
GROQ        = gsk_xxxxxxxxxxxx
GEMINI      = AIzaxxxxxxxxxxxx
OPENROUTER  = sk-or-xxxxxxxxxx
```
(Chahe to chat mein "Jarvis code api: groq ..." bhi likh sakte ho, lekin wo restart par
delete ho sakti hai — Environment Variables permanent rehti hain.)

### 4. Deploy
Save karte hi Render build+deploy shuru kar dega (2-4 min lagte hain). Deploy hone ke baad
ek URL milega jaisे `https://jarvis-xxxx.onrender.com`.

## Free tier ki limits (yaad rakhna)
- 15 min inactivity par app **sleep** ho jata hai, agli request par 30-50 sec mein wake up hota hai
- Disk **ephemeral** hai — redeploy/restart hone par `memory/` folder (chats, todos, notes) reset
  ho sakta hai. API keys Environment Variables mein hone se safe rahengi, baaki data ke liye
  persistent disk chahiye hoga (paid feature)
- Agar 24x7 bina sleep chahiye, Render ka paid "Starter" plan chahiye hoga

## Phone-control tools (call, SMS, torch, battery, vibrate, notification, alarm, location)

Ye tools **sirf tabhi kaam karte hain jab kisi phone ka Termux:API access ho** — Render server
khud ek cloud data-center mein hai, uske paas SIM/battery/torch/GPS nahi hota. Isliye inhe
Render par kaam karwane ke liye **ek chhota "agent" apne phone par (Termux mein) chalana padega**:

1. Render dashboard → Environment → naya variable add karo:
   ```
   PHONE_AGENT_TOKEN = koi lambi random secret string (khud bana lo, jaise: jarvis-secret-8k2m9x)
   ```
2. Apne phone ke Termux mein (usi `jarvis_v6_advanced` folder ke andar):
   ```bash
   export JARVIS_SERVER_URL="https://your-app.onrender.com"
   export PHONE_AGENT_TOKEN="wahi-secret-jo-render-mein-daala"
   python phone_agent.py
   ```
3. Jab tak ye script chal rahi hai (Termux foreground mein khula), Render-hosted Jarvis
   call/SMS/torch/battery/vibrate/notification/alarm/location jaise commands isi phone par
   execute kar payega — jaise pehle direct Termux mode mein hota tha.

**Note:** `phone_agent.py` band hote hi (Termux close, phone offline, waghera) ye commands
"📵 Phone se connect nahi ho paya" jaisa friendly error denge — server crash nahi hoga.
Baaki saare tools (weather, search, calculator, todo, chat, waghera) is agent ke bina bhi
normally kaam karte rahenge, kyunki unhe phone hardware ki zaroorat nahi.
