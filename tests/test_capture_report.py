import importlib.util
import pathlib
import unittest

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

    def test_daily_identity_is_stable_across_report_revisions(self):
        first = stable_report_identity("sale_day", "MARCOPAO", "2026-10-27", "2026-10-27")
        second = stable_report_identity("sale_day", "MARCOPAO", "2026-10-27", "2026-10-27")
        other_day = stable_report_identity("sale_day", "MARCOPAO", "2026-10-28", "2026-10-28")
        other_report = stable_report_identity("monthly_sales", "MARCOPAO", "2026-10-01", "2026-10-31")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_day)
        self.assertNotEqual(first, other_report)


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
