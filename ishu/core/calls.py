# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import asyncio
import re
from pathlib import Path

from ntgcalls import (ConnectionNotFound, TelegramServerError,
                      RTMPStreamingUnsupported, ConnectionError)
from pyrogram.errors import (ChatSendMediaForbidden, ChatSendPhotosForbidden,
                             MessageIdInvalid)
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession

from ishu import (app, config, db, lang, logger,
                   queue, thumb, userbot, yt)
from ishu.core.youtube import YouTube, set_dl_context
from ishu.helpers import Media, Track, buttons, utils


def _cleanup_file(media) -> None:
    """Clear media file_path reference without purging persistent disk cache."""
    if getattr(media, "file_path", None):
        try:
            path = Path(media.file_path)
            if path.exists() and not (path.parent.name == "downloads" and path.suffix.lower() in [".mp3", ".mp4", ".webm", ".m4a"]):
                path.unlink()
                logger.info("Cleaned up temp file: %s", media.file_path)
        except Exception as e:
            logger.warning("Failed to clean up file %s: %s", media.file_path, e)
        media.file_path = None



def _bg_download(media) -> None:
    """
    Kick off a background download for a track.
    Only starts if neither stream_url nor file_path is already set.
    This ensures the file is ready if the stream URL expires mid-play.
    """
    if isinstance(media, Track) and not media.file_path:
        async def _task():
            try:
                path = await yt.download(media.id, video=media.video)
                if path:
                    media.file_path = path
                    logger.info("Background download complete: %s → %s", media.id, path)
            except Exception as e:
                logger.warning("Background download failed for %s: %s", media.id, e)

        asyncio.create_task(_task())


# Per-chat recently-played ids and title hashes — stops autoplay from looping the same
# songs or re-uploads.
_recent_ids: "dict[int, list[str]]" = {}
_recent_titles: "dict[int, list[str]]" = {}

def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', title)
    cleaned = cleaned.split('|')[0].split('-')[0].strip().lower()
    return cleaned

def _remember(chat_id: int, vid: str | None, title: str | None = None) -> None:
    if vid:
        hist = _recent_ids.setdefault(chat_id, [])
        if vid not in hist:
            hist.append(vid)
        if len(hist) > 200:
            del hist[: len(hist) - 200]

    if title:
        norm_title = _normalize_title(title)
        if norm_title:
            thist = _recent_titles.setdefault(chat_id, [])
            if norm_title not in thist:
                thist.append(norm_title)
            if len(thist) > 200:
                del thist[: len(thist) - 200]

def _is_recent(chat_id: int, vid: str | None, title: str | None = None) -> bool:
    if vid and vid in _recent_ids.get(chat_id, []):
        return True
    if title:
        norm_title = _normalize_title(title)
        if norm_title and norm_title in _recent_titles.get(chat_id, []):
            return True
    return False

def _clear_old_history(chat_id: int) -> None:
    """Trim oldest 75% history when candidates are exhausted."""
    if chat_id in _recent_ids and len(_recent_ids[chat_id]) > 5:
        del _recent_ids[chat_id][: int(len(_recent_ids[chat_id]) * 0.75)]
    if chat_id in _recent_titles and len(_recent_titles[chat_id]) > 5:
        del _recent_titles[chat_id][: int(len(_recent_titles[chat_id]) * 0.75)]


class TgCall(PyTgCalls):
    def __init__(self):
        self.clients = []

    async def pause(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=True)
        return await client.pause(chat_id)

    async def resume(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        await db.playing(chat_id, paused=False)
        return await client.resume(chat_id)

    async def stop(self, chat_id: int) -> None:
        client = await db.get_assistant(chat_id)

        # Clean up files for all media items in queue
        q_items = queue.get_queue(chat_id)
        for item in q_items:
            _cleanup_file(item)

        queue.clear(chat_id)
        await db.remove_call(chat_id)
        await db.set_loop(chat_id, 0)

        try:
            await client.leave_call(chat_id, close=False)
        except Exception:
            pass


    async def play_media(
        self,
        chat_id: int,
        message: Message,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)
        _thumb = (
            await thumb.generate(media)
            if isinstance(media, Track)
            else config.DEFAULT_THUMB
        ) if config.THUMB_GEN else None

        # ── Step 1: Resolve media path ─────────────────────────────────────────
        # Prefer a locally cached file over the direct stream URL. Stream URLs
        # (googlevideo.com) expire after ~6h, so once a track's file has been
        # downloaded the local copy becomes the source of truth — this stops
        # the call from dropping (and the assistant from leaving the GC) when an
        # old URL silently dies mid-play.
        media_path = media.file_path or media.stream_url
        used_stream = bool(media.stream_url) and not media.file_path

        if not media_path and isinstance(media, Track):
            cached_file = await yt.download(media.id, video=media.video)
            if cached_file:
                media.file_path = cached_file
                media_path = cached_file
            else:
                media_path = await yt.get_stream_url(media.id, video=media.video)
                if media_path:
                    media.stream_url = media_path
                    used_stream = True

        # ── Step 2: Attempt playback ──────────────────────────────────────────
        stream_success = False
        if media_path:
            try:
                stream = types.MediaStream(
                    media_path=media_path,
                    audio_parameters=types.AudioQuality.HIGH,
                    video_parameters=types.VideoQuality.HD_720p,
                    audio_flags=types.MediaStream.Flags.REQUIRED,
                    video_flags=(
                        types.MediaStream.Flags.AUTO_DETECT
                        if media.video
                        else types.MediaStream.Flags.IGNORE
                    ),
                    ffmpeg_parameters=f"-ss {seek_time}" if seek_time > 1 else None,
                )
                await client.play(
                    chat_id=chat_id,
                    stream=stream,
                    config=types.GroupCallConfig(auto_start=True),
                )
                stream_success = True

                # If we started via stream URL, kick off a background download
                # so that the file is cached and cleanup works normally.
                if used_stream and isinstance(media, Track):
                    _bg_download(media)

            except Exception as e:
                logger.warning("Stream URL failed: %s. Falling back to download.", e)
                stream_success = False

        # ── Step 3: Fallback — download then play ─────────────────────────────
        if not stream_success and isinstance(media, Track):
            set_dl_context(
                chat_id=chat_id,
                chat_title=getattr(message.chat, "title", None),
                title=media.title,
                video=media.video,
            )
            media.file_path = await yt.download(media.id, video=media.video)
            media_path = media.file_path

        if not media_path:
            await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            if isinstance(media, Track):
                await utils.error_log(
                    context="Stream URL + Download both failed",
                    error="No media source could be resolved (all download methods returned None).",
                    chat_id=chat_id,
                    chat_title=getattr(message.chat, "title", None),
                    title=media.title,
                    video=media.video,
                )
            return await self.play_next(chat_id)

        try:
            if not stream_success:
                stream = types.MediaStream(
                    media_path=media_path,
                    audio_parameters=types.AudioQuality.HIGH,
                    video_parameters=types.VideoQuality.HD_720p,
                    audio_flags=types.MediaStream.Flags.REQUIRED,
                    video_flags=(
                        types.MediaStream.Flags.AUTO_DETECT
                        if media.video
                        else types.MediaStream.Flags.IGNORE
                    ),
                    ffmpeg_parameters=f"-ss {seek_time}" if seek_time > 1 else None,
                )
                await client.play(
                    chat_id=chat_id,
                    stream=stream,
                    config=types.GroupCallConfig(auto_start=True),
                )

            if not seek_time:
                media.time = 1
                await db.add_call(chat_id)
                _remember(chat_id, getattr(media, "id", None), getattr(media, "title", None))

                # Shorten title to 50 characters max
                short_title = media.title.split("|")[0].split("(")[0].strip()
                if len(short_title) > 50:
                    short_title = short_title[:47].rstrip() + "…"

                text = _lang["play_media"].format(
                    media.url,
                    short_title,
                    media.duration,
                    media.user,
                )

                keyboard = buttons.controls(
                    chat_id,
                    autoplay=await db.get_autoplay(chat_id),
                    mode=await db.get_autoplay_mode(chat_id),
                )

                if _thumb:
                    await message.edit_media(
                        media=InputMediaPhoto(
                            media=_thumb,
                            caption=text,
                        ),
                        reply_markup=keyboard,
                    )
                else:
                    await message.edit_text(
                        text,
                        reply_markup=keyboard,
                    )

                media.message_id = message.id
                if await db.get_autoplay(chat_id) and not queue.get_next(chat_id, check=True):
                    asyncio.create_task(self._prefetch_autoplay(chat_id, media))

        except FileNotFoundError:
            await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            await self.play_next(chat_id)
        except exceptions.NoActiveGroupCall:
            await self.stop(chat_id)
            await message.edit_text(_lang["error_no_call"])
        except exceptions.NoAudioSourceFound:
            await message.edit_text(_lang["error_no_audio"])
            await self.play_next(chat_id)
        except (ConnectionError, ConnectionNotFound, TelegramServerError):
            await self.stop(chat_id)
            await message.edit_text(_lang["error_tg_server"])
        except RTMPStreamingUnsupported:
            await self.stop(chat_id)
            await message.edit_text(_lang["error_rtmp"])


    async def replay(self, chat_id: int) -> None:
        if not await db.get_call(chat_id):
            return

        media = queue.get_current(chat_id)
        _lang = await lang.get_lang(chat_id)
        msg = await app.send_message(chat_id=chat_id, text=_lang["play_again"])
        media.message_id = msg.id
        await self.play_media(chat_id, msg, media)


    async def _prefetch_autoplay(self, chat_id: int, last) -> None:
        """
        Background Zero-Gap Pre-fetcher: Silently downloads the next autoplay candidate in advance while current track plays!
        """
        try:
            if getattr(last, "_prefetch_autoplay", None):
                return
            if not await db.get_autoplay(chat_id):
                return
            if queue.get_next(chat_id, check=True):
                return

            last_id = getattr(last, "id", None)
            last_title = getattr(last, "title", None) or ""
            last_channel = getattr(last, "channel_name", None) or ""
            clean_title = _normalize_title(last_title)
            mode = await db.get_autoplay_mode(chat_id)

            candidates: list = []
            if mode == "artist" and last_channel:
                candidates = await yt.search_similar_candidates(f"{last_channel} top songs", limit=10)
            elif mode == "trending":
                candidates = await yt.search_similar_candidates("top trending songs", limit=10)
            else:
                if clean_title:
                    fast_similar = await yt.search_similar_candidates(f"songs like {clean_title}", limit=8)
                    if fast_similar:
                        candidates.extend(fast_similar)
                if last_id and len(candidates) < 5:
                    related = await yt.get_related_candidates(last_id, limit=10)
                    if related:
                        candidates.extend(related)

            if not candidates:
                candidates = await yt.search_similar_candidates("top songs", limit=10)

            duration_limit = getattr(config, "DURATION_LIMIT", 7200)
            curr_queue_ids = [getattr(t, "id", None) for t in queue.get_queue(chat_id) if hasattr(t, "id")]

            valid = []
            for t in candidates:
                if not t or not getattr(t, "id", None):
                    continue
                tid = t.id
                if tid == last_id or tid in curr_queue_ids or _is_recent(chat_id, tid, getattr(t, "title", "")):
                    continue
                dur = getattr(t, "duration_sec", 0) or 0
                if dur > 0 and (dur < 20 or dur > duration_limit):
                    continue
                valid.append(t)

            if not valid and candidates:
                _clear_old_history(chat_id)
                for t in candidates:
                    if t and getattr(t, "id", None) and t.id != last_id:
                        valid.append(t)

            if valid:
                candidate = valid[0]
                candidate.user = "Autoplay"
                candidate._chat_id = chat_id
                cached_path = await yt.download(candidate.id, video=candidate.video)
                if cached_path:
                    candidate.file_path = cached_path
                    last._prefetch_autoplay = candidate
                    logger.info("Zero-Gap Autoplay Pre-fetch READY for chat %s -> %s (%s)", chat_id, candidate.id, candidate.title)
        except Exception as e:
            logger.warning("Zero-Gap Autoplay Pre-fetch failed for chat %s: %s", chat_id, e)


    async def _autoplay_next(self, chat_id: int, last) -> None:
        """
        Smart Autoplay System: Automatically finds, verifies, and streams a non-repeating related song.
        """
        # User Priority Check: If user queued a song in the meantime, abort autoplay and play user song!
        if queue.get_queue(chat_id):
            return await self.play_next(chat_id)

        _lang = await lang.get_lang(chat_id)
        msg = await app.send_message(chat_id=chat_id, text=_lang["play_searching"], parse_mode=enums.ParseMode.HTML)

        last_id = getattr(last, "id", None) if last else None
        last_title = getattr(last, "title", None) or "" if last else ""
        last_channel = getattr(last, "channel_name", None) or "" if last else ""
        clean_title = _normalize_title(last_title)
        mode = await db.get_autoplay_mode(chat_id)

        candidates: list = []
        if mode == "artist" and last_channel:
            candidates = await yt.search_similar_candidates(f"{last_channel} top songs", limit=10)
        elif mode == "trending":
            candidates = await yt.search_similar_candidates("top trending songs", limit=10)
        else:
            if clean_title:
                fast_similar = await yt.search_similar_candidates(f"songs like {clean_title}", limit=8)
                if fast_similar:
                    candidates.extend(fast_similar)
            if last_id and len(candidates) < 5:
                related = await yt.get_related_candidates(last_id, limit=10)
                if related:
                    candidates.extend(related)

        if len(candidates) < 5:
            queries = []
            if last_channel and clean_title:
                queries.append(f"{last_channel} {clean_title}")
            if clean_title:
                queries.append(f"{clean_title} full song")
            if not queries:
                queries.append("top trending songs")

            for q in queries:
                try:
                    similar = await yt.search_similar_candidates(q, limit=5)
                    if similar:
                        candidates.extend(similar)
                except Exception as e:
                    logger.warning("Autoplay Tier 2 failed for query '%s': %s", q, e)

        if not candidates:
            try:
                fallback_q = f"{last_channel} top songs" if last_channel else "top trending songs"
                candidates = await yt.search_similar_candidates(fallback_q, limit=10)
            except Exception as e:
                logger.warning("Autoplay Tier 3 fallback failed: %s", e)

        # Candidate Validation & Filtering
        duration_limit = getattr(config, "DURATION_LIMIT", 7200)
        curr_queue_ids = [getattr(t, "id", None) for t in queue.get_queue(chat_id) if hasattr(t, "id")]

        def _filter(cand_list: list) -> list:
            valid = []
            seen_in_batch = set()
            for t in cand_list:
                if not t or not getattr(t, "id", None):
                    continue
                tid = t.id
                if tid == last_id or tid in seen_in_batch:
                    continue
                if tid in curr_queue_ids:
                    continue
                if _is_recent(chat_id, tid, getattr(t, "title", "")):
                    continue
                # Duration filter: must be between 20 sec and DURATION_LIMIT
                dur = getattr(t, "duration_sec", 0) or 0
                if dur > 0 and (dur < 20 or dur > duration_limit):
                    continue
                seen_in_batch.add(tid)
                valid.append(t)
            return valid

        valid_candidates = _filter(candidates)

        # Exhaustion fallback: If all candidates filtered out (history full), trim history and re-filter
        if not valid_candidates and candidates:
            _clear_old_history(chat_id)
            valid_candidates = _filter(candidates)

        if not valid_candidates:
            logger.info("Autoplay found no unique candidates for chat %s", chat_id)
            try:
                await msg.delete()
            except Exception:
                pass
            return await self.stop(chat_id)

        # Stream / File Availability Verification Loop
        selected_track = None
        for candidate in valid_candidates:
            # Check user queue priority right before streaming
            if queue.get_queue(chat_id):
                try:
                    await msg.delete()
                except Exception:
                    pass
                return await self.play_next(chat_id)

            try:
                candidate.stream_url = await yt.get_stream_url(candidate.id)
                if not candidate.stream_url:
                    candidate.file_path = await yt.download(candidate.id)

                if candidate.stream_url or candidate.file_path:
                    selected_track = candidate
                    break
            except Exception as err:
                logger.warning("Autoplay candidate %s stream/download failed: %s", candidate.id, err)
                continue

        if not selected_track:
            try:
                await msg.delete()
            except Exception:
                pass
            return await self.stop(chat_id)

        # Final setup and play
        selected_track.user = "Autoplay"
        selected_track._chat_id = chat_id
        _remember(chat_id, selected_track.id, selected_track.title)

        queue.force_add(chat_id, selected_track)
        selected_track.message_id = msg.id
        await self.play_media(chat_id, msg, selected_track)


    async def play_next(self, chat_id: int) -> None:
        if loop := await db.get_loop(chat_id):
            await db.set_loop(chat_id, loop - 1)
            return await self.replay(chat_id)

        # ── Clean up the finished song's file BEFORE popping it ───────────────
        current_media = queue.get_current(chat_id)
        if current_media:
            _cleanup_file(current_media)

        # ── Advance queue ─────────────────────────────────────────────────────
        media = queue.get_next(chat_id)

        # ── FIX: check media is not None BEFORE accessing its attributes ──────
        if not media:
            # Autoplay: when the queue empties, fetch a related track from the
            # last played song so the stream keeps going instead of stopping.
            if await db.get_autoplay(chat_id):
                last = current_media
                # Zero-Gap HIT: Use pre-fetched track if available!
                pre_track = getattr(last, "_prefetch_autoplay", None) if last else None
                if pre_track and isinstance(pre_track, Track):
                    pre_track.user = "Autoplay"
                    pre_track._chat_id = chat_id
                    _remember(chat_id, pre_track.id, pre_track.title)
                    queue.force_add(chat_id, pre_track)
                    _lang = await lang.get_lang(chat_id)
                    msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])
                    pre_track.message_id = msg.id
                    logger.info("Zero-Gap Autoplay HIT! Instant transition for chat %s -> %s", chat_id, pre_track.id)
                    return await self.play_media(chat_id, msg, pre_track)

                await self._autoplay_next(chat_id, last)
                return
            return await self.stop(chat_id)

        # Delete the "now playing" message of the next track (it was "queued")
        try:
            if media.message_id:
                await app.delete_messages(
                    chat_id=chat_id,
                    message_ids=media.message_id,
                    revoke=True,
                )
                media.message_id = 0
        except Exception:
            pass

        _lang = await lang.get_lang(chat_id)
        msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])

        # ── Resolve playback source for the next track ────────────────────────
        # Priority: existing file_path → existing stream_url → re-fetch stream
        # URL → download. We always try to ensure a valid source here; relying
        # on a stale (expired) stream_url alone is what caused the call to drop
        # and the assistant to leave the GC. (If only a stale URL remains,
        # play_media() will download + play the file when the URL fails.)
        if not media.file_path:
            fname = f"downloads/{media.id}.{'mp4' if media.video else 'mp3'}"
            if Path(fname).exists():
                media.file_path = fname
            elif not media.stream_url:
                # No usable file yet and no cached URL — fetch a fresh one.
                media.stream_url = await yt.get_stream_url(media.id, video=media.video)
            # If we still have nothing usable, fall back to a local download.
            if not media.file_path and not media.stream_url:
                media.file_path = await yt.download(media.id, video=media.video)

        if not media.stream_url and not media.file_path:
            await msg.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            return await self.play_next(chat_id)

        # ── Pre-download the track AFTER this one (look-ahead) ───────────────
        next_media = queue.get_next(chat_id, check=True)
        if next_media and isinstance(next_media, Track):
            _bg_download(next_media)

        media.message_id = msg.id
        await self.play_media(chat_id, msg, media)


    async def ping(self) -> float:
        pings = [client.ping for client in self.clients]
        return round(sum(pings) / len(pings), 2)


    async def decorators(self, client: PyTgCalls) -> None:
        @client.on_update()
        async def update_handler(_, update: types.Update) -> None:
            if isinstance(update, types.StreamEnded):
                if update.stream_type == types.StreamEnded.Type.AUDIO:
                    await self.play_next(update.chat_id)
            elif isinstance(update, types.ChatUpdate):
                if update.status in [
                    types.ChatUpdate.Status.KICKED,
                    types.ChatUpdate.Status.LEFT_GROUP,
                    types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                ]:
                    # A dead stream URL (expires ~6h) or a transient Telegram
                    # disconnect can end the call and would normally make the
                    # assistant leave the GC. Try to recover once before giving
                    # up: refresh the source for the current track (cached file
                    # if present, otherwise a fresh stream URL) and replay it.
                    media = queue.get_current(update.chat_id)
                    if media and isinstance(media, Track):
                        try:
                            if not media.file_path:
                                media.stream_url = await yt.get_stream_url(
                                    media.id, video=media.video
                                )
                            await self.replay(update.chat_id)
                            return
                        except Exception as e:
                            logger.warning(
                                "Auto-reconnect failed for %s: %s",
                                update.chat_id, e,
                            )
                    await self.stop(update.chat_id)


    async def boot(self) -> None:
        PyTgCallsSession.notice_displayed = True
        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self.decorators(client)
        logger.info("PyTgCalls client(s) started.")
