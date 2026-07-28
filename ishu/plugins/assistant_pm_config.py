# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


from datetime import datetime
from pyrogram import filters, types, enums

from ishu import app, config, db


MAX_BUTTONS = 48
MAX_ROWS = 12
CB_PREFIX = "asspm_"


def _owner_only(m: types.Message | types.CallbackQuery) -> bool:
    if isinstance(m, types.CallbackQuery):
        return bool(m.from_user) and m.from_user.id == config.OWNER_ID
    return bool(m.from_user) and m.from_user.id == config.OWNER_ID


def _owner_filter(_, __, m):
    return _owner_only(m)


owner_filter = filters.create(_owner_filter)


def _fmt_buttons_preview(raw_buttons) -> str:
    if not raw_buttons:
        return "(none — using default premium buttons)"
    lines = []
    count = 0
    for ri, entry in enumerate(raw_buttons):
        row_items = []
        if isinstance(entry, list):
            if entry and isinstance(entry[0], list):
                row_items = entry
            else:
                row_items = [entry]
        elif isinstance(entry, dict) and "row" in entry:
            row_items = entry.get("row") or []
        elif isinstance(entry, str):
            row_split = [p.strip() for p in entry.split(" || ") if p.strip()]
            for seg in row_split:
                if "|" not in seg:
                    continue
                lab, ur = seg.split("|", 1)
                row_items.append([lab.strip(), ur.strip()])
        if not row_items:
            continue
        cells = []
        for it in row_items:
            if isinstance(it, list) and len(it) >= 2:
                lab, ur = str(it[0]).strip(), str(it[1]).strip()
            elif isinstance(it, dict):
                lab = str(it.get("text") or it.get("label") or "").strip()
                ur = str(it.get("url") or "").strip()
            else:
                continue
            if lab and ur:
                count += 1
                cells.append(f"{lab}→{ur}")
        if cells:
            lines.append(f"  Row {ri+1}: {'  ||  '.join(cells)}")
        if count >= MAX_BUTTONS:
            break
    return "\n".join(lines) if lines else "(none — using default premium buttons)"


def _ents_to_dicts(entities) -> list | None:
    if not entities:
        return None
    out = []
    for ent in entities:
        try:
            d = {
                "type": str(getattr(ent, "type", "") or "").lower() or None,
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
            if d.get("type"):
                out.append(d)
        except Exception:
            continue
    return out or None


def _panel_markup(cfg: dict) -> types.InlineKeyboardMarkup:
    rows = []
    rows.append([
        types.InlineKeyboardButton(text="📝 Set Message Text", callback_data=f"{CB_PREFIX}settext"),
        types.InlineKeyboardButton(text="🖼️ Set Media", callback_data=f"{CB_PREFIX}setmedia"),
    ])
    rows.append([
        types.InlineKeyboardButton(text="🔘 Set Inline Buttons", callback_data=f"{CB_PREFIX}setbtns"),
        types.InlineKeyboardButton(text="⏱️ Set Delay", callback_data=f"{CB_PREFIX}setdelay"),
    ])
    disabled = bool(cfg.get("disabled"))
    toggle_text = "✅ Enabled – tap to Disable" if not disabled else "⛔ Disabled – tap to Enable"
    rows.append([types.InlineKeyboardButton(text=toggle_text, callback_data=f"{CB_PREFIX}toggle")])
    rows.append([
        types.InlineKeyboardButton(text="🗑️ Clear Media", callback_data=f"{CB_PREFIX}clmedia"),
        types.InlineKeyboardButton(text="↩️ Reset Buttons", callback_data=f"{CB_PREFIX}rstbtns"),
    ])
    rows.append([
        types.InlineKeyboardButton(text="🔄 Full Reset (Defaults)", callback_data=f"{CB_PREFIX}reset"),
        types.InlineKeyboardButton(text="🔍 View Config", callback_data=f"{CB_PREFIX}view"),
    ])
    rows.append([types.InlineKeyboardButton(text="❌ Close Panel", callback_data=f"{CB_PREFIX}close")])
    return types.InlineKeyboardMarkup(rows)


def _media_info_text(cfg: dict) -> str:
    m = cfg.get("media") if isinstance(cfg, dict) else None
    if not m or not isinstance(m, dict):
        return "🖼️ <b>Media:</b> <i>(not set)</i>"
    mtype = str(m.get("type") or "unknown").upper()
    lines = [f"🖼️ <b>Media:</b> {mtype}"]
    for k in ("file_name", "file_size", "width", "height", "duration", "title", "performer"):
        v = m.get(k)
        if v not in (None, ""):
            lines.append(f"    • {k}: <code>{v}</code>")
    return "\n".join(lines)


def _status_text(cfg: dict) -> str:
    disabled = bool(cfg.get("disabled"))
    text = cfg.get("text") if isinstance(cfg, dict) else None
    buttons = cfg.get("buttons") if isinstance(cfg, dict) else None
    delay = cfg.get("delay") if isinstance(cfg, dict) else None
    updated = cfg.get("updated_at") if isinstance(cfg, dict) else None

    lines = []
    lines.append("⚙️ <b>Assistant PM Auto-Reply Panel</b>")
    lines.append("")
    lines.append(f"🔔 Status: {'<b>ENABLED</b>' if not disabled else '<b>DISABLED</b>'}")
    if delay is not None:
        lines.append(f"⏱️ Delay: <code>{float(delay):.2f}s</code>  <i>(config override)</i>")
    else:
        lines.append("⏱️ Delay: <i>default</i>")
    if updated:
        try:
            ts = datetime.fromtimestamp(float(updated)).strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"🕒 Last updated: <code>{ts}</code>")
        except Exception:
            pass
    lines.append("")
    if text:
        preview = str(text)[:400] + ("…" if len(str(text)) > 400 else "")
        lines.append(f"📝 <b>Text (custom)</b> — preview:\n{preview}")
    else:
        lines.append("📝 <b>Text:</b> <i>(using default premium welcome message)</i>")
    lines.append("")
    n_btns = 0
    if buttons:
        try:
            for b in buttons:
                if isinstance(b, list) and b:
                    if isinstance(b[0], list):
                        n_btns += sum(1 for x in b)
                    else:
                        n_btns += 1
                elif isinstance(b, str):
                    n_btns += len([p for p in b.split(" || ") if "|" in p])
                elif isinstance(b, dict):
                    r = b.get("row") or []
                    n_btns += len(r)
        except Exception:
            pass
    lines.append(f"🔘 <b>Buttons:</b> {n_btns} total (max {MAX_BUTTONS}, rows {MAX_ROWS})")
    lines.append("")
    lines.append(_media_info_text(cfg if isinstance(cfg, dict) else {}))
    lines.append("")
    lines.append("<i>Use the buttons below to configure the assistant autoreply.</i>")
    return "\n".join(lines)


@app.on_message(filters.command(["assistantpanel", "apmpanel", "setassistantpanel"]) & owner_filter)
async def assistant_panel_cmd(_, m: types.Message):
    cfg = await db.get_assistant_pm_config() or {}
    await m.reply_text(
        _status_text(cfg),
        reply_markup=_panel_markup(cfg),
        disable_web_page_preview=True,
    )


@app.on_callback_query(filters.regex(f"^{CB_PREFIX}"))
async def assistant_panel_cb(_, cb: types.CallbackQuery):
    if not _owner_only(cb):
        await cb.answer("⚠️ Owner only.", show_alert=True)
        return
    action = cb.data[len(CB_PREFIX):]
    try:
        cfg = await db.get_assistant_pm_config() or {}
    except Exception:
        cfg = {}

    if action == "close":
        await cb.message.delete()
        await cb.answer()
        return

    if action == "toggle":
        disabled = not bool(cfg.get("disabled"))
        await db.set_assistant_pm_disabled(disabled)
        await cb.answer(f"✅ Autoreply {'ENABLED' if not disabled else 'DISABLED'}.", show_alert=True)
        cfg = await db.get_assistant_pm_config() or {}
        await cb.message.edit_text(_status_text(cfg), reply_markup=_panel_markup(cfg), disable_web_page_preview=True)
        return

    if action == "reset":
        await db.reset_assistant_pm_config()
        await cb.answer("🔄 Config reset to defaults.", show_alert=True)
        cfg = await db.get_assistant_pm_config() or {}
        await cb.message.edit_text(_status_text(cfg), reply_markup=_panel_markup(cfg), disable_web_page_preview=True)
        return

    if action == "clmedia":
        await db.set_assistant_pm_media(None)
        await cb.answer("🗑️ Media cleared.", show_alert=True)
        cfg = await db.get_assistant_pm_config() or {}
        await cb.message.edit_text(_status_text(cfg), reply_markup=_panel_markup(cfg), disable_web_page_preview=True)
        return

    if action == "rstbtns":
        await db.set_assistant_pm_buttons([])
        await cb.answer("↩️ Buttons reset to default.", show_alert=True)
        cfg = await db.get_assistant_pm_config() or {}
        await cb.message.edit_text(_status_text(cfg), reply_markup=_panel_markup(cfg), disable_web_page_preview=True)
        return

    if action == "view":
        text = cfg.get("text") if isinstance(cfg, dict) else None
        buttons = cfg.get("buttons") if isinstance(cfg, dict) else None
        lines = []
        lines.append("🔍 <b>Full Assistant PM Config</b>")
        lines.append("")
        if text:
            lines.append(f"📝 <b>Text:</b>\n{text}")
        else:
            lines.append("📝 <b>Text:</b> <i>(default)</i>")
        lines.append("")
        lines.append(f"🔘 <b>Buttons (max {MAX_BUTTONS}):</b>")
        lines.append(_fmt_buttons_preview(buttons))
        lines.append("")
        lines.append(_media_info_text(cfg if isinstance(cfg, dict) else {}))
        lines.append("")
        lines.append("<b>Quick Commands:</b>")
        lines.append("  /setassistantpm  &lt;text&gt;   — set message (supports premium emoji via reply)")
        lines.append("  /setassistantmedia         — reply to a photo/video/gif/audio/doc/sticker")
        lines.append("  /setassistantbtn   &lt;lines&gt; — set inline buttons (label|url per line, ' || ' for same row)")
        lines.append("  /setassistantdelay &lt;sec&gt;   — set typing delay (e.g. 1.5)")
        lines.append("  /clearassistantmedia       — remove media")
        lines.append("  /resetassistantpm          — full reset")
        lines.append("  /getassistantpm            — view config")
        await cb.message.edit_text("\n".join(lines), reply_markup=_panel_markup(cfg), disable_web_page_preview=True)
        await cb.answer()
        return

    if action == "settext":
        msg = (
            "📝 <b>Set Assistant PM Text</b>\n\n"
            "Please send me the new autoreply message now — or reply to this message with the text.\n\n"
            "<b>Supports:</b>\n"
            "  • Markdown/HTML formatting\n"
            "  • Premium custom emoji (send sticker pack emojis inline — entities are preserved)\n"
            "  • Template variables: <code>{mention}</code> <code>{bot_link}</code> <code>{bot_name}</code> <code>{channel_link}</code> <code>{support_link}</code>\n\n"
            "Or use: <code>/setassistantpm &lt;text&gt;</code>"
        )
        await cb.message.edit_text(msg, reply_markup=types.InlineKeyboardMarkup([[
            types.InlineKeyboardButton("⬅️ Back to Panel", callback_data=f"{CB_PREFIX}back"),
        ]]), disable_web_page_preview=True)
        await cb.answer()
        return

    if action == "setmedia":
        msg = (
            "🖼️ <b>Set Assistant PM Media</b>\n\n"
            "Send or forward <b>any single media</b> now and I'll attach it to the autoreply.\n\n"
            "<b>Supported media types:</b>\n"
            "  • Photo / Image\n"
            "  • Video (mp4)\n"
            "  • Animation / GIF\n"
            "  • Audio / Voice / Music file\n"
            "  • Document / File\n"
            "  • Sticker (sends sticker + caption below)\n\n"
            "Or reply to a media message with: <code>/setassistantmedia</code>\n"
            "Remove media with: <code>/clearassistantmedia</code>"
        )
        await cb.message.edit_text(msg, reply_markup=types.InlineKeyboardMarkup([[
            types.InlineKeyboardButton("⬅️ Back to Panel", callback_data=f"{CB_PREFIX}back"),
        ]]), disable_web_page_preview=True)
        await cb.answer()
        return

    if action == "setbtns":
        msg = (
            "🔘 <b>Set Inline Buttons</b>\n\n"
            "Send a message with up to <b>12 rows</b>, one per line.\n\n"
            "<b>Format:</b>\n"
            "  One button per line:\n"
            "    <code>Button Label|https://example.com</code>\n\n"
            "  Multiple buttons on same row — separate with <code> || </code>:\n"
            "    <code>Left|https://a.com || Middle|https://b.com || Right|https://c.com</code>\n\n"
            "Reset to default premium buttons:\n"
            "  <code>/setassistantbtn clear</code>\n\n"
            "Or use the command: <code>/setassistantbtn</code> with the same format."
        )
        await cb.message.edit_text(msg, reply_markup=types.InlineKeyboardMarkup([[
            types.InlineKeyboardButton("⬅️ Back to Panel", callback_data=f"{CB_PREFIX}back"),
        ]]), disable_web_page_preview=True)
        await cb.answer()
        return

    if action == "setdelay":
        msg = (
            "⏱️ <b>Set Typing Delay</b>\n\n"
            "Send a number (seconds) e.g. <code>1.2</code> or <code>0</code> (no delay).\n\n"
            "Default (environment) is used when not set here.\n\n"
            "Or use: <code>/setassistantdelay 1.5</code>"
        )
        await cb.message.edit_text(msg, reply_markup=types.InlineKeyboardMarkup([[
            types.InlineKeyboardButton("⬅️ Back to Panel", callback_data=f"{CB_PREFIX}back"),
        ]]), disable_web_page_preview=True)
        await cb.answer()
        return

    if action == "back":
        await cb.message.edit_text(_status_text(cfg), reply_markup=_panel_markup(cfg), disable_web_page_preview=True)
        await cb.answer()
        return

    await cb.answer()


def _extract_text_and_entities(m: types.Message) -> tuple[str | None, list | None]:
    candidates = []
    if getattr(m, "text", None):
        candidates.append((m.text, getattr(m, "entities", None)))
    if getattr(m, "caption", None):
        candidates.append((m.caption, getattr(m, "caption_entities", None)))
    if m.reply_to_message:
        r = m.reply_to_message
        if getattr(r, "text", None):
            candidates.append((r.text, getattr(r, "entities", None)))
        if getattr(r, "caption", None):
            candidates.append((r.caption, getattr(r, "caption_entities", None)))
    cmd_parts = m.text.split(None, 1) if getattr(m, "text", None) else []
    if len(cmd_parts) > 1:
        candidates.append((cmd_parts[1], None))
    if not candidates:
        return None, None
    text, ents = candidates[0]
    if not text:
        return None, None
    return str(text), _ents_to_dicts(ents)


def _parse_buttons_from_text(raw: str) -> list:
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        return []
    if lines and lines[0].lower() in ("clear", "reset", "none", "default"):
        return []
    parsed = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if " || " in ln:
            parsed.append(ln)
            continue
        if "|" not in ln:
            continue
        label, url = ln.split("|", 1)
        label = label.strip()
        url = url.strip()
        if not label or not url:
            continue
        parsed.append([label, url])
        if len(parsed) >= MAX_ROWS:
            break
    return parsed


@app.on_message(filters.command(["setassistantpm", "setassistanttext"]) & owner_filter)
async def set_assistant_pm(_, m: types.Message):
    text, ents = _extract_text_and_entities(m)
    if not text:
        return await m.reply_text(
            "❌ Usage:\n"
            "  <code>/setassistantpm &lt;message-text&gt;</code>\n"
            "  or <b>reply</b> with <code>/setassistantpm</code> to a message (preserves premium emoji entities).\n\n"
            "Supported variables:\n"
            "  <code>{mention}</code>    - user mention\n"
            "  <code>{bot_link}</code>   - music bot t.me link\n"
            "  <code>{bot_name}</code>   - music bot display name\n"
            "  <code>{channel_link}</code> - updates channel\n"
            "  <code>{support_link}</code> - support group (if set)\n\n"
            "Premium custom emoji are preserved when replying to a message that contains them."
        )
    await db.set_assistant_pm_text(text, ents)
    preview = text[:800] + ("…" if len(text) > 800 else "")
    out = [f"✅ Assistant PM text saved (entities: {len(ents) if ents else 0})."]
    out.append("")
    out.append(f"<b>Preview:</b>\n{preview}")
    out.append("")
    out.append("Use <code>/getassistantpm</code> or <code>/assistantpanel</code> to view full config.")
    await m.reply_text("\n".join(out), disable_web_page_preview=True)


@app.on_message(filters.command(["setassistantmedia"]) & owner_filter)
async def set_assistant_media(_, m: types.Message):
    target = m.reply_to_message or m
    media_dict = None

    for attr, mtype in (
        ("photo", "photo"),
        ("video", "video"),
        ("animation", "animation"),
        ("audio", "audio"),
        ("voice", "voice"),
        ("document", "document"),
        ("sticker", "sticker"),
    ):
        obj = getattr(target, attr, None)
        if not obj:
            continue
        if isinstance(obj, list):
            obj = obj[-1] if obj else None
        if not obj:
            continue
        media_dict = {"type": mtype}
        if getattr(obj, "file_id", None):
            media_dict["file_id"] = obj.file_id
        if getattr(obj, "file_unique_id", None):
            media_dict["file_unique_id"] = obj.file_unique_id
        if getattr(obj, "file_ref", None):
            media_dict["file_ref"] = obj.file_ref
        if getattr(obj, "file_name", None):
            media_dict["file_name"] = obj.file_name
        if getattr(obj, "file_size", None):
            media_dict["file_size"] = int(obj.file_size)
        if getattr(obj, "width", None):
            media_dict["width"] = int(obj.width)
        if getattr(obj, "height", None):
            media_dict["height"] = int(obj.height)
        if getattr(obj, "duration", None):
            media_dict["duration"] = int(obj.duration)
        if getattr(obj, "mime_type", None):
            media_dict["mime_type"] = obj.mime_type
        if getattr(obj, "title", None):
            media_dict["title"] = obj.title
        if getattr(obj, "performer", None):
            media_dict["performer"] = obj.performer
        if getattr(obj, "supports_streaming", None):
            media_dict["supports_streaming"] = True
        if getattr(obj, "emoji", None):
            media_dict["emoji"] = obj.emoji
        if getattr(obj, "set_name", None):
            media_dict["set_name"] = obj.set_name
        break

    if not media_dict:
        return await m.reply_text(
            "❌ Usage:\n"
            "  <b>Reply</b> with <code>/setassistantmedia</code> to any:\n"
            "    • Photo / Image\n"
            "    • Video / Animation / GIF\n"
            "    • Audio / Voice\n"
            "    • Document / File\n"
            "    • Sticker\n\n"
            "  Or attach the media <b>directly</b> with the command as caption."
        )
    await db.set_assistant_pm_media(media_dict)
    lines = [f"✅ Assistant PM media saved: <b>{media_dict['type'].upper()}</b>"]
    for k in ("file_name", "file_size", "width", "height", "duration", "mime_type", "emoji", "set_name"):
        if media_dict.get(k) not in (None, ""):
            lines.append(f"  • {k}: <code>{media_dict[k]}</code>")
    lines.append("")
    lines.append("The autoreply will send this media along with your text/caption and inline buttons.")
    lines.append("Use <code>/clearassistantmedia</code> to remove.")
    await m.reply_text("\n".join(lines), disable_web_page_preview=True)


@app.on_message(filters.command(["clearassistantmedia"]) & owner_filter)
async def clear_assistant_media(_, m: types.Message):
    await db.set_assistant_pm_media(None)
    await m.reply_text("🗑️ Assistant PM media cleared. Autoreply will use plain text mode.")


@app.on_message(filters.command(["setassistantbtn", "setassistantbuttons"]) & owner_filter)
async def set_assistant_btn(_, m: types.Message):
    raw = None
    if m.reply_to_message:
        for field in ("text", "caption"):
            if getattr(m.reply_to_message, field, None):
                raw = getattr(m.reply_to_message, field)
                break
    if not raw:
        parts = m.text.split(None, 1)
        if len(parts) > 1:
            raw = parts[1]
    if not raw:
        return await m.reply_text(
            "❌ Usage:\n"
            "  <code>/setassistantbtn</code> followed by up to <b>12 rows</b> of:\n"
            "    One button per line:  <code>Label Text|https://example.com</code>\n"
            "    Multiple per row:     <code>A|urlA || B|urlB || C|urlC</code>\n\n"
            "  or reply to a message with the same format.\n\n"
            "Clear / reset to default premium buttons:\n"
            "  <code>/setassistantbtn clear</code>\n"
            "  <code>/setassistantbtn reset</code>\n\n"
            f"Maximum {MAX_BUTTONS} buttons total across {MAX_ROWS} rows."
        )
    parsed = _parse_buttons_from_text(raw)
    await db.set_assistant_pm_buttons(parsed)
    await m.reply_text(
        f"✅ Assistant PM buttons saved.\n\n"
        f"<b>Configured rows:</b>\n{_fmt_buttons_preview(parsed)}\n\n"
        "Use <code>/getassistantpm</code> to view full config or <code>/assistantpanel</code> for the GUI.",
        disable_web_page_preview=True,
    )


@app.on_message(filters.command(["setassistantdelay"]) & owner_filter)
async def set_assistant_delay(_, m: types.Message):
    parts = m.text.split(None, 1)
    if len(parts) < 2:
        return await m.reply_text(
            "❌ Usage:\n"
            "  <code>/setassistantdelay &lt;seconds&gt;</code>\n\n"
            "Examples:\n"
            "  <code>/setassistantdelay 1.5</code>  — 1.5 seconds typing delay\n"
            "  <code>/setassistantdelay 0</code>    — no delay (instant reply)\n"
            "  <code>/setassistantdelay default</code> — unset, use env/default (1.2s)\n"
        )
    val = parts[1].strip()
    if val.lower() in ("default", "none", "null", "unset", "reset"):
        await db.set_assistant_pm_delay(None)
        return await m.reply_text("⏱️ Assistant PM delay reset to <b>default</b> (from env or 1.2s).")
    try:
        delay = float(val)
    except (TypeError, ValueError):
        return await m.reply_text("❌ Invalid number. Use e.g. <code>1.2</code> or <code>0</code> or <code>default</code>.")
    if delay < 0:
        delay = 0.0
    if delay > 10:
        delay = 10.0
    await db.set_assistant_pm_delay(delay)
    await m.reply_text(f"⏱️ Assistant PM delay set to <b>{delay:.2f}s</b>.")


@app.on_message(filters.command(["resetassistantpm"]) & owner_filter)
async def reset_assistant_pm(_, m: types.Message):
    await db.reset_assistant_pm_config()
    await m.reply_text("🔄 Assistant PM config fully reset to <b>defaults</b> (text, buttons, media, delay cleared).")


@app.on_message(filters.command(["toggleassistantpm"]) & owner_filter)
async def toggle_assistant_pm(_, m: types.Message):
    cfg = await db.get_assistant_pm_config() or {}
    new_state = not bool(cfg.get("disabled"))
    await db.set_assistant_pm_disabled(not new_state)
    status = "ENABLED ✅" if new_state else "DISABLED ⛔"
    await m.reply_text(f"🔔 Assistant PM autoreply is now <b>{status}</b>.")


@app.on_message(filters.command(["getassistantpm"]) & owner_filter)
async def get_assistant_pm(_, m: types.Message):
    cfg = await db.get_assistant_pm_config() or {}
    text = cfg.get("text") if isinstance(cfg, dict) else None
    buttons = cfg.get("buttons") if isinstance(cfg, dict) else None
    updated = cfg.get("updated_at") if isinstance(cfg, dict) else None
    disabled = bool(cfg.get("disabled")) if isinstance(cfg, dict) else False
    delay = cfg.get("delay") if isinstance(cfg, dict) else None

    lines = []
    lines.append("<b>Assistant PM Config</b>")
    lines.append(f"🔔 Status: <b>{'ENABLED' if not disabled else 'DISABLED'}</b>")
    if delay is not None:
        lines.append(f"⏱️ Delay: <code>{float(delay):.2f}s</code> (set via DB)")
    else:
        lines.append("⏱️ Delay: default (env fallback)")
    if updated:
        try:
            ts = datetime.fromtimestamp(float(updated)).strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"<i>Last updated: {ts}</i>")
        except Exception:
            pass

    lines.append("")
    if text:
        preview = str(text)[:1800] + ("…" if len(str(text)) > 1800 else "")
        lines.append(f"<b>Custom text (set):</b>\n{preview}")
        ents = cfg.get("text_entities") if isinstance(cfg, dict) else None
        if ents:
            ce = sum(1 for e in ents if isinstance(e, dict) and e.get("custom_emoji_id"))
            if ce:
                lines.append(f"✨ Premium custom emoji entities: <b>{ce}</b>")
    else:
        lines.append("<b>Text:</b> <i>(using default premium welcome message)</i>")

    lines.append("")
    lines.append(f"<b>Buttons ({MAX_BUTTONS} max, {MAX_ROWS} rows):</b>")
    lines.append(_fmt_buttons_preview(buttons))

    lines.append("")
    lines.append(_media_info_text(cfg if isinstance(cfg, dict) else {}))

    lines.append("")
    lines.append("<b>Commands / GUI:</b>")
    lines.append("  <code>/assistantpanel</code>        — interactive GUI panel")
    lines.append("  <code>/setassistantpm</code> &lt;text&gt;    — set custom message (preserves emoji when replying)")
    lines.append("  <code>/setassistantmedia</code>       — reply to media to attach it")
    lines.append("  <code>/clearassistantmedia</code>     — remove media")
    lines.append("  <code>/setassistantbtn</code> &lt;lines&gt;  — set buttons (label|url lines, ' || ' same row)")
    lines.append("  <code>/setassistantdelay</code> &lt;s&gt;    — set typing delay (e.g. 1.2)")
    lines.append("  <code>/toggleassistantpm</code>       — enable/disable autoreply")
    lines.append("  <code>/resetassistantpm</code>        — full reset")

    await m.reply_text("\n".join(lines), disable_web_page_preview=True)
