# Supabase 10-Node Deterministic Hashed CDN Uploader
import asyncio
import hashlib
import logging
import os
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
    {
        "url": "https://updmgiihcmfxldicitml.supabase.co",
        "key": "sb_publishable_ruWFuzR94d9nY2NyRWoOhg_VYC6jRHp",
    },
    {
        "url": "https://jnhiyuxrfavutqjgbgmo.supabase.co",
        "key": "sb_publishable_CjKu2ks-QsvFn7sYsNM1nA_BH85Z00O",
    },
    {
        "url": "https://osrkveuangnuiywqnzna.supabase.co",
        "key": "sb_publishable_hwmoMCZLT9We73JAG7GC1A_vTq1n77r",
    },
    {
        "url": "https://zepwnxgylolgkzbvhdfb.supabase.co",
        "key": "sb_publishable_eC0_RE035ulTARRBrEB9vg_Sa6VBKf7",
    },
    {
        "url": "https://lsvhlnuamgmolpjosydc.supabase.co",
        "key": "sb_publishable_YBpXeHP7uLWTherHTz978A_qc4u_KMZ",
    },
]

def get_node_for_video(video_id: str) -> dict:
    idx = int(hashlib.md5(video_id.encode("utf-8")).hexdigest(), 16) % len(SUPABASE_NODES)
    return SUPABASE_NODES[idx]

def _upload_sync(file_path: str, video_id: str, is_video: bool = False) -> Optional[str]:
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return None

    ext = "mp4" if is_video else "mp3"
    content_type = "video/mp4" if is_video else "audio/mpeg"
    object_path = f"{video_id}.{ext}"

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        logger.error("Failed to read %s for Supabase upload: %s", file_path, e)
        return None

    primary_idx = int(hashlib.md5(video_id.encode("utf-8")).hexdigest(), 16) % len(SUPABASE_NODES)
    ordered_nodes = SUPABASE_NODES[primary_idx:] + SUPABASE_NODES[:primary_idx]

    for node in ordered_nodes:
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
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    cdn_link = f"{url}/storage/v1/object/public/songs/{object_path}"
                    logger.info("Supabase CDN upload SUCCESS for %s → %s", video_id, cdn_link)
                    return cdn_link
        except Exception as e:
            logger.warning("Supabase upload attempt failed on node %s for %s: %s", url, video_id, e)

    return None

def _restore_sync(video_id: str, target_path: str, is_video: bool = False) -> bool:
    ext = "mp4" if is_video else "mp3"
    object_path = f"{video_id}.{ext}"
    primary_idx = int(hashlib.md5(video_id.encode("utf-8")).hexdigest(), 16) % len(SUPABASE_NODES)
    ordered_nodes = SUPABASE_NODES[primary_idx:] + SUPABASE_NODES[:primary_idx]

    for node in ordered_nodes:
        cdn_url = f"{node['url']}/storage/v1/object/public/songs/{object_path}"
        try:
            req = urllib.request.Request(cdn_url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if len(data) > 0:
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with open(target_path, "wb") as f:
                            f.write(data)
                        logger.info("Supabase CDN RESTORE HIT for %s → %s", video_id, cdn_url)
                        return True
        except Exception:
            pass

    return False

async def upload_to_supabase(file_path: str, video_id: str, is_video: bool = False) -> Optional[str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _upload_sync, file_path, video_id, is_video)

async def restore_from_supabase(video_id: str, target_path: str, is_video: bool = False) -> bool:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _restore_sync, video_id, target_path, is_video)

