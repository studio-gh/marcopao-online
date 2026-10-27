import importlib.util
import pathlib
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("capture_report", ROOT / "telegram-bridge" / "capture_report.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
parse_totals = MODULE.parse_totals
parse_report_data = MODULE.parse_report_data
matches_report_type = MODULE.matches_report_type
report_period = MODULE.report_period
matches_report = MODULE.matches_report
daily_report_date_mismatch = MODULE.daily_report_date_mismatch
report_type_for_option = MODULE.report_type_for_option
monthly_period = MODULE.monthly_period
stable_report_identity = MODULE.stable_report_identity
report_content_hash = MODULE.report_content_hash
find_report_payload = MODULE.find_report_payload
report_title_match = MODULE.report_title_match
is_report_menu_message = MODULE.is_report_menu_message



class ParseTotalsTests(unittest.TestCase):
    def test_parses_brazilian_currency_and_reconciles_payments(self):
        raw = """Venda do Dia
Total de vendas nos Pdvs
Dinheiro: R$ 100,00
PIX: R$ 25,00
POS Crédito: R$ 10,00
POS Débito: R$ 5,00
TEF: R$ 60,00
TICKET: R$ 0,00
Total: R$ 200,00
Quantidades de Cupons: 10
Ticket Médio: 20,00
"""
        totals, warnings = parse_totals(raw)
        self.assertEqual(totals["dinheiro"], 100.0)
        self.assertEqual(totals["pix"], 25.0)
        self.assertEqual(totals["pos_credito"], 10.0)
        self.assertEqual(totals["pos_debito"], 5.0)
        self.assertEqual(totals["tef"], 60.0)
        self.assertEqual(totals["total"], 200.0)
        self.assertEqual(totals["cupons"], 10)
        self.assertEqual(totals["payment_reconciliation_diff"], 0.0)
        self.assertEqual(warnings, [])


    def test_monthly_report_is_not_matched_as_daily(self):
        monthly = """Venda Acumulada do Mês de Outubro
Total de vendas nos Pdvs
Dinheiro: R$ 100,00
TEF: R$ 100,00
Total: R$ 200,00
Quantidades de Cupons: 10
"""
        self.assertTrue(matches_report(monthly, "monthly_october"))
        self.assertFalse(matches_report(monthly, "daily_yesterday"))

    def test_rejects_today_report_when_yesterday_was_requested(self):
        self.assertTrue(daily_report_date_mismatch(
            "Venda de Hoje Filial: Marcopao Total de vendas nos Pdvs", "daily_yesterday"
        ))
        self.assertFalse(daily_report_date_mismatch(
            "Venda do Dia Anterior Total de vendas nos Pdvs", "daily_yesterday"
        ))
        self.assertFalse(daily_report_date_mismatch(
            "Venda de Hoje Total de vendas nos Pdvs", "monthly_october"
        ))

    def test_daily_report_is_recognized(self):
        daily = """Venda do Dia
Total de vendas nos Pdvs
Dinheiro: R$ 100,00
TEF: R$ 60,00
Total: R$ 160,00
Quantidades de Cupons: 8
"""
        self.assertTrue(matches_report(daily, "daily_yesterday"))
        self.assertFalse(matches_report("1: Venda do Dia. 2: Venda Acumulada Mês.", "daily_yesterday"))
        self.assertFalse(matches_report("Venda do Dia: selecione a opção que deseja", "daily_yesterday"))

    def test_missing_total_is_flagged(self):
        totals, warnings = parse_totals("Venda do Dia\nQuantidades de Cupons: 10")
        self.assertNotIn("total", totals)
        self.assertTrue(any("Total de vendas" in warning for warning in warnings))

    def test_missing_card_methods_are_flagged_even_when_total_exists(self):
        raw = """Venda do Dia
Total de vendas nos Pdvs
Dinheiro: R$ 100,00
PIX: R$ 25,00
Total: R$ 125,00
Quantidades de Cupons: 5
"""
        totals, warnings = parse_totals(raw)
        self.assertFalse(totals["card_sales_included"])
        self.assertNotIn("payment_methods_sum", totals)
        self.assertTrue(any("Detalhes de cartão incompletos" in warning for warning in warnings))

    def test_cards_are_included_in_payment_reconciliation(self):
        raw = """Venda do Dia
Total de vendas nos Pdvs
Dinheiro: R$ 100,00
PIX: R$ 25,00
POS Crédito: R$ 10,00
POS Débito: R$ 5,00
TEF: R$ 60,00
TICKET: R$ 0,00
Total: R$ 200,00
Quantidades de Cupons: 10
"""
        totals, warnings = parse_totals(raw)
        self.assertTrue(totals["card_sales_included"])
        self.assertEqual(totals["payment_methods_sum"], 200.0)
        self.assertEqual(totals["payment_reconciliation_diff"], 0.0)
        self.assertEqual(warnings, [])

    def test_branch_selection_prompt_is_not_a_report_menu(self):
        message = self.fake_message(
            "Bem vindo Relatório Bellabyte Digite qual filial deseja se conectar? 1: MARCOPAO",
            ("1", "2", "3", "4", "10", "12", "13", "Menu Inicial", "#", "0"),
        )
        self.assertFalse(is_report_menu_message(message))

    def test_report_menu_with_selected_branch_is_recognized(self):
        message = self.fake_message(
            "Bem vindo Relatório Bellabyte Sua filial é a [1] MARCOPAO\\n1: Venda do Dia\\n2: Venda Acumulada Mês\\n3: Top 100 Valor",
            ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "#", "0"),
        )
        self.assertTrue(is_report_menu_message(message))

    def test_menu_has_all_seven_report_types(self):
        expected = {
            "1": "sale_day",
            "2": "monthly_sales",
            "3": "top_value",
            "4": "top_quantity",
            "10": "ticket_average",
            "12": "sales_by_group",
            "13": "products_by_group",
        }
        for option, report_type in expected.items():
            self.assertEqual(report_type_for_option(option), report_type)
        self.assertIsNone(report_type_for_option("5"))

    def test_monthly_period_is_dynamic_not_hardcoded(self):
        self.assertEqual(monthly_period("2026-10-27"), "2026-10")
        self.assertEqual(monthly_period("2027-01-02"), "2027-01")

    def test_report_content_hash_distinguishes_revisions(self):
        first = report_content_hash("Venda do Dia Total: R$ 100,00")
        same = report_content_hash("Venda do Dia Total: R$ 100,00")
        revised = report_content_hash("Venda do Dia Total: R$ 110,00")
        self.assertEqual(first, same)
        self.assertNotEqual(first, revised)

    def test_daily_identity_is_stable_across_report_revisions(self):
        first = stable_report_identity("sale_day", "MARCOPAO", "2026-10-27", "2026-10-27")
        second = stable_report_identity("sale_day", "MARCOPAO", "2026-10-27", "2026-10-27")
        other_day = stable_report_identity("sale_day", "MARCOPAO", "2026-10-28", "2026-10-28")
        other_report = stable_report_identity("monthly_sales", "MARCOPAO", "2026-10-01", "2026-10-31")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_day)
        self.assertNotEqual(first, other_report)


    def test_daily_total_parses_when_telegram_collapses_metrics_to_one_line(self):
        raw = "Venda do Dia Total de vendas nos Pdvs Dinheiro: R$ 100,00 PIX: R$ 20,00 POS Crédito: R$ 10,00 POS Débito: R$ 5,00 TEF: R$ 60,00 TICKET: R$ 5,00 Total: R$ 200,00 Quantidades de Cupons: 10 Ticket Médio: R$ 20,00"
        totals, warnings = parse_report_data("sale_day", raw)
        self.assertEqual(totals["total"], 200.00)
        self.assertEqual(totals["cupons"], 10)
        self.assertEqual(totals["payment_reconciliation_diff"], 0.0)

    def test_type_aware_parser_reads_monthly_and_all_payment_methods(self):
        raw = """Venda Acumulada do Mês de Outubro Filial: MARCOPAO LANCHONETE E PANIFICACAO LTDA
Total de vendas nos Pdvs
Dinheiro: R$ 44037,26
PIX: R$ 1352,25
POS Crédito: R$ 2684,55
POS Débito: R$ 2549,19
TEF: R$ 156650,62
TICKET: R$ 703,31
Total: R$ 207977,18
Quantidades de Cupons: 12584
Ticket Médio: 16,53
"""
        totals, warnings = parse_report_data("monthly_sales", raw)
        self.assertEqual(totals["total"], 207977.18)
        self.assertEqual(totals["cupons"], 12584)
        self.assertEqual(totals["payment_reconciliation_diff"], 0.0)

    def test_product_and_group_reports_do_not_invent_sales_total(self):
        raw = "Pão francês R$ 41600,11\nPão de queijo R$ 10048,36"
        totals, warnings = parse_report_data("top_value", raw)
        self.assertEqual(totals["entry_count"], 2)
        self.assertNotIn("total", totals)
        self.assertEqual(totals["entries"][0]["name"], "Pão francês")
        self.assertTrue(matches_report_type("Top 100 Valor\nPão francês R$ 41600,11", "top_value"))
        self.assertFalse(matches_report_type("Venda Acumulada do Mês\nTotal: R$ 207977,18", "top_value"))

    def fake_message(self, text, labels=(), message_id=1):
        button_rows = []
        if labels:
            button_rows = [SimpleNamespace(buttons=[SimpleNamespace(text=value) for value in labels])]
        markup = SimpleNamespace(rows=button_rows)
        return SimpleNamespace(message=text, reply_markup=markup, id=message_id)

    def test_report_payload_joins_multiple_telegram_chunks(self):
        messages = [
            self.fake_message("Selecione o mês", ("Outubro",), 1),
            self.fake_message("Top 100 Valor Acumulada do Mês de Outubro\\n1: PAO FRANCES\\nTotal R$: 43261,02", (), 2),
            self.fake_message("2: PAO DE QUEIJO\\nTotal R$: 10411,11", (), 3),
            self.fake_message("Bem vindo Relatório Bellabyte", ("1", "2", "3"), 4),
        ]
        report, raw = find_report_payload(messages, "top_value")
        self.assertEqual(report.id, 2)
        self.assertIn("PAO FRANCES", raw)
        self.assertIn("PAO DE QUEIJO", raw)
        self.assertIn("\n", raw)
        self.assertNotIn("Bem vindo", raw)
        parsed, warnings = parse_report_data("top_value", raw)
        self.assertGreaterEqual(parsed["entry_count"], 2)
        self.assertEqual(parsed["entries"][0]["value"], 43261.02)

    def test_report_payload_skips_ticket_average_group_selector_prompt(self):
        messages = [
            self.fake_message("Ticket Medio Preço Vendas por quantidade", ("Todos", "PADARIA", "BEBIDAS"), 1),
            self.fake_message("Ticket Medio Preço Vendas por quantidade\\nPAO FRANCES\\nPreço Médio:R$24,99", ("1", "2"), 2),
        ]
        report, raw = find_report_payload(messages, "ticket_average")
        self.assertEqual(report.id, 2)
        self.assertIn("24,99", raw)

    def test_ranked_rows_have_clean_names_and_all_100_entries(self):
        rows = []
        for rank in range(1, 101):
            rows.append(f"{rank}: PRODUTO {rank:03d} Total R$: 123,45 Quantidade: 2UN Preço Atual R$: 5,00 Preço Medio R$: 5,00")
        raw = "Top 100 Valor Acumulada do Mês de Outubro Filial: MARCOPAO\\n" + " ".join(rows)
        totals, warnings = parse_report_data("top_value", raw)
        self.assertEqual(totals["entry_count"], 100)
        self.assertEqual(totals["entries"][0]["name"], "PRODUTO 001")
        self.assertEqual(totals["entries"][0]["value"], 123.45)
        self.assertEqual(totals["entries"][0]["quantity"], 2)
        self.assertEqual(totals["entries"][99]["name"], "PRODUTO 100")
        self.assertFalse(any("Ranking incompleto" in warning for warning in warnings))

    def test_ticket_average_pairs_names_with_price_quantity_lines(self):
        raw = """Ticket Preço venda Acumulada do Mês de Outubro Filial: MARCOPAO

ACUCAR UNIAO 1 KG
R$:6,90, Qtd:12UN

ADES 200 ML
R$:4,00, Qtd:4UN"""
        totals, warnings = parse_report_data("ticket_average", raw)
        self.assertEqual(totals["entry_count"], 2)
        self.assertEqual(totals["entries"][0]["name"], "ACUCAR UNIAO 1 KG")
        self.assertEqual(totals["entries"][0]["unit_price"], 6.90)
        self.assertEqual(totals["entries"][0]["quantity"], 12)
        self.assertEqual(totals["entries"][1]["name"], "ADES 200 ML")
        self.assertEqual(totals["entries"][1]["unit_price"], 4.00)

    def test_products_by_group_parses_revenue_stock_and_cost(self):
        raw = """Vendas produtos por grupo Todos Filial: MARCOPAO
1: PAO FRANCES
Quantidade: 1738,609 KG
Preço:R$24,99
Total:R$ 43448,76
Estoque: -163293,062 KG
Custo:R$ 9,99"""
        totals, warnings = parse_report_data("products_by_group", raw)
        self.assertEqual(totals["entry_count"], 1)
        self.assertEqual(totals["entries"][0]["name"], "PAO FRANCES")
        self.assertEqual(totals["entries"][0]["value"], 43448.76)
        self.assertEqual(totals["entries"][0]["stock"], -163293.062)
        self.assertEqual(totals["entries"][0]["stock_validation"], "suspect_negative")
        self.assertTrue(any("Estoque negativo" in warning for warning in warnings))
        self.assertEqual(totals["entries"][0]["cost"], 9.99)

    def test_group_report_extracts_name_and_average(self):
        raw = "Vendas por grupo Acumulada do Mês de Outubro\\n1: PADARIA Total R$: 87739,20 Quantidade: 7600,325 Preço Médio Venda R$: 11,54 2: LANCHONETE Total R$: 37169,57 Quantidade: 4794,730 Preço Médio Venda R$: 7,75"
        totals, warnings = parse_report_data("sales_by_group", raw)
        self.assertEqual(totals["entry_count"], 2)
        self.assertEqual(totals["entries"][0]["name"], "PADARIA")
        self.assertEqual(totals["entries"][0]["value"], 87739.20)
        self.assertEqual(totals["entries"][0]["average"], 11.54)
        self.assertEqual(totals["entries"][1]["name"], "LANCHONETE")
        self.assertEqual(totals["entries"][1]["average"], 7.75)

    def test_products_by_group_title_variant_is_recognized(self):
        self.assertTrue(report_title_match("Vendas produtos por grupo Todos Filial: MARCOPAO", "products_by_group"))

    def test_ticket_average_can_start_from_price_rows_after_selector_prompts(self):
        messages = [
            self.fake_message("Ticket Medio Preço Vendas por quantidade", ("Todos", "PADARIA"), 1),
            self.fake_message("Informe a data", ("Hoje", "Outubro"), 2),
            self.fake_message("PAO FRANCES Quantidade: 100 KG Preço Médio R$: 24,99", ("1", "2"), 3),
            self.fake_message("PAO DE QUEIJO Quantidade: 20 KG Preço Médio R$: 63,00", ("1", "2"), 4),
        ]
        report, raw = find_report_payload(messages, "ticket_average")
        self.assertEqual(report.id, 3)
        self.assertIn("PAO FRANCES", raw)
        self.assertIn("PAO DE QUEIJO", raw)

    def test_ticket_average_parses_paired_product_price_and_quantity_lines(self):
        raw = """Ticket Preço venda Acumulada do Mês de Outubro Filial: MARCOPAO

ACUCAR UNIAO 1 KG
R$:6,90, Qtd:12UN

CAVACA DE MILHO
R$:49,90, Qtd:0,312KG
R$:47,90, Qtd:21,876KG
P.M:R$48,90, Q.T:22,188KG"""
        totals, warnings = parse_report_data("ticket_average", raw)
        self.assertEqual(totals["entry_count"], 2)
        self.assertEqual(totals["entries"][0]["name"], "ACUCAR UNIAO 1 KG")
        self.assertEqual(totals["entries"][0]["quantity"], 12)
        self.assertEqual(totals["entries"][0]["unit"], "un")
        self.assertEqual(totals["entries"][0]["average"], 6.90)
        self.assertEqual(totals["entries"][1]["name"], "CAVACA DE MILHO")
        self.assertEqual(totals["entries"][1]["quantity"], 22.188)
        self.assertEqual(totals["entries"][1]["unit"], "kg")
        self.assertEqual(totals["entries"][1]["average"], 48.90)

    def test_ticket_average_fallback_accepts_price_only_rows(self):
        messages = [
            self.fake_message("Informe a data", ("Hoje", "Outubro"), 1),
            self.fake_message("PAO FRANCES Quantidade: 100 KG Preço:R$24,99", ("1", "2"), 2),
        ]
        report, raw = find_report_payload(messages, "ticket_average")
        self.assertEqual(report.id, 2)
        self.assertIn("PAO FRANCES", raw)

    def test_group_title_is_recognized_even_when_body_is_a_separate_message(self):
        self.assertTrue(report_title_match("Vendas por grupoAcumulada do Mês de Outubro Filial: MARCOPAO", "sales_by_group"))
        self.assertTrue(report_title_match("Vendas de Produto Por Grupo Acumulada do Mês", "products_by_group"))

    def test_report_payload_combines_group_heading_and_body(self):
        messages = [
            self.fake_message("Vendas por grupoAcumulada do Mês de Outubro Filial: MARCOPAO", ("1", "2", "3"), 1),
            self.fake_message("PADARIA\\nTotal R$: 80000,00\\nLANCHONETE\\nTotal R$: 40000,00", ("1", "2", "3"), 2),
        ]
        report, raw = find_report_payload(messages, "sales_by_group")
        self.assertEqual(report.id, 1)
        self.assertIn("PADARIA", raw)
        self.assertIn("80000,00", raw)
        self.assertIn("40000,00", raw)

    def test_actual_bellabyte_top_value_blocks(self):
        raw = """Top 100 Valor Acumulada do Mês de Outubro Filial: MARCOPAO LANCHONETE E PANIFICACAO LTDA

1: PAO FRANCES
Total R$: 43261,02
Quantidade: 1731,097KG
Preço Atual R$: 24,99
Preço Medio R$: 24,99

2: PAO DE QUEIJO
Total R$: 10411,11
Quantidade: 165,320KG
Preço Atual R$: 63,00
Preço Medio R$: 63,00
"""
        totals, warnings = parse_report_data("top_value", raw)
        self.assertEqual(totals["entry_count"], 2)
        self.assertEqual(totals["entries"][0]["name"], "PAO FRANCES")
        self.assertEqual(totals["entries"][0]["value"], 43261.02)
        self.assertEqual(totals["entries"][0]["quantity"], 1731.097)
        self.assertEqual(totals["entries"][0]["unit"], "kg")

    def test_actual_bellabyte_top_quantity_blocks(self):
        raw = """Top 100 Quantidade Acumulada do Mês de Outubro Filial: MARCOPAO LANCHONETE E PANIFICACAO LTDA

1: SACOLA BIODEGRADAVEL 40X50
Quantidade: 3188 UN
Preço:R$0,08
Preço Médio Venda:R$0,08

2: PAO FRANCES
Quantidade: 1731,1 KG
Preço:R$24,99
Preço Médio Venda:R$24,99
"""
        totals, warnings = parse_report_data("top_quantity", raw)
        self.assertEqual(totals["entry_count"], 2)
        self.assertEqual(totals["entries"][0]["name"], "SACOLA BIODEGRADAVEL 40X50")
        self.assertEqual(totals["entries"][0]["quantity"], 3188.0)
        self.assertEqual(totals["entries"][0]["unit"], "un")
        self.assertEqual(totals["entries"][0]["unit_price"], 0.08)
        self.assertEqual(totals["entries"][1]["quantity"], 1731.1)
        self.assertEqual(totals["entries"][1]["unit"], "kg")
        self.assertEqual(totals["entries"][1]["unit_price"], 24.99)

    def test_product_rows_can_include_revenue_and_quantity(self):
        row = "Pão francês R$ 41600,11 1.664,635 kg"
        totals, warnings = parse_report_data("products_by_group", row)
        self.assertEqual(totals["entry_count"], 1)
        self.assertEqual(totals["entries"][0]["name"], "Pão francês")
        self.assertEqual(totals["entries"][0]["value"], 41600.11)
        self.assertEqual(totals["entries"][0]["quantity"], 1664.635)
        self.assertEqual(totals["entries"][0]["unit"], "kg")

    def test_each_menu_report_type_is_distinct(self):
        examples = {
            "top_value": "Top 100 Valor\nPão francês R$ 41600,11",
            "top_quantity": "Top 100 Quantidade\nPão francês 1664,635 kg",
            "ticket_average": "Ticket Médio Preço Vendas por quantidade\nPão francês R$ 16,53",
            "sales_by_group": "Total Vendas Por Grupo\nPadaria R$ 85000,00",
            "products_by_group": "Vendas de Produto Por Grupo\nPão francês R$ 41600,11",
        }
        for report_type, body in examples.items():
            with self.subTest(report_type=report_type):
                self.assertTrue(matches_report_type(body, report_type))
                for other in examples:
                    if other != report_type:
                        self.assertFalse(matches_report_type(body, other))

    def test_periods_are_report_specific(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 10, 27, 8, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
        self.assertEqual(report_period("sale_day", now, "2026-10-26"), ("2026-10-26", "2026-10-26", "2026-10-26"))
        self.assertEqual(report_period("monthly_sales", now, "2026-10"), ("2026-10-01", "2026-10-31", None))
        self.assertEqual(report_period("top_value", now, "2026-10"), ("2026-10-01", "2026-10-27", "2026-10-27"))

if __name__ == "__main__":
    unittest.main()
