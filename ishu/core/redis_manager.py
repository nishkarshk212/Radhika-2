import os
import json
import logging
import asyncio
from typing import Optional
import redis.asyncio as redis

logger = logging.getLogger("ishu.redis")

class RedisManager:
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self.enabled = False

    async def connect(self):
        redis_url = os.getenv("REDIS_URL") or os.getenv("REDIS_TLS_URL")
        if not redis_url:
            logger.info("REDIS_URL not configured. Running without Redis RAM cache.")
            return

        try:
            kwargs = {"socket_timeout": 5.0}
            if redis_url.startswith("rediss://"):
                kwargs["ssl_cert_reqs"] = None

            self.client = redis.from_url(redis_url, **kwargs)
            await self.client.ping()
            self.enabled = True
            logger.info("Connected to Heroku Redis RAM Cache successfully! 🚀")
        except Exception as e:
            logger.warning("Redis connection failed: %s. Falling back to MongoDB/CDN.", e)
            self.enabled = False

    async def close(self):
        if self.client:
            await self.client.close()

    async def get_json(self, key: str) -> Optional[dict]:
        if not self.enabled or not self.client:
            return None
        try:
            val = await self.client.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            logger.warning("Redis GET failed for %s: %s", key, e)
        return None

    async def set_json(self, key: str, value: dict, ttl: int = 86400):
        if not self.enabled or not self.client:
            return
        try:
            await self.client.set(key, json.dumps(value), ex=ttl)
        except Exception as e:
            logger.warning("Redis SET failed for %s: %s", key, e)

    async def delete(self, key: str):
        if not self.enabled or not self.client:
            return
        try:
            await self.client.delete(key)
        except Exception as e:
            logger.warning("Redis DELETE failed for %s: %s", key, e)

redis_db = RedisManager()
