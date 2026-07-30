# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import re

from pyrogram import enums, types

from ishu import app, config, logger


class Utilities:
    def __init__(self):
        pass

    def format_eta(self, seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}:{seconds % 60:02d} min"
        else:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            return f"{h}:{m:02d}:{s:02d} h"

    def format_size(self, bytes: int) -> str:
        if bytes >= 1024**3:
            return f"{bytes / 1024 ** 3:.2f} GB"
        elif bytes >= 1024**2:
            return f"{bytes / 1024 ** 2:.2f} MB"
        else:
            return f"{bytes / 1024:.2f} KB"

    def to_seconds(self, time: str) -> int:
        parts = [int(p) for p in time.strip().split(":")]
        return sum(value * 60**i for i, value in enumerate(reversed(parts)))


    def get_url(self, message_1: types.Message) -> str | None:
        link = None
        messages = [message_1]

        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)

        for message in messages:
            entities = message.entities or message.caption_entities or []

            for entity in entities:
                if entity.type == enums.MessageEntityType.TEXT_LINK:
                    link = entity.url
                    break
                elif entity.type == enums.MessageEntityType.URL:
                    text = message.text or message.caption
                    if not text:
                        continue
                    link = text[entity.offset: entity.offset + entity.length]
                    break

        if link:
            return link.split("&si")[0].split("?si")[0]
        return None


    async def extract_user(self, msg: types.Message) -> types.User | None:
        if msg.reply_to_message:
            return msg.reply_to_message.from_user

        if msg.entities:
            for e in msg.entities:
                if e.type == enums.MessageEntityType.TEXT_MENTION:
                    return e.user

        if msg.text:
            try:
                if m := re.search(r"@(\w{5,32})", msg.text):
                    return await app.get_users(m.group(0))
                if m := re.search(r"\b\d{6,15}\b", msg.text):
                    return await app.get_users(int(m.group(0)))
            except Exception:
                pass

        return None


    async def play_log(
        self,
        m: types.Message,
        link: str,
        title: str,
        duration: str,
        video: bool = False,
        media=None,
    ) -> None:
        """Forward a detailed play log to the log group (LOGGER_ID).

        Controlled by config.PLAY_LOG so it can be toggled off without a code
        change. We intentionally send this on *every* play request (not just
        when the /logger switch is on) so the owner always has a searchable
        audit trail of what the bot played and from where. The log group is
        skipped for messages sent from inside the log group itself.
        """
        if not getattr(config, "PLAY_LOG", True):
            return
        if m.chat.id == app.logger:
            return

        # Extra detail when a media object is supplied.
        extra = ""
        if media is not None:
            source = "file" if getattr(media, "file_path", None) else (
                "stream" if getattr(media, "stream_url", None) else "fetching"
            )
            extra = (
                f"\n<b>Video:</b> {'yes' if getattr(media, 'video', False) else 'no'}"
                f"\n<b>Source:</b> {source}"
            )
            vid = getattr(media, "id", None)
            if vid:
                extra += f"\n<b>Video ID:</b> <code>{vid}</code>"
            if getattr(media, "view_count", None):
                extra += f"\n<b>Views:</b> {media.view_count}"
            if getattr(media, "channel_name", None):
                extra += f"\n<b>Channel:</b> {media.channel_name}"

        _text = m.lang["play_log"].format(
            app.name,
            m.chat.id,
            m.chat.title,
            m.from_user.id,
            m.from_user.mention,
            link,
            title,
            duration,
        ) + extra
        try:
            await app.send_message(chat_id=app.logger, text=_text)
        except Exception as ex:
            logger.warning("play_log send failed: %s", ex)

    async def error_log(
           self,
           chat_id: int | None = None,
           context: str = "",
           error: Exception | str | None = None,
           chat_title: str | None = None,
           title: str | None = None,
           video: bool = False,
           media=None,
       ) -> None:
           """Forward a playback / download error to the configured log group.

           Controlled by config.ERROR_LOG. This gives the owner a real-time view
           of failures (dead stream URLs, download failures, Telegram server
           errors) instead of having to dig through log.txt.
           """
           if not getattr(config, "ERROR_LOG", True):
               return
           import html
           import traceback

           chat_label = html.escape(str(chat_title or str(chat_id or "?")))
           source_label = "video" if video else "audio"
           err_reason = html.escape(str(error)[:800]) if error else "Unknown error"
           song_title = html.escape(str(title or "—"))
           tb_text = html.escape(traceback.format_exc()[-1200:])

           header = (
               "<blockquote><b>"
               "<emoji id=5364040533498932357>💎</emoji> [ ʟ ɪ ʟ ʏ ϻ ᴧ ɪ n f ʀ ᴧ ϻ є ᴧ s s ɪ s ᴛ ᴧ n ᴛ c ʀ ᴧ s ʜ ] <emoji id=5364040533498932357>💎</emoji>\n"
               f"<emoji id=5422485795627892255>🧪</emoji> ʀ є ᴧ s σ n : {err_reason}\n"
               f"<emoji id=5334607938546953071>📮</emoji> ᴄ ʜ ᴧ ᴛ : {chat_label} | "
               f"<emoji id=5334607938546953071>🎵</emoji> s σ ᴜ ɴ ɢ : {song_title}\n"
               "<emoji id=6131660139729522939>🔥</emoji> s ʏ s ᴛ є ϻ n є є ᴅ s ϻ ᴧ ɪ n ᴛ є n ᴧ n c є ʙ σ s s . . .</b></blockquote>"
           )
           detail = header + "\n<pre>" + tb_text + "</pre>"
           try:
               await app.send_message(
                   chat_id=(app.logger or chat_id or 0),
                   text=detail,
                   parse_mode=enums.ParseMode.HTML,
               )
           except Exception as ex:
               logger.warning("error_log send failed: %s", ex)


    async def send_log(self, m: types.Message, chat: bool = False) -> None:
        if chat:
            user = m.from_user
            return await app.send_message(
                chat_id=app.logger,
                text=m.lang["log_chat"].format(
                    m.chat.id,
                    m.chat.title,
                    user.id if user else 0,
                    user.mention if user else "Anonymous",
                ),
            )

        await app.send_message(
            chat_id=app.logger,
            text=m.lang["log_user"].format(
                m.from_user.id,
                f"@{m.from_user.username}",
                m.from_user.mention,
            ),
        )
