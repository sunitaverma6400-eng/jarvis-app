# Jarvis v5 — Install Guide (Termux)

## Step 1: Basic packages
```bash
pkg update && pkg upgrade -y
pkg install python python-pip git ffmpeg termux-api -y
```

> ⚠️ **Zaroori**: Play Store / F-Droid se **"Termux:API"** app bhi alag se install karo (sirf `pkg install termux-api` package kaafi nahi hai — yeh companion app hi asli location/battery/SMS/call/torch/notification/vibrate control deta hai). Dono install karne ke baad Termux ko restart kar lo.
>
> Location kaam karne ke liye:
> 1. Phone ki Settings → Apps → Termux:API → Permissions → **Location "Allow"** karo (agar option ho to "Allow all the time" ya kam se kam "While using the app")
> 2. Phone ka GPS/Location **ON** hona chahiye
> 3. Jarvis server chalate waqt Termux app **foreground/open** rakho (background mein Android location block kar deta hai)

## Step 2: Python packages
```bash
pip install flask groq tinydb duckduckgo-search yt-dlp edge-tts PyPDF2 openpyxl --break-system-packages
```

## Step 3: RAG Memory
SQLite Python ke saath built-in hai — kuch install nahi karna! 🎉

## Step 4: Image Search (optional but recommended)
```bash
pip install icrawler --break-system-packages
```

## Step 5: Server chalao
```bash
cd jarvis
python server.py
```
Browser mein kholein: `http://localhost:5000`

## Step 6: API Keys save karo
Chat mein likho:
```
Jarvis code api: groq YOUR_GROQ_KEY
Jarvis code api: gemini YOUR_GEMINI_KEY
```

---
## v6.1 — Crash Fixes (Screenshots se mile bugs)

Aapke bheje 9 screenshots dekh ke ye sab fix kiya:

### 🐛 Critical Fixes:
1. **Mic/Chat "freeze" bug** — `core.classList.contains(...)` bina null-check ke call ho raha tha (jo element already page se hataya gaya tha). Har baar jab mic recording khatam hoti, ye crash hota tha. **Fix ho gaya.**
2. **Chat permanently blank/frozen** — `initChats()`, `loadChatMessages()`, `sendMessage()` mein ab har step try/catch mein hai. Agar history load fail ho, corrupt ho, ya server unreachable ho — composer **kabhi bhi permanently dead nahi hoga**, hamesha ek fresh chat ya clear error message milega.
3. **Server crash on YouTube stream (ndk-context/Rust panic)** — `/api/ytstream` ab yt-dlp extraction ko ek **alag subprocess** mein chalata hai. Agar wahi native crash phir bhi ho, sirf woh chhota helper process marega — **Jarvis ka main server kabhi nahi girega**.
4. **Auto-restart safety net** — naya `run.sh` script add kiya. Isse chalao (`./run.sh`) to agar server kisi bhi wajah se crash ho jaaye, 2 second mein khud restart ho jayega.
5. **Country Info "error: 0" bug** — jab desh nahi milta tha, code galat tarah se crash hota tha (`KeyError: 0`) jo sirf "0" dikhata tha. Ab clear message milega.
6. **QR Code "HTTP 400" on empty input** — ab empty text/URL par friendly error milega, raw HTTP error nahi.
7. **Memory (Yaad Rakho/Karo) — TinyDB na hone par bhi fully kaam karega** — pehle "Yaad Rakho" TinyDB na hone par ek confusing message ke saath save karta tha, lekin "Yaad Karo"/"Saari Yaadein" completely fail ho jaate the. Ab dono cases (TinyDB ho ya na ho) mein **remember/recall/list/forget sab consistently kaam karte hain** (JSON fallback store).
8. **Todo List** — same JSON-fallback treatment mili, TinyDB na hone par bhi add/list/complete/delete sab kaam karenge.
9. **Web Search resilience** — 3-layer fallback (ddgs library → HTML scrape → Tavily) aur behtar error diagnostics.

### 💡 Naya:
- `run.sh` — supervisor script, crash-resilient startup ke liye recommended.

### Recommended: ab is tarah chalao
```bash
chmod +x run.sh
./run.sh
```
Simple `python server.py` bhi chalega, bas crash hone par khud restart nahi hoga.


### 🆕 16 Naye Tools Add Kiye:
- **calculate** — safe offline calculator (koi eval() nahi, secure)
- **convert_units** — length/weight/volume/speed/data/temperature converter
- **convert_currency** — live exchange rate se currency convert (100+ currencies)
- **translate_text** — kisi bhi language mein translate
- **get_dictionary** — English word meaning + pronunciation + synonyms
- **get_wikipedia_summary** — kisi bhi topic ka Wikipedia summary
- **get_crypto_price** — live Bitcoin/Ethereum/etc price (USD+INR)
- **generate_qr** — text/URL ka QR code image
- **get_random_quote** — motivational quotes
- **generate_password** — cryptographically-secure password generator
- **text_analyzer** — word/char/sentence count + reading time
- **add_todo / list_todos / complete_todo / delete_todo** — poora todo list system (TinyDB)
- **system_info** — Termux/phone storage, battery, Python version
- **morning_briefing** — time+weather+news+quote ek hi jawab mein (smart composite tool)

### 🐛 Bug Fix:
- `get_country_info` aur `check_termux_compatibility` — ye tools code mein the lekin Groq ko kabhi advertise nahi kiye gaye the (isliye AI kabhi use hi nahi kar sakta tha). Ab dono TOOL_DEFINITIONS mein register hain.

### ✅ Quality:
- Har naya tool try/except mein wrapped hai — koi bhi internet/API failure crash nahi karega, hamesha readable Hinglish error message milega.
- Koi naya heavy dependency add nahi hua — sab stdlib ya already-installed libraries use karte hain. `requirements.txt` unchanged hai.
- `/tools` page (`templates/tools.html`) mein sab 16 naye tools ke liye UI cards bhi add kiye — AI se bina bhi seedha test kar sakte ho.
- TOOL_DEFINITIONS aur TOOL_FUNCTIONS dono ab 62-62 tools par exactly sync hain (verified).

### 📋 Roadmap (agle phases ke liye — bolo to karta hoon):
- Phase 2: Voice/TTS aur RAG memory ko aur smart banana
- Phase 3: Self-evolve engine ko aur safe/robust banana (auto-rollback on error)
- Phase 4: Automation (recurring reminders, smart notifications, context-aware suggestions)


### ✅ Fix kiya:
- Selenium / Chromium → **hata diya** (kaam nahi karta tha)
- 4chan / Civitai → **hata diya**
- Image search → DuckDuckGo + Wikimedia imageinfo API (proper URLs)
- YouTube → Invidious embed chain + yt-dlp stream
- Radio → 3 server fallback + HTTPS-only streams
- NASA APOD/Mars/ISS/Asteroids → improved APIs
- SpaceX → multi-API fallback (Launch Library 2)
- File upload → ZIP (content read), CSV/Excel (rows read), PDF (text extract)
- File download → AI ab asli PDF/DOCX/XLSX/ZIP generate kar sakta hai (text se), download button ke saath
- TinyDB → properly working

### 🆕 Naya:
- **Multi-LLM Orchestration**: Simple task → fast model, Complex → heavy model
- **RAG Memory (SQLite + TF-IDF)**: Purani baatein yaad rehti hain — koi heavy install nahi
- **AI Theme System**: 7 preset + AI se apna theme banao
- **Theme button** (🎨) topbar mein

### 🎨 Themes:
- Jarvis Default, Iron Man, Matrix, Deep Ocean, Purple Galaxy, Gold, Light Mode
- Ya chat mein "neon cyberpunk theme banao" jaisa likh do → AI bana dega

### 🧠 Orchestration Logic:
- "hi", "time", simple math → `llama-3.1-8b-instant` (fastest)
- Code, essay, analysis → `llama-3.3-70b-versatile` (smartest)
- Weather, images, tools → auto balanced model
