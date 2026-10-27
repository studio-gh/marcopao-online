#!/usr/bin/env python3
"""Recover Bellabyte menu state, then select monthly sales and capture next prompt.

Uses only explicit menu options: 0 to return to branch selection, 1 for the
known branch, and 2 for Venda Acumulada Mês. Stops if any expected state is
not observed. Does not select a period or write to Supabase.
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

def labels(message):
    markup = getattr(message, "reply_markup", None)
    out = []
    for row in getattr(markup, "rows", []) or []:
        for button in getattr(row, "buttons", []) or []:
            label = getattr(button, "text", None)
            if label:
                out.append(str(label).strip())
    return out

async def latest(client, entity):
    items = await client.get_messages(entity, limit=1)
    return items[0] if items else None

async def wait_bot(client, entity, before_id, seconds=WAIT_SECONDS):
    for _ in range(seconds):
        await asyncio.sleep(1)
        items = await client.get_messages(entity, limit=15)
        incoming = [m for m in items if m.id > before_id and not m.out]
        if incoming:
            return sorted(incoming, key=lambda m: m.id)
    return []

def show(m):
    text = normalized(m.message)
    print(f"MSG_ID={m.id} [BOT] text={text[:500]!r}" + ("…" if len(text) > 500 else ""))
    b = labels(m)
    if b:
        print("BUTTONS: " + " | ".join(b[:30]))
    return text

async def send_and_wait(client, entity, choice, action):
    last = await latest(client, entity)
    if not last:
        print("STOPPED: no latest message before action.")
        return None
    print(f"ACTION: sending documented menu choice {choice} ({action}).")
    await client.send_message(entity, choice)
    incoming = await wait_bot(client, entity, last.id, seconds=WAIT_SECONDS + 4)
    if not incoming:
        print("NO_REPLY: no incoming bot response within wait window.")
        return None
    for m in incoming:
        show(m)
    return await latest(client, entity)

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
        cur = await latest(client, entity)
        if not cur:
            print("STOPPED: no conversation history.")
            return 4
        text = normalized(cur.message)
        print(f"CURRENT_STATE: {text[:300]!r}")
        low = text.lower()

        if "venda de hoje" in low and "dia anterior" in low:
            cur = await send_and_wait(client, entity, "0", "return to branch selection")
            if not cur:
                return 5
            text = normalized(cur.message)
            low = text.lower()

        if "qual filial deseja se conectar" in low:
            cur = await send_and_wait(client, entity, "1", "select Marcopão Lanchonete e Panificação Ltda")
            if not cur:
                return 6
            text = normalized(cur.message)
            low = text.lower()

        if "precisa se logar" in low or "precisa fazer login" in low:
            print("STOPPED: Bellabyte login is still required; no report choice sent.")
            return 7

        if "venda acumulada mês" not in low or "top 100" not in low:
            print("STOPPED: main report menu not confirmed; no report choice sent.")
            return 8

        cur = await send_and_wait(client, entity, "2", "select Venda Acumulada Mês")
        if not cur:
            return 9
        text = normalized(cur.message)
        low = text.lower()
        if "venda de hoje" in low and "dia anterior" in low:
            print("WARNING: bot returned daily-sales submenu; monthly report not confirmed.")
        else:
            print("NEXT_STATE_CAPTURED: inspect the prompt above to identify the monthly report's next step.")
        print("SAFE_END: no period selected and no Supabase writes made.")
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"PROBE_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
