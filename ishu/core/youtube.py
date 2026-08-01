# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic
#
# Download and Streaming Method:
#   - Railway YT API  (RAILWAY_YT_API_URL / RAILWAY_YT_API_KEY)
#

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
RAILWAY_YT_API_URL  = getattr(config, "RAILWAY_YT_API_URL",  None)
RAILWAY_YT_API_KEY  = getattr(config, "RAILWAY_YT_API_KEY",  None)

DOWNLOAD_DIR        = "downloads"

# Per-video_id locks so the foreground download() and the background prefetch
# task never run yt-dlp on the SAME video_id concurrently. Two concurrent
# yt-dlp processes writing the same "<id>.mp3.part" / "<id>.orig.mp3" temp
# files cause "Unable to rename file: [Errno 2]" crashes.
_dl_locks: "dict[str, asyncio.Lock]" = {}

# Download context used to enrich the error-log message sent to the log group.
_DL_CTX: "dict" = {}


def set_dl_context(
    chat_id: "int | None" = None,
    chat_title: "str | None" = None,
    title: "str | None" = None,
    video: bool = False,
) -> None:
    """Store group/song context for the next download failure report."""
    global _DL_CTX
    _DL_CTX = {
        "chat_id": chat_id,
        "chat_title": chat_title,
        "title": title,
        "video": video,
    }


async def _notify_download_failure(video_id: str, media_type: str) -> None:
    """Best-effort: forward a total download failure to the configured log group."""
    from ishu.helpers import utils
    if not getattr(config, "LOGGER_ID", 0):
        return
    try:
        ctx = dict(_DL_CTX)
        await utils.error_log(
            context=f"Download ({'video' if media_type == 'video' else 'audio'})",
            error=f"All download methods failed for video_id: {video_id}",
            chat_id=ctx.get("chat_id"),
            chat_title=ctx.get("chat_title"),
            title=ctx.get("title") or video_id,
            video=ctx.get("video", media_type == "video"),
        )
    except Exception as e:
        logger.warning("Failed to forward download failure to log group: %s", e)


def _dl_lock(video_id: str) -> asyncio.Lock:
    lock = _dl_locks.get(video_id)
    if lock is None:
        lock = asyncio.Lock()
        _dl_locks[video_id] = lock
    return lock


def _release_dl_lock(video_id: str) -> None:
    """Clean up lock object from dictionary to prevent memory leaks."""
    if video_id in _dl_locks and not _dl_locks[video_id].locked():
        _dl_locks.pop(video_id, None)


def _evict_disk_cache(max_mb: int = 3000) -> None:
    """LRU-evict oldest files from DOWNLOAD_DIR if total size exceeds max_mb."""
    if not os.path.exists(DOWNLOAD_DIR):
        return
    try:
        max_bytes = max_mb * 1024 * 1024
        entries = []
        total = 0
        for name in os.listdir(DOWNLOAD_DIR):
            p = os.path.join(DOWNLOAD_DIR, name)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
                entries.append((st.st_atime, st.st_size, p))
                total += st.st_size
            except Exception:
                continue
        if total <= max_bytes:
            return
        entries.sort(key=lambda e: e[0])
        for _atime, size, p in entries:
            if total <= max_bytes:
                break
            try:
                os.remove(p)
                total -= size
                logger.info("LRU disk cache evicted: %s (%s bytes)", p, size)
            except Exception:
                pass
    except Exception as e:
        logger.warning("LRU disk cache eviction error: %s", e)


_SESSION: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        connector = aiohttp.TCPConnector(
            limit=100,
            ttl_dns_cache=300,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
        )
        _SESSION = aiohttp.ClientSession(connector=connector)
    return _SESSION


# YT-dlp gets occasionally blocked by YouTube's bot check.
# If COOKIES_FILE or COOKIES_DATA is configured, decode/derive a cookie text
# file that yt-dlp can load via STANDARD cookie file auth header auth semantics.
_COOKIE_PATH: str | None = None


def cookie_txt_file() -> str | None:
    """Return the best available Netscape-format cookie file path for yt-dlp."""
    global _COOKIE_PATH
    if _COOKIE_PATH is not None:
        return _COOKIE_PATH
    base_dir = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.abspath(os.path.join(base_dir, "..", "cookies"))
    primary = os.path.join(folder, "cookie_0.txt")
    if os.path.exists(primary):
        _COOKIE_PATH = primary
        return primary
    try:
        txt_files = glob.glob(os.path.join(folder, "*.txt"))
    except Exception:
        txt_files = []
    _COOKIE_PATH = txt_files[0] if txt_files else None
    return _COOKIE_PATH


def _resolve_downloaded_file(video_id: str, ext: str) -> str | None:
    """
    Find the actual file produced by yt-dlp for `video_id`.
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

    proxy = os.environ.get("YTDLP_PROXY") or os.environ.get("HTTPS_PROXY")
    if proxy:
        out["proxy"] = proxy
    return out



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


# ── Downloader: Railway YT API + Direct yt-dlp Fallback ───────────────────
async def _railway_download(video_id: str, media_type: str) -> str | None:
    """
    Download via Railway self-hosted YouTube API proxy.
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
        try:
            os.utime(file_path, None)
        except Exception:
            pass
        return file_path

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-API-Key": str(RAILWAY_YT_API_KEY),
    }
    endpoints = ["play/video/hq", "play/video"] if media_type == "video" else ["play/audio"]

    try:
        session = _get_http_session()
        for endpoint in endpoints:
            media_url = f"{RAILWAY_YT_API_URL}/{endpoint}?id={video_id}"
            try:
                async with session.get(
                    media_url,
                    headers=headers,
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
                        async for chunk in file_resp.content.iter_chunked(512 * 1024):
                            fobj.write(chunk)

                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        _evict_disk_cache()
                        logger.info("Railway YT API ✓ %s → %s", video_id, file_path)
                        return file_path
            except Exception as ep_err:
                logger.warning("Railway YT API endpoint %s failed for %s: %s", endpoint, video_id, ep_err)

        return None

    except Exception as exc:
        logger.warning("Railway YT API download failed for %s: %s", video_id, exc)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass
        return None


async def _direct_ytdlp_download(video_id: str, media_type: str) -> str | None:
    """Fast direct yt-dlp fallback with multi-threaded fragment downloads (-N 4)."""
    ext = "mp4" if media_type == "video" else "mp3"
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    link = f"https://www.youtube.com/watch?v={video_id}"

    cmd = [
        "yt-dlp",
        "--js-runtimes", "node",
        "-N", "4",
        "--buffer-size", "16k",
        "--no-playlist",
        "--no-warnings",
        "-q",
    ]
    # YouTube bot-checks the default `web` client hardest; the mobile/TV clients
    # (tv, ios, android, web_safari, mweb) routinely bypass the "Sign in to
    # confirm you're not a bot" check with no cookies or proxy needed. Tune the
    # list via the YT_PLAYER_CLIENTS env var (comma-separated).
    _clients = [
        c.strip()
        for c in os.environ.get("YT_PLAYER_CLIENTS", _DEFAULT_PLAYER_CLIENTS).split(",")
        if c.strip()
    ]
    if _clients:
        cmd += ["--extractor-args", f"youtube:player_client={','.join(_clients)}"]
    cookie = cookie_txt_file()
    if cookie:
        cmd.extend(["--cookies", cookie])

    if media_type == "video":
        cmd.extend(["-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best", "--merge-output-format", "mp4"])
    else:
        cmd.extend(["-x", "--audio-format", "mp3", "--audio-quality", "0"])

    cmd.extend(["-o", file_path, link])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        resolved = _resolve_downloaded_file(video_id, ext)
        if resolved:
            _evict_disk_cache()
            return resolved
        logger.warning("Direct yt-dlp download returned no file for %s: %s", video_id, stderr.decode())
    except Exception as e:
        logger.warning("Direct yt-dlp download failed for %s: %s", video_id, e)
    return None


# ── Main download entrypoint ──────────────────────────────────────────────────
async def _download_with_fallback(
    link: str,
    media_type: str,
) -> tuple[str | None, str]:
    """
    Download using Railway YT API -> Fallback to direct fast multi-threaded yt-dlp.
    Returns (file_path, downloader_name)
    """
    video_id = _extract_video_id(link) or link

    result = await _railway_download(video_id, media_type)
    if result:
        return result, "railway"

    logger.info("Railway YT API unavailable/failed for %s. Trying fast direct yt-dlp fallback.", video_id)
    result = await _direct_ytdlp_download(video_id, media_type)
    if result:
        return result, "yt-dlp"

    logger.error("Download failed for: %s", video_id)
    await _notify_download_failure(video_id, media_type)
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

        self.dl_stats = {
            "total_requests": 0,
            "railway":        0,
            "failed":         0,
        }

    # ── Validators ────────────────────────────────────────────────────────────
    def valid(self, url: str) -> bool:
        return bool(re.search(self.regex, url))

    def invalid(self, url: str) -> bool:
        return not self.valid(url)

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


    async def _raw_cold_download(self, video_id: str, video: bool = False) -> str | None:
        """Internal helper for cold YouTube download when cache is completely missing."""
        link = _normalize_youtube_link(video_id, self.base)
        try:
            result, downloader = await _download_with_fallback(
                link, "video" if video else "audio"
            )
            if result:
                self.dl_stats[downloader] = self.dl_stats.get(downloader, 0) + 1
                logger.info(
                    "Cold YouTube.download success: %s (%s) via %s",
                    video_id,
                    "video" if video else "audio",
                    downloader,
                )
            else:
                self.dl_stats["failed"] += 1
            return result
        except Exception as e:
            self.dl_stats["failed"] += 1
            logger.warning("Cold YouTube.download error for '%s': %s", video_id, e)
            return None

    # ── Download (main method called by play.py / calls.py) ──────────────────
    async def download(
        self,
        video_id: str,
        video: bool = False,
        title: str | None = None,
    ) -> str | None:
        """
        Download audio/video by video_id using ultra-fast HybridCacheManager.
        Priority: Local SSD (50-300ms) -> Telegram Dump Backup (1-3s) -> Cold YT Download (5-20s).
        Returns file path or None.
        """
        from ishu.core.cache_manager import cache_manager

        self.dl_stats["total_requests"] += 1

        async def _dl_wrapper(vid: str, is_vid: bool):
            return await self._raw_cold_download(vid, is_vid)

        return await cache_manager.get_or_fetch(
            video_id=video_id,
            title=title or "",
            duration=0,
            is_video=video,
            downloader_fn=_dl_wrapper,
        )

    async def prefetch_song(
        self,
        video_id: str,
        title: str | None = None,
        video: bool = False,
    ) -> None:
        """Background prefetch for upcoming songs in queue."""
        from ishu.core.cache_manager import cache_manager

        async def _dl_wrapper(vid: str, is_vid: bool):
            return await self._raw_cold_download(vid, is_vid)

        await cache_manager.prefetch_song(
            video_id=video_id,
            title=title or "",
            is_video=video,
            downloader_fn=_dl_wrapper,
        )

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
