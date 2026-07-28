# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


from pyrogram import filters, types

from ishu import app, config, db


MAX_BUTTONS = 8


def _owner_only(m: types.Message) -> bool:
    return bool(m.from_user) and m.from_user.id == config.OWNER_ID


def _fmt_buttons_preview(raw_buttons) -> str:
    if not raw_buttons:
        return "(none — using default premium buttons)"
    lines = []
    for i, entry in enumerate(raw_buttons[:MAX_BUTTONS], 1):
        try:
            if isinstance(entry, list) and len(entry) >= 2:
                label, url = entry[0], entry[1]
            else:
                parts = str(entry).split("|", 1)
                if len(parts) == 2:
                    label, url = parts[0].strip(), parts[1].strip()
                else:
                    label, url = str(entry), "?"
            lines.append(f"  {i}. {label} → {url}")
        except Exception:
            continue
    return "\n".join(lines) if lines else "(none — using default premium buttons)"


@app.on_message(filters.command(["setassistantpm"]))
async def set_assistant_pm(_, m: types.Message):
    if not _owner_only(m):
        return
    text = None
    if m.reply_to_message and m.reply_to_message.text:
        text = m.reply_to_message.text.markdown if hasattr(m.reply_to_message.text, "markdown") else m.reply_to_message.text
    else:
        parts = m.text.split(None, 1)
        if len(parts) > 1:
            text = parts[1]
    if not text:
        return await m.reply_text(
            "❌ Usage:\n"
            "  /setassistantpm <message-text>\n"
            "  or reply with /setassistantpm to a message.\n\n"
            "Supported variables:\n"
            "  {mention}    - user mention (HTML)\n"
            "  {bot_link}   - music bot t.me link\n"
            "  {bot_name}   - music bot display name\n"
            "  {channel_link} - updates channel\n"
            "  {support_link} - support group (if set)\n"
        )
    await db.set_assistant_pm_text(text)
    preview = text[:800] + ("…" if len(text) > 800 else "")
    await m.reply_text(
        "✅ Assistant PM text saved.\n\n"
        f"<b>Preview:</b>\n{preview}\n\n"
        "Use /getassistantpm to view full config.",
        disable_web_page_preview=True,
    )


@app.on_message(filters.command(["setassistantbtn"]))
async def set_assistant_btn(_, m: types.Message):
    if not _owner_only(m):
        return
    raw = None
    if m.reply_to_message and m.reply_to_message.text:
        raw = m.reply_to_message.text
    else:
        parts = m.text.split(None, 1)
        if len(parts) > 1:
            raw = parts[1]
    if not raw:
        return await m.reply_text(
            "❌ Usage:\n"
            "  /setassistantbtn followed by up to 8 lines of:\n"
            "    Label Text|https://example.com\n"
            "  or reply /setassistantbtn to a message with the same format.\n\n"
            "One button per line. First 8 valid lines are kept.\n"
            "Send empty text or use /setassistantbtn clear to reset to default."
        )
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if lines and lines[0].lower() in ("clear", "reset", "none", "default"):
        parsed = []
    else:
        parsed = []
        for ln in lines:
            if "|" not in ln:
                continue
            label, url = ln.split("|", 1)
            label = label.strip()
            url = url.strip()
            if not label or not url:
                continue
            parsed.append([label, url])
            if len(parsed) >= MAX_BUTTONS:
                break
    await db.set_assistant_pm_buttons(parsed)
    await m.reply_text(
        f"✅ Assistant PM buttons saved ({len(parsed)} buttons).\n\n"
        f"<b>Configured buttons:</b>\n{_fmt_buttons_preview(parsed)}\n\n"
        "Use /getassistantpm to view full config.",
        disable_web_page_preview=True,
    )


@app.on_message(filters.command(["getassistantpm"]))
async def get_assistant_pm(_, m: types.Message):
    if not _owner_only(m):
        return
    cfg = await db.get_assistant_pm_config() or {}
    text = cfg.get("text") if isinstance(cfg, dict) else None
    buttons = cfg.get("buttons") if isinstance(cfg, dict) else None
    updated = cfg.get("updated_at") if isinstance(cfg, dict) else None

    lines = []
    lines.append("<b>Assistant PM Config</b>")
    if updated:
        from datetime import datetime
        try:
            ts = datetime.fromtimestamp(float(updated)).strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"<i>Last updated: {ts}</i>")
        except Exception:
            pass

    lines.append("")
    if text:
        preview = str(text)[:1200] + ("…" if len(str(text)) > 1200 else "")
        lines.append(f"<b>Custom text (set):</b>\n{preview}")
    else:
        lines.append("<b>Text:</b> <i>(using default premium welcome message)</i>")

    lines.append("")
    lines.append(f"<b>Buttons ({len(buttons) if buttons else 0} set, max {MAX_BUTTONS}):</b>")
    lines.append(_fmt_buttons_preview(buttons))

    lines.append("")
    lines.append("<b>Commands:</b>")
    lines.append("  /setassistantpm &lt;text&gt;    — set custom message")
    lines.append("  /setassistantbtn &lt;lines&gt;   — set label|url buttons")
    lines.append("  /setassistantbtn clear     — reset to default buttons")

    await m.reply_text("\n".join(lines), disable_web_page_preview=True)
