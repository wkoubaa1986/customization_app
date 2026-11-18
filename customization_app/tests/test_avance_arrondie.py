"""L'avoir d'une commande n'est plus refusé pour un écart de virgule flottante (customization_app.avance_arrondie)."""
from __future__ import annotations

import os
import unittest

from customization_app import avance_arrondie as A

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTolerance(unittest.TestCase):
    def test_le_cas_reel_du_18_11_2026(self):
        """Espèces 50 + chèque 40 + carte 116,133, puis 78 d'avoir sur un total de 284,133."""
        total, avance, avoir = 284.133, 206.133, 78.0
        self.assertTrue(total < avance + avoir)                                    # le refus d'ERPNext, à tort
        self.assertFalse(total < avance + A.totaux_toleres({"C1": avoir})["C1"])   # avec la tolérance : accepté

    def test_un_vrai_depassement_reste_refuse(self):
        total, avance = 284.133, 206.133
        self.assertTrue(total < avance + A.totaux_toleres({"C1": 78.001})["C1"])   # un millime de trop
        self.assertTrue(total < avance + A.totaux_toleres({"C1": 80.0})["C1"])

    def test_la_tolerance_est_sous_le_millime(self):
        self.assertLess(A.TOLERANCE, 0.001)

    def test_totaux_vides(self):
        self.assertEqual(A.totaux_toleres(None), {})
        self.assertEqual(A.totaux_toleres({}), {})


class TestBranchement(unittest.TestCase):
    def test_la_surcharge_est_appliquee_au_chargement_de_l_application(self):
        with open(os.path.join(RACINE, "__init__.py"), encoding="utf-8") as f:
            init = f.read()
        self.assertIn("from customization_app.avance_arrondie import appliquer", init)
        self.assertIn("\n_apply_avance_arrondie()", init)

    def test_la_surcharge_enveloppe_le_controle_et_rend_les_totaux(self):
        try:
            from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
        except Exception:
            self.skipTest("erpnext absent")
        self.assertTrue(A.appliquer())
        origine = getattr(JournalEntry, A.MARQUE)
        self.assertIsNot(JournalEntry.validate_orders, origine)
        self.assertTrue(A.appliquer())                                             # idempotent
        self.assertIs(getattr(JournalEntry, A.MARQUE), origine)

        class Faux:
            """Une écriture qui ne vise aucune commande : le contrôle d'origine la parcourt sans rien lire."""
            reference_totals = {"F1": 78.0}
            reference_types = {"F1": "Sales Invoice"}
            reference_accounts = {"F1": "Débiteurs"}

        faux, totaux = Faux(), Faux.reference_totals
        JournalEntry.validate_orders(faux)
        self.assertIs(faux.reference_totals, totaux)                               # l'écriture retrouve ses totaux
        self.assertEqual(totaux, {"F1": 78.0})


if __name__ == "__main__":
    unittest.main()
