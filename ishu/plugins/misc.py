# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import time
import asyncio
from pathlib import Path

from pyrogram import enums, errors, filters, types

from ishu import anon, app, config, db, lang, logger, queue, tasks, userbot, yt
from ishu.helpers import buttons


def _in_use_media_ids() -> set[str]:
    """Collect every video_id currently referenced by an active queue so the
    cleanup task never deletes a file that is mid-playback or about to play."""
    ids: set[str] = set()
    for chat_id in list(db.active_calls):
        for item in queue.get_queue(chat_id):
            vid = getattr(item, "id", None)
            if vid:
                ids.add(vid)
    return ids


async def auto_cleanup():
    """Periodically reclaim disk space by deleting orphaned cached media.

    A disk that fills up is the most common *silent* cause of the bot slowing
    down: yt-dlp / ffmpeg write ``.part`` and ``.orig.mp3`` temp files, and
    once free space is gone those writes start failing (download stalls, then
    playback stalls). This task only purges files that are NOT referenced by
    any active or queued stream, and only once free space drops under
    CLEANUP_DISK_THRESHOLD% — so a healthy disk is left completely alone.
    """
    last_alert = 0.0
    while True:
        await asyncio.sleep(config.CLEANUP_INTERVAL)

        try:
            free = psutil_disk_free_percent()
        except Exception as ex:
            logger.warning("auto_cleanup: disk check failed: %s", ex)
            continue

        threshold = config.CLEANUP_DISK_THRESHOLD
        if free >= threshold:
            continue

        in_use = _in_use_media_ids()
        deleted = 0
        freed_bytes = 0

        # ── downloads/ : delete orphaned media (keep .part of active dl) ──────
        dldir = Path("downloads")
        if dldir.is_dir():
            for f in dldir.iterdir():
                if not f.is_file():
                    continue
                # Never touch in-progress download temp files.
                if f.name.endswith(".part") or f.name.endswith(".orig.mp3"):
                    continue
                # Keep files that belong to an active/queued track.
                stem = f.stem  # e.g. "dQw4w9WgXcQ.mp3" -> "dQw4w9WgXcQ"
                if stem in in_use:
                    continue
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted += 1
                except Exception:
                    pass

        # ── cache/ : delete orphaned thumbnails only ─────────────────────────
        cachedir = Path("cache")
        if cachedir.is_dir():
            for f in cachedir.glob("*.jpg"):
                # Keep font + transient temp files; only prune finished thumbs.
                if f.name.startswith("temp_") or f.name in ("font.ttf", "font2.ttf"):
                    continue
                stem = f.stem  # "dQw4w9WgXcQ.jpg" -> "dQw4w9WgXcQ"
                if stem in in_use:
                    continue
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted += 1
                except Exception:
                    pass

        freed_mb = freed_bytes / (1024 * 1024)
        logger.info(
            "auto_cleanup: freed %d orphaned file(s) (%.1f MB); disk free now %.1f%%",
            deleted,
            freed_mb,
            psutil_disk_free_percent(),
        )

        now = time.time()
        # Alert the log group only if still low AND we haven't alerted recently.
        if psutil_disk_free_percent() < threshold and (now - last_alert) > 21600:
            last_alert = now
            try:
                await app.send_message(
                    chat_id=app.logger,
                    text=(
                        f"⚠️ <b>Disk space low</b>\n\n"
                        f"Bot: <b>{app.name}</b>\n"
                        f"Free: <code>{psutil_disk_free_percent():.1f}%</code> "
                        f"(threshold {threshold}%)\n"
                        f"Cleaned: {deleted} orphaned file(s), {freed_mb:.1f} MB\n\n"
                        f"If this keeps recurring, bump CLEANUP_DISK_THRESHOLD "
                        f"or scale the volume."
                    ),
                )
            except Exception as ex:
                logger.warning("auto_cleanup: alert send failed: %s", ex)


def psutil_disk_free_percent() -> float:
    import psutil

    return psutil.disk_usage("/").percent



@app.on_message(filters.video_chat_started, group=19)
@app.on_message(filters.video_chat_ended, group=20)
async def _watcher_vc(_, m: types.Message):
    await anon.stop(m.chat.id)


async def auto_leave():
    while True:
        await asyncio.sleep(3600)
        # Never leave groups the bot itself is present in — the userbot must
        # stay a member so it can serve voice chats and so the dialog crawl
        # keeps counting them. Only stray/unknown inactive groups get cleaned.
        known = set(await db.get_chats())
        for ub in userbot.clients:
            try:
                chats = [dialog.chat.id async for dialog in ub.get_dialogs()
                         if dialog.chat.type in [
                             enums.ChatType.GROUP, enums.ChatType.SUPERGROUP,
                         ]]
                for chat in chats:
                    if chat in (app.logger,):
                        continue
                    if chat in known:
                        continue
                    if chat in db.active_calls:
                        continue
                    await ub.leave_chat(chat)
                    await asyncio.sleep(12)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue


async def track_time():
    while True:
        await asyncio.sleep(1)
        for chat_id in list(db.active_calls):
            if not await db.playing(chat_id):
                continue
            media = queue.get_current(chat_id)
            if not media:
                continue
            media.time += 1


async def update_timer(length=10, sleep=5):
    while True:
        await asyncio.sleep(sleep)
        for chat_id in list(db.active_calls):
            if not await db.playing(chat_id):
                continue
            try:
                media = queue.get_current(chat_id)
                if not media:
                    continue
                from ishu.helpers._inline import _panel_state
                if _panel_state.get(chat_id, {}).get("menu_open"):
                    continue
                duration, message_id = media.duration_sec, media.message_id
                if not duration or not message_id or not media.time:
                    continue
                played = media.time
                remaining = max(duration - played, 0)
                pos = min(int((played / duration) * length), length - 1)
                timer = "—" * pos + "◉" + "—" * (length - pos - 1)

                if remaining <= 30:
                    next = queue.get_next(chat_id, check=True)
                    if next and not next.file_path:
                        next.file_path = await yt.download(next.id, video=next.video)

                if remaining < 10:
                    remove = True
                else:
                    if config.THUMB_GEN:
                        timer = f"{time.strftime('%M:%S', time.gmtime(played))} | {timer} | -{time.strftime('%M:%S', time.gmtime(remaining))}"
                    else:
                        timer = None
                    remove = False

                if not timer and not remove:
                    continue

                await app.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=buttons.controls(
                        chat_id=chat_id, timer=timer, remove=remove,
                        autoplay=await db.get_autoplay(chat_id),
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                pass


async def vc_watcher(sleep=15):
    while True:
        await asyncio.sleep(sleep)
        for chat_id in list(db.active_calls):
            client = await db.get_assistant(chat_id)
            media = queue.get_current(chat_id)
            if not media:
                continue
            participants = await client.get_participants(chat_id)
            if len(participants) < 2 and media.time > 30:
                _lang = await lang.get_lang(chat_id)
                try:
                    sent = await app.edit_message_reply_markup(
                        chat_id=chat_id,
                        message_id=media.message_id,
                        reply_markup=buttons.controls(
                            chat_id=chat_id, status=_lang["stopped"], remove=True
                        ),
                    )
                    await anon.stop(chat_id)
                    await sent.reply_text(_lang["auto_left"])
                except errors.MessageIdInvalid:
                    pass


if config.AUTO_END:
    tasks.append(asyncio.create_task(vc_watcher()))
if config.AUTO_LEAVE:
    tasks.append(asyncio.create_task(auto_leave()))
tasks.append(asyncio.create_task(track_time()))
tasks.append(asyncio.create_task(update_timer()))
if getattr(config, "AUTO_CLEANUP", True):
    tasks.append(asyncio.create_task(auto_cleanup()))
