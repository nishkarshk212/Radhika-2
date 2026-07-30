# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic

import asyncio
import random
from pyrogram import enums, filters, types

from ishu import app, boot, config, db, lang
from ishu.helpers import buttons, utils


@app.on_message(filters.command(["help"]) & filters.private & ~app.bl_users)
@lang.language()
async def _help(_, m: types.Message):
    await m.reply_text(
        text=m.lang["help_menu"],
        reply_markup=buttons.help_markup(m.lang),
        quote=True,
    )



START_IMAGES = [
    "https://i.ibb.co/nMTGfdrC/Cute-girl-pic.jpg",
    "https://i.ibb.co/5X3GtfK3/2.jpg",
    "https://i.ibb.co/wDnd11t/Bandana-hairstyle.jpg",
    "https://i.ibb.co/TqH6tNYM/image.jpg",
    "https://i.ibb.co/0jw84PFV/Elegant-Indian-beauty-with-dreamy-aesthetic-vibes.jpg",
]

START_ANIMATION = [
    "<blockquote><b><emoji id=5411285122215332752></emoji> ɪɴɪᴛɪᴀʟɪᴢɪɴɢ... <emoji id=5425004944270850753>💀</emoji></b></blockquote>",
    "<blockquote><b><emoji id=5411285122215332752></emoji>  ʟᴏᴀᴅɪɴɢ ᴍᴜꜱɪᴄ ᴇɴɢɪɴᴇ... <emoji id=5470135030393090150>🎵</emoji></b></blockquote>",
    "<blockquote><b><emoji id=5411285122215332752></emoji>  ᴄᴏɴɴᴇᴄᴛɪɴɢ ᴛᴏ ꜱᴇʀᴠᴇʀ... <emoji id=5447410659077661506>🌐</emoji></b></blockquote>",
    "<blockquote><b><emoji id=5411285122215332752></emoji>  ꜰᴇᴛᴄʜɪɴɢ ᴘʟᴀʏʟɪꜱᴛ... <emoji id=5431721976769027887>📂</emoji></b></blockquote>",
    "<blockquote><b><emoji id=5411285122215332752></emoji>  ᴄʜᴇᴄᴋɪɴɢ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ... <emoji id=5294339927318739359>🎙️</emoji></b></blockquote>",
    "<blockquote><b><emoji id=5411285122215332752></emoji>  ᴏᴘᴛɪᴍɪᴢɪɴɢ ꜱᴛʀᴇᴀᴍ... <emoji id=5372917041193828849>🚀</emoji></b></blockquote>",
    "<blockquote><b><emoji id=5411285122215332752></emoji>  ꜱʏꜱᴛᴇᴍ ꜱᴛᴀʀᴛᴇᴅ! <emoji id=5895449329430173749>❤️</emoji> <emoji id=6082505415847842709>☺️</emoji></b></blockquote>",
]


@app.on_message(filters.command(["start"]))
@lang.language()
async def start(_, message: types.Message):
    if message.from_user.id in app.bl_users and message.from_user.id not in db.notified:
        return await message.reply_text(message.lang["bl_user_notify"])

    if len(message.command) > 1 and message.command[1] == "help":
        return await _help(_, message)

    private = message.chat.type == enums.ChatType.PRIVATE
    key = buttons.start_key(message.lang, private)

    if private:
        # Delete the /start command message
        try:
            await message.delete()
        except Exception:
            pass
        # Play animation
        anim_msg = await message.reply_text(START_ANIMATION[0], parse_mode=enums.ParseMode.HTML)
        for frame in START_ANIMATION[1:]:
            await asyncio.sleep(0.8)
            await anim_msg.edit_text(frame, parse_mode=enums.ParseMode.HTML)
        await asyncio.sleep(0.5)
        await anim_msg.delete()

        # Build clickable mentions
        user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.first_name}</a>'
        bot_mention = f'<a href="https://t.me/{app.username}">{app.name}</a>'

        # ONE message: photo + start_pm blockquote caption + inline buttons
        await message.reply_photo(
            photo=random.choice(START_IMAGES),
            caption=message.lang["start_pm"].format(user_mention, bot_mention),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=key,
            has_spoiler=True,
        )
    else:
        import time
        uptime_sec = int(time.time() - boot)
        days = uptime_sec // 86400
        hours = (uptime_sec // 3600) % 24
        minutes = (uptime_sec // 60) % 60
        seconds = uptime_sec % 60
        
        if days > 0:
            uptime_str = f"{days}ᴅᴧʏꜱ, {hours}ʜ:{minutes}ϻ:{seconds}ꜱ"
        else:
            uptime_str = f"{hours}ʜ:{minutes}ϻ:{seconds}ꜱ"

        caption_text = (
            "<blockquote><b><emoji id=6125150373763094821>⭐</emoji> ᴀ ᴄ ᴛ ɪ ᴠ ᴇ  ᴀ ɴ ᴅ  ᴀ ʟ ɪ ᴠ ᴇ\n"
            "──────────────────\n"
            "<emoji id=6124898345082165755>⚡</emoji> ꜱʏꜱᴛᴇᴍ ɪꜱ ʀᴜɴɴɪɴɢ ꜱᴍᴏᴏᴛʜʟʏ .\n"
            "<emoji id=6125150373763094821>⭐</emoji> ᴄᴏʀᴇ : ˹ 🇨🇦 ♫ ʟɪʟʏ፝֟ ꭙ ᴍᴜꜱɪᴄʙᴏᴛ 𐦍 ˼ ᴇɴɢɪɴᴇ ᴠ2.0\n"
            "──────────────────\n"
            "<emoji id=6124898345082165755>⚡</emoji> ᴜᴘᴛɪᴍᴇ : " + uptime_str + "</b></blockquote>"
        )
        await message.reply_photo(
            photo=random.choice(START_IMAGES),
            caption=caption_text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=key,
            quote=True,
            has_spoiler=True,
        )

    if private:
        if await db.is_user(message.from_user.id):
            return
        await utils.send_log(message)
        await db.add_user(message.from_user.id)
    else:
        if await db.is_chat(message.chat.id):
            return
        await utils.send_log(message, True)
        await db.add_chat(message.chat.id, message.chat.title)



@app.on_message(filters.command(["playmode", "settings"]) & filters.group & ~app.bl_users)
@lang.language()
async def settings(_, message: types.Message):
    admin_only = await db.get_play_mode(message.chat.id)
    cmd_delete = await db.get_cmd_delete(message.chat.id)
    _language = await db.get_lang(message.chat.id)
    await message.reply_text(
        text=message.lang["start_settings"].format(message.chat.title),
        reply_markup=buttons.settings_markup(
            message.lang, admin_only, cmd_delete, _language, message.chat.id
        ),
        quote=True,
    )


@app.on_message(filters.new_chat_members, group=7)
@lang.language()
async def _new_member(_, message: types.Message):
    for member in message.new_chat_members:
        if member.id == app.id:
            if not await db.is_chat(message.chat.id):
                await utils.send_log(message, True)
                await db.add_chat(message.chat.id, message.chat.title)
        elif not member.is_bot:
            welcome_pm_text = (
                f"👋 <b>Welcome {member.mention}!</b>\n\n"
                f"Thank you for joining <b>{message.chat.title}</b>! 🎵\n\n"
                f"I am <b>{app.name}</b>, your ultimate high-quality Music & Video Bot!\n"
                f"You can stream music, HD videos, playlists, and radio directly in voice chats.\n\n"
                f"🎧 <b>Quick Commands:</b>\n"
                f"• <code>/play [song name]</code> — Play audio song\n"
                f"• <code>/vplay [video name]</code> — Play video song\n"
                f"• <code>/settings</code> — Group settings\n\n"
                f"Enjoy listening to music! 🎶"
            )
            try:
                pm_kb = types.InlineKeyboardMarkup(
                    [
                        [
                            types.InlineKeyboardButton(
                                text="➕ Add Me To Your Group",
                                url=f"https://t.me/{app.username}?startgroup=true",
                            ),
                        ]
                    ]
                )
                await app.send_message(
                    chat_id=member.id,
                    text=welcome_pm_text,
                    reply_markup=pm_kb,
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as e:
                logger.debug("Could not send direct welcome PM to user %s: %s", member.id, e)


@app.on_message(filters.command(["groupdetail", "groups"]) & filters.private)
async def group_detail(_, message: types.Message):
    from ishu import config
    
    if message.from_user.id != config.OWNER_ID:
        return
        
    chat_ids = await db.get_chats()
    if not chat_ids:
        return await message.reply_text("No groups added yet!")
        
    text = "<u><b>Group List</b></u>\n\n"
    for chat_id in chat_ids:
        try:
            chat = await app.get_chat(chat_id)
            title = chat.title
            # Update title in DB if missing
            doc = await db.chatsdb.find_one({"_id": chat_id})
            if not doc or not doc.get("title"):
                await db.chatsdb.update_one({"_id": chat_id}, {"$set": {"title": title}}, upsert=True)
            text += f"• <code>{chat_id}</code> - {title}\n"
        except Exception as e:
            # Fallback to stored title
            doc = await db.chatsdb.find_one({"_id": chat_id})
            stored_title = doc.get("title", "Unknown") if doc else "Unknown"
            text += f"• <code>{chat_id}</code> - {stored_title} (Failed to fetch details)\n"
    
    if len(text) > 4096:
        # Split into parts
        for i in range(0, len(text), 4096):
            await message.reply_text(text[i:i+4096])
    else:
        await message.reply_text(text)


@app.on_message(filters.regex(r"^/$") & ~app.bl_users)
@lang.language()
async def slash_help(_, message: types.Message):
    await message.reply_text(
        text=message.lang["help_menu"],
        reply_markup=buttons.help_markup(message.lang),
        quote=True,
    )


# Catch-all: record EVERY group the bot is active in so /stats reflects reality.
# Bots cannot enumerate their own dialogs (GetDialogs is forbidden), so the only
# reliable way to know a group is to observe activity in it. Low priority
# (group=99) so it never precedes command/service handlers.
@app.on_message(filters.group & ~app.bl_users, group=99)
async def _record_group(_, message: types.Message):
    chat = message.chat
    if chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return
    await db.add_chat(chat.id, getattr(chat, "title", None))
