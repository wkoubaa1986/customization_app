"""Inventaire du Magasin (bouton de la page « Stock par entrepôt ») : feuille par emplacement, enregistrement
partiel, clôture = une Stock Reconciliation sur le Magasin. Vrais articles dans un savepoint : tout est annulé."""
from __future__ import annotations

import json
import unittest

from customization_app import inventaire_magasin as I
from customization_app import stock_entrepots as S
from customization_app import zones_magasin as Z
from customization_app.tests.test_stock_entrepots import TRAJETS_DEFAUT, entrepot_essai, fin_de_test, regler_trajets

NEJIB = "aquaworld.servicing@gmail.com"
EMPLOYE_SANS_ROLE = "morchediakram0@gmail.com"     # Akram : Stock User, pas le rôle Inventaire Magasin


class TestCalculPur(unittest.TestCase):
    def test_cases_valides_et_somme(self):
        cases = I.cases_valides({"Magasin - A1": "3", "Hall - C2": 2.5, "Magasin - Z9": 1, I.SANS_ZONE: "", "": 4},
                                {"Magasin - A1", "Hall - C2"})
        self.assertEqual(cases, {"Magasin - A1": 3.0, "Hall - C2": 2.5})      # zone inconnue et vide ignorées
        self.assertEqual(I.sommer_cases(cases), 5.5)
        self.assertEqual(I.cases_valides({"Magasin - A1": -2}), {"Magasin - A1": 0})   # jamais négatif
        self.assertEqual(I.cases_valides({I.SANS_ZONE: 0}, set()), {I.SANS_ZONE: 0.0})   # compté 0 = compté

    def test_quantite_rapprochee(self):
        self.assertEqual(I.quantite_rapprochee(12, 10, 12), 10)       # rien n'a bougé : le compté
        self.assertEqual(I.quantite_rapprochee(12, 10, 9), 7)         # 3 vendus depuis : l'écart −2 reste
        self.assertEqual(I.quantite_rapprochee(5, 0, 2), 0)           # jamais négatif


class TestInventaire(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("inventaire_magasin")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        self.magasin = S.magasin()
        regler_trajets(TRAJETS_DEFAUT + [("Magasin", "Autre entrepôt", 0), ("Autre entrepôt", "Magasin", 0)])
        self.essai = entrepot_essai("ESSAI INV")
        self.items = frappe.db.sql_list("""select b.item_code from tabBin b join tabItem i on i.name = b.item_code
                                           where b.warehouse = %s and b.actual_qty > 10 and b.valuation_rate > 0
                                             and i.disabled = 0 and i.is_stock_item = 1 and i.has_variants = 0
                                           order by b.item_code limit 3""", self.magasin)
        if len(self.items) < 3:
            self.skipTest("pas assez d'articles en stock au Magasin")
        for n in frappe.get_all(I.INV, filters={"statut": I.EN_COURS}, pluck="name"):   # un seul ouvert à la fois
            frappe.db.set_value(I.INV, n, "statut", I.ANNULE)
        self.zone = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0}, "name")
        if not self.zone:
            self.skipTest("aucune zone de magasin")

    def tearDown(self):
        fin_de_test("inventaire_magasin")

    def _stock(self, item):
        import frappe
        return S.flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": self.magasin}, "actual_qty"), 6)

    def _ligne(self, d, item):
        return next(l for l in d["lignes"] if l["item_code"] == item)

    def test_acces(self):
        import frappe
        frappe.set_user(EMPLOYE_SANS_ROLE)
        self.assertNotIn(I.ROLE, frappe.get_roles())
        with self.assertRaises(frappe.PermissionError):
            I.inventaires_etat()
        self.assertFalse(S.get_context()["inventaire"])
        frappe.set_user(NEJIB)
        if I.ROLE in frappe.get_roles():                  # rôle posé par le patch attribuer_role_inventaire_magasin
            self.assertTrue(S.get_context()["inventaire"])
            self.assertIn("en_cours", I.inventaires_etat())

    def test_feuille_par_emplacement_puis_cloture(self):
        import frappe
        i1, i2, i3 = self.items
        s1, s2, s3 = self._stock(i1), self._stock(i2), self._stock(i3)
        name = I.ouvrir_inventaire()
        self.assertEqual(I.ouvrir_inventaire(), name)                    # un seul inventaire ouvert à la fois
        d = I.detail_inventaire(name)
        self.assertEqual(d["fiche"]["statut"], I.EN_COURS)
        l1 = self._ligne(d, i1)
        self.assertEqual(l1["qte_systeme"], s1)
        self.assertIsNone(l1["qte_comptee"])
        self.assertEqual(l1["zones"], Z.zones_de(i1))                    # une case par emplacement de l'article
        self.assertIn("image", l1)
        self.assertTrue(d["zones"])
        # i1 : compté en deux emplacements (dont un nouveau), somme = stock − 2 ; i2 : compté juste ; i3 : pas compté.
        nouvelle = self.zone if self.zone not in Z.zones_de(i1) else frappe.db.get_value(
            Z.DOCTYPE_ZONE, {"est_espace": 0, "name": ["not in", Z.zones_de(i1)]}, "name")
        r = I.enregistrer_inventaire(name, json.dumps({
            i1: {"cases": {I.SANS_ZONE: s1 - 3, nouvelle: 1}},
            i2: {"cases": {I.SANS_ZONE: s2}, "commentaire": "ras"}}))
        self.assertEqual((r["nb_comptes"], r["nb_ecarts"]), (2, 1))
        d = I.detail_inventaire(name)
        l1, l2, l3 = self._ligne(d, i1), self._ligne(d, i2), self._ligne(d, i3)
        self.assertEqual((l1["qte_comptee"], l1["ecart"]), (s1 - 2, -2))
        self.assertEqual(l1["cases"], {I.SANS_ZONE: s1 - 3, nouvelle: 1})
        self.assertEqual((l2["qte_comptee"], l2["ecart"], l2["commentaire"]), (s2, 0, "ras"))
        self.assertIsNone(l3["qte_comptee"])
        # Une ligne renvoyée sans case redevient « non comptée » ; les lignes absentes de l'envoi ne bougent pas.
        I.enregistrer_inventaire(name, json.dumps({i2: {"cases": {I.SANS_ZONE: None}}}))
        d = I.detail_inventaire(name)
        self.assertIsNone(self._ligne(d, i2)["qte_comptee"])
        self.assertEqual(self._ligne(d, i1)["qte_comptee"], s1 - 2)
        b = I.bilan_cloture(name)
        self.assertEqual((b["nb_comptes"], b["nb_non_comptes"], b["nb_ecarts"]), (1, len(d["lignes"]) - 1, 1))
        self.assertEqual([x["item_code"] for x in b["a_rapprocher"]], [i1])
        self.assertEqual(b["zones_nouvelles"], [{"item_code": i1, "item_name": l1["item_name"], "zones": [nouvelle]}])
        r = I.cloturer_inventaire(name, note="essai")
        self.assertTrue(r["rapprochement"])
        self.assertEqual((r["nb_rapproches"], r["nb_zones_ajoutees"]), (1, 1))
        self.assertEqual(frappe.db.get_value("Stock Reconciliation", r["rapprochement"], "docstatus"), 1)
        self.assertEqual(self._stock(i1), s1 - 2)                          # le Magasin est aligné sur le compté
        self.assertEqual((self._stock(i2), self._stock(i3)), (s2, s3))     # les non comptés ne bougent pas
        self.assertIn(nouvelle, Z.zones_de(i1))                            # emplacement découvert → sur l'article
        v = frappe.get_doc(I.INV, name)
        self.assertEqual((v.statut, v.note, v.rapprochement), (I.TERMINE, "essai", r["rapprochement"]))
        self.assertIsNone(I.en_cours())
        with self.assertRaises(frappe.ValidationError):
            I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: 1}}}))

    def test_un_article_qui_bouge_apres_son_comptage(self):
        """Compté 10 quand le système disait 12, puis 3 sortis : le rapproché est 9 − 2 = 7, pas 10."""
        import frappe
        i1 = self.items[0]
        s1 = self._stock(i1)
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: s1 - 2}}}))
        r = S.creer_transfert(self.magasin, self.essai, json.dumps([{"item_code": i1, "qte": 3}]))
        self.assertFalse(r.get("en_attente"))
        self.assertEqual(self._stock(i1), s1 - 3)
        d = I.detail_inventaire(name)
        l1 = self._ligne(d, i1)
        self.assertEqual((l1["qte_systeme"], l1["qte_actuelle"], l1["bouge"]), (s1, s1 - 3, True))
        b = I.bilan_cloture(name)
        self.assertEqual([(x["item_code"], x["qte_rapprochee"]) for x in b["bouges"]], [(i1, s1 - 5)])
        r = I.cloturer_inventaire(name)
        self.assertEqual((r["nb_bouges"], self._stock(i1)), (1, s1 - 5))

    def test_annuler_ne_bouge_rien(self):
        import frappe
        i1 = self.items[0]
        s1 = self._stock(i1)
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: 0}}}))
        r = I.annuler_inventaire(name, motif="test")
        self.assertEqual(r["statut"], I.ANNULE)
        self.assertEqual(self._stock(i1), s1)
        self.assertFalse(frappe.db.get_value(I.INV, name, "rapprochement"))
        self.assertIsNone(I.en_cours())

    def test_ecritures_en_post_seulement(self):
        import frappe
        for fn in (I.ouvrir_inventaire, I.enregistrer_inventaire, I.cloturer_inventaire, I.annuler_inventaire):
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func.get(fn), ["POST"], fn.__name__)
