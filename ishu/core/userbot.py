# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import os
from time import time
from pyrogram import Client, filters, types, enums

from pyrogram import errors

from ishu import config, logger


_PM_COOLDOWN_SECONDS = 120
_pm_last_reply = {}


def _default_pm_text(mention: str, bot_link: str, bot_name: str,
                     channel_link: str, support_link: str | None) -> str:
    text = (
        f"👋 Hello, {mention}!\n\n"
        f"🔰 Welcome to {bot_name} Assistant 🔰\n\n"
        f"I'm an assistant account that helps stream HD music in voice chats.\n"
        f"To play music, use our official music bot below — just send /start and add it to your group!\n\n"
        f"🎵 MUSIC BOT: {bot_link}\n"
        f"📢 UPDATES CHANNEL: {channel_link}\n"
    )
    if support_link:
        text += f"💬 SUPPORT GROUP: {support_link}\n"
    text += (
        f"\n👉 Tap the Music Bot link above, press START, then add it to your group's voice chat to enjoy unlimited songs!\n\n"
        f"🎧 Powered by HD Music Streaming Engine ✨"
    )
    return text


def _default_pm_buttons(bot_link: str, channel_link: str, support_link: str | None) -> types.InlineKeyboardMarkup | None:
    rows = []
    rows.append([
        types.InlineKeyboardButton(
            text="💎 MUSIC BOT",
            url=bot_link,
        ),
        types.InlineKeyboardButton(
            text="🎵 ADD ME",
            url=f"{bot_link}?startgroup=new",
        ),
    ])
    rows.append([
        types.InlineKeyboardButton(
            text="📢 UPDATES",
            url=channel_link,
        ),
    ])
    if support_link:
        rows.append([
            types.InlineKeyboardButton(
                text="💬 SUPPORT",
                url=support_link,
            ),
        ])
    if not rows:
        return None
    return types.InlineKeyboardMarkup(rows)


def _parse_custom_buttons(raw_buttons: list) -> types.InlineKeyboardMarkup | None:
    if not raw_buttons:
        return None
    rows = []
    for entry in raw_buttons[:8]:
        try:
            if isinstance(entry, list):
                label, url = entry[0], entry[1]
            else:
                parts = str(entry).split("|", 1)
                if len(parts) != 2:
                    continue
                label, url = parts[0].strip(), parts[1].strip()
            if not label or not url:
                continue
            rows.append([types.InlineKeyboardButton(text=label, url=url)])
        except Exception:
            continue
    if not rows:
        return None
    return types.InlineKeyboardMarkup(rows)


def _register_pm_autoreply(client, ub_num: int) -> None:
    @client.on_message(filters.private & ~filters.me & ~filters.bot & ~filters.service, group=100)
    async def _assistant_pm_reply(_, message):
        if not message.from_user:
            return
        user_id = message.from_user.id
        now = time()
        last = _pm_last_reply.get((ub_num, user_id), 0)
        if now - last < _PM_COOLDOWN_SECONDS:
            return
        _pm_last_reply[(ub_num, user_id)] = now
        try:
            try:
                from ishu import app as _app
                from ishu import db as _db
            except ImportError:
                from Infinix import app as _app
                from Infinix import db as _db

            delay_raw = os.getenv("ASSISTANT_PM_DELAY") or getattr(config, "ASSISTANT_PM_DELAY", None)
            try:
                delay_s = float(delay_raw) if delay_raw else 1.2
            except (TypeError, ValueError):
                delay_s = 1.2

            bot_link = f"https://t.me/{_app.username}" if getattr(_app, "username", None) else f"tg://user?id={getattr(_app, 'id', 0)}"
            channel_link = getattr(config, "SUPPORT_CHANNEL", None) or "https://t.me/"
            support_link = getattr(config, "SUPPORT_CHAT", None)
            bot_name = getattr(_app, "name", "Music Bot")
            mention = message.from_user.mention

            cfg = {}
            try:
                cfg = await _db.get_assistant_pm_config() or {}
            except Exception:
                cfg = {}

            custom_text = cfg.get("text") if isinstance(cfg, dict) else None
            if custom_text:
                text = str(custom_text)
                text = text.replace("{mention}", mention)
                text = text.replace("{bot_link}", bot_link)
                text = text.replace("{bot_name}", bot_name)
                text = text.replace("{channel_link}", channel_link)
                if support_link:
                    text = text.replace("{support_link}", support_link)
            else:
                text = _default_pm_text(mention, bot_link, bot_name, channel_link, support_link)

            custom_btns_raw = cfg.get("buttons") if isinstance(cfg, dict) else None
            reply_markup = _parse_custom_buttons(custom_btns_raw) if custom_btns_raw else None
            if reply_markup is None:
                reply_markup = _default_pm_buttons(bot_link, channel_link, support_link)

            if delay_s > 0:
                try:
                    await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
                    import asyncio
                    await asyncio.sleep(delay_s)
                except Exception:
                    pass

            try:
                kwargs = dict(text=text, disable_web_page_preview=True)
                if reply_markup is not None:
                    kwargs["reply_markup"] = reply_markup
                await message.reply_text(**kwargs)
            except Exception as send_err:
                logger.warning(f"[Assistant{ub_num}] PM autoreply send fail uid={user_id}: {send_err}")
        except Exception as outer:
            logger.warning(f"[Assistant{ub_num}] PM autoreply handler error uid={user_id}: {outer}")


class Userbot(Client):
    def __init__(self):
        """
        Initializes the userbot with multiple clients.

        This method sets up clients for the userbot using predefined session strings.
        Each client is assigned a unique name based on the key in the `clients` dictionary.
        """
        self.clients = []
        clients = {"one": "SESSION1", "two": "SESSION2", "three": "SESSION3"}
        for key, string_key in clients.items():
            name = f"AnonyUB{key[-1]}"
            session = getattr(config, string_key)
            setattr(
                self,
                key,
                Client(
                    name=name,
                    api_id=config.API_ID,
                    api_hash=config.API_HASH,
                    session_string=session,
                ),
            )
            client = getattr(self, key)

    async def boot_client(self, num: int, ub: Client):
        """
        Boot a client and perform initial setup.
        Args:
            num (int): The client number to boot (1, 2, or 3).
            ub (Client): The userbot client instance.
        Raises:
            SystemExit: If the client fails to send a message in the log group.
        """
        clients = {
            1: self.one,
            2: self.two,
            3: self.three,
        }
        client = clients[num]
        try:
            await client.start()
            _register_pm_autoreply(client, num)
        except errors.AuthKeyDuplicated:
            raise SystemExit(
                f"\n\n[Assistant {num}] AuthKeyDuplicated: the SESSION{num} "
                "session string is being used by another instance (or was), "
                "so Telegram invalidated it.\n"
                "FIX (pick ONE):\n"
                "  1. Make sure ONLY ONE deployment uses this session string "
                "(kill the duplicate Railway deploy / local run / other host).\n"
                "  2. If the key is already dead, generate a fresh session with "
                "`python generate_session.py` and update SESSION"
                f"{num} in your env.\n"
            )
        try:
            await client.send_message(config.LOGGER_ID, "Assistant Started")
        except Exception:
            raise SystemExit(f"Assistant {num} failed to send message in log group.")

        client.id = ub.me.id
        client.name = ub.me.first_name
        client.username = ub.me.username
        client.mention = ub.me.mention
        self.clients.append(client)
        try:
            await ub.join_chat("AvyraUpdates")
        except Exception:
            pass
        logger.info(f"Assistant {num} started as @{client.username}")

    async def boot(self):
        """
        Asynchronously starts the assistants.
        """
        if config.SESSION1:
            await self.boot_client(1, self.one)
        if config.SESSION2:
            await self.boot_client(2, self.two)
        if config.SESSION3:
            await self.boot_client(3, self.three)

    async def exit(self):
        """
        Asynchronously stops the assistants.
        """
        if config.SESSION1:
            await self.one.stop()
        if config.SESSION2:
            await self.two.stop()
        if config.SESSION3:
            await self.three.stop()
        logger.info("Assistants stopped.")
