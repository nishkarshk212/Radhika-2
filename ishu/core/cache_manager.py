# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# Ultra-Fast Hybrid Cache System for Telegram Music Bot

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import pyrogram.errors as _pg_errors

from ishu import config, logger, app, db, userbot

# ── Silence pyrogram.client FileReferenceExpired noise ────────────────────────
# pyrogram logs this at ERROR level internally before our except block catches
# it. Filtering it here keeps logs clean without losing real errors.
class _SuppressFileRefFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "FILE_REFERENCE" not in record.getMessage()

logging.getLogger("pyrogram.client").addFilter(_SuppressFileRefFilter())

CACHE_DIR = getattr(config, "CACHE_DIR", "cache")
MAX_CACHE_GB = float(getattr(config, "MAX_CACHE_GB", 100))

# Per-video_id in-flight single-flight locks to guarantee zero duplicate downloads
_video_locks: dict[str, asyncio.Lock] = {}


def get_video_lock(video_id: str) -> asyncio.Lock:
    """Return an asyncio.Lock for the given video_id."""
    if video_id not in _video_locks:
        _video_locks[video_id] = asyncio.Lock()
    return _video_locks[video_id]


def release_video_lock(video_id: str) -> None:
    """Clean up lock to prevent memory leaks."""
    lock = _video_locks.get(video_id)
    if lock and not lock.locked():
        _video_locks.pop(video_id, None)


class HybridCacheManager:
    """
    Ultra-fast 3-tier Hybrid Cache Manager:
    Tier 1 (Hot):  Local SSD Cache (/cache/video_id.mp3)  → 50-300ms playback
    Tier 2 (Warm): Telegram Dump Channel Backup           → 1-3s restoration
    Tier 3 (Cold): YouTube Downloader                     → 5-20s initial fetch

    Guarantees zero duplicate YouTube downloads if metadata exists in MongoDB.

    FileReferenceExpired self-healing strategy
    ------------------------------------------
    Telegram file_ids embed a short-lived file_reference (~1 hour) that is
    bot-token-specific. When multiple bots share one dump channel + MongoDB:

      1. message_id path (primary)   — always fresh, bot-independent.
         Gets message → downloads media → persists new file_id to MongoDB.
      2. file_id path (fallback)     — works within the reference lifetime.
         On FileReferenceExpired → falls back to (3).
      3. userbot message_id path     — assistant session has its own reference.
         Tried when bot session fails to resolve the message.
      4. Self-heal — delete stale MongoDB record so next request triggers a
         clean cold download that re-uploads + re-stores a valid message_id.
    """

    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _get_local_path(self, video_id: str, is_video: bool = False) -> str:
        ext = "mp4" if is_video else "mp3"
        dl_path = os.path.join("downloads", f"{video_id}.{ext}")
        if os.path.exists(dl_path) and os.path.getsize(dl_path) > 0:
            return dl_path
        return os.path.join(CACHE_DIR, f"{video_id}.{ext}")

    def is_local_cached(self, video_id: str, is_video: bool = False) -> bool:
        """Check if local SSD file exists in downloads/ or cache/ and is non-empty."""
        path = self._get_local_path(video_id, is_video)
        return os.path.exists(path) and os.path.getsize(path) > 0

    async def get_or_fetch(
        self,
        video_id: str,
        title: str = "",
        duration: int = 0,
        is_video: bool = False,
        added_by: int = 0,
        downloader_fn=None,
    ) -> str | None:
        """
        Main entry point for song playback resolution.
        Returns the absolute local SSD file path or None on failure.
        """
        local_path = self._get_local_path(video_id, is_video)
        warm_restore_failed = False  # skip MongoDB re-check in Step 2 if warm restore failed

        # ── Step 1: Query MongoDB Metadata ──────────────────────────────────
        doc = await db.get_music_cache(video_id, is_video)

        if doc:
            logger.info("MongoDB Cache HIT for video_id: %s", video_id)

            # HOT CACHE — file already on local SSD
            if self.is_local_cached(video_id, is_video):
                logger.info("[HOT CACHE SSD] Playing %s immediately from local SSD.", video_id)
                try:
                    os.utime(local_path, None)
                except Exception:
                    pass
                asyncio.create_task(db.update_music_stats(video_id, is_video))
                return local_path

            # WARM CACHE — restore from Telegram dump channel
            logger.info(
                "[WARM CACHE RESTORE] Local SSD missing for %s. Restoring from Telegram dump...",
                video_id,
            )
            restored = await self._restore_from_telegram(doc, local_path)
            if restored:
                asyncio.create_task(db.update_music_stats(video_id, is_video))
                asyncio.create_task(self.enforce_lru_eviction())
                return local_path
            else:
                # Both file_id and message_id failed — self-heal by deleting
                # the stale document so Step 2 does a clean cold download.
                logger.warning(
                    "[WARM CACHE FAIL] All restore attempts exhausted for %s. "
                    "Deleting stale MongoDB record → will re-download and re-upload.",
                    video_id,
                )
                asyncio.create_task(db.delete_music_cache(video_id, is_video))
                warm_restore_failed = True

        # ── Step 2: Cold Cache — download from YouTube ───────────────────────
        lock = get_video_lock(video_id)
        async with lock:
            try:
                # Re-check MongoDB only when we didn't already try warm restore.
                # If warm_restore_failed is True, the document was just invalidated.
                if not warm_restore_failed:
                    doc_recheck = await db.get_music_cache(video_id, is_video)
                    if doc_recheck:
                        if self.is_local_cached(video_id, is_video):
                            asyncio.create_task(db.update_music_stats(video_id, is_video))
                            return local_path
                        restored = await self._restore_from_telegram(doc_recheck, local_path)
                        if restored:
                            asyncio.create_task(db.update_music_stats(video_id, is_video))
                            return local_path
                        # Same self-heal for the inner re-check path
                        asyncio.create_task(db.delete_music_cache(video_id, is_video))

                if not downloader_fn:
                    logger.error(
                        "No downloader function provided for cold cache download of %s", video_id
                    )
                    return None

                logger.info("[COLD CACHE DOWNLOAD] Fetching %s from YouTube API...", video_id)
                dl_result = await downloader_fn(video_id, is_video)
                if not dl_result or not os.path.exists(dl_result) or os.path.getsize(dl_result) == 0:
                    logger.error("Cold YouTube download failed for %s", video_id)
                    return None

                file_size = os.path.getsize(dl_result)

                # Upload MP3/MP4 to Telegram Dump Channel as permanent backup
                dump_meta = await self._upload_to_telegram_dump(dl_result, video_id, title, is_video)

                # Persist metadata to MongoDB (single source of truth)
                channel_id = getattr(config, "STORAGE_GROUP_ID", 0) or getattr(config, "LOGGER_ID", 0)
                await db.save_music_cache(
                    video_id=video_id,
                    title=title or video_id,
                    duration=duration,
                    file_path=local_path,
                    file_size=file_size,
                    file_id=dump_meta.get("file_id", ""),
                    file_unique_id=dump_meta.get("file_unique_id", ""),
                    message_id=dump_meta.get("message_id", 0),
                    channel_id=channel_id,
                    added_by=added_by,
                    is_video=is_video,
                )

                asyncio.create_task(self.enforce_lru_eviction())
                return dl_result

            finally:
                release_video_lock(video_id)

    # ── Warm Cache Restore ────────────────────────────────────────────────────

    async def _restore_from_telegram(self, doc: dict, target_path: str) -> bool:
        """
        Restore a missing local file from the Telegram dump channel.

        Attempt order (most reliable → least reliable):
          1. bot session  + message_id   — fresh file_reference, bot-independent
          2. bot session  + file_id      — fast when within reference lifetime
             → on FileReferenceExpired: retry via message_id
          3. userbot session + message_id — independent session, own reference
          4. userbot session + file_id

        On ANY success: persist refreshed file_id + message_id to MongoDB.
        Returns True on success, False when all attempts exhausted.
        """
        file_id   = doc.get("file_id") or ""
        channel_id = doc.get("channel_id") or getattr(config, "STORAGE_GROUP_ID", 0)
        message_id = doc.get("message_id") or 0
        video_id   = doc.get("video_id") or doc.get("_id", "?")
        is_video   = doc.get("is_video", False)

        async def _persist(client_name: str, msg) -> None:
            """Save refreshed file_id + message_id back to MongoDB."""
            media = msg.audio or msg.video or msg.document
            if media and media.file_id:
                try:
                    await db.update_music_message_id(
                        video_id=video_id,
                        message_id=msg.id,
                        channel_id=channel_id,
                        file_id=media.file_id,
                        file_unique_id=getattr(media, "file_unique_id", ""),
                        is_video=is_video,
                    )
                    logger.info(
                        "Persisted refreshed file_id from %s session for %s",
                        client_name, video_id,
                    )
                except Exception as pe:
                    logger.warning("Failed to persist refreshed file_id for %s: %s", video_id, pe)

        async def _download_via_message(client, ch_id: int, msg_id: int, label: str) -> bool:
            """Fetch message → download media → return True on success."""
            try:
                msg = await client.get_messages(ch_id, msg_id)
                if msg and not getattr(msg, "empty", True) and (msg.audio or msg.video or msg.document):
                    path = await client.download_media(msg, file_name=target_path)
                    if path and os.path.exists(path) and os.path.getsize(path) > 0:
                        asyncio.create_task(_persist(label, msg))
                        logger.info("Restored %s via %s.", video_id, label)
                        return True
            except (_pg_errors.ChannelInvalid, _pg_errors.ChannelPrivate):
                logger.info("[%s] Client not in dump channel (%s) — skipping.", label, ch_id)
            except Exception as e:
                logger.warning("[%s] message restore failed for %s: %s", label, video_id, e)
            return False

        async def _download_via_file_id(client, fid: str, label: str) -> bool:
            """Download directly by file_id → return True on success."""
            try:
                path = await client.download_media(fid, file_name=target_path)
                if path and os.path.exists(path) and os.path.getsize(path) > 0:
                    logger.info("Restored %s via %s (file_id).", video_id, label)
                    return True
            except _pg_errors.FileReferenceExpired:
                logger.info(
                    "[%s] file_id reference expired for %s — will try message_id next.",
                    label, video_id,
                )
            except Exception as e:
                logger.warning("[%s] file_id restore failed for %s: %s", label, video_id, e)
            return False

        # ── Attempt 1: bot + message_id ──────────────────────────────────────
        if channel_id and message_id:
            logger.info("Restoring %s via bot+message_id (%s:%s)", video_id, channel_id, message_id)
            if await _download_via_message(app, channel_id, message_id, "bot+message_id"):
                return True

        # ── Attempt 2: bot + file_id ─────────────────────────────────────────
        if file_id:
            logger.info("Restoring %s via bot+file_id", video_id)
            if await _download_via_file_id(app, file_id, "bot"):
                return True
            # FileReferenceExpired from bot session — try message_id refresh
            if channel_id and message_id:
                if await _download_via_message(app, channel_id, message_id, "bot+message_id (retry after FileRefExpired)"):
                    return True

        # ── Attempt 3: userbot + message_id ──────────────────────────────────
        # The assistant has an independent session with its own file_reference.
        assistant = _get_assistant_client()
        if assistant and channel_id and message_id:
            logger.info("Restoring %s via userbot+message_id", video_id)
            if await _download_via_message(assistant, channel_id, message_id, "userbot+message_id"):
                return True

        # ── Attempt 4: userbot + file_id ─────────────────────────────────────
        if assistant and file_id:
            logger.info("Restoring %s via userbot+file_id", video_id)
            if await _download_via_file_id(assistant, file_id, "userbot"):
                return True
            if channel_id and message_id:
                if await _download_via_message(assistant, channel_id, message_id, "userbot+message_id (retry after FileRefExpired)"):
                    return True

        logger.error(
            "ALL restore attempts failed for %s (channel=%s, msg=%s, has_file_id=%s). "
            "Will self-heal via cold download.",
            video_id, channel_id, message_id, bool(file_id),
        )
        return False

    # ── Telegram Dump Upload ──────────────────────────────────────────────────

    async def _upload_to_telegram_dump(
        self, file_path: str, video_id: str, title: str, is_video: bool
    ) -> dict:
        """Upload local MP3/MP4 to Telegram dump channel and return message metadata."""
        channel_id = getattr(config, "STORAGE_GROUP_ID", 0) or getattr(config, "LOGGER_ID", 0)
        if not channel_id:
            logger.warning("No STORAGE_GROUP_ID / LOGGER_ID configured for dump backup!")
            return {}

        try:
            caption = f"🎵 **{title or video_id}**\n🆔 `{video_id}`"
            if is_video:
                msg = await app.send_video(channel_id, video=file_path, caption=caption)
                file_id       = msg.video.file_id       if msg and msg.video else ""
                file_unique_id = msg.video.file_unique_id if msg and msg.video else ""
            else:
                msg = await app.send_audio(channel_id, audio=file_path, caption=caption, title=title)
                file_id       = msg.audio.file_id       if msg and msg.audio else ""
                file_unique_id = msg.audio.file_unique_id if msg and msg.audio else ""

            if msg and msg.id:
                logger.info(
                    "Uploaded %s to dump channel (%s) → msg_id: %s", video_id, channel_id, msg.id
                )
                return {
                    "file_id": file_id,
                    "file_unique_id": file_unique_id,
                    "message_id": msg.id,
                    "channel_id": channel_id,
                }
        except Exception as e:
            logger.error("Failed to upload %s to Telegram dump channel: %s", video_id, e)

        return {}

    # ── LRU Eviction ─────────────────────────────────────────────────────────

    async def enforce_lru_eviction(self) -> None:
        """
        LRU Eviction Policy: delete oldest files from /cache/ when total size
        exceeds MAX_CACHE_GB. NEVER deletes MongoDB docs or Telegram messages.
        """
        max_bytes = MAX_CACHE_GB * 1024 * 1024 * 1024
        if not os.path.exists(CACHE_DIR):
            return

        try:
            files: list[tuple[float, int, str]] = []
            total_size = 0

            for entry in os.scandir(CACHE_DIR):
                if entry.is_file():
                    stat = entry.stat()
                    total_size += stat.st_size
                    files.append((stat.st_atime, stat.st_size, entry.path))

            if total_size <= max_bytes:
                return

            logger.info(
                "Cache size (%.2f GB) exceeds MAX_CACHE_GB (%.2f GB). Running LRU eviction...",
                total_size / (1024 ** 3), MAX_CACHE_GB,
            )

            files.sort(key=lambda x: x[0])  # oldest first
            freed = 0
            for atime, size, filepath in files:
                if total_size <= max_bytes:
                    break
                try:
                    os.remove(filepath)
                    total_size -= size
                    freed += size
                    logger.info(
                        "LRU Evicted: %s (freed %.2f MB)", filepath, size / (1024 ** 2)
                    )
                except Exception as evict_err:
                    logger.warning("Failed to evict %s: %s", filepath, evict_err)

            logger.info("LRU Eviction finished. Total freed: %.2f MB", freed / (1024 ** 2))

        except Exception as e:
            logger.error("Error during LRU eviction: %s", e)

    # ── Background Prefetch ───────────────────────────────────────────────────

    async def prefetch_song(
        self,
        video_id: str,
        title: str = "",
        is_video: bool = False,
        downloader_fn=None,
    ) -> None:
        """Background prefetch upcoming songs in queue to local SSD cache."""
        if self.is_local_cached(video_id, is_video):
            return
        logger.info("[QUEUE PREFETCH] Prefetching upcoming song %s to local SSD cache...", video_id)
        await self.get_or_fetch(
            video_id=video_id,
            title=title,
            is_video=is_video,
            downloader_fn=downloader_fn,
        )


def _get_assistant_client():
    """
    Return the first available assistant (userbot) Pyrogram client, or None.
    The userbot has an independent session with its own file_reference tokens.
    """
    try:
        clients = getattr(userbot, "clients", None)
        if clients:
            return clients[0]
        client = getattr(userbot, "client", None)
        if client:
            return client
        # Userbot class itself may be a client
        from pyrogram import Client
        if isinstance(userbot, Client):
            return userbot
    except Exception:
        pass
    return None


cache_manager = HybridCacheManager()
