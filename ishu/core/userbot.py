# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import os
from time import time
from pyrogram import Client, filters, types, enums, errors

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
        types.InlineKeyboardButton(text="💎 MUSIC BOT", url=bot_link),
        types.InlineKeyboardButton(text="🎵 ADD ME", url=f"{bot_link}?startgroup=new"),
    ])
    rows.append([types.InlineKeyboardButton(text="📢 UPDATES", url=channel_link)])
    if support_link:
        rows.append([types.InlineKeyboardButton(text="💬 SUPPORT", url=support_link)])
    if not rows:
        return None
    return types.InlineKeyboardMarkup(rows)


def _ent_to_dict(ent) -> dict | None:
    try:
        d = {
            "type": getattr(ent, "type", None) or (getattr(ent, "TYPE", None) and ent.TYPE.value),
            "offset": int(getattr(ent, "offset", 0) or 0),
            "length": int(getattr(ent, "length", 0) or 0),
        }
        url = getattr(ent, "url", None)
        user = getattr(ent, "user", None)
        lang = getattr(ent, "language", None)
        cid = getattr(ent, "custom_emoji_id", None)
        if url:
            d["url"] = url
        if user and getattr(user, "id", None):
            d["user_id"] = int(user.id)
        if lang:
            d["language"] = lang
        if cid:
            d["custom_emoji_id"] = str(cid)
        return d
    except Exception:
        return None


def _dict_to_ents(ents_list: list | None) -> list | None:
    if not ents_list:
        return None
    out = []
    for e in ents_list:
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if not t:
            continue
        try:
            kws = {
                "offset": int(e.get("offset", 0)),
                "length": int(e.get("length", 0)),
            }
            if e.get("url"):
                kws["url"] = e["url"]
            if e.get("user_id"):
                kws["user"] = types.User(id=int(e["user_id"]))
            if e.get("language"):
                kws["language"] = e["language"]
            if e.get("custom_emoji_id"):
                kws["custom_emoji_id"] = e["custom_emoji_id"]
            out.append(types.MessageEntity(type=enums.MessageEntityType(t) if not isinstance(t, enums.MessageEntityType) else t, **kws))
        except Exception:
            continue
    return out or None


def _apply_template_vars(text: str, mention: str, bot_link: str, bot_name: str,
                         channel_link: str, support_link: str | None) -> str:
    out = str(text)
    out = out.replace("{mention}", mention)
    out = out.replace("{bot_link}", bot_link)
    out = out.replace("{bot_name}", bot_name)
    out = out.replace("{channel_link}", channel_link)
    if support_link:
        out = out.replace("{support_link}", support_link)
    else:
        out = out.replace("{support_link}", "")
    return out


def _parse_custom_buttons(raw_buttons: list) -> types.InlineKeyboardMarkup | None:
    if not raw_buttons:
        return None
    rows = []
    MAX_ROWS = 12
    total_btns = 0
    for entry in raw_buttons:
        try:
            row_buttons = []
            items = []
            if isinstance(entry, list):
                if entry and isinstance(entry[0], list):
                    items = entry
                else:
                    items = [entry]
            elif isinstance(entry, dict) and "row" in entry:
                items = entry.get("row") or []
            elif isinstance(entry, str):
                row_split = [p.strip() for p in entry.split(" || ") if p.strip()]
                for seg in row_split:
                    if "|" not in seg:
                        continue
                    lab, ur = seg.split("|", 1)
                    items.append([lab.strip(), ur.strip()])
                if items:
                    pass
            for it in items:
                if isinstance(it, list) and len(it) >= 2:
                    label, url = str(it[0]).strip(), str(it[1]).strip()
                elif isinstance(it, dict):
                    label = str(it.get("text") or it.get("label") or "").strip()
                    url = str(it.get("url") or "").strip()
                else:
                    continue
                if not label or not url:
                    continue
                btn_kwargs = {"text": label, "url": url}
                try:
                    row_buttons.append(types.InlineKeyboardButton(**btn_kwargs))
                    total_btns += 1
                except Exception:
                    continue
            if row_buttons:
                rows.append(row_buttons)
                if len(rows) >= MAX_ROWS:
                    break
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

            cfg = {}
            try:
                cfg = await _db.get_assistant_pm_config() or {}
            except Exception:
                cfg = {}

            if cfg.get("disabled"):
                return

            delay_raw = cfg.get("delay")
            if delay_raw is None:
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

            custom_text = cfg.get("text") if isinstance(cfg, dict) else None
            custom_ents_raw = cfg.get("text_entities") if isinstance(cfg, dict) else None
            custom_ents = _dict_to_ents(custom_ents_raw)
            if custom_text:
                text = _apply_template_vars(str(custom_text), mention, bot_link, bot_name, channel_link, support_link)
                entities = custom_ents
            else:
                text = _default_pm_text(mention, bot_link, bot_name, channel_link, support_link)
                entities = None

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

            media_cfg = cfg.get("media") if isinstance(cfg, dict) else None
            try:
                sent = False
                if media_cfg and isinstance(media_cfg, dict):
                    mtype = str(media_cfg.get("type") or "").lower()
                    file_id = media_cfg.get("file_id") or media_cfg.get("file_ref") or media_cfg.get("url")
                    file_ref = media_cfg.get("file_ref")
                    caption = text
                    caption_ents = entities
                    send_kwargs = {"chat_id": message.chat.id, "reply_to_message_id": message.id}
                    if reply_markup is not None:
                        send_kwargs["reply_markup"] = reply_markup
                    try:
                        if mtype in ("photo", "image", "picture"):
                            if not file_id:
                                raise ValueError("photo missing file_id")
                            send_kwargs["photo"] = file_id
                            send_kwargs["caption"] = caption
                            if caption_ents:
                                send_kwargs["caption_entities"] = caption_ents
                            if file_ref:
                                send_kwargs["file_ref"] = file_ref
                            await client.send_photo(**send_kwargs)
                            sent = True
                        elif mtype in ("video", "mp4"):
                            if not file_id:
                                raise ValueError("video missing file_id")
                            send_kwargs["video"] = file_id
                            send_kwargs["caption"] = caption
                            if caption_ents:
                                send_kwargs["caption_entities"] = caption_ents
                            if media_cfg.get("width"):
                                send_kwargs["width"] = int(media_cfg["width"])
                            if media_cfg.get("height"):
                                send_kwargs["height"] = int(media_cfg["height"])
                            if media_cfg.get("duration"):
                                send_kwargs["duration"] = int(media_cfg["duration"])
                            if media_cfg.get("supports_streaming"):
                                send_kwargs["supports_streaming"] = bool(media_cfg["supports_streaming"])
                            thumb = media_cfg.get("thumb")
                            if thumb:
                                send_kwargs["thumb"] = thumb
                            if file_ref:
                                send_kwargs["file_ref"] = file_ref
                            await client.send_video(**send_kwargs)
                            sent = True
                        elif mtype in ("animation", "gif"):
                            if not file_id:
                                raise ValueError("animation missing file_id")
                            send_kwargs["animation"] = file_id
                            send_kwargs["caption"] = caption
                            if caption_ents:
                                send_kwargs["caption_entities"] = caption_ents
                            if file_ref:
                                send_kwargs["file_ref"] = file_ref
                            await client.send_animation(**send_kwargs)
                            sent = True
                        elif mtype in ("audio", "voice", "mp3", "ogg", "m4a"):
                            if not file_id:
                                raise ValueError("audio missing file_id")
                            if mtype in ("voice", "ogg"):
                                send_kwargs["voice"] = file_id
                                send_kwargs["caption"] = caption
                                if caption_ents:
                                    send_kwargs["caption_entities"] = caption_ents
                                await client.send_voice(**send_kwargs)
                            else:
                                send_kwargs["audio"] = file_id
                                send_kwargs["caption"] = caption
                                if caption_ents:
                                    send_kwargs["caption_entities"] = caption_ents
                                if media_cfg.get("title"):
                                    send_kwargs["title"] = media_cfg["title"]
                                if media_cfg.get("performer"):
                                    send_kwargs["performer"] = media_cfg["performer"]
                                if media_cfg.get("duration"):
                                    send_kwargs["duration"] = int(media_cfg["duration"])
                                thumb = media_cfg.get("thumb")
                                if thumb:
                                    send_kwargs["thumb"] = thumb
                                if file_ref:
                                    send_kwargs["file_ref"] = file_ref
                                await client.send_audio(**send_kwargs)
                            sent = True
                        elif mtype in ("document", "file"):
                            if not file_id:
                                raise ValueError("document missing file_id")
                            send_kwargs["document"] = file_id
                            send_kwargs["caption"] = caption
                            if caption_ents:
                                send_kwargs["caption_entities"] = caption_ents
                            if file_ref:
                                send_kwargs["file_ref"] = file_ref
                            await client.send_document(**send_kwargs)
                            sent = True
                        elif mtype in ("sticker",):
                            if not file_id:
                                raise ValueError("sticker missing file_id")
                            send_kwargs_stk = {"chat_id": message.chat.id, "sticker": file_id, "reply_to_message_id": message.id}
                            if file_ref:
                                send_kwargs_stk["file_ref"] = file_ref
                            await client.send_sticker(**send_kwargs_stk)
                            try:
                                if caption and caption.strip():
                                    txt_kwargs = {"text": caption, "disable_web_page_preview": True}
                                    if entities:
                                        txt_kwargs["entities"] = entities
                                    if reply_markup is not None:
                                        txt_kwargs["reply_markup"] = reply_markup
                                    await client.send_message(chat_id=message.chat.id, **txt_kwargs)
                            except Exception:
                                pass
                            sent = True
                    except Exception as media_err:
                        logger.warning(f"[Assistant{ub_num}] PM autoreply media send fail uid={user_id}: {media_err}")
                        sent = False
                if not sent:
                    kwargs = dict(text=text, disable_web_page_preview=True)
                    if entities:
                        kwargs["entities"] = entities
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
