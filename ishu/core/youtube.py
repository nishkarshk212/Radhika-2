# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic
#
# Download chain (in order of priority):
#   1. Cookies Base64  (COOKIES_DATA env var → yt-dlp with cookie_0.txt)
#   2. Railway YT API  (RAILWAY_YT_API_URL / RAILWAY_YT_API_KEY)
#   3. Shruti API      (SHRUTI_API_URL / SHRUTI_API_KEY)
#   4. xBit API        (YTPROXY_URL / YT_API_KEY)
#
# Stream URL chain (for instant playback without download):
#   1. yt-dlp --get-url  (uses cookies base64 if available)
#   2. Railway API       (validated with HEAD request before returning)

import asyncio
import glob
import os
import random
import re
import time as _time
from typing import Union

import aiohttp
import yt_dlp
from py_yt import Playlist, VideosSearch
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message

from ishu import config, logger
from ishu.helpers import utils
from ishu.helpers._dataclass import Track

# ── Config ────────────────────────────────────────────────────────────────────
SHRUTI_API_URL      = getattr(config, "SHRUTI_API_URL",      "https://api.shrutibots.site")
SHRUTI_API_KEY      = getattr(config, "SHRUTI_API_KEY",      None)

RAILWAY_YT_API_URL  = getattr(config, "RAILWAY_YT_API_URL",  None)
RAILWAY_YT_API_KEY  = getattr(config, "RAILWAY_YT_API_KEY",  None)

YTPROXY_URL         = getattr(config, "YTPROXY_URL",         None)
YT_API_KEY          = getattr(config, "YT_API_KEY",          None)

DOWNLOAD_DIR        = "downloads"

# Per-video_id locks so the foreground download() and the background prefetch
# task never run yt-dlp on the SAME video_id concurrently. Two concurrent
# yt-dlp processes writing the same "<id>.mp3.part" / "<id>.orig.mp3" temp
# files cause "Unable to rename file: [Errno 2]" crashes.
_dl_locks: "dict[str, asyncio.Lock]" = {}


def _dl_lock(video_id: str) -> asyncio.Lock:
    lock = _dl_locks.get(video_id)
    if lock is None:
        lock = asyncio.Lock()
        _dl_locks[video_id] = lock
    return lock


def _resolve_downloaded_file(video_id: str, ext: str) -> str | None:
    """
    Find the actual file produced by yt-dlp for `video_id`.

    Because FFmpegExtractAudio / merge postprocessors rewrite the extension,
    the real output may be:
      - downloads/<id>.<ext>            (preferred, after outtmpl fix)
      - downloads/<id>.<ext>.<ext>      (legacy when outtmpl ended in .mp3)
      - downloads/<id>.<any-valid-ext>  (yt-dlp chose a different container)
    We ignore transient temp files (*.part, *.ytdl, *.orig.*).
    Returns the first existing, non-empty path or None.
    """
    import glob as _glob

    candidates = [
        os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}"),
        os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}.{ext}"),
    ]
    for c in sorted(_glob.glob(os.path.join(DOWNLOAD_DIR, f"{video_id}.*"))):
        if c.endswith((".part", ".ytdl")) or ".orig." in os.path.basename(c):
            continue
        candidates.append(c)

    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return c
    return None


# yt-dlp 2026.x needs a JS runtime to solve YouTube's n-signature challenge.
# The default runtime is 'deno', but it is unreliable in containers; Node >= 23.5
# is the dependable choice. If no working runtime is available every request
# fails with "Sign in to confirm you're not a bot". Force the 'node' runtime.
JS_RUNTIMES = {"node": {}}

# YouTube's "Sign in to confirm you're not a bot" check is applied per player
# client. The default 'web' client is the most aggressively gated; the mobile
# and TV clients ('tv', 'ios', 'android', 'mweb', 'web_safari') frequently
# bypass it entirely — no cookies or proxy needed. yt-dlp tries these in order
# and uses the first that returns a playable response. Override with the
# YT_PLAYER_CLIENTS env var (comma-separated) if YouTube shifts its gating.
_DEFAULT_PLAYER_CLIENTS = "tv,ios,android,web_safari,mweb,web"


def _with_js_runtime(opts: dict) -> dict:
    """Return a copy of yt-dlp opts with the node runtime, player-client
    bypass, and optional proxy.

    - Player clients (tv/ios/android/...) dodge the web bot-check with no
      external dependency. Tune with the YT_PLAYER_CLIENTS env var.
    - Set the YTDLP_PROXY env var (e.g. http://user:pass@host:port or
      socks5://host:port) to route every yt-dlp request through a clean IP —
      the reliable fix when the whole server IP is bot-flagged.
    """
    out = dict(opts)
    out["js_runtimes"] = JS_RUNTIMES

    clients = [
        c.strip()
        for c in os.environ.get("YT_PLAYER_CLIENTS", _DEFAULT_PLAYER_CLIENTS).split(",")
        if c.strip()
    ]
    if clients:
        extractor_args = dict(out.get("extractor_args") or {})
        yt_args = dict(extractor_args.get("youtube") or {})
        yt_args["player_client"] = clients
        extractor_args["youtube"] = yt_args
        out["extractor_args"] = extractor_args
    proxy = os.environ.get("YTDLP_PROXY")
    if proxy:
        out["proxy"] = proxy
    return out


# ── Cookie helper ─────────────────────────────────────────────────────────────
_COOKIE_PATH: str | None = None

def cookie_txt_file() -> str | None:
    """Return the decoded COOKIES_DATA path (cookies/cookie_0.txt).

    COOKIES_DATA always decodes to cookie_0.txt in __init__, so there is no
    need to glob the folder + pick a random file + append to logs.csv on every
    call (that was one disk read + one disk write per play). Resolve once and
    cache. Falls back to a glob only if cookie_0.txt is absent (legacy setups).
    """
    global _COOKIE_PATH
    if _COOKIE_PATH is not None:
        return _COOKIE_PATH
    base_dir = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.abspath(os.path.join(base_dir, "..", "cookies"))
    primary = os.path.join(folder, "cookie_0.txt")
    if os.path.exists(primary):
        _COOKIE_PATH = primary
        return primary
    # Legacy: no COOKIES_DATA decode yet — fall back to any .txt present.
    try:
        txt_files = glob.glob(os.path.join(folder, "*.txt"))
    except Exception:
        txt_files = []
    _COOKIE_PATH = txt_files[0] if txt_files else None
    return _COOKIE_PATH


# ── Link helpers ──────────────────────────────────────────────────────────────
def _normalize_youtube_link(
    link: str,
    base: str = "https://www.youtube.com/watch?v=",
) -> str:
    if not link:
        return ""
    cleaned = link.strip()
    if "youtube.com" not in cleaned and "youtu.be" not in cleaned:
        cleaned = base + cleaned
    cleaned = cleaned.split("&si=")[0].split("?si=")[0]
    if "&" in cleaned and "list=" not in cleaned:
        cleaned = cleaned.split("&")[0]
    return cleaned


def _extract_video_id(link: str) -> str | None:
    cleaned = _normalize_youtube_link(link)
    if not cleaned:
        return None
    if "v=" in cleaned:
        return cleaned.split("v=")[-1].split("&")[0]
    if "youtu.be/" in cleaned:
        return cleaned.split("youtu.be/")[-1].split("?")[0].split("&")[0]
    return cleaned if len(cleaned) == 11 else None


# ── Downloader 1: Cookies Base64 via yt-dlp ──────────────────────────────────
async def _cookies_download(link: str, media_type: str) -> str | None:
    """
    Priority 1: Download via yt-dlp using Base64 cookies (COOKIES_DATA env var).
    The YouTube class __init__ decodes COOKIES_DATA into cookies/cookie_0.txt.
    Falls back to any available cookie file in the cookies/ directory.
    Returns local file path on success, None on failure.
    """
    video_id  = _extract_video_id(link) or link
    ext       = "mp4" if media_type == "video" else "mp3"
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # De-duplicate concurrent fetches of the same video (foreground + background).
    async with _dl_lock(video_id):
        # Already downloaded (or left over from a prior run)? Reuse it.
        existing = _resolve_downloaded_file(video_id, ext)
        if existing:
            return existing

        cookie = cookie_txt_file()
        # Default to the anonymous yt-dlp path: the deploy server IP is 403'd by
        # googlevideo for signed-in (cookie) requests (logs 2026-07-13), while
        # the no-cookie download succeeds. Opt IN to cookies with
        # ALLOW_COOKIE_DOWNLOAD=1 only if anonymous gets bot-flagged.
        if not cookie or not os.environ.get("ALLOW_COOKIE_DOWNLOAD"):
            return None

        try:
            # Prune stale temp/residue left by a previous crashed run so yt-dlp
            # doesn't collide on its own "<id>.orig.<ext>" rename step.
            for suffix in (".part", ".ytdl"):
                _tmp = f"{file_path}{suffix}"
                if os.path.exists(_tmp):
                    try:
                        os.remove(_tmp)
                    except OSError:
                        pass
            _orig = os.path.join(DOWNLOAD_DIR, f"{video_id}.orig.{ext}")
            if os.path.exists(_orig):
                try:
                    os.remove(_orig)
                except OSError:
                    pass

            # Use a bare template ending in '.%(ext)s'. For audio the
            # FFmpegExtractAudio postprocessor then writes the final file as
            # downloads/<id>.mp3 (NOT <id>.mp3.mp3), avoiding the
            # "<id>.mp3 -> <id>.orig.mp3" rename collision that was crashing
            # the download chain. For video the merge step produces <id>.mp4.
            outtmpl = os.path.join(DOWNLOAD_DIR, f"{video_id}.%(ext)s")
            if media_type == "video":
                ydl_opts = {
                    "format":              "bestvideo[height<=720]+bestaudio/best[height<=720]",
                    "outtmpl":             outtmpl,
                    "quiet":               True,
                    "no_warnings":         True,
                    "cookiefile":          cookie,
                    "merge_output_format": "mp4",
                }
            else:
                ydl_opts = {
                    "format":       "bestaudio/best",
                    "outtmpl":      outtmpl,
                    "quiet":        True,
                    "no_warnings":  True,
                    "cookiefile":   cookie,
                    "postprocessors": [{
                        "key":              "FFmpegExtractAudio",
                        "preferredcodec":   "mp3",
                        "preferredquality": "192",
                    }],
                }

            loop = asyncio.get_event_loop()
            def _run():
                with yt_dlp.YoutubeDL(_with_js_runtime(ydl_opts)) as ydl:
                    ydl.download([_normalize_youtube_link(link)])

            await loop.run_in_executor(None, _run)

            result = _resolve_downloaded_file(video_id, ext)
            if result:
                logger.info("Cookies Base64 (yt-dlp) ✓ %s → %s", video_id, result)
                return result

            return None

        except Exception as exc:
            logger.warning("Cookies Base64 download failed for %s: %s", video_id, exc)
            return None


# ── Downloader 2: Railway YT API ─────────────────────────────────────────────
async def _railway_download(video_id: str, media_type: str) -> str | None:
    """
    Priority 2: Download via Railway self-hosted YouTube API proxy.
    Streams the media directly from the Railway endpoint to a local file.
    Returns local file path on success, None on failure.
    """
    if not RAILWAY_YT_API_URL or not RAILWAY_YT_API_KEY:
        return None

    ext        = "mp4" if media_type == "video" else "mp3"
    timeout_dl = 600   if media_type == "video" else 300
    file_path  = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-API-Key": str(RAILWAY_YT_API_KEY),
    }
    endpoints = ["play/video/hq", "play/video"] if media_type == "video" else ["play/audio"]

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            for endpoint in endpoints:
                media_url = f"{RAILWAY_YT_API_URL}/{endpoint}?id={video_id}"

                async with session.get(
                    media_url,
                    timeout=aiohttp.ClientTimeout(total=timeout_dl),
                    allow_redirects=True,
                ) as file_resp:
                    if file_resp.status != 200:
                        logger.warning(
                            "Railway YT API stream failed: status %s for %s",
                            file_resp.status, endpoint,
                        )
                        continue

                    with open(file_path, "wb") as fobj:
                        async for chunk in file_resp.content.iter_chunked(1024 * 1024):
                            fobj.write(chunk)

                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        logger.info("Railway YT API ✓ %s → %s", video_id, file_path)
                        return file_path

            return None

    except Exception as exc:
        logger.warning("Railway YT API download failed for %s: %s", video_id, exc)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return None


# ── Downloader 3: Shruti API ──────────────────────────────────────────────────
async def _shruti_download(video_id: str, media_type: str) -> str | None:
    """
    Priority 3: Download via Shruti API.
    GET {SHRUTI_API_URL}/download?url=<video_id>&type=audio|video&api_key=<key>
    Returns local file path on success, None on failure.
    """
    if not SHRUTI_API_KEY:
        return None

    ext         = "mp4" if media_type == "video" else "mp3"
    timeout_dl  = 600   if media_type == "video" else 300
    file_path   = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SHRUTI_API_URL}/download",
                params={"url": video_id, "type": media_type, "api_key": SHRUTI_API_KEY},
                timeout=aiohttp.ClientTimeout(total=timeout_dl),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Shruti API status %s for %s", resp.status, video_id)
                    return None
                with open(file_path, "wb") as fobj:
                    async for chunk in resp.content.iter_chunked(131072):
                        fobj.write(chunk)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            logger.info("Shruti API ✓ %s → %s", video_id, file_path)
            return file_path

        return None

    except Exception as exc:
        logger.warning("Shruti API error for %s: %s", video_id, exc)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return None


# ── Downloader 4: xBit API ────────────────────────────────────────────────────
async def _xbit_download(link: str, media_type: str) -> str | None:
    """
    Priority 4: Download via xBit / YTPROXY API.
    GET {YTPROXY_URL}/info/<video_id>  →  audio_url / video_url  →  stream download.
    Returns local file path on success, None on failure.
    """
    if not YTPROXY_URL or not YT_API_KEY:
        return None

    video_id = _extract_video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    ext         = "mp4" if media_type == "video" else "mp3"
    timeout_dl  = 600   if media_type == "video" else 300
    file_path   = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path

    headers = {
        "x-api-key": str(YT_API_KEY),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                f"{YTPROXY_URL}/info/{video_id}",
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.warning("xBit info failed: status %s", resp.status)
                    return None
                try:
                    data = await resp.json(content_type=None)
                except Exception as e:
                    logger.warning("xBit info returned invalid JSON: %s", e)
                    return None

            if data.get("status") != "success":
                logger.warning("xBit API error: %s", data.get("message", "unknown"))
                return None

            media_url = (
                data.get("video_url") if media_type == "video" else data.get("audio_url")
            )
            if not media_url:
                logger.warning("xBit: no %s_url in response", media_type)
                return None

            async with session.get(
                media_url,
                timeout=aiohttp.ClientTimeout(total=timeout_dl),
                allow_redirects=True,
            ) as file_resp:
                if file_resp.status != 200:
                    return None
                with open(file_path, "wb") as fobj:
                    async for chunk in file_resp.content.iter_chunked(1024 * 1024):
                        fobj.write(chunk)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            logger.info("xBit API ✓ %s → %s", video_id, file_path)
            return file_path

        return None

    except Exception as exc:
        logger.warning("xBit download failed for %s: %s", video_id, exc)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return None


# ── Downloader 5: yt-dlp without cookies (last resort) ───────────────────────
async def _ytdlp_nocookie_download(link: str, media_type: str) -> str | None:
    """
    Last-resort local yt-dlp download without any cookies.
    Used only when no cookie file is available.
    Returns local file path or None.
    """
    video_id  = _extract_video_id(link) or link
    ext       = "mp4" if media_type == "video" else "mp3"
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    async with _dl_lock(video_id):
        existing = _resolve_downloaded_file(video_id, ext)
        if existing:
            return existing

        try:
            # Bare template ending in '.%(ext)s' so the postprocessor produces
            # downloads/<id>.mp3 (not <id>.mp3.mp3).
            outtmpl = os.path.join(DOWNLOAD_DIR, f"{video_id}.%(ext)s")
            if media_type == "video":
                ydl_opts = {
                    "format":              "bestvideo[height<=720]+bestaudio/best[height<=720]",
                    "outtmpl":             outtmpl,
                    "quiet":               True,
                    "no_warnings":         True,
                    "merge_output_format": "mp4",
                }
            else:
                ydl_opts = {
                    "format":       "bestaudio/best",
                    "outtmpl":      outtmpl,
                    "quiet":        True,
                    "no_warnings":  True,
                    "postprocessors": [{
                        "key":              "FFmpegExtractAudio",
                        "preferredcodec":   "mp3",
                        "preferredquality": "192",
                    }],
                }

            loop = asyncio.get_event_loop()
            def _run():
                with yt_dlp.YoutubeDL(_with_js_runtime(ydl_opts)) as ydl:
                    ydl.download([_normalize_youtube_link(link)])

            await loop.run_in_executor(None, _run)

            result = _resolve_downloaded_file(video_id, ext)
            if result:
                logger.info("yt-dlp (no-cookie) ✓ %s → %s", video_id, result)
                return result

            return None

        except Exception as exc:
            logger.warning("yt-dlp (no-cookie) download failed for %s: %s", video_id, exc)
            return None


# ── Main download entrypoint ──────────────────────────────────────────────────
async def _download_with_fallback(
    link: str,
    media_type: str,
) -> tuple[str | None, str]:
    """
    Try all downloaders in priority order:
      1. Cookies Base64 (yt-dlp + COOKIES_DATA)
      2. yt-dlp without cookies (local download)
      3. Railway YT API (used last — rate-limited / dead-key prone)
    Returns (file_path, downloader_name)
    """
    video_id = _extract_video_id(link) or link

    # 1. Cookies Base64 (yt-dlp with decoded COOKIES_DATA)
    result = await _cookies_download(link, media_type)
    if result:
        return result, "cookies_b64"

    # 2. yt-dlp without cookies (local download)
    result = await _ytdlp_nocookie_download(link, media_type)
    if result:
        return result, "ytdlp"

    # 3. Railway YT API (last resort)
    result = await _railway_download(video_id, media_type)
    if result:
        return result, "railway"

    logger.error("All download methods failed for: %s", video_id)
    return None, "none"


# ── Public helpers (kept for backward compat with play.py / calls.py) ─────────
async def download_song(link: str, title: str | None = None) -> str | None:
    path, _ = await _download_with_fallback(link, "audio")
    return path


async def download_video(link: str, title: str | None = None) -> str | None:
    path, _ = await _download_with_fallback(link, "video")
    return path


# ── YouTube class ─────────────────────────────────────────────────────────────
class YouTube:
    def __init__(self):
        self.base     = "https://www.youtube.com/watch?v="
        self.regex    = r"(?:youtube\.com|youtu\.be)"
        self.listbase = "https://youtube.com/playlist?list="
        self.reg      = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        self.api      = None
        self.cookies_dir = os.path.join(os.path.dirname(__file__), "..", "cookies")

        # Load cookies into cookie_0.txt for yt-dlp. Supports COOKIES_DATA
        # (base64, tolerant of whitespace/padding) and COOKIES_FILE (path to a
        # raw Netscape or gzip'd file on disk — use this to avoid paste
        # truncation when deploying).
        self._load_cookies()

        self.dl_stats = {
            "total_requests": 0,
            "cookies_b64":    0,
            "railway":        0,
            "shruti":         0,
            "xbit":           0,
            "ytdlp":          0,
            "existing_files": 0,
            "failed":         0,
        }

    # ── Validators ────────────────────────────────────────────────────────────
    def valid(self, url: str) -> bool:
        return bool(re.search(self.regex, url))

    def invalid(self, url: str) -> bool:
        return not self.valid(url)

    # ── Cookie management ─────────────────────────────────────────────────────
    def _load_cookies(self) -> None:
        """Load YouTube cookies into cookie_0.txt for yt-dlp.

        Two sources, in order:
          - COOKIES_DATA: base64 (gzip or plain). Tolerant of surrounding
            whitespace and a missing '=' pad.
          - COOKIES_FILE: path to a raw Netscape cookies.txt OR a gzip'd file
            on disk. Prefer this when deploying to avoid paste truncation.
        """
        import base64, gzip

        def _write(decoded: str, src: str, gz: bool) -> None:
            os.makedirs(self.cookies_dir, exist_ok=True)
            with open(os.path.join(self.cookies_dir, "cookie_0.txt"), "w") as f:
                f.write(decoded)
            logger.info("Loaded cookies from %s (base64%s).", src,
                        " +gzip" if gz else "")

        # 1) COOKIES_DATA (base64)
        cookies_data = getattr(config, "COOKIES_DATA", None) or os.environ.get("COOKIES_DATA")
        if cookies_data:
            try:
                cd = cookies_data.strip().replace("\n", "").replace("\r", "").replace(" ", "")
                pad = (-len(cd)) % 4
                raw = base64.b64decode(cd + "=" * pad)
                if raw[:2] == b"\x1f\x8b":
                    decoded = gzip.decompress(raw).decode("utf-8")
                else:
                    decoded = raw.decode("utf-8")
                _write(decoded, "COOKIES_DATA", raw[:2] == b"\x1f\x8b")
                return
            except Exception as e:
                logger.error(
                    "Error decoding COOKIES_DATA (len=%d): %s — the blob is "
                    "likely truncated/corrupted in transit. Prefer COOKIES_FILE "
                    "(a cookies.txt path) to avoid paste truncation.",
                    len(cookies_data.strip()), e,
                )

        # 2) COOKIES_FILE (path on disk)
        cookies_file = getattr(config, "COOKIES_FILE", None) or os.environ.get("COOKIES_FILE")
        if cookies_file and os.path.exists(cookies_file):
            try:
                data = open(cookies_file, "rb").read()
                if data[:2] == b"\x1f\x8b":
                    decoded = gzip.decompress(data).decode("utf-8")
                else:
                    decoded = data.decode("utf-8")
                _write(decoded, "COOKIES_FILE", data[:2] == b"\x1f\x8b")
            except Exception as e:
                logger.error("Error reading COOKIES_FILE (%s): %s", cookies_file, e)

    async def save_cookies(self, urls: list) -> None:
        if not urls:
            return
        os.makedirs(self.cookies_dir, exist_ok=True)
        try:
            async with aiohttp.ClientSession() as session:
                for i, url in enumerate(urls):
                    if not url:
                        continue
                    try:
                        async with session.get(
                            url, timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status == 200:
                                content = await resp.text()
                                path = os.path.join(self.cookies_dir, f"cookies_{i}.txt")
                                with open(path, "w") as f:
                                    f.write(content)
                                logger.info("Saved cookies → %s", path)
                            else:
                                logger.warning("Cookie fetch failed %s (status %s)", url, resp.status)
                    except Exception as e:
                        logger.warning("Cookie error from %s: %s", url, e)
        except Exception as e:
            logger.warning("save_cookies error: %s", e)

    # ── URL utilities ─────────────────────────────────────────────────────────
    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            text = message.text or message.caption or ""
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        return text[entity.offset: entity.offset + entity.length]
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    # ── Metadata fetchers ─────────────────────────────────────────────────────
    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        r = (await results.next())["result"][0]
        title        = r["title"]
        duration_min = r["duration"]
        thumbnail    = r["thumbnails"][0]["url"].split("?")[0]
        vidid        = r["id"]
        duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str | None:
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            return r["title"]
        return None

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str | None:
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            return r["duration"]
        return None

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str | None:
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            return r["thumbnails"][0]["url"].split("?")[0]
        return None

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        results = VideosSearch(link, limit=1)
        for r in (await results.next())["result"]:
            track_details = {
                "title":        r["title"],
                "link":         r["link"],
                "vidid":        r["id"],
                "duration_min": r["duration"],
                "thumb":        r["thumbnails"][0]["url"].split("?")[0],
            }
            return track_details, r["id"]
        return None, None

    async def search(
        self,
        query: str,
        message_id: int,
        video: bool = False,
    ):
        """Search YouTube and return a Track dataclass or None.
        Prioritizes official studio versions, avoids remixes/covers/live etc.
        """
        from ishu.helpers._dataclass import Track

        avoid_keywords = [
            "remix", "cover", "live", "slowed", "reverb", "extended", "acoustic",
            "instrumental", "karaoke", "8d", "bass boosted", "nightcore", "edit"
        ]

        query_lower = query.strip().lower()
        explicit_avoid = any(kw in query_lower for kw in avoid_keywords)

        try:
            search_queries = [
                f"{query.strip()} official audio",
                f"{query.strip()} official video",
                query.strip()
            ] if not explicit_avoid else [query.strip()]

            for sq in search_queries:
                results = VideosSearch(sq, limit=10)
                raw_results = (await results.next())["result"]
                if not raw_results:
                    continue

                filtered = []
                for r in raw_results:
                    title_lower = r.get("title", "").lower()

                    if not explicit_avoid:
                        if any(kw in title_lower for kw in avoid_keywords):
                            continue

                    duration_str = r.get("duration") or "0:00"
                    parts = duration_str.split(":")
                    try:
                        if len(parts) == 3:
                            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        elif len(parts) == 2:
                            secs = int(parts[0]) * 60 + int(parts[1])
                        else:
                            secs = 0
                    except (ValueError, IndexError):
                        secs = 0

                    if 30 <= secs <= 3600:
                        filtered.append(r)

                if filtered:
                    r = filtered[0]
                    vidid = r["id"]
                    duration_min = r.get("duration") or "00:00"
                    duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
                    view_count = None
                    if "viewCount" in r and isinstance(r["viewCount"], dict):
                        view_count = r["viewCount"].get("short") or r["viewCount"].get("text")
                    return Track(
                        id           = vidid,
                        title        = r["title"],
                        url          = r.get("link", self.base + vidid),
                        duration     = duration_min,
                        duration_sec = duration_sec,
                        thumbnail    = r["thumbnails"][0]["url"].split("?")[0],
                        channel_name = (r.get("channel") or {}).get("name", ""),
                        message_id   = message_id,
                        video        = video,
                        time         = int(_time.time()),
                        view_count   = view_count,
                    )

            return None
        except Exception as e:
            logger.warning("YouTube search error for '%s': %s", query, e)
            return None

    # ── Slider ────────────────────────────────────────────────────────────────
    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link        = _normalize_youtube_link(link)
        search      = VideosSearch(link, limit=10)
        raw_results = (await search.next()).get("result", [])

        filtered = []
        for item in raw_results:
            duration_str = item.get("duration") or "0:00"
            parts = duration_str.split(":")
            try:
                if len(parts) == 3:
                    secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    secs = int(parts[0]) * 60 + int(parts[1])
                else:
                    secs = 0
            except (ValueError, IndexError):
                continue
            if 0 < secs <= 3600:
                filtered.append(item)

        if not filtered or query_type >= len(filtered):
            raise ValueError("No suitable videos found within duration limit")

        s = filtered[query_type]
        return s["title"], s.get("duration") or "0:00", s["thumbnails"][0]["url"].split("?")[0], s["id"]

    # ── Formats (yt-dlp) ──────────────────────────────────────────────────────
    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        ydl = yt_dlp.YoutubeDL(_with_js_runtime({"quiet": True}))
        with ydl:
            info = ydl.extract_info(link, download=False)
        formats_available = []
        for fmt in info.get("formats", []):
            try:
                if "dash" not in str(fmt["format"]).lower():
                    formats_available.append({
                        "format":      fmt["format"],
                        "filesize":    fmt.get("filesize"),
                        "format_id":   fmt["format_id"],
                        "ext":         fmt["ext"],
                        "format_note": fmt.get("format_note"),
                        "yturl":       link,
                    })
            except Exception:
                continue
        return formats_available, link

    # ── Video stream URL (yt-dlp, no download) ────────────────────────────────
    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = _normalize_youtube_link(link)
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--js-runtimes", "node", "-g",
            "-f", "best[height<=?720][width<=?1280]", link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 0, "yt-dlp video extract timed out"
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        return 0, stderr.decode()

    async def get_related(self, video_id: str, message_id: int) -> "Track | None":
        """Return a RELATED Track for autoplay (NOT the same song).

        Uses yt-dlp to pull YouTube's up-next 'related_videos' of the current
        track and returns the first usable one as a Track. This guarantees a
        *different* track (YouTube's own recommendations) instead of
        re-searching the current title (which just returns the same song).

        FIX (autoplay was broken): the old call used ``extract_flat=True``,
        which SKIPS the watch_next browse request that populates
        ``related_videos`` — so it always returned ``None`` and forced the
        caller into its title-search fallback (same song). A normal (non-flat)
        extract is used here so the related list is actually filled.
        """
        link = self.base + video_id
        loop = asyncio.get_event_loop()
        def _run():
            try:
                # NOTE: extract_flat=True omits the watch_next browse request,
                # so YouTube never returns 'related_videos'. Use a normal
                # extract (player-client bypass from _with_js_runtime helps
                # dodge the bot-check) so the up-next list is populated.
                with yt_dlp.YoutubeDL(_with_js_runtime({
                    "quiet": True,
                    "no_warnings": True,
                })) as ydl:
                    info = ydl.extract_info(link, download=False) or {}
                rel = info.get("related_videos") or []
                # Skip the finished video itself and any playlist/mix/channel
                # entries (no usable single-video id / duration).
                for r in rel:
                    rid = r.get("id")
                    if not rid or rid == video_id:
                        continue
                    if "list=" in (r.get("url") or ""):
                        continue
                    if r.get("duration") is None and not r.get("title"):
                        continue
                    return r
                return None
            except Exception as e:
                logger.warning("get_related failed for %s: %s", video_id, e)
                return None
        try:
            r = await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=20)
        except asyncio.TimeoutError:
            logger.warning("get_related timed out for %s", video_id)
            return None
        if not r:
            return None
        rid = r.get("id")
        if not rid:
            return None
        duration_min = r.get("duration") or "00:00"
        try:
            duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
        except Exception:
            duration_sec = 0
        return Track(
            id=rid,
            title=r.get("title", "Unknown"),
            url=r.get("url", self.base + rid),
            duration=duration_min,
            duration_sec=duration_sec,
            thumbnail=(r.get("thumbnails") or [{}])[0].get("url", "").split("?")[0],
            channel_name=r.get("channel") or r.get("uploader") or "",
            message_id=message_id,
            video=False,
            time=int(_time.time()),
        )

    async def get_related_candidates(self, video_id: str, limit: int = 15) -> list[Track]:
        """Return a list of RELATED Tracks for autoplay candidate pool."""
        link = self.base + video_id
        loop = asyncio.get_event_loop()

        def _run():
            try:
                opts = {
                    "quiet": True,
                    "no_warnings": True,
                }
                cookie = cookie_txt_file()
                if cookie:
                    opts["cookiefile"] = cookie
                with yt_dlp.YoutubeDL(_with_js_runtime(opts)) as ydl:
                    info = ydl.extract_info(link, download=False) or {}
                rel = info.get("related_videos") or []
                candidates = []
                for r in rel:
                    rid = r.get("id")
                    if not rid or rid == video_id:
                        continue
                    if "list=" in (r.get("url") or ""):
                        continue
                    if r.get("duration") is None and not r.get("title"):
                        continue
                    candidates.append(r)
                    if len(candidates) >= limit:
                        break
                return candidates
            except Exception as e:
                logger.warning("get_related_candidates failed for %s: %s", video_id, e)
                return []

        try:
            raw_list = await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=20)
        except asyncio.TimeoutError:
            logger.warning("get_related_candidates timed out for %s", video_id)
            raw_list = []

        tracks = []
        for r in raw_list:
            rid = r.get("id")
            if not rid:
                continue
            dur_val = r.get("duration")
            if isinstance(dur_val, (int, float)):
                m, s = divmod(int(dur_val), 60)
                h, m = divmod(m, 60)
                duration_min = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                duration_sec = int(dur_val)
            else:
                duration_min = str(dur_val or "00:00")
                try:
                    duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
                except Exception:
                    duration_sec = 0

            tracks.append(
                Track(
                    id=rid,
                    title=r.get("title", "Unknown Track"),
                    url=r.get("url", self.base + rid),
                    duration=duration_min,
                    duration_sec=duration_sec,
                    thumbnail=(r.get("thumbnails") or [{}])[0].get("url", "").split("?")[0],
                    channel_name=r.get("channel") or r.get("uploader") or "",
                    message_id=0,
                    video=False,
                    time=int(_time.time()),
                )
            )
        return tracks

    async def search_similar_candidates(self, query: str, limit: int = 10) -> list[Track]:
        """Search YouTube for similar candidate tracks for autoplay fallback."""
        try:
            results = VideosSearch(query, limit=limit)
            raw_results = (await results.next()).get("result", [])
            tracks = []
            for r in raw_results:
                vidid = r.get("id")
                if not vidid:
                    continue
                duration_min = r.get("duration") or "00:00"
                try:
                    duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
                except Exception:
                    duration_sec = 0

                tracks.append(
                    Track(
                        id=vidid,
                        title=r.get("title", "Unknown Track"),
                        url=r.get("link", self.base + vidid),
                        duration=duration_min,
                        duration_sec=duration_sec,
                        thumbnail=(r.get("thumbnails") or [{}])[0].get("url", "").split("?")[0],
                        channel_name=(r.get("channel") or {}).get("name", ""),
                        message_id=0,
                        video=False,
                        time=int(_time.time()),
                    )
                )
            return tracks
        except Exception as e:
            logger.warning("search_similar_candidates error for '%s': %s", query, e)
            return []

    async def get_stream_url(
        self,
        video_id: str,
        video: bool = False,
        force_cookies: bool = False,
    ) -> str | None:
        """
        Get a direct stream URL for instant playback (no download).

        Method 1 — Cookies Base64 (yt-dlp extract_info):
          Uses COOKIES_DATA-decoded cookie file for authenticated access.
          Returns a direct googlevideo.com URL valid for ~6 hours.
          Cookies are attached when ALLOW_COOKIE_DOWNLOAD=1 OR force_cookies
          (used by autoplay to stream live without downloading).

        Method 2 — Railway API:
          Returns the Railway proxy endpoint URL and validates it with
          a HEAD request before returning to avoid silent failures.
        """
        link = _normalize_youtube_link(video_id, self.base)

        # Decide cookie attachment up front. The cookie scan + signed-in
        # extract is skipped entirely for the default (anonymous) path, so a
        # normal play doesn't pay for a glob + 30s-bound extract attempt that
        # the flagged IP will only reject anyway.
        cookie = cookie_txt_file()
        use_cookies = bool(cookie) and (
            force_cookies or os.environ.get("ALLOW_COOKIE_DOWNLOAD")
        )

        # ── Method 1: yt-dlp stream-URL extract (anonymous or cookie) ────────
        try:
            ydl_opts = {
                "format": (
                    "bestvideo[height<=720]+bestaudio/best[height<=720]"
                    if video else "bestaudio/best"
                ),
                "quiet":       True,
                "no_warnings": True,
            }
            ydl_opts = _with_js_runtime(ydl_opts)

            loop = asyncio.get_event_loop()
            def _run():
                _opts = dict(ydl_opts)
                if use_cookies:
                    _opts["cookiefile"] = cookie
                with yt_dlp.YoutubeDL(_with_js_runtime(_opts)) as ydl:
                    info = ydl.extract_info(link, download=False)
                    # For merged formats, prefer the best audio URL
                    if info.get("url"):
                        return info["url"]
                    formats = info.get("formats") or []
                    if formats:
                        return formats[-1].get("url")
                    return None

            # Anonymous extract needs a tight cap: a hang here just delays the
            # (working) download fallback. Cookies path keeps a little more room.
            url = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=18 if not use_cookies else 30,
            )
            if url:
                logger.info(
                    "Stream URL via %s: %s",
                    "Cookies Base64" if use_cookies else "yt-dlp",
                    video_id,
                )
                return url
        except asyncio.TimeoutError:
            logger.warning("get_stream_url yt-dlp timed out for %s", video_id)
        except Exception as e:
            logger.warning("get_stream_url yt-dlp failed for %s: %s", video_id, e)

        # ── Method 2: Railway API (validated) ────────────────────────────────
        if RAILWAY_YT_API_URL and RAILWAY_YT_API_KEY:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "X-API-Key": str(RAILWAY_YT_API_KEY),
                }
                endpoint  = "play/video/hq" if video else "play/audio"
                media_url = f"{RAILWAY_YT_API_URL}/{endpoint}?id={video_id}"

                # Validate the endpoint responds before returning it as stream URL
                async with aiohttp.ClientSession(headers=headers) as session:
                    # NOTE: the Railway proxy rejects HEAD requests (405), so we
                    # validate with a GET but release the body immediately — we only
                    # need the HTTP status to confirm the endpoint serves media.
                    async with session.get(
                        media_url,
                        timeout=aiohttp.ClientTimeout(total=10),
                        allow_redirects=True,
                    ) as resp:
                        status = resp.status
                        resp.release()  # drop the connection without reading media
                        if status in (200, 206):
                            logger.info("Stream URL via Railway API: %s", video_id)
                            return media_url
                        else:
                            logger.warning(
                                "Railway stream URL validation failed: status %s",
                                status,
                            )
            except Exception as e:
                logger.warning("Railway get_stream_url failed: %s", e)

        return None

    async def _store_in_dump(self, file_path: str, video_id: str, is_video: bool = False) -> None:
        """Upload downloaded track to STORAGE_GROUP_ID and save file_id + shared msg_id in MongoDB."""
        try:
            from ishu import app, db
            storage_id = getattr(config, "STORAGE_GROUP_ID", 0) or getattr(config, "LOGGER_ID", 0)
            if not storage_id:
                return

            if is_video:
                msg = await app.send_video(storage_id, video=file_path, caption=f"#VIDEO #{video_id}")
                file_id = msg.video.file_id if msg and msg.video else None
            else:
                msg = await app.send_audio(storage_id, audio=file_path, caption=f"#AUDIO #{video_id}")
                file_id = msg.audio.file_id if msg and msg.audio else None

            if msg and msg.id:
                if file_id:
                    await db.save_song_file_id(video_id, file_id, is_video)
                await db.save_shared_song(video_id, msg.id, is_video)
                logger.info("Stored %s in dump channel (%s) -> msg_id: %s", video_id, storage_id, msg.id)
        except Exception as e:
            logger.warning("Failed to store %s in dump channel: %s", video_id, e)


    # ── Download (main method called by play.py / calls.py) ──────────────────
    async def download(
        self,
        video_id: str,
        video: bool = False,
        title: str | None = None,
    ) -> str | None:
        """
        Download audio/video by video_id using Telegram file_id cache, shared MongoDB Atlas msg_id cache, or Railway YT API.
        Returns file path or None.
        """
        from ishu import app, db
        self.dl_stats["total_requests"] += 1
        ext = "mp4" if video else "mp3"
        target_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")

        # Check local disk cache first
        if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
            return target_path

        # Check bot-local Telegram file_id cache in MongoDB
        try:
            cached_file_id = await db.get_song_file_id(video_id, video)
            if cached_file_id:
                logger.info("Telegram Dump Cache HIT for %s (file_id: %s...)", video_id, cached_file_id[:15])
                downloaded_path = await app.download_media(cached_file_id, file_name=target_path)
                if downloaded_path and os.path.exists(downloaded_path) and os.path.getsize(downloaded_path) > 0:
                    self.dl_stats["telegram_cache"] = self.dl_stats.get("telegram_cache", 0) + 1
                    return str(downloaded_path)
        except Exception as cache_err:
            logger.warning("Telegram file_id cache fetch failed for %s: %s", video_id, cache_err)

        # Check SHARED MongoDB msg_id cache across ALL 8 BOTS!
        try:
            shared_doc = await db.get_shared_song(video_id, video)
            if shared_doc and shared_doc.get("msg_id"):
                msg_id = shared_doc["msg_id"]
                storage_id = shared_doc.get("channel_id") or getattr(config, "STORAGE_GROUP_ID", -1003913556820)
                logger.info("Shared MongoDB Cache HIT for %s (channel: %s, msg_id: %s)", video_id, storage_id, msg_id)
                msg = await app.get_messages(storage_id, msg_id)
                if msg and (msg.audio or msg.video or msg.document):
                    downloaded_path = await app.download_media(msg, file_name=target_path)
                    if downloaded_path and os.path.exists(downloaded_path) and os.path.getsize(downloaded_path) > 0:
                        file_id = (msg.audio or msg.video or msg.document).file_id
                        await db.save_song_file_id(video_id, file_id, video)
                        self.dl_stats["shared_cache"] = self.dl_stats.get("shared_cache", 0) + 1
                        return str(downloaded_path)
        except Exception as shared_err:
            logger.warning("Shared MongoDB cache fetch failed for %s: %s", video_id, shared_err)

        # Fallback to YouTube extraction & download
        link = _normalize_youtube_link(video_id, self.base)
        try:
            result, downloader = await _download_with_fallback(
                link, "video" if video else "audio"
            )
            if result:
                self.dl_stats[downloader] = self.dl_stats.get(downloader, 0) + 1
                logger.info(
                    "YouTube.download success: %s (%s) via %s",
                    video_id,
                    "video" if video else "audio",
                    downloader,
                )
                # Store downloaded file in Telegram Dump Channel for instant future plays across ALL bots
                asyncio.create_task(self._store_in_dump(result, video_id, video))
            else:
                self.dl_stats["failed"] += 1
            return result
        except Exception as e:
            self.dl_stats["failed"] += 1
            logger.warning("YouTube.download error for '%s': %s", video_id, e)
            return None

    # ── Playlist ──────────────────────────────────────────────────────────────
    async def playlist(
        self,
        limit: int,
        mention: str,
        link: str,
        video: bool = False,
    ) -> list:
        """Fetch playlist tracks, return list of Track dataclasses."""
        from ishu.helpers._dataclass import Track

        link = _normalize_youtube_link(link)
        try:
            plist = await Playlist.get(link)
        except Exception:
            return []

        tracks = []
        for data in (plist.get("videos") or [])[:limit]:
            if not data:
                continue
            vidid = data.get("id")
            if not vidid:
                continue
            duration_min = data.get("duration") or "00:00"
            duration_sec = int(utils.to_seconds(duration_min)) if duration_min else 0
            thumbs       = data.get("thumbnails") or []
            thumbnail    = thumbs[0].get("url", "").split("?")[0] if thumbs else ""
            view_count = None
            if "viewCount" in data and isinstance(data["viewCount"], dict):
                view_count = data["viewCount"].get("short") or data["viewCount"].get("text")
            channel_name = (data.get("channel") or {}).get("name", "")
            tracks.append(Track(
                id           = vidid,
                title        = data.get("title") or vidid,
                url          = data.get("link") or self.base + vidid,
                duration     = duration_min,
                duration_sec = duration_sec,
                thumbnail    = thumbnail,
                user         = mention,
                video        = video,
                time         = int(_time.time()),
                view_count   = view_count,
                channel_name = channel_name,
            ))
        return tracks
