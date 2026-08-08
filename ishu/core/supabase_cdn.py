# Supabase 5-Node Load-Balanced CDN Uploader
import asyncio
import logging
import os
import random
import urllib.request
from typing import Optional

logger = logging.getLogger("ishu")

SUPABASE_NODES = [
    {
        "url": "https://otteptfrjoaptzwksxzg.supabase.co",
        "key": "sb_publishable_FBbYKAsumzvFwlL9_m7lTQ_JW_ijylw",
    },
    {
        "url": "https://qfrhlqouantcpygymawz.supabase.co",
        "key": "sb_publishable_XapA8MYk6AOWEUSKzjWI1A_jSKzxd6J",
    },
    {
        "url": "https://pxsynuwfwbouxwfglidt.supabase.co",
        "key": "sb_publishable_LPIdD4NHWMUMB6oa_iLZNA_LdJ3Vw4p",
    },
    {
        "url": "https://hmloutacfdyjcmiyfydn.supabase.co",
        "key": "sb_publishable_IZpBLJMop6hXNwHTDdPZUA_eMHC1Mrr",
    },
    {
        "url": "https://osalzusukowsoicsxikq.supabase.co",
        "key": "sb_publishable_VCIZj0YfM512BR2tlqZzAw_kqnDHxZN",
    },
]

def _upload_sync(file_path: str, video_id: str, is_video: bool = False) -> Optional[str]:
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return None

    ext = "mp4" if is_video else "mp3"
    content_type = "video/mp4" if is_video else "audio/mpeg"
    object_path = f"{video_id}.{ext}"

    nodes = list(SUPABASE_NODES)
    random.shuffle(nodes)

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        logger.error("Failed to read %s for Supabase upload: %s", file_path, e)
        return None

    for node in nodes:
        url = node["url"]
        key = node["key"]
        upload_url = f"{url}/storage/v1/object/songs/{object_path}"
        req = urllib.request.Request(
            upload_url,
            data=file_bytes,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": content_type,
                "x-upsert": "true"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status in (200, 201):
                    cdn_link = f"{url}/storage/v1/object/public/songs/{object_path}"
                    logger.info("Supabase CDN upload SUCCESS for %s → %s", video_id, cdn_link)
                    return cdn_link
        except Exception as e:
            logger.warning("Supabase upload node %s failed for %s: %s", url, video_id, e)
            continue

    return None

async def upload_to_supabase(file_path: str, video_id: str, is_video: bool = False) -> Optional[str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _upload_sync, file_path, video_id, is_video)
