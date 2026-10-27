#!/usr/bin/env python3
"""Safe recovery from the Bellabyte monthly-report year-choice prompt.

Recognizes the bot's explicit "Ano Atual"/"Ano Anterior" choices and stops
without choosing either. If the stale daily-sales submenu is present, sends
only the documented 0 to return to branch selection. It never sends 1 while
the year-choice prompt is active and never writes to Supabase.
"""
import asyncio, os, sys
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT = "MonitorMarcopaoBot"

def norm(s):
    return " ".join((s or "").split())

def labels(m):
    markup = getattr(m, "reply_markup", None)
    return [str(b.text).strip() for row in (getattr(markup, "rows", []) or [])
            for b in (getattr(row, "buttons", []) or []) if getattr(b, "text", None)]

async def latest_incoming(client, entity):
    msgs = await client.get_messages(entity, limit=30)
    incoming = [m for m in msgs if not m.out]
    return incoming[0] if incoming else None

async def wait_incoming(client, entity, before_id, seconds=12):
    for _ in range(seconds):
        await asyncio.sleep(1)
        msgs = await client.get_messages(entity, limit=30)
        found = sorted([m for m in msgs if m.id > before_id and not m.out], key=lambda m:m.id)
        if found:
            return found
    return []

async def send_choice(client, entity, current, choice, reason):
    print(f"ACTION: sending {choice!r} ({reason})")
    await client.send_message(entity, choice)
    incoming = await wait_incoming(client, entity, current.id)
    if not incoming:
        print("NO_BOT_REPLY: no incoming response after the choice.")
        return None
    for m in incoming:
        print(f"BOT_REPLY id={m.id} text={norm(m.message)[:500]!r}")
        b = labels(m)
        if b: print("BUTTONS: " + " | ".join(b[:20]))
    return await latest_incoming(client, entity)

async def main():
    api_id=os.environ.get("TELEGRAM_API_ID","").strip()
    api_hash=os.environ.get("TELEGRAM_API_HASH","").strip()
    session=os.environ.get("TELEGRAM_SESSION_STRING","").strip()
    if not all((api_id,api_hash,session)):
        print("CONFIG_MISSING: Telegram secrets required"); return 2
    client=TelegramClient(StringSession(session),int(api_id),api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED"); return 3
        entity=await client.get_entity(BOT)
        cur=await latest_incoming(client,entity)
        if not cur:
            print("NO_INCOMING_BOT_MESSAGES"); return 4
        text=norm(cur.message); low=text.lower()
        print(f"CURRENT_BOT_STATE id={cur.id}: {text[:500]!r}")
        b=labels(cur)
        if b: print("BUTTONS: " + " | ".join(b[:20]))

        # Do not send arbitrary digits while the year-choice question is active.
        if ("ano atual" in low and "ano anterior" in low) or ("ano atual" in " ".join(b).lower() and "ano anterior" in " ".join(b).lower()):
            print("YEAR_CHOICE_PROMPT_DETECTED: bot is waiting for Ano Atual or Ano Anterior.")
            # First test is October 2026, so choose the named current-year option.
            cur = await send_choice(client, entity, cur, "Ano Atual", "select current year for October 2026 test")
            if not cur: return 7
            text = norm(cur.message); low = text.lower()
            print(f"STATE_AFTER_YEAR: {text[:500]!r}")
            b = labels(cur)
            if b: print("BUTTONS: " + " | ".join(b[:20]))
            print("SAFE_STOP: captured next state only; no report or period guessed.")
            return 0

        # Recover only if the daily-sales submenu is explicitly current.
        if "dia anterior" in low and ("venda" in low or "opção" in low or "opcao" in low):
            print("DAILY_SALES_SUBMENU_DETECTED")
            cur=await send_choice(client,entity,cur,"0","documented return to branch selection")
            if not cur: return 5
            text=norm(cur.message); low=text.lower()
            print(f"STATE_AFTER_ZERO: {text[:500]!r}")
            if "qual filial deseja se conectar" in low:
                cur=await send_choice(client,entity,cur,"1","select known branch")
                if not cur: return 6
                text=norm(cur.message); low=text.lower()
                print(f"STATE_AFTER_BRANCH: {text[:500]!r}")

        if "venda acumulada mês" in low and "top 100" in low:
            print("MAIN_REPORT_MENU_CONFIRMED")
            print("SAFE_STOP: this recovery run does not select report or period.")
        else:
            print("SAFE_STOP: current state not confirmed as main report menu; no more options sent.")
        return 0
    finally:
        await client.disconnect()

if __name__=="__main__":
    try: sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"RECOVERY_FAILED: {type(exc).__name__}: {exc}"); sys.exit(1)
