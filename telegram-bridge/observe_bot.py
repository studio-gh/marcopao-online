#!/usr/bin/env python3
"""Read-only connectivity check for MonitorMarcopaoBot. Never sends messages."""
import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT_USERNAME = "MonitorMarcopaoBot"

async def main():
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not all((api_id, api_hash, session)):
        print("CONFIG_MISSING: configure TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_SESSION_STRING as GitHub Actions secrets.")
        return 2
    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED: the supplied session is not authorized.")
            return 3
        entity = await client.get_entity(BOT_USERNAME)
        print(f"TELEGRAM_CONNECTION_OK: found @{BOT_USERNAME} (id={entity.id}).")
        print("READ_ONLY_MODE: no messages sent; no reports fetched; no database writes.")
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"CHECK_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
