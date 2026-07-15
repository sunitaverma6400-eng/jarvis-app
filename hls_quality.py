"""
Jarvis HLS Quality Selector
------------------------------
Kai HLS (.m3u8) streams ek "master playlist" hoti hain — jisme alag-alag
resolution/bandwidth ke multiple variant sub-streams listed hote hain
(#EXT-X-STREAM-INF). Yeh module aisi master playlist ko parse karke
available qualities nikalta hai, aur user ki maangi hui quality
("144p", "480p", "kam data", "HD", "auto", waghera) ke sabse kareeb
wala variant URL chunta hai — taaki mobile data bachaya ja sake ya
best quality maangi ja sake, jaisa user chahe.

NOTE: Agar koi stream sirf single-quality (non-adaptive) hai, to usme
quality switch karne ka koi option nahi hota — us case mein original
URL hi play hoti hai aur user ko bata diya jaata hai.
"""

import re
import urllib.error
import urllib.parse
import urllib.request

from logger import get_logger
log = get_logger("hls_quality")

MAX_PLAYLIST_BYTES = 2 * 1024 * 1024  # master playlists chhoti hoti hain, 2MB kaafi hai
FETCH_TIMEOUT = 15

# height (px) ke hisaab se friendly label — jitni upar wali line se match
# ho jaaye, wahi label mil jaata hai (144p tak "sabse kam", 2160+ tak "4k")
QUALITY_LADDER = [
    ("144p", 144), ("240p", 240), ("360p", 360), ("480p", 480),
    ("720p", 720), ("1080p", 1080), ("1440p", 1440), ("4k", 2160),
]

# User jo bhi bole usse height (px) mein normalize karne ke liye
QUALITY_ALIASES = {
    "144p": 144, "240p": 240, "360p": 360,
    "480p": 480, "sd": 480,
    "720p": 720, "hd": 720,
    "1080p": 1080, "fhd": 1080, "fullhd": 1080,
    "1440p": 1440, "2k": 1440,
    "4k": 2160, "uhd": 2160, "2160p": 2160,
}

_STREAM_INF_RE = re.compile(r'#EXT-X-STREAM-INF:(.*)')
_RES_RE = re.compile(r'RESOLUTION=(\d+)x(\d+)')
_BW_RE = re.compile(r'BANDWIDTH=(\d+)')


def _fetch_small(url: str, timeout: int = FETCH_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_PLAYLIST_BYTES)
    return data.decode("utf-8", errors="ignore")


def _label_from_height(h: int) -> str:
    for label, hh in QUALITY_LADDER:
        if h <= hh:
            return label
    return QUALITY_LADDER[-1][0]


def _label_from_bandwidth(bw: int) -> str:
    """RESOLUTION attribute na ho to bandwidth se rough quality guess karta hai."""
    kbps = bw / 1000
    if kbps <= 200:
        return "144p"
    if kbps <= 400:
        return "240p"
    if kbps <= 800:
        return "360p"
    if kbps <= 1200:
        return "480p"
    if kbps <= 2500:
        return "720p"
    if kbps <= 6000:
        return "1080p"
    return "4k"


def get_stream_qualities(url: str, timeout: int = FETCH_TIMEOUT) -> dict:
    """
    Diye gaye .m3u8 ko fetch karke check karta hai ki yeh master
    playlist hai ya nahi. Master ho to available variants (resolution/
    bandwidth/url) ki list deta hai, sorted descending (best pehle).
    Return: {"is_master": bool, "variants": [...], "error": str|None}
    """
    try:
        text = _fetch_small(url, timeout=timeout)
    except Exception as e:
        return {"is_master": False, "variants": [], "error": str(e)}

    if "#EXT-X-STREAM-INF" not in text:
        return {"is_master": False, "variants": [], "error": None}

    lines = text.splitlines()
    variants = []
    for i, line in enumerate(lines):
        line = line.strip()
        m = _STREAM_INF_RE.match(line)
        if not m:
            continue
        attrs = m.group(1)
        uri = None
        for nxt in lines[i + 1:]:
            nxt = nxt.strip()
            if not nxt or nxt.startswith("#"):
                continue
            uri = nxt
            break
        if not uri:
            continue

        full_url = urllib.parse.urljoin(url, uri)
        rm = _RES_RE.search(attrs)
        bm = _BW_RE.search(attrs)
        height = int(rm.group(2)) if rm else 0
        bandwidth = int(bm.group(1)) if bm else 0
        label = _label_from_height(height) if height else (_label_from_bandwidth(bandwidth) if bandwidth else "unknown")
        variants.append({"label": label, "height": height, "bandwidth": bandwidth, "url": full_url})

    variants.sort(key=lambda v: (v["height"], v["bandwidth"]), reverse=True)
    return {"is_master": True, "variants": variants, "error": None}


def normalize_quality(quality: str):
    """
    User ka quality input samajhta hai. Returns:
      "auto"    -> adaptive/best-effort, koi fixed level nahi
      "lowest"  -> jo bhi sabse chhoti quality available ho
      "highest" -> jo bhi sabse badi quality available ho
      <int>     -> target height (px), e.g. 480
      None      -> samajh nahi aaya
    """
    if not quality:
        return None
    q = str(quality).strip().lower().replace(" ", "")
    if q in ("auto", "automatic", "adaptive", "bestquality", "default"):
        return "auto"
    if q in ("low", "lowest", "datasaver", "kamdata", "databachao", "kamdatawala"):
        return "lowest"
    if q in ("high", "highest", "best", "sabseachi", "sabsebadhiya", "top"):
        return "highest"
    if q.isdigit():
        q += "p"
    return QUALITY_ALIASES.get(q)


def pick_variant(variants: list, quality: str):
    """
    variants: get_stream_qualities()["variants"] (sorted desc by height).
    quality: raw string jo user ne bola.
    Returns (variant_dict|None, mode:str|None).
      mode None ka matlab hai "auto" ya kuch samajh nahi aaya — caller
      ko original/master URL hi use karni chahiye (adaptive rehne do).
    """
    if not variants:
        return None, None

    target = normalize_quality(quality)
    if target is None or target == "auto":
        return None, None

    if target == "lowest":
        return variants[-1], "lowest"
    if target == "highest":
        return variants[0], "highest"

    # target ek height (int) hai — sabse kareebi variant dhundo
    best = min(variants, key=lambda v: abs((v["height"] or 0) - target))
    return best, "closest"
