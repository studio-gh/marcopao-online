#!/usr/bin/env python3
"""State-aware Bellabyte conversation diagnostic.

If the bot is on the daily-sales period menu (Ano Atual/Ano Anterior), this
script stops and reports the choices without selecting either. It does not
send report selections, choose a period, or write to Supabase.
"""
import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT_USERNAME = "MonitorMarcopaoBot"
WAIT_SECONDS = 8

def normalized(message):
    return " ".join((message or "").split())

def button_labels(message):
    markup = getattr(message, "reply_markup", None)
    labels = []
    for row in getattr(markup, "rows", []) or []:
        for button in getattr(row, "buttons", []) or []:
            label = getattr(button, "text", None)
            if label:
                labels.append(str(label).strip())
    return labels

async def main():
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not all((api_id, api_hash, session)):
        print("CONFIG_MISSING: Telegram Actions secrets are required.")
        return 2

    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED")
            return 3
        entity = await client.get_entity(BOT_USERNAME)
        messages = await client.get_messages(entity, limit=5)
        if not messages:
            print("NO_HISTORY")
            return 0
        latest = messages[0]
        text = normalized(latest.message)
        labels = button_labels(latest)
        print(f"BOT_STATE: {text[:500]!r}")
        if labels:
            print("BUTTONS: " + " | ".join(labels[:30]))
        lower = text.lower()
        if "venda de hoje" in lower and "dia anterior" in lower:
            print("STATE_CLASSIFIED: daily-sales submenu.")
            print("SAFE_STOP: no option selected; 'Ano Atual' and 'Ano Anterior' are not monthly-report periods.")
        elif "venda acumulada mês" in lower and "top 100" in lower:
            print("STATE_CLASSIFIED: main report menu.")
            print("READY: next controlled step can select report 2.")
        elif "qual filial deseja se conectar" in lower:
            print("STATE_CLASSIFIED: branch selection menu.")
        elif "precisa se logar" in lower or "precisa fazer login" in lower:
            print("STATE_CLASSIFIED: Bellabyte login prompt.")
        else:
            print("STATE_CLASSIFIED: unknown state; no messages sent.")
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"DIAGNOSTIC_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
