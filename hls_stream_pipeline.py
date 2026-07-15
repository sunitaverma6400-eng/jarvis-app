# -*- coding: utf-8 -*-
"""
Jarvis HLS Stream Pipeline
--------------------------
Modular network-to-player pipeline for HLS (.m3u8) sources on headless
hardware (e.g. a custom Termux/Pi box). This module only plays streams
you already have a legitimate URL for (your own camera, your own media
server, a public/authorized API) — it does not discover, scrape, or
bypass access controls for third-party streams. That is out of scope.

Pipeline stages:
    1. resolve_manifest(url)   -> fetch and identify master vs. media playlist
    2. ManifestParser          -> parse #EXTM3U tags, variant streams, segments
    3. StreamPipe              -> hand the resolved media URL to a player
                                  backend (ffplay preferred, vlc fallback)

Design notes:
  - We don't hand-roll segment downloading/decryption. ffplay/ffmpeg already
    implement the HLS spec (variant selection, segment sequencing, adaptive
    reload of live playlists) correctly and robustly — reimplementing that
    is unnecessary and error-prone. This module's job is: figure out which
    concrete media playlist URL to hand off, then manage the player process.
  - "Chunked stream data efficiently" in a headless environment really means:
    let ffplay/ffmpeg do the segment fetching (they pipeline HTTP range/byte
    requests internally), and keep our process wrapper non-blocking so the
    rest of Jarvis (voice, tools, etc.) stays responsive.

Use:
    from hls_stream_pipeline import StreamPipe

    pipe = StreamPipe()
    print(pipe.load("https://your-own-server/stream.m3u8"))
    print(pipe.status())
    pipe.stop()
"""

import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from logger import get_logger

log = get_logger("hls_stream_pipeline")

_TIMEOUT = 8  # seconds for manifest fetch


# --------------------------------------------------------------------------
# Stage 1+2: fetch + parse the manifest, resolve which playlist to actually play
# --------------------------------------------------------------------------

class ManifestParser:
    """
    Minimal M3U8 parser: distinguishes a master playlist (lists variant
    streams at different bitrates) from a media playlist (lists actual
    .ts/.m4s segments), and resolves relative URLs to absolute ones.
    """

    def __init__(self, text: str, base_url: str):
        self.lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        self.base_url = base_url

    def is_valid(self) -> bool:
        return bool(self.lines) and self.lines[0] == "#EXTM3U"

    def is_master_playlist(self) -> bool:
        return any(ln.startswith("#EXT-X-STREAM-INF") for ln in self.lines)

    def variant_streams(self):
        """
        Returns a list of dicts: [{"bandwidth": int, "resolution": str|None, "url": str}, ...]
        parsed from #EXT-X-STREAM-INF lines, sorted low->high bandwidth.
        """
        variants = []
        for i, ln in enumerate(self.lines):
            if not ln.startswith("#EXT-X-STREAM-INF"):
                continue
            attrs = _parse_attr_list(ln.split(":", 1)[1] if ":" in ln else "")
            uri = self.lines[i + 1] if i + 1 < len(self.lines) else None
            if not uri or uri.startswith("#"):
                continue
            variants.append({
                "bandwidth": int(attrs.get("BANDWIDTH", 0) or 0),
                "resolution": attrs.get("RESOLUTION"),
                "url": urllib.parse.urljoin(self.base_url, uri),
            })
        variants.sort(key=lambda v: v["bandwidth"])
        return variants

    def segment_count(self) -> int:
        """How many media segments this (media) playlist currently lists."""
        return sum(1 for ln in self.lines if ln and not ln.startswith("#"))

    def is_live(self) -> bool:
        """No #EXT-X-ENDLIST tag => live/growing playlist."""
        return not any(ln.startswith("#EXT-X-ENDLIST") for ln in self.lines)


def _parse_attr_list(raw: str) -> dict:
    """Parse HLS attribute-list syntax: KEY=VAL,KEY="VAL",... """
    attrs, buf, key, in_quotes = {}, "", "", False
    parts, current = [], ""
    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
            current += ch
        elif ch == "," and not in_quotes:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current:
        parts.append(current)
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        attrs[k.strip()] = v.strip().strip('"')
    return attrs


def resolve_manifest(url: str, prefer: str = "best") -> dict:
    """
    Fetches `url`, determines whether it's a master or media playlist, and
    resolves it down to a single concrete media-playlist URL that a player
    can be pointed at directly.

    prefer: "best" | "worst" -> which variant to pick from a master playlist.

    Returns: {"play_url": str, "is_live": bool|None, "variants": [...]}
    Raises: ValueError on unreachable / non-HLS content.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-HLS/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            final_url = resp.geturl()  # follow redirects
    except Exception as e:
        raise ValueError(f"Manifest fetch failed: {e}")

    parser = ManifestParser(raw, final_url)
    if not parser.is_valid():
        raise ValueError("URL did not return a valid #EXTM3U manifest.")

    if parser.is_master_playlist():
        variants = parser.variant_streams()
        if not variants:
            raise ValueError("Master playlist had no usable variant streams.")
        chosen = variants[-1] if prefer == "best" else variants[0]
        # Recurse one level to get live/segment info for the chosen variant.
        sub = resolve_manifest(chosen["url"], prefer=prefer)
        sub["variants"] = variants
        return sub

    return {"play_url": final_url, "is_live": parser.is_live(), "variants": []}


# --------------------------------------------------------------------------
# Stage 3: hand off to a player backend as a managed subprocess
# --------------------------------------------------------------------------

class StreamPipe:
    """
    Headless playback pipe: resolves a manifest, then launches ffplay
    (preferred, since it's built for exactly this) or vlc as a subprocess.
    Non-blocking — playback runs in the background process while Jarvis
    keeps handling other requests.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._url = None
        self._backend = None
        self._loaded_at = None

    def load(self, source_url: str, prefer_quality: str = "best") -> str:
        with self._lock:
            self._stop_locked()

            try:
                resolved = resolve_manifest(source_url, prefer=prefer_quality)
            except ValueError as e:
                return f"❌ {e}"

            play_url = resolved["play_url"]
            backend, cmd = self._build_command(play_url)
            if cmd is None:
                return "❌ Na ffplay na vlc mila — `apt install ffmpeg` ya `pip install python-vlc` karo."

            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except Exception as e:
                log.exception("Failed to launch player backend")
                return f"❌ Player start nahi hua: {e}"

            self._proc = proc
            self._url = play_url
            self._backend = backend
            self._loaded_at = time.time()

            live_tag = "live" if resolved.get("is_live") else "VOD"
            n_variants = len(resolved.get("variants") or [])
            extra = f", {n_variants} variant(s) available" if n_variants else ""
            log.info(f"HLS pipeline: playing {play_url} via {backend} ({live_tag}{extra})")
            return f"▶️ Playing via {backend} ({live_tag}{extra})."

    @staticmethod
    def _build_command(play_url: str):
        if shutil.which("ffplay"):
            return "ffplay", [
                "ffplay", "-hide_banner", "-loglevel", "warning",
                "-autoexit", "-infbuf",  # -infbuf: don't drop buffered live data
                play_url,
            ]
        if shutil.which("cvlc") or shutil.which("vlc"):
            binname = "cvlc" if shutil.which("cvlc") else "vlc"
            return binname, [binname, "--play-and-exit", play_url]
        return None, None

    def _stop_locked(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    log.exception("Failed to kill lingering player process")
        self._proc, self._url, self._backend, self._loaded_at = None, None, None, None

    def stop(self) -> str:
        with self._lock:
            was_active = self._proc is not None
            self._stop_locked()
            return "⏹️ Stopped." if was_active else "Kuch chal hi nahi raha tha."

    def status(self) -> str:
        with self._lock:
            if not self._proc:
                return "Idle — koi stream loaded nahi hai."
            alive = self._proc.poll() is None
            state = "playing" if alive else "ended"
            uptime = int(time.time() - self._loaded_at) if self._loaded_at else 0
            return f"{state} ({self._backend}), {uptime}s: {self._url}"
