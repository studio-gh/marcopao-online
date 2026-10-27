#!/usr/bin/env python3
"""Guided probe: select branch 1 if prompted, then report 2, and capture next prompt.

This sends up to two expected menu choices to the bot. It does not select a
period, request further steps, or write to Supabase.
"""
import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT_USERNAME = "MonitorMarcopaoBot"
BRANCH_CHOICE = "1"
REPORT_CHOICE = "2"
WAIT_SECONDS = 8

def normalized(message):
    return " ".join((message or "").split())

async def latest_message(client, entity):
    msgs = await client.get_messages(entity, limit=1)
    return msgs[0] if msgs else None

async def wait_for_new(client, entity, previous_id, seconds=WAIT_SECONDS):
    for _ in range(seconds):
        await asyncio.sleep(1)
        msgs = await client.get_messages(entity, limit=10)
        new = [m for m in msgs if m.id > previous_id]
        if new:
            return sorted(new, key=lambda m: m.id)
    return []

async def show_messages(messages):
    for m in messages:
        direction = "OUTGOING" if m.out else "BOT"
        text = normalized(m.message)
        print(f"MSG_ID={m.id} [{direction}] text={text[:500]!r}" + ("…" if len(text) > 500 else ""))
        markup = getattr(m, "reply_markup", None)
        labels = []
        for row in getattr(markup, "rows", []) or []:
            for button in getattr(row, "buttons", []) or []:
                label = getattr(button, "text", None)
                if label:
                    labels.append(str(label)[:100])
        if labels:
            print("BUTTONS: " + " | ".join(labels[:30]))

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
        latest = await latest_message(client, entity)
        if not latest:
            print("STOPPED: no conversation history.")
            return 4

        current = normalized(latest.message)
        print(f"CURRENT_STATE: {current[:300]!r}")
        # If the bot currently asks for a branch, select branch 1 first.
        if "qual filial deseja se conectar" in current.lower():
            print("ACTION: selecting branch 1 (Marcopão Lanchonete e Panificação Ltda).")
            before = latest.id
            await client.send_message(entity, BRANCH_CHOICE)
            new = await wait_for_new(client, entity, before)
            await show_messages(new)
            latest = await latest_message(client, entity)
            current = normalized(latest.message if latest else "")
        # Only send report choice if the latest bot state is a report menu.
        if "venda acumulada mês" not in current.lower() or "top 100" not in current.lower():
            print("STOPPED: latest state is not the expected report-selection menu; no report option sent.")
            return 5

        print("ACTION: selecting report 2 (Venda Acumulada Mês). No period will be selected.")
        before = latest.id
        await client.send_message(entity, REPORT_CHOICE)
        new = await wait_for_new(client, entity, before, seconds=WAIT_SECONDS + 4)
        if new:
            await show_messages(new)
        else:
            print("NO_REPLY_YET: no new message appeared within the wait window.")
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
