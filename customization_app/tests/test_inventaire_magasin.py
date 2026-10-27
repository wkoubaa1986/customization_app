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

    def test_perimetres(self):
        self.assertEqual(I.libelle_perimetre([]), "Tout le Magasin")
        self.assertEqual(I.libelle_perimetre(["Magasin - A1", I.SANS_ZONE], {"Magasin - A1": "A1"}), "A1, Non rangé")
        self.assertTrue(I.perimetres_se_recoupent([], ["Magasin - A1"]))              # un complet exclut tout
        self.assertTrue(I.perimetres_se_recoupent(["Magasin - A1"], []))
        self.assertTrue(I.perimetres_se_recoupent(["Magasin - A1", "Magasin - A2"], ["Magasin - A2"]))
        self.assertFalse(I.perimetres_se_recoupent(["Magasin - A1"], ["Magasin - B1", I.SANS_ZONE]))

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
        self.espace = I.espace()
        self.zone = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace}, "name")
        if not self.zone:
            self.skipTest("aucune zone dans l'espace de l'inventaire")

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
        self.assertEqual(l1["zones"], [z for z in Z.zones_de(i1) if z == self.espace or z.startswith(self.espace + " - ")])
        self.assertTrue(all(z["name"] == self.espace or z["espace"] == self.espace for z in d["zones"]))   # jamais le Hall
        self.assertIn("image", l1)
        self.assertTrue(d["zones"])
        # i1 : compté en deux emplacements (dont un nouveau), somme = stock − 2 ; i2 : compté juste ; i3 : pas compté.
        nouvelle = self.zone if self.zone not in Z.zones_de(i1) else frappe.db.get_value(
            Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace, "name": ["not in", Z.zones_de(i1)]}, "name")
        hall = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": ["!=", self.espace]}, "name")
        # « ＋ » : l'emplacement est AUSSITÔT sur la fiche article (demande du 27/10) ; une zone du Hall est refusée.
        with self.assertRaises(frappe.ValidationError):
            I.ajouter_emplacement(name, i1, hall)
        r = I.ajouter_emplacement(name, i1, nouvelle)
        self.assertIn(nouvelle, Z.zones_de(i1))
        self.assertEqual(r["zones_ajoutees"], [nouvelle])
        self.assertIn(nouvelle, self._ligne(I.detail_inventaire(name), i1)["zones"])
        r = I.enregistrer_inventaire(name, json.dumps({
            i1: {"cases": {I.SANS_ZONE: s1 - 3, nouvelle: 1, hall: 7}},      # une case du Hall est ignorée
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
        self.assertIn(nouvelle, Z.zones_de(i1))                            # toujours sur l'article
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

    def test_inventaire_partiel_par_zone(self):
        """Un périmètre = des zones : feuille limitée, inventaires parallèles sans recoupement, un complet exclu
        tant qu'un partiel est ouvert, historique par zone après clôture."""
        import frappe
        i1, i2 = self.items[0], self.items[1]
        s1 = self._stock(i1)
        z1 = self.zone
        z2 = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace, "name": ["!=", z1]}, "name")
        if Z._ajouter(i1, z1):
            Z._resynchroniser(i1)
        for z in Z.zones_de(i2):                                            # i2 hors de z1 pour le test
            if z == z1:
                self.skipTest("l'article témoin est rangé dans la zone d'essai")
        a = I.ouvrir_inventaire(json.dumps([z1]))
        self.assertEqual(I.ouvrir_inventaire([z1]), a)                      # même périmètre = repris
        d = I.detail_inventaire(a)
        self.assertEqual((d["fiche"]["perimetre"], d["fiche"]["statut"]), ([z1], I.EN_COURS))
        codes = {l["item_code"] for l in d["lignes"]}
        self.assertIn(i1, codes)
        self.assertNotIn(i2, codes)
        self.assertTrue(all(z1 in l["zones"] for l in d["lignes"]))
        with self.assertRaises(frappe.ValidationError):                     # recoupement : un complet est refusé
            I.ouvrir_inventaire()
        with self.assertRaises(frappe.ValidationError):
            I.ouvrir_inventaire([z1, I.SANS_ZONE])
        with self.assertRaises(frappe.ValidationError):                     # zone inconnue
            I.ouvrir_inventaire(["Hall - Nulle part"])
        b = I.ouvrir_inventaire([I.SANS_ZONE] + ([z2] if z2 else []))      # disjoint : ouvert en parallèle
        self.assertNotEqual(a, b)
        etat = I.inventaires_etat()
        self.assertEqual([v["name"] for v in etat["ouverts"]], [a, b])
        hz = {z["name"]: z for z in etat["zones_historique"]}
        self.assertEqual((hz[z1]["en_cours"], hz[I.SANS_ZONE]["en_cours"]), (a, b))
        self.assertTrue(all(i2 != l["item_code"] or not l["zones"] for l in I.detail_inventaire(b)["lignes"]))
        I.enregistrer_inventaire(a, json.dumps({i1: {"cases": {z1: s1 - 1}}}))
        r = I.cloturer_inventaire(a)
        self.assertEqual((r["nb_rapproches"], self._stock(i1)), (1, s1 - 1))
        etat = I.inventaires_etat()
        hz = {z["name"]: z for z in etat["zones_historique"]}
        self.assertEqual((hz[z1]["en_cours"], hz[z1]["dernier_inventaire"]), (None, a))
        self.assertEqual([v["name"] for v in etat["ouverts"]], [b])
        with self.assertRaises(frappe.ValidationError):                     # b toujours ouvert : pas de complet
            I.ouvrir_inventaire()
        I.annuler_inventaire(b)
        c = I.ouvrir_inventaire()
        self.assertEqual(I.detail_inventaire(c)["fiche"]["perimetre"], [])
        self.assertIn(i2, {l["item_code"] for l in I.detail_inventaire(c)["lignes"]})

    def test_taux_saisi_a_la_cloture(self):
        """Un article sans aucun taux est exclu ; un taux saisi par un responsable l'inclut et prime sur
        l'automatique ; l'écriture porte ce taux ; vide = retour à l'automatique."""
        import frappe
        i1, i2 = self.items[0], self.items[1]
        s1, s2 = self._stock(i1), self._stock(i2)
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: s1 - 2}}, i2: {"cases": {I.SANS_ZONE: s2 + 1}}}))
        v = frappe.get_doc(I.INV, name)
        frappe.db.set_value("Inventaire Magasin Ligne", I._ligne(v, i1).name, "taux", 0)     # plus aucun taux nulle part
        frappe.db.set_value("Item", i1, {"valuation_rate": 0, "last_purchase_rate": 0})
        b = I.bilan_cloture(name)
        self.assertEqual([x["item_code"] for x in b["sans_taux"]], [i1])
        self.assertEqual([x["item_code"] for x in b["a_rapprocher"]], [i2])
        taux_auto_i2 = b["a_rapprocher"][0]["taux"]
        self.assertGreater(taux_auto_i2, 0)
        with self.assertRaises(frappe.ValidationError):
            I.definir_taux(name, i1, -1)
        r = I.definir_taux(name, i1, 12.5)
        self.assertEqual((r["taux"], r["taux_saisi"], r["valeur_ecart"]), (12.5, 12.5, -25))
        I.definir_taux(name, i2, 7)                                        # prime sur le taux photographié
        b = I.bilan_cloture(name)
        self.assertFalse(b["sans_taux"])
        self.assertEqual({x["item_code"]: x["taux"] for x in b["a_rapprocher"]}, {i1: 12.5, i2: 7})
        self.assertEqual(self._ligne(I.detail_inventaire(name), i2)["taux_saisi"], 7)
        I.definir_taux(name, i2, None)                                     # vide = automatique
        b = I.bilan_cloture(name)
        self.assertEqual({x["item_code"]: x["taux"] for x in b["a_rapprocher"]}, {i1: 12.5, i2: taux_auto_i2})
        r = I.cloturer_inventaire(name)
        self.assertEqual((r["nb_rapproches"], r["nb_sans_taux"]), (2, 0))
        taux = {x.item_code: S.flt(x.valuation_rate, 3) for x in frappe.get_doc("Stock Reconciliation", r["rapprochement"]).items}
        self.assertEqual(taux, {i1: 12.5, i2: taux_auto_i2})
        self.assertEqual((self._stock(i1), self._stock(i2)), (s1 - 2, s2 + 1))

    def test_retirer_un_emplacement_ajoute_par_erreur(self):
        import frappe
        i1 = self.items[0]
        name = I.ouvrir_inventaire()
        zone = self.zone if self.zone not in Z.zones_de(i1) else frappe.db.get_value(
            Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace, "name": ["not in", Z.zones_de(i1)]}, "name")
        avant = Z.zones_de(i1)
        I.ajouter_emplacement(name, i1, zone)
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {zone: 4}}}))
        r = I.retirer_emplacement(name, i1, zone)
        self.assertEqual((Z.zones_de(i1), r["zones_ajoutees"]), (avant, []))
        self.assertIsNone(self._ligne(I.detail_inventaire(name), i1)["qte_comptee"])   # sa case est effacée
        if avant:                                                          # un emplacement d'origine ne se retire pas ici
            with self.assertRaises(frappe.ValidationError):
                I.retirer_emplacement(name, i1, avant[0])

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
        for fn in (I.ouvrir_inventaire, I.enregistrer_inventaire, I.cloturer_inventaire, I.annuler_inventaire,
                   I.ajouter_emplacement, I.retirer_emplacement):
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func.get(fn), ["POST"], fn.__name__)
