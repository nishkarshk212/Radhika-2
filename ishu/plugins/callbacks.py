# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import re

from pyrogram import enums, errors, filters, types

from ishu import anon, app, config, db, lang, queue, tg, userbot, yt
from ishu.helpers import admin_check, buttons, can_manage_vc
from ishu.plugins import all_modules


@app.on_callback_query(filters.regex("cancel_dl") & ~app.bl_users)
@lang.language()
async def cancel_dl(_, query: types.CallbackQuery):
    await query.answer()
    await tg.cancel(query)


@app.on_callback_query(filters.regex("controls") & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _controls(_, query: types.CallbackQuery):
    args = query.data.split()
    action, chat_id = args[1], int(args[2])
    qaction = len(args) == 4
    user = query.from_user.mention

    if not await db.get_call(chat_id):
        try:
            return await query.answer(query.lang["not_playing"], show_alert=True)
        except errors.QueryIdInvalid:
            try:
                await query.message.delete()
            except Exception:
                pass
            return

    if action == "status":
        return await query.answer()
    await query.answer(query.lang["processing"], show_alert=True)

    if action == "pause":
        if not await db.playing(chat_id):
            return await query.answer(
                query.lang["play_already_paused"], show_alert=True
            )
        await anon.pause(chat_id)
        if qaction:
            return await query.edit_message_reply_markup(
                reply_markup=buttons.queue_markup(chat_id, query.lang["paused"], False)
            )
        status = query.lang["paused"]
        reply = query.lang["play_paused"].format(user)

    elif action == "resume":
        if await db.playing(chat_id):
            return await query.answer(query.lang["play_not_paused"], show_alert=True)
        await anon.resume(chat_id)
        if qaction:
            return await query.edit_message_reply_markup(
                reply_markup=buttons.queue_markup(chat_id, query.lang["playing"], True)
            )
        reply = query.lang["play_resumed"].format(user)

    elif action == "skip":
        await anon.play_next(chat_id)
        status = query.lang["skipped"]
        reply = query.lang["play_skipped"].format(user)

    elif action == "force":
        pos, media = queue.check_item(chat_id, args[3])
        if not media or pos == -1:
            return await query.edit_message_text(query.lang["play_expired"])

        m_id = queue.get_current(chat_id).message_id
        queue.force_add(chat_id, media, remove=pos)
        try:
            await app.delete_messages(
                chat_id=chat_id, message_ids=[m_id, media.message_id], revoke=True
            )
            media.message_id = None
        except Exception:
            pass

        msg = await app.send_message(chat_id=chat_id, text=query.lang["play_next"])
        # Try stream URL first for instant playback; fall back to download
        if not media.stream_url and not media.file_path:
            media.stream_url = await yt.get_stream_url(media.id, video=media.video)
            if not media.stream_url:
                media.file_path = await yt.download(media.id, video=media.video)
        media.message_id = msg.id
        return await anon.play_media(chat_id, msg, media)

    elif action == "replay":
        media = queue.get_current(chat_id)
        media.user = user
        await anon.replay(chat_id)
        status = query.lang["replayed"]
        reply = query.lang["play_replayed"].format(user)

    elif action == "stop":
        await anon.stop(chat_id)
        status = query.lang["stopped"]
        reply = query.lang["play_stopped"].format(user)

    try:
        if action in ["skip", "replay", "stop"]:
            await query.message.reply_text(reply, quote=False)
            await query.message.delete()
        else:
            mtext = re.sub(
                r"\n\n<blockquote>.*?</blockquote>",
                "",
                query.message.caption.html or query.message.text.html,
                flags=re.DOTALL,
            )
            keyboard = buttons.controls(
                chat_id, status=status if action != "resume" else None,
                autoplay=await db.get_autoplay(chat_id),
            )
        await query.edit_message_text(
            f"{mtext}\n\n<blockquote>{reply}</blockquote>", reply_markup=keyboard
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^autoplay$|^autoplay\s") & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _autoplay(_, query: types.CallbackQuery):
    chat_id = int(query.data.split()[1])
    enable = not await db.get_autoplay(chat_id)
    await db.set_autoplay(chat_id, enable)
    mode = await db.get_autoplay_mode(chat_id)
    await query.answer(
        f"Autoplay {'enabled' if enable else 'disabled'}", show_alert=True
    )
    try:
        await query.edit_message_reply_markup(
            reply_markup=buttons.controls(chat_id, autoplay=enable, mode=mode)
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^autoplay_mode") & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _autoplay_mode_cb(_, query: types.CallbackQuery):
    chat_id = int(query.data.split()[1])
    curr_mode = await db.get_autoplay_mode(chat_id)
    next_mode = {"vibe": "artist", "artist": "trending", "trending": "vibe"}.get(curr_mode, "vibe")
    await db.set_autoplay_mode(chat_id, next_mode)
    labels = {"vibe": "🎧 Vibe Radio", "artist": "🎤 Artist Radio", "trending": "🔥 Trending Hits"}
    await query.answer(f"Autoplay Mode: {labels[next_mode]}", show_alert=True)
    try:
        await query.edit_message_reply_markup(
            reply_markup=buttons.controls(chat_id, autoplay=await db.get_autoplay(chat_id), mode=next_mode)
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^youtube_menu") & ~app.bl_users)
@lang.language()
async def _youtube_menu_cb(_, query: types.CallbackQuery):
    args = query.data.split()
    chat_id = int(args[1]) if len(args) > 1 else query.message.chat.id
    from ishu.helpers._inline import _panel_state
    link = _panel_state.get(chat_id, {}).get("link")
    
    menu_text = (
        "<b><emoji id=5321505140199418151>🔴</emoji> YouTube Music Category & Filter Options:</b>\n\n"
        "Select a section to browse & stream Indian music:\n"
        "• <emoji id=5321505140199418151>🎵</emoji> <b>Songs</b> — High Quality Audio Hits\n"
        "• <emoji id=5233578612665375810>🎤</emoji> <b>Artists</b> — Popular Indian Artists\n"
        "• <emoji id=5462956611033117422>💿</emoji> <b>Albums</b> — Official Music Albums\n"
        "• <emoji id=6007817446398890097>📑</emoji> <b>Playlists</b> — Top Indian Charts & Mixes\n"
        "• <emoji id=5366477429223209600>🎬</emoji> <b>Music Videos</b> — Official HD Music Videos"
    )
    await query.answer("YouTube Music Category Options")
    try:
        if query.message.caption:
            await query.edit_message_caption(
                caption=menu_text,
                reply_markup=buttons.youtube_menu_markup(chat_id, link=link),
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await query.edit_message_text(
                menu_text,
                reply_markup=buttons.youtube_menu_markup(chat_id, link=link),
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^yt_cat\s") & ~app.bl_users)
@lang.language()
async def _yt_cat_cb(_, query: types.CallbackQuery):
    args = query.data.split()
    cat_type = args[1]
    chat_id = int(args[2]) if len(args) > 2 else query.message.chat.id
    
    query_map = {
        "songs": "top trending hindi punjabi indian songs",
        "artists": "top indian music artists hits",
        "albums": "latest indian bollywood albums",
        "playlists": "top hindi punjabi playlist songs",
        "videos": "latest official indian music videos",
    }
    search_q = query_map.get(cat_type, "top trending indian songs")
    cat_labels = {
        "songs": "🎵 Songs",
        "artists": "🎤 Artists",
        "albums": "💿 Albums",
        "playlists": "📑 Playlists",
        "videos": "🎬 Music Videos",
    }
    
    await query.answer(f"Fetching {cat_labels.get(cat_type, cat_type)}...", show_alert=False)
    
    msg = await app.send_message(chat_id=chat_id, text=f"🔍 <b>Fetching {cat_labels.get(cat_type, cat_type)}...</b>", parse_mode=enums.ParseMode.HTML)
    results = await yt.search_similar_candidates(search_q, limit=5)
    if not results:
        return await msg.edit_text("❌ No tracks found for this category.")
    
    first_track = results[0]
    first_track.user = query.from_user.mention
    first_track._chat_id = chat_id
    
    await msg.edit_text(f"▶️ <b>Playing from {cat_labels.get(cat_type, cat_type)}:</b> {first_track.title}")
    
    for t in results[1:]:
        t.user = query.from_user.mention
        t._chat_id = chat_id
        queue.add(chat_id, t)
        
    await anon.play_media(chat_id, msg, first_track)


@app.on_callback_query(filters.regex(r"^yt_menu_back") & ~app.bl_users)
@lang.language()
async def _yt_menu_back_cb(_, query: types.CallbackQuery):
    args = query.data.split()
    chat_id = int(args[1]) if len(args) > 1 else query.message.chat.id
    await query.answer()
    try:
        kb = buttons.controls(
            chat_id,
            autoplay=await db.get_autoplay(chat_id),
            mode=await db.get_autoplay_mode(chat_id),
        )
        if query.message.caption:
            await query.edit_message_caption(
                caption=query.message.caption.html or "<b>Player Control Panel</b>",
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await query.edit_message_text(
                query.message.text.html or "<b>Player Control Panel</b>",
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception:
        pass


@app.on_callback_query(filters.regex("help") & ~app.bl_users)
@lang.language()
async def _help(_, query: types.CallbackQuery):
    data = query.data.split()
    if len(data) == 1:
        return await query.answer(url=f"https://t.me/{app.username}?start=help")

    if data[1] == "back":
        return await query.edit_message_text(
            text=query.lang["help_menu"], reply_markup=buttons.help_markup(query.lang)
        )
    elif data[1] == "close":
        try:
            await query.message.delete()
            return await query.message.reply_to_message.delete()
        except Exception:
            return

    await query.edit_message_text(
        text=query.lang[f"help_{data[1]}"],
        reply_markup=buttons.help_markup(query.lang, True),
    )


@app.on_callback_query(filters.regex("settings") & ~app.bl_users)
@lang.language()
@admin_check
async def _settings_cb(_, query: types.CallbackQuery):
    cmd = query.data.split()
    if len(cmd) == 1:
        return await query.answer()
    await query.answer(query.lang["processing"], show_alert=True)

    chat_id = query.message.chat.id
    _admin = await db.get_play_mode(chat_id)
    _delete = await db.get_cmd_delete(chat_id)
    _language = await db.get_lang(chat_id)

    if cmd[1] == "delete":
        _delete = not _delete
        await db.set_cmd_delete(chat_id, _delete)
    elif cmd[1] == "play":
        await db.set_play_mode(chat_id, _admin)
        _admin = not _admin
    await query.edit_message_reply_markup(
        reply_markup=buttons.settings_markup(
            query.lang,
            _admin,
            _delete,
            _language,
            chat_id,
        )
    )


@app.on_callback_query(filters.regex("^stats_") & ~app.bl_users)
@lang.language()
async def _stats_cb(_, query: types.CallbackQuery):
    data = query.data.split("_")[1]
    if data == "close":
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    elif data == "back":
        admin_count = 0
        for chat_id in await db.get_chats():
            try:
                member = await app.get_chat_member(chat_id, app.me.id)
                if member.status == enums.ChatMemberStatus.ADMINISTRATOR:
                    admin_count += 1
            except Exception:
                pass
        text = (
            "<blockquote><b><emoji id=5231065262228250587>🌐</emoji> ʟ ɪ ʟ ʏ  s ʏ s ᴛ є ϻ\n"
            "──────────────────\n"
            "<emoji id=5431757423134121353>✅</emoji> s ᴛ ᴧ ᴛ υ s : σηʟɪηє ᴧηᴅ ʀєᴧᴅʏ\n"
            "<emoji id=5345905193005371012>⚡</emoji> ᴘ ɪ η ɢ : υʟᴛʀᴧ ғᴧsᴛ\n"
            f"<emoji id=5363992034728229166>✨</emoji> ᴄ ʜ ᴧ ᴛ s : {admin_count}\n"
            "──────────────────\n"
            "<emoji id=6131977542107665315>🔥</emoji> ᴘσᴡєʀєᴅ ʙʏ : ʟ ɪ ʟ ʏ  ϻ υ s ɪ ᴄ</b></blockquote>"
        )
        try:
            await query.edit_message_caption(
                caption=text,
                reply_markup=buttons.stats_key(),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return
    elif data == "net":
        assistants_count = len(userbot.clients)
        blocked_count = len(db.blacklisted) + len(app.bl_users)
        chats_count = len(await db.get_chats())
        users_count = len(await db.get_users())
        modules_count = len(all_modules)
        sudoers_count = len(app.sudoers)
        auto_leave = config.AUTO_LEAVE
        play_limit = config.DURATION_LIMIT // 60
        
        text = (
            "<blockquote><b><emoji id=5364040533498932357>💎</emoji> ʟɪʟʏ  ϻ ᴧ ɪ η ғ ʀ ᴧ ϻ є\n"
            "──────────────────\n"
            f"<emoji id=5855037359371851292>👑</emoji> ᴧ s s ɪ s ᴛ ᴧ η ᴛ s : {assistants_count}\n"
            f"<emoji id=5424857974784925603>🚫</emoji> ʙ ʟ σ ᴄ ᴋ є ᴅ : {blocked_count}\n"
            f"<emoji id=5231065262228250587>🌐</emoji> ᴄ ʜ ᴧ ᴛ s : {chats_count}\n"
            f"<emoji id=5350444080084033572>✨</emoji> υ s є ʀ s : {users_count}\n"
            f"<emoji id=5345905193005371012>⚡</emoji> ϻ σ ᴅ υ ʟ є s : {modules_count}\n"
            f"<emoji id=5260553279321944543>😈</emoji> s υ ᴅ σ є ʀ s : {sudoers_count}\n"
            "──────────────────\n"
            f"<emoji id=6131874750655369599>🔼</emoji> ᴧ υ ᴛ σ  ʟ є ᴧ ᴠ є : {auto_leave}\n"
            f"<emoji id=6131977542107665315>🔥</emoji> ᴘ ʟ ᴧ ʏ  ʟ ɪ ϻ ɪ ᴛ : {play_limit} ϻɪηs</b></blockquote>"
        )
        try:
            await query.edit_message_caption(
                caption=text,
                reply_markup=buttons.stats_net_key(),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
