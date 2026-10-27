#!/usr/bin/env python3
"""One-step guided report-flow probe: selects report 2 and captures the next bot prompt.

This sends a single message ("2") to the configured bot conversation. It does not
select a period, request additional report steps, or write to Supabase.
"""
import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT_USERNAME = "MonitorMarcopaoBot"
REPORT_CHOICE = "2"
WAIT_SECONDS = 12

async def main():
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not all((api_id, api_hash, session)):
        print("CONFIG_MISSING: configure Telegram GitHub Actions secrets.")
        return 2

    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED")
            return 3
        entity = await client.get_entity(BOT_USERNAME)
        latest = await client.get_messages(entity, limit=1)
        if not latest:
            print("STOPPED: no conversation history; refusing to send a menu choice.")
            return 4
        latest_text = (latest[0].message or "").strip()
        # Only proceed when the latest bot response clearly shows the report menu.
        if not ("Venda Acumulada Mês" in latest_text and "Top 100" in latest_text):
            print("STOPPED: latest message does not look like the report-selection menu.")
            print("Please run the read-only inspector again and check the bot is at the report menu.")
            return 5

        print("PRECHECK_OK: latest bot message appears to be the report menu.")
        print("ACTION: sending only option 2 (Venda Acumulada Mês); no period selection will be sent.")
        before_id = latest[0].id
        await client.send_message(entity, REPORT_CHOICE)
        await asyncio.sleep(WAIT_SECONDS)
        responses = await client.get_messages(entity, limit=8)
        new_messages = [m for m in reversed(responses) if m.id > before_id]
        if not new_messages:
            print("NO_REPLY_YET: no new message appeared within the wait window.")
            return 0
        for message in new_messages:
            direction = "OUTGOING" if message.out else "BOT"
            text = " ".join((message.message or "").split())
            # Capture prompt text only, truncated to avoid dumping an entire report.
            preview = text[:500] + ("…" if len(text) > 500 else "")
            print(f"MSG_ID={message.id} [{direction}] text={preview!r}")
            markup = getattr(message, "reply_markup", None)
            labels = []
            for row in getattr(markup, "rows", []) or []:
                for button in getattr(row, "buttons", []) or []:
                    label = getattr(button, "text", None)
                    if label:
                        labels.append(str(label)[:100])
            if labels:
                print("BUTTONS: " + " | ".join(labels[:30]))
        print("PROBE_COMPLETE: no period was selected and no database writes were made.")
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"PROBE_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
