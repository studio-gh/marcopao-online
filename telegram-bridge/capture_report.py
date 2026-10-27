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
        or "top 100 valor" in low
        or "top 100 por valor" in low
        or "top 100 quantidade" in low
        or "top 100 por quantidade" in low
        or "ticket médio preço" in low
        or "total vendas por grupo" in low
        or "total de vendas por grupo" in low
        or "vendas de produto por grupo" in low
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
    """Parse report-specific fields. Keep product/group reports separate from financial totals."""
    raw = raw or ""
    text = norm(raw)
    low = text.lower()
    totals = {}
    warnings = []

    def money(pattern):
        m = re.search(pattern, raw, re.IGNORECASE | re.MULTILINE)
        if not m:
            return None
        try:
            return round(br_number(m.group(1)), 2)
        except ValueError:
            return None

    def integer(pattern):
        m = re.search(pattern, raw, re.IGNORECASE)
        return int(m.group(1).replace(".", "")) if m else None

    if report_type in {"sale_day", "monthly_sales"}:
        if report_type == "sale_day":
            if "venda acumulada do mês" in low or not any(x in low for x in ("venda do dia", "venda de hoje", "venda de ontem", "venda total do dia", "total de vendas nos pdvs")):
                raise ValueError("Resposta não corresponde à venda diária.")
        elif "venda acumulada do mês" not in low:
            raise ValueError("Resposta não corresponde à venda acumulada mensal.")

        patterns = {
            "dinheiro": r"Dinheiro\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
            "pix": r"PIX\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
            "pos_credito": r"POS Crédito\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
            "pos_debito": r"POS Débito\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
            "tef": r"TEF\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
            "ticket_pagamento": r"TICKET\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
            "ticket_medio": r"Ticket Médio\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        }
        for key, pattern in patterns.items():
            value = money(pattern)
            if value is not None:
                totals[key] = value

        # Telegram sometimes joins the report heading and all metrics into one line.
        # Match the explicit "Total R$" label anywhere, but never the phrase "Total de vendas".
        total = money(r"(?<![\w])(?:Venda Total do dia|Total)\s*:?\s*R?\$?\s*([\d.]+,\d{2})")
        if total is None:
            total = money(r"Total de vendas nos Pdvs\s*\n\s*Total\s*:?\s*R?\$?\s*([\d.]+,\d{2})")
        if total is not None:
            totals["total"] = total
        coupons = integer(r"Quantidades?\s+de\s+Cupons\s*:?\s*([\d.]+)")
        if coupons is not None:
            totals["cupons"] = coupons
        cancelled_coupons = integer(r"Qtd de Cupons cancelados\s*:?\s*([\d.]+)")
        if cancelled_coupons is not None:
            totals["cupons_cancelados"] = cancelled_coupons
        for key, pattern in {
            "valor_vendas_nao_concluidas": r"Venda não Conclu[ií]da\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
            "valor_itens_cancelados": r"Vlr Total Item Canc\.\s*:?\s*R?\$?\s*([\d.]+,\d{2})",
        }.items():
            value = money(pattern)
            if value is not None:
                totals[key] = value

        if "total" not in totals:
            raise ValueError("Total de vendas não foi identificado.")
        if "cupons" not in totals:
            warnings.append("Quantidade de cupons não foi identificada.")
        payment_keys = ["dinheiro", "pix", "pos_credito", "pos_debito", "tef", "ticket_pagamento"]
        missing = [key for key in payment_keys if key not in totals]
        missing_cards = [key for key in ("pos_credito", "pos_debito", "tef") if key not in totals]
        totals["payment_methods_included"] = [key for key in payment_keys if key in totals]
        totals["card_sales_included"] = not missing_cards
        if missing_cards:
            warnings.append("Detalhes de cartão incompletos: faltando " + ", ".join(missing_cards) + ".")
        other_missing = [key for key in missing if key not in {"pos_credito", "pos_debito", "tef"}]
        if other_missing:
            warnings.append("Meios de pagamento ausentes: " + ", ".join(other_missing) + ".")
        else:
            payment_sum = round(sum(totals[k] for k in payment_keys), 2)
            totals["payment_methods_sum"] = payment_sum
            totals["payment_reconciliation_diff"] = round(payment_sum - totals["total"], 2)
            if abs(totals["payment_reconciliation_diff"]) > 0.02:
                warnings.append("Soma dos meios de pagamento difere do total informado.")
    elif report_type == "ticket_average":
        # Report 10 is a two-line-per-product listing: product name, then price and quantity.
        # [collect-report:ticket_average] Refresh report 10 after its parser correction.
        # Retry after recognizing the full numbered report keyboard.
        # Some products have multiple price/quantity rows followed by P.M (average) and Q.T (total quantity).
        entries = []
        blocks = re.split(r"\n\s*\n+", raw.strip())
        for block_text in blocks:
            block_lines = [line.strip() for line in block_text.splitlines() if line.strip()]
            if len(block_lines) < 2:
                continue
            name = block_lines[0].strip(" .:-")
            metrics_text = "\n".join(block_lines[1:])
            if not re.search(r"R\$\s*:|Qtd\s*:|P\.?M\.?\s*:|Q\.?T\.?\s*:", metrics_text, re.IGNORECASE):
                continue
            if re.search(r"^(ticket|preço|preco|filial|total|vendas por grupo)\b", name, re.IGNORECASE):
                continue

            def decimal_number(token):
                return float(token.replace(".", "").replace(",", "."))

            total_quantity_match = re.search(
                r"Q\.?\s*T\.?\s*:\s*([\d.]+(?:,\d+)?)\s*(kg|un(?:idade)?s?)?",
                metrics_text, re.IGNORECASE
            )
            quantity_matches = re.findall(
                r"Qtd\s*:\s*([\d.]+(?:,\d+)?)\s*(kg|un(?:idade)?s?)?",
                metrics_text, re.IGNORECASE
            )
            unit = None
            quantity = None
            if total_quantity_match:
                quantity = decimal_number(total_quantity_match.group(1))
                unit = total_quantity_match.group(2)
            elif quantity_matches:
                quantity = sum(decimal_number(qty) for qty, _ in quantity_matches)
                unit = next((u for _, u in quantity_matches if u), None)
            if unit and unit.lower().startswith("un"):
                unit = "un"
            elif unit:
                unit = unit.lower()

            average_match = re.search(
                r"P\.?\s*M\.?\s*:\s*R\$\s*:?\s*([\d.]+,\d{2})",
                metrics_text, re.IGNORECASE
            )
            price_matches = re.findall(r"R\$\s*:?\s*([\d.]+,\d{2})", metrics_text, re.IGNORECASE)
            average = round(br_number(average_match.group(1)), 2) if average_match else (
                round(br_number(price_matches[0]), 2) if len(price_matches) == 1 else None
            )
            entry = {"rank": len(entries) + 1, "name": name, "raw": block_text}
            if quantity is not None:
                entry["quantity"] = quantity
            if unit:
                entry["unit"] = unit
            if average is not None:
                entry["average"] = average
                entry["unit_price"] = average
            entries.append(entry)

        totals["entries"] = entries
        totals["entry_count"] = len(entries)
        totals["raw_line_count"] = len(raw.splitlines())
        if not entries:
            warnings.append("Formato do relatório de preço/quantidade não reconhecido; conteúdo bruto preservado para revisão.")
    elif report_type == "ticket_average":
        # Report 10 alternates a product name with "R$:6,90, Qtd:12UN".
        # Pair adjacent lines so the quantity/price line is not mistaken for a product.
        lines = [line.strip() for line in raw.replace("\\r", "\\n").splitlines() if line.strip()]
        entries = []
        pending_name = None
        price_qty_pattern = re.compile(
            r"R\$\s*:?\s*([\d.]+,\d{2})\s*,?\s*Qtd\s*:?\s*([\d.]+(?:,\d+)?)\s*(kg|un|unidade|unidades|pct)?",
            re.IGNORECASE
        )
        for line in lines:
            if report_title_match(line, report_type) or "filial:" in line.lower() or line.lower().startswith("ticket preço") or line.lower().startswith("ticket medio"):
                continue
            match = price_qty_pattern.search(line)
            if match:
                price_value = round(br_number(match.group(1)), 2)
                quantity_value = float(match.group(2).replace(".", "").replace(",", "."))
                unit = (match.group(3) or "").lower()
                if unit.startswith("un"):
                    unit = "un"
                if pending_name:
                    entries.append({
                        "rank": len(entries) + 1,
                        "name": pending_name,
                        "raw": pending_name + "\\n" + line,
                        "unit_price": price_value,
                        "quantity": quantity_value,
                        "unit": unit or None,
                    })
                pending_name = None
            elif not re.match(r"^R\\$|^Qtd\\b", line, re.IGNORECASE):
                pending_name = line
        totals["entries"] = entries
        totals["entry_count"] = len(entries)
        totals["raw_line_count"] = len(lines)
        if not entries:
            warnings.append("Formato do relatório 10 não reconhecido; conteúdo bruto preservado para revisão.")
    else:
        # Bellabyte often sends 30 ranked rows per Telegram message on a single line.
        # Split on every rank marker, even when a message chunk was joined with literal spaces.
        expanded_raw = re.sub(r"(?<!^)(?:\s|\\n|\\r)+(?=\d{1,3}\s*:\s*\S)", "\n", raw)
        expanded_raw = expanded_raw.replace("\\n", "\n").replace("\\r", "\n")
        lines = [line.strip() for line in expanded_raw.splitlines() if line.strip()]
        entries = []
        # Bellabyte ranks are block-structured: "1: PRODUCT", then Total/Quantidade/Preço lines.
        blocks = []
        current = None
        for line in lines:
            header = re.match(r"^\s*(\d+)\s*:\s*(.+?)\s*$", line)
            if header:
                if current:
                    blocks.append(current)
                current = {"rank": int(header.group(1)), "name": header.group(2).strip(), "raw_lines": [line]}
            elif current:
                current["raw_lines"].append(line)
        if current:
            blocks.append(current)

        def parse_quantity(block_text):
            match = re.search(r"Quantidade\s*:\s*([\d.]+(?:,\d+)?)(?:\s*(kg|un(?:idade)?s?|pct|pacotes?))?\b", block_text, re.IGNORECASE)
            if not match:
                return None, None
            try:
                quantity = float(match.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                return None, None
            unit = (match.group(2) or "").lower()
            if unit.startswith("un"):
                unit = "un"
            return quantity, (unit or None)

        def parse_price(block_text, patterns):
            for pattern in patterns:
                match = re.search(pattern, block_text, re.IGNORECASE)
                if match:
                    try:
                        return round(br_number(match.group(1)), 2)
                    except ValueError:
                        continue
            return None

        for block in blocks:
            block_text = "\n".join(block["raw_lines"])
            # The heading line can contain the full row, not just the product/group name.
            clean_name = re.split(
                r"\s+(?=(?:Total\s*R\$|Faturamento\s*:|Valor Total\s*:|Quantidade\s*:|Preço\s*(?:Atual|M[eé]dio|Medio)\s*:))",
                block["name"], maxsplit=1, flags=re.IGNORECASE
            )[0].strip(" .:-")
            entry = {"rank": block["rank"], "name": clean_name or block["name"], "raw": block_text}
            quantity, unit = parse_quantity(block_text)
            if quantity is not None:
                entry["quantity"] = quantity
                entry["unit"] = unit
            revenue = parse_price(block_text, [
                r"Total\s*:?\s*R\$\s*:?\s*([\d.]+,\d{2})",
                r"Faturamento\s*:?\s*R\$\s*:?\s*([\d.]+,\d{2})",
                r"Valor Total\s*:?\s*R\$\s*:?\s*([\d.]+,\d{2})",
            ])
            if revenue is not None:
                entry["value"] = revenue
            unit_price = parse_price(block_text, [
                r"Preço\s*M[eé]dio(?:\s+Venda)?\s*:?\s*R\$\s*:?\s*([\d.]+,\d{2})",
                r"Preço\s*Atual\s*:?\s*R\$\s*:?\s*([\d.]+,\d{2})",
                r"Preço\s*:?\s*R\$\s*:?\s*([\d.]+,\d{2})",
            ])
            if unit_price is not None:
                entry["unit_price"] = unit_price
                if report_type == "sales_by_group":
                    entry["average"] = unit_price
            stock_match = re.search(r"Estoque\s*:?\s*(-?[\d.]+(?:,\d+)?)(?:\s*(kg|un(?:idade)?s?))?", block_text, re.IGNORECASE)
            if stock_match:
                try:
                    entry["stock"] = float(stock_match.group(1).replace(".", "").replace(",", "."))
                except ValueError:
                    pass
                stock_unit = (stock_match.group(2) or "").lower()
                if stock_unit.startswith("un"):
                    entry["stock_unit"] = "un"
                elif stock_unit:
                    entry["stock_unit"] = stock_unit
            cost_match = re.search(r"Custo\s*:?\s*R\$\s*:?\s*([\d.]+,\d{2})", block_text, re.IGNORECASE)
            if cost_match:
                try:
                    entry["cost"] = round(br_number(cost_match.group(1)), 2)
                except ValueError:
                    pass
            entries.append(entry)

        # Fallback for report variants where a product row and its metrics share one line.
        if not entries:
            for line in lines:
                money_match = re.search(r"R\$\s*:?\s*([\d.]+,\d{2})", line, re.IGNORECASE)
                qty_match = re.search(r"([\d.]+(?:,\d+)?)\s*(kg|un(?:idade)?s?|pct|pacotes?)\b", line, re.IGNORECASE)
                if not money_match and not qty_match:
                    continue
                name = line
                if money_match:
                    name = re.sub(r"R\$\s*:?\s*[\d.]+,\d{2}", "", name, count=1, flags=re.IGNORECASE)
                if qty_match:
                    name = re.sub(r"[\d.]+(?:,\d+)?\s*(?:kg|un(?:idade)?s?|pct|pacotes?)\b", "", name, count=1, flags=re.IGNORECASE)
                name = re.sub(r"^\s*\d+\s*:\s*", "", name)
                name = re.sub(r"\s*[-:·]+\s*", " ", name).strip(" .:-")
                if not name or re.match(r"^(total|faturamento|valor total|preço|preco)\b", name, re.IGNORECASE):
                    continue
                entry = {"name": name, "raw": line}
                if money_match:
                    entry["value"] = round(br_number(money_match.group(1)), 2)
                if qty_match:
                    entry["quantity"] = float(qty_match.group(1).replace(".", "").replace(",", "."))
                    unit = qty_match.group(2).lower()
                    entry["unit"] = "un" if unit.startswith("un") else unit
                entries.append(entry)

        totals["entries"] = entries
        totals["entry_count"] = len(entries)
        totals["raw_line_count"] = len(lines)
        if not entries:
            warnings.append("Formato tabular não reconhecido automaticamente; conteúdo bruto preservado para revisão.")
        elif report_type in {"top_value", "top_quantity"} and len(entries) < 80:
            warnings.append(f"Ranking incompleto: {len(entries)} linhas reconhecidas, esperado até 100. Conferir captura e quebras de mensagem.")
        elif report_type == "sales_by_group" and len(entries) < 2:
            warnings.append("Poucos grupos reconhecidos; conferir separação das linhas do relatório.")
    totals["validation_warnings"] = warnings
    return totals, warnings

def parse_totals(raw):
    """Backward-compatible daily-summary parser that reports missing fields without raising."""
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
    totals, warnings = {}, []
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
    missing_methods = [key for key in payment_keys if key not in totals]
    totals["payment_methods_included"] = [key for key in payment_keys if key in totals]
    totals["card_sales_included"] = not missing_cards
    if missing_cards:
        warnings.append("Detalhes de cartão incompletos: faltando " + ", ".join(missing_cards) + ".")
    if "total" in totals and not missing_methods:
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
        "imported_at": report.get("captured_at") or datetime.now(timezone.utc).isoformat(),
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
# [collect-report] Re-run serialized collection after ticket-average and report-13 navigation fixes.
# [collect-report:ticket_average] Retry only the ticket-average report after chunk-reassembly fix.
# [collect-report:ticket_average] Retry after relaxing ticket-average row matching.
# Trigger again after workflow condition was widened to accept typed report selectors.
# [collect-report] Run the serialized live collector after parser regression tests pass.

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
    if re.fullmatch(r"\d{4}-\d{2}", requested_period):
        start = requested_period + "-01"
        end = local_today_from_queried_at(queried_at, requested_period)
        return start, end, end
    return requested_period, requested_period, requested_period

def report_title_match(text, report_type):
    low = norm(text).lower()
    marker_map = {
        "top_value": ("top 100 valor", "top 100 por valor", "maiores vendas por valor"),
        "top_quantity": ("top 100 quantidade", "top 100 por quantidade", "maiores vendas por quantidade"),
        "ticket_average": ("ticket médio preço vendas por quantidade", "ticket medio preço vendas por quantidade", "ticket médio preço", "ticket medio preço"),
        "sales_by_group": ("total vendas por grupo", "total de vendas por grupo", "vendas por grupo"),
        "products_by_group": ("vendas de produto por grupo", "venda de produto por grupo", "vendas produtos por grupo", "vendas produto por grupo", "produtos por grupo"),
    }
    if report_type == "sale_day":
        return any(marker in low for marker in ("venda total do dia", "total de vendas nos pdvs", "venda do dia", "venda de ontem"))
    if report_type == "monthly_sales":
        return "venda acumulada do mês" in low
    return any(marker in low for marker in marker_map.get(report_type, ()))

def matches_report_type(text, report_type):
    low = norm(text).lower()
    if report_type == "sale_day":
        return matches_report(text, "daily_yesterday")
    if report_type == "monthly_sales":
        return "venda acumulada do mês" in low and ("total de vendas nos pdvs" in low or "venda total do dia" in low)
    if not is_report_body(text):
        return False
    return report_title_match(text, report_type)

def report_message_has_data(text):
    return bool(re.search(
        r"R\$\s*:?\s*[\d.]+,\d{2}|Quantidade\s*:|Preço\s*:|Preço\s+(?:Atual|M[eé]dio)|^\s*\d+\s*:\s+\S",
        text or "", re.IGNORECASE | re.MULTILINE
    ))

def find_report_payload(replies, report_type):
    """Join all Telegram message chunks belonging to one requested report."""
    start_index = None
    for index, message in enumerate(replies):
        text = message.message or ""
        labels = [label.lower() for label in buttons(message)]
        # These are selection prompts, not report content. Advance through them before searching.
        # Group selectors can share the report title; only skip a pure selector prompt.
        if report_type in {"ticket_average", "products_by_group"} and "todos" in labels and not report_message_has_data(text) and not report_title_match(text, report_type):
            continue
        if report_title_match(text, report_type) and (report_message_has_data(text) or report_type in {"sales_by_group", "products_by_group"}):
            start_index = index
            break
        if matches_report_type(text, report_type):
            start_index = index
            break
    if start_index is None and report_type == "ticket_average":
        # Bellabyte often emits the report title on the group-selection prompt, then sends
        # the actual rows without repeating the title. Find the first new price-bearing row.
        for index, message in enumerate(replies):
            text = message.message or ""
            low = norm(text).lower()
            if report_message_has_data(text) and re.search(r"preço|preco|quantidade", low, re.IGNORECASE):
                start_index = index
                break
    if start_index is None:
        return None, None

    fragments = []
    first_message = replies[start_index]
    for message in replies[start_index:]:
        raw_text = (message.message or "").strip()
        normalized = norm(raw_text)
        low = normalized.lower()
        if not normalized:
            continue
        if any(prompt in low for prompt in (
            "deseja visualizar os cartões", "deseja visualizar os cartoes",
            "você precisa se logar", "voce precisa se logar",
            "qual filial deseja se conectar", "digite qual filial deseja se conectar",
        )):
            break
        if report_title_match(normalized, report_type) or report_message_has_data(raw_text) or matches_report_type(normalized, report_type):
            # Preserve each message's internal line breaks; the block parser depends on them.
            fragments.append(raw_text)
            continue
        if fragments:
            break
    if not fragments:
        return None, None
    return first_message, "\n\n".join(fragments)

def report_target_period(report_type, now):
    if report_type == "sale_day":
        return (now.date() - timedelta(days=1)).isoformat()
    if report_type == "monthly_sales":
        return now.strftime("%Y-%m")
    return now.strftime("%Y-%m")


def is_main_menu(text):
    low = norm(text).lower()
    return "venda do dia" in low and "venda acumulada mês" in low and "top 100" in low


# [collect-report:ticket_average] Retry ticket average after menu recovery fix.
def is_report_menu_message(message):
    """Recognize the report keyboard even when Bellabyte repeats its branch welcome text."""
    text = message.message or ""
    if is_main_menu(text):
        return True
    labels = {label.strip().lower() for label in buttons(message)}
    required = {"1", "2", "3", "4", "10", "12", "13"}
    return required.issubset(labels) and "relatório bellabyte" in norm(text).lower()

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
    candidates = [m for m in items if not m.out and matches_report_type(m.message, mode)]
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
    """Reset safely, complete Bellabyte login and confirm branch/menu before selecting reports."""
    if current is None:
        current = await latest_incoming(client, entity)
    if current is None:
        raise RuntimeError("No incoming Monitorbot messages are available.")

    current = await ensure_bellabyte_login(client, entity, current)
    current = await latest_incoming(client, entity) or current
    text = norm(current.message)
    low = text.lower()
    if is_report_menu_message(current):
        return current

    branch_prompt = "qual filial deseja se conectar" in low or "digite qual filial" in low
    if not branch_prompt:
        reset, replies = await send_choice(client, entity, current, "0", "reset conversation to branch/menu selection")
        if not replies:
            raise RuntimeError("No response after the documented reset option 0.")
        current = await latest_incoming(client, entity) or replies[-1]
        current = await ensure_bellabyte_login(client, entity, current)
        current = await latest_incoming(client, entity) or current
        text = norm(current.message)
        low = text.lower()
        branch_prompt = "qual filial deseja se conectar" in low or "digite qual filial" in low

    if branch_prompt:
        branch_reply, replies = await send_choice(client, entity, current, "1", "select the configured Marcopao branch")
        if not replies:
            raise RuntimeError("No response after branch selection 1.")
        current = await latest_incoming(client, entity) or replies[-1]
        current = await ensure_bellabyte_login(client, entity, current)
        current = await latest_incoming(client, entity) or current
        text = norm(current.message)

    if not is_report_menu_message(current):
        history = await client.get_messages(entity, limit=40)
        menus = [m for m in history if not m.out and is_report_menu_message(m)]
        # A stale menu from before login/reset must not silently be used.
        current_id = getattr(current, "id", 0) or 0
        menus = [m for m in menus if m.id >= current_id]
        if menus:
            current = max(menus, key=lambda m: m.id)
            text = norm(current.message)
        else:
            raise RuntimeError("Main report menu not confirmed after login, reset and branch selection.")
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

async def resolve_period_selectors(client, entity, latest, replies, requested_period, report_type):
    """Resolve year/month filters for the requested report period."""
    month_names = {
        1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
        5: "maio", 6: "junho", 7: "julho", 8: "agosto",
        9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
    }
    target_date = requested_period[:7] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested_period) else requested_period
    if not re.fullmatch(r"\d{4}-\d{2}", target_date):
        return latest, replies
    target_month = month_names[int(target_date[5:7])]
    target_year = int(target_date[:4])
    current_year = datetime.now(ZoneInfo("America/Sao_Paulo")).year

    for _ in range(4):
        prompt = norm(latest.message).lower()
        labels = [label.strip() for label in buttons(latest)]
        labels_low = [label.lower() for label in labels]
        year_options = [label for label in labels if "ano atual" in label.lower() or "ano anterior" in label.lower()]
        if year_options or ("ano atual" in prompt and "ano anterior" in prompt):
            desired = "Ano Atual" if target_year == current_year else "Ano Anterior"
            choice = next((label for label in labels if desired.lower() in label.lower()), desired)
            latest, more = await send_choice(client, entity, latest, choice, f"select target year for {report_type}")
            if not more:
                raise RuntimeError(f"Monitorbot did not respond to {desired} selection for {report_type}.")
            replies.extend(more)
            latest = await latest_incoming(client, entity) or more[-1]
            continue

        day_options = [label for label in labels if "dia anterior" in label.lower() or label.lower() == "anterior"]
        if report_type == "sale_day" and day_options:
            latest, more = await send_choice(client, entity, latest, day_options[0], "select previous day for daily sales")
            if not more:
                raise RuntimeError("Monitorbot did not return the previous-day report.")
            replies.extend(more)
            latest = await latest_incoming(client, entity) or more[-1]
            if any(matches_report_type(m.message or "", "sale_day") for m in more):
                break
            continue

        month_options = [label for label in labels if target_month in label.lower()]
        month_prompt = any(term in prompt for term in ("selecione o mês", "selecione a opção desejada", "selecione o periodo", "selecione o período", "mês desejado", "escolha o mês"))
        has_group_scope = "todos" in labels_low and report_type in {"ticket_average", "products_by_group"}
        if month_options and not has_group_scope and (month_prompt or labels):
            latest, more = await send_choice(client, entity, latest, month_options[0], f"select {target_month} for {report_type}")
            if not more:
                raise RuntimeError(f"Monitorbot did not respond to {target_month} selection for {report_type}.")
            replies.extend(more)
            latest = await latest_incoming(client, entity) or more[-1]
            continue
        break
    return latest, replies


async def resolve_group_scope(client, entity, latest, replies, report_type):
    """Select Todos in report 10/13 group-scoped menus so the collector gets a complete list."""
    if report_type not in {"ticket_average", "products_by_group"}:
        return latest, replies
    labels = buttons(latest)
    everyone = next((label for label in labels if label.strip().lower() == "todos"), None)
    if not everyone:
        return latest, replies
    if report_message_has_data(latest.message or ""):
        return latest, replies
    latest, more = await send_choice(client, entity, latest, everyone, f"select all groups for {report_type}")
    if not more:
        raise RuntimeError(f"Monitorbot did not return the Todos report for {report_type}.")
    replies.extend(more)
    latest = await latest_incoming(client, entity) or more[-1]
    return latest, replies


async def resolve_daily_selection(client, entity, latest, replies, requested_period, local_now):
    """Handle year selection and record the message id of the explicit previous-day choice."""
    target_year = int(requested_period[:4])
    current_year = local_now.year
    previous_day_confirmed = False
    selection_after_id = None
    for _ in range(5):
        text = norm(latest.message).lower()
        labels = [label.strip() for label in buttons(latest)]
        labels_low = [label.lower() for label in labels]
        if any("ano atual" in x for x in labels_low) or ("ano atual" in text and "ano anterior" in text):
            desired = "Ano Atual" if target_year == current_year else "Ano Anterior"
            choice = next((label for label in labels if desired.lower() in label.lower()), desired)
            latest, more = await send_choice(client, entity, latest, choice, f"select target year {target_year} for daily sales")
            if not more:
                raise RuntimeError("Monitorbot did not return the target year menu for daily sales.")
            replies.extend(more)
            latest = await latest_incoming(client, entity) or more[-1]
            continue

        previous_day = next((label for label in labels if "dia anterior" in label.lower() or label.lower() == "anterior"), None)
        if previous_day:
            selection_after_id = getattr(latest, "id", None)
            latest, more = await send_choice(client, entity, latest, previous_day, "select previous day for daily sales")
            if not more:
                raise RuntimeError("Monitorbot did not return the previous-day report.")
            replies.extend(more)
            previous_day_confirmed = True
            latest = await latest_incoming(client, entity) or more[-1]
            if any(matches_report_type(m.message or "", "sale_day") for m in more):
                break
            continue

        month_names = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
        target_month = month_names[int(requested_period[5:7]) - 1]
        month_option = next((label for label in labels if target_month in label.lower()), None)
        if month_option and previous_day_confirmed and any(token in text for token in ("selecione", "mês", "mes", "opção desejada")):
            latest, more = await send_choice(client, entity, latest, month_option, f"select target month for daily sales {requested_period}")
            if not more:
                raise RuntimeError(f"Monitorbot did not return the {target_month} daily report.")
            replies.extend(more)
            latest = await latest_incoming(client, entity) or more[-1]
            continue
        break
    return latest, replies, previous_day_confirmed, selection_after_id


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

    def login_gate(messages):
        return any(
            "precisa se logar" in norm(message.message or "").lower()
            or "precisa fazer login" in norm(message.message or "").lower()
            for message in messages
        )

    latest_now = await latest_incoming(client, entity)
    gate_seen = login_gate(replies) or (
        latest_now is not None
        and ("precisa se logar" in norm(latest_now.message or "").lower()
             or "precisa fazer login" in norm(latest_now.message or "").lower())
    )
    if gate_seen:
        print(f"LOGIN_GATE_DURING_SELECTION report_type={report_type}; discarding stale replies and restarting from a clean menu.")
        current = await recover_menu(client, entity, latest_now or replies[-1])
        menu_id = current.id
        latest, replies = await send_choice(client, entity, current, option, f"retry report type {report_type} after login")
        if not replies or login_gate(replies):
            raise RuntimeError(f"Login gate persisted while selecting report {report_type}; refusing to use stale Telegram replies.")
        latest = await latest_incoming(client, entity) or replies[-1]
    else:
        latest = latest_now or latest
        latest = await ensure_bellabyte_login(client, entity, latest)
        latest = await latest_incoming(client, entity) or latest

    daily_selection_confirmed = False
    daily_selection_after_id = None
    if report_type == "sale_day":
        latest, replies, daily_selection_confirmed, daily_selection_after_id = await resolve_daily_selection(
            client, entity, latest, replies, requested_period, local_now
        )
        # Only use messages after the explicit "Dia anterior" selection, not earlier/stale replies.
        if daily_selection_confirmed and daily_selection_after_id is not None:
            replies = [message for message in replies if getattr(message, "id", 0) > daily_selection_after_id]
    else:
        # Report 13 first asks for a group scope, then a year/month. Resolve in that order.
        if report_type in {"ticket_average", "products_by_group"}:
            latest, replies = await resolve_group_scope(client, entity, latest, replies, report_type)
            latest, replies = await resolve_period_selectors(client, entity, latest, replies, requested_period, report_type)
        else:
            latest, replies = await resolve_period_selectors(client, entity, latest, replies, requested_period, report_type)

    report, report_text = find_report_payload(replies, report_type)
    if report is None:
        # Fallback is constrained to messages arriving after this report-selection turn.
        found = await wait_for_report(client, entity, report_type, after_id=menu_id, timeout=40)
        if found is not None:
            report, report_text = found, found.message or ""
    if report is None or not report_text:
        # Some Bellabyte reports arrive as several data-only Telegram chunks after a
        # "report may take a while" message. Reassemble only messages newer than this turn.
        history = await client.get_messages(entity, limit=120)
        recent = sorted(
            (message for message in history
             if not message.out and getattr(message, "id", 0) > menu_id),
            key=lambda message: getattr(message, "id", 0)
        )
        report, report_text = find_report_payload(recent, report_type)
    if report is None or not report_text:
        latest_now = await latest_incoming(client, entity)
        raise RuntimeError(
            f"Report {option} ({report_type}) not found; latest response="
            f"{safe_preview(latest_now.message) if latest_now else 'none'}; "
            f"buttons={buttons(latest_now) if latest_now else []}"
        )

    low = norm(report_text).lower()
    if report_type == "sale_day" and daily_report_date_mismatch(report_text, "daily_yesterday"):
        raise RuntimeError("Daily report title says today while the requested period is yesterday.")
    if report_type == "sale_day" and not daily_selection_confirmed:
        has_yesterday_label = "venda de ontem" in low or "dia anterior" in low
        date_match = re.search(r"(?:data|referência|período)\s*:?\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", report_text, re.IGNORECASE)
        confirmed_date = None
        if date_match:
            token = date_match.group(1)
            if "/" in token:
                day, month, year = token.split("/")
                confirmed_date = f"{year}-{month}-{day}"
            else:
                confirmed_date = token
        if not has_yesterday_label and confirmed_date != requested_period:
            raise RuntimeError("Daily report lacks an explicit previous-day selection or matching date; refusing to save an ambiguous snapshot.")
    if report_type == "monthly_sales" and "venda acumulada do mês" not in low:
        raise RuntimeError("Monthly report semantic validation failed.")
    ticket_average_rows = (
        report_type == "ticket_average"
        and report_message_has_data(report_text)
        and re.search(r"preço|preco", report_text, re.IGNORECASE)
    )
    if report_type not in {"sale_day", "monthly_sales"} and not (
        report_title_match(report_text, report_type)
        or matches_report_type(report_text, report_type)
        or ticket_average_rows
    ):
        raise RuntimeError(f"Report body failed semantic validation for {report_type}.")

    output = save_report(report, report_type, requested_period, raw_text=report_text)
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
        card_totals, card_warnings = parse_totals(card_details["raw_text"])
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
