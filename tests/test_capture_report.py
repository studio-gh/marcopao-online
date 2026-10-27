import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("capture_report", ROOT / "telegram-bridge" / "capture_report.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
parse_totals = MODULE.parse_totals
matches_report = MODULE.matches_report



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

    def test_missing_total_is_flagged(self):
        totals, warnings = parse_totals("Venda do Dia\\nQuantidades de Cupons: 10")
        self.assertNotIn("total", totals)
        self.assertTrue(any("Total de vendas" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
