# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# Ultra-Fast Hybrid Cache System for Telegram Music Bot

import asyncio
import os
import time
from datetime import datetime, timezone

from ishu import config, logger, app, db

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
    Tier 1 (Hot): Local SSD Cache (/cache/video_id.mp3) -> 50-300ms playback
    Tier 2 (Warm): Telegram Dump Channel Backup -> 1-3s restoration
    Tier 3 (Cold): YouTube Downloader -> 5-20s initial fetch

    Guarantees zero duplicate YouTube downloads if metadata exists in MongoDB.
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

        # ── Step 1: Query MongoDB Metadata ──
        doc = await db.get_music_cache(video_id, is_video)

        if doc:
            # Document exists in MongoDB! NEVER download from YouTube again.
            logger.info("MongoDB Cache HIT for video_id: %s", video_id)

            # Check if local SSD file exists (Hot Cache)
            if self.is_local_cached(video_id, is_video):
                logger.info("[HOT CACHE SSD] Playing %s immediately from local SSD.", video_id)
                # Touch file access time for LRU tracking
                try:
                    os.utime(local_path, None)
                except Exception:
                    pass

                # Asynchronously update play stats in MongoDB
                asyncio.create_task(db.update_music_stats(video_id, is_video))
                return local_path

            # Local file missing/evicted -> Warm Cache: Restore from Telegram Dump Channel
            logger.info("[WARM CACHE RESTORE] Local SSD missing for %s. Restoring from Telegram dump...", video_id)
            restored = await self._restore_from_telegram(doc, local_path)
            if restored:
                asyncio.create_task(db.update_music_stats(video_id, is_video))
                asyncio.create_task(self.enforce_lru_eviction())
                return local_path
            else:
                logger.error("Failed to restore %s from Telegram dump channel!", video_id)

        # ── Step 2: Document does not exist in MongoDB (Cold Cache) ──
        # Single-flight lock prevents duplicate YouTube downloads across concurrent tasks
        lock = get_video_lock(video_id)
        async with lock:
            try:
                # Re-check MongoDB in case another concurrent task just finished downloading it
                doc_recheck = await db.get_music_cache(video_id, is_video)
                if doc_recheck:
                    if self.is_local_cached(video_id, is_video):
                        asyncio.create_task(db.update_music_stats(video_id, is_video))
                        return local_path
                    restored = await self._restore_from_telegram(doc_recheck, local_path)
                    if restored:
                        asyncio.create_task(db.update_music_stats(video_id, is_video))
                        return local_path

                if not downloader_fn:
                    logger.error("No downloader function provided for cold cache download of %s", video_id)
                    return None

                logger.info("[COLD CACHE DOWNLOAD] Fetching %s from YouTube API...", video_id)
                dl_result = await downloader_fn(video_id, is_video)
                if not dl_result or not os.path.exists(dl_result) or os.path.getsize(dl_result) == 0:
                    logger.error("Cold YouTube download failed for %s", video_id)
                    return None

                file_size = os.path.getsize(dl_result)

                # Upload MP3 to Telegram Dump Channel as permanent storage
                dump_meta = await self._upload_to_telegram_dump(dl_result, video_id, title, is_video)

                # Store metadata in MongoDB as the single Source of Truth
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

    async def _restore_from_telegram(self, doc: dict, target_path: str) -> bool:
        """
        Restore a missing local file from the Telegram dump channel.

        The message_id path is tried FIRST and the bare file_id SECOND. This is
        deliberate: all bots share one dump channel + one SharedStorage MongoDB,
        so a file_id written by bot A is routinely stale (FILE_REFERENCE_EXPIRED)
        for bot B. Re-fetching the message first yields a fresh, bot-specific
        file_reference that always works, and we persist that refreshed file_id
        back to MongoDB as a best-effort fast path for next time.
        """
        file_id = doc.get("file_id")
        channel_id = doc.get("channel_id") or getattr(config, "STORAGE_GROUP_ID", 0)
        message_id = doc.get("message_id")

        async def _persist_fresh_id(msg) -> None:
            media = msg.audio or msg.video or msg.document
            if media and media.file_id:
                try:
                    await db.update_music_file_id(
                        video_id=doc.get("video_id", doc.get("_id")),
                        file_id=media.file_id,
                        file_unique_id=getattr(media, "file_unique_id", ""),
                        is_video=doc.get("is_video", False),
                    )
                except Exception as e:
                    logger.warning("Failed to persist refreshed file_id for %s: %s", doc.get("_id"), e)

        # Attempt 1 (primary): fetch the message -> fresh file_reference -> download.
        # Immune to cross-bot file_id expiry because get_messages re-resolves it.
        if channel_id and message_id:
            try:
                logger.info("Restoring media via message_id (%s:%s)", channel_id, message_id)
                msg = await app.get_messages(channel_id, message_id)
                if msg and (msg.audio or msg.video or msg.document):
                    path = await app.download_media(msg, file_name=target_path)
                    if path and os.path.exists(path) and os.path.getsize(path) > 0:
                        await _persist_fresh_id(msg)
                        logger.info("Successfully restored %s via message_id.", doc.get("_id"))
                        return True
            except Exception as e:
                logger.warning("Telegram message_id restoration failed: %s", e)

        # Attempt 2 (fallback): the previously-cached file_id, when the message
        # lookup itself failed (e.g. message deleted). Often stale across bots,
        # but cheap and occasionally works for the bot that originally stored it.
        if file_id:
            try:
                logger.info("Restoring media via file_id (%s...)", file_id[:15])
                path = await app.download_media(file_id, file_name=target_path)
                if path and os.path.exists(path) and os.path.getsize(path) > 0:
                    logger.info("Successfully restored %s via file_id.", doc.get("_id"))
                    return True
            except Exception as e:
                logger.warning("Telegram file_id restoration failed: %s", e)

        return False

    async def _upload_to_telegram_dump(self, file_path: str, video_id: str, title: str, is_video: bool) -> dict:
        """
        Upload local MP3/MP4 file to Telegram dump channel and return message metadata.
        """
        channel_id = getattr(config, "STORAGE_GROUP_ID", 0) or getattr(config, "LOGGER_ID", 0)
        if not channel_id:
            logger.warning("No STORAGE_GROUP_ID / LOGGER_ID configured for dump backup!")
            return {}

        try:
            caption = f"🎵 **{title or video_id}**\n🆔 `{video_id}`"
            if is_video:
                msg = await app.send_video(channel_id, video=file_path, caption=caption)
                file_id = msg.video.file_id if msg and msg.video else ""
                file_unique_id = msg.video.file_unique_id if msg and msg.video else ""
            else:
                msg = await app.send_audio(channel_id, audio=file_path, caption=caption, title=title)
                file_id = msg.audio.file_id if msg and msg.audio else ""
                file_unique_id = msg.audio.file_unique_id if msg and msg.audio else ""

            if msg and msg.id:
                logger.info("Uploaded %s to dump channel (%s) -> msg_id: %s", video_id, channel_id, msg.id)
                return {
                    "file_id": file_id,
                    "file_unique_id": file_unique_id,
                    "message_id": msg.id,
                    "channel_id": channel_id,
                }
        except Exception as e:
            logger.error("Failed to upload %s to Telegram dump channel: %s", video_id, e)

        return {}

    async def enforce_lru_eviction(self) -> None:
        """
        LRU Eviction Policy:
        Deletes oldest files from local SSD /cache/ if total size exceeds MAX_CACHE_GB.
        NEVER deletes MongoDB metadata or Telegram dump channel backup files.
        """
        max_bytes = MAX_CACHE_GB * 1024 * 1024 * 1024
        if not os.path.exists(CACHE_DIR):
            return

        try:
            files = []
            total_size = 0

            for entry in os.scandir(CACHE_DIR):
                if entry.is_file():
                    stat = entry.stat()
                    total_size += stat.st_size
                    files.append((stat.st_atime, stat.st_size, entry.path))

            if total_size <= max_bytes:
                return

            logger.info("Cache size (%.2f GB) exceeds MAX_CACHE_GB (%.2f GB). Running LRU eviction...", 
                        total_size / (1024**3), MAX_CACHE_GB)

            # Sort by last access time (oldest first)
            files.sort(key=lambda x: x[0])

            freed = 0
            for atime, size, filepath in files:
                if total_size <= max_bytes:
                    break
                try:
                    os.remove(filepath)
                    total_size -= size
                    freed += size
                    logger.info("LRU Evicted local SSD cache file: %s (freed %.2f MB)", filepath, size / (1024**2))
                except Exception as evict_err:
                    logger.warning("Failed to evict file %s: %s", filepath, evict_err)

            logger.info("LRU Eviction finished. Total freed: %.2f MB", freed / (1024**2))

        except Exception as e:
            logger.error("Error during LRU eviction: %s", e)

    async def prefetch_song(self, video_id: str, title: str = "", is_video: bool = False, downloader_fn=None) -> None:
        """
        Background task to prefetch upcoming songs in queue to local SSD cache.
        """
        if self.is_local_cached(video_id, is_video):
            return
        logger.info("[QUEUE PREFETCH] Prefetching upcoming song %s to local SSD cache...", video_id)
        await self.get_or_fetch(video_id=video_id, title=title, is_video=is_video, downloader_fn=downloader_fn)


cache_manager = HybridCacheManager()
