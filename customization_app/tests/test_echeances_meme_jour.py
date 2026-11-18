"""Plusieurs paiements le même jour sur un document de vente (customization_app.echeances_meme_jour) :
le refus d'ERPNext des dates en double est levé, plus aucune échéance n'est décalée. Pur + branchements."""
from __future__ import annotations

import os
import unittest
from datetime import date

from customization_app import echeances_meme_jour as E
from customization_app.patches import echeances_meme_jour as P

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDatesRefusees(unittest.TestCase):
    JOUR = date(2026, 11, 18)

    def test_deux_paiements_le_meme_jour_ne_sont_pas_une_erreur(self):
        lignes = [{"idx": 1, "due_date": self.JOUR}, {"idx": 2, "due_date": self.JOUR}, {"idx": 3, "due_date": self.JOUR}]
        self.assertEqual(E.dates_refusees("Sales Order", self.JOUR, lignes), [])

    def test_une_echeance_avant_la_date_de_la_commande_reste_refusee(self):
        lignes = [{"idx": 1, "due_date": self.JOUR}, {"idx": 2, "due_date": date(2026, 11, 17)}]
        self.assertEqual(E.dates_refusees("Sales Order", self.JOUR, lignes), [2])
        self.assertEqual(E.dates_refusees("Quotation", self.JOUR, lignes), [2])

    def test_la_facture_n_a_pas_ce_refus(self):
        """Comme dans ERPNext : seuls la commande et le devis refusent une échéance antérieure à leur date."""
        self.assertEqual(E.dates_refusees("Sales Invoice", self.JOUR, [{"idx": 1, "due_date": date(2026, 11, 1)}]), [])

    def test_dates_en_texte(self):
        self.assertEqual(E.dates_refusees("Sales Order", "2026-11-18", [{"idx": 1, "due_date": "2026-11-18"},
                                                                         {"idx": 2, "due_date": "2026-11-10"}]), [2])


class TestPerimetre(unittest.TestCase):
    def test_seuls_les_documents_de_vente_sont_concernes(self):
        self.assertEqual(E.DOCUMENTS_DE_VENTE, ("Sales Order", "Sales Invoice", "Quotation"))

    def test_la_surcharge_est_appliquee_au_chargement_de_l_application(self):
        with open(os.path.join(RACINE, "__init__.py"), encoding="utf-8") as f:
            init = f.read()
        self.assertIn("from customization_app.echeances_meme_jour import appliquer", init)
        self.assertIn("_apply_echeances_meme_jour()", init)


class TestPlusDeDecalage(unittest.TestCase):
    def test_la_ligne_d_avoir_du_formulaire_ne_cherche_plus_de_date_libre(self):
        script = "var jour = x;\r\n    while (prises.indexOf(jour) !== -1) {\r\n        jour = add(jour, 1);\r\n    }\r\n"
        nouveau, fait = P.corriger(script)
        self.assertEqual(fait, "modifié")
        self.assertIn("while (false) {", nouveau)
        self.assertEqual(nouveau.count("\r\n"), script.count("\r\n"))            # fins de ligne intactes
        self.assertEqual(P.corriger(nouveau), (nouveau, "déjà fait"))
        self.assertEqual(P.corriger("autre script")[1], "jeton introuvable")

    def test_le_bloc_de_paiement_ne_decale_plus_et_ne_montre_plus_l_echeance(self):
        with open(os.path.join(RACINE, "public", "js", "sales_order_paiement.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("due_date: jourDuPaiement(frm)", js)
        self.assertNotIn("add_days", js)
        self.assertNotIn("sp-in-date", js)

    def test_avoir_et_sous_traitant_gardent_le_jour(self):
        from customization_app.avoir_client import date_echeance_libre
        self.assertEqual(date_echeance_libre([date(2026, 9, 5)], date(2026, 9, 5)), date(2026, 9, 5))
        with open(os.path.join(RACINE, "sous_traitant.py"), encoding="utf-8") as f:
            self.assertNotIn("add_days(getdate(jour), len(lignes))", f.read())


if __name__ == "__main__":
    unittest.main()
