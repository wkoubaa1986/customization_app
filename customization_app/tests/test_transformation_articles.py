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

    def test_article_nouveau_prend_le_reste_les_connus_gardent_leur_taux(self):
        """MAT-STE-2026-00171 : osmoseur jamais stocké (référence 0) + 2 coudes à 0,097 + raccord fixé
        à 6,279, pour 1 224,474 consommés. Avant : coudes à 609,097, osmoseur à 0."""
        taux = TA.repartir_valeur(1224.474, [
            {"qty": 1, "taux_fixe": None, "taux_ref": 0},
            {"qty": 2, "taux_fixe": None, "taux_ref": 0.097},
            {"qty": 1, "taux_fixe": 6.279, "taux_ref": 6.279},
        ])
        self.assertEqual(taux[1:], [0.097, 6.279])
        self.assertAlmostEqual(taux[0], 1224.474 - 2 * 0.097 - 6.279, places=3)
        # deux articles nouveaux : le reste à parts égales par unité
        taux = TA.repartir_valeur(100, [{"qty": 1, "taux_fixe": None, "taux_ref": 0},
                                        {"qty": 3, "taux_fixe": None, "taux_ref": 0},
                                        {"qty": 2, "taux_fixe": None, "taux_ref": 5}])
        self.assertEqual(taux, [22.5, 22.5, 5.0])
        # pas assez de valeur pour les lignes connues : prorata, l'inconnue au taux moyen connu
        taux = TA.repartir_valeur(10, [{"qty": 1, "taux_fixe": None, "taux_ref": 0},
                                       {"qty": 1, "taux_fixe": None, "taux_ref": 20}])
        self.assertEqual(taux, [5.0, 5.0])

    def test_alertes(self):
        self.assertEqual(TA.alertes_stock([{"item_code": "A", "qty": 2, "stock": 3}]), [])
        self.assertEqual(len(TA.alertes_stock([{"item_code": "A", "qty": 4, "stock": 3}])), 1)
        self.assertEqual(TA.ecart_taux(113.8, 46.6), 144.2)
        self.assertIsNone(TA.ecart_taux(10, 0))


class TestEntrepotParLigne(unittest.TestCase):
    """Règles pures de l'entrepôt par ligne (demande du 20/10/2026)."""

    def test_resoudre_entrepot(self):
        self.assertEqual(TA.resoudre_entrepot({"warehouse": "Hall - A&S"}, "Magasins - A&S", "X"), "Hall - A&S")
        self.assertEqual(TA.resoudre_entrepot({}, "Magasins - A&S", "X"), "Magasins - A&S")
        self.assertEqual(TA.resoudre_entrepot({"warehouse": "  "}, "", "X"), "X")
        self.assertEqual(TA.resoudre_entrepot({"warehouse": None}, None, "X"), "X")

    def test_options_entrepot_sans_doublon_et_sans_perte(self):
        self.assertEqual(TA.options_entrepot(["Magasins - A&S", "Hall - A&S"], "Magasins - A&S", "Stock Hedi - A&S"),
                         ["Magasins - A&S", "Hall - A&S", "Stock Hedi - A&S"])
        self.assertEqual(TA.options_entrepot([], "X", None), ["X"])


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
        # Entrepôt fixé sur chaque ligne : sans cela, un article partirait de sa sortie principale.
        conso = [{"item_code": self.a.item_code, "qty": 2, "warehouse": TA.MAGASIN_DEFAUT}]
        obt = [{"item_code": self.b.item_code, "qty": 1, "warehouse": TA.MAGASIN_DEFAUT}]
        sim = TA.simuler(conso, obt, TA.MAGASIN_DEFAUT)
        self.assertTrue(sim["valide"], sim["alertes"])
        self.assertAlmostEqual(sim["total_consomme"], sim["total_obtenu"], places=2)
        self.assertAlmostEqual(sim["obtenus"][0]["taux"], 2 * flt(self.a.valuation_rate), places=1)
        # stock insuffisant → refus
        self.assertFalse(TA.simuler([{"item_code": self.a.item_code, "qty": flt(self.a.actual_qty) + 1, "warehouse": TA.MAGASIN_DEFAUT}], obt)["valide"])

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


    def test_entrepot_par_ligne_et_sortie_principale(self):
        """b entre dans le Hall (choisi sur la ligne) ; puis, sans choix, dans sa sortie principale."""
        import frappe
        from frappe.utils import flt
        hall = "Hall - A&S"
        if not frappe.db.exists("Warehouse", hall):
            self.skipTest("pas de Hall sur ce site")
        conso = [{"item_code": self.a.item_code, "qty": 2, "warehouse": TA.MAGASIN_DEFAUT}]
        obt = [{"item_code": self.b.item_code, "qty": 1, "warehouse": hall}]
        sim = TA.simuler(conso, obt, TA.MAGASIN_DEFAUT)
        self.assertEqual((sim["consommes"][0]["warehouse"], sim["obtenus"][0]["warehouse"]), (TA.MAGASIN_DEFAUT, hall))
        # même article des deux côtés mais entrepôts différents : permis (c'est un transfert valorisé)
        sim2 = TA.simuler([{"item_code": self.a.item_code, "qty": 1, "warehouse": TA.MAGASIN_DEFAUT}],
                          [{"item_code": self.a.item_code, "qty": 1, "warehouse": hall}])
        self.assertTrue(sim2["valide"], sim2["alertes"])
        qb0 = flt(frappe.db.get_value("Bin", {"item_code": self.b.item_code, "warehouse": hall}, "actual_qty"))
        res = TA.valider(conso, obt, TA.MAGASIN_DEFAUT)
        se = frappe.get_doc("Stock Entry", res["name"])
        self.assertEqual([(d.s_warehouse, d.t_warehouse) for d in se.items], [(TA.MAGASIN_DEFAUT, None), (None, hall)])
        self.assertAlmostEqual(flt(frappe.db.get_value("Bin", {"item_code": self.b.item_code, "warehouse": hall}, "actual_qty")), qb0 + 1, places=3)
        self.assertEqual(TA.historique(1)[0]["warehouse"], f"{hall}, {TA.MAGASIN_DEFAUT}")
        TA.annuler(res["name"])
        # sortie principale : l'article b réglé sur le Hall y va tout seul, et la recherche le montre
        from customization_app.zones_magasin import _societe
        item = frappe.get_doc("Item", self.b.item_code)
        ligne = next((d for d in item.item_defaults if d.company == _societe()), None)
        if ligne:
            ligne.default_warehouse = hall
        else:
            item.append("item_defaults", {"company": _societe(), "default_warehouse": hall})
        item.flags.ignore_permissions = True
        item.save()
        sim3 = TA.simuler(conso, [{"item_code": self.b.item_code, "qty": 1}], TA.MAGASIN_DEFAUT)
        self.assertEqual((sim3["obtenus"][0]["warehouse"], sim3["obtenus"][0]["sortie"]), (hall, hall))
        trouve = next((r for r in TA.rechercher(self.b.item_code, TA.MAGASIN_DEFAUT) if r["item_code"] == self.b.item_code), None)
        self.assertIsNotNone(trouve)
        self.assertEqual((trouve["warehouse"], trouve["sortie"]), (hall, hall))
        # le choix explicite de la ligne prime sur la sortie principale
        sim4 = TA.simuler(conso, [{"item_code": self.b.item_code, "qty": 1, "warehouse": TA.MAGASIN_DEFAUT}], TA.MAGASIN_DEFAUT)
        self.assertEqual(sim4["obtenus"][0]["warehouse"], TA.MAGASIN_DEFAUT)


class TestModeles(unittest.TestCase):
    """Modèle unitaire enregistré puis appliqué × N."""

    def test_multiplier(self):
        self.assertEqual(TA.multiplier_lignes([{"item_code": "A", "qty": 1}, {"item_code": "B", "qty": 2.5, "warehouse": "Hall - A&S"}], 3),
                         [{"item_code": "A", "qty": 3.0, "warehouse": None}, {"item_code": "B", "qty": 7.5, "warehouse": "Hall - A&S"}])
        self.assertEqual(TA.multiplier_lignes([{"item_code": "A", "qty": 1}], 0), [{"item_code": "A", "qty": 1.0, "warehouse": None}])

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
            # l'entrepôt d'une ligne est gardé par le modèle, l'absence aussi
            if frappe.db.exists("Warehouse", "Hall - A&S"):
                nom2 = TA.enregistrer_modele("Essai modèle Hall", [{"item_code": a, "qty": 1, "warehouse": "Hall - A&S"}], [{"item_code": b, "qty": 1}])
                app2 = TA.appliquer_modele(nom2, 2)
                self.assertEqual((app2["consommes"][0]["warehouse"], app2["obtenus"][0]["warehouse"]), ("Hall - A&S", None))
                TA.supprimer_modele(nom2)
            # même nom = remplacé, pas dupliqué
            TA.enregistrer_modele("Essai modèle", [{"item_code": a, "qty": 1}], [{"item_code": b, "qty": 1}])
            self.assertEqual(sum(1 for x in TA.modeles() if x["nom"] == "Essai modèle"), 1)
            TA.supprimer_modele(nom)
            self.assertFalse(any(x["name"] == nom for x in TA.modeles()))
        finally:
            frappe.db.rollback(save_point="modele")
