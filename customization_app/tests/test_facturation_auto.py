"""Facturation Auto : la liste des paiements éligibles AVANT lancement et les exclusions (01/10/2026).

Tests purs (fixture + fonction sans base) : en juillet 2026, deux factures auto de CFP ont été générées
sans qu'on puisse les écarter — la page n'affichait les paiements qu'après coup."""
from __future__ import annotations

import json
import os
import unittest

from customization_app import facturation_auto as FA

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "server_script.json")


def _script():
    with open(FIXTURE, encoding="utf-8") as f:
        return next(s["script"] for s in json.load(f) if s.get("name") == "Facturation Auto")


class TestExclusions(unittest.TestCase):
    def test_la_liste_json_de_la_page_devient_une_chaine_pour_le_script(self):
        self.assertEqual(FA._exclusions('["ACC-PAY-2026-00001", " ACC-PAY-2026-00002 "]'), "ACC-PAY-2026-00001,ACC-PAY-2026-00002")
        self.assertEqual(FA._exclusions(["A", "", "B"]), "A,B")
        self.assertEqual(FA._exclusions("A,B"), "A,B")
        self.assertEqual(FA._exclusions(None), "")
        self.assertEqual(FA._exclusions("[]"), "")


class TestScriptFacturationAuto(unittest.TestCase):
    def setUp(self):
        self.script = _script()

    def test_le_script_compile(self):
        compile(self.script, "Facturation Auto", "exec")

    def test_les_exclusions_sont_retirees_avant_tout_traitement(self):
        s = self.script
        self.assertIn('EXCLUDE_PAYMENTS = set(', s)
        self.assertLess(s.index("payments_of_the_month.append(pay_all)"), s.index("STATISTIQUES PAIEMENTS AVANT TRAITEMENT"))
        self.assertLess(s.index("payments_excluded_count = "), s.index("FACTURES LIEES AUX PAIEMENTS"))

    def test_l_apercu_rend_le_detail_avec_client_et_groupe(self):
        s = self.script
        apercu = s[s.index("if PREVIEW:"):s.index("raise _FacPreviewDone()")]
        self.assertIn('"payments_detail": [payment_row(p) for p in payments_all]', apercu)
        self.assertIn("CUS.customer_group AS Customer_Group", s)
        self.assertIn("CUS.customer_name AS Customer_Name", s)
        for cle in ('"customer_group"', '"customer_name"', '"excluded"'):
            self.assertIn(cle, s[s.index("def payment_row"):s.index("STATISTIQUES PAIEMENTS AVANT TRAITEMENT")])
