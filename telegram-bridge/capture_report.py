#!/usr/bin/env python3
"""Capture Bellabyte daily-yesterday or monthly report as JSON; never writes to Supabase."""
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT = "MonitorMarcopaoBot"
BRANCH = "MARCOPAO LANCHONETE E PANIFICACAO LTDA"
OUT_DIR = Path("artifacts")

def norm(text):
    return " ".join((text or "").split())

def buttons(message):
    markup = getattr(message, "reply_markup", None)
    return [str(button.text).strip()
            for row in (getattr(markup, "rows", []) or [])
            for button in (getattr(row, "buttons", []) or [])
            if getattr(button, "text", None)]

async def latest_incoming(client, entity):
    items = await client.get_messages(entity, limit=100)
    incoming = [message for message in items if not message.out]
    return incoming[0] if incoming else None

async def wait_replies(client, entity, after_id, timeout=30):
    found = {}
    for _ in range(timeout):
        await asyncio.sleep(1)
        for message in await client.get_messages(entity, limit=100):
            if message.id > after_id and not message.out:
                found[message.id] = message
        if found:
            await asyncio.sleep(2)
            for message in await client.get_messages(entity, limit=100):
                if message.id > after_id and not message.out:
                    found[message.id] = message
            return sorted(found.values(), key=lambda message: message.id)
    return []

async def send_choice(client, entity, current, choice, reason):
    print(f"ACTION: sending {choice!r} ({reason})")
    await client.send_message(entity, choice)
    replies = await wait_replies(client, entity, current.id)
    if not replies:
        print("NO_BOT_REPLY: no response within 30 seconds.")
        return None, []
    for message in replies:
        print(f"BOT_REPLY id={message.id} text={norm(message.message)[:900]!r}")
        labels = buttons(message)
        if labels:
            print("BUTTONS: " + " | ".join(labels[:25]))
    return replies[-1], replies

def save_report(message, report_type, requested_period):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = message.message or ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    payload = {
        "source": "telegram_monitorbot",
        "bot": BOT,
        "branch": BRANCH,
        "report_type": report_type,
        "requested_period": requested_period,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "message_id": message.id,
        "message_date": message.date.astimezone(timezone.utc).isoformat() if message.date else None,
        "buttons": buttons(message),
        "content_sha256": digest,
        "raw_text": raw,
        "status": "review_required",
        "database_written": False,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{report_type}_{requested_period}_{stamp}.json"
    output = OUT_DIR / filename
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT_CAPTURED={output}")
    print(f"CONTENT_SHA256={digest}")
    print(f"REPORT_MESSAGE_ID={message.id}")
    print(f"REPORT_TEXT_BEGIN\n{raw[:6000]}\nREPORT_TEXT_END")
    return output

def is_main_menu(text):
    low = norm(text).lower()
    return "venda do dia" in low and "venda acumulada mês" in low and "top 100" in low

def matches_report(text, mode):
    low = norm(text).lower()
    if mode == "daily_yesterday":
        return (("venda do dia" in low or "venda de hoje" in low or "venda de ontem" in low)
                and ("total" in low or "cupons" in low)
                and "venda acumulada do mês" not in low)
    return "venda acumulada do mês" in low and "total" in low

async def search_recent_report(client, entity, mode, after_id=None):
    items = await client.get_messages(entity, limit=100)
    candidates = [m for m in items if not m.out and matches_report(m.message, mode)]
    if after_id is not None:
        new_candidates = [m for m in candidates if m.id > after_id]
        if new_candidates:
            candidates = new_candidates
    return max(candidates, key=lambda m: m.id) if candidates else None

async def recover_menu(client, entity, current):
    """Use the bot-documented 0 reset if conversation is not at a known menu."""
    text = norm(current.message)
    low = text.lower()
    if is_main_menu(text):
        return current
    if "qual filial deseja se conectar" in low or "digite qual filial" in low:
        pass
    else:
        reset, replies = await send_choice(client, entity, current, "0", "reset stale conversation to branch selection")
        if not replies:
            raise RuntimeError("No response after the documented reset option 0.")
        current = replies[-1]
        text = norm(current.message)
        low = text.lower()
        if not is_main_menu(text) and "qual filial deseja se conectar" not in low and "digite qual filial" not in low:
            latest = await latest_incoming(client, entity)
            if latest:
                current = latest
                text = norm(current.message)
                low = text.lower()

    if "qual filial deseja se conectar" in low or "digite qual filial" in low:
        branch_reply, replies = await send_choice(client, entity, current, "1", "select the configured Marcopao branch")
        if not replies:
            raise RuntimeError("No response after branch selection 1.")
        latest = await latest_incoming(client, entity)
        current = latest or branch_reply
        text = norm(current.message)

    if not is_main_menu(text):
        history = await client.get_messages(entity, limit=40)
        menus = [m for m in history if not m.out and is_main_menu(m.message)]
        if menus:
            current = max(menus, key=lambda m:m.id)
        else:
            raise RuntimeError("Main report menu not confirmed after reset and branch selection.")
    return current

async def finish_card_prompt(client, entity):
    latest = await latest_incoming(client, entity)
    if latest and ("deseja visualizar os cartões" in norm(latest.message).lower()
                   or "deseja visualizar os cartoes" in norm(latest.message).lower()):
        await send_choice(client, entity, latest, "Não", "decline optional card details")

async def main():
    mode = os.environ.get("REPORT_MODE", "daily_yesterday").strip()
    if mode not in {"daily_yesterday", "monthly_october"}:
        print("INVALID_REPORT_MODE")
        return 2
    report_type = "sale_day" if mode == "daily_yesterday" else "monthly_sales"
    requested_period = "2026-10-26" if mode == "daily_yesterday" else "2026-10"

    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not all((api_id, api_hash, session)):
        print("CONFIG_MISSING: required Telegram Actions secrets are missing.")
        return 3

    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED")
            return 4
        entity = await client.get_entity(BOT)
        current = await latest_incoming(client, entity)
        if not current:
            print("NO_INCOMING_BOT_MESSAGES")
            return 5
        print(f"CURRENT_BOT_STATE id={current.id}: {norm(current.message)[:700]!r}")
        if buttons(current):
            print("BUTTONS: " + " | ".join(buttons(current)[:25]))

        current = await recover_menu(client, entity, current)
        menu_id = current.id
        option = "1" if mode == "daily_yesterday" else "2"
        current, replies = await send_choice(
            client, entity, current, option,
            "select daily sales report" if mode == "daily_yesterday" else "select monthly accumulated sales"
        )
        if not replies:
            return 6
        latest = replies[-1]
        combined = (norm(latest.message) + " " + " ".join(buttons(latest))).lower()

        if mode == "daily_yesterday":
            if "dia anterior" in combined or ("hoje" in combined and "anterior" in combined):
                latest, replies2 = await send_choice(client, entity, latest, "Dia anterior", "request the previous day's sales")
                if not replies2:
                    return 7
            report = await search_recent_report(client, entity, mode, after_id=menu_id)
            if not report:
                report = next((m for m in reversed(replies) if matches_report(m.message, mode)), None)
            if not report:
                latest_now = await latest_incoming(client, entity)
                if latest_now:
                    print(f"DAILY_REPORT_NOT_FOUND; current prompt={norm(latest_now.message)[:1200]!r}; buttons={buttons(latest_now)!r}")
                return 8
            save_report(report, report_type, requested_period)
            await finish_card_prompt(client, entity)
            return 0

        if ("ano atual" in combined and "ano anterior" in combined):
            latest, year_replies = await send_choice(client, entity, latest, "Ano Atual", "select current year for October 2026")
            if not year_replies:
                return 9
            replies.extend(year_replies)
            combined = (norm(latest.message) + " " + " ".join(buttons(latest))).lower()
        if "outubro" in combined and "selecione a opção desejada" in combined:
            latest, month_replies = await send_choice(client, entity, latest, "Outubro", "select October 2026")
            if not month_replies:
                return 10
            replies.extend(month_replies)
        report = await search_recent_report(client, entity, mode, after_id=menu_id)
        if not report:
            report = next((m for m in reversed(replies) if matches_report(m.message, mode)), None)
        if not report:
            latest_now = await latest_incoming(client, entity)
            if latest_now:
                print(f"MONTHLY_REPORT_NOT_FOUND; current prompt={norm(latest_now.message)[:1200]!r}; buttons={buttons(latest_now)!r}")
            return 11
        save_report(report, report_type, requested_period)
        await finish_card_prompt(client, entity)
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"COLLECTION_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
