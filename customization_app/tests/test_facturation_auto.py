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


def _fonctions_numerotation(script):
    """Les fonctions pures de numérotation du script, exécutées hors Frappe."""
    def bloc(debut, fin):
        return script[script.index(debut):script.index(fin)]
    ns = {}
    exec(bloc("def pget(", "def to_bool("), ns)
    exec(bloc("def left_invoices(", "def get_invoice_number("), ns)
    return ns


class TestNumerosPrisParDAutresMois(unittest.TestCase):
    """07/10/2026 : la génération de septembre a redonné 2397-2417 à des factures du 30/09 alors que
    des factures d'octobre, saisies du 2 au 7, les portaient déjà (19 doublons). Le script ne lisait
    que les factures datées du mois visé."""

    def setUp(self):
        self.script = _script()

    def test_cablage(self):
        s = self.script
        self.assertIn("def get_reserved_numbers_outside_month(", s)
        self.assertIn("posting_date NOT BETWEEN %s AND %s", s)
        self.assertIn("docstatus IN (0, 1)", s)
        self.assertLess(s.index("RESERVED_OUTSIDE_MONTH = get_reserved_numbers_outside_month("),
                        s.index("initial_gaps = left_invoices("))
        self.assertIn('"reserved_outside_month_count"', s[s.index("if PREVIEW:"):])
        self.assertIn('SCRIPT_VERSION = "v9-', s)

    def test_ni_trou_ni_suite(self):
        ns = _fonctions_numerotation(self.script)
        # Septembre : série 2168..2396 ; octobre porte déjà 2397-2402 et 2405-2417.
        ns["RESERVED_OUTSIDE_MONTH"] = set(range(2397, 2403)) | set(range(2405, 2418))
        serie = [{"Fac_num": n, "Date": None} for n in (2390, 2391, 2393, 2396)]
        self.assertEqual(ns["left_invoices"](serie, 2389), {2392, 2394, 2395})
        self.assertEqual(ns["get_next_invoice_number"](serie, 2389), 2403)
        serie.append({"Fac_num": 2403, "Date": None})
        self.assertEqual(ns["get_next_invoice_number"](serie, 2389), 2404)
        serie.append({"Fac_num": 2404, "Date": None})
        self.assertEqual(ns["get_next_invoice_number"](serie, 2389), 2418)
        # Avec une cible manuelle, les numéros d'octobre ne deviennent pas des trous non plus.
        self.assertEqual(ns["left_invoices"](serie, 2389, 2406), {2392, 2394, 2395})

    def test_sans_autre_mois_rien_ne_change(self):
        ns = _fonctions_numerotation(self.script)
        ns["RESERVED_OUTSIDE_MONTH"] = set()
        serie = [{"Fac_num": 10, "Date": None}, {"Fac_num": 12, "Date": None}]
        self.assertEqual(ns["left_invoices"](serie, 9), {11})
        self.assertEqual(ns["get_next_invoice_number"](serie, 9), 13)

    def test_le_bilan_cherche_les_doublons_dans_toute_la_base(self):
        import inspect
        src = inspect.getsource(FA._enrich_result)
        bloc = src[src.index("collisions = set()"):src.index("existing_set = ")]
        self.assertNotIn("posting_date", bloc)
        self.assertIn("docstatus IN (0, 1)", bloc)
        self.assertIn("CAST(custom_numero_facture AS UNSIGNED) BETWEEN %s AND %s", bloc)


class TestDernierRempart(unittest.TestCase):
    """Quel que soit le script qui choisit le numéro, une facture de l'année ne peut pas en porter un déjà pris."""

    def test_cablage(self):
        import inspect
        from customization_app import facturation_numbering as N
        hooks = open(os.path.join(os.path.dirname(os.path.dirname(FIXTURE)), "hooks.py"), encoding="utf-8").read()
        bloc = hooks[hooks.index('"Sales Invoice": {'):]
        bloc = bloc[:bloc.index("},")]
        self.assertIn('"customization_app.facturation_numbering.refuser_numero_en_double"', bloc)
        self.assertIn('"validate": [', bloc)
        src = inspect.getsource(N.autre_facture_avec_le_numero)
        self.assertIn("docstatus < 2", src)
        self.assertIn("YEAR(posting_date) = YEAR(%s)", src)
        self.assertIn("name != %s", src)

    def test_un_numero_vide_ou_non_numerique_ne_bloque_rien(self):
        from customization_app import facturation_numbering as N
        self.assertIsNone(N.autre_facture_avec_le_numero("", "2026-10-01"))
        self.assertIsNone(N.autre_facture_avec_le_numero("2397-bis", "2026-10-01"))
        self.assertIsNone(N.autre_facture_avec_le_numero("2397", None))
