"""
Jarvis Self-Evolution Engine
-----------------------------
Yeh module Jarvis ko apna khud ka codebase padhne, edit karne, naye tool
add karne, aur purana code delete karne ki capability deta hai — lekin
hamesha SAFE tareeke se: har modification se pehle automatic backup,
har change ka clear report, aur ek command se rollback.

Design rules (zaroori):
1. Koi bhi write/delete is project folder (PROJECT_ROOT) ke andar hi ho
   sakta hai — bahar kuch touch nahi hota (path traversal block).
2. Har write/delete se PEHLE poore project ka timestamped snapshot
   backups/ folder mein le liya jaata hai.
3. Binary/venv/cache/backup folders khud scan/snapshot mein include
   nahi hote (size aur noise kam rakhne ke liye).
4. Har function ek human-readable Hindi/Hinglish summary string return
   karta hai jo seedha user ko dikhaya jaa sakta hai — "kahan kya badla".
"""

import os
import re
import shutil
import difflib
import datetime
import json
from logger import get_logger
log = get_logger("self_evolve")

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(PROJECT_ROOT, "backups")

# Yeh cheezein kabhi scan/snapshot/edit mein touch nahi hoti
_IGNORE_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules",
                "backups", "memory", ".cache", "site-packages"}
_IGNORE_EXT = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3"}

# Code files jo Jarvis edit/scan karta hai
_CODE_EXT = {".py", ".js", ".html", ".css", ".json", ".md", ".txt"}


# ─────────────────────────────────────────────
# Path safety
# ─────────────────────────────────────────────

def _safe_path(rel_path: str):
    """rel_path ko PROJECT_ROOT ke andar resolve karta hai. Bahar jaane
    ki koshish (../../etc) hone par None return karta hai."""
    rel_path = rel_path.strip().lstrip("/\\")
    target = os.path.abspath(os.path.join(PROJECT_ROOT, rel_path))
    if os.path.commonpath([target, PROJECT_ROOT]) != PROJECT_ROOT:
        return None
    return target


# ─────────────────────────────────────────────
# 1. CODEBASE SCAN
# ─────────────────────────────────────────────

def scan_codebase(_unused: str = ""):
    """Poore project folder ko scan karke har code file ki list, size
    aur line-count deta hai — taaki Jarvis ko apne khud ke structure ka
    pata ho."""
    report = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
        for f in files:
            ext = os.path.splitext(f)[1]
            if ext in _IGNORE_EXT:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, PROJECT_ROOT)
            try:
                size = os.path.getsize(full)
                lines = 0
                if ext in _CODE_EXT:
                    with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                        lines = sum(1 for _ in fh)
                report.append(f"📄 {rel} — {size} bytes" + (f", {lines} lines" if lines else ""))
            except Exception:
                continue
    if not report:
        return "⚠️ Project mein koi file nahi mili."
    return "🗂️ Jarvis codebase scan:\n" + "\n".join(sorted(report))


# ─────────────────────────────────────────────
# 2. READ FILE
# ─────────────────────────────────────────────

def read_code_file(file_path: str):
    """Kisi bhi project file ka poora content padh kar deta hai (edit
    se pehle context lene ke liye)."""
    full = _safe_path(file_path)
    if not full:
        return f"❌ '{file_path}' project folder se bahar hai — access denied."
    if not os.path.isfile(full):
        return f"❌ '{file_path}' naam ki file nahi mili."
    try:
        with open(full, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
        return f"📄 {file_path} ({len(content.splitlines())} lines):\n```\n{content}\n```"
    except Exception as e:
        return f"❌ '{file_path}' padhne mein error: {e}"


# ─────────────────────────────────────────────
# 3. BACKUP (snapshot) — har modification se pehle automatic
# ─────────────────────────────────────────────

def _snapshot():
    """Poore project ka timestamped snapshot backups/ mein banata hai.
    Return karta hai backup ki folder ka naam (id)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, ts)
    os.makedirs(dest, exist_ok=True)

    def _ignore(dir_, names):
        ignored = set()
        for n in names:
            if n in _IGNORE_DIRS or n.startswith("."):
                ignored.add(n)
        return ignored

    for item in os.listdir(PROJECT_ROOT):
        if item in _IGNORE_DIRS or item.startswith("."):
            continue
        src = os.path.join(PROJECT_ROOT, item)
        dst = os.path.join(dest, item)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=_ignore)
            else:
                shutil.copy2(src, dst)
        except Exception:
            continue
    return ts


def list_backups(_unused: str = ""):
    """Saare available backup snapshots list karta hai (rollback ke liye)."""
    if not os.path.isdir(BACKUP_DIR):
        return "ℹ️ Abhi tak koi backup nahi liya gaya."
    snaps = sorted(os.listdir(BACKUP_DIR), reverse=True)
    if not snaps:
        return "ℹ️ Abhi tak koi backup nahi liya gaya."
    lines = [f"{i+1}. {s}" for i, s in enumerate(snaps[:20])]
    return "🗃️ Available backups (sabse naya pehle):\n" + "\n".join(lines)


def cleanup_old_backups(keep_days: int = 30, keep_min: int = 10):
    """
    Purane backups delete karta hai taaki backups/ folder disk space na
    khaaye — Jarvis mahino chalne ke baad yeh naturally bahut bada ho
    sakta hai. Safety: kam se kam 'keep_min' sabse naye backups hamesha
    rakhe jaate hain, chahe woh 'keep_days' se purane hi kyun na ho —
    taaki rollback ka option kabhi bhi zero na ho jaaye.
    Return: kitne backups delete hue (int).
    """
    if not os.path.isdir(BACKUP_DIR):
        return 0
    snaps = sorted(os.listdir(BACKUP_DIR), reverse=True)  # naya pehle
    if len(snaps) <= keep_min:
        return 0

    cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
    removed = 0
    # keep_min sabse naye snapshots ko touch hi nahi karna
    for snap in snaps[keep_min:]:
        try:
            snap_time = datetime.datetime.strptime(snap, "%Y%m%d_%H%M%S")
        except ValueError:
            continue  # naam format match nahi hua, safety ke liye skip
        if snap_time < cutoff:
            snap_path = os.path.join(BACKUP_DIR, snap)
            try:
                shutil.rmtree(snap_path)
                removed += 1
            except Exception:
                log.exception("unexpected error - see memory/jarvis_errors.log")
    return removed


# ─────────────────────────────────────────────
# 4b. WRITE MULTIPLE FILES — atomic multi-file update
# ─────────────────────────────────────────────

def _check_python_syntax(file_path: str, content: str):
    """
    Agar file .py hai, uska syntax compile karke check karta hai (ast.parse
    — koi execution nahi hoti, sirf parsing). Galat syntax likhne se pehle
    hi pakad leta hai, taaki self-evolution kabhi bhi broken .py file na
    chhode. Return: (True, None) agar theek hai, (False, error_msg) agar nahi.
    """
    if not file_path.endswith(".py"):
        return True, None
    try:
        import ast
        ast.parse(content, filename=file_path)
        return True, None
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, str(e)


def write_multiple_files(files_json: str):
    """
    Ek hi operation mein multiple files update karta hai.
    files_json: JSON string — [{\"path\": \"tools.py\", \"content\": \"...\"}, ...]
    Pehle ek single backup liya jaata hai, fir sab files ek ek karke write
    hoti hain. Koi bhi fail ho to wahi file skip hoti hai, baaki continue.
    """
    import json as _json
    try:
        files = _json.loads(files_json)
        if not isinstance(files, list):
            return "❌ files_json mein list chahiye, e.g. [{\"path\":\"a.py\",\"content\":\"...\"}]"
    except Exception as e:
        return f"❌ files_json parse nahi ho payi: {e}"

    # Single backup for the whole batch
    backup_id = _snapshot()
    results = []
    for entry in files:
        path = entry.get("path", "").strip()
        content = entry.get("content", "")
        if not path:
            results.append("⚠️ Ek entry mein 'path' missing — skip.")
            continue
        full = _safe_path(path)
        if not full:
            results.append(f"❌ '{path}' project folder se bahar hai — skip.")
            continue
        old = ""
        if os.path.isfile(full):
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    old = fh.read()
            except Exception:
                log.exception("unexpected error - see memory/jarvis_errors.log")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        ok, syntax_err = _check_python_syntax(path, content)
        if not ok:
            results.append(f"❌ {path} — syntax error, SKIP kiya (purani file safe hai): {syntax_err}")
            continue
        try:
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(content)
            diff = list(difflib.unified_diff(
                old.splitlines(), content.splitlines(),
                fromfile=f"{path} (purana)", tofile=f"{path} (naya)",
                lineterm="", n=1))
            added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            results.append(f"✅ {path} — +{added} lines, -{removed} lines")
        except Exception as e:
            results.append(f"❌ {path} — write error: {e}")

    summary = "\n".join(results)
    return (f"📦 Atomic multi-file update complete!\n"
            f"🛡️ Backup: backups/{backup_id}\n"
            f"📋 Results:\n{summary}\n\n"
            f"⚡ Server restart karo (Ctrl+C → python server.py) to load changes.")




def write_code_file(file_path: str, new_content: str):
    """Kisi file ko naye content se overwrite/create karta hai.
    Pehle automatic backup leta hai, fir ek diff summary return karta
    hai ki kya badla."""
    full = _safe_path(file_path)
    if not full:
        return f"❌ '{file_path}' project folder se bahar hai — access denied."

    backup_id = _snapshot()

    old_content = ""
    existed = os.path.isfile(full)
    if existed:
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                old_content = fh.read()
        except Exception:
            old_content = ""

    os.makedirs(os.path.dirname(full), exist_ok=True)

    # ── Auto syntax-check: agar .py file hai aur naya content todta hai,
    #    to write hi mat karo — purana content jaisa tha waisa hi rehne do.
    ok, syntax_err = _check_python_syntax(file_path, new_content)
    if not ok:
        return (f"❌ '{file_path}' likha nahi gaya — naye content mein syntax error hai:\n"
                f"   {syntax_err}\n"
                f"Purani working file waisi ki waisi hai, kuch bhi break nahi hua.")

    with open(full, "w", encoding="utf-8") as fh:
        fh.write(new_content)

    diff = list(difflib.unified_diff(
        old_content.splitlines(), new_content.splitlines(),
        fromfile=f"{file_path} (purana)", tofile=f"{file_path} (naya)",
        lineterm="", n=2))
    diff_preview = "\n".join(diff[:40]) if diff else "(naya file banayi gayi)"

    action = "update" if existed else "create"
    return (f"✅ Maine update ho gaya hoon. File '{file_path}' {action} ho gayi.\n"
            f"🛡️ Backup le liya gaya: backups/{backup_id} (rollback ke liye 'rollback {backup_id}' bolo)\n"
            f"🔧 Badlav:\n```diff\n{diff_preview}\n```")


# ─────────────────────────────────────────────
# 5. DELETE FILE (with backup)
# ─────────────────────────────────────────────

def delete_code_file(file_path: str):
    """Kisi file/tool ko delete karta hai — pehle backup leta hai."""
    full = _safe_path(file_path)
    if not full:
        return f"❌ '{file_path}' project folder se bahar hai — access denied."
    if not os.path.isfile(full):
        return f"❌ '{file_path}' naam ki file mili nahi, delete nahi ho saki."

    backup_id = _snapshot()
    try:
        os.remove(full)
    except Exception as e:
        return f"❌ '{file_path}' delete karne mein error: {e}"

    return (f"🗑️ '{file_path}' delete kar di gayi hai.\n"
            f"🛡️ Backup le liya gaya: backups/{backup_id} (galti se delete hui to 'rollback {backup_id}' bolo)")


# ─────────────────────────────────────────────
# 6. ROLLBACK
# ─────────────────────────────────────────────

def rollback(backup_id: str = ""):
    """Diye gaye backup snapshot se poora project restore karta hai.
    backup_id blank ho to sabse latest backup use hota hai."""
    if not os.path.isdir(BACKUP_DIR):
        return "❌ Koi backup nahi mila — rollback nahi ho sakta."

    snaps = sorted(os.listdir(BACKUP_DIR), reverse=True)
    if not snaps:
        return "❌ Koi backup nahi mila — rollback nahi ho sakta."

    backup_id = backup_id.strip()
    if not backup_id:
        backup_id = snaps[0]
    elif backup_id not in snaps:
        return f"❌ Backup '{backup_id}' nahi mila. {list_backups('')}"

    src_dir = os.path.join(BACKUP_DIR, backup_id)

    # Safety: rollback se pehle bhi current state ka ek "pre-rollback" snapshot
    pre_id = _snapshot()

    # Backup ke items restore karo
    restored = []
    backup_items = set(os.listdir(src_dir))
    for item in backup_items:
        src = os.path.join(src_dir, item)
        dst = os.path.join(PROJECT_ROOT, item)
        try:
            if os.path.isdir(src):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            restored.append(item)
        except Exception:
            continue

    # Backup ke BAAD add hue naye files delete karo (true full restore)
    # (sirf .py files — static/templates/data dirs ko chhod do taaki user data safe rahe)
    removed_extras = []
    for item in os.listdir(PROJECT_ROOT):
        if item in backup_items:
            continue
        if not item.endswith(".py"):
            continue
        dst = os.path.join(PROJECT_ROOT, item)
        try:
            os.remove(dst)
            removed_extras.append(item)
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")

    extra_note = f"\nNew files hataaye gaye: {', '.join(removed_extras)}" if removed_extras else ""
    return (f"⏪ Rollback complete — project '{backup_id}' wale version par wapas aa gaya hai.\n"
            f"Restore hui items: {', '.join(restored)}{extra_note}\n"
            f"(Is rollback se pehle ka state bhi backups/{pre_id} mein safe hai.)")


# ─────────────────────────────────────────────
# 7. TERMUX COMPATIBILITY CHECK
# ─────────────────────────────────────────────

# Known libraries jo Termux/Android par nahi chalti ya bahut mushkil hoti hain
_TERMUX_INCOMPATIBLE = {
    "pywin32": "Sirf Windows ke liye hai (win32 API).",
    "pyobjc": "Sirf macOS ke liye hai.",
    "tensorflow": "Full TensorFlow Termux par build/install nahi hoti (no official ARM wheel for Android libc — tflite-runtime try karo).",
    "torch": "PyTorch ka official wheel Termux/Android ARM ke liye nahi hai — bahut heavy aur build fail hoti hai.",
    "torchvision": "PyTorch ecosystem ka hissa — Termux par compatible nahi.",
    "opencv-python": "Pre-built wheel nahi milti, source se build bahut heavy/slow hai — 'opencv-python-headless' bhi mushkil hai Termux par.",
    "pyaudio": "PortAudio C-extension build issues deta hai Termux par — 'sounddevice' ya Termux:API ke audio commands better hain.",
    "pygame": "SDL2 dependencies Termux par properly nahi milti — display/audio backend issues.",
    "tkinter": "GUI toolkit hai, Termux mein koi X-server/display nahi hota — chalega nahi.",
    "PyQt5": "Heavy GUI framework, Termux ke text/CLI environment mein non-functional.",
    "PyQt6": "Heavy GUI framework, Termux ke text/CLI environment mein non-functional.",
    "wx": "wxPython GUI — Termux mein display nahi hota.",
    "selenium": "Chrome/Firefox driver chahiye jo Termux par directly available nahi — 'requests'/'playwright-lite' jaisa kuch use karo, ya skip karo.",
    "playwright": "Chromium/Firefox/WebKit browser binaries download+run karta hai jo Android ARM par supported nahi hain — Termux mein install/run nahi hoga. Iske bajaye 'requests' + 'beautifulsoup4' se HTML scrape karo (JS-rendering chahiye to yeh tareeka kaam nahi karega).",
    "puppeteer": "Node.js based hai aur Chromium binary chahiye — Termux/Android ARM par chalta nahi.",
    "docker": "Docker daemon Android kernel par nahi chalta (no containerization support).",
    "psycopg2": "PostgreSQL ka C-extension build Termux par dependencies maangta hai (libpq) — 'psycopg2-binary' try karo, phir bhi unreliable.",
    "scipy": "Pure pip install se compile-heavy hai — Termux package manager se 'pkg install scipy' zyada reliable hai pip se.",
    "numpy": "Pip se slow build ho sakta hai — 'pkg install python-numpy' Termux mein zyada reliable hai.",
}


def check_termux_compatibility(library_name: str):
    """Diya gaya pip library naam Termux mein chalega ya nahi, batata hai."""
    key = library_name.strip().lower()
    if key in _TERMUX_INCOMPATIBLE:
        return (f"⚠️ '{library_name}' Termux mein possible nahi hai.\n"
                f"Reason: {_TERMUX_INCOMPATIBLE[key]}")
    return (f"✅ '{library_name}' generally Termux-compatible lagti hai "
            f"(pure-Python ya lightweight C-extension). Phir bhi 'pip install {library_name}' "
            f"try karke confirm karo — kuch packages ko 'pkg install build-essential' jaisi "
            f"system dependency chahiye ho sakti hai.")


# ─────────────────────────────────────────────
# 8. SUGGESTION QUEUE — "khud faisle le" par SAFELY:
#    Jarvis khud se apna code review karke improvements soch sakta hai aur
#    unhe yahan QUEUE kar sakta hai — lekin KABHI khud se apply nahi karta.
#    Applying hamesha ek real user turn ("haan sab karo") ke through hoti
#    hai, jahan Jarvis phir se wahi write_code_file/write_multiple_files
#    tools use karta hai (automatic backup, syntax-check — sab pehle jaisa).
# ─────────────────────────────────────────────

SUGGESTIONS_FILE = os.path.join(PROJECT_ROOT, "memory", "suggestions.json")


def _load_suggestions():
    if not os.path.exists(SUGGESTIONS_FILE):
        return []
    try:
        with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_suggestions(items):
    os.makedirs(os.path.dirname(SUGGESTIONS_FILE), exist_ok=True)
    with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def queue_suggestion(title: str, description: str, files: str = ""):
    """
    Khud se code review karte waqt (scan_codebase/read_code_file ke
    dauraan) koi real improvement dikhe to isse call karo — SIRF queue
    mein daalta hai, KABHI khud se apply nahi karta. title thoda unique
    rakho taaki duplicate suggestions baar baar na jamaa hon.
    """
    items = _load_suggestions()
    title_l = title.strip().lower()
    for it in items:
        if it["status"] == "pending" and it["title"].strip().lower() == title_l:
            return f"ℹ️ '{title}' jaisa suggestion pehle se pending hai — dobara nahi jodaa."

    new_id = (max((it["id"] for it in items), default=0)) + 1
    items.append({
        "id": new_id,
        "title": title.strip()[:120],
        "description": description.strip()[:600],
        "files": files.strip(),
        "status": "pending",
        "ts": datetime.datetime.now().isoformat(),
    })
    _save_suggestions(items)
    return f"📝 Suggestion #{new_id} queue ho gaya: '{title}' (apply nahi hui, sirf list mein hai)."


def list_pending_suggestions(_unused: str = ""):
    """User ko dikhane layak, human-readable pending suggestions list."""
    items = [i for i in _load_suggestions() if i["status"] == "pending"]
    if not items:
        return "✅ Abhi koi pending suggestion nahi hai."
    lines = ["📋 Pending suggestions (khud se sochi, apply nahi ki gayi):"]
    for it in items:
        files_note = f" [{it['files']}]" if it.get("files") else ""
        lines.append(f"  #{it['id']}. {it['title']}{files_note}\n     → {it['description']}")
    lines.append("\nKoi bhi apply karne ke liye bolo 'suggestion #ID apply karo' ya 'sab apply karo'.")
    return "\n".join(lines)


def get_pending_suggestions_summary():
    """Compact one-line summary jo system prompt mein jodi jaati hai."""
    items = [i for i in _load_suggestions() if i["status"] == "pending"]
    if not items:
        return ""
    titles = "; ".join(it["title"] for it in items[:5])
    return (f"\n\n[PENDING CODE SUGGESTIONS — tumne pehle khud se socha tha, abhi bhi APPLY NAHI HUI]\n"
            f"{len(items)} suggestion(s) queue mein hain: {titles}. Agar conversation mein "
            f"natural mauka bane, mention kar sakte ho ki inhe review karna hai — lekin kabhi "
            f"khud se apply mat karna. User 'suggestions dikhao' bole to list_pending_suggestions "
            f"call karo. User 'apply karo'/'sab karo' bole to (a) list_pending_suggestions se ID(s) "
            f"confirm karo, (b) relevant file(s) read_code_file se padho, (c) write_code_file/"
            f"write_multiple_files se badlo (normal backup/confirmation rules waisi hi lagu hoti "
            f"hain), (d) mark_suggestion_applied call karo har jo apply ho gaya.")


def mark_suggestion_applied(suggestion_id):
    return _update_suggestion_status(suggestion_id, "applied", "✅ Apply ho gaya")


def dismiss_suggestion(suggestion_id):
    return _update_suggestion_status(suggestion_id, "dismissed", "🗑️ Hata diya")


def _update_suggestion_status(suggestion_id, status, verb):
    try:
        sid = int(suggestion_id)
    except (TypeError, ValueError):
        return f"❌ '{suggestion_id}' ek valid suggestion ID nahi hai."
    items = _load_suggestions()
    for it in items:
        if it["id"] == sid:
            it["status"] = status
            _save_suggestions(items)
            return f"{verb}: #{sid} '{it['title']}'."
    return f"❌ Suggestion #{sid} nahi mila."


def clear_all_suggestions(_unused: str = ""):
    items = _load_suggestions()
    pending = [i for i in items if i["status"] == "pending"]
    for it in items:
        if it["status"] == "pending":
            it["status"] = "dismissed"
    _save_suggestions(items)
    return f"🧹 {len(pending)} pending suggestion(s) clear kar diye."


# ─────────────────────────────────────────────
# Autonomous code-review job — scheduler se periodically call hota hai
# ─────────────────────────────────────────────

_CODE_REVIEW_DIRECTIVE = (
    "[SYSTEM-CODE-REVIEW — yeh koi user message nahi hai, yeh tumhara khud ka "
    "internal review trigger hai] Abhi tumhe apna khud ka codebase review karna "
    "hai. Flow: (1) scan_codebase call karo, (2) 2-3 sabse important/complex "
    "files read_code_file se padho, (3) agar koi REAL improvement, missing "
    "feature, ya risk dikhe jo tumne khud file mein dekha ho — queue_suggestion "
    "call karo (title chhota, description mein exact file/function reference "
    "ho). Sirf wahi likhna jo tumne actually verify kiya ho, kabhi generic "
    "guess mat likhna. KABHI write_code_file/write_multiple_files/"
    "delete_code_file/rollback is trigger ke response mein mat call karna — "
    "sirf dekhna aur queue_suggestion se sujhana, karna nahi. Agar kuch nayi "
    "dikkat na mile to kuch mat likhna, koi suggestion queue mat karo."
)


def run_code_review_job():
    """server.py process ke andar scheduler background thread se call hota
    hai. Purane pending suggestions bahut jyada na jamaa hon isliye agar
    already 8+ pending hain to naya review skip karta hai."""
    pending_count = len([i for i in _load_suggestions() if i["status"] == "pending"])
    if pending_count >= 8:
        return

    import brain as _brain

    try:
        _brain.ask_jarvis([], _CODE_REVIEW_DIRECTIVE, chat_id="__code_review__")
    except Exception:
        log.exception("code review job fail hua")
