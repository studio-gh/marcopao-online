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

def parse_report_data(report_type, raw):
    """Parse report-specific summary fields and preserve table-like lines without guessing."""
    raw = raw or ""
    text = norm(raw)
    low = text.lower()
    totals = {}
    warnings = []

    def find_money(pattern):
        match = re.search(pattern, raw, re.IGNORECASE | re.MULTILINE)
        if not match:
            return None
        try:
            return round(br_number(match.group(1)), 2)
        except ValueError:
            return None

    if report_type in {"sale_day", "monthly_sales"}:
        if report_type == "sale_day":
            if "venda acumulada do mês" in low or not any(x in low for x in ("venda do dia", "venda de hoje", "venda de ontem", "venda total do dia", "total de vendas nos pdvs")):
                raise ValueError("Resposta não corresponde ao relatório de venda do dia.")
        elif "venda acumulada do mês" not in low:
            raise ValueError("Resposta não corresponde ao relatório acumulado do mês.")

        patterns = {
            "dinheiro": r"Dinheiro\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
            "pix": r"PIX\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
            "pos_credito": r"POS Crédito\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
            "pos_debito": r"POS Débito\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
            "tef": r"TEF\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
            "ticket_pagamento": r"TICKET\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
            "ticket_medio": r"Ticket Médio\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
        }
        for key, pattern in patterns.items():
            value = find_money(pattern)
            if value is not None:
                totals[key] = value
        total = find_money(r"^(?:Venda Total do dia|Total)\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})")
        if total is None:
            total = find_money(r"^Total de vendas nos Pdvs\\s*\\n\\s*Total\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})")
        if total is not None:
            totals["total"] = total
        coupons = re.search(r"Quantidades?\\s+de\\s+Cupons\\s*:?\\s*([\\d.]+)", raw, re.IGNORECASE)
        if coupons:
            totals["cupons"] = int(coupons.group(1).replace(".", ""))
        for key, pattern in {
            "cupons_cancelados": r"Qtd de Cupons cancelados\\s*:?\\s*([\\d.]+)",
        }.items():
            match = re.search(pattern, raw, re.IGNORECASE)
            if match:
                totals[key] = int(match.group(1).replace(".", ""))
        for key, pattern in {
            "valor_vendas_nao_concluidas": r"Venda não Conclu[ií]da\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
            "valor_itens_cancelados": r"Vlr Total Item Canc\\.\\s*:?\\s*R?\\$?\\s*([\\d.]+,\\d{2})",
        }.items():
            value = find_money(pattern)
            if value is not None:
                totals[key] = value
        if "total" not in totals:
            raise ValueError("Total de vendas não foi identificado.")
        if "cupons" not in totals:
            warnings.append("Quantidade de cupons não foi identificada.")
        payment_keys = ["dinheiro", "pix", "pos_credito", "pos_debito", "tef", "ticket_pagamento"]
        missing = [key for key in payment_keys if key not in totals]
        totals["payment_methods_included"] = [key for key in payment_keys if key in totals]
        totals["card_sales_included"] = not any(k in missing for k in ("pos_credito", "pos_debito", "tef"))
        if missing:
            warnings.append("Meios de pagamento ausentes: " + ", ".join(missing) + ".")
        if not missing:
            payment_sum = round(sum(totals[k] for k in payment_keys), 2)
            totals["payment_methods_sum"] = payment_sum
            totals["payment_reconciliation_diff"] = round(payment_sum - totals["total"], 2)
            if abs(totals["payment_reconciliation_diff"]) > 0.02:
                warnings.append("Soma dos meios de pagamento difere do total informado.")
    else:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        entries = []
        for line in lines:
            if line.lower() in {"top 100 valor", "top 100 por valor", "top 100 quantidade", "top 100 por quantidade", "vendas por grupo", "total de vendas por grupo", "vendas de produto por grupo"}:
                continue
            money_match = re.search(r"^(.*?)\\s+R?\\$?\\s*([\\d.]+,\\d{2})\\s*$", line, re.IGNORECASE)
            qty_match = re.search(r"^(.*?)\\s+([\\d.]+)\\s*(kg|un(?:idade)?s?|pct|pacotes?)\\s*$", line, re.IGNORECASE)
            if money_match and not re.match(r"^(total|faturamento|valor total)\\b", line, re.IGNORECASE):
                entries.append({"name": money_match.group(1).strip(" .:-"), "value": round(br_number(money_match.group(2)), 2), "raw": line})
            elif qty_match:
                try:
                    entries.append({"name": qty_match.group(1).strip(" .:-"), "quantity": float(qty_match.group(2).replace(".", "").replace(",", ".")), "unit": qty_match.group(3).lower(), "raw": line})
                except ValueError:
                    pass
        totals["entries"] = entries
        totals["entry_count"] = len(entries)
        totals["raw_line_count"] = len(lines)
        if not entries:
            warnings.append("Formato tabular não reconhecido automaticamente; conteúdo bruto preservado para revisão.")
    totals["validation_warnings"] = warnings
    return totals, warnings

def parse_totals(raw):
    # Backwards-compatible entry point retained for current daily/monthly tests.
    return parse_report_data("sale_day", raw)

def persist_to_supabase(report_file, report_type, requested_period, queried_at):
    base_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base_url or not service_key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY secret; refusing to discard captured report.")
    report = json.loads(Path(report_file).read_text(encoding="utf-8"))
    raw = report["raw_text"]
    try:
        totals, warnings = parse_report_data(report_type, raw)
    except ValueError as exc:
        raise RuntimeError(f"Report validation failed; database write was not attempted: {exc}") from None
    branch = report.get("branch", BRANCH)
    period_start, period_end, report_date = report_period(report_type, queried_at, requested_period)

    notes = [
        f"Captured report type={report_type}; needs human review.",
        "Original Telegram text preserved in protected report_imports row.",
    ] + warnings
    card_details = report.get("card_details")
    if card_details and report_type == "sale_day":
        totals["card_details"] = card_details
        notes.append("Card breakdown captured separately; do not add twice without confirming overlap.")

    identity_hash = stable_report_identity(report_type, branch, period_start, period_end)
    row = {
        "report_type": report_type,
        "branch_id": None,
        "branch_name_raw": branch,
        "period_start": period_start,
        "period_end": period_end,
        "report_date": report_date,
        "source": "telegram_monitorbot",
        "raw_text": raw,
        "content_sha256": identity_hash,
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
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 201, 204):
                raise RuntimeError(f"Supabase returned HTTP {response.status}")
            print(f"SUPABASE_IMPORT_HTTP={response.status}")
            print(f"SUPABASE_IMPORT_IDENTITY_HASH={identity_hash}")
            print(f"SUPABASE_STATUS=needs_review")
    except urllib.error.HTTPError as exc:
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

REPORT_OPTIONS = {
    "1": "sale_day",
    "2": "monthly_sales",
    "3": "top_value",
    "4": "top_quantity",
    "10": "ticket_average",
    "12": "sales_by_group",
    "13": "products_by_group",
}
REPORT_MENU_OPTIONS = tuple(REPORT_OPTIONS.keys())
REPORT_MODES = {
    "daily_yesterday": "sale_day",
    "monthly_accumulated": "monthly_sales",
    "top_value": "top_value",
    "top_quantity": "top_quantity",
    "ticket_average": "ticket_average",
    "sales_by_group": "sales_by_group",
    "products_by_group": "products_by_group",
}
REPORT_TYPE_TO_OPTION = {value: key for key, value in REPORT_OPTIONS.items()}

def report_type_for_option(option):
    return REPORT_OPTIONS.get(str(option).strip())

def monthly_period(date_value):
    return date_value[:7]

def local_today_from_queried_at(queried_at, requested_month):
    stamp = queried_at
    if not isinstance(stamp, datetime):
        stamp = datetime.now(ZoneInfo("America/Sao_Paulo"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
    local = stamp.astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    if local[:7] == requested_month:
        return local
    next_month = (datetime.strptime(requested_month, "%Y-%m").replace(day=28) + timedelta(days=4)).replace(day=1)
    return (next_month.date() - timedelta(days=1)).isoformat()

def stable_report_identity(report_type, branch, period_start, period_end):
    identity = "|".join((str(report_type), norm(branch).upper(), str(period_start), str(period_end)))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()

def report_period(report_type, queried_at, requested_period):
    if report_type == "monthly_sales":
        start = requested_period + "-01"
        next_month = (datetime.strptime(requested_period, "%Y-%m").replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, (next_month.date() - timedelta(days=1)).isoformat(), None
    if report_type == "sale_day":
        return requested_period, requested_period, requested_period
    if re.fullmatch(r"\\d{4}-\\d{2}", requested_period):
        start = requested_period + "-01"
        end = local_today_from_queried_at(queried_at, requested_period)
        return start, end, end
    return requested_period, requested_period, requested_period

def matches_report_type(text, report_type):
    low = norm(text).lower()
    if not is_report_body(text):
        return False
    if report_type == "sale_day":
        return matches_report(text, "daily_yesterday")
    if report_type == "monthly_sales":
        return "venda acumulada do mês" in low and ("total de vendas nos pdvs" in low or "venda total do dia" in low)
    marker_map = {
        "top_value": ("top 100 valor", "top 100 por valor", "maiores vendas por valor", "valor total"),
        "top_quantity": ("top 100 quantidade", "top 100 por quantidade", "maiores vendas por quantidade"),
        "ticket_average": ("ticket médio preço vendas por quantidade", "ticket médio preço", "ticket médio por produto"),
        "sales_by_group": ("total vendas por grupo", "total de vendas por grupo", "vendas por grupo"),
        "products_by_group": ("vendas de produto por grupo", "venda de produto por grupo", "produtos por grupo"),
    }
    markers = marker_map.get(report_type, ())
    return any(marker in low for marker in markers)

def report_target_period(report_type, now):
    if report_type == "sale_day":
        return (now.date() - timedelta(days=1)).isoformat()
    if report_type == "monthly_sales":
        return now.strftime("%Y-%m")
    return now.strftime("%Y-%m")


def is_main_menu(text):
    low = norm(text).lower()
    return "venda do dia" in low and "venda acumulada mês" in low and "top 100" in low

def daily_report_date_mismatch(text, mode):
    """Detect the known dangerous case: 'today' sales returned when yesterday was requested."""
    return mode == "daily_yesterday" and "venda de hoje" in norm(text).lower()


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
    # Card prompt may already have been answered in a previous run, leaving the bot at its
    # menu or at the report body. Do not send report 1 again just to "finish" the flow.
    return None

async def collect_one_report(client, entity, report_type, requested_period, local_now):
    option = REPORT_TYPE_TO_OPTION[report_type]
    current = await latest_incoming(client, entity)
    current = await recover_menu(client, entity, current)
    if current is None or not is_main_menu(current.message):
        raise RuntimeError("Report menu could not be confirmed before selecting option " + option)
    menu_id = current.id
    latest, replies = await send_choice(client, entity, current, option, f"select report type {report_type}")
    if not replies:
        raise RuntimeError("Monitorbot did not answer option " + option)
    latest = await ensure_bellabyte_login(client, entity, latest)
    latest = await latest_incoming(client, entity) or latest
    combined = (norm(latest.message) + " " + " ".join(buttons(latest))).lower()
    if report_type == "sale_day":
        # Handle the daily report's explicit previous-day flow; never save today's report as yesterday.
        visible = [label.lower() for label in buttons(latest)]
        if any("dia anterior" in label or label == "anterior" for label in visible) or ("hoje" in norm(latest.message).lower() and "anterior" in norm(latest.message).lower()):
            latest, replies2 = await send_choice(client, entity, latest, "Dia anterior", "select previous day for daily sales")
            if not replies2:
                raise RuntimeError("Monitorbot did not return the previous-day report")
            replies.extend(replies2)
            latest = await latest_incoming(client, entity) or latest
    elif report_type == "monthly_sales":
        if "ano atual" in combined and "ano anterior" in combined:
            latest, more = await send_choice(client, entity, latest, "Ano Atual", "select current year for monthly accumulated sales")
            if not more:
                raise RuntimeError("Monitorbot did not return the current-year menu")
            replies.extend(more)
            latest = await latest_incoming(client, entity) or latest
        month_name = datetime.strptime(requested_period, "%Y-%m").strftime("%B").lower()
        month_pt = {"january":"janeiro","february":"fevereiro","march":"março","april":"abril","may":"maio","june":"junho","july":"julho","august":"agosto","september":"setembro","october":"outubro","november":"novembro","december":"dezembro"}[month_name]
        if "selecione a opção desejada" in combined or ("selecione o mês" in combined):
            candidates = [label for label in buttons(latest) if month_pt in label.lower()]
            if candidates:
                latest, more = await send_choice(client, entity, latest, candidates[0], "select current month for monthly accumulated sales")
                if not more:
                    raise RuntimeError("Monitorbot did not return monthly accumulated sales")
                replies.extend(more)
                latest = await latest_incoming(client, entity) or latest
    else:
        if not any(matches_report_type(message.message, report_type) for message in replies):
            # The intermediate menu may expose the submenu choice after the report option.
            available = [label for label in buttons(latest) if label.isdigit()]
            if available and "selecione" in norm(latest.message).lower():
                latest, more = await send_choice(client, entity, latest, option, "confirm report option " + option)
                if more:
                    replies.extend(more)
                    latest = await latest_incoming(client, entity) or latest

    report = next((m for m in reversed(replies) if matches_report_type(m.message, report_type)), None)
    if report is None:
        report = await wait_for_report(client, entity, report_type, after_id=menu_id, timeout=35)
    if report is None:
        latest_now = await latest_incoming(client, entity)
        raise RuntimeError(f"Report {option} ({report_type}) not found; latest response={safe_preview(latest_now.message) if latest_now else 'none'}; buttons={buttons(latest_now) if latest_now else []}")

    # Date-specific safeguards for daily and monthly reports.
    low = norm(report.message).lower()
    if report_type == "sale_day" and daily_report_date_mismatch(report.message, "daily_yesterday"):
        raise RuntimeError("Daily report title says today while the requested period is yesterday.")
    if report_type == "monthly_sales" and "venda acumulada do mês" not in low:
        raise RuntimeError("Monthly report semantic validation failed.")

    output = save_report(report, report_type, requested_period)
    card_report = None
    if report_type == "sale_day":
        card_report = await finish_card_prompt(client, entity)
    if card_report:
        card_details = {
            "message_id": card_report.id,
            "message_date": card_report.date.astimezone(timezone.utc).isoformat() if card_report.date else None,
            "raw_text": card_report.message or "",
            "content_sha256": hashlib.sha256((card_report.message or "").encode("utf-8")).hexdigest(),
            "status": "needs_review",
        }
        card_totals, card_warnings = parse_report_data("sale_day", card_details["raw_text"])
        card_details["parsed_totals"] = card_totals
        card_details["validation_warnings"] = card_warnings
        card_details["note"] = "Card breakdown captured separately; do not add it again without confirming overlap."
        report_data = json.loads(Path(output).read_text(encoding="utf-8"))
        report_data["card_details"] = card_details
        Path(output).write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    persist_to_supabase(output, report_type, requested_period, local_now)
    print(f"COLLECTION_COMPLETE report_type={report_type} requested_period={requested_period} option={option}")
    return output

async def main():
    mode = os.environ.get("REPORT_MODE", "daily_yesterday").strip()
    if mode == "all_reports":
        modes = list(REPORT_OPTIONS.values())
    else:
        allowed = set(REPORT_MODES)
        if mode not in allowed:
            print("INVALID_REPORT_MODE")
            return 2
        modes = [REPORT_MODES[mode]]
    local_now = datetime.now(ZoneInfo("America/Sao_Paulo"))
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
    succeeded, failed = [], []
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("SESSION_NOT_AUTHORIZED")
            return 4
        entity = await client.get_entity(BOT)
        for report_type in modes:
            try:
                # Recover to a known menu between reports; one Telegram chat is shared, so collect serially.
                now_msg = await latest_incoming(client, entity)
                if now_msg and ("precisa se logar" in norm(now_msg.message).lower() or "precisa fazer login" in norm(now_msg.message).lower()):
                    await ensure_bellabyte_login(client, entity, now_msg)
                requested_period = report_target_period(report_type, local_now)
                await collect_one_report(client, entity, report_type, requested_period, local_now)
                succeeded.append(report_type)
            except Exception as exc:
                failed.append({"report_type": report_type, "error": str(exc)})
                print(f"COLLECTION_FAILED report_type={report_type}: {type(exc).__name__}: {exc}")
                # A failed navigation can leave the chat in an unknown state; attempt reset before next type.
                try:
                    latest_msg = await latest_incoming(client, entity)
                    if latest_msg:
                        await send_choice(client, entity, latest_msg, "0", "reset menu after failed report")
                except Exception as reset_exc:
                    print(f"MENU_RESET_FAILED after {report_type}: {type(reset_exc).__name__}")
                    break
        print("COLLECTION_SUMMARY=" + json.dumps({"succeeded": succeeded, "failed": failed}, ensure_ascii=False))
        return 0 if not failed else 1
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"COLLECTION_FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
