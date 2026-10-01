"""Situation mensuelle : mêmes chiffres que le rapport « Test », omissions totales et partielles,
détail cohérent avec les totaux, droits. Tout est annulé (savepoint)."""
from __future__ import annotations

import unittest

from customization_app import situation_mensuelle as SM


class TestEquivalenceRapport(unittest.TestCase):
    def test_memes_chiffres_que_le_rapport_test(self):
        """Sans les omissions ajoutées depuis la page, les chiffres sont ceux du rapport « Test » au
        millime. Les omissions des utilisateurs sont mises de côté dans un savepoint, puis rétablies."""
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("equivalence")
        frappe.db.delete(SM.DOCTYPE_EXCLUSION, {"motif": ["not like", "Repris de l%"]})
        try:
            self._comparer()
        finally:
            frappe.db.rollback(save_point="equivalence")

    def _comparer(self):
        import frappe
        rep = frappe.get_doc("Report", "Test")
        cas = [({"mensuel": 1, "mois": m}, ("mois", m, None)) for m in SM.mois_disponibles()] + \
              [({"mensuel": 0, "annee": a}, ("annee", None, a)) for a in ("Total", "2023", "2024", "2025")]
        for f, (mode, mois, annee) in cas:
            attendu = {r["account_name"]: r["total_monthly"] for r in rep.execute_script_report(frappe._dict(f))[1]}
            c = SM.calculer(SM.periode(mode, mois, annee))
            for libelle, valeur in (("Total des ventes délivrées", c["ventes"]), ("Coût de la marchandise délivrée", c["cout"]["net"]),
                                    ("Total des charges", c["charges"]["net"]), ("TVA Achat", c["tva"]["net"]),
                                    ("Bénéfice / Perte", c["benefice"])):
                self.assertAlmostEqual(valeur, attendu[libelle], places=2, msg=f"{mois or annee} — {libelle}")


class TestDetail(unittest.TestCase):
    def test_detail_de_chaque_rubrique_concorde_avec_le_total(self):
        """Toutes les rubriques, plusieurs mois et une année : le détail se charge (le Coût de la
        marchandise plantait sur les Bons de livraison) et la somme des lignes = total compté."""
        import frappe
        frappe.set_user("Administrator")
        periodes = [("mois", m, None) for m in SM.mois_disponibles()[:3]] + [("annee", None, "2025"), ("annee", None, "Total")]
        for mode, mois, annee in periodes:
            for rub in ("Ventes", "Coût de la marchandise", "Charges", "TVA Achat"):
                det = SM.get_detail(rub, mode, mois, annee)
                somme = sum(l["montant"] for c in det["comptes"] for l in c["pieces"]) + sum(c["reste"]["montant"] for c in det["comptes"])
                self.assertAlmostEqual(somme, det["total"], places=2, msg=f"{rub} {mois or annee}")
                self.assertTrue(all(len(c["pieces"]) <= SM.LIGNES_PAR_COMPTE for c in det["comptes"]))
                if rub == "Coût de la marchandise" and det["comptes"]:
                    self.assertTrue(any(l["libelle"] for c in det["comptes"] for l in c["pieces"]), "libellés vides")


class TestMargesEtComparaison(unittest.TestCase):
    def test_ratios(self):
        r = SM.ratios(1000, 380, 340)
        self.assertAlmostEqual(r["marge"], 34.0)
        self.assertAlmostEqual(r["marge_brute"], 38.0)                 # définition utilisateur : coût / ventes
        self.assertAlmostEqual(r["marge_brute_usuelle"], 62.0)         # (ventes − coût) / ventes
        self.assertEqual(SM.ratios(0, 10, -10), {"marge": None, "marge_brute": None, "marge_brute_usuelle": None})

    def test_comparaison_des_annees(self):
        import frappe
        from frappe.utils import getdate, nowdate
        frappe.set_user("Administrator")
        r = SM.get_comparaison()
        annees = {a["annee"]: a for a in r["annees"]}
        self.assertEqual(list(annees)[0], "2023")
        self.assertEqual(annees["2023"]["mois"][:9], [None] * 9)                 # avant octobre 2023 : rien
        auj = getdate(nowdate())
        courante = annees[str(auj.year)]
        self.assertTrue(all(m is None for m in courante["mois"][auj.month:]))   # mois à venir : rien
        self.assertIsNotNone(courante["mois"][auj.month - 1])
        c = SM.calculer(SM.periode("annee", annee="2025"))
        self.assertAlmostEqual(annees["2025"]["total"]["benefice"], c["benefice"], places=2)
        sept = annees["2025"]["mois"][8]
        cs = SM.calculer(SM.periode("mois", "septembre 2025"))
        self.assertAlmostEqual(sept["ventes"], cs["ventes"], places=2)
        self.assertAlmostEqual(sept["marge"], 100 * cs["benefice"] / cs["ventes"], places=6)


class TestRecherche(unittest.TestCase):
    def test_recherche_retrouve_une_petite_piece_hors_liste(self):
        import frappe
        frappe.set_user("Administrator")
        det = SM.get_detail("Coût de la marchandise", "annee", None, "Total")
        compte = next((c for c in det["comptes"] if c["reste"]["n"]), None)
        if not compte:
            self.skipTest("aucun compte au-delà de la limite")
        cachees = {l["voucher_no"] for c in det["comptes"] for l in c["pieces"]}
        p = SM.periode("annee", annee="Total")
        petites = [vn for (vt, vn), v in SM._pieces("Coût de la marchandise", p["debut"], p["fin"]).items() if vn not in cachees]
        trouve = SM.get_detail("Coût de la marchandise", "annee", None, "Total", recherche=petites[0])
        self.assertIn(petites[0], [l["voucher_no"] for c in trouve["comptes"] for l in c["pieces"]])
        self.assertAlmostEqual(trouve["total"], det["total"], places=2)                 # le total ne dépend pas du filtre


class TestOmissions(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("situation")
        # le mois complet le plus récent qui a des charges
        self.mois = SM.mois_disponibles()[1]
        self.p = SM.periode("mois", self.mois)
        pieces = SM._pieces("Charges", self.p["debut"], self.p["fin"])
        libres = [(k, v) for k, v in pieces.items() if v > 100 and not SM._exclusions("Charges", {k: v})]
        if not libres:
            self.skipTest("aucune pièce de charge libre ce mois-là")
        (self.vt, self.vn), self.montant = max(libres, key=lambda x: x[1])

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="situation")

    def test_totale_partielle_retablir(self):
        import frappe
        avant = SM.calculer(self.p)
        SM.omettre("Charges", self.vt, self.vn, "Partielle", 100, "test partiel")
        apres = SM.calculer(self.p)
        self.assertAlmostEqual(apres["charges"]["net"], avant["charges"]["net"] - 100, places=3)
        self.assertAlmostEqual(apres["benefice"], avant["benefice"] + 100, places=3)
        with self.assertRaises(frappe.ValidationError):
            SM.omettre("Charges", self.vt, self.vn, "Partielle", self.montant + 1, "trop")
        with self.assertRaises(frappe.ValidationError):
            SM.omettre("Charges", self.vt, self.vn, "Totale", None, "  ")          # motif obligatoire
        SM.omettre("Charges", self.vt, self.vn, "Totale", None, "test total")     # met à jour, pas de doublon
        self.assertEqual(frappe.db.count(SM.DOCTYPE_EXCLUSION, {"voucher_no": self.vn, "rubrique": "Charges"}), 1)
        apres = SM.calculer(self.p)
        self.assertAlmostEqual(apres["charges"]["net"], avant["charges"]["net"] - self.montant, places=3)
        # le détail montre la pièce omise et ses totaux concordent
        det = SM.get_detail("Charges", "mois", self.mois, recherche=self.vn)
        lignes = [l for c in det["comptes"] for l in c["pieces"]]
        self.assertAlmostEqual(det["net"], apres["charges"]["net"], places=3)
        self.assertTrue(any(l["voucher_no"] == self.vn and l["omission"] for l in lignes))
        self.assertIn(self.vn, [o.voucher_no for o in SM.get_omissions("mois", self.mois)["omissions"]])
        nom = frappe.db.get_value(SM.DOCTYPE_EXCLUSION, {"voucher_no": self.vn, "rubrique": "Charges"})
        SM.retablir(nom)
        self.assertAlmostEqual(SM.calculer(self.p)["charges"]["net"], avant["charges"]["net"], places=3)
        with self.assertRaises(frappe.ValidationError):
            SM.omettre("TVA Achat", "Journal Entry", "ACC-JV-INEXISTANTE", "Totale", None, "x")

    def test_omettre_reserve_au_role_banque(self):
        import frappe
        from unittest import mock
        # Un comptable sans le rôle Banque. On simule ses rôles : créer un vrai User réenregistre son
        # Contact, et le Server Script « update_list_tel » plante sur un contact sans client.
        with mock.patch.object(SM.frappe, "get_roles", return_value=["Accounts User", "Employee"]):
            r = SM.get_situation("mois", self.mois)
            self.assertFalse(r["peut_omettre"])
            self.assertIn("ventes", r["calcul"])                                # il lit
            with self.assertRaises(frappe.PermissionError):
                SM.omettre("Charges", self.vt, self.vn, "Totale", None, "interdit")
        with mock.patch.object(SM.frappe, "get_roles", return_value=["Employee"]):
            with self.assertRaises(frappe.PermissionError):
                SM.get_situation("mois", self.mois)                             # hors comptabilité : rien
