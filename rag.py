"""
Jarvis RAG (Retrieval-Augmented Generation) System — SQLite Edition
---------------------------------------------------------------------
ChromaDB ki jagah pure-Python SQLite + TF-IDF based similarity search.
Koi heavy dependency nahi — Termux mein perfectly chalta hai.

Kaise kaam karta hai:
1. Har conversation turn SQLite mein store hota hai (sqlite3 — Python built-in)
2. Jab user kuch puchta hai, TF-IDF cosine similarity se relevant purani
   baatein dhundi jaati hain (numpy bhi nahi chahiye — pure Python math)
3. Woh context brain.py ko diya jaata hai
4. Sab kuch disk par persist hota hai — server restart pe data nahi jaata
"""

import os
import re
import json
import math
import sqlite3
import datetime
from collections import Counter
from logger import get_logger
log = get_logger("rag")

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
DB_PATH = os.path.join(DB_DIR, "rag_store.db")

_conn = None

# Hinglish + English dono ke liye common stopwords (TF-IDF noise kam karne ke liye)
_STOPWORDS = {
    "hai", "hain", "ho", "ka", "ki", "ke", "ko", "se", "me", "mein", "ek",
    "aur", "ya", "to", "tha", "thi", "the", "kya", "kyun", "kaise", "kab",
    "kahan", "kaun", "is", "us", "ye", "yeh", "wo", "woh", "iske", "uske",
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "for", "of",
    "to", "in", "on", "at", "by", "with", "i", "you", "he", "she", "it",
    "we", "they", "my", "your", "his", "her", "its", "our", "their",
}


def _get_conn():
    """SQLite connection lazily initialize karo + tables banao."""
    global _conn
    if _conn is not None:
        return _conn
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TEXT NOT NULL
            )
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON memories(chat_id)")
        _conn.commit()
        return _conn
    except Exception as e:
        print(f"[RAG] SQLite init error: {e}")
        return None


def _tokenize(text: str):
    """Simple tokenizer — words nikaalo, lowercase, stopwords hatao."""
    words = re.findall(r"[a-zA-Z\u0900-\u097F]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def _tf_vector(tokens):
    """Term frequency vector — Counter ke roop mein."""
    return Counter(tokens)


def _cosine_similarity(vec_a: Counter, vec_b: Counter) -> float:
    """Do TF vectors ke beech cosine similarity — pure Python, no numpy."""
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a.keys()) & set(vec_b.keys())
    dot = sum(vec_a[w] * vec_b[w] for w in common)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def store_turn(user_msg: str, assistant_msg: str, chat_id: str = "default"):
    """
    Ek conversation turn store karo — user + assistant dono SQLite mein.
    Automatically call hota hai server.py se har message ke baad.
    """
    conn = _get_conn()
    if not conn:
        return False
    try:
        now = datetime.datetime.now().isoformat()
        if user_msg and len(user_msg.strip()) > 5:
            conn.execute(
                "INSERT INTO memories (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (chat_id, "user", user_msg[:1000], now)
            )
        clean_asst = _clean_for_rag(assistant_msg)
        if clean_asst and len(clean_asst.strip()) > 5:
            conn.execute(
                "INSERT INTO memories (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (chat_id, "assistant", clean_asst[:1000], now)
            )
        conn.commit()

        # Purane records prune karo (max 500 per chat — storage bloat avoid)
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE chat_id=?", (chat_id,)
        ).fetchone()[0]
        if count > 500:
            conn.execute("""
                DELETE FROM memories WHERE id IN (
                    SELECT id FROM memories WHERE chat_id=?
                    ORDER BY id ASC LIMIT ?
                )
            """, (chat_id, count - 500))
            conn.commit()
        return True
    except Exception as e:
        print(f"[RAG] store error: {e}")
        return False


def retrieve_context(query: str, chat_id: str = "default", n_results: int = 4) -> str:
    """
    Query se related purani baatein dhundo (TF-IDF cosine similarity)
    aur ek context string return karo.
    """
    conn = _get_conn()
    if not conn:
        return ""
    try:
        rows = conn.execute(
            "SELECT role, content, ts FROM memories WHERE chat_id=? ORDER BY id DESC LIMIT 300",
            (chat_id,)
        ).fetchall()
        if not rows:
            return ""

        query_tokens = _tokenize(query)
        if not query_tokens:
            return ""
        query_vec = _tf_vector(query_tokens)

        scored = []
        for role, content, ts in rows:
            doc_tokens = _tokenize(content)
            doc_vec = _tf_vector(doc_tokens)
            sim = _cosine_similarity(query_vec, doc_vec)
            if sim > 0.12:  # similarity threshold
                scored.append((sim, role, content, ts))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:n_results]

        lines = []
        for sim, role, content, ts in top:
            ts_short = ts[:16].replace("T", " ")
            lines.append(f"[{ts_short}] {role}: {content}")

        return "📚 Purani relevant baatein (context):\n" + "\n".join(lines)
    except Exception as e:
        print(f"[RAG] retrieve error: {e}")
        return ""


def _clean_for_rag(text: str) -> str:
    """Media tokens aur FILE blocks hata ke clean text return karo."""
    text = re.sub(r"IMAGE_FOUND:\S+", "", text)
    text = re.sub(r"IMAGE_GENERATED:\S+", "", text)
    text = re.sub(r"VIDEO_FOUND:\S+\|[^\n]*", "", text)
    text = re.sub(r"RADIO_STREAM:[^\n]+", "", text)
    text = re.sub(r"FILE_CREATE:[^\n]+\n[\s\S]*?\nFILE_END", "", text)
    return text.strip()


def get_all_chat_ids() -> list:
    """RAG store mein jitne bhi distinct chat_ids hain, unki list deta hai."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        rows = conn.execute("SELECT DISTINCT chat_id FROM memories").fetchall()
        return [r[0] for r in rows]
    except Exception:
        log.exception("unexpected error - see memory/jarvis_errors.log")
        return []


def summarize_old_turns(chat_id: str, summarizer_fn, keep_recent: int = 60, batch_size: int = 40) -> bool:
    """
    Long-term memory summarization: agar ek chat_id ke paas 'keep_recent'
    se zyada purani entries hain, sabse purani 'batch_size' turns ko
    ek single compact summary row mein badal deta hai (role='summary').
    Yeh store ko chhota rakhta hai aur purani baatein bhi context ke roop
    mein zinda rehti hain, bina raw messages ke bulk ke.

    summarizer_fn: ek function jo (text: str) -> str leta hai — is text ka
    summary bana ke deta hai. (server.py se brain.py ka koi AI call pass
    kiya jaata hai, taaki rag.py khud kisi AI provider se seedha juda na ho.)
    """
    conn = _get_conn()
    if not conn:
        return False
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE chat_id=? AND role != 'summary'", (chat_id,)
        ).fetchone()[0]
        if total <= keep_recent:
            return False  # abhi summarize karne ki zaroorat nahi

        old_rows = conn.execute(
            """SELECT id, role, content FROM memories
               WHERE chat_id=? AND role != 'summary'
               ORDER BY id ASC LIMIT ?""",
            (chat_id, batch_size)
        ).fetchall()
        if not old_rows:
            return False

        combined_text = "\n".join(f"{role}: {content}" for _, role, content in old_rows)
        summary_text = summarizer_fn(combined_text)
        if not summary_text or not summary_text.strip():
            return False

        ids_to_remove = [r[0] for r in old_rows]
        now = datetime.datetime.now().isoformat()
        placeholders = ",".join("?" * len(ids_to_remove))
        conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids_to_remove)
        conn.execute(
            "INSERT INTO memories (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (chat_id, "summary", f"[Purani baaton ka summary]: {summary_text.strip()[:800]}", now)
        )
        conn.commit()
        log.info(f"summarize_old_turns: chat_id={chat_id} — {len(ids_to_remove)} turns → 1 summary")
        return True
    except Exception as e:
        print(f"[RAG] summarize error: {e}")
        return False


def get_rag_stats() -> dict:
    """RAG store ka status — settings page ke liye."""
    conn = _get_conn()
    total = 0
    if conn:
        try:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        except Exception:
            log.exception("unexpected error - see memory/jarvis_errors.log")
    return {
        "available": conn is not None,
        "engine": "sqlite-tfidf",
        "total_docs": total,
        "store_path": DB_PATH
    }


def clear_rag(chat_id: str = None):
    """
    RAG store clear karo.
    chat_id diya to sirf us chat ka, warna poora.
    """
    conn = _get_conn()
    if not conn:
        return "RAG available nahi hai."
    try:
        if chat_id:
            cur = conn.execute("DELETE FROM memories WHERE chat_id=?", (chat_id,))
            conn.commit()
            return f"Chat '{chat_id}' ki {cur.rowcount} memories hata di."
        else:
            conn.execute("DELETE FROM memories")
            conn.commit()
            return "Poori RAG memory clear ho gayi."
    except Exception as e:
        return f"RAG clear error: {e}"
