# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import shutil
from pathlib import Path

from ishu import logger


def ensure_dirs():
    """
    Ensure that the necessary directories exist.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("FFmpeg binary not found in system PATH.")

    for dir in ["cache", "downloads"]:
        Path(dir).mkdir(parents=True, exist_ok=True)

    # Ensure cookies dir exists for COOKIES_DATA base64 decoding
    Path("ishu/cookies").mkdir(parents=True, exist_ok=True)
    logger.info("Cache directories updated.")
