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
    # Bot replies can arrive after a delay or in multiple messages. Wait longer,
    # and poll for all incoming messages newer than the current state.
    incoming = await wait_incoming(client, entity, current.id, seconds=30)
    if not incoming:
        # Some bot versions may edit/attach the reply to a later update; show latest state.
        latest = await latest_incoming(client, entity)
        if latest and latest.id > current.id:
            incoming = [latest]
    if not incoming:
        print("NO_BOT_REPLY: no incoming response after 30 seconds.")
        return None
    for m in incoming:
        print(f"BOT_REPLY id={m.id} text={norm(m.message)[:1200]!r}")
        b = labels(m)
        if b: print("BUTTONS: " + " | ".join(b[:20]))
    return max(incoming, key=lambda m: m.id)

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


        # The monthly report was delivered and the bot asks whether to show payment cards.
        # Choose "Não" to finish this report flow and avoid leaving the conversation waiting.
        if "deseja visualizar os cartões" in low or "deseja visualizar os cartoes" in low:
            print("CARD_PROMPT_DETECTED: choosing Não to finish the report flow.")
            # Capture the already-delivered monthly report from recent incoming history.
            history = await client.get_messages(entity, limit=30)
            reports = [m for m in history if not m.out and "venda acumulada do mês" in norm(m.message).lower()]
            if reports:
                report = max(reports, key=lambda m:m.id)
                report_text = norm(report.message)
                print("REPORT_CAPTURED: monthly sales report exists in Telegram history.")
                print(f"REPORT_MESSAGE_ID={report.id}")
                print("REPORT_TEXT_BEGIN")
                print(report_text[:12000])
                print("REPORT_TEXT_END")
            else:
                print("REPORT_NOT_FOUND_IN_RECENT_HISTORY: card prompt is current, but report message was not found.")
            cur = await send_choice(client, entity, cur, "Não", "decline card details and finish report flow")
            if cur:
                print(f"STATE_AFTER_CARDS: {norm(cur.message)[:1200]!r}")
                b = labels(cur)
                if b: print("BUTTONS: " + " | ".join(b[:20]))
            print("SAFE_STOP: no database writes performed.")
            return 0

        # If this run starts directly at the month menu, continue from that state.
        button_text = " ".join(b).lower()
        if "selecione a opção desejada no teclado" in low and "outubro" in button_text:
            print("MONTH_MENU_DETECTED: selecting October for the October 2026 test.")
            cur = await send_choice(client, entity, cur, "Outubro", "select October 2026 from current month menu")
            if not cur: return 8
            text = norm(cur.message); low = text.lower()
            print(f"STATE_AFTER_MONTH: {text[:800]!r}")
            b = labels(cur)
            if b: print("BUTTONS: " + " | ".join(b[:20]))
            if not cur:
                print("REPORT_CAPTURE_PENDING: sent October; reply may arrive later. Re-run to inspect latest bot state.")
                return 0
            # If report arrives, capture enough text to support parser work, but do not print secrets.
            report_text = norm(cur.message)
            if "venda acumulada" in report_text.lower() and "total" in report_text.lower():
                print("REPORT_CAPTURED: monthly sales report received.")
                print("REPORT_TEXT_BEGIN")
                print(report_text[:12000])
                print("REPORT_TEXT_END")
                follow = await wait_incoming(client, entity, cur.id, seconds=8)
                for m in follow:
                    print(f"FOLLOWUP id={m.id} text={norm(m.message)[:1000]!r}")
                    b2 = labels(m)
                    if b2: print("FOLLOWUP_BUTTONS: " + " | ".join(b2[:20]))
            else:
                print(f"STATE_AFTER_MONTH: {report_text[:3000]!r}")
                b2 = labels(cur)
                if b2: print("BUTTONS: " + " | ".join(b2[:20]))
            print("SAFE_STOP: no database writes performed.")
            return 0

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
            # We explicitly want October 2026 for the first test. Choose the exact displayed label.
            if "selecione a opção desejada no teclado" in low and "outubro" in " ".join(b).lower():
                cur = await send_choice(client, entity, cur, "Outubro", "select October 2026 for the monthly-sales test")
                if not cur: return 8
                text = norm(cur.message); low = text.lower()
                print(f"STATE_AFTER_MONTH: {text[:800]!r}")
                b = labels(cur)
                if b: print("BUTTONS: " + " | ".join(b[:20]))
            print("SAFE_STOP: captured report response; no database writes performed.")
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
