# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# Join Request Handler — premium emojis · per-chat enable/disable · auto-approve

import pyrogram.errors as pg_errors
from pyrogram import enums, filters, types
from ishu import app, config, db, logger

# ── Premium Emoji IDs ──────────────────────────────────────────────────────────
ACCEPT_EMOJI_ID = 6296367896398399651   # displays ✅
DECLINE_EMOJI_ID = 6298671811345254603  # displays 😭


def _accept_emoji() -> str:
    return f'<emoji id="{ACCEPT_EMOJI_ID}">✅</emoji>'


def _decline_emoji() -> str:
    return f'<emoji id="{DECLINE_EMOJI_ID}">😭</emoji>'


# ── /joinreq command — admin toggle ────────────────────────────────────────────

@app.on_message(filters.command("joinreq") & filters.group)
async def joinreq_toggle_cmd(client, message: types.Message):
    """
    /joinreq — toggle join-request handling ON / OFF for this group.
    Only group admins / bot owner / sudoers may use it.
    """
    chat_id = message.chat.id
    user = message.from_user

    # Permission check
    is_owner = user.id == config.OWNER_ID
    is_sudo = user.id in await db.get_sudoers()
    if not is_owner and not is_sudo:
        admins = await db.get_admins(chat_id)
        if user.id not in admins:
            return await message.reply(
                f"{_decline_emoji()} <b>Only group admins can toggle join-request handling!</b>",
                parse_mode=enums.ParseMode.HTML,
            )

    currently_on = await db.get_joinreq(chat_id)
    new_state = not currently_on
    await db.set_joinreq(chat_id, new_state)

    if new_state:
        text = (
            f"{_accept_emoji()} <b>Join Request Handler: ENABLED</b>\n\n"
            f"I will now notify this group whenever someone requests to join,\n"
            f"with <b>Accept</b> / <b>Decline</b> buttons for admins."
        )
    else:
        text = (
            f"{_decline_emoji()} <b>Join Request Handler: DISABLED</b>\n\n"
            f"Join requests will no longer be posted here.\n"
            f"Use /joinreq again to re-enable."
        )

    await message.reply(text, parse_mode=enums.ParseMode.HTML)


# ── Join Request Event ─────────────────────────────────────────────────────────

@app.on_chat_join_request()
async def on_join_request(client, request: types.ChatJoinRequest):
    """
    Triggered when a user requests to join a group/channel.
    Only fires when join-request handling is enabled for that chat.
    """
    chat = request.chat
    user = request.from_user

    # Skip if not enabled for this chat
    if not await db.get_joinreq(chat.id):
        return

    logger.info(
        "New join request in chat %s (%s) from user %s (%s)",
        chat.title, chat.id, user.first_name, user.id,
    )

    # 1. Send PM to the requesting user (best-effort)
    pm_text = (
        f"👋 Hello <b>{user.first_name}</b>!\n\n"
        f"Your request to join <b>{chat.title}</b> has been received.\n"
        f"An admin will review and process your request shortly."
    )
    try:
        await client.send_message(user.id, pm_text, parse_mode=enums.ParseMode.HTML)
        await db.add_user(user.id)
    except Exception as e:
        logger.warning("Could not send join request PM to user %s: %s", user.id, e)

    # 2. Post notification in group with premium emoji Accept / Decline buttons
    group_text = (
        f"📥 <b>New Join Request</b>\n\n"
        f"👤 <b>User:</b> {user.mention} (<code>{user.id}</code>)\n"
        f"💬 <b>Group:</b> <b>{chat.title}</b>\n\n"
        f"Admins, please review and approve or decline below:"
    )

    accept_text = f"{_accept_emoji()} Accept"
    decline_text = f"{_decline_emoji()} Decline"

    buttons = types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    text=accept_text,
                    callback_data=f"join_req:accept:{user.id}:{chat.id}",
                ),
                types.InlineKeyboardButton(
                    text=decline_text,
                    callback_data=f"join_req:decline:{user.id}:{chat.id}",
                ),
            ]
        ]
    )

    try:
        await client.send_message(
            chat_id=chat.id,
            text=group_text,
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as err:
        logger.error("Failed to send join request notification in chat %s: %s", chat.id, err)


# ── Callback: Accept / Decline buttons ────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^join_req:(accept|decline):(\d+):(-?\d+)$"))
async def handle_join_request_callback(client, callback: types.CallbackQuery):
    """
    Handles admin clicks on Accept / Decline buttons.
    Verifies permissions, takes action, edits the group message, and PMs the user.
    """
    action, target_user_id_str, chat_id_str = callback.data.split(":")[1:]
    target_user_id = int(target_user_id_str)
    chat_id = int(chat_id_str)
    admin = callback.from_user

    # Permission check
    is_owner = admin.id == config.OWNER_ID
    is_sudo = admin.id in await db.get_sudoers()
    if not is_owner and not is_sudo:
        admins = await db.get_admins(chat_id)
        if admin.id not in admins:
            return await callback.answer(
                "⚠️ Only group admins can approve or decline join requests!",
                show_alert=True,
            )

    # Resolve target user info
    try:
        target_user = await client.get_users(target_user_id)
        target_user_mention = target_user.mention
        target_user_name = target_user.first_name
    except Exception:
        target_user_mention = f"<code>{target_user_id}</code>"
        target_user_name = "User"

    chat_title = callback.message.chat.title or "Group"

    # ── ACCEPT ─────────────────────────────────────────────────────────────────
    if action == "accept":
        try:
            await client.approve_chat_join_request(chat_id, target_user_id)
            await callback.answer("✅ Request Approved!")

            accepted_msg = (
                f"{_accept_emoji()} <b>Join Request Approved</b>\n\n"
                f"👤 <b>User:</b> {target_user_mention}\n"
                f"💬 <b>Group:</b> <b>{chat_title}</b>\n"
                f"👮‍♂️ <b>Approved By:</b> {admin.mention}"
            )
            await callback.message.edit_text(accepted_msg, parse_mode=enums.ParseMode.HTML)

            pm_confirm = (
                f"{_accept_emoji()} <b>Join Request Approved!</b>\n\n"
                f"🎉 Congratulations <b>{target_user_name}</b>!\n"
                f"Your request to join <b>{chat_title}</b> has been approved "
                f"by {admin.mention}.\n\nYou can now join and participate!"
            )
            try:
                await client.send_message(
                    target_user_id,
                    pm_confirm,
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as pm_err:
                logger.warning("Failed to send approval PM to %s: %s", target_user_id, pm_err)

        except pg_errors.UserAlreadyParticipant:
            # User already joined (e.g. via invite link) — treat as success.
            logger.info(
                "Join request for %s in %s: user already a participant (auto-resolved).",
                target_user_id, chat_id,
            )
            await callback.answer("✅ User is already in the group!")
            already_msg = (
                f"{_accept_emoji()} <b>Already a Member</b>\n\n"
                f"👤 <b>User:</b> {target_user_mention}\n"
                f"💬 <b>Group:</b> <b>{chat_title}</b>\n"
                f"ℹ️ User had already joined the group."
            )
            try:
                await callback.message.edit_text(already_msg, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

        except Exception as e:
            logger.error("Failed to approve join request for %s in %s: %s", target_user_id, chat_id, e)
            await callback.answer(f"❌ Error: {e}", show_alert=True)

    # ── DECLINE ────────────────────────────────────────────────────────────────
    elif action == "decline":
        try:
            await client.decline_chat_join_request(chat_id, target_user_id)
            await callback.answer("❌ Request Declined!")

            declined_msg = (
                f"{_decline_emoji()} <b>Join Request Declined</b>\n\n"
                f"👤 <b>User:</b> {target_user_mention}\n"
                f"💬 <b>Group:</b> <b>{chat_title}</b>\n"
                f"👮‍♂️ <b>Declined By:</b> {admin.mention}"
            )
            await callback.message.edit_text(declined_msg, parse_mode=enums.ParseMode.HTML)

            pm_decline = (
                f"{_decline_emoji()} <b>Join Request Declined</b>\n\n"
                f"Hello <b>{target_user_name}</b>, your request to join "
                f"<b>{chat_title}</b> was declined by the admins."
            )
            try:
                await client.send_message(target_user_id, pm_decline, parse_mode=enums.ParseMode.HTML)
            except Exception as pm_err:
                logger.warning("Failed to send decline PM to %s: %s", target_user_id, pm_err)

        except Exception as e:
            logger.error("Failed to decline join request for %s in %s: %s", target_user_id, chat_id, e)
            await callback.answer(f"❌ Error: {e}", show_alert=True)


# ── Personal welcome PM when a user actually joins ─────────────────────────────

@app.on_chat_member_updated()
async def on_user_joined_group(client, update: types.ChatMemberUpdated):
    """
    Sends a personal welcome PM to any user who joins a group where the bot is
    admin AND join-request handling is enabled. Only fires for real new additions.
    """
    old = update.old_chat_member
    new = update.new_chat_member
    if new is None:
        return
    user_obj = new.user
    if user_obj is None or getattr(user_obj, "is_bot", False) or getattr(user_obj, "is_deleted", False):
        return

    new_status = new.status
    if new_status not in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR):
        return
    old_status = old.status if old else enums.ChatMemberStatus.LEFT
    if old_status not in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
        return

    chat = update.chat

    # Only send welcome PM when join-request mode is enabled for this chat
    if not await db.get_joinreq(chat.id):
        return

    welcome_text = (
        f"{_accept_emoji()} <b>Welcome to {chat.title}!</b>\n\n"
        f"Hey <b>{user_obj.first_name}</b>, glad to have you here!\n\n"
        f"🎵 Use the music commands to enjoy songs together.\n"
        f"Type /play &lt;song name&gt; to start playing music!\n\n"
        f"Enjoy your stay! 🚀"
    )
    try:
        await client.send_message(
            user_obj.id,
            welcome_text,
            parse_mode=enums.ParseMode.HTML,
        )
        await db.add_user(user_obj.id)
    except Exception as e:
        logger.debug("Could not send welcome PM to %s: %s", user_obj.id, e)
