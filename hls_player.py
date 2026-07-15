# -*- coding: utf-8 -*-
"""
Jarvis HLS Player
------------------
Headless HLS (.m3u8) playback module — for streams you already have a
legitimate URL for (apna camera, apna server, ek authorized/public API).
Yeh module sirf PLAY karta hai; kahin se bhi URL "discover"/scrape/bypass
nahi karta — woh iska scope hi nahi hai.

Do backends support karta hai:
  1. python-vlc (agar installed hai)  -> play/pause/stop/seek control milta hai
  2. ffmpeg subprocess (fallback)      -> hamesha available agar ffmpeg binary hai

Use:
    import hls_player

    hls_player.play("https://your-own-server/stream.m3u8")
    hls_player.pause()
    hls_player.resume()
    hls_player.stop()
    hls_player.status()
"""

import shutil
import subprocess
import threading

from logger import get_logger

log = get_logger("hls_player")

_vlc_available = False
try:
    import vlc  # python-vlc (wraps libVLC)
    _vlc_available = True
except ImportError:
    vlc = None

_state_lock = threading.Lock()
_state = {
    "backend": None,      # "vlc" | "ffmpeg" | None
    "url": None,
    "playing": False,
    "vlc_instance": None,
    "vlc_player": None,
    "ffmpeg_proc": None,
}


def _is_valid_hls_url(url: str) -> bool:
    """Basic sanity check — asli validation player khud karega, yeh sirf obvious ghalat input rokta hai."""
    if not url or not isinstance(url, str):
        return False
    return url.strip().lower().startswith(("http://", "https://"))


def play(url: str) -> str:
    """
    Diye gaye .m3u8 URL ko play karna shuru karta hai. VLC available ho to
    usse (play/pause/seek control ke liye), warna ffmpeg subprocess se.
    """
    if not _is_valid_hls_url(url):
        return "❌ Valid HTTP(S) .m3u8 URL do."

    stop()  # koi pehle se chal raha ho to rok do

    with _state_lock:
        if _vlc_available:
            try:
                instance = vlc.Instance("--no-video" if False else "")  # audio+video dono chalne do
                player = instance.media_player_new()
                media = instance.media_new(url)
                player.set_media(media)
                player.play()

                _state.update({
                    "backend": "vlc",
                    "url": url,
                    "playing": True,
                    "vlc_instance": instance,
                    "vlc_player": player,
                })
                log.info(f"HLS playback started via VLC: {url}")
                return "▶️ Stream shuru ho gaya (VLC backend)."
            except Exception:
                log.exception("VLC playback failed, falling back to ffmpeg")

        # ffmpeg fallback — local audio/video output device par decode+play karta hai
        if shutil.which("ffmpeg") is None and shutil.which("ffplay") is None:
            return "❌ Na VLC na ffmpeg/ffplay mila — inme se ek install karo (`pip install python-vlc` ya `apt install ffmpeg`)."

        player_bin = "ffplay" if shutil.which("ffplay") else "ffmpeg"
        try:
            if player_bin == "ffplay":
                cmd = ["ffplay", "-autoexit", "-nodisp" if False else "-loglevel", "warning", url]
            else:
                # sirf decode karke discard — real device output ke liye ffplay behtar hai
                cmd = ["ffmpeg", "-i", url, "-f", "null", "-"]

            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            _state.update({
                "backend": "ffmpeg",
                "url": url,
                "playing": True,
                "ffmpeg_proc": proc,
            })
            log.info(f"HLS playback started via {player_bin}: {url}")
            return f"▶️ Stream shuru ho gaya ({player_bin} backend)."
        except Exception:
            log.exception("ffmpeg/ffplay playback failed to start")
            return "❌ Stream start nahi ho paya — logs check karo (memory/jarvis_errors.log)."


def pause() -> str:
    """VLC backend hi pause/resume support karta hai. ffmpeg process ko pause karna reliable nahi, isliye stop() use karo."""
    with _state_lock:
        if not _state["playing"]:
            return "Kuch chal hi nahi raha."
        if _state["backend"] == "vlc" and _state["vlc_player"]:
            _state["vlc_player"].pause()
            _state["playing"] = False
            return "⏸️ Pause kar diya."
        return "⚠️ ffmpeg backend pause support nahi karta — stop() use karo."


def resume() -> str:
    with _state_lock:
        if _state["backend"] == "vlc" and _state["vlc_player"]:
            _state["vlc_player"].play()
            _state["playing"] = True
            return "▶️ Resume kar diya."
        return "⚠️ Kuch resume karne layak nahi hai."


def stop() -> str:
    """Chal rahi stream (VLC ya ffmpeg, dono) ko band karta hai."""
    with _state_lock:
        if _state["backend"] == "vlc" and _state["vlc_player"]:
            try:
                _state["vlc_player"].stop()
            except Exception:
                log.exception("error stopping vlc player")
        if _state["backend"] == "ffmpeg" and _state["ffmpeg_proc"]:
            try:
                _state["ffmpeg_proc"].terminate()
            except Exception:
                log.exception("error terminating ffmpeg process")

        was_playing = _state["playing"]
        _state.update({
            "backend": None, "url": None, "playing": False,
            "vlc_instance": None, "vlc_player": None, "ffmpeg_proc": None,
        })
        return "⏹️ Stream band kar diya." if was_playing else "Kuch chal hi nahi raha tha."


def status() -> str:
    with _state_lock:
        if not _state["playing"]:
            return "Abhi koi stream nahi chal rahi."
        return f"▶️ Chal raha hai ({_state['backend']}): {_state['url']}"
