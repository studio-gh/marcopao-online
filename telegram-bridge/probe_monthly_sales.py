#!/usr/bin/env python3
"""Guided Bellabyte login + monthly sales prompt probe.

Uses BELLABYTE_PASSWORD only if the bot explicitly says a login is required.
Never prints the password. Selects branch 1 and report 2 only when the
observed conversation state matches the expected prompts. No period selection
or Supabase writes occur.
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
        # Ignore our own outgoing echo; wait for a bot response to avoid racing the state machine.
        new = [m for m in msgs if m.id > previous_id and not m.out]
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
    password = os.environ.get("BELLABYTE_PASSWORD", "")
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

        # Bellabyte's own application login, not Telegram account authentication.
        if "precisa se logar" in current.lower() or "precisa fazer login" in current.lower():
            if not password:
                print("BELLABYTE_PASSWORD_MISSING: add the Bellabyte-only password as Actions secret BELLABYTE_PASSWORD.")
                return 6
            print("ACTION: sending Bellabyte password from Actions secret; value is never logged.")
            before = latest.id
            await client.send_message(entity, password)
            new = await wait_for_new(client, entity, before, seconds=WAIT_SECONDS + 4)
            if new:
                await show_messages(new)
            latest = await latest_message(client, entity)
            current = normalized(latest.message if latest else "")
            if "precisa se logar" in current.lower() or "precisa fazer login" in current.lower():
                print("LOGIN_NOT_ACCEPTED_OR_MORE_STEPS_REQUIRED: stopped without selecting a report.")
                return 7

        # If the bot asks for a branch, select the known branch.
        if "qual filial deseja se conectar" in current.lower():
            print("ACTION: selecting branch 1 (Marcopão Lanchonete e Panificação Ltda).")
            before = latest.id
            await client.send_message(entity, BRANCH_CHOICE)
            new = await wait_for_new(client, entity, before)
            if new:
                await show_messages(new)
            latest = await latest_message(client, entity)
            current = normalized(latest.message if latest else "")

        if "precisa se logar" in current.lower() or "precisa fazer login" in current.lower():
            print("STOPPED: Bellabyte still requires login.")
            return 7
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
