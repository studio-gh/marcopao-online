#!/usr/bin/env python3
"""Capture Bellabyte sales reports and persist raw data plus parsed totals to Supabase for review."""
import asyncio
import hashlib
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession

BOT = "MonitorMarcopaoBot"
BRANCH = "MARCOPAO LANCHONETE E PANIFICACAO LTDA"
OUT_DIR = Path("artifacts")

def norm(text):
    return " ".join((text or "").split())

def is_report_body(text):
    low = norm(text).lower()
    return (
        "r$" in low
        or "dinheiro:" in low
        or "pos crédito:" in low
        or "pos débito:" in low
        or "total de vendas nos pdvs" in low
        or "venda acumulada do mês" in low
        or "venda total do dia" in low
    )

def safe_preview(text, limit=220):
    normalized = norm(text)
    return f"<report body redacted; {len(normalized)} chars>" if is_report_body(normalized) else repr(normalized[:limit])

def buttons(message):
    markup = getattr(message, "reply_markup", None)
    return [str(button.text).strip()
            for row in (getattr(markup, "rows", []) or [])
            for button in (getattr(row, "buttons", []) or [])
            if getattr(button, "text", None)]

async def latest_incoming(client, entity):
    items = await client.get_messages(entity, limit=100)
    incoming = [message for message in items if not message.out]
    return max(incoming, key=lambda message: message.id) if incoming else None

async def wait_replies(client, entity, after_id, timeout=30):
    found = {}
    for _ in range(timeout):
        await asyncio.sleep(1)
        for message in await client.get_messages(entity, limit=100):
            if message.id > after_id and not message.out:
                found[message.id] = message
        if found:
            await asyncio.sleep(2)
            for message in await client.get_messages(entity, limit=100):
                if message.id > after_id and not message.out:
                    found[message.id] = message
            return sorted(found.values(), key=lambda message: message.id)
    return []

async def send_choice(client, entity, current, choice, reason):
    safe_choice = "<redacted>" if ("password" in reason.lower() or "login" in reason.lower()) else repr(choice)
    print(f"ACTION: sending {safe_choice} ({reason})")
    await client.send_message(entity, choice)
    replies = await wait_replies(client, entity, current.id)
    if not replies:
        print("NO_BOT_REPLY: no response within 30 seconds.")
        return None, []
    for message in replies:
        preview = norm(message.message)
        print(f"BOT_REPLY id={message.id} prompt={safe_preview(preview)}")
        labels = buttons(message)
        if labels:
            print("BUTTONS: " + " | ".join(labels[:25]))
    return replies[-1], replies

async def ensure_bellabyte_login(client, entity, current):
    """Complete Bellabyte's application login whenever a workflow navigation step triggers it."""
    state = norm(current.message).lower()
    if "precisa se logar" not in state and "precisa fazer login" not in state:
        return current
    password = os.environ.get("BELLABYTE_PASSWORD", "").strip()
    if not password:
        print("BELLABYTE_PASSWORD_MISSING")
        raise RuntimeError("Bellabyte application login is required but BELLABYTE_PASSWORD is empty.")
    current, replies = await send_choice(
        client, entity, current, password, "Bellabyte application login password"
    )
    if not replies:
        raise RuntimeError("Bellabyte login prompt received no reply after password submission.")
    latest = await latest_incoming(client, entity)
    current = latest or current
    state = norm(current.message).lower()
    if "precisa se logar" in state or "precisa fazer login" in state:
        print("BELLABYTE_LOGIN_NOT_ACCEPTED")
        raise RuntimeError("Bellabyte did not accept the application login; verify the BELLABYTE_PASSWORD secret.")
    return current

def br_number(value):
    return float(value.replace(".", "").replace(",", "."))

def parse_totals(raw):
    patterns = {
        "dinheiro": r"Dinheiro\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        "pix": r"PIX\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        "pos_credito": r"POS Crédito\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        "pos_debito": r"POS Débito\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        "tef": r"TEF\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        "ticket_pagamento": r"TICKET\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        "total": r"(?:Venda Total do dia|Total)\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        "ticket_medio": r"Ticket Médio\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
    }
    totals = {}
    warnings = []
    for key, pattern in patterns.items():
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            try:
                totals[key] = round(br_number(match.group(1)), 2)
            except ValueError:
                warnings.append(f"Valor inválido: {key}")
    coupons = re.search(r"Quantidades?\s+de\s+Cupons\s*:?\s*([\d.]+)", raw, re.IGNORECASE)
    if coupons:
        totals["cupons"] = int(coupons.group(1).replace(".", ""))
    if "total" not in totals:
        warnings.append("Total de vendas não foi identificado.")
    if "cupons" not in totals:
        warnings.append("Quantidade de cupons não foi identificada.")
    payment_keys = ["dinheiro", "pix", "pos_credito", "pos_debito", "tef", "ticket_pagamento"]
    card_keys = ["pos_credito", "pos_debito", "tef"]
    missing_cards = [key for key in card_keys if key not in totals]
    totals["payment_methods_included"] = [key for key in payment_keys if key in totals]
    totals["card_sales_included"] = not missing_cards
    if missing_cards:
        warnings.append("Detalhes de cartão incompletos: faltando " + ", ".join(missing_cards) + ".")
    if "total" in totals and all(key in totals for key in payment_keys):
        payment_sum = round(sum(totals[key] for key in payment_keys), 2)
        totals["payment_methods_sum"] = payment_sum
        totals["payment_reconciliation_diff"] = round(payment_sum - totals["total"], 2)
        if abs(totals["payment_reconciliation_diff"]) > 0.02:
            warnings.append("Soma dos meios de pagamento difere do total informado.")
    totals["validation_warnings"] = warnings
    return totals, warnings

def persist_to_supabase(report_file, report_type, requested_period, queried_at):
    base_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base_url or not service_key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY secret; refusing to discard captured report.")
    report = json.loads(Path(report_file).read_text(encoding="utf-8"))
    raw = report["raw_text"]
    totals, warnings = parse_totals(raw)
    low_raw = norm(raw).lower()
    allowed_markers = (
        "venda do dia", "venda de hoje", "venda de ontem",
        "venda acumulada do mês", "venda total do dia", "total de vendas nos pdvs"
    )
    if "total" not in totals or not any(term in low_raw for term in allowed_markers):
        raise RuntimeError("Report validation failed; database write was not attempted.")
    if report_type == "sale_day":
        if not any(term in low_raw for term in ("venda do dia", "venda de hoje", "venda de ontem", "venda total do dia")):
            raise RuntimeError("Daily report did not contain a recognized daily-sales marker.")
    elif "venda acumulada do mês" not in low_raw:
        raise RuntimeError("Monthly report did not contain a recognized monthly-sales marker.")
    branch = report.get("branch", "")
    if report_type == "sale_day":
        period_start = requested_period
        period_end = requested_period
        report_date = requested_period
    else:
        period_start = requested_period + "-01"
        period_end = queried_at.date().isoformat()
        report_date = None
    notes = [
        "Capturado automaticamente do Bellabyte; aguarda revisão semântica.",
        "Relatório bruto preservado; status não será promovido automaticamente a validated.",
    ] + warnings
    row = {
        "report_type": report_type,
        "branch_id": None,
        "branch_name_raw": branch,
        "period_start": period_start,
        "period_end": period_end,
        "report_date": report_date,
        "source": "telegram_monitorbot",
        "raw_text": raw,
        "content_sha256": report["content_sha256"],
        "status": "needs_review",
        "validation_notes": notes,
        "totals": totals,
    }
    body = json.dumps([row], ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/rest/v1/report_imports?on_conflict=content_sha256",
        data=body,
        method="POST",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 201, 204):
                raise RuntimeError(f"Supabase returned HTTP {response.status}")
            print(f"SUPABASE_IMPORT_HTTP={response.status}")
            print(f"SUPABASE_IMPORT_HASH={report['content_sha256']}")
            print(f"SUPABASE_STATUS=needs_review")
    except urllib.error.HTTPError as exc:
        # Do not print response body, which can contain business data.
        raise RuntimeError(f"Supabase import failed with HTTP {exc.code}") from None

def save_report(message, report_type, requested_period, raw_text=None):
    """Persist a Telegram message or an explicitly supplied card-detail text as a protected artifact."""

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = raw_text if raw_text is not None else (message.message or "")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    payload = {
        "source": "telegram_monitorbot",
        "bot": BOT,
        "branch": BRANCH,
        "report_type": report_type,
        "requested_period": requested_period,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "message_id": message.id,
        "message_date": message.date.astimezone(timezone.utc).isoformat() if message.date else None,
        "buttons": buttons(message),
        "content_sha256": digest,
        "raw_text": raw,
        "status": "review_required",
        "database_written": False,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{report_type}_{requested_period}_{stamp}.json"
    output = OUT_DIR / filename
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT_CAPTURED={output}")
    print(f"CONTENT_SHA256={digest}")
    print(f"REPORT_MESSAGE_ID={message.id}")
    # Public repository: never print raw sales data to workflow logs.
    print("REPORT_TEXT_REDACTED: raw report stored only in protected Supabase row.")
    return output

def is_main_menu(text):
    low = norm(text).lower()
    return "venda do dia" in low and "venda acumulada mês" in low and "top 100" in low

def matches_report(text, mode):
    low = norm(text).lower()
    if mode == "daily_yesterday":
        # Require explicit report-body markers. A generic "Venda do Dia Anterior"
        # heading is not enough because Telegram history can contain stale reports.
        return (
            any(marker in low for marker in (
                "venda total do dia", "total de vendas nos pdvs", "quantidades de cupons"
            ))
            and ("dinheiro" in low or "tef" in low or "pix" in low)
            and "venda acumulada do mês" not in low
            and "selecione a opção da venda de hoje" not in low
        )
    return (
        "venda acumulada do mês" in low
        and ("total de vendas nos pdvs" in low or "venda total do dia" in low)
        and "deseja visualizar os cartões" not in low
    )
async def search_recent_report(client, entity, mode, after_id=None, limit=100):
    # Scan enough history to recover missed days without flooding Telegram with unbounded history.
    items = await client.get_messages(entity, limit=limit)
    candidates = [m for m in items if not m.out and matches_report(m.message, mode)]
    if after_id is not None:
        candidates = [m for m in candidates if m.id > after_id]
    return max(candidates, key=lambda m: m.id) if candidates else None

async def find_historical_daily_report(client, entity, target_date, scan_limit=500):
    """Locate a real daily-report message already present in the Monitorbot chat history."""
    items = await client.get_messages(entity, limit=scan_limit)
    for message in sorted((m for m in items if not m.out), key=lambda m: m.id, reverse=True):
        if not matches_report(message.message, "daily_yesterday"):
            continue
        body = norm(message.message)
        low = body.lower()
        # The Monitorbot's explicit "Venda do Dia Anterior" label means the report date
        # can be inferred from the report request only when its message timestamp is the target day.
        # Prefer explicit dates if the body contains one; otherwise use the Telegram message date
        # only when it is on the target calendar day, avoiding assumptions about stale reports.
        explicit = re.search(r"(?:data|referência|período)\s*:?\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", body, re.I)
        if explicit:
            value = explicit.group(1)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                report_date = value
            else:
                dd, mm, yyyy = value.split("/")
                report_date = f"{yyyy}-{mm}-{dd}"
            if report_date == target_date:
                return message
            continue
        if "venda do dia anterior" in low and message.date:
            message_day = message.date.astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat()
            if message_day == target_date:
                return message
    return None

async def wait_for_report(client, entity, mode, after_id, timeout=30):
    """Poll Telegram history for the report, not just the first prompt response."""
    for _ in range(max(1, timeout // 2)):
        report = await search_recent_report(client, entity, mode, after_id=after_id)
        if report:
            return report
        await asyncio.sleep(2)
    return await search_recent_report(client, entity, mode, after_id=after_id)

async def recover_menu(client, entity, current):
    """Use the bot-documented 0 reset if conversation is not at a known menu."""
    text = norm(current.message)
    low = text.lower()
    if is_main_menu(text):
        return current
    if "qual filial deseja se conectar" in low or "digite qual filial" in low:
        pass
    else:
        reset, replies = await send_choice(client, entity, current, "0", "reset stale conversation to branch selection")
        if not replies:
            raise RuntimeError("No response after the documented reset option 0.")
        current = replies[-1]
        text = norm(current.message)
        low = text.lower()
        if not is_main_menu(text) and "qual filial deseja se conectar" not in low and "digite qual filial" not in low:
            latest = await latest_incoming(client, entity)
            if latest:
                current = latest
                text = norm(current.message)
                low = text.lower()

    if "qual filial deseja se conectar" in low or "digite qual filial" in low:
        branch_reply, replies = await send_choice(client, entity, current, "1", "select the configured Marcopao branch")
        if not replies:
            raise RuntimeError("No response after branch selection 1.")
        latest = await latest_incoming(client, entity)
        current = latest or branch_reply
        current = await ensure_bellabyte_login(client, entity, current)
        current = await latest_incoming(client, entity) or current
        text = norm(current.message)

    if not is_main_menu(text):
        history = await client.get_messages(entity, limit=40)
        menus = [m for m in history if not m.out and is_main_menu(m.message)]
        if menus:
            current = max(menus, key=lambda m:m.id)
        else:
            raise RuntimeError("Main report menu not confirmed after reset and branch selection.")
    return current

async def finish_card_prompt(client, entity):
    """Always request card details when the Monitorbot offers them; never skip card sales."""
    latest = await latest_incoming(client, entity)
    if latest and ("deseja visualizar os cartões" in norm(latest.message).lower()
                   or "deseja visualizar os cartoes" in norm(latest.message).lower()):
        _, replies = await send_choice(
            client, entity, latest, "Sim", "request card transaction details for complete sales totals"
        )
        if not replies:
            raise RuntimeError("Monitorbot did not return the requested card details.")
        card_report = next((m for m in reversed(replies) if is_report_body(m.message)), None)
        if card_report is None:
            latest_now = await latest_incoming(client, entity)
            if latest_now and is_report_body(latest_now.message):
                card_report = latest_now
        if card_report is None:
            print("CARD_DETAILS_RESPONSE_NOT_RECOGNIZED")
        else:
            print(f"CARD_DETAILS_CAPTURED message_id={card_report.id}; review card totals before combining with daily total.")
        return card_report
    return None

async def main():
    mode = os.environ.get("REPORT_MODE", "daily_yesterday").strip()
    if mode not in {"daily_yesterday", "monthly_october"}:
        print("INVALID_REPORT_MODE")
        return 2
    report_type = "sale_day" if mode == "daily_yesterday" else "monthly_sales"
    local_now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    requested_period = (local_now.date() - timedelta(days=1)).isoformat() if mode == "daily_yesterday" else "2026-10"

    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not all((api_id, api_hash, session)):
        print("CONFIG_MISSING: required Telegram Actions secrets are missing.")
        return 3

    if not os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip():
        print("SUPABASE_SERVICE_ROLE_KEY_MISSING: add the secret before running a collector that persists data.")
        return 12
    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED")
            return 4
        entity = await client.get_entity(BOT)
        current = await latest_incoming(client, entity)
        if not current:
            print("NO_INCOMING_BOT_MESSAGES")
            return 5

        # Bellabyte application login is separate from Telegram session authorization.
        state = norm(current.message).lower()
        if "precisa se logar" in state or "precisa fazer login" in state:
            password = os.environ.get("BELLABYTE_PASSWORD", "")
            if not password:
                print("BELLABYTE_PASSWORD_MISSING")
                return 13
            current, login_replies = await send_choice(client, entity, current, password, "Bellabyte application login password")
            if not login_replies:
                print("BELLABYTE_LOGIN_NO_REPLY")
                return 14
            latest = await latest_incoming(client, entity)
            if latest:
                current = latest
            state = norm(current.message).lower()
            if "precisa se logar" in state or "precisa fazer login" in state:
                print("BELLABYTE_LOGIN_NOT_ACCEPTED")
                return 15

        initial_preview = norm(current.message)
        print(f"CURRENT_BOT_STATE id={current.id}: {safe_preview(initial_preview)}")
        if buttons(current):
            print("BUTTONS: " + " | ".join(buttons(current)[:25]))

        current = await recover_menu(client, entity, current)
        menu_id = current.id
        option = "1" if mode == "daily_yesterday" else "2"
        current, replies = await send_choice(
            client, entity, current, option,
            "select daily sales report" if mode == "daily_yesterday" else "select monthly accumulated sales"
        )
        if not replies:
            return 6
        latest = replies[-1]
        latest = await ensure_bellabyte_login(client, entity, latest)
        latest = await latest_incoming(client, entity) or latest
        combined = (norm(latest.message) + " " + " ".join(buttons(latest))).lower()

        if mode == "daily_yesterday":
            # Historical messages are not date-keyed reliably by Telegram and can be stale.
            # Never backfill a date from the message timestamp alone; request the target day
            # from the bot's explicit date-selection flow below instead.
            # Avoid stale monthly/year-selection prompts returned from old bot history.
            latest_text = norm(latest.message).lower()
            if (("ano atual" in latest_text and "ano anterior" in latest_text)
                    or ("selecione o mês" in latest_text and "outubro" in latest_text)
                    or ("selecione a opção desejada" in latest_text and "outubro" in combined)):
                print("DAILY_FLOW_BLOCKED_BY_STALE_MONTH_PROMPT: returning to a clean state instead of selecting a mismatched button.")
                reset, reset_replies = await send_choice(client, entity, latest, "0", "recover daily collector from stale monthly-report prompt")
                if not reset_replies:
                    print("RESET_AFTER_STALE_MONTH_PROMPT_FAILED")
                    return 16
                print("RECOVERY_COMPLETE: clean-state reset sent; refusing to treat a monthly result as a daily report.")
                return 0
            visible_labels = [label.strip().lower() for label in buttons(latest)]
            has_previous_day_button = any(
                label == "anterior" or "dia anterior" in label for label in visible_labels
            )
            prompt_mentions_day_choices = "dia anterior" in norm(latest.message).lower() or ("hoje" in norm(latest.message).lower() and "anterior" in norm(latest.message).lower())
            if has_previous_day_button:
                latest, replies2 = await send_choice(client, entity, latest, "Dia anterior", "request the previous day's sales")
                if not replies2:
                    return 7
                replies.extend(replies2)
                # If the bot shows a year/month selector rather than a report, complete the
                # explicit historical date flow instead of saving a current-day result as yesterday.
                prompt = norm(latest.message).lower()
                visible = [label.strip().lower() for label in buttons(latest)]
                if "ano atual" in prompt and "ano anterior" in prompt:
                    latest, year_replies = await send_choice(client, entity, latest, "Ano Anterior", "select previous year for historical daily report")
                    if not year_replies:
                        return 18
                    replies.extend(year_replies)
                    latest = await latest_incoming(client, entity) or latest
                    prompt = norm(latest.message).lower()
                    visible = [label.strip().lower() for label in buttons(latest)]
                if "selecione o mês" in prompt or ("mês" in prompt and visible):
                    candidates = [label for label in buttons(latest) if "outubro" in label.lower()]
                    if candidates:
                        latest, month_replies = await send_choice(client, entity, latest, candidates[0], "select October for historical daily report")
                        if not month_replies:
                            return 19
                        replies.extend(month_replies)
                        latest = await latest_incoming(client, entity) or latest
                        prompt = norm(latest.message).lower()
                        visible = [label.strip().lower() for label in buttons(latest)]
                # The bot's daily result arrives with an optional "Deseja visualizar os cartões?" prompt.
                # In this flow the actual report is the preceding reply, not that follow-up prompt.
                report_candidate = next((m for m in reversed(replies2) if matches_report(m.message, mode)), None)
                if report_candidate is None:
                    print(f"HISTORICAL_DAILY_SELECTION_INCOMPLETE prompt={safe_preview(latest.message)}; refusing to persist an incorrectly dated report.")
                    return 20
                latest = report_candidate
                latest_text = norm(latest.message).lower()
                explicit_dates = re.search(r"(?:data|referência|período)\\s*:?\\s*(\\d{4}-\\d{2}-\\d{2}|\\d{2}/\\d{2}/\\d{4})", latest.message, re.I)
                if explicit_dates:
                    raw_date = explicit_dates.group(1)
                    if "/" in raw_date:
                        dd, mm, yyyy = raw_date.split("/")
                        confirmed_date = f"{yyyy}-{mm}-{dd}"
                    else:
                        confirmed_date = raw_date
                    if confirmed_date != requested_period:
                        print(f"HISTORICAL_DAILY_DATE_MISMATCH expected={requested_period} found={confirmed_date}; not saving.")
                        return 21
            elif prompt_mentions_day_choices and not visible_labels:
                latest, replies2 = await send_choice(client, entity, latest, "Dia anterior", "request the previous day's sales from an explicit text prompt")
                if not replies2:
                    return 7
                replies.extend(replies2)
            elif prompt_mentions_day_choices and visible_labels:
                # Do not choose the wrong year/month buttons when the prompt and keyboard disagree.
                print("DAILY_MENU_MISMATCH: visible buttons do not include Dia anterior; resetting to branch selection.")
                reset, reset_replies = await send_choice(client, entity, latest, "0", "recover mismatched daily menu")
                if not reset_replies:
                    print("RESET_AFTER_DAILY_MISMATCH_FAILED")
                    return 16
                print("RECOVERY_COMPLETE: returned to a clean menu state; daily report not captured this run.")
                return 0

            report = await wait_for_report(client, entity, mode, after_id=menu_id)
            if not report:
                report = next((m for m in reversed(replies) if matches_report(m.message, mode)), None)
            if not report:
                latest_now = await latest_incoming(client, entity)
                if latest_now:
                    latest_text = norm(latest_now.message).lower()
                    if any(marker in latest_text for marker in ("venda do dia", "venda de hoje", "venda de ontem")) and ("total" in latest_text or "cupons" in latest_text):
                        print(f"DAILY_REPORT_NOT_FOUND; latest_prompt={safe_preview(latest_now.message)}; buttons={buttons(latest_now)!r}")
                return 8
            latest_text = norm(report.message).lower()
            if ("venda acumulada do mês" in latest_text
                    or not any(marker in latest_text for marker in ("venda total do dia", "venda do dia", "venda de ontem", "venda de hoje", "total de vendas nos pdvs"))):
                print("DAILY_MATCH_VALIDATION_FAILED: report body did not pass daily semantic validation; no database write attempted.")
                return 17
            output = save_report(report, report_type, requested_period)
            # Always answer "Sim" to the optional card-details prompt. Keep the raw card
            # detail in a linked review artifact rather than blindly adding it to daily sales,
            # since card subtotals can overlap with POS/TEF already counted in the daily total.
            persist_to_supabase(output, report_type, requested_period, local_now)
            card_report = await finish_card_prompt(client, entity)
            if card_report:
                card_file = save_report(
                    card_report, "sale_day_cards", requested_period,
                    raw_text=(card_report.message or "")
                )
                print(f"CARD_DETAILS_CAPTURED_FILE={card_file}")
            print(f"COLLECTION_COMPLETE report_type={report_type} requested_period={requested_period} cards_requested=true")
            return 0

        if ("ano atual" in combined and "ano anterior" in combined):
            latest, year_replies = await send_choice(client, entity, latest, "Ano Atual", "select current year for October 2026")
            if not year_replies:
                return 9
            replies.extend(year_replies)
            combined = (norm(latest.message) + " " + " ".join(buttons(latest))).lower()
        if "outubro" in combined and "selecione a opção desejada" in combined:
            latest, month_replies = await send_choice(client, entity, latest, "Outubro", "select October 2026")
            if not month_replies:
                return 10
            replies.extend(month_replies)
        report = await wait_for_report(client, entity, mode, after_id=menu_id)
        if not report:
            report = next((m for m in reversed(replies) if matches_report(m.message, mode)), None)
        if not report:
            latest_now = await latest_incoming(client, entity)
            if latest_now:
                print(f"MONTHLY_REPORT_NOT_FOUND; current_prompt={safe_preview(latest_now.message)}; buttons={buttons(latest_now)!r}")
            return 11
        output = save_report(report, report_type, requested_period)
        persist_to_supabase(output, report_type, requested_period, local_now)
        await finish_card_prompt(client, entity)
        print(f"COLLECTION_COMPLETE report_type={report_type} requested_period={requested_period}")
        return 0
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"COLLECTION_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
