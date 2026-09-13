# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import asyncio

from pyrogram import enums, errors, types

from ishu import app, config, db, logger, queue, yt
from ishu.helpers import utils


from pyrogram.raw import functions, types as raw_types

async def is_vc_active(chat_id: int) -> bool:
    """Check if Telegram Voice Chat / Group Call is currently active in the chat."""
    if chat_id in db.active_calls:
        return True

    # 1. Check via Bot client
    try:
        peer = await app.resolve_peer(chat_id)
        if isinstance(peer, raw_types.InputPeerChannel):
            full = await app.invoke(functions.channels.GetFullChannel(channel=peer))
            return getattr(full.full_chat, "call", None) is not None
        elif isinstance(peer, raw_types.InputPeerChat):
            full = await app.invoke(functions.messages.GetFullChat(chat_id=peer.chat_id))
            return getattr(full.full_chat, "call", None) is not None
    except Exception as e:
        logger.debug("app.invoke GetFullChannel check: %s", e)

    # 2. Check via Assistant Userbot client
    try:
        client = await db.get_client(chat_id)
        if client:
            peer = await client.resolve_peer(chat_id)
            if isinstance(peer, raw_types.InputPeerChannel):
                full = await client.invoke(functions.channels.GetFullChannel(channel=peer))
                return getattr(full.full_chat, "call", None) is not None
            elif isinstance(peer, raw_types.InputPeerChat):
                full = await client.invoke(functions.messages.GetFullChat(chat_id=peer.chat_id))
                return getattr(full.full_chat, "call", None) is not None
    except Exception as e:
        logger.debug("client.invoke GetFullChannel check: %s", e)

    return True

def checkUB(play):
    async def wrapper(_, m: types.Message):
        if not m.from_user:
            return await m.reply_text(m.lang["play_user_invalid"])

        chat_id = m.chat.id
        if m.chat.type != enums.ChatType.SUPERGROUP:
            await m.reply_text(m.lang["play_chat_invalid"])
            return await app.leave_chat(chat_id)

        if not m.reply_to_message and (
            len(m.command) < 2 or (len(m.command) == 2 and m.command[1] == "-f")
        ):
            return await m.reply_text(m.lang["play_usage"])

        if len(queue.get_queue(chat_id)) >= config.QUEUE_LIMIT:
            return await m.reply_text(m.lang["play_queue_full"].format(config.QUEUE_LIMIT))

        # ── Detect Voice Chat Active FIRST ─────────────────────────────────
        if not await is_vc_active(chat_id):
            return await m.reply_text("ʙᴀʙᴜ ᴛᴀɴɪ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴄʜᴀʟᴜ ᴋᴀʀ")


        force = m.command[0].endswith("force") or (
            len(m.command) > 1 and "-f" in m.command[1]
        )
        video = m.command[0][0] == "v" and config.VIDEO_PLAY
        url = utils.get_url(m)
        if url and yt.invalid(url):
            return await m.reply_text(m.lang["play_not_found"].format(config.SUPPORT_CHAT))
        m3u8 = url and not yt.valid(url)

        play_mode = await db.get_play_mode(chat_id)
        if play_mode or force:
            adminlist = await db.get_admins(chat_id)
            if (
                m.from_user.id not in adminlist
                and not await db.is_auth(chat_id, m.from_user.id)
                and not m.from_user.id in app.sudoers
            ):
                return await m.reply_text(m.lang["play_admin"])

        if chat_id not in db.active_calls:
            client = await db.get_client(chat_id)
            is_participant = False
            try:
                member = await app.get_chat_member(chat_id, client.id)
                if member and member.status in [
                    enums.ChatMemberStatus.BANNED,
                    enums.ChatMemberStatus.RESTRICTED,
                ]:
                    try:
                        await app.unban_chat_member(chat_id=chat_id, user_id=client.id)
                    except Exception:
                        return await m.reply_text(
                            m.lang["play_banned"].format(
                                app.name,
                                client.id,
                                client.mention,
                                f"@{client.username}" if client.username else None,
                            )
                        )
                if member and member.status not in [
                    enums.ChatMemberStatus.BANNED,
                    enums.ChatMemberStatus.RESTRICTED,
                    enums.ChatMemberStatus.LEFT,
                ]:
                    is_participant = True
            except errors.ChatAdminRequired:
                return await m.reply_text(m.lang["admin_required"])
            except Exception as e:
                err_str = str(e).upper()
                if "USER_NOT_PARTICIPANT" in err_str or "USERNOTPARTICIPANT" in err_str:
                    is_participant = False
                elif isinstance(e, (TimeoutError, asyncio.TimeoutError)):
                    is_participant = True
                else:
                    is_participant = False

            if not is_participant:
                # 1. Try direct add member first (instant)
                added = False
                try:
                    await app.add_chat_members(chat_id, client.id)
                    added = True
                except Exception:
                    added = False

                if not added:
                    invite_link = None
                    if m.chat.username:
                        invite_link = m.chat.username
                        try:
                            await client.resolve_peer(invite_link)
                        except Exception:
                            pass
                    else:
                        try:
                            chat_obj = await app.get_chat(chat_id)
                            invite_link = chat_obj.invite_link
                            if not invite_link:
                                invite_link = await app.export_chat_invite_link(chat_id)
                        except errors.ChatAdminRequired:
                            return await m.reply_text(m.lang["admin_required"])
                        except Exception as ex:
                            return await m.reply_text(
                                m.lang["play_invite_error"].format(type(ex).__name__)
                            )

                    umm = await m.reply_text(m.lang["play_invite"].format(app.name))
                    await asyncio.sleep(1)
                    try:
                        await client.join_chat(invite_link)
                    except errors.UserAlreadyParticipant:
                        pass
                    except errors.InviteRequestSent:
                        await asyncio.sleep(1)
                        try:
                            await app.approve_chat_join_request(chat_id, client.id)
                        except Exception:
                            pass
                    except Exception as ex:
                        logger.error(f"Error joining chat - {chat_id}: {ex}")
                        return await umm.edit_text(
                            m.lang["play_invite_error"].format(type(ex).__name__)
                        )

                    try:
                        await umm.delete()
                    except Exception:
                        pass

                try:
                    await client.resolve_peer(chat_id)
                except Exception:
                    pass

        if await db.get_cmd_delete(chat_id):
            try:
                await m.delete()
            except Exception:
                pass

        return await play(_, m, force, m3u8, video, url)

    return wrapper
