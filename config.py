from os import getenv
from dotenv import load_dotenv

load_dotenv()

class Config:
    def __init__(self):
        self.API_ID = int(getenv("API_ID", 0))
        self.API_HASH = getenv("API_HASH")

        self.BOT_TOKEN = getenv("BOT_TOKEN")
        self.MONGO_URL = getenv("MONGO_URL")

        self.LOGGER_ID = int(getenv("LOGGER_ID", 0))
        self.OWNER_ID = int(getenv("OWNER_ID", 0))

        self.DURATION_LIMIT = int(getenv("DURATION_LIMIT", 120)) * 60
        self.QUEUE_LIMIT = int(getenv("QUEUE_LIMIT", 20))
        self.PLAYLIST_LIMIT = int(getenv("PLAYLIST_LIMIT", 20))

        self.SESSION1 = getenv("SESSION", None)
        self.SESSION2 = getenv("SESSION2", None)
        self.SESSION3 = getenv("SESSION3", None)

        self.SUPPORT_CHANNEL = getenv("SUPPORT_CHANNEL", "https://t.me/titanic_network")
        self.SUPPORT_CHAT = getenv("SUPPORT_CHAT", "https://t.me/+WAOT47P-70QwOTBl")

        self.YTPROXY_URL = getenv("YTPROXY_URL", "https://tgapi.xbitcode.com")  # xBit Music Endpoint
        self.YT_API_KEY = getenv("YT_API_KEY", "")  # Get from https://t.me/tgmusic_apibot

        # Self-hosted YouTube API — EC2 proxy (alive, needs X-API-Key = your Lily- key).
        # Falls back to Railway proxy if RAILWAY_YT_API_URL/KEY are also set.
        # NOTE: the old default Railway 824b key is dead (403); set the EC2 URL
        # here and provide the real key via RAILWAY_YT_API_KEY env var.
        self.RAILWAY_YT_API_URL = getenv("RAILWAY_YT_API_URL", "http://13.61.0.2:8000")
        self.RAILWAY_YT_API_KEY = getenv("RAILWAY_YT_API_KEY", "")

        # Shruti API — Primary download source (get key from @SHRUTIAPIBOT)
        self.SHRUTI_API_URL = getenv("SHRUTI_API_URL", "http://api01.shrutibots.site")
        self.SHRUTI_API_KEY = getenv("SHRUTI_API_KEY", "")
        
        self.AUTO_LEAVE: bool = getenv("AUTO_LEAVE", "False").lower() == "true"
        self.AUTO_END: bool = getenv("AUTO_END", "False").lower() == "true"
    
        self.THUMB_GEN: bool = getenv("THUMB_GEN", "True").lower() == "true"
        self.VIDEO_PLAY: bool = getenv("VIDEO_PLAY", "True").lower() == "true"

        # Auto cleanup — removes orphaned download/thumbnail files not
        # referenced by an active stream. A full disk is the #1 cause of the
        # bot slowing down (yt-dlp / ffmpeg temp writes start failing).
        self.AUTO_CLEANUP: bool = getenv("AUTO_CLEANUP", "True").lower() == "true"
        # Only delete cached files once free disk space drops below this %.
        self.CLEANUP_DISK_THRESHOLD: int = int(getenv("CLEANUP_DISK_THRESHOLD", 20))
        # How often (seconds) to run the cleanup scan. Default every 30 min.
        self.CLEANUP_INTERVAL: int = int(getenv("CLEANUP_INTERVAL", 1800))

        # ── Logging to the log group ──────────────────────────────────────────
        # Always-forward detailed play logs + playback/download errors to
        # LOGGER_ID (in addition to the file logger). Controlled by env so it
        # can be toggled without a code change.
        self.PLAY_LOG: bool = getenv("PLAY_LOG", "True").lower() == "true"
        self.ERROR_LOG: bool = getenv("ERROR_LOG", "True").lower() == "true"

        self.LANG_CODE = getenv("LANG_CODE", "en")

        self.COOKIES_URL = [
            url for url in getenv("COOKIES_URL", "").split(" ")
            if url and "batbin.me" in url
        ]
        self.COOKIES_DATA = getenv("COOKIES_DATA", "")
        self.DEFAULT_THUMB = getenv("DEFAULT_THUMB", "https://te.legra.ph/file/3e40a408286d4eda24191.jpg")
        self.PING_IMG = getenv("PING_IMG", "https://files.catbox.moe/haagg2.png")
        self.START_IMG = getenv("START_IMG", "https://files.catbox.moe/zvziwk.jpg")

    def check(self):
        missing = [
            var
            for var in ["API_ID", "API_HASH", "BOT_TOKEN", "MONGO_URL", "LOGGER_ID", "OWNER_ID", "SESSION1"]
            if not getattr(self, var)
        ]
        if missing:
            raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
