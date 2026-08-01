# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import asyncio
from pathlib import Path

from pyrogram import filters, types

from ishu import anon, app, config, db, lang, logger, queue, tg, yt
from ishu.helpers import buttons, utils
from ishu.helpers._play import checkUB

# Track active background download tasks to avoid duplicates
_background_tasks: set[str] = set()


async def _background_download_task(track) -> None:
    """Download the actual file (Railway YT API -> yt-dlp fallback) so it's ready."""
    try:
        if not track.file_path:
            path = await yt.download(track.id, video=track.video)
            if path:
                track.file_path = path
                logger.info("Background: file download complete for %s → %s", track.id, path)
            else:
                logger.warning(
                    "Background: file download failed for %s",
                    track.id,
                )
                await utils.error_log(
                    track._chat_id, "play:bg_file_download_empty",
                    Exception(f"yt.download returned no path for {track.id}"), track,
                )
    except Exception as e:
        logger.warning("Background download task failed for %s: %s", track.id, e)
        await utils.error_log(track._chat_id, "play:bg_download_exception", e, track)


def _start_background_download(track) -> None:
    """
    Start a background download task for a queued track.
    Skips if a task is already running for this track ID.
    """
    if track.id not in _background_tasks:
        _background_tasks.add(track.id)
        task = asyncio.create_task(_background_download_task(track))
        task.add_done_callback(lambda _: _background_tasks.discard(track.id))


def playlist_to_queue(chat_id: int, tracks: list) -> str:
    text = "<blockquote expandable>"
    for track in tracks:
        pos = queue.add(chat_id, track)
        text += f"<b>{pos}.</b> {track.title}\n"
        _start_background_download(track)
    text = text[:1948] + "</blockquote>"
    return text


@app.on_message(
    filters.command(["play", "playforce", "vplay", "vplayforce"])
    & filters.group
    & ~app.bl_users
)
@lang.language()
@checkUB
async def play_hndlr(
    _,
    m: types.Message,
    force: bool = False,
    m3u8: bool = False,
    video: bool = False,
    url: str = None,
) -> None:
    sent = await m.reply_text(m.lang["play_searching"])
    file = None
    mention = m.from_user.mention
    media = tg.get_media(m.reply_to_message) if m.reply_to_message else None
    tracks = []

    if media:
        setattr(sent, "lang", m.lang)
        file = await tg.download(m.reply_to_message, sent)

    elif m3u8:
        file = await tg.process_m3u8(url, sent.id, video)

    elif url:
        if "playlist" in url:
            await sent.edit_text(m.lang["playlist_fetch"])
            tracks = await yt.playlist(
                config.PLAYLIST_LIMIT, mention, url, video
            )

            if not tracks:
                return await sent.edit_text(m.lang["playlist_error"])

            file = tracks[0]
            tracks.remove(file)
            file.message_id = sent.id
        else:
            file = await yt.search(url, sent.id, video=video)

        if not file:
            return await sent.edit_text(
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )

    elif len(m.command) >= 2:
        query = " ".join(m.command[1:])
        file = await yt.search(query, sent.id, video=video)
        if not file:
            return await sent.edit_text(
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )

    if not file:
        return await sent.edit_text(m.lang["play_usage"])

    if file.duration_sec > config.DURATION_LIMIT:
        return await sent.edit_text(
            m.lang["play_duration_limit"].format(config.DURATION_LIMIT // 60)
        )

    # Tag the resolved track(s) with the chat id so background/error logging
    # knows where to report playback failures.
    file._chat_id = m.chat.id
    for t in tracks:
        t._chat_id = m.chat.id

    if config.PLAY_LOG:
        await utils.play_log(m, sent.link, file.title, file.duration, media=file)

    file.user = mention

    if force:
        queue.force_add(m.chat.id, file)
    else:
        position = queue.add(m.chat.id, file)

        if position != 0 or await db.get_call(m.chat.id):
            title = file.title.split("|")[0].split("(")[0].strip()

            await sent.edit_text(
                m.lang["play_queued"].format(
                    position,
                    file.url,
                    title,
                    file.duration,
                    m.from_user.mention,
                ),
                reply_markup=buttons.play_queued(
                    m.chat.id, file.id, m.lang["play_now"]
                ),
            )

            # Start background: get stream URL + download file for this queued song
            _start_background_download(file)

            if tracks:
                added = playlist_to_queue(m.chat.id, tracks)
                await app.send_message(
                    chat_id=m.chat.id,
                    text=m.lang["playlist_queued"].format(len(tracks)) + added,
                )

            return

    # ── Immediate play: prefer a cached file, else download fresh ───────────
    if not file.file_path:
        fname = f"downloads/{file.id}.{'mp4' if video else 'mp3'}"
        if Path(fname).exists():
            file.file_path = fname
        else:
            file.file_path = await yt.download(file.id, video=video)

    # Start playback (background download is triggered inside play_media)
    await anon.play_media(chat_id=m.chat.id, message=sent, media=file)

    # Pre-download playlist tracks in background while first track plays
    if tracks:
        added = playlist_to_queue(m.chat.id, tracks)
        await app.send_message(
            chat_id=m.chat.id,
            text=m.lang["playlist_queued"].format(len(tracks)) + added,
        )
