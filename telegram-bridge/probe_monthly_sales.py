#!/usr/bin/env python3
"""State-aware, conservative probe for Bellabyte monthly sales menu.

Logs current bot state and buttons. If the conversation is on the daily-sales
sub-menu (Atual/Anterior), it backs out using the bot's documented '#' option
only when the menu's own prompt offers that option; otherwise it stops.
Never sends a report choice unless the main report menu is explicitly visible.
No period selection and no Supabase writes.
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

def buttons(message):
    markup = getattr(message, "reply_markup", None)
    labels = []
    for row in getattr(markup, "rows", []) or []:
        for button in getattr(row, "buttons", []) or []:
            label = getattr(button, "text", None)
            if label:
                labels.append(str(label).strip())
    return labels

async def latest_message(client, entity):
    msgs = await client.get_messages(entity, limit=1)
    return msgs[0] if msgs else None

async def wait_for_bot(client, entity, previous_id, seconds=WAIT_SECONDS):
    for _ in range(seconds):
        await asyncio.sleep(1)
        msgs = await client.get_messages(entity, limit=15)
        new = [m for m in msgs if m.id > previous_id and not m.out]
        if new:
            return sorted(new, key=lambda m: m.id)
    return []

def show(message):
    text = normalized(message.message)
    print(f"BOT_STATE: {text[:500]!r}" + ("…" if len(text) > 500 else ""))
    labels = buttons(message)
    if labels:
        print("BUTTONS: " + " | ".join(labels[:30]))
    return text, labels

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
        latest = await latest_message(client, entity)
        if not latest:
            print("STOPPED: no conversation history.")
            return 4

        current, labels = show(latest)
        lower = current.lower()

        if "venda de hoje" in lower and "dia anterior" in lower:
            # Only use the back/menu token if the same keyboard explicitly offers it.
            if "#" in labels:
                print("ACTION: returning from daily-sales submenu with offered '#' option.")
                before = latest.id
                await client.send_message(entity, "#")
                replies = await wait_for_bot(client, entity, before)
                for msg in replies:
                    show(msg)
                latest = await latest_message(client, entity)
                current = normalized(latest.message if latest else "")
                lower = current.lower()
            else:
                print("STOPPED: daily-sales submenu detected, but no explicit back option found.")
                return 5

        if "precisa se logar" in lower or "precisa fazer login" in lower:
            print("STOPPED: Bellabyte application login is required.")
            return 6

        if "venda acumulada mês" in lower and "top 100" in lower:
            print("MAIN_REPORT_MENU_CONFIRMED: ready to select report 2 in a separate approved step.")
            print("NO_REPORT_CHOICE_SENT: this run only diagnoses conversation state.")
            return 0

        if "qual filial deseja se conectar" in lower:
            print("STOPPED: bot is asking for branch selection; no branch or report choice sent in this diagnostic run.")
            return 0

        print("STOPPED: state not recognized; no report choice sent.")
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"PROBE_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
