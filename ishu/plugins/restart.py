# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import os
import shutil
import asyncio

from pyrogram import filters, types

from ishu import app, db, lang, stop, config


@app.on_message(filters.command(["logs"]) & app.sudoers)
@lang.language()
async def _logs(_, m: types.Message):
    sent = await m.reply_text(m.lang["log_fetch"])
    if not os.path.exists("log.txt"):
        return await sent.edit_text(m.lang["log_not_found"])
    await sent.edit_media(
        media=types.InputMediaDocument(
            media="log.txt",
            caption=m.lang["log_sent"].format(app.name),
        )
    )


@app.on_message(filters.command(["logger"]) & app.sudoers)
@lang.language()
async def _logger(_, m: types.Message):
    if len(m.command) < 2:
        return await m.reply_text(m.lang["logger_usage"].format(m.command[0]))
    if m.command[1] not in ("on", "off"):
        return await m.reply_text(m.lang["logger_usage"].format(m.command[0]))

    if m.command[1] == "on":
        await db.set_logger(True)
        await m.reply_text(m.lang["logger_on"])
    else:
        await db.set_logger(False)
        await m.reply_text(m.lang["logger_off"])


async def restart_bot():
    asyncio.create_task(stop())
    await asyncio.sleep(2)
    os._exit(1)


@app.on_message(filters.command(["restart"]) & app.sudoers)
@lang.language()
async def _restart(_, m: types.Message):
    sent = await m.reply_text(m.lang["restarting"])

    for directory in ["cache", "downloads"]:
        shutil.rmtree(directory, ignore_errors=True)

    await sent.edit_text(m.lang["restarted"])
    try: os.remove("log.txt")
    except Exception: pass

    await restart_bot()


@app.on_message(filters.command(["update"]) & filters.user(config.OWNER_ID))
async def _update(_, m: types.Message):
    sent = await m.reply_text("Checking for updates and pulling from git...")
    
    process = await asyncio.create_subprocess_shell(
        "git pull",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    output = stdout.decode().strip()
    error = stderr.decode().strip()
    
    if "Already up to date." in output:
        return await sent.edit_text("Bot is already up to date!")
        
    if process.returncode != 0:
        return await sent.edit_text(f"**Git Pull Failed:**\n`{error or output}`")
        
    await sent.edit_text(f"**Updated successfully!**\n\n`{output}`\n\nRestarting the bot...")
    
    for directory in ["cache", "downloads"]:
        shutil.rmtree(directory, ignore_errors=True)
        
    try: os.remove("log.txt")
    except Exception: pass
    
    await restart_bot()
