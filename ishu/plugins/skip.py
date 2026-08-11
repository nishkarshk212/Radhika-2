# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


from pyrogram import enums, filters, types
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ishu import anon, app, db, lang
from ishu.helpers import admin_check, can_skip


@app.on_message(filters.command(["skip", "next"]) & filters.group & ~app.bl_users)
@lang.language()
async def _skip(_, m: types.Message):
    if not await can_skip(m.chat.id, m.from_user.id):
        return await m.reply_text(
            "<b><emoji id=5424857974784925603>⚠️</emoji> Only admins can skip songs in this chat.</b>\n"
            "<i>An admin can change this setting using <code>/skipmode everyone</code>.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    if not await db.get_call(m.chat.id):
        return await m.reply_text(m.lang["not_playing"])

    await anon.play_next(m.chat.id)
    await m.reply_text(m.lang["play_skipped"].format(m.from_user.mention))


@app.on_message(filters.command(["skipmode", "cskipmode"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def _skipmode(_, m: types.Message):
    args = m.text.split()
    chat_id = m.chat.id
    if len(args) > 1:
        param = args[1].lower()
        if param in ["admin", "admins", "adminonly", "on", "true", "1"]:
            await db.set_skip_mode(chat_id, admin_only=True)
            return await m.reply_text(
                "<b><emoji id=5431757423134121353>✅</emoji> Skip Mode updated: Admins Only</b>\n"
                "<i>Now only admins and auth users can skip songs in this chat.</i>",
                parse_mode=enums.ParseMode.HTML,
            )
        elif param in ["everyone", "all", "off", "false", "0"]:
            await db.set_skip_mode(chat_id, admin_only=False)
            return await m.reply_text(
                "<b><emoji id=5431757423134121353>✅</emoji> Skip Mode updated: Everyone</b>\n"
                "<i>Now everyone in this chat can skip songs.</i>",
                parse_mode=enums.ParseMode.HTML,
            )

    is_admin_only = await db.get_skip_mode(chat_id)
    status_text = "🔒 <b>Admins Only</b>" if is_admin_only else "🌐 <b>Everyone</b> (Default)"
    text = (
        f"<b><emoji id=5321505140199418151>🎵</emoji> Skip Command Mode Setting</b>\n\n"
        f"<b>Current Mode:</b> {status_text}\n\n"
        f"• <b>Everyone</b> — Any member in the group can use <code>/skip</code>\n"
        f"• <b>Admins Only</b> — Only group admins can use <code>/skip</code>\n\n"
        f"<i>Toggle mode using buttons below or type <code>/skipmode everyone</code> / <code>/skipmode admin</code></i>"
    )
    buttons_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Everyone", callback_data="skipmode_set_everyone"),
            InlineKeyboardButton("🔒 Admins Only", callback_data="skipmode_set_admin"),
        ],
        [
            InlineKeyboardButton("🗑 Close", callback_data="stats_close"),
        ],
    ])
    await m.reply_text(text, reply_markup=buttons_markup, parse_mode=enums.ParseMode.HTML)


@app.on_callback_query(filters.regex(r"^skipmode_set_") & ~app.bl_users)
@lang.language()
@admin_check
async def _skipmode_cb(_, query: types.CallbackQuery):
    action = query.data.split("_")[-1]
    chat_id = query.message.chat.id
    if action == "admin":
        await db.set_skip_mode(chat_id, admin_only=True)
        await query.answer("Skip Mode set to Admins Only 🔒", show_alert=True)
    else:
        await db.set_skip_mode(chat_id, admin_only=False)
        await query.answer("Skip Mode set to Everyone 🌐", show_alert=True)

    is_admin_only = await db.get_skip_mode(chat_id)
    status_text = "🔒 <b>Admins Only</b>" if is_admin_only else "🌐 <b>Everyone</b> (Default)"
    text = (
        f"<b><emoji id=5321505140199418151>🎵</emoji> Skip Command Mode Setting</b>\n\n"
        f"<b>Current Mode:</b> {status_text}\n\n"
        f"• <b>Everyone</b> — Any member in the group can use <code>/skip</code>\n"
        f"• <b>Admins Only</b> — Only group admins can use <code>/skip</code>\n\n"
        f"<i>Toggle mode using buttons below or type <code>/skipmode everyone</code> / <code>/skipmode admin</code></i>"
    )
    buttons_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Everyone", callback_data="skipmode_set_everyone"),
            InlineKeyboardButton("🔒 Admins Only", callback_data="skipmode_set_admin"),
        ],
        [
            InlineKeyboardButton("🗑 Close", callback_data="stats_close"),
        ],
    ])
    try:
        await query.edit_message_text(text, reply_markup=buttons_markup, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass
