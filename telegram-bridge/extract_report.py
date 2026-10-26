#!/usr/bin/env python3
"""Parse locally exported Marcopão/Bellabyte Telegram messages into reviewable JSON.

Read-only: this script never connects to Telegram or writes to Supabase.
Usage:
  python telegram-bridge/extract_report.py relatorios/vendas_outubro_20261026_154943.json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


MONEY_RE = re.compile(r"R\$\s*([\d.]+,\d{2})")
FIELD_RE = re.compile(
    r"(Dinheiro|PIX|POS Crédito|POS Débito|TEF|TICKET|Total|"
    r"Quantidade de Cupons|Ticket Médio|Qtd de Cupons cancelados|"
    r"Valor Cupons canc\.|Qtd Venda não Concluída|Venda não Concluída|"
    r"Vlr Total Item Canc\.)\s*:\s*(R\$\s*)?([\d.]+,\d{2}|\d+)",
    re.IGNORECASE,
)


def br_number(value: str) -> Decimal:
    value = value.strip()
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return Decimal(value)


def json_number(value: Decimal):
    return str(value.quantize(Decimal("0.01")))


def load_source(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("mensagens"), list):
        raise ValueError("JSON não tem a estrutura esperada: objeto com lista 'mensagens'.")
    return data


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python telegram-bridge/extract_report.py caminho/do/relatorio.json")
        return 2

    source_path = Path(sys.argv[1])
    data = load_source(source_path)
    messages = data["mensagens"]
    received = [m for m in messages if m.get("direcao") == "recebida"]
    text = "\n".join(str(m.get("texto", "")) for m in received)

    result = {
        "source_file": str(source_path),
        "report": data.get("relatorio", "não identificado"),
        "requested_period": data.get("periodo_solicitado", "não informado"),
        "query_started_at": data.get("consulta_iniciada"),
        "message_count": len(messages),
        "received_message_count": len(received),
        "metrics": {},
        "card_administrator": [],
        "card_network": [],
        "warnings": [],
        "status": "review_required",
    }

    # Extract fields from the monthly-sales summary message only, avoiding
    # duplicate "Total" fields in the card breakdowns.
    summary_match = re.search(
        r"Venda Acumulada do Mês.*?(?=Deseja visualizar os cartões\?|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    summary = summary_match.group(0) if summary_match else ""
    if not summary:
        result["warnings"].append("Não encontrei o bloco de resumo mensal; confirme o formato da resposta.")
    else:
        for match in FIELD_RE.finditer(summary):
            label, _currency, raw_value = match.groups()
            key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            try:
                value = br_number(raw_value)
            except InvalidOperation:
                result["warnings"].append(f"Valor inválido para {label}: {raw_value}")
                continue
            result["metrics"][key] = json_number(value)

    # Parse both card breakdowns by section. Keep labels as returned by the bot.
    card_blocks = re.findall(
        r"VENDAS POR ADMINISTRADORA DE CARTÃO\s*(.*?)(?=VENDAS POR ADMINISTRADORA DE CARTÃO|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for index, block in enumerate(card_blocks[:2]):
        target = result["card_administrator"] if index == 0 else result["card_network"]
        section_total = None
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            money = MONEY_RE.search(line)
            if not money:
                continue
            label = line[:money.start()].strip(" :|")
            try:
                amount = br_number(money.group(1))
            except InvalidOperation:
                continue
            if label.lower() == "total":
                section_total = amount
            else:
                target.append({"label": label, "amount": json_number(amount)})
        if section_total is not None:
            result["card_administrator" if index == 0 else "card_network"] = {
                "items": target,
                "reported_total": json_number(section_total),
            }

    # Basic arithmetic checks are warnings, never silent corrections.
    metrics = result["metrics"]
    payment_keys = ["dinheiro", "pix", "pos_credito", "pos_debito", "tef", "ticket"]
    if all(k in metrics for k in payment_keys) and "total" in metrics:
        payment_sum = sum((Decimal(metrics[k]) for k in payment_keys), Decimal("0"))
        reported_total = Decimal(metrics["total"])
        diff = (payment_sum - reported_total).quantize(Decimal("0.01"))
        if diff != 0:
            result["warnings"].append(
                f"Soma dos meios de pagamento difere do total informado em R$ {json_number(diff)}; preservados os valores originais."
            )

    if not result["warnings"]:
        result["status"] = "parsed_needs_review"

    output_path = source_path.with_name(source_path.stem + "_extraido.json")
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Extração concluída.")
    print("Status:", result["status"])
    print("Mensagens recebidas:", len(received))
    print("Métricas identificadas:", len(result["metrics"]))
    print("Itens de cartão:", sum(len(result[k].get("items", [])) if isinstance(result[k], dict) else len(result[k]) for k in ("card_administrator", "card_network")))
    print("Alertas:", len(result["warnings"]))
    for warning in result["warnings"]:
        print("-", warning)
    print("Arquivo salvo em:", output_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print("Erro:", exc, file=sys.stderr)
        raise SystemExit(1)
