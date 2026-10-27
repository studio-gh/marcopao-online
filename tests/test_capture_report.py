import unittest

from telegram_bridge_capture_import import parse_totals


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

    def test_missing_total_is_flagged(self):
        totals, warnings = parse_totals("Venda do Dia\\nQuantidades de Cupons: 10")
        self.assertNotIn("total", totals)
        self.assertTrue(any("Total de vendas" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
