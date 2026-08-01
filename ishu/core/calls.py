# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import asyncio
import re
from pathlib import Path

from ntgcalls import (ConnectionNotFound, TelegramServerError,
                      RTMPStreamingUnsupported, ConnectionError)
from pyrogram import enums
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
    Only starts if file_path is not already set.
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
    cleaned = title.lower()
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', cleaned)
    keywords = [
        "official video", "official audio", "official music video", "full song", "lyrical video",
        "lyrics", "remix", "extended", "video song", "audio song", "4k video", "hd video",
        "studio version", "live performance", "unplugged", "cover", "visualizer", "prod.", "feat.", "ft."
    ]
    for kw in keywords:
        cleaned = cleaned.replace(kw, "")
    cleaned = re.sub(r'[^\w\s]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def _title_fingerprint(title: str | None) -> set:
    norm = _normalize_title(title)
    return set(w for w in norm.split() if len(w) >= 3)

def _is_duplicate_title(chat_id: int, candidate_title: str | None) -> bool:
    if not candidate_title:
        return False
    cand_norm = _normalize_title(candidate_title)
    if not cand_norm:
        return False

    cand_words = _title_fingerprint(candidate_title)
    recent_titles = _recent_titles.get(chat_id, [])

    for past_norm in recent_titles:
        if not past_norm:
            continue
        if cand_norm == past_norm:
            return True
        if len(cand_norm) >= 6 and len(past_norm) >= 6:
            if cand_norm in past_norm or past_norm in cand_norm:
                return True
        past_words = set(w for w in past_norm.split() if len(w) >= 3)
        if cand_words and past_words:
            intersection = cand_words.intersection(past_words)
            overlap = len(intersection) / float(min(len(cand_words), len(past_words)))
            if overlap >= 0.70:
                return True
    return False

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
    if title and _is_duplicate_title(chat_id, title):
        return True
    return False

def _clear_old_history(chat_id: int, keep: int = 30) -> None:
    """Trim oldest history when candidates are exhausted, keeping the latest 'keep' items."""
    if chat_id in _recent_ids:
        hist = _recent_ids[chat_id]
        if len(hist) > keep:
            del hist[: len(hist) - keep]
    if chat_id in _recent_titles:
        thist = _recent_titles[chat_id]
        if len(thist) > keep:
            del thist[: len(thist) - keep]


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
        media_path = media.file_path
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
                logger.warning("Playback failed for %s: %s. Falling back to download.", getattr(media, "id", "?"), e)
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

                from ishu.helpers._inline import _panel_state
                _panel_state[chat_id] = _panel_state.get(chat_id, {})
                _panel_state[chat_id]["playing_caption"] = text

                keyboard = buttons.controls(
                    chat_id,
                    autoplay=await db.get_autoplay(chat_id),
                    mode=await db.get_autoplay_mode(chat_id),
                    link=media.url if (isinstance(media, Track) and getattr(media, "url", None)) else None,
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


    async def _get_autoplay_candidate(self, chat_id: int, last) -> Track | None:
        """
        Unified Autoplay Selection: Resolves the next candidate according to active mode,
        applies strict non-repeating filters, and falls back gracefully.
        """
        last_id = getattr(last, "id", None) if last else None
        last_title = getattr(last, "title", None) or "" if last else ""
        last_channel = getattr(last, "channel_name", None) or "" if last else ""
        clean_title = _normalize_title(last_title)
        mode = await db.get_autoplay_mode(chat_id)

        # 1. Gather all candidates from potential queries based on current configuration
        candidates = []
        
        # Tier 1: Mode Query
        query_map = {
            "songs": "top trending hindi punjabi indian songs",
            "artists": "top indian music artists hits",
            "albums": "latest indian bollywood albums",
            "playlists": "top hindi punjabi playlist songs",
            "videos": "latest official indian music videos",
        }
        if mode in query_map:
            try:
                candidates = await yt.search_similar_candidates(query_map[mode], limit=12)
            except Exception as e:
                logger.warning("Autoplay mode search failed: %s", e)
        elif mode == "artist" and last_channel:
            try:
                candidates = await yt.search_similar_candidates(f"{last_channel} top indian songs", limit=12)
            except Exception as e:
                logger.warning("Autoplay artist search failed: %s", e)
        elif mode == "trending":
            try:
                candidates = await yt.search_similar_candidates("top trending hindi punjabi indian songs", limit=12)
            except Exception as e:
                logger.warning("Autoplay trending search failed: %s", e)

        # Tier 2: Watch Next / Related Recommendations
        if len(candidates) < 5:
            if clean_title:
                try:
                    fast_similar = await yt.search_similar_candidates(f"songs like {clean_title} indian", limit=8)
                    if fast_similar:
                        candidates.extend(fast_similar)
                except Exception:
                    pass
            if last_id and len(candidates) < 5:
                try:
                    related = await yt.get_related_candidates(last_id, limit=10)
                    if related:
                        candidates.extend(related)
                except Exception:
                    pass

        # Tier 3: Emergency Fallbacks
        fallback_queries = [
            f"{last_channel} {clean_title}" if last_channel and clean_title else None,
            f"{clean_title} full song" if clean_title else None,
            "top trending hindi punjabi indian songs",
            "latest indian music hits",
            "hindi lofi mix songs",
            "bollywood hits unplugged"
        ]
        
        # Validator function
        duration_limit = getattr(config, "DURATION_LIMIT", 7200)
        curr_queue_ids = [getattr(t, "id", None) for t in queue.get_queue(chat_id) if hasattr(t, "id")]

        def _filter(cand_list: list) -> list:
            valid = []
            seen_in_batch_ids = set()
            seen_in_batch_titles = set()
            for t in cand_list:
                if not t or not getattr(t, "id", None):
                    continue
                tid = t.id
                ttitle = getattr(t, "title", "") or ""
                norm_title = _normalize_title(ttitle)
                if tid == last_id or tid in seen_in_batch_ids or tid in curr_queue_ids:
                    continue
                if norm_title and norm_title in seen_in_batch_titles:
                    continue
                if _is_recent(chat_id, tid, ttitle):
                    continue
                dur = getattr(t, "duration_sec", 0) or 0
                if dur > 0 and (dur < 20 or dur > duration_limit):
                    continue
                seen_in_batch_ids.add(tid)
                if norm_title:
                    seen_in_batch_titles.add(norm_title)
                valid.append(t)
            return valid

        # Filter initial list of candidates
        valid_candidates = _filter(candidates)

        # Fallback loop: Query wider sources if we still don't have enough valid candidates
        if not valid_candidates:
            for q in fallback_queries:
                if not q:
                    continue
                try:
                    similar = await yt.search_similar_candidates(q, limit=8)
                    if similar:
                        valid_candidates = _filter(similar)
                        if valid_candidates:
                            break
                except Exception:
                    pass

        # Exhaustion fallback: Prune history (preserving the last 30 songs) and re-filter
        if not valid_candidates and candidates:
            _clear_old_history(chat_id, keep=30)
            valid_candidates = _filter(candidates)

        # Final loop: Verify extraction/stream URL compatibility for candidates
        selected_track = None
        for candidate in valid_candidates:
            try:
                stream_url = await yt.get_stream_url(candidate.id)
                if stream_url:
                    candidate.stream_url = stream_url
                    selected_track = candidate
                    break
                else:
                    path = await yt.download(candidate.id)
                    if path:
                        candidate.file_path = path
                        selected_track = candidate
                        break
            except Exception as e:
                logger.warning("Verification of autoplay candidate %s failed: %s", candidate.id, e)
                continue

        if not selected_track and valid_candidates:
            selected_track = valid_candidates[0]

        return selected_track


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

            candidate = await self._get_autoplay_candidate(chat_id, last)
            if candidate:
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

        # Check if pre-fetched candidate exists on the last track
        pre_track = getattr(last, "_prefetch_autoplay", None) if last else None
        selected_track = None
        if pre_track:
            if pre_track.file_path:
                selected_track = pre_track
                logger.info("Zero-Gap Autoplay HIT! Instant transition for chat %s -> %s", chat_id, selected_track.id)

        if not selected_track:
            selected_track = await self._get_autoplay_candidate(chat_id, last)

        if not selected_track:
            logger.warning("Autoplay exhausted all search candidates for chat %s", chat_id)
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
