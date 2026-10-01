"""Transformation d'articles : règles pures + une vraie écriture Reconditionnement (annulée par rollback)."""
from __future__ import annotations

import unittest

from customization_app import transformation_articles as TA


class TestRegles(unittest.TestCase):
    def test_repartition_prorata(self):
        taux = TA.repartir_valeur(1218.1, [
            {"qty": 2, "taux_fixe": None, "taux_ref": 52.6},
            {"qty": 2, "taux_fixe": None, "taux_ref": 61.8},
            {"qty": 1, "taux_fixe": None, "taux_ref": 980.0},
        ])
        self.assertAlmostEqual(2 * taux[0] + 2 * taux[1] + taux[2], 1218.1, places=2)
        # même proportion que les taux de référence
        self.assertAlmostEqual(taux[0] / taux[1], 52.6 / 61.8, places=3)

    def test_taux_fixe_respecte(self):
        taux = TA.repartir_valeur(1218.1, [
            {"qty": 2, "taux_fixe": 52.6, "taux_ref": 52.6},
            {"qty": 2, "taux_fixe": 61.8, "taux_ref": 61.8},
            {"qty": 1, "taux_fixe": None, "taux_ref": 980.0},
        ])
        self.assertEqual(taux[:2], [52.6, 61.8])
        self.assertAlmostEqual(taux[2], 1218.1 - 2 * 52.6 - 2 * 61.8, places=2)

    def test_sans_reference_parts_egales_et_un_seul_obtenu(self):
        self.assertEqual(TA.repartir_valeur(300, [{"qty": 1, "taux_fixe": None, "taux_ref": 0},
                                                  {"qty": 2, "taux_fixe": None, "taux_ref": 0}]), [100.0, 100.0])
        self.assertEqual(TA.repartir_valeur(285, [{"qty": 1, "taux_fixe": None, "taux_ref": 999}]), [285.0])
        # tout fixé au-delà du total : les libres tombent à 0, jamais négatifs
        self.assertEqual(TA.repartir_valeur(100, [{"qty": 1, "taux_fixe": 150, "taux_ref": 1},
                                                  {"qty": 1, "taux_fixe": None, "taux_ref": 1}]), [150.0, 0.0])

    def test_alertes(self):
        self.assertEqual(TA.alertes_stock([{"item_code": "A", "qty": 2, "stock": 3}]), [])
        self.assertEqual(len(TA.alertes_stock([{"item_code": "A", "qty": 4, "stock": 3}])), 1)
        self.assertEqual(TA.ecart_taux(113.8, 46.6), 144.2)
        self.assertIsNone(TA.ecart_taux(10, 0))


class TestEcriture(unittest.TestCase):
    """Une vraie transformation sur le site, dans un savepoint : 2 unités d'un article
    en stock deviennent 1 unité d'un autre ; l'écriture est soumise puis tout est annulé."""

    def setUp(self):
        import frappe
        frappe.db.savepoint("transfo")
        frappe.set_user("Administrator")
        rows = frappe.db.sql("""
            SELECT b.item_code, b.actual_qty, b.valuation_rate FROM `tabBin` b
            JOIN `tabItem` i ON i.name = b.item_code AND i.is_stock_item = 1 AND i.disabled = 0
              AND i.has_batch_no = 0 AND i.has_serial_no = 0
            WHERE b.warehouse = %s AND b.actual_qty >= 2 AND b.valuation_rate > 0
            ORDER BY b.actual_qty DESC LIMIT 2
        """, (TA.MAGASIN_DEFAUT,), as_dict=True)
        if len(rows) < 2:
            self.skipTest("pas deux articles en stock dans le magasin par défaut")
        self.a, self.b = rows

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="transfo")

    def test_simuler_puis_valider_puis_annuler(self):
        import frappe
        from frappe.utils import flt
        conso = [{"item_code": self.a.item_code, "qty": 2}]
        obt = [{"item_code": self.b.item_code, "qty": 1}]
        sim = TA.simuler(conso, obt, TA.MAGASIN_DEFAUT)
        self.assertTrue(sim["valide"], sim["alertes"])
        self.assertAlmostEqual(sim["total_consomme"], sim["total_obtenu"], places=2)
        self.assertAlmostEqual(sim["obtenus"][0]["taux"], 2 * flt(self.a.valuation_rate), places=1)
        # stock insuffisant → refus
        self.assertFalse(TA.simuler([{"item_code": self.a.item_code, "qty": flt(self.a.actual_qty) + 1}], obt)["valide"])

        res = TA.valider(conso, obt, TA.MAGASIN_DEFAUT)
        se = frappe.get_doc("Stock Entry", res["name"])
        self.assertEqual((se.docstatus, se.purpose), (1, "Repack"))
        self.assertAlmostEqual(res["total_consomme"], res["total_obtenu"], places=2)
        qa = frappe.db.get_value("Bin", {"item_code": self.a.item_code, "warehouse": TA.MAGASIN_DEFAUT}, "actual_qty")
        qb = frappe.db.get_value("Bin", {"item_code": self.b.item_code, "warehouse": TA.MAGASIN_DEFAUT}, "actual_qty")
        self.assertAlmostEqual(flt(qa), flt(self.a.actual_qty) - 2, places=3)
        self.assertAlmostEqual(flt(qb), flt(self.b.actual_qty) + 1, places=3)
        hist = TA.historique(5)
        self.assertEqual(hist[0]["name"], res["name"])
        self.assertEqual([l["item_code"] for l in hist[0]["obtenus"]], [self.b.item_code])

        TA.annuler(res["name"])
        qa2 = frappe.db.get_value("Bin", {"item_code": self.a.item_code, "warehouse": TA.MAGASIN_DEFAUT}, "actual_qty")
        self.assertAlmostEqual(flt(qa2), flt(self.a.actual_qty), places=3)
        self.assertEqual(frappe.db.get_value("Stock Entry", res["name"], "docstatus"), 2)


class TestModeles(unittest.TestCase):
    """Modèle unitaire enregistré puis appliqué × N."""

    def test_multiplier(self):
        self.assertEqual(TA.multiplier_lignes([{"item_code": "A", "qty": 1}, {"item_code": "B", "qty": 2.5}], 3),
                         [{"item_code": "A", "qty": 3.0}, {"item_code": "B", "qty": 7.5}])
        self.assertEqual(TA.multiplier_lignes([{"item_code": "A", "qty": 1}], 0), [{"item_code": "A", "qty": 1.0}])

    def test_enregistrer_puis_appliquer(self):
        import frappe
        frappe.db.savepoint("modele")
        try:
            a, b = frappe.get_all("Item", filters={"is_stock_item": 1, "disabled": 0}, pluck="name", limit=2)
            # saisi pour 2 applications → gardé pour 1
            nom = TA.enregistrer_modele("Essai modèle", [{"item_code": a, "qty": 4}], [{"item_code": b, "qty": 2}], facteur=2)
            m = next(x for x in TA.modeles() if x["name"] == nom)
            self.assertEqual((m["consommes"][0]["qty"], m["obtenus"][0]["qty"]), (2.0, 1.0))
            app = TA.appliquer_modele(nom, 3)
            self.assertEqual((app["consommes"][0]["qty"], app["obtenus"][0]["qty"]), (6.0, 3.0))
            # même nom = remplacé, pas dupliqué
            TA.enregistrer_modele("Essai modèle", [{"item_code": a, "qty": 1}], [{"item_code": b, "qty": 1}])
            self.assertEqual(sum(1 for x in TA.modeles() if x["nom"] == "Essai modèle"), 1)
            TA.supprimer_modele(nom)
            self.assertFalse(any(x["name"] == nom for x in TA.modeles()))
        finally:
            frappe.db.rollback(save_point="modele")
