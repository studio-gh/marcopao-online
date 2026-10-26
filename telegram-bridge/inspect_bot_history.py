#!/usr/bin/env python3
"""Inspect the existing MonitorMarcopaoBot conversation without sending messages."""
import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT_USERNAME = "MonitorMarcopaoBot"
MAX_MESSAGES = 12

def button_labels(message):
    markup = getattr(message, "reply_markup", None)
    if not markup:
        return []
    labels = []
    for row in getattr(markup, "rows", []) or []:
        for button in getattr(row, "buttons", []) or []:
            label = getattr(button, "text", None)
            if label:
                labels.append(str(label)[:100])
    return labels

async def main():
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not all((api_id, api_hash, session)):
        print("CONFIG_MISSING: configure the three Telegram GitHub Actions secrets.")
        return 2

    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED")
            return 3
        entity = await client.get_entity(BOT_USERNAME)
        print(f"CONNECTION_OK: @{BOT_USERNAME}; inspecting up to {MAX_MESSAGES} existing messages.")
        print("READ_ONLY: this script does not send messages or write to Supabase.")
        messages = await client.get_messages(entity, limit=MAX_MESSAGES)
        if not messages:
            print("NO_HISTORY: no existing messages were returned for this bot.")
            return 0
        for message in reversed(messages):
            direction = "OUTGOING" if message.out else "BOT"
            stamp = message.date.isoformat() if message.date else "unknown-time"
            text = (message.message or "").replace("\\n", " ").replace("\n", " ").strip()
            # Deliberately show only a short prefix to avoid dumping sales data into CI logs.
            preview = text[:140] + ("…" if len(text) > 140 else "")
            print(f"MSG {stamp} [{direction}] text={preview!r}")
            labels = button_labels(message)
            if labels:
                print("  BUTTONS: " + " | ".join(labels[:30]))
        print("INSPECTION_COMPLETE: use the observed menu labels/sequence to implement a controlled report request.")
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"INSPECTION_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
