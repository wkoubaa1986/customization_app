"""Le paiement né d'une validation par le système va dans la caisse du technicien de la tâche (27/11/2026).

Cas d'origine : ACC-PAY-2026-08643, 50 DT de Ben Aissa Ahmed. Entretien fait par Radhouan le 25/11 au soir, saisi le
lendemain par Salma, commande validée par le bouton « ✅ Valider » de la fenêtre de clôture — donc sous
« Administrator », qui n'est la caisse de personne : aucune caisse ne comptait ces 50 DT.
"""
import datetime
import unittest

from customization_app import rapport_caisse_journaliere as R


class TestCaisseDuPaiement(unittest.TestCase):
    def test_regle_generale_celui_qui_a_saisi(self):
        """Une dette réglée au magasin, une avance prise à la commande pour une intervention à venir : l'argent est
        chez celui qui a saisi (sur novembre, 3 avances sur 6 paiements « tâche un autre jour » étaient dans ce cas)."""
        self.assertEqual(R.caisse_du_paiement("Salma Ben Saïd", None), "Salma Ben Saïd")
        self.assertEqual(R.caisse_du_paiement("Nejib Koubaa", {}), "Nejib Koubaa")

    def test_valide_par_le_systeme_va_au_technicien(self):
        self.assertEqual(R.caisse_du_paiement("Administrator", {"caisse": "Radhouan Sifi", "tache": "Tache-09575"}),
                         "Radhouan Sifi")

    def test_ne_de_la_validation(self):
        valide = datetime.datetime(2026, 11, 26, 9, 30, 15)
        self.assertTrue(R.ne_de_la_validation(datetime.datetime(2026, 11, 26, 9, 30, 14), valide))
        # Recréé plus tard (correction d'un bordereau, réémission) : ce n'est pas l'encaissement du technicien.
        self.assertFalse(R.ne_de_la_validation(datetime.datetime(2026, 11, 27, 12, 51, 0), valide))
        self.assertFalse(R.ne_de_la_validation(None, valide))
        self.assertFalse(R.ne_de_la_validation(valide, None))


if __name__ == "__main__":
    unittest.main()
