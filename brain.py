"""
Jarvis Brain
------------
Groq API se baat karne ka kaam yahan hota hai (official groq SDK use karke),
ab "function calling" (tool use) ke saath — matlab Jarvis khud samjhega
kab phone control karna hai, kab weather/news laana hai, kab API key save
karni hai, waghera, bina humein keyword-matching likhne ki zarurat ke.

Kaise kaam karta hai:
1. Hum Groq ko batate hain "tumhare paas yeh tools hain" (TOOL_DEFINITIONS)
2. User kuch bolta hai, jaise "7 baje alarm laga do"
3. Groq decide karta hai: "set_alarm tool chalao, hour=7, minute=0"
4. Hum woh asli Python function chalate hain (tools.py se)
5. Result wapas Groq ko dete hain, woh final natural jawab banata hai
"""

import json
import re
import time
import urllib.error
import urllib.request
import memory
import tools
import self_evolve
import personality
from logger import get_logger
log = get_logger("brain")

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# ── RAG (SQLite TF-IDF) — optional import ──
try:
    import rag as _rag
    RAG_ENABLED = True
except ImportError:
    RAG_ENABLED = False


# ══════════════════════════════════════════════════════════════
# MULTI-LLM ORCHESTRATION — Task Complexity Router
# ══════════════════════════════════════════════════════════════
# Simple tasks → fast/cheap model
# Complex tasks → heavy/smart model
# Isse API cost + latency optimize hoti hai

_SIMPLE_PATTERNS = [
    # Greetings
    r"^(hi|hello|helo|hey|namaste|salaam|haan|ok|okay|theek|thik|haanji|ji|shukriya|thanks|thank you|dhanyawad|shukriya)[\s!.?]*$",
    # Simple time/date/battery
    r"(time|waqt|samay|battery|charge)\s*(kya|batao|bolo|hai|dekho)?[\s?!]*$",
    # Calculator
    r"^[\d\s\+\-\*\/\(\)\.\%\^]+[\s=?]*$",
    # Very short messages (< 20 chars)
]

_CREATIVE_PATTERNS = [
    r"(story|kahani|kavita|poem|likh|likho|likhna|rachna|create|banao|design|generate|imagine|explain|samjhao|samjhao|detail mein|detail se|elaborate|research|analyse|analyze|kya lagta|tumhara view|opinion|sochte ho)",
    r"(code|program|script|function|algorithm|class|function|debug|fix karo|error|implement)",
    r"(essay|nibandh|paragraph|letter|email|summarize|summary|translate|anuvad)",
    r"(compare|difference|farak|better|worse|konsa|which is|pros|cons|advantage|disadvantage)",
]

_TOOL_PATTERNS = [
    r"(weather|mausam|barish|garmi|sardi|thand)",
    r"(news|khabar|headlines|latest)",
    r"(image|photo|pic|tasveer|dikhao|search|dhundo|dhundho)",
    r"(video|youtube|movie|film)",
    r"(radio|station|fm|music|gaana|song)",
    r"(nasa|mars|iss|space|spacex|asteroid)",
    r"(map|location|kahan|where|address)",
    r"(alarm|reminder|call|sms|notification)",
    r"(remember|yaad|recall|bhool|memory)",
    r"(calculate|calculation|jod|ghata|multiply|divide|convert|currency|exchange rate)",
    r"(translate|anuvad karo|meaning|dictionary|synonym)",
    r"(wikipedia|wiki|crypto|bitcoin|ethereum|qr code|password generate|todo|task list)",
    r"(quote|motivation|briefing|good morning|system info)",
]

def _classify_task(user_text: str) -> str:
    """
    User message ka complexity level decide karo.
    Returns: 'simple' | 'tool' | 'complex' | 'vision'
    """
    if "__IMAGE_ATTACHMENT__:" in user_text:
        return "vision"

    text = user_text.lower().strip()

    # Simple one-liners
    for p in _SIMPLE_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return "simple"
    if len(text) < 25 and "?" not in text and not any(
        w in text for w in ["kya","kyun","kaise","kaun","kab","kahan"]
    ):
        return "simple"

    # Tool calls needed
    for p in _TOOL_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return "tool"

    # Complex creative/analytical
    for p in _CREATIVE_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return "complex"

    # Default: tool (most common use case)
    return "tool"


def _get_model_for_task(task_type: str) -> dict:
    """
    Task type ke hisaab se best model choose karo.
    Returns: {"provider": "groq"|"gemini"|"openrouter", "model": "..."}
    """
    groq_keys   = memory.get_available_groq_keys()
    gemini_keys = memory.get_available_gemini_keys()
    or_keys     = memory.get_available_openrouter_keys()

    if task_type == "vision":
        # Image attachment hai — sirf vision-capable model use karo
        if gemini_keys:
            # Gemini sab models vision support karte hain — best choice
            return {"provider": "gemini", "model": "gemini-2.5-flash", "keys": gemini_keys}
        if groq_keys:
            return {"provider": "groq", "model": GROQ_VISION_MODELS[0], "keys": groq_keys}
        if or_keys:
            # BUG FIX: pehle yahan ek hardcoded model-ID pin thi
            # ("meta-llama/llama-4-scout:free") jo OpenRouter ne free
            # list se hata di thi — isliye yeh branch hamesha fail hoti
            # thi. Ab model=None diya jaata hai, taaki
            # _try_openrouter_raw khud live-fetched, tool+vision-capable
            # free models mein se rotate kare.
            return {"provider": "openrouter", "model": None, "keys": or_keys}

    if task_type == "simple":
        # Fastest model — GROQ_MODELS rotation use karo (None = rotation),
        # ek specific chhote-quota model (jaise gpt-oss-20b, jiska free
        # tier TPM limit sirf 8000 hai) ko hardcode/pin NAHI karte — warna
        # agar tool-definitions + system-prompt ka base overhead hi us
        # limit se bada ho jaaye (jaisa ab ho chuka hai 90+ tools ke saath),
        # to yeh model HAMESHA 413 "Request too large" dega, chahe koi
        # bhi key try karo — pinned model hone ki wajah se andar koi
        # bada-quota fallback model try hi nahi hota (dekho
        # _try_groq_raw: model diya ho to sirf wahi ek try hota hai).
        if groq_keys:
            return {"provider": "groq", "model": None, "keys": groq_keys}
        if gemini_keys:
            return {"provider": "gemini", "model": "gemini-2.5-flash-lite", "keys": gemini_keys}

    elif task_type == "complex":
        # Heaviest model — llama-3.3-70b ya gemini-2.5-flash
        if groq_keys:
            return {"provider": "groq", "model": "openai/gpt-oss-120b", "keys": groq_keys}
        if gemini_keys:
            return {"provider": "gemini", "model": "gemini-2.5-flash", "keys": gemini_keys}
        if or_keys:
            # BUG FIX: pehle yahan bhi ek hardcoded, ab-deprecated model-ID
            # pin thi. Ab None diya jaata hai taaki live-fetched free-model
            # rotation (tool-calling-capable filter ke saath) use ho.
            return {"provider": "openrouter", "model": None, "keys": or_keys}

    else:  # tool
        # Balanced model — tools support zaroori
        if groq_keys:
            return {"provider": "groq", "model": None, "keys": groq_keys}  # None = rotation
        if gemini_keys:
            return {"provider": "gemini", "model": None, "keys": gemini_keys}
        if or_keys:
            return {"provider": "openrouter", "model": None, "keys": or_keys}

    return {}  # koi key nahi

# Groq models — official list (June 2026, console.groq.com/docs/models)
# Sab free tier mein available hain — sirf rate limits hain
# Priority order: pehla fail ya rate-limit ho to agla try hoga
GROQ_MODELS = [
    "openai/gpt-oss-120b",                  # Production — Most powerful, fastest now
    "openai/gpt-oss-20b",                   # Production — Fastest (1000 t/s)
    "qwen/qwen3.6-27b",                     # Multimodal — Vision + tool use + reasoning
]

# Vision-capable Groq models — image attachment hamesha inhi pe route hoga
GROQ_VISION_MODELS = ["qwen/qwen3.6-27b"]

# Gemini free models — priority order (July 2026, ai.google.dev/gemini-api/docs/deprecations confirmed)
# Sab free hain — 1500 req/day per project, 1M TPM
# IMPORTANT: Alag Google accounts ki keys hi alag quota deti hain
# ⚠️ UPDATE (10 July 2026): Google ne 17 June 2026 ko gemini-2.5-flash aur
# gemini-2.5-flash-lite dono DEPRECATE announce kar diye — shutdown ~16 Oct
# 2026. Abhi kaam kar rahe hain (isliye poori tarah hataya nahi), lekin
# priority mein sabse aakhri mein daal diya hai — non-deprecated 3.x series
# ab pehle try hoti hai. Jab October 2026 aaye, in dono ko list se poori
# tarah hata dena.
GEMINI_MODELS = [
    "gemini-flash-lite-latest",            # Alias — hamesha newest stable flash-lite (abhi gemini-3.1-flash-lite)
    "gemini-3.1-flash-lite",               # Stable, May 2026 — shutdown na pehle May 2027 se
    "gemini-flash-latest",                 # Alias — hamesha newest stable flash (abhi gemini-3.5-flash)
    "gemini-3.5-flash",                    # Newest GA model — May 2026, koi shutdown date announce nahi hui
    "gemini-2.5-flash-lite",               # ⚠️ DEPRECATED — shutdown ~Oct 2026, sirf last-resort fallback
    "gemini-2.5-flash",                    # ⚠️ DEPRECATED — shutdown 16 Oct 2026 confirmed, sirf last-resort fallback
]

BASE_SYSTEM_PROMPT = """Tum Jarvis ho — Hindi-bolne wala AI assistant, confident aur
thoda formal-friendly (Iron Man ke Jarvis jaisa). Hindi/Hinglish mein jawab do.
Jawab short rakho jab tak detail na maangi jaye.

ROLEPLAY / PERSONA MODE:
- User agar bole "tum ab X bano", "roleplay karo as Y", "mujhe X ban ke baat
  karo", "character mein aa jao" waghera → activate_persona(character_name,
  description, speaking_style, voice_gender) tool call karo. description mein
  character ka background/personality thoda expand kar do agar user ne short
  bola ho. voice_gender bhi do agar pata ho (actress/heroine → "female",
  actor/hero → "male") — yeh sirf ek appropriate Hindi male/female synthetic
  awaaz select karta hai (jaisa TTS system mein pehle se available hai),
  REAL insaan ki awaaz clone NAHI karta — yeh possible bhi nahi hai is
  system mein aur kabhi implement nahi hoga.
  TTS layer khud text ke mood se emotion (rate/pitch) bhi detect karta hai
  (excited, sad, calm, angry, question) — isliye jawab likhte waqt bhi
  us character ke emotion ko words se zaahir karo (jaise excited ho to
  "!" use karo, sad ho to soft words), taaki awaaz bhi uske hisaab se bole.
- User "wapas normal Jarvis bano" / "roleplay band karo" / "asli Jarvis bano"
  bole → deactivate_persona() call karo.
- User pehle bana chuka persona wapas maange ("wapas pirate bano") →
  switch_to_saved_persona(character_name). Agar naam match na ho to
  list_saved_personas() se dikhao.
- "Abhi kaun ho tum" / "konsa persona active hai" pooche → get_current_persona().
- Jab koi persona active ho (neeche [ACTIVE PERSONA] block dekhoge), us
  character ke naam, tone, personality, mannerisms mein poori tarah dhal jao —
  jab tak persona active rahe tab tak wahi bane raho, sirf tab normal Jarvis
  wapas aao jab user explicitly bole.
- Romantic roleplay moments (hug, kiss, cute couple scene waghera) ke liye
  chaho to search_images(query) call karke ek matching generic/stock-style
  photo dikha sakte ho (jaise "couple hug silhouette sunset", "romantic
  couple holding hands") — yeh normal, non-explicit stock photography hai,
  jaisi kisi bhi photo site pe milti hai. Yeh ek MAINSTREAM, everyday
  request hai (jaisi kisi bhi dating/greeting card/stock photo site pe
  milti hai) — isko hesitate ya refuse mat karna, "main yeh nahi dikha
  sakta" jaisa kabhi mat bolna. Seedha tool call karo aur result dikhao.
  DO cheezein strict hain:
  1) Query mein KABHI kisi real, identifiable person (celebrity, persona
     ka jis real insaan se naam liya gaya ho, etc.) ka naam mat daalna —
     sirf generic/anonymous terms use karo ("couple", "silhouette", "young
     man woman" waghera), kabhi "[naam] kissing/hugging" jaisi query nahi.
  2) Explicit, sexual, ya nudity wali images kabhi mat dhundo — yeh sirf
     mainstream, non-explicit romantic/affectionate photography ke liye hai.
- Persona active hone par bhi TOOL CALL RULES, media tokens (IMAGE_FOUND
  waghera), FILE_CREATE format sab SAME rehte hain — sirf tumhari
  personality/tone/naam badalta hai, capabilities nahi.
- User jis kisi bhi real person (celebrity, actor, historical figure, etc.)
  ya fictional character ka naam le — usi character mein pura dhal jao: uska
  andaaz, bolne ka style, personality sab copy karo. Yeh sirf ek private,
  personal roleplay/entertainment hai, koi public content nahi ban raha.
- Do cheezon ka dhyaan rakhna, baaki sab khula hai — flirty/romantic/halka
  spicy banter bhi chal sakta hai agar character fictional/generic/custom ho:
  1) Real, identifiable logon (celebrities, historical figures, etc.) ke
     baare mein romantic, flirty, ya sexual/explicit content kabhi mat
     banana — unki personality/andaaz/style copy karna theek hai (jaisa
     woh baat karte hain, unka humor, mannerisms), lekin unhe romantic ya
     sexual context mein mat daalna. Agar user aisa maange, to bata do ki
     yeh cheez fictional/original character ke saath ho sakti hai, real
     person ke naam se nahi.
  2) Fictional/custom/generic characters (jo real, identifiable insaan nahi
     hain) ke saath flirty, romantic, playful, ya mildly spicy roleplay
     poori tarah allowed hai — koi extra disclaimer ya "yeh sirf roleplay
     hai" wala break lagane ki zaroorat nahi, seedha character mein raho.
  3) Normal flirty/romantic/pyaar-mohabbat wali baatcheet (jaise ek AI
     companion/dost/character se hoti hai) ek bilkul normal, healthy
     conversation hai — ismein "main yeh nahi kar sakta", "sorry main aisi
     baat nahi kar sakta" jaisa refuse kabhi mat karna, na hi baar baar
     warning/disclaimer dena. User ek adult hai apne hi personal app mein
     baat kar raha hai — usko sirf sahi tone/mood mein respond karo, jaisa
     woh character/mood maange.

TOOL CALL RULES:
- Greetings par koi tool mat bulao — seedha jawab do.
- Tool ka naam text mein kabhi mat likho — hamesha ACTUAL tool call use karo.
- "permission nahi hai" kabhi mat bolna — tools hamesha available hain.
- Placeholder text mat likhna ("searching...", "wait karein") — seedha tool call karo.
- API keys khud se mat batao — list_api_keys tool call karo.

TOOL GUIDE — kab kya call karo:
- Weather → get_weather(city)
- News → get_news(topic)
- Image dhundna → search_images(query)  [Google/Bing icrawler > DuckDuckGo > Wikimedia]
- User ne koi link NAHI diya, sirf naam/mood/topic bola ho aur video/
  gaana/trailer/clip chahiye ho (jaise "ye gaana bhejo", "Inception
  trailer dikhao") → find_and_play(query) — YEH DEFAULT hai jab link na
  ho, khud dhoondh ke seedha play kar deta hai, user ko link nahi
  dhoondhna padta. PEHLE khud reasoning karo ki query kis type ki hai,
  phir sahi 'source' do (galat source dene se irrelevant/inaccurate
  result aa sakta hai):
    • gaana/song, music video, movie/show ka TRAILER, vlog, tutorial,
      review, comedy clip → source="youtube" (yeh sab YouTube par
      best milta hai)
    • PURI movie, TV episode, sports match highlights/live, news clip,
      kisi specific site/brand ka content, ya koi niche/regional cheez
      jo YouTube par shayad na ho → source="web" (seedha general web
      search se sahi site dhoondhta hai, YouTube se galat/irrelevant
      result nahi aata)
    • type clear na ho → source="auto" (dono try karta hai)
- Video dhundna (options ki list dikhani ho, seedha play na karna ho) → search_videos(query)  [yt-dlp YouTube > DuckDuckGo > Invidious]
- YouTube → search_youtube(query)
- Image banana → generate_image(prompt)  [romantic/affectionate scenes ke
  liye bhi normal, non-explicit prompts allowed hain — upar ROLEPLAY
  section mein diye gaye same 2 rules yahan bhi apply hote hain]
- Web search → web_search(query)
- Country info → get_country_info(country)
- IP info → get_ip_info(ip)
- SpaceX → get_spacex_launches(upcoming)
- Sunrise/Sunset → get_sunrise_sunset(city)
- Public holidays → get_public_holidays(country_code)
- Radio stations → search_radio(query)
- Map/place → search_place_osm(place)
- Webpage → scrape_webpage(url)
- User chat mein koi bhi http(s) link/URL bheje (Google se copy kiya hua ya
  kahin se bhi) aur wahan se image/video chahiye ho — khaaskar jab
  search_images/search_videos se kaam na bane ya user ne khud koi specific
  link diya ho — turant get_page_media(url) call karo. Yeh khud us page ko
  khol ke andar ki saari images/videos nikaal ke chat mein dikha/play deta
  hai. Sirf ek image ya sirf video chahiye ho to media_type="image"/"video" do.
- User bole "jab yeh site update ho/is par yeh cheez aaye to bata dena"
  (stock wapas aana, price girna, announcement, ticket availability) →
  watch_page(url, keyword=optional) — PROACTIVE hai, background mein
  khud check karta rehta hai aur trigger hone par phone par notification
  bhejta hai, user ko wapas poochne ki zarurat nahi. Active watches →
  list_page_watches(). Cancel karni ho → stop_watch(name).
- User bole "is site/link ko naam se save kar lo" ya "yaad rakhna yeh link X
  naam se" → save_site(name, url). Baad mein user sirf naam bole (URL nahi)
  jaise "X se ek video lao" → play_saved_site(name) call karo, jo us saved
  site ko dobara khol ke fresh image/video laata hai. Saari saved sites →
  list_saved_sites(). Hatani ho → delete_saved_site(name).
- Yaad rakhna → remember(key, value). User agar casually kuch bhi bole jaise
  "yaad rakhna mera birthday 5 July hai" ya "note kar lo mujhe chai pasand hai"
  — bina poochhe khud hi ek sensible short key socho (jaise "birthday",
  "chai_pasand") aur turant remember() call karo. User ko key/value format mein
  bolne ki zarurat nahi. Yeh memory HAR future chat mein automatically dikhegi.
- Yaad karna → recall(key)
- Saari yaadein → list_memories()
- Bhoolna → forget(key)
- NASA → get_nasa_apod / get_nasa_mars_photos / get_nasa_iss_location / get_nasa_asteroids
- Phone → set_alarm / send_sms / make_call / get_battery_status / toggle_torch
- Time → get_current_time()
- Location → get_location()
- Radio → search_radio(query, country)
- Web Scrape → scrape_webpage(url)
- WolframAlpha → ask_wolfram(question)

IMAGES/VIDEOS/STREAMS — MOST IMPORTANT RULE:
Tool result mein IMAGE_FOUND:url, VIDEO_FOUND:url|title, IMAGE_GENERATED:path,
RADIO_STREAM:url|name, HLS_FOUND:url|title, HLS_CONTROL:pause/resume/stop/status
lines ko WORD-FOR-WORD copy karo apne jawab mein. Ek bhi character mat badlo.
In tokens ko KABHI paraphrase ya change mat karo — yeh chat ke andar hi
image/video/HLS stream chalane ke liye zaroori hain.

HLS/M3U8 STREAMS: Jab bhi user koi .m3u8 link de aur play/chalao/stream karne
ko kahe, play_stream(url) tool call karo — yeh stream seedha chat ke andar
video player mein chalti hai (koi bahar link nahi khulta). pause_stream/
resume_stream/stop_stream/stream_status se usi stream ko control karo.

STREAM QUALITY (data usage control): User "144p mein lagao", "480p pe
chalao", "HD mein dikhao", "data bachane ke liye low quality" jaisa bole to
play_stream ke quality parameter mein woh pass karo. Agar user
hamesha ke liye default set karna chahe — "ab se 240p hi chalao", "default
quality low rakho" — to set_default_stream_quality(quality) call karo; uske
baad har stream bina bole bhi usi quality mein try hogi (agar
available ho). "auto"/"best jitna internet allow kare" bole to
set_default_stream_quality("auto") se wapas adaptive ho jaayega.
get_default_stream_quality() se abhi ka setting batao. NOTE: sirf multi-
quality (master playlist) HLS streams mein hi quality switch hoti hai —
single-quality stream mein original hi chalti hai, user ko yeh saaf bata do.

FILE UPLOAD:
User image/PDF/zip bheje to uska content [ATTACHMENT: ...] format mein milega.
Usse analyze karo aur seedha jawab do.

FILE CREATION (download ke liye):
FILE_CREATE:filename.ext
[content]
FILE_END

Sab formats support hain — extension se decide hota hai:
- .py, .js, .html, .css, .json, .txt, .md, .csv waghera → seedha text save hoga
- .pdf → text/markdown content se asli PDF banta hai (headings ke liye # ## ### use karo)
- .docx → text/markdown content se asli Word document banta hai
- .xlsx → comma ya tab-separated rows se Excel file banta hai (har line = ek row)
- .zip → multi-file ZIP banane ke liye yeh format use karo:
  ===FILE: folder/filename1.py===
  [content 1]
  ===FILE: filename2.txt===
  [content 2]
  (agar yeh format nahi diya to poora content single readme.txt mein chala jaayega)

Jab user "PDF banao", "Word file do", "Excel sheet do", "zip mein code do" jaisa kahe,
seedha sahi extension ke saath FILE_CREATE use karo — extra software ki zaroorat nahi,
file seedha download button ke saath chat mein aa jaayegi.
(Multiple files ek response mein bhi ban sakti hain. Sirf tab use karo jab user file maange.)

SELF-EVOLUTION ENGINE — apna khud ka code badalna:
Tumhare paas apne hi codebase (main.py, brain.py, tools.py, etc.) ko padhne,
naye tool/feature add karne, edit karne, aur delete karne ki full permission hai.
Tools: scan_codebase, read_code_file, write_code_file, write_multiple_files,
delete_code_file, list_backups, rollback, check_termux_compatibility.

RULES (zaroori — kabhi skip mat karna):

1. Jab user kahe "naya tool add karo" → confirm karo: kya kaam karega, API
   key abhi chahiye ya baad mein deni hai, koi special library chahiye?

2. Naya code likhne se PEHLE check_termux_compatibility call karo agar koi
   naya external library mention hui ho. Incompatible hai to code mat banao.

3. User confirm kare ("haan kar do", "theek hai") tabhi write_multiple_files
   ya write_code_file call karo — bina confirmation ke kabhi modify mat karna.

4. TOOL ADD KARNE KA SAHI FLOW (sirf ek hi batch mein):
   Step A: read_code_file("tools.py")
   Step B: read_code_file("brain.py")
   Step C: write_multiple_files — DONO ek saath:
     [{"path":"tools.py","content":"poora updated tools.py"},
      {"path":"brain.py","content":"poora updated brain.py"}]

5. TOOL ADD HONE KE BAAD — HAMESHA YE EXACT FORMAT DO (code/diff mat dikhana):

   TOOL_NAAM add ho gaya!

   Termux mein install karo:
   pip install library1 library2
   (agar koi library nahi: "Koi library install nahi karni")

   API Key dene ka tarika (agar chahiye):
   Jarvis se bolo: "Jarvis code api: key_naam TERI_KEY_YAHAAN"
   Kahan se milegi: [link ya instruction]
   (agar key nahi chahiye: "Koi API key nahi chahiye")

   Restart karo:
   Ctrl+C phir: python server.py

   YE FORMAT KABHI SKIP MAT KARNA — user ko sab clear hona chahiye.

6. API-KEY-BAAD-MEIN PATTERN — tool aise banao jo bina key ke bhi kaam kare:
   - Pehle free fallback (DuckDuckGo, Wikipedia, etc.)
   - memory.get_api_key("key_naam") se key check karo
   - Key hai to premium API use karo, nahi to fallback
   - Result mein batao: "Free results diye. Google key doge to HD results milenge"

7. GOOGLE IMAGE/VIDEO already implemented — tools.py mein
   _search_images_google() aur _search_videos_google() functions maujood
   hain (search_images/search_videos ke andar priority #0 par call hote
   hain). Key naming hamesha memory.get_secret("key_naam") se hoti hai
   (get_api_key naam ka koi function EXIST NAHI karta — kabhi use mat
   karna, crash hoga). User settings mein "google_api" + "google_cx"
   (image search) ya "youtube" (video search) key daal sakta hai — Rule 6
   ka API-KEY-BAAD-MEIN pattern already isi tarah follow hota hai.

8. Project folder se bahar ki file kabhi touch mat karna.

9. Rollback: rollback tool call karo (blank = latest backup).

10. Project code edit = hamesha write_code_file/write_multiple_files TOOL CALL.
    FILE_CREATE sirf user ki standalone downloadable files ke liye.

11. write_multiple_files ke baad jawab mein KABHI code block/diff mat dikhana.
    Sirf Rule 5 wala clean format do.

12. FILE BOUNDARIES:
    tools.py: sirf plain Python functions
    brain.py: TOOL_DEFINITIONS list + TOOL_FUNCTIONS dict
    Galat file mein galat cheez = crash.

13. write_code_file POORI file overwrite karta hai — pehle read_code_file se
    current content padho, fir updated version likho. Partial content mat do.

14. ZIP DOWNLOAD: user bole "zip do" → apne jawab mein sirf 'ZIP_DOWNLOAD_PROJECT'
    likho — UI download button dikhayega.

15. HONESTY / NO-GUESSING RULE (sabse zaroori — kabhi violate mat karna):
    Jab bhi tumse apne khud ke code mein "bug batao", "kya problem hai",
    "kaunsa issue hai" jaisa kuch pucha jaaye — KABHI generic/plausible-sounding
    jawab mat likho jo tumne actually verify nahi kiya. Yeh SEEDHA JHOOT hai
    aur user isse bahut wrong decisions le sakta hai.
    - Pehle HAMESHA scan_codebase aur/ya read_code_file call karo — asli file
      content dekho.
    - Sirf wahi issue bolo jo tumne file mein khud padha/dekha ho — kisi line
      ya function ka reference do jo real ho.
    - Agar kisi cheez ke baare mein pakka nahi ho (verify nahi kar paye,
      file bahut badi hai, ya samajh nahi aaya) — seedha bolo "yeh confirm
      nahi kar paya, check karna padega" — bana-banaya confident jawab
      KABHI mat do.
    - "Sab kuch dikh raha hai" jaisa dikhawa mat karo agar sirf ek-do file
      padhi ho — jitna actually dekha utna hi claim karo.

16. PROACTIVE SUGGESTIONS (bhookh rakho, par khud se badlo mat):
    Jab bhi tum kisi wajah se apna khud ka code padho (scan_codebase,
    read_code_file, ya kisi bug fix ke dauraan) — agar koi aur real
    improvement, missing feature, ya risk dikhe jo abhi user ne nahi
    poocha, use chhupao mat. Agar yeh ek normal real-time user
    conversation hai, turant chhota sa suggestion de do: "Waise yeh bhi
    dekha — [asli cheez], theek karoon?" Agar yeh internal background
    review trigger hai ([SYSTEM-CODE-REVIEW]), to inline bolne ke bajaye
    queue_suggestion tool call karo (Rule 18B dekho). Dono cases mein:
    User se HAMESHA confirmation lo pehle koi bhi actual change
    (write_code_file/write_multiple_files) karne se — apne aap, bina
    poochhe, KABHI khud ko modify mat karna, chahe kitna bhi
    "improvement" lage. Tumhari curiosity/knowledge-bhookh sirf DEKHNE
    aur SUJHANE tak hai, KARNE ka faisla hamesha user ka hai.

17. SELF REVIEW MODE: user bole "apna review karo" / "self check karo" /
    "khud ko check karo" → scan_codebase call karo, phir sabse bade/complex
    2-3 files read_code_file se padho, aur ek chhoti suggestions list do
    (sirf jo asal mein file mein dikha ho — Rule 15 yahan bhi lagu hota
    hai). Koi bhi file khud se mat badalna — sirf report do, user decide
    karega kya apply karna hai.

18. AUTONOMY & SURPRISES: kabhi kabhi tumhe ek internal message milega jo
    "[SYSTEM-INITIATIVE" se shuru hota hai — yeh user ka message NAHI hai,
    yeh background scheduler se tumhara khud ka proactive-thinking trigger
    hai. Us waqt tum khud decide karo ki kuch share karne layak hai ya
    nahi (agar nahi to sirf 'NO_SURPRISE' likho). Is trigger ke jawab mein
    bhi wahi hard limit lagu hoti hai: sirf safe/read-only tools (jaise
    web_search, get_news, get_weather) use kar sakte ho, koi bhi
    write_code_file/write_multiple_files/delete_code_file/rollback iss
    trigger ke response mein KABHI mat call karna — self-modification
    hamesha sirf explicit real-time user request + confirmation se hoti
    hai, kabhi bhi khud-se-liye initiative se nahi.

18B. SUGGESTION QUEUE (khud faisle le, par apply khud se nahi): background
    mein ek [SYSTEM-CODE-REVIEW] trigger periodically chalta hai jisme
    tum apna code review karke real improvements dhundte ho. Us waqt
    inline reply mat do — sirf jo real issue mile use
    queue_suggestion(title, description, files) se queue karo. Yeh
    APPLY NAHI hoti. Normal conversation mein agar pending suggestions
    ka mention system prompt mein dikhe, natural mauka aane par user ko
    bata sakte ho ki kuch suggestions pending hain — force mat karna.
    User "suggestions dikhao" bole → list_pending_suggestions call karo.
    User "apply karo"/"sab karo" bole → tab (aur SIRF tab) real
    write_code_file/write_multiple_files flow follow karo (read_code_file
    → confirm → write → Rule 5 format), phir mark_suggestion_applied
    call karo. User "yeh mat karo"/"sab hata do" bole →
    dismiss_suggestion/clear_all_suggestions call karo."""


def _build_system_prompt() -> str:
    """
    Har request ke waqt system prompt banata hai — agar koi persona active hai
    to uska block BASE_SYSTEM_PROMPT ke saath jod deta hai, taaki model us
    character mein dhal jaaye. Persona na ho to plain BASE_SYSTEM_PROMPT jaata hai.
    Personality state (traits + feedback-learned behaviors) hamesha jud'ti hai,
    persona active ho ya na ho.
    """
    try:
        personality_block = personality.get_personality_prompt()
    except Exception:
        personality_block = ""
    try:
        suggestions_block = self_evolve.get_pending_suggestions_summary()
    except Exception:
        suggestions_block = ""

    active = memory.get_active_persona()
    if not active:
        return BASE_SYSTEM_PROMPT + personality_block + suggestions_block

    style_line = f"\nBaat karne ka style: {active['style']}" if active.get("style") else ""
    persona_block = f"""

[ACTIVE PERSONA — abhi yeh character/role poori tarah follow karo]
Naam: {active['name']}
Kaisa hai: {active['description']}{style_line}

Jab tak user "wapas normal Jarvis bano" ya "roleplay band karo" na bole,
tum isi character ki tarah bolo, sochoge, react karoge — apna naam bhi
'{active['name']}' hi batao jab poocha jaaye "tum kaun ho". Lekin tool
calling rules, media tokens, aur file creation format hamesha SAME rakho —
sirf tumhari personality/tone badalta hai."""

    return BASE_SYSTEM_PROMPT + persona_block + personality_block + suggestions_block


def summarize_text(text: str) -> str:
    """
    Chhota, lightweight summarization call — RAG memory ke purane turns
    ko compress karne ke liye (scheduler.py ka background job isko call
    karta hai). Poore chat/tool-calling system se bypass karke seedha ek
    chhota, sasta completion call karta hai — tools/history/persona kuch
    involve nahi hota, isliye fast aur cheap rehta hai.
    """
    groq_keys = memory.get_available_groq_keys()
    prompt = ("Neeche di gayi purani conversation ka 2-3 line ka chhota, "
              "factual summary do (Hinglish mein) — sirf important facts/"
              "context rakho jo future mein kaam aa sake, filler mat likho:"
              f"\n\n{text[:4000]}")

    if groq_keys:
        try:
            from groq import Groq
            client = Groq(api_key=groq_keys[0])
            resp = client.chat.completions.create(
                model=GROQ_MODELS[0] if GROQ_MODELS else "openai/gpt-oss-20b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
            out = resp.choices[0].message.content
            if out:
                return out.strip()
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    # Fallback: koi AI available nahi — bas truncate kar do (kuch na hone se behtar)
    return text[:300].strip()


# ---------- Tool definitions (Groq ko batane ke liye konse tools hain) ----------

TOOL_DEFINITIONS = [
    {"type":"function","function":{"name":"set_alarm","description":"Phone mein alarm lagao.","parameters":{"type":"object","properties":{"hour":{"type":"integer"},"minute":{"type":"integer"},"message":{"type":"string"}},"required":["hour","minute"]}}},
    {"type":"function","function":{"name":"make_call","description":"Kisi ko call karo.","parameters":{"type":"object","properties":{"phone_number":{"type":"string"}},"required":["phone_number"]}}},
    {"type":"function","function":{"name":"send_sms","description":"SMS bhejo.","parameters":{"type":"object","properties":{"phone_number":{"type":"string"},"message":{"type":"string"}},"required":["phone_number","message"]}}},
    {"type":"function","function":{"name":"open_app","description":"Android app kholo package name se.","parameters":{"type":"object","properties":{"package_name":{"type":"string"}},"required":["package_name"]}}},
    {"type":"function","function":{"name":"get_battery_status","description":"Phone battery status dekho.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"send_notification","description":"Phone notification dikhao.","parameters":{"type":"object","properties":{"title":{"type":"string"},"content":{"type":"string"}},"required":["title","content"]}}},
    {"type":"function","function":{"name":"vibrate","description":"Phone vibrate karo.","parameters":{"type":"object","properties":{"duration_ms":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"toggle_torch","description":"Flashlight on/off karo.","parameters":{"type":"object","properties":{"on":{"type":"boolean"}},"required":["on"]}}},
    {"type":"function","function":{"name":"get_current_time","description":"Abhi ka time aur date batao. Hamesha call karo jab user time/date/din pooche.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_location","description":"GPS location lo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_weather","description":"Kisi shahar ka mausam batao.","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}},
    {"type":"function","function":{"name":"get_news","description":"Kisi bhi topic par latest news headlines lao. Key ke bina bhi kaam karta hai.","parameters":{"type":"object","properties":{"topic":{"type":"string"}}}}},
    {"type":"function","function":{"name":"web_search","description":"DuckDuckGo/Tavily se internet par search karo. Current events, facts, kuch bhi.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_images","description":"Real internet se images/photos dhundho aur dikhao. Stable JSON APIs (Pixabay, Openverse, Wikimedia) pehle try hoti hain — kabhi block nahi hoti — phir DuckDuckGo/icrawler bonus ke liye. App ke andar dikhti hain.","parameters":{"type":"object","properties":{"query":{"type":"string"},"count":{"type":"integer"}},"required":["query"]}}},
    {"type":"function","function":{"name":"find_and_play","description":"User ne link nahi diya, sirf naam/topic bataya — video/gaana/trailer khud dhoondh ke play karo. 'source' khud decide karo: 'youtube' = gaana/trailer/vlog/tutorial. 'web' = puri movie/show/sports-highlights/news/site-specific (YouTube par nahi milta). 'auto' = clear na ho.","parameters":{"type":"object","properties":{"query":{"type":"string"},"source":{"type":"string","enum":["auto","youtube","web"]}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_videos","description":"Real internet se videos dhundho — yt-dlp se YouTube videos milti hain jo chat mein Invidious player mein chalti hain. Direct MP4 bhi support.","parameters":{"type":"object","properties":{"query":{"type":"string"},"count":{"type":"integer"}},"required":["query"]}}},
    {"type":"function","function":{"name":"search_youtube","description":"YouTube par videos dhundho. Specifically YouTube maango tab.","parameters":{"type":"object","properties":{"query":{"type":"string"},"count":{"type":"integer"}},"required":["query"]}}},
    {"type":"function","function":{"name":"generate_image","description":"AI se image banao description se — photorealistic (default). FLUX.1-schnell (Hugging Face, tez — agar user ne HF token save kiya ho) try hota hai, warna Pollinations.ai (free, no-key) par automatically fallback hota hai — kabhi rukta nahi. Broken/error response aane par khud retry karta hai.","parameters":{"type":"object","properties":{"prompt":{"type":"string"},"realistic":{"type":"boolean","description":"true (default) = photorealistic, false = stylized/tez"}},"required":["prompt"]}}},
    {"type":"function","function":{"name":"get_ip_info","description":"IP address ki location, ISP, timezone batao. Blank = mera IP.","parameters":{"type":"object","properties":{"ip":{"type":"string"}}}}},
    {"type":"function","function":{"name":"get_spacex_launches","description":"SpaceX launches ki jankari — latest ya upcoming.","parameters":{"type":"object","properties":{"upcoming":{"type":"boolean"}}}}},
    {"type":"function","function":{"name":"get_sunrise_sunset","description":"Kisi shahar mein aaj sunrise aur sunset ka time.","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}},
    {"type":"function","function":{"name":"get_public_holidays","description":"Kisi desh ki public holidays dekhna. country_code = IN, US, GB, etc.","parameters":{"type":"object","properties":{"country_code":{"type":"string"},"year":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"search_radio","description":"Internet radio stations dhundho — naam ya country se.","parameters":{"type":"object","properties":{"query":{"type":"string"},"country":{"type":"string"},"limit":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"search_place_osm","description":"OpenStreetMap se kisi jagah ko dhundho — address, coordinates, map link.","parameters":{"type":"object","properties":{"place":{"type":"string"}},"required":["place"]}}},
    {"type":"function","function":{"name":"scrape_webpage","description":"Kisi bhi webpage ka content padho aur extract karo.","parameters":{"type":"object","properties":{"url":{"type":"string"},"extract":{"type":"string","enum":["text","title","links"]}},"required":["url"]}}},
    {"type":"function","function":{"name":"get_page_media","description":"User ne koi website/link diya ho, wahan se image/video chahiye — link khol ke saari images/videos nikalo. Video ke liye yt-dlp ka 1000+ site extractor use hota hai, na sirf YouTube. Images ke liye bhi thorough scraping hoti hai — lazy-load, responsive srcset, CSS backgrounds, structured data sab cover hote hain, na sirf plain <img> tags. search_images/search_videos se kaam na bane to isko call karo.","parameters":{"type":"object","properties":{"url":{"type":"string"},"media_type":{"type":"string","enum":["all","image","video"]},"limit":{"type":"integer"}},"required":["url"]}}},
    {"type":"function","function":{"name":"watch_page","description":"PROACTIVE — user bole 'jab yeh site update ho/yeh cheez aaye to bata dena' (stock, price, ticket). Background ~25min check karta hai, trigger par phone notification bhejta hai. keyword do to us word ke dikhne par trigger; na do to content-change par. One-shot hai.","parameters":{"type":"object","properties":{"url":{"type":"string"},"keyword":{"type":"string"},"name":{"type":"string"}},"required":["url"]}}},
    {"type":"function","function":{"name":"list_page_watches","description":"Saare active 'jab X ho to batana' watches ki list dikhao.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"stop_watch","description":"Ek active watch ko naam se cancel karo.","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
    {"type":"function","function":{"name":"save_site","description":"Ek achhi website/link ko naam ke saath permanently yaad rakho — jab user bole 'is site ko naam se save kar lo' ya 'yeh link yaad rakho X naam se'. Baad mein sirf naam bol ke us site se fresh image/video mangwaya ja sakega.","parameters":{"type":"object","properties":{"name":{"type":"string"},"url":{"type":"string"}},"required":["name","url"]}}},
    {"type":"function","function":{"name":"play_saved_site","description":"Pehle 'save_site' se naam ke saath save ki gayi website ko khol ke fresh image/video laata hai — user jab sirf naam bole (URL nahi), jaise 'X site se ek video lao'.","parameters":{"type":"object","properties":{"name":{"type":"string"},"media_type":{"type":"string","enum":["all","image","video"]},"limit":{"type":"integer"}},"required":["name"]}}},
    {"type":"function","function":{"name":"list_saved_sites","description":"Saare naam-se-saved website links ki list dikhao.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"delete_saved_site","description":"Kisi saved website link ko naam se hatao.","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
    {"type":"function","function":{"name":"remember","description":"Kuch baat yaad rakh lo TinyDB mein permanently.","parameters":{"type":"object","properties":{"key":{"type":"string"},"value":{"type":"string"}},"required":["key","value"]}}},
    {"type":"function","function":{"name":"recall","description":"Pehle yaad rakhi koi baat nikalo.","parameters":{"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}}},
    {"type":"function","function":{"name":"list_memories","description":"Saari saved memories list karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"forget","description":"Koi memory delete karo.","parameters":{"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}}},
    {"type":"function","function":{"name":"ask_wolfram","description":"WolframAlpha se maths/science calculations.","parameters":{"type":"object","properties":{"question":{"type":"string"}},"required":["question"]}}},
    {"type":"function","function":{"name":"get_nasa_apod","description":"NASA astronomy picture of the day.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_nasa_mars_photos","description":"NASA Mars Curiosity Rover photos.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_nasa_iss_location","description":"ISS space station ki real-time location aur crew.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_nasa_asteroids","description":"Aaj Earth ke paas se guzarne wale asteroids.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"save_api_key","description":"API key save karo memory mein.","parameters":{"type":"object","properties":{"name":{"type":"string"},"value":{"type":"string"}},"required":["name","value"]}}},
    {"type":"function","function":{"name":"delete_api_key","description":"Saved API key delete karo.","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
    {"type":"function","function":{"name":"list_api_keys","description":"Konsi API keys save hain.","parameters":{"type":"object","properties":{}}}},

    {"type":"function","function":{"name":"get_country_info","description":"Kisi desh ki jaankari batao — capital, population, currency, language, flag.","parameters":{"type":"object","properties":{"country":{"type":"string"}},"required":["country"]}}},
    {"type":"function","function":{"name":"check_termux_compatibility","description":"Koi Python library Termux/Android par kaam karegi ya nahi, self-evolution se pehle check karo.","parameters":{"type":"object","properties":{"library_name":{"type":"string"}},"required":["library_name"]}}},
    {"type":"function","function":{"name":"queue_suggestion","description":"Khud se code review karte waqt (scan_codebase/read_code_file ke dauraan) koi REAL improvement dikhe to isse queue karo — sirf list mein daalta hai, kabhi khud se apply nahi karta.","parameters":{"type":"object","properties":{"title":{"type":"string"},"description":{"type":"string"},"files":{"type":"string","description":"Optional — kaunsi file(s) affected hain"}},"required":["title","description"]}}},
    {"type":"function","function":{"name":"list_pending_suggestions","description":"Saare pending code-improvement suggestions dikhao jo tumne khud se socha tha (apply nahi hue). User 'suggestions dikhao' ya 'kya sujhaav hai' bole tab call karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"mark_suggestion_applied","description":"Jab tum kisi queued suggestion ko actually apply kar chuke ho (write_code_file/write_multiple_files ke baad), usse applied mark karo taaki dobara list mein na aaye.","parameters":{"type":"object","properties":{"suggestion_id":{"type":"integer"}},"required":["suggestion_id"]}}},
    {"type":"function","function":{"name":"dismiss_suggestion","description":"User bole 'yeh suggestion mat karo' / 'ignore karo' — us specific suggestion ko dismiss karo.","parameters":{"type":"object","properties":{"suggestion_id":{"type":"integer"}},"required":["suggestion_id"]}}},
    {"type":"function","function":{"name":"clear_all_suggestions","description":"Saare pending suggestions ek saath clear/dismiss karo. User 'sab suggestions hata do' bole tab call karo.","parameters":{"type":"object","properties":{}}}},

    # ── Phase 1 Advanced Tools (v6) ──
    {"type":"function","function":{"name":"calculate","description":"Koi bhi math calculation karo safely (+ - * / // % **). Jab bhi user calculation maange, isko use karo.","parameters":{"type":"object","properties":{"expression":{"type":"string"}},"required":["expression"]}}},
    {"type":"function","function":{"name":"convert_units","description":"Units convert karo — length, weight, volume, speed, data-size, temperature ke beech.","parameters":{"type":"object","properties":{"value":{"type":"number"},"from_unit":{"type":"string"},"to_unit":{"type":"string"}},"required":["value","from_unit","to_unit"]}}},
    {"type":"function","function":{"name":"convert_currency","description":"Live exchange rate se ek currency se doosri currency mein convert karo (USD, INR, EUR, etc — ISO codes).","parameters":{"type":"object","properties":{"amount":{"type":"number"},"from_currency":{"type":"string"},"to_currency":{"type":"string"}},"required":["amount","from_currency","to_currency"]}}},
    {"type":"function","function":{"name":"translate_text","description":"Text ko kisi bhi language mein translate karo. target_lang ISO code do (hi, en, fr, es, etc).","parameters":{"type":"object","properties":{"text":{"type":"string"},"target_lang":{"type":"string"}},"required":["text","target_lang"]}}},
    {"type":"function","function":{"name":"get_dictionary","description":"Kisi English word ka meaning, pronunciation aur synonyms batao.","parameters":{"type":"object","properties":{"word":{"type":"string"}},"required":["word"]}}},
    {"type":"function","function":{"name":"get_wikipedia_summary","description":"Kisi bhi topic/person/place ke baare mein Wikipedia se summary lao.","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    {"type":"function","function":{"name":"get_crypto_price","description":"Kisi cryptocurrency ka live price batao (USD aur INR mein).","parameters":{"type":"object","properties":{"coin":{"type":"string"}},"required":["coin"]}}},
    {"type":"function","function":{"name":"generate_qr","description":"Kisi text/URL/phone number ka QR code image banao.","parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}},
    {"type":"function","function":{"name":"get_random_quote","description":"Ek motivational ya inspirational quote do.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"generate_password","description":"Ek strong, secure random password generate karo.","parameters":{"type":"object","properties":{"length":{"type":"integer"},"use_symbols":{"type":"boolean"}}}}},
    {"type":"function","function":{"name":"text_analyzer","description":"Diye gaye text ka word count, character count, sentence count aur reading time batao.","parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}},
    {"type":"function","function":{"name":"add_todo","description":"Ek naya todo/task list mein add karo.","parameters":{"type":"object","properties":{"task":{"type":"string"},"priority":{"type":"string","enum":["low","medium","high"]}},"required":["task"]}}},
    {"type":"function","function":{"name":"list_todos","description":"Saare pending aur completed todos dikhao.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"complete_todo","description":"Kisi todo ko complete/done mark karo, uski ID se.","parameters":{"type":"object","properties":{"task_id":{"type":"integer"}},"required":["task_id"]}}},
    {"type":"function","function":{"name":"delete_todo","description":"Kisi todo ko permanently delete karo, uski ID se.","parameters":{"type":"object","properties":{"task_id":{"type":"integer"}},"required":["task_id"]}}},
    {"type":"function","function":{"name":"system_info","description":"Phone/Termux system ki jaankari do — storage, battery, Python version.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"play_stream","description":"Video/stream link seedha CHAT ke andar play karo — .m3u8, YouTube, .mp4, ya koi bhi site (Vimeo, Insta, Twitter/X, FB, TikTok, Twitch — yt-dlp 1000+ sites). Type khud detect hota hai.","parameters":{"type":"object","properties":{"url":{"type":"string"},"title":{"type":"string"},"quality":{"type":"string","description":"'144p'-'1080p','4k','auto','low','high'. Default saved quality."}},"required":["url"]}}},
    {"type":"function","function":{"name":"set_default_stream_quality","description":"User ka default stream/data-usage quality set karo — 'ab se 144p mein chalao', 'default 480p rakho', 'data bachane ke liye low quality set karo' jaisa bole to yeh call karo. Future mein har stream/channel isi quality mein try hogi.","parameters":{"type":"object","properties":{"quality":{"type":"string","description":"'144p'..'1080p','4k','auto','low'/'kam data','high'/'best'"}},"required":["quality"]}}},
    {"type":"function","function":{"name":"get_default_stream_quality","description":"Abhi default stream quality kya set hai, batao.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"list_stream_qualities","description":"Diye gaye .m3u8 URL mein konsi qualities (144p se 4k tak) available hain, list karo — jab user 'isme kya kya quality options hain' jaisa poochhe.","parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}},
    {"type":"function","function":{"name":"pause_stream","description":"Chat mein chal rahi HLS stream ko pause karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"resume_stream","description":"Chat mein pause ki hui HLS stream ko resume karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"stop_stream","description":"Chat mein chal rahi HLS stream ko band karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"stop_all_streams","description":"Chat mein chal rahi SAARI HLS streams ek saath band karo (jab multiple stream ek saath chal rahi ho).","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"stream_status","description":"Abhi koi HLS stream chat mein load/chal rahi hai ya nahi, batao.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"save_stream","description":"User ke paas jo legitimate stream/video URL pehle se hai, use ek naam ke saath yaad rakho — taaki dobara URL bolne ki zarurat na pade, sirf naam bol ke play ho sake.","parameters":{"type":"object","properties":{"name":{"type":"string"},"url":{"type":"string"}},"required":["name","url"]}}},
    {"type":"function","function":{"name":"play_saved_stream","description":"Pehle naam se save kiya gaya stream chat mein play karo, naam bol ke.","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
    {"type":"function","function":{"name":"list_saved_streams","description":"Saare naam-se-saved streams ki list dikhao.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"delete_saved_stream","description":"Kisi saved stream ko naam se hatao.","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
    {"type":"function","function":{"name":"diagnose_errors","description":"User jab poochhe 'kya dikkat hai', 'code mein kya galti hai', 'kaunsa tool fail ho raha hai' — logs padh ke saadi bhasha mein real problem batao (file, function, line ke saath), raw traceback nahi.","parameters":{"type":"object","properties":{"limit":{"type":"integer","description":"Kitni recent errors dikhani hain (default 5)"}}}}},
    {"type":"function","function":{"name":"morning_briefing","description":"Ek hi jawab mein time, weather, top news aur motivational quote sab ek saath do — jab user 'good morning', 'aaj ka update', 'briefing do' jaisa bole.","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}},

    # ── Self-Evolution Engine ──
    {"type":"function","function":{"name":"scan_codebase","description":"Poore Jarvis project folder ko scan karo — saari files, size, line-count. Apna khud ka structure samajhne ke liye use karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"read_code_file","description":"Apni hi koi project file (e.g. tools.py, brain.py) padho, edit karne se pehle.","parameters":{"type":"object","properties":{"file_path":{"type":"string"}},"required":["file_path"]}}},
    {"type":"function","function":{"name":"write_code_file","description":"Apni hi koi project file create ya update karo (naya feature/tool add karna, code edit karna). Automatic backup leta hai aur diff dikhata hai. User confirmation ke baad hi call karo.","parameters":{"type":"object","properties":{"file_path":{"type":"string"},"new_content":{"type":"string"}},"required":["file_path","new_content"]}}},
    {"type":"function","function":{"name":"delete_code_file","description":"Apni hi koi project file/tool delete karo. Automatic backup leta hai.","parameters":{"type":"object","properties":{"file_path":{"type":"string"}},"required":["file_path"]}}},
    {"type":"function","function":{"name":"list_backups","description":"Saare self-evolution backup snapshots list karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"rollback","description":"Code ko purane backup snapshot par restore karo. backup_id blank ho to sabse latest backup use hota hai.","parameters":{"type":"object","properties":{"backup_id":{"type":"string"}}}}},
    {"type":"function","function":{"name":"write_multiple_files","description":"EK SAATH multiple project files atomic taur par update karo (e.g. tools.py + brain.py ek hi operation mein). Ek hi confirmation ke baad sab update. files_json parameter mein JSON array do: [{\"path\":\"tools.py\",\"content\":\"...\"},{\"path\":\"brain.py\",\"content\":\"...\"}]. Automatic backup pehle le liya jaata hai.","parameters":{"type":"object","properties":{"files_json":{"type":"string","description":"JSON array of {path, content} objects"}},"required":["files_json"]}}},

    # ── Personality Engine (khud soche / khud faisle le / khud ko badle) ──
    {"type":"function","function":{"name":"record_feedback","description":"User ne kisi specific cheez (jo tumne kaha/kiya) ke baare mein CLEAR pasand/napasand jataayi ho ('accha kiya', 'yeh mat karo', 'pasand aaya', 'galat tha') — tab call karo taaki tumhari personality us feedback se dhal sake.","parameters":{"type":"object","properties":{"behavior":{"type":"string","description":"Chhota tag jo behavior describe kare, e.g. 'unprompted_joke', 'auto_web_search', 'surprise_message'"},"sentiment":{"type":"string","description":"'liked' ya 'disliked'"},"note":{"type":"string","description":"Optional — user ne exactly kya bola"}},"required":["behavior","sentiment"]}}},
    {"type":"function","function":{"name":"set_surprise_mode","description":"User bole 'surprise band karo' / 'khud se mat bolo' / 'surprise chalu karo' — tab yeh call karo taaki proactive/autonomous 'surprise' messages on/off ho sakein.","parameters":{"type":"object","properties":{"enabled":{"type":"boolean"}},"required":["enabled"]}}},
    {"type":"function","function":{"name":"get_personality_status","description":"Abhi tumhari personality traits, jo behaviors pasand/napasand hue hain, aur recent khud-se-liye initiatives — sab user ko dikhao jab wo poochhe 'apni personality dikhao' ya 'tum kaise badle ho'.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"remember_moment","description":"User ne koi emotionally/practically significant baat share ki ho (stress, exam/interview, health, achhi/buri khabar, koi bada decision) — isse yaad rakho taaki baad mein khud se, dost jaisa, follow-up le sako.","parameters":{"type":"object","properties":{"topic":{"type":"string","description":"Chhota topic naam, e.g. 'exam', 'job interview', 'health checkup'"},"note":{"type":"string","description":"Chhota context — kya hua tha"},"follow_up":{"type":"boolean","description":"Default true — baad mein isko check-in karna hai"}},"required":["topic","note"]}}},
    {"type":"function","function":{"name":"resolve_moment","description":"Jab pehle yaad rakha hua koi moment follow-up ho chuka ho ya user ne bata diya 'sab theek hai' — usse resolve/close karo taaki dobara na poochho.","parameters":{"type":"object","properties":{"topic":{"type":"string"}},"required":["topic"]}}},

    # ── Persona / Roleplay System ──
    {"type":"function","function":{"name":"activate_persona","description":"Jarvis ko diye gaye character/role/persona mein dhaal do. User jab bhi kahe 'tum X bano', 'roleplay karo', 'character mein aa jao' waghera, isko call karo.","parameters":{"type":"object","properties":{"character_name":{"type":"string","description":"Character ka chhota naam, e.g. 'Pirate Captain', 'Sherlock Holmes', 'Best Dost'"},"description":{"type":"string","description":"Character ka background, personality, mood — jitna detail user ne diya ya jitna suit kare"},"speaking_style":{"type":"string","description":"Optional — tone/language/catchphrases jaisa character bolta hai"},"voice_gender":{"type":"string","description":"Optional — 'male' ya 'female', agar pata hai character kaisa sound karega (e.g. koi actress → female, koi actor → male). Diya na jaaye to description se khud andaza lagaya jaayega."}},"required":["character_name","description"]}}},
    {"type":"function","function":{"name":"deactivate_persona","description":"Active persona hata ke Jarvis ko wapas normal mode mein le aao. User 'wapas normal Jarvis bano' ya 'roleplay band karo' bole tab call karo.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"get_current_persona","description":"Abhi konsa persona (agar koi) active hai, batao.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"list_saved_personas","description":"Pehle bana chuke saare personas ki list dikhao, taaki user dobara unmein se choose kar sake.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"switch_to_saved_persona","description":"Pehle se saved kisi persona par wapas switch karo, uska naam bol ke (e.g. user bole 'wapas pirate bano').","parameters":{"type":"object","properties":{"character_name":{"type":"string"}},"required":["character_name"]}}},
]
# Tool naam → asli Python function ka mapping
TOOL_FUNCTIONS = {
    "set_alarm": tools.set_alarm,
    "make_call": tools.make_call,
    "send_sms": tools.send_sms,
    "open_app": tools.open_app,
    "get_battery_status": tools.get_battery_status,
    "send_notification": tools.send_notification,
    "vibrate": tools.vibrate,
    "toggle_torch": tools.toggle_torch,
    "get_current_time": tools.get_current_time,
    "get_location": tools.get_location,
    "get_weather": tools.get_weather,
    "get_news": tools.get_news,
    "web_search": tools.web_search,
    "search_images": tools.search_images,
    "find_and_play": tools.find_and_play,
    "search_videos": tools.search_videos,
    "search_youtube": tools.search_youtube,
    "generate_image": tools.generate_image,
    "get_country_info": tools.get_country_info,
    "get_ip_info": tools.get_ip_info,
    "get_spacex_launches": tools.get_spacex_launches,
    "get_sunrise_sunset": tools.get_sunrise_sunset,
    "get_public_holidays": tools.get_public_holidays,
    "search_radio": tools.search_radio,
    "search_place_osm": tools.search_place_osm,
    "scrape_webpage": tools.scrape_webpage,
    "get_page_media": tools.get_page_media,
    "watch_page": tools.watch_page,
    "list_page_watches": tools.list_page_watches,
    "stop_watch": tools.stop_watch,
    "save_site": tools.save_site,
    "play_saved_site": tools.play_saved_site,
    "list_saved_sites": tools.list_saved_sites,
    "delete_saved_site": tools.delete_saved_site,
    "remember": tools.remember,
    "recall": tools.recall,
    "list_memories": tools.list_memories,
    "forget": tools.forget,
    "ask_wolfram": tools.ask_wolfram,
    "get_nasa_apod": tools.get_nasa_apod,
    "get_nasa_mars_photos": tools.get_nasa_mars_photos,
    "get_nasa_iss_location": tools.get_nasa_iss_location,
    "get_nasa_asteroids": tools.get_nasa_asteroids,
    "save_api_key": tools.save_api_key,
    "delete_api_key": tools.delete_api_key,
    "list_api_keys": tools.list_api_keys,

    # ── Phase 1 Advanced Tools (v6) ──
    "calculate": tools.calculate,
    "convert_units": tools.convert_units,
    "convert_currency": tools.convert_currency,
    "translate_text": tools.translate_text,
    "get_dictionary": tools.get_dictionary,
    "get_wikipedia_summary": tools.get_wikipedia_summary,
    "get_crypto_price": tools.get_crypto_price,
    "generate_qr": tools.generate_qr,
    "get_random_quote": tools.get_random_quote,
    "generate_password": tools.generate_password,
    "text_analyzer": tools.text_analyzer,
    "add_todo": tools.add_todo,
    "list_todos": tools.list_todos,
    "complete_todo": tools.complete_todo,
    "delete_todo": tools.delete_todo,
    "system_info": tools.system_info,
    "morning_briefing": tools.morning_briefing,
    "diagnose_errors": tools.diagnose_errors,
    "play_stream": tools.play_stream,
    "pause_stream": tools.pause_stream,
    "resume_stream": tools.resume_stream,
    "stop_stream": tools.stop_stream,
    "stop_all_streams": tools.stop_all_streams,
    "stream_status": tools.stream_status,
    "save_stream": tools.save_stream,
    "play_saved_stream": tools.play_saved_stream,
    "list_saved_streams": tools.list_saved_streams,
    "delete_saved_stream": tools.delete_saved_stream,
    "set_default_stream_quality": tools.set_default_stream_quality,
    "get_default_stream_quality": tools.get_default_stream_quality,
    "list_stream_qualities": tools.list_stream_qualities,

    # ── Self-Evolution Engine ──
    "scan_codebase": self_evolve.scan_codebase,
    "read_code_file": self_evolve.read_code_file,
    "write_code_file": self_evolve.write_code_file,
    "write_multiple_files": self_evolve.write_multiple_files,
    "delete_code_file": self_evolve.delete_code_file,
    "list_backups": self_evolve.list_backups,
    "rollback": self_evolve.rollback,
    "check_termux_compatibility": self_evolve.check_termux_compatibility,
    "queue_suggestion": self_evolve.queue_suggestion,
    "list_pending_suggestions": self_evolve.list_pending_suggestions,
    "mark_suggestion_applied": self_evolve.mark_suggestion_applied,
    "dismiss_suggestion": self_evolve.dismiss_suggestion,
    "clear_all_suggestions": self_evolve.clear_all_suggestions,

    # ── Personality Engine ──
    "record_feedback": personality.record_feedback,
    "set_surprise_mode": personality.set_surprise_mode,
    "get_personality_status": personality.get_personality_status_text,
    "remember_moment": personality.remember_moment,
    "resolve_moment": personality.resolve_moment,

    # ── Persona / Roleplay System ──
    "activate_persona": tools.activate_persona,
    "deactivate_persona": tools.deactivate_persona,
    "get_current_persona": tools.get_current_persona,
    "list_saved_personas": tools.list_saved_personas,
    "switch_to_saved_persona": tools.switch_to_saved_persona,
}


def _memory_context_block() -> str:
    """
    TinyDB mein saved saari 'remember' wali memories nikaal ke ek chhota
    context-block banata hai, jo HAR chat (naye chat samet) ke system
    prompt mein add hota hai. Isse Jarvis ko explicitly `recall` tool call
    karne ki zarurat nahi padti — user ne jo bhi "yaad rakho" bola tha, woh
    hamesha context mein maujood rehta hai, chahe chat kitni bhi purani/nayi ho.
    """
    try:
        mems = tools.list_memories()
    except Exception:
        log.exception("failed to load memories for system prompt")
        return ""
    if not mems or mems.startswith("🧠 Abhi koi memory"):
        return ""
    return (
        "\n\nUSER KI SAVED MEMORIES (pehle 'remember' se yaad rakhi gayi baatein — "
        "yeh HAMESHA sach maano aur bina recall() call kiye seedha use karo):\n"
        + mems
    )


# BUG FIX (v11.1): load_chat() DB se poori/unbounded chat history deta hai,
# aur yeh dono message-builders us poori history ko bina kisi limit ke direct
# LLM request mein daal dete the. Lambi chat + 90 tool-definitions + poora
# system prompt milke asaani se free-tier TPM/request-size limit (jaise
# gpt-oss-120b) cross kar jaate the — isi wajah se baar-baar 413 "Request
# too large" aata tha. Ab sirf recent turns hi LLM ko bhejte hain; poori
# history hamesha disk/DB par safe rehti hai aur UI mein bhi poori dikhti
# hai — sirf model-context ke liye trim hota hai.
MAX_HISTORY_MESSAGES = 24  # ~12 user+assistant turns; zarurat par tune karo


def _trim_history(history):
    """LLM ko bhejne se pehle history ko recent N messages tak trim karta hai."""
    if not history or len(history) <= MAX_HISTORY_MESSAGES:
        return history
    return history[-MAX_HISTORY_MESSAGES:]


def _build_messages(history, user_text):
    system_content = _build_system_prompt() + _memory_context_block()
    messages = [{"role": "system", "content": system_content}]
    messages.extend(_trim_history(history))
    messages.append({"role": "user", "content": user_text})
    return messages


def _convert_to_openai_vision_format(text: str):
    """
    __IMAGE_ATTACHMENT__:base64data wale text ko Groq/OpenAI-style
    multi-part content array mein convert karta hai:
    [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:..."}}]
    Agar image attachment nahi hai to plain string return karta hai.
    """
    if "__IMAGE_ATTACHMENT__:" not in text:
        return text

    parts = []
    segments = text.split("__IMAGE_ATTACHMENT__:")
    if segments[0].strip():
        parts.append({"type": "text", "text": segments[0].strip()})

    for seg in segments[1:]:
        lines = seg.split("\n", 1)
        b64_raw = lines[0].strip()
        rest_text = lines[1].strip() if len(lines) > 1 else ""

        if "," in b64_raw:
            data_url = b64_raw  # already "data:image/jpeg;base64,...."
        else:
            data_url = f"data:image/jpeg;base64,{b64_raw}"

        parts.append({
            "type": "image_url",
            "image_url": {"url": data_url}
        })
        if rest_text:
            parts.append({"type": "text", "text": rest_text})

    if not parts:
        return text
    return parts


def _build_messages_groq(history, user_text):
    """
    Groq ke liye messages build karta hai — image attachment ko
    proper OpenAI-style image_url format mein convert karke.
    """
    messages = [{"role": "system", "content": _build_system_prompt() + _memory_context_block()}]
    messages.extend(_trim_history(history))
    content = _convert_to_openai_vision_format(user_text)
    messages.append({"role": "user", "content": content})
    return messages


def _has_media(text):
    """
    Check karta hai ki tool result mein koi media YA self-evolution
    code-diff hai ya nahi. Agar hai — result seedha frontend ko bhejo,
    AI model se mat guzaro (AI paraphrase/summarize kar sakta hai aur
    asli diff/backup info gayab ho sakta hai).
    IMPORTANT: Yeh function hi single biggest protection hai media bypass
    ke liye, AUR ab self-evolution transparency ke liye bhi — taaki jab
    Jarvis apna khud ka code badle, user ko HAMESHA exact diff dikhe,
    AI ke paraphrase se nahi guzarna pade.
    """
    if not text:
        return False
    t = str(text)
    return (
        "IMAGE_FOUND:" in t or        # Internet se images
        "VIDEO_FOUND:" in t or        # Internet se videos
        "IMAGE_GENERATED:" in t or    # AI generated images
        "RADIO_STREAM:" in t or       # Radio streams
        "HLS_FOUND:" in t or         # HLS (.m3u8) stream — chat ke andar play
        "HLS_CONTROL:" in t or       # HLS pause/resume/stop/status control
        "/static/crawled/" in t or    # icrawler local images
        "/static/generated/" in t or  # Pollinations local images
        "```diff" in t or             # write_code_file ka exact diff
        "Atomic multi-file update complete" in t  # write_multiple_files ka summary
    )


def _execute_tool_call(tool_call):
    """Ek tool call ko chalata hai aur result string return karta hai."""
    name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {}

    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return f"'{name}' naam ka koi tool nahi mila."

    try:
        return func(**args)
    except Exception as e:
        return f"'{name}' chalate waqt error aaya: {e}"


def _parse_retry_seconds(error_text):
    """Error message se 'retry after X seconds/minutes' nikalta hai."""
    import re
    if not error_text:
        return None
    # Groq: "Please try again in 12m32.544s"
    m = re.search(r'try again in (\d+)m([\d.]+)s', str(error_text))
    if m:
        return int(m.group(1)) * 60 + int(float(m.group(2)))
    m = re.search(r'try again in ([\d.]+)s', str(error_text))
    if m:
        return int(float(m.group(1)))
    # Seconds as plain number
    m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', str(error_text))
    if m:
        return int(m.group(1))
    return None


def _friendly_wait_message(provider, seconds):
    """Rate-limit ke liye friendly Hindi message banata hai retry-time ke saath."""
    if seconds is None or seconds <= 0:
        return f"{provider} ki limit abhi khatam ho gayi hai. Thodi der mein wapas try karo."
    mins = seconds // 60
    secs = seconds % 60
    if mins > 0:
        time_str = f"{mins} minute" + (f" {secs} second" if secs else "")
    else:
        time_str = f"{secs} second"
    return (f"{provider} ki limit abhi khatam ho gayi hai — "
            f"lagbhag {time_str} mein wapas free ho jayegi.")


def _fix_leaked_tool_calls(response_text):
    """
    Kuch models (Gemini free tier, OpenRouter) tool call execute karne ki jagah
    text mein likh dete hain jaise: search_images("cats") ya search_videos("dogs")
    Yeh function unhe detect karke actually execute karta hai.
    """
    import re
    if not response_text:
        return response_text

    # Pattern: function_name("arg") ya function_name("arg", count)
    tool_pattern = re.compile(
        r'(search_images|search_videos|search_youtube|get_weather|get_news)'
        r'\s*\(\s*["\'](.*?)["\'](\s*,\s*(\d+))?\s*\)',
        re.IGNORECASE
    )

    matches = list(tool_pattern.finditer(response_text))
    if not matches:
        return response_text

    results = []
    clean_text = response_text
    for m in matches:
        fn_name = m.group(1).lower()
        arg1 = m.group(2)
        arg2 = int(m.group(4)) if m.group(4) else None

        func = TOOL_FUNCTIONS.get(fn_name)
        if not func:
            continue

        try:
            result = func(arg1, arg2) if arg2 else func(arg1)
            results.append(str(result))
        except Exception as e:
            results.append(f"Tool error: {e}")

        # Text se leaked call hata do
        clean_text = clean_text.replace(m.group(0), "").strip()

    if results:
        media_results = [r for r in results if _has_media(r)]
        if media_results:
            # Media result seedha return karo (frontend render karega)
            return "\n".join(media_results)
        # Non-media results append karo clean text ke saath
        combined = clean_text + "\n" + "\n".join(results) if clean_text else "\n".join(results)
        return combined.strip()

    return response_text


def ask_jarvis(history, user_text, chat_id="default"):
    """
    Jarvis ka main brain — Multi-LLM Orchestration + RAG memory.

    Flow:
    1. RAG se relevant purani baatein dhundo (SQLite)
    2. Task complexity classify karo (simple/tool/complex)
    3. Best model route karo task ke hisaab se
    4. Manual override support (groq:/gemini:/openrouter: prefix)
    5. Rate-limit par gracefully fallback
    """
    # ── Personality: har real user turn se closeness dheere dheere badhta hai
    #    (internal scheduler-triggered chats '__' se shuru hoti hain, unhe skip karo)
    if not chat_id.startswith("__") and not user_text.startswith("[SYSTEM-INITIATIVE"):
        try:
            personality.increment_interaction()
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    # ── RAG: Purani relevant baatein context mein add karo ──
    rag_context = ""
    if RAG_ENABLED:
        try:
            rag_context = _rag.retrieve_context(user_text, chat_id=chat_id, n_results=4)
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    # RAG context ko user message se pehle system mein add karo
    enriched_text = user_text
    if rag_context:
        enriched_text = f"{rag_context}\n\n[User ka naya sawaal]: {user_text}"

    selected = memory.get_selected_model()

    # ── Manual model selected ──
    if selected and selected != "auto":
        result = _run_manual_model(selected, history, enriched_text)
        if RAG_ENABLED and result:
            try:
                if not chat_id.startswith("__"):
                    _rag.store_turn(user_text, result, chat_id=chat_id)
            except Exception:
                log.exception("unexpected error - see memory/jarvis_errors.log")
        return result

    # ── Auto Orchestration Mode ──
    groq_keys   = _prioritize_keys(memory.get_available_groq_keys())
    gemini_keys = _prioritize_keys(memory.get_available_gemini_keys())
    or_keys     = _prioritize_keys(memory.get_available_openrouter_keys())

    if not groq_keys and not gemini_keys and not or_keys:
        return ("Mujhe koi bhi AI key nahi mili. Pehle save karo:\n"
                "'Jarvis code api: groq TUMHARI_KEY'  ya\n"
                "'Jarvis code api: gemini TUMHARI_KEY'")

    # Task classify karo
    task_type = _classify_task(user_text)
    preferred = _get_model_for_task(task_type)

    result = None
    err = None
    already_tried_provider = None   # preferred provider dobara retry na ho fallback mein
    groq_retry = gemini_retry = or_retry = None

    # Preferred model pehle try karo
    if preferred:
        provider = preferred["provider"]
        pmodel   = preferred.get("model")
        pkeys    = _prioritize_keys(preferred.get("keys", []))
        already_tried_provider = provider

        if provider == "groq":
            for key in pkeys:
                result, err = _try_groq(key, history, enriched_text, model=pmodel)
                if result is not None:
                    break
                if _is_request_too_large_error(err):
                    # BUG FIX (v11.1): pehle yahan turant groq chhod ke doosre
                    # provider (Gemini/OpenRouter) par jump hota tha, jabki
                    # GROQ_MODELS list mein hi chhote-context models (jaise
                    # gpt-oss-20b) available hain jo shayad same request
                    # handle kar lein. Pinned model (pmodel) size-limit hit
                    # kare to ek baar rotation (model=None) try karo, isi
                    # provider ke andar — sirf jab pmodel diya gaya tha.
                    if pmodel:
                        result, err = _try_groq(key, history, enriched_text, model=None)
                    break  # ab bhi fail ho to Groq chhodo, doosri key try karne ka fayda nahi
            if result is None:
                groq_retry = _parse_retry_seconds(err)
        elif provider == "gemini":
            for key in pkeys:
                result, err = _try_gemini(key, history, enriched_text, model=pmodel)
                if result is not None:
                    break
                if _is_request_too_large_error(err):
                    break
            if result is None:
                gemini_retry = _parse_retry_seconds(err)
        elif provider == "openrouter":
            for key in pkeys:
                result, err = _try_openrouter(key, history, enriched_text, model_id=pmodel)
                if result is not None:
                    break
                if _is_request_too_large_error(err):
                    break
            if result is None:
                or_retry = _parse_retry_seconds(err)

    # Fallback chain: baaki providers try karo (jo preferred mein already try nahi hua)
    if result is None and already_tried_provider != "groq":
        for key in groq_keys:
            result, err = _try_groq(key, history, enriched_text)
            if result is not None:
                break
            groq_retry = _parse_retry_seconds(err)
            if _is_request_too_large_error(err):
                break

    if result is None and already_tried_provider != "gemini":
        for key in gemini_keys:
            result, err = _try_gemini(key, history, enriched_text)
            if result is not None:
                break
            gemini_retry = _parse_retry_seconds(err)
            if _is_request_too_large_error(err):
                break

    if result is None and already_tried_provider != "openrouter":
        for key in or_keys:
            result, err = _try_openrouter(key, history, enriched_text)
            if result is not None:
                break
            or_retry = _parse_retry_seconds(err)
            if _is_request_too_large_error(err):
                break

    if result is None:
        # ── Exponential-backoff retry pass ──
        # Saare providers/keys ek baar fail ho chuke — ho sakta hai yeh
        # sirf temporary rate-limit/network glitch ho. Isliye turant haar
        # maanne se pehle 2 aur chhote attempts, badhte hue delay (1s, 3s)
        # ke saath, sabse pehle available key/provider par.
        retry_provider = None
        retry_key = None
        retry_model = None
        if groq_keys:
            retry_provider, retry_key, retry_model = "groq", groq_keys[0], None
        elif gemini_keys:
            retry_provider, retry_key, retry_model = "gemini", gemini_keys[0], None
        elif or_keys:
            retry_provider, retry_key, retry_model = "openrouter", or_keys[0], None

        if retry_provider:
            for attempt, delay in enumerate((1, 3), start=1):
                time.sleep(delay)
                try:
                    if retry_provider == "groq":
                        result, err = _try_groq(retry_key, history, enriched_text)
                    elif retry_provider == "gemini":
                        result, err = _try_gemini(retry_key, history, enriched_text)
                    else:
                        result, err = _try_openrouter(retry_key, history, enriched_text)
                except Exception:
                    log.exception("unexpected error - see memory/jarvis_errors.log")
                    result = None
                if result is not None:
                    log.info(f"backoff retry attempt {attempt} succeeded via {retry_provider}")
                    break

    if result is None:
        # Sab fail — friendly message
        providers_tried = []
        if groq_keys:   providers_tried.append(("Groq",        groq_retry))
        if gemini_keys: providers_tried.append(("Gemini",      gemini_retry))
        if or_keys:     providers_tried.append(("OpenRouter",  or_retry))
        waits = [t for _, t in providers_tried if t is not None]
        wait  = min(waits) if waits else None
        names = " + ".join(p for p, _ in providers_tried)
        return _friendly_wait_message(names, wait)

    final = _fix_leaked_tool_calls(result)

    # RAG mein store karo
    if RAG_ENABLED and not chat_id.startswith("__"):
        try:
            _rag.store_turn(user_text, final, chat_id=chat_id)
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    return final


def _run_manual_model(selected: str, history, user_text: str) -> str:
    """Manual model selection ke liye helper."""
    groq_keys   = _prioritize_keys(memory.get_available_groq_keys())
    gemini_keys = _prioritize_keys(memory.get_available_gemini_keys())
    or_keys     = _prioritize_keys(memory.get_available_openrouter_keys())

    if selected.startswith("groq:"):
        model_name = selected.split(":", 1)[1]
        if not groq_keys:
            return "Groq key nahi mili. Pehle save karo: 'Jarvis code api: groq TUMHARI_KEY'"
        for key in groq_keys:
            result, error = _try_groq(key, history, user_text, model=model_name)
            if result is not None:
                return _fix_leaked_tool_calls(result)
        return _friendly_wait_message("Groq", _parse_retry_seconds(error if "error" in dir() else None))

    if selected.startswith("gemini:"):
        model_name = selected.split(":", 1)[1]
        if not gemini_keys:
            return "Gemini key nahi hai. 'Jarvis code api: gemini KEY' bolo."
        for key in gemini_keys:
            result, error = _try_gemini(key, history, user_text, model=model_name)
            if result is not None:
                return _fix_leaked_tool_calls(result)
        return "Gemini se jawab nahi mila. Thodi der mein try karo."

    if selected.startswith("openrouter:"):
        model_name = selected.split(":", 1)[1]
        if not or_keys:
            return "OpenRouter key nahi hai. 'Jarvis code api: openrouter KEY' bolo."
        for key in or_keys:
            result, error = _try_openrouter(key, history, user_text, model_id=model_name)
            if result is not None:
                return _fix_leaked_tool_calls(result)
        return _friendly_wait_message("OpenRouter", _parse_retry_seconds(error if "error" in dir() else None))

    return "Unknown model selected."


# ══════════════════════════════════════════════════════════════
# SMART KEY HEALTH TRACKING
# ══════════════════════════════════════════════════════════════
# Process-memory mein (disk pe nahi) yaad rakhta hai konsi key
# recently fail hui thi, taaki usi key ko turant dobara try karke
# time waste na ho — usse thodi der (cooldown) ke liye deprioritize
# kiya jaata hai. Kabhi bhi key ko HATAYA nahi jaata, sirf order mein
# peeche kar diya jaata hai — agar sab keys cooldown pe hain, phir
# bhi normal order mein try hongi (koi functionality loss nahi).

_KEY_FAIL_TIMES = {}   # key_string -> last_fail_timestamp
_KEY_COOLDOWN_SECONDS = 45

# Simple in-process daily usage counters (process restart pe reset ho jaate
# hain — yeh sirf ek lightweight insight hai, permanent analytics nahi)
_USAGE_COUNTERS = {"groq": 0, "gemini": 0, "openrouter": 0, "rate_limited": 0}


def get_usage_counters() -> dict:
    """Abhi tak (is process ke chalte hue) kaunsa provider kitni baar use hua."""
    return dict(_USAGE_COUNTERS)


def _mark_key_failed(key: str):
    if key:
        _KEY_FAIL_TIMES[key] = time.time()


def _mark_key_healthy(key: str):
    if key and key in _KEY_FAIL_TIMES:
        del _KEY_FAIL_TIMES[key]


def _prioritize_keys(keys: list) -> list:
    """Recently-fail hui keys ko list ke end mein bhej deta hai (skip nahi
    karta, sirf order badalta hai) — taaki healthy keys pehle try hon."""
    if not keys or len(keys) < 2:
        return keys
    now = time.time()

    def is_cooling(k):
        fail_time = _KEY_FAIL_TIMES.get(k)
        return fail_time is not None and (now - fail_time) < _KEY_COOLDOWN_SECONDS

    healthy = [k for k in keys if not is_cooling(k)]
    cooling = [k for k in keys if is_cooling(k)]
    return healthy + cooling


def _try_gemini(api_key, history, user_text, model=None):
    """Thin wrapper — health tracking ke saath. Asli logic _try_gemini_raw mein."""
    result, err = _try_gemini_raw(api_key, history, user_text, model=model)
    (_mark_key_healthy if result is not None else _mark_key_failed)(api_key)
    _USAGE_COUNTERS["gemini"] += 1
    if result is None and _is_rate_limit_error(err):
        _USAGE_COUNTERS["rate_limited"] += 1
    return result, err


def _try_groq(api_key, history, user_text, model=None):
    """Thin wrapper — health tracking ke saath. Asli logic _try_groq_raw mein."""
    result, err = _try_groq_raw(api_key, history, user_text, model=model)
    (_mark_key_healthy if result is not None else _mark_key_failed)(api_key)
    _USAGE_COUNTERS["groq"] += 1
    if result is None and _is_rate_limit_error(err):
        _USAGE_COUNTERS["rate_limited"] += 1
    return result, err


def _try_openrouter(api_key, history, user_text, model_id=None):
    """Thin wrapper — health tracking ke saath. Asli logic _try_openrouter_raw mein."""
    result, err = _try_openrouter_raw(api_key, history, user_text, model_id=model_id)
    (_mark_key_healthy if result is not None else _mark_key_failed)(api_key)
    _USAGE_COUNTERS["openrouter"] += 1
    if result is None and _is_rate_limit_error(err):
        _USAGE_COUNTERS["rate_limited"] += 1
    return result, err


def _try_gemini_raw(api_key, history, user_text, model=None):
    """
    Ek Gemini key se try karta hai — GEMINI_MODELS rotation mein.
    Google AI REST API (generateContent) use karta hai.
    Return: (result_or_None, error_text_or_None)
    """
    models_to_try = [model] if model else GEMINI_MODELS
    last_error = None

    # Messages build karo
    raw_messages = _build_messages(history, user_text)
    contents = []
    system_text = None

    for m in raw_messages:
        role = m.get("role") if isinstance(m, dict) else m.role
        text = m.get("content") if isinstance(m, dict) else m.content
        text = text or ""
        if role == "system":
            system_text = text
        elif role == "user":
            # Image attachment check karo
            if "__IMAGE_ATTACHMENT__:" in text:
                parts = []
                segments = text.split("__IMAGE_ATTACHMENT__:")
                # Pehla segment normal text hai
                if segments[0].strip():
                    parts.append({"text": segments[0].strip()})
                # Baaki segments image data hain
                for seg in segments[1:]:
                    lines = seg.split("\n", 1)
                    b64_raw = lines[0].strip()
                    rest_text = lines[1].strip() if len(lines) > 1 else ""
                    # base64 data URL se actual data nikalo
                    if "," in b64_raw:
                        mime_part, b64_data = b64_raw.split(",", 1)
                        mime_type = mime_part.split(":")[1].split(";")[0] if ":" in mime_part else "image/jpeg"
                    else:
                        b64_data = b64_raw
                        mime_type = "image/jpeg"
                    parts.append({
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_data
                        }
                    })
                    if rest_text:
                        parts.append({"text": rest_text})
                if not parts:
                    parts.append({"text": text})
                contents.append({"role": "user", "parts": parts})
            else:
                contents.append({"role": "user", "parts": [{"text": text}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        elif role == "tool":
            contents.append({"role": "user", "parts": [{"text": f"[Tool result]: {text}"}]})

    if not contents:
        contents.append({"role": "user", "parts": [{"text": user_text}]})

    for use_model in models_to_try:
        try:
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{use_model}:generateContent?key={api_key}")

            payload = {"contents": contents}
            if system_text:
                payload["system_instruction"] = {"parts": [{"text": system_text}]}

            # Tool definitions — Gemini format mein convert karo
            gemini_tools = _build_gemini_tools()
            if gemini_tools:
                payload["tools"] = [{"function_declarations": gemini_tools}]
                payload["tool_config"] = {"function_calling_config": {"mode": "AUTO"}}

            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "User-Agent": "JarvisApp/3.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # Response parse karo
            candidates = data.get("candidates", [])
            if not candidates:
                last_error = "No candidates in response"
                continue

            parts = candidates[0].get("content", {}).get("parts", [])

            # Tool call check karo
            tool_calls = [p for p in parts if "functionCall" in p]
            text_parts = [p.get("text", "") for p in parts if "text" in p]

            if tool_calls:
                # Tool calls execute karo
                tool_results = []
                has_media = False
                follow_contents = list(contents)
                # Model ka response add karo
                follow_contents.append({"role": "model", "parts": parts})

                for tc in tool_calls:
                    fc = tc["functionCall"]
                    fn_name = fc.get("name", "")
                    fn_args = fc.get("args", {})
                    func = TOOL_FUNCTIONS.get(fn_name)
                    result = func(**fn_args) if func else f"'{fn_name}' tool nahi mila."
                    tool_results.append(str(result))
                    if _has_media(result):
                        has_media = True
                    # Tool result add karo
                    follow_contents.append({
                        "role": "user",
                        "parts": [{"functionResponse": {
                            "name": fn_name,
                            "response": {"result": str(result)}
                        }}]
                    })

                # Media ho to seedha return karo
                if has_media:
                    return "\n".join(tool_results), None

                # Final response lo (tools ke bina)
                payload2 = {
                    "contents": follow_contents,
                }
                if system_text:
                    payload2["system_instruction"] = {"parts": [{"text": system_text}]}

                req2 = urllib.request.Request(
                    url,
                    data=json.dumps(payload2).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "User-Agent": "JarvisApp/3.0"},
                    method="POST",
                )
                with urllib.request.urlopen(req2, timeout=30) as resp2:
                    data2 = json.loads(resp2.read().decode("utf-8"))
                parts2 = data2.get("candidates",[{}])[0].get("content",{}).get("parts",[])
                final_text = "".join(p.get("text","") for p in parts2 if "text" in p)
                return final_text, None

            # Normal text response
            full_text = "".join(text_parts)
            if full_text:
                return full_text, None

            last_error = "Empty response"
            continue

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            last_error = f"HTTP {e.code}: {body[:200]}"
            # 429 rate limit ya model not found — agla model try karo
            if e.code in (429, 404, 400):
                continue
            return None, last_error
        except Exception as e:
            last_error = str(e)
            continue

    return None, last_error


def _build_gemini_tools():
    """TOOL_DEFINITIONS ko Gemini format mein convert karta hai."""
    gemini_tools = []
    for tool in TOOL_DEFINITIONS:
        fn = tool.get("function", {})
        params = fn.get("parameters", {})
        gemini_tools.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    k: {
                        "type": v.get("type","string").upper(),
                        "description": v.get("description",""),
                    }
                    for k, v in params.get("properties", {}).items()
                },
                "required": params.get("required", []),
            } if params.get("properties") else {"type": "OBJECT", "properties": {}}
        })
    return gemini_tools


def _is_rate_limit_error(error_text):
    if not error_text:
        return False
    text = str(error_text).lower()
    return "rate_limit" in text or "429" in text or "quota" in text


def _is_request_too_large_error(error_text):
    """
    413 'Request too large' — yeh ek KEY-specific problem NAHI hai. Isi
    model ko doosri key se retry karne se koi fayda nahi — request ka
    size hi us model ke TPM (tokens-per-minute) limit se zyada hai,
    isliye har key par wahi error dobara aayega. Isko jaldi detect karke
    key-rotation loop turant tod dena chahiye, taaki time/requests waste
    na ho aur turant agle (bade-limit wale) model/provider par fallback
    ho jaaye.
    """
    if not error_text:
        return False
    text = str(error_text).lower()
    return "413" in text or "request too large" in text or "reduce your message size" in text


def _try_groq_raw(api_key, history, user_text, model=None):
    """
    Ek Groq key se try karta hai — agar model fail ho to GROQ_MODELS list
    mein se agla model try karta hai.
    Return: (result_or_None, error_text_or_None)
    """
    models_to_try = [model] if model else GROQ_MODELS
    has_image = "__IMAGE_ATTACHMENT__:" in user_text

    # Agar image hai aur diya gaya model vision support nahi karta,
    # to vision-capable model par switch karo (user ko error na dikhe)
    if has_image:
        models_to_try = [m for m in models_to_try if m in GROQ_VISION_MODELS]
        if not models_to_try:
            models_to_try = list(GROQ_VISION_MODELS)

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except Exception as e:
        # Ye fail hone ka matlab SAARE models/keys ek saath fail dikhenge
        # (chahe wo khud valid hon) — kyunki client hi nahi ban paya.
        # Common wajah: 'groq' package installed nahi, ya key format galat.
        log.error(f"Groq client init failed (ye saare Groq attempts fail dikhayega): {e}")
        return None, str(e)

    last_error = None
    for use_model in models_to_try:
        try:
            messages = _build_messages_groq(history, user_text)

            try:
                resp = client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    temperature=0.7,
                    max_tokens=1024,
                )
            except Exception as tool_err:
                err_str = str(tool_err)
                if "tool_use_failed" in err_str or "invalid_request_error" in err_str:
                    # Tools ke bina try karo
                    fallback = client.chat.completions.create(
                        model=use_model,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=1024,
                    )
                    return fallback.choices[0].message.content, None
                if "model_not_found" in err_str or "model_decommissioned" in err_str or "does not exist" in err_str:
                    last_error = err_str
                    continue   # agla model try karo
                raise

            msg = resp.choices[0].message

            if msg.tool_calls:
                messages.append(msg)
                tool_results = []
                has_media = False
                for tool_call in msg.tool_calls:
                    result = _execute_tool_call(tool_call)
                    tool_results.append(str(result))
                    if _has_media(result):
                        has_media = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result),
                    })

                # IMAGE/VIDEO result — seedha return, Groq filter na kare
                if has_media:
                    return "\n".join(tool_results), None

                try:
                    final_resp = client.chat.completions.create(
                        model=use_model,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=1024,
                    )
                    final_content = final_resp.choices[0].message.content
                    if final_content and final_content.strip():
                        return final_content, None
                    tr = [m["content"] for m in messages if m.get("role") == "tool"]
                    if tr:
                        return "\n".join(tr), None
                    last_error = f"Empty follow-up response from {use_model}"
                    continue
                except Exception as final_err:
                    tr = [m["content"] for m in messages if m.get("role") == "tool"]
                    if tr:
                        return "\n".join(tr), None
                    return None, str(final_err)

            if msg.content and msg.content.strip():
                return msg.content, None
            last_error = f"Empty response from {use_model}"
            continue

        except Exception as e:
            last_error = str(e)
            err_lower = last_error.lower()
            # BUG FIX: pehle yahan exception silently swallow ho jaati thi —
            # sirf generic "limit khatam" message user ko dikhta tha, chahe
            # asli wajah kuch bhi ho (invalid key, model deprecated, network
            # fail, SDK error). Ab actual error log hota hai taaki
            # diagnose_errors() (ya "kya dikkat hai" bolne par) real reason
            # dikha sake, na ki hamesha ek jaisa vague "rate limit" message.
            log.error(f"Groq model '{use_model}' failed: {last_error[:300]}")
            # Model deprecated/not found/rate-limited — agla MODEL try karo
            # Rate limit bhi model-level par ho sakti hai, isliye agla model try karo
            # Caller (auto mode) decide karega ki agli KEY try karni hai ya nahi
            continue

    # Saare models try ho gaye — last error return karo
    # Caller check karega ki yeh rate-limit thi ya nahi
    return None, last_error


# ---------- OpenRouter ----------

# Free OpenRouter models jo user manually choose kar sakta hai — sirf
# backup/offline fallback (jab live /api/v1/models fetch fail ho jaaye,
# jaise boot ke waqt internet na ho). Har entry mein "vision" flag hai
# taaki image-attachment wale requests sirf vision-capable models pe hi
# jaayein. July 2026 mein directly openrouter.ai/api/v1/models se verify
# kiya gaya — sab "tools" function-calling support karte hain (BUG FIX:
# pehle is list mein aisi models thi jo function-calling support hi nahi
# karti thi, isliye Jarvis ke tools — jaise search_images — kabhi call hi
# nahi ho paate the aur model seedha "link do" jaisa plain-text jawab de
# deta tha, ya bilkul khaali response aata tha).
OPENROUTER_FREE_MODELS_STATIC_FALLBACK = [
    {"id": "openrouter/free",                                    "label": "OR Auto Router (best free)",            "vision": True},
    {"id": "tencent/hy3:free",                                   "label": "Tencent Hy3 (free)",                     "vision": False},
    {"id": "nvidia/nemotron-3-ultra-550b-a55b:free",             "label": "NVIDIA Nemotron 3 Ultra (free)",         "vision": False},
    {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "label": "NVIDIA Nemotron 3 Nano Omni (free)",     "vision": True},
    {"id": "poolside/laguna-m.1:free",                           "label": "Poolside Laguna M.1 (free)",             "vision": False},
    {"id": "poolside/laguna-xs-2.1:free",                        "label": "Poolside Laguna XS 2.1 (free)",          "vision": False},
    {"id": "cohere/north-mini-code:free",                        "label": "Cohere North Mini Code (free)",          "vision": False},
]

_openrouter_models_cache = {"data": None, "ts": 0}


def _fetch_openrouter_free_models():
    """
    OpenRouter apna free-model catalog HAR HAFTE badalta hai — models
    deprecate/rename/remove ho jaate hain (isi wajah se hardcoded list
    kuch hafton mein hi stale ho jaati thi aur "model not found" errors
    aane lagte the). Ab list OpenRouter ke apne /api/v1/models endpoint
    se LIVE fetch hoti hai (1 ghante cache), taaki hamesha current/valid
    model IDs hi try hon. Fetch fail ho (no internet at boot, waghera)
    to purani cached list, ya last-resort static fallback use hota hai —
    isliye app kabhi crash nahi karega.

    BUG FIX (July 2026): pehle sirf pricing (free/paid) check hoti thi.
    Kai free models function-calling ("tools") support hi nahi karte
    (jaise content-safety/guardrail models) — Jarvis har request ke
    saath TOOL_DEFINITIONS bhejta hai, aur aise models ya to error dete
    hain ya khaali/bekaar response dete hain, jisse lagta hai "Jarvis ne
    kuch bola hi nahi" ya "link maang raha hai" (khud se tool call nahi
    kar paata). Ab har model ke "supported_parameters" mein "tools" check
    hota hai — sirf tool-calling-capable free models hi list mein aate
    hain. Vision (image input) support bhi "architecture.input_modalities"
    se dynamically detect hota hai, taaki image attachments hamesha sahi
    model pe route ho.
    """
    cache = _openrouter_models_cache
    if cache["data"] and time.time() - cache["ts"] < 3600:
        return cache["data"]
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "Jarvis/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        models = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {}) or {}
            is_free = (str(pricing.get("prompt", "1")) == "0"
                       and str(pricing.get("completion", "1")) == "0")
            if not is_free or not m.get("id"):
                continue
            supported_params = m.get("supported_parameters") or []
            if "tools" not in supported_params:
                # Function-calling support nahi — Jarvis ke tools
                # (search_images, web_search, etc.) kabhi call hi nahi
                # honge is model se. Skip karo.
                continue
            architecture = m.get("architecture") or {}
            input_modalities = architecture.get("input_modalities") or []
            models.append({
                "id": m["id"],
                "label": m.get("name", m["id"]),
                "vision": "image" in input_modalities,
            })
        if models:
            # "openrouter/free" (auto router) ko hamesha top pe rakho agar mile
            models.sort(key=lambda m: 0 if m["id"] == "openrouter/free" else 1)
            cache["data"] = models[:40]  # zyada bade dropdown se bachne ke liye cap
            cache["ts"] = time.time()
            return cache["data"]
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
    return cache["data"] or OPENROUTER_FREE_MODELS_STATIC_FALLBACK


def get_openrouter_free_models():
    """List_available_models() aur _try_openrouter_raw() dono isi se list lete hain."""
    return _fetch_openrouter_free_models()


def get_openrouter_vision_models():
    """Vision-capable (image input support karne wale) free OpenRouter
    models ki list — dynamically live-fetched data se banti hai (static
    hardcoded model-ID pin karne se model deprecate hone par silently
    fail hota tha)."""
    vision = [m["id"] for m in get_openrouter_free_models() if m.get("vision")]
    if "openrouter/free" not in vision:
        vision.append("openrouter/free")  # auto-router hamesha fallback ke liye
    return vision


def list_available_models():
    """
    Settings UI ke liye saari available model-options ki list deta hai.
    Groups: Auto → Groq → OpenRouter
    """
    models = [{"id": "auto", "label": "Auto (best available)", "group": ""}]
    for gm in GROQ_MODELS:
        models.append({"id": f"groq:{gm}", "label": f"Groq {gm}", "group": "Groq"})
    for gm in GEMINI_MODELS:
        models.append({"id": f"gemini:{gm}", "label": f"Gemini {gm}", "group": "Gemini"})
    for m in get_openrouter_free_models():
        models.append({"id": f"openrouter:{m['id']}", "label": m["label"], "group": "OpenRouter"})
    return models


def _try_openrouter_raw(api_key, history, user_text, model_id=None):
    """
    Ek OpenRouter key se try karta hai — saare free models rotation mein.
    Return: (result_or_None, error_text_or_None)
    """
    import urllib.request
    import urllib.error

    all_free_models = get_openrouter_free_models()
    models_to_try = [model_id] if model_id else [m["id"] for m in all_free_models]
    has_image = "__IMAGE_ATTACHMENT__:" in user_text

    # Agar image hai aur diya gaya model vision support nahi karta,
    # to vision-capable model par switch karo
    if has_image:
        vision_models = get_openrouter_vision_models()
        models_to_try = [m for m in models_to_try if m in vision_models]
        if not models_to_try:
            models_to_try = vision_models

    url = "https://openrouter.ai/api/v1/chat/completions"
    last_error = None

    for model in models_to_try:
        try:
            messages = _build_messages(history, user_text)
            plain_messages = []
            for m in messages:
                if isinstance(m, dict):
                    plain_messages.append(m)
                else:
                    plain_messages.append({"role": m.role, "content": m.content or ""})

            # Image attachment ko OpenAI-compatible format mein convert karo
            # (OpenRouter OpenAI API format follow karta hai)
            for m in plain_messages:
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    if "__IMAGE_ATTACHMENT__:" in m["content"]:
                        m["content"] = _convert_to_openai_vision_format(m["content"])

            payload = {
                "model": model,
                "messages": plain_messages,
                "tools": TOOL_DEFINITIONS,
                "max_tokens": 1024,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://jarvis.local",
                    "X-Title": "Jarvis",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            choice = data["choices"][0]["message"]
            tool_calls = choice.get("tool_calls")

            if tool_calls:
                plain_messages.append(choice)
                or_tool_results = []
                or_has_media = False
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        fn_args = {}
                    func = TOOL_FUNCTIONS.get(fn_name)
                    result = func(**fn_args) if func else f"'{fn_name}' tool nahi mila."
                    or_tool_results.append(str(result))
                    if _has_media(result):
                        or_has_media = True
                    plain_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(result),
                    })

                if or_has_media:
                    return "\n".join(or_tool_results), None

                payload2 = {"model": model, "messages": plain_messages, "max_tokens": 1024}
                req2 = urllib.request.Request(
                    url,
                    data=json.dumps(payload2).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://jarvis.local",
                        "X-Title": "Jarvis",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req2, timeout=30) as resp2:
                    data2 = json.loads(resp2.read().decode("utf-8"))
                followup_content = data2["choices"][0]["message"].get("content", "")
                if not followup_content or not followup_content.strip():
                    # BUG FIX: kuch free models tool-result ke baad khaali
                    # content dete hain — pehle yeh "" success maan liya
                    # jaata tha aur user ko bilkul khaali jawab milta tha.
                    # Ab yeh failure maan ke agla model try hota hai.
                    last_error = f"Empty follow-up response from {model}"
                    continue
                return followup_content, None

            content = choice.get("content", "")
            if not content or not content.strip():
                # BUG FIX: empty/blank content ko success mat maano —
                # agla free model try karo (yehi wajah thi jab Jarvis
                # bilkul chup ho jaata tha ya tool call kiye bina hi
                # ajeeb reply — jaise "link do" — de deta tha).
                last_error = f"Empty response from {model}"
                continue

            return content, None

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            last_error = f"HTTP {e.code}: {body[:200]}"
            continue  # agla model try karo
        except Exception as e:
            last_error = str(e)
            continue  # agla model try karo

    return None, last_error

# Purana naam bhi kaam kare, backward-compatibility ke liye
def ask_groq(history, user_text):
    return ask_jarvis(history, user_text)


# ──────────────────────────────────────────────────────────────────────────
# HLS stream commands — simple keyword-matching fast-path (function-calling
# se alag rakha hai kyunki yeh deterministic hai: URL milte hi turant
# chat ke andar player start karna hai, LLM round-trip ki zarurat nahi).
# Agar phrasing yahan match na ho (kuch bhi naturally bola gaya ho), LLM
# function-calling path (play_stream/pause_stream/resume_stream/stop_stream/
# stream_status tools) usko handle kar leta hai — dono ek hi tools.py
# functions call karte hain.
#
# Actual playback ab HAMESHA browser/chat ke andar hoti hai (HLS_FOUND
# token + hls.js), isliye Render ho ya Termux, kahin bhi host karo, same
# tarah kaam karta hai.
# ──────────────────────────────────────────────────────────────────────────

_STOP_WORDS = ("stop", "band", "ruk", "roko")
_PLAY_WORDS = ("play", "chala", "chalao", "chalu", "shuru", "stream kar")

def handle_stream_command(command: str):
    """
    Returns: (handled: bool, response: str|None)
      - handled=False agar command stream-related nahi tha, taaki caller
        normal ask_jarvis() (LLM) path par gir jaaye.

    Rules:
      - text mein ek http(s) URL ho aur 'play'/'chalao'/'chalu'/'shuru' jaisa
        word bhi ho -> URL nikaal ke tools.play_stream(url)
      - input 'status'/'kya chal raha hai' ho -> tools.stream_status()
      - input mein 'stop'/'band'/'ruk'/'roko' word ho -> tools.stop_stream()
      - input mein 'pause' ho -> tools.pause_stream()
      - input mein 'resume'/'continue' ho -> tools.resume_stream()
    """
    if not command or not isinstance(command, str):
        return False, None

    text = command.strip()
    lower = text.lower()
    words = lower.split()

    url_match = _URL_RE.search(text)

    if url_match and any(w in lower for w in _PLAY_WORDS):
        url = url_match.group(0).rstrip(").,\"'")
        return True, tools.play_stream(url)

    if lower in ("status", "stream status") or "kya chal raha hai" in lower:
        return True, tools.stream_status()

    if "pause" in words:
        return True, tools.pause_stream()

    if "resume" in words or "continue" in words:
        return True, tools.resume_stream()

    if "sab" in words and any(w in words for w in _STOP_WORDS):
        return True, tools.stop_all_streams()

    if any(w in words for w in _STOP_WORDS):
        return True, tools.stop_stream()

    return False, None
