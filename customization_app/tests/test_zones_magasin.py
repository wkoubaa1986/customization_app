"""Zones du magasin (espace > zone, un article dans plusieurs zones) et sortie principale.
Vrais articles dans un savepoint : tout est annulé."""
from __future__ import annotations

import unittest

from customization_app import zones_magasin as ZM
from customization_app.customize_erpnext.doctype.zone_magasin.zone_magasin import decouper_codes, normaliser_code
from customization_app.patches.zones_magasin_unitaires import interpreter


class TestRegles(unittest.TestCase):
    def test_codes(self):
        self.assertEqual([normaliser_code(x) for x in ("A 4", "a-4", "A-4", "A4", "  Armoire ", "B 3 - B 2")],
                         ["A4", "A4", "A4", "A4", "Armoire", "B 3 - B 2"])
        self.assertEqual(decouper_codes("A1, a 1 ; A2\nK3"), ["A1", "A2", "K3"])

    def test_reprise_de_l_existant(self):
        self.assertEqual(interpreter("V-3 / Hall / Sedda"), [("Hall", "V3"), ("Hall", "Sedda")])
        self.assertEqual(interpreter("A 4"), [("Magasin", "A4")])
        self.assertEqual(interpreter("Sedda"), [("Hall", "Sedda")])
        self.assertEqual(interpreter("Hall"), [("Hall", None)])
        self.assertEqual(interpreter("Hall - J2-J3-K2-K3"), [("Hall", "J2-J3-K2-K3")])
        self.assertEqual(interpreter("W-2 / / Hall / Sedda"), [("Hall", "W2"), ("Hall", "Sedda")])
        self.assertEqual(interpreter(""), [])


class TestZones(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("zones")
        self.items = frappe.get_all("Item", filters={"disabled": 0, "is_stock_item": 1, "has_variants": 0},
                                    pluck="name", limit=3, order_by="name")
        self.societe = ZM._societe()
        ZM.creer_zones("TESTESP")                                      # un espace
        ZM.creer_zones("A1, a 2, A2", espace="TESTESP")                 # deux zones (doublon ignoré)

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="zones")
        for i in self.items:
            frappe.clear_document_cache("Item", i)

    def _zones(self, item):
        import frappe
        return ZM.zones_de(item), frappe.db.get_value("Item", item, ZM.TEXTE)

    def test_creation_et_unicite(self):
        import frappe
        noms = {z["name"] for z in ZM.get_context()["zones"]}
        self.assertTrue({"TESTESP", "TESTESP - A1", "TESTESP - A2"} <= noms)
        r = ZM.creer_zones("a1, A3", espace="TESTESP")
        self.assertEqual((r["creees"], r["deja"]), (["TESTESP - A3"], ["TESTESP - A1"]))
        with self.assertRaises(frappe.ValidationError):
            ZM.creer_zones("X1", espace="TESTESP - A1")                  # une zone n'est pas un espace
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "Zone Magasin", "espace": "TESTESP", "code": "K / L"}).insert()

    def test_un_article_dans_plusieurs_zones_dans_les_deux_sens(self):
        i0, i1 = self.items[:2]
        # depuis la zone : on y range deux articles
        r = ZM.ajouter_a_zone("TESTESP - A1", [i0, i1])
        self.assertEqual(r["ajoutes"], 2)
        self.assertEqual(ZM.ajouter_a_zone("TESTESP - A1", [i0])["deja"], 1)
        # depuis l'article : il est AUSSI dans A2
        ZM.definir_zones(i0, ["TESTESP - A1", "TESTESP - A2"])
        self.assertEqual(self._zones(i0), (["TESTESP - A1", "TESTESP - A2"], "TESTESP - A1 / TESTESP - A2"))
        membres = [a.item_code for a in ZM.get_articles(zone="TESTESP - A1", stockes=0)["articles"]]
        self.assertEqual(sorted(membres), sorted([i0, i1]))
        compte = {z["name"]: z["articles"] for z in ZM.get_context()["zones"]}
        self.assertEqual((compte["TESTESP - A1"], compte["TESTESP - A2"]), (2, 1))
        ZM.retirer_de_zone("TESTESP - A1", [i0])
        self.assertEqual(self._zones(i0), (["TESTESP - A2"], "TESTESP - A2"))
        ZM.definir_zones(i0, [])
        self.assertEqual(self._zones(i0), ([], None))
        self.assertIn(i0, [a.item_code for a in ZM.get_articles(zone=ZM.SANS_ZONE, recherche=i0, stockes=0)["articles"]])

    def test_renommer_fusionner_et_supprimer(self):
        import frappe
        i0, i1 = self.items[:2]
        ZM.ajouter_a_zone("TESTESP - A1", [i0])
        ZM.ajouter_a_zone("TESTESP - A2", [i1])
        # fusion : A1 renommée en « a2 » -> rejoint A2
        r = ZM.renommer_zone("TESTESP - A1", "a2")
        self.assertTrue(r["fusion"])
        self.assertFalse(frappe.db.exists("Zone Magasin", "TESTESP - A1"))
        self.assertEqual(ZM.zones_de(i0), ["TESTESP - A2"])
        # renommer l'espace : ses zones et les textes suivent
        ZM.renommer_zone("TESTESP", "TESTDEP")
        self.assertEqual(self._zones(i1), (["TESTDEP - A2"], "TESTDEP - A2"))
        self.assertEqual(frappe.db.get_value("Zone Magasin", "TESTDEP - A2", ["espace", "code"]), ("TESTDEP", "A2"))
        # supprimer l'espace emporte ses zones et libère les articles
        r = ZM.supprimer_zone("TESTDEP")
        self.assertEqual((r["liberes"], r["supprimees"]), (2, 2))
        self.assertEqual(self._zones(i0), ([], None))

    def test_fiche_article_reecrit_le_texte(self):
        import frappe
        doc = frappe.get_doc("Item", self.items[0])
        doc.set(ZM.TABLE, [{"zone": "TESTESP - A2"}, {"zone": "TESTESP - A1"}, {"zone": "TESTESP - A2"}])
        ZM.item_validate(doc)
        self.assertEqual(([r.zone for r in doc.get(ZM.TABLE)], doc.get(ZM.TEXTE)),
                         (["TESTESP - A2", "TESTESP - A1"], "TESTESP - A2 / TESTESP - A1"))

    def test_sortie_principale_prise_par_les_ventes(self):
        import frappe
        from erpnext.stock.get_item_details import get_item_details
        entrepots = [e.name for e in ZM.entrepots_de_sortie(self.societe)]
        cible = next(e for e in entrepots if not e.startswith("Magasins"))
        item = self.items[0]
        ZM.definir_sortie([item], cible)
        self.assertEqual(frappe.get_all("Item Default", filters={"parent": item, "company": self.societe},
                                        pluck="default_warehouse"), [cible])
        base = {"item_code": item, "company": self.societe, "doctype": "Sales Order", "conversion_rate": 1,
                "price_list": frappe.db.get_single_value("Selling Settings", "selling_price_list"),
                "currency": frappe.get_cached_value("Company", self.societe, "default_currency"),
                "transaction_date": frappe.utils.nowdate(), "qty": 1, "customer": frappe.db.get_value("Customer", {}, "name")}
        self.assertEqual(get_item_details(dict(base)).get("warehouse"), cible)               # sortie automatique
        impose = next(e for e in entrepots if e != cible)
        self.assertEqual(get_item_details(dict(base, set_warehouse=impose)).get("warehouse"), impose)
        for wh in filter(None, (frappe.db.get_value("Warehouse", {"disabled": 1, "is_group": 0}, "name"),
                                frappe.db.get_value("Warehouse", {"is_group": 1}, "name"))):
            with self.assertRaises(frappe.ValidationError):
                ZM.definir_sortie([item], wh)

    def test_ecriture_reservee(self):
        import frappe
        user = next((u for u in frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name")
                     if u != "Administrator" and frappe.has_permission("Item", "read", user=u)
                     and not frappe.has_permission("Item", "write", user=u)), None)
        if not user:
            self.skipTest("aucun utilisateur en lecture seule sur Item")
        frappe.set_user(user)
        self.assertIsNotNone(ZM.get_articles()["articles"])
        for appel in (lambda: ZM.ajouter_a_zone("TESTESP - A1", self.items[:1]), lambda: ZM.creer_zones("Z9", "TESTESP")):
            with self.assertRaises(frappe.PermissionError):
                appel()
