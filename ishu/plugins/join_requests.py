# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# Join Request Handler with Colourful Accept/Reject Buttons & PM Notifications

import asyncio
from pyrogram import enums, filters, types
from ishu import app, config, db, logger

ACCEPT_EMOJI_ID = "6296367896398399651"
DECLINE_EMOJI_ID = "6298671811345254603"


@app.on_chat_join_request()
async def on_join_request(client, request: types.ChatJoinRequest):
    """
    Triggered when a user requests to join a group/channel.
    - Sends a PM to the requesting user.
    - Posts a notification in the group with colourful Accept/Decline buttons.
    """
    chat = request.chat
    user = request.from_user

    logger.info(
        "New join request in chat %s (%s) from user %s (%s)",
        chat.title, chat.id, user.first_name, user.id,
    )

    # 1. Send PM to the requesting user
    pm_text = (
        f"👋 Hello <b>{user.first_name}</b>!\n\n"
        f"Your request to join <b>{chat.title}</b> has been received.\n"
        f"An admin will review and process your request shortly."
    )
    try:
        await client.send_message(user.id, pm_text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning("Could not send join request PM to user %s: %s", user.id, e)

    # 2. Send notification in group with colourful Accept/Decline buttons
    group_text = (
        f"📥 <b>New Join Request</b>\n\n"
        f"👤 <b>User:</b> {user.mention} (<code>{user.id}</code>)\n"
        f"💬 <b>Group:</b> <b>{chat.title}</b>\n\n"
        f"Admins, please review and approve or decline below:"
    )

    # Colourful buttons — 🟢 green for Accept, 🔴 red for Reject
    accept_btn_text = f'<emoji id="{ACCEPT_EMOJI_ID}">🟢</emoji> ✅ Accept'
    decline_btn_text = f'<emoji id="{DECLINE_EMOJI_ID}">🔴</emoji> ❌ Reject'

    buttons = types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    text=accept_btn_text,
                    callback_data=f"join_req:accept:{user.id}:{chat.id}",
                ),
                types.InlineKeyboardButton(
                    text=decline_btn_text,
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


@app.on_callback_query(filters.regex(r"^join_req:(accept|decline):(\d+):(-?\d+)$"))
async def handle_join_request_callback(client, callback: types.CallbackQuery):
    """
    Handles admin clicks on Accept or Reject buttons.
    - Verifies admin permissions.
    - Approves or declines the join request.
    - Edits the group message with confirmation.
    - Sends a personal confirmation PM to the user.
    """
    action, target_user_id_str, chat_id_str = callback.data.split(":")[1:]
    target_user_id = int(target_user_id_str)
    chat_id = int(chat_id_str)
    admin = callback.from_user

    # Verify admin permissions
    if admin.id != config.OWNER_ID and admin.id not in await db.get_sudoers():
        admins = await db.get_admins(chat_id)
        if admin.id not in admins:
            return await callback.answer(
                "⚠️ Only group admins can approve or decline join requests!",
                show_alert=True,
            )

    # Get target user info
    try:
        target_user = await client.get_users(target_user_id)
        target_user_mention = target_user.mention
        target_user_name = target_user.first_name
    except Exception:
        target_user_mention = f"<code>{target_user_id}</code>"
        target_user_name = "User"

    chat_title = callback.message.chat.title or "Group"

    if action == "accept":
        try:
            await client.approve_chat_join_request(chat_id, target_user_id)
            await callback.answer("✅ Request Approved!")

            # Edit group message
            accepted_msg = (
                f'<emoji id="{ACCEPT_EMOJI_ID}">🟢</emoji> <b>Join Request Approved</b>\n\n'
                f"👤 <b>User:</b> {target_user_mention}\n"
                f"💬 <b>Group:</b> <b>{chat_title}</b>\n"
                f"👮‍♂️ <b>Approved By:</b> {admin.mention}"
            )
            await callback.message.edit_text(accepted_msg, parse_mode=enums.ParseMode.HTML)

            # Send personal approval PM with a coloured reply keyboard
            pm_confirm = (
                f'<emoji id="{ACCEPT_EMOJI_ID}">🟢</emoji> <b>Join Request Approved!</b>\n\n'
                f"🎉 Congratulations <b>{target_user_name}</b>!\n"
                f"Your request to join <b>{chat_title}</b> has been approved by {admin.mention}.\n\n"
                f"You can now join and participate in the group!"
            )
            try:
                await client.send_message(
                    target_user_id,
                    pm_confirm,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=types.ReplyKeyboardMarkup(
                        [
                            [
                                types.KeyboardButton("✅ Joined — Let's Go! 🎵"),
                            ],
                            [
                                types.KeyboardButton("📢 Check Group"),
                            ],
                        ],
                        resize_keyboard=True,
                        one_time_keyboard=True,
                        selective=True,
                    ),
                )
            except Exception as pm_err:
                logger.warning("Failed to send approval PM to %s: %s", target_user_id, pm_err)

        except Exception as e:
            logger.error("Failed to approve join request for %s in %s: %s", target_user_id, chat_id, e)
            await callback.answer(f"❌ Error: {e}", show_alert=True)

    elif action == "decline":
        try:
            await client.decline_chat_join_request(chat_id, target_user_id)
            await callback.answer("❌ Request Declined!")

            # Edit group message
            declined_msg = (
                f'<emoji id="{DECLINE_EMOJI_ID}">🔴</emoji> <b>Join Request Declined</b>\n\n'
                f"👤 <b>User:</b> {target_user_mention}\n"
                f"💬 <b>Group:</b> <b>{chat_title}</b>\n"
                f"👮‍♂️ <b>Declined By:</b> {admin.mention}"
            )
            await callback.message.edit_text(declined_msg, parse_mode=enums.ParseMode.HTML)

            # Send personal decline PM
            pm_decline = (
                f'<emoji id="{DECLINE_EMOJI_ID}">🔴</emoji> <b>Join Request Declined</b>\n\n'
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


# ── Personal welcome PM when a user actually joins the group ──

@app.on_chat_member_updated()
async def on_user_joined_group(client, update: types.ChatMemberUpdated):
    """
    Sends a personal welcome PM to any user who is added to or joins a group
    where the bot is an admin. Only fires for *new* additions (old_status is
    Left / Banned, new_status is Member / Administrator).
    """
    old = update.old_chat_member
    new = update.new_chat_member
    user = update.from_user or update.new_chat_member.user

    # Only trigger on: Left/Banned → Member/Admin
    if new.status not in (
        enums.ChatMemberStatus.MEMBER,
        enums.ChatMemberStatus.ADMINISTRATOR,
    ):
        return
    if old.status not in (
        enums.ChatMemberStatus.LEFT,
        enums.ChatMemberStatus.BANNED,
    ):
        return

    chat = update.chat
    user_obj = update.new_chat_member.user

    # Skip bots and self-joins
    if user_obj.is_bot or user_obj.is_deleted:
        return

    welcome_text = (
        f"🎉 <b>Welcome to {chat.title}!</b>\n\n"
        f"Hey <b>{user_obj.first_name}</b>, glad to have you here!\n\n"
        f"🎵 Use the music commands to enjoy songs together.\n"
        f"Type /play <song name> to start playing music!\n\n"
        f"Enjoy your stay! 🚀"
    )
    try:
        await client.send_message(
            user_obj.id,
            welcome_text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=types.ReplyKeyboardMarkup(
                [
                    [
                        types.KeyboardButton("🎵 Play a Song"),
                        types.KeyboardButton("📋 Help"),
                    ],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
                selective=True,
            ),
        )
    except Exception as e:
        logger.debug("Could not send welcome PM to %s: %s", user_obj.id, e)
