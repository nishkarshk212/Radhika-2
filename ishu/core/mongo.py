# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import os
from random import randint
from time import time
from datetime import datetime, timezone
import certifi

from pymongo import AsyncMongoClient

from ishu import config, logger, userbot


class MongoDB:
    def __init__(self):
        """
        Initialize the MongoDB connection.
        """
        self.mongo = AsyncMongoClient(config.MONGO_URL, serverSelectionTimeoutMS=30000, tlsCAFile=certifi.where())
        self.db = self.mongo.Yukki

        storage_url = os.getenv("STORAGE_MONGO_URL") or getattr(config, "STORAGE_MONGO_URL", None) or config.MONGO_URL
        self.storage_mongo = AsyncMongoClient(storage_url, serverSelectionTimeoutMS=30000, tlsCAFile=certifi.where())
        self.storage_db = self.storage_mongo.SharedStorage

        self.admin_list = {}
        self.active_calls = {}
        self.admin_play = []
        self.blacklisted = []
        self.cmd_delete = []
        self.loop = {}
        self.notified = []
        self.autoplay = []
        self.cache = self.db.cache
        # Default: logger is ON whenever LOGGER_ID is configured. The value is
        # overwritten by get_logger() at boot if a persisted setting exists.
        self.logger = bool(config.LOGGER_ID)

        self.assistant = {}
        self.assistantdb = self.db.assistant

        self.auth = {}
        self.authdb = self.db.auth

        self.chats = []
        self.chatsdb = self.db.chats

        self.lang = {}
        self.langdb = self.db.lang

        self.users = []
        self.usersdb = self.db.users
        self.song_cachedb = self.db.song_cache
        self.music_cachedb = self.storage_db.music_cache

    async def connect(self) -> None:
        """Check if we can connect to the database.

        Raises:
            SystemExit: If the connection to the database fails.
        """
        try:
            start = time()
            await self.mongo.admin.command("ping")
            logger.info(f"Database connection successful. ({time() - start:.2f}s)")
            await self.load_cache()
            try:
                await self.music_cachedb.create_index("last_played")
            except Exception:
                pass
        except Exception as e:
            raise SystemExit(f"Database connection failed: {type(e).__name__}") from e

    async def close(self) -> None:
        """Close the connection to the database."""
        await self.mongo.close()
        logger.info("Database connection closed.")

    # CACHE
    async def get_call(self, chat_id: int) -> bool:
        return chat_id in self.active_calls

    async def add_call(self, chat_id: int) -> None:
        self.active_calls[chat_id] = 1

    async def remove_call(self, chat_id: int) -> None:
        self.active_calls.pop(chat_id, None)

    async def playing(self, chat_id: int, paused: bool = None) -> bool | None:
        if paused is not None:
            self.active_calls[chat_id] = int(not paused)
        return bool(self.active_calls.get(chat_id, 0))

    async def get_admins(self, chat_id: int, reload: bool = False) -> list[int]:
        from ishu.helpers._admins import reload_admins

        if chat_id not in self.admin_list or reload:
            self.admin_list[chat_id] = await reload_admins(chat_id)
        return self.admin_list[chat_id]

    async def get_loop(self, chat_id: int) -> int:
        return self.loop.get(chat_id, 0)

    async def set_loop(self, chat_id: int, count: int) -> None:
        self.loop[chat_id] = count

    # AUTH METHODS
    async def _get_auth(self, chat_id: int) -> set[int]:
        if chat_id not in self.auth:
            doc = await self.authdb.find_one({"_id": chat_id}) or {}
            self.auth[chat_id] = set(doc.get("user_ids", []))
        return self.auth[chat_id]

    async def is_auth(self, chat_id: int, user_id: int) -> bool:
        return user_id in await self._get_auth(chat_id)

    async def add_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id not in users:
            users.add(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$addToSet": {"user_ids": user_id}}, upsert=True
            )

    async def rm_auth(self, chat_id: int, user_id: int) -> None:
        users = await self._get_auth(chat_id)
        if user_id in users:
            users.discard(user_id)
            await self.authdb.update_one(
                {"_id": chat_id}, {"$pull": {"user_ids": user_id}}
            )

    # ASSISTANT METHODS
    async def set_assistant(self, chat_id: int) -> int:
        num = randint(1, len(userbot.clients))
        await self.assistantdb.update_one(
            {"_id": chat_id},
            {"$set": {"num": num}},
            upsert=True,
        )
        self.assistant[chat_id] = num
        return num

    async def get_assistant(self, chat_id: int):
        from ishu import anon

        if chat_id not in self.assistant:
            doc = await self.assistantdb.find_one({"_id": chat_id})
            num = doc["num"] if doc else None

            if not num or num > len(anon.clients):
                num = await self.set_assistant(chat_id)
            self.assistant[chat_id] = num

        return anon.clients[self.assistant[chat_id] - 1]

    async def get_client(self, chat_id: int):
        if chat_id not in self.assistant:
            await self.get_assistant(chat_id)

        num = self.assistant[chat_id]
        if num > len(userbot.clients):
            num = await self.set_assistant(chat_id)
            self.assistant[chat_id] = num

        return {1: userbot.one, 2: userbot.two, 3: userbot.three}.get(num)


    # ULTRA-FAST HYBRID MUSIC CACHE METHODS
    async def get_music_cache(self, video_id: str, is_video: bool = False) -> dict | None:
        """Get full metadata document for a song from shared MongoDB."""
        try:
            key = f"{video_id}_v" if is_video else f"{video_id}_a"
            return await self.music_cachedb.find_one({"_id": key})
        except Exception as e:
            logger.warning("get_music_cache failed for %s: %s", video_id, e)
            return None

    async def save_music_cache(
        self,
        video_id: str,
        title: str,
        duration: int,
        file_path: str,
        file_size: int,
        file_id: str,
        file_unique_id: str,
        message_id: int,
        channel_id: int,
        added_by: int = 0,
        is_video: bool = False,
    ) -> None:
        """Store song metadata in shared MongoDB as the single source of truth."""
        try:
            key = f"{video_id}_v" if is_video else f"{video_id}_a"
            now_iso = datetime.now(timezone.utc).isoformat()
            doc = {
                "_id": key,
                "video_id": video_id,
                "title": title,
                "duration": duration,
                "file_path": file_path,
                "file_size": file_size,
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "message_id": message_id,
                "channel_id": channel_id,
                "added_by": added_by,
                "is_video": is_video,
                "play_count": 1,
                "last_played": now_iso,
                "created_at": now_iso,
            }
            await self.music_cachedb.update_one(
                {"_id": key},
                {"$set": doc},
                upsert=True,
            )
        except Exception as e:
            logger.error("save_music_cache failed for %s: %s", video_id, e)

    async def update_music_stats(self, video_id: str, is_video: bool = False) -> None:
        """Atomically increment play_count and update last_played timestamp."""
        try:
            key = f"{video_id}_v" if is_video else f"{video_id}_a"
            now_iso = datetime.now(timezone.utc).isoformat()
            await self.music_cachedb.update_one(
                {"_id": key},
                {
                    "$inc": {"play_count": 1},
                    "$set": {"last_played": now_iso},
                },
            )
        except Exception as e:
            logger.warning("update_music_stats failed for %s: %s", video_id, e)

    async def update_music_file_id(
        self, video_id: str, file_id: str, file_unique_id: str = "", is_video: bool = False
    ) -> None:
        """Update file_id and file_unique_id in MongoDB when re-indexed or restored."""
        try:
            key = f"{video_id}_v" if is_video else f"{video_id}_a"
            update_data = {"file_id": file_id}
            if file_unique_id:
                update_data["file_unique_id"] = file_unique_id
            await self.music_cachedb.update_one({"_id": key}, {"$set": update_data})
        except Exception as e:
            logger.warning("update_music_file_id failed for %s: %s", video_id, e)

    # TELEGRAM STORAGE CHANNEL FILE_ID CACHE
    async def get_song_file_id(self, video_id: str, is_video: bool = False) -> str | None:
        """Get Telegram file_id for a cached song from MongoDB."""
        key = f"{video_id}_v" if is_video else f"{video_id}_a"
        doc = await self.song_cachedb.find_one({"_id": key})
        return doc.get("file_id") if doc else None

    async def save_song_file_id(self, video_id: str, file_id: str, is_video: bool = False) -> None:
        try:
            key = f"{video_id}_{'v' if is_video else 'a'}"
            await self.db.song_cache.update_one(
                {"_id": key},
                {"$set": {"video_id": video_id, "file_id": file_id, "is_video": is_video, "created_at": time()}},
                upsert=True
            )
        except Exception:
            pass

    async def save_shared_song(self, video_id: str, msg_id: int, is_video: bool = False, title: str = "") -> None:
        try:
            key = f"{video_id}_{'v' if is_video else 'a'}"
            await self.storage_db.shared_song_cache.update_one(
                {"_id": key},
                {
                    "$set": {
                        "video_id": video_id,
                        "msg_id": msg_id,
                        "channel_id": getattr(config, "STORAGE_GROUP_ID", -1003913556820),
                        "is_video": is_video,
                        "title": title,
                        "created_at": time(),
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logger.warning("save_shared_song failed for %s: %s", video_id, e)

    async def get_shared_song(self, video_id: str, is_video: bool = False) -> dict | None:
        try:
            key = f"{video_id}_{'v' if is_video else 'a'}"
            doc = await self.storage_db.shared_song_cache.find_one({"_id": key})
            if doc:
                return doc
        except Exception as e:
            logger.warning("get_shared_song failed for %s: %s", video_id, e)
        return None

    # BLACKLIST METHODS
    async def add_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.append(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"},
                {"$addToSet": {"chat_ids": chat_id}},
                upsert=True,
            )
        await self.cache.update_one(
            {"_id": "bl_users"},
            {"$addToSet": {"user_ids": chat_id}},
            upsert=True,
        )

    async def del_blacklist(self, chat_id: int) -> None:
        if str(chat_id).startswith("-"):
            self.blacklisted.remove(chat_id)
            return await self.cache.update_one(
                {"_id": "bl_chats"},
                {"$pull": {"chat_ids": chat_id}},
            )
        await self.cache.update_one(
            {"_id": "bl_users"},
            {"$pull": {"user_ids": chat_id}},
        )

    async def get_blacklisted(self, chat: bool = False) -> list[int]:
        if chat:
            if not self.blacklisted:
                doc = await self.cache.find_one({"_id": "bl_chats"})
                self.blacklisted.extend(doc.get("chat_ids", []) if doc else [])
            return self.blacklisted
        doc = await self.cache.find_one({"_id": "bl_users"})
        return doc.get("user_ids", []) if doc else []

    # CHAT METHODS
    async def is_chat(self, chat_id: int) -> bool:
        return chat_id in self.chats

    async def add_chat(self, chat_id: int, chat_title: str = None) -> None:
        if not await self.is_chat(chat_id):
            self.chats.append(chat_id)
            await self.chatsdb.insert_one({"_id": chat_id, "title": chat_title})

    async def rm_chat(self, chat_id: int) -> None:
        if await self.is_chat(chat_id):
            self.chats.remove(chat_id)
            await self.chatsdb.delete_one({"_id": chat_id})

    async def get_chats(self) -> list:
        if not self.chats:
            self.chats.extend([chat["_id"] async for chat in self.chatsdb.find()])
        return self.chats

    # COMMAND DELETE
    async def get_cmd_delete(self, chat_id: int) -> bool:
        if chat_id not in self.cmd_delete:
            doc = await self.chatsdb.find_one({"_id": chat_id})
            if doc and doc.get("cmd_delete"):
                self.cmd_delete.append(chat_id)
        return chat_id in self.cmd_delete

    async def set_cmd_delete(self, chat_id: int, delete: bool = False) -> None:
        if delete:
            self.cmd_delete.append(chat_id)
        else:
            self.cmd_delete.remove(chat_id)
        await self.chatsdb.update_one(
            {"_id": chat_id},
            {"$set": {"cmd_delete": delete}},
            upsert=True,
        )

    # LANGUAGE METHODS
    async def set_lang(self, chat_id: int, lang_code: str):
        await self.langdb.update_one(
            {"_id": chat_id},
            {"$set": {"lang": lang_code}},
            upsert=True,
        )
        self.lang[chat_id] = lang_code

    async def get_lang(self, chat_id: int) -> str:
        if chat_id not in self.lang:
            doc = await self.langdb.find_one({"_id": chat_id})
            self.lang[chat_id] = doc["lang"] if doc else config.LANG_CODE
        return self.lang[chat_id]

    # LOGGER METHODS
    async def is_logger(self) -> bool:
        return self.logger

    async def get_logger(self) -> bool:
        doc = await self.cache.find_one({"_id": "logger"})
        if doc:
            self.logger = doc["status"]
        return self.logger

    async def set_logger(self, status: bool) -> None:
        self.logger = status
        await self.cache.update_one(
            {"_id": "logger"},
            {"$set": {"status": status}},
            upsert=True,
        )

    # PLAY MODE METHODS
    async def get_play_mode(self, chat_id: int) -> bool:
        if chat_id not in self.admin_play:
            doc = await self.chatsdb.find_one({"_id": chat_id})
            if doc and doc.get("admin_play"):
                self.admin_play.append(chat_id)
        return chat_id in self.admin_play

    async def set_play_mode(self, chat_id: int, remove: bool = False) -> None:
        if remove and chat_id in self.admin_play:
            self.admin_play.remove(chat_id)
        else:
            self.admin_play.append(chat_id)
        await self.chatsdb.update_one(
            {"_id": chat_id},
            {"$set": {"admin_play": not remove}},
            upsert=True,
        )

    # AUTOPLAY METHODS
    async def get_autoplay(self, chat_id: int) -> bool:
        if chat_id not in self.autoplay:
            doc = await self.chatsdb.find_one({"_id": chat_id})
            if doc and doc.get("autoplay"):
                self.autoplay.append(chat_id)
        return chat_id in self.autoplay

    async def set_autoplay(self, chat_id: int, enable: bool = False) -> None:
        if enable:
            if chat_id not in self.autoplay:
                self.autoplay.append(chat_id)
        else:
            if chat_id in self.autoplay:
                self.autoplay.remove(chat_id)
        await self.chatsdb.update_one(
            {"_id": chat_id},
            {"$set": {"autoplay": enable}},
            upsert=True,
        )

    async def get_autoplay_mode(self, chat_id: int) -> str:
        if not hasattr(self, "autoplay_mode"):
            self.autoplay_mode = {}
        if chat_id not in self.autoplay_mode:
            doc = await self.chatsdb.find_one({"_id": chat_id})
            self.autoplay_mode[chat_id] = (doc or {}).get("autoplay_mode", "vibe")
        return self.autoplay_mode.get(chat_id, "vibe")

    async def set_autoplay_mode(self, chat_id: int, mode: str = "vibe") -> None:
        if not hasattr(self, "autoplay_mode"):
            self.autoplay_mode = {}
        if mode not in ["vibe", "artist", "trending"]:
            mode = "vibe"
        self.autoplay_mode[chat_id] = mode
        await self.chatsdb.update_one(
            {"_id": chat_id},
            {"$set": {"autoplay_mode": mode}},
            upsert=True,
        )


    # SUDO METHODS
    async def add_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$addToSet": {"user_ids": user_id}}, upsert=True
        )

    async def del_sudo(self, user_id: int) -> None:
        await self.cache.update_one(
            {"_id": "sudoers"}, {"$pull": {"user_ids": user_id}}
        )

    async def get_sudoers(self) -> list[int]:
        doc = await self.cache.find_one({"_id": "sudoers"})
        return doc.get("user_ids", []) if doc else []

    # USER METHODS
    async def is_user(self, user_id: int) -> bool:
        return user_id in self.users

    async def add_user(self, user_id: int) -> None:
        if not await self.is_user(user_id):
            self.users.append(user_id)
            await self.usersdb.insert_one({"_id": user_id})

    async def rm_user(self, user_id: int) -> None:
        if await self.is_user(user_id):
            self.users.remove(user_id)
            await self.usersdb.delete_one({"_id": user_id})

    async def get_users(self) -> list:
        if not self.users:
            self.users.extend([user["_id"] async for user in self.usersdb.find()])
        return self.users


    async def migrate_coll(self) -> None:
        logger.info("Migrating users and chats from old collections...")

        users, musers, mchats = [], [], []
        seen_chats, seen_users = set(), set()
        users.extend([user async for user in self.usersdb.find()])
        users.extend([user async for user in self.db.tgusersdb.find()])

        for user in users:
            _id = user.get("_id")
            if isinstance(_id, int):
                user_id = _id
            else:
                user_id = int(user.get("user_id"))

            if user_id in seen_users:
                continue
            seen_users.add(user_id)
            musers.append({"_id": user_id})

        await self.usersdb.drop()
        await self.db.tgusersdb.drop()
        if musers:
            await self.usersdb.insert_many(musers)

        async for chat in self.chatsdb.find():
            _id = chat.get("_id")
            if isinstance(_id, int):
                chat_id = _id
            else:
                chat_id = int(chat.get("chat_id"))

            if chat_id in seen_chats:
                continue
            seen_chats.add(chat_id)
            mchats.append({"_id": chat_id})

        await self.chatsdb.drop()
        if mchats:
            await self.chatsdb.insert_many(mchats)

        await self.cache.insert_one({"_id": "migrated"})
        logger.info("Migration completed successfully.")

    async def crawl_dialogs(self) -> int:
        """Backfill the chats collection with every group the bot is actually
        in. Bots cannot call GetDialogs (BOT_METHOD_INVALID), so we crawl via
        the userbot sessions (userbot.clients) — same mechanism auto_leave
        uses in misc.py. Persists every supergroup/group not already known.

        Returns the number of groups discovered. Safe to call repeatedly;
        chats already known are skipped.
        """
        from pyrogram import enums
        ChatType = enums.ChatType

        count = 0
        crawled_any = False
        for ub in getattr(userbot, "clients", []):
            try:
                async for dialog in ub.get_dialogs():
                    chat = dialog.chat
                    if not chat.id:
                        continue
                    if chat.type not in (ChatType.SUPERGROUP, ChatType.GROUP):
                        continue
                    count += 1
                    if not await self.is_chat(chat.id):
                        self.chats.append(chat.id)
                        await self.chatsdb.insert_one(
                            {"_id": chat.id, "title": getattr(chat, "title", None)}
                        )
                crawled_any = True
            except Exception as e:
                logger.warning("Dialog crawl failed for a userbot session: %s", e)
        if crawled_any:
            logger.info("Crawled dialogs: found %d groups.", count)
        else:
            logger.warning(
                "Dialog crawl found no userbot sessions (set SESSION1) — "
                "group count is limited to chats the bot has seen."
            )
        return count

    async def get_assistant_pm_config(self) -> dict:
        try:
            doc = await self.storage_db.assistant_pm_config.find_one({"_id": "default"})
            return doc or {}
        except Exception as e:
            logger.warning("get_assistant_pm_config failed: %s", e)
            return {}

    async def set_assistant_pm_text(self, text: str, entities: list | None = None) -> None:
        try:
            update = {"text": text, "updated_at": time()}
            if entities is not None:
                update["text_entities"] = entities
            await self.storage_db.assistant_pm_config.update_one(
                {"_id": "default"},
                {"$set": update},
                upsert=True,
            )
        except Exception as e:
            logger.warning("set_assistant_pm_text failed: %s", e)

    async def set_assistant_pm_buttons(self, buttons: list) -> None:
        try:
            await self.storage_db.assistant_pm_config.update_one(
                {"_id": "default"},
                {"$set": {"buttons": buttons, "updated_at": time()}},
                upsert=True,
            )
        except Exception as e:
            logger.warning("set_assistant_pm_buttons failed: %s", e)

    async def set_assistant_pm_media(self, media: dict | None) -> None:
        try:
            if media is None:
                await self.storage_db.assistant_pm_config.update_one(
                    {"_id": "default"},
                    {"$unset": {"media": ""}, "$set": {"updated_at": time()}},
                    upsert=True,
                )
            else:
                await self.storage_db.assistant_pm_config.update_one(
                    {"_id": "default"},
                    {"$set": {"media": media, "updated_at": time()}},
                    upsert=True,
                )
        except Exception as e:
            logger.warning("set_assistant_pm_media failed: %s", e)

    async def set_assistant_pm_delay(self, delay: float | None) -> None:
        try:
            if delay is None:
                await self.storage_db.assistant_pm_config.update_one(
                    {"_id": "default"},
                    {"$unset": {"delay": ""}, "$set": {"updated_at": time()}},
                    upsert=True,
                )
            else:
                await self.storage_db.assistant_pm_config.update_one(
                    {"_id": "default"},
                    {"$set": {"delay": float(delay), "updated_at": time()}},
                    upsert=True,
                )
        except Exception as e:
            logger.warning("set_assistant_pm_delay failed: %s", e)

    async def set_assistant_pm_disabled(self, disabled: bool) -> None:
        try:
            await self.storage_db.assistant_pm_config.update_one(
                {"_id": "default"},
                {"$set": {"disabled": bool(disabled), "updated_at": time()}},
                upsert=True,
            )
        except Exception as e:
            logger.warning("set_assistant_pm_disabled failed: %s", e)

    async def reset_assistant_pm_config(self) -> None:
        try:
            await self.storage_db.assistant_pm_config.delete_one({"_id": "default"})
        except Exception as e:
            logger.warning("reset_assistant_pm_config failed: %s", e)

    async def load_cache(self) -> None:
        doc = await self.cache.find_one({"_id": "migrated"})
        if not doc:
            await self.migrate_coll()

        await self.get_chats()
        await self.get_users()
        await self.get_blacklisted(True)
        await self.get_logger()
        logger.info("Database cache loaded.")
