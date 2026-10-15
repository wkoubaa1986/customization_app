"""Qualification des réponses d'appel (15/10/2026) : lecture du JSON du modèle, date de rappel, effets sur la fiche,
réponses à choix du hook, SMS de réveil — règles pures."""
from __future__ import annotations

import datetime
import unittest

from customization_app import liste_appels as H, qualification_appels as Q
from customization_app.Maintenance import sms_reveil as S

TODAY = "2026-10-15"


class TestInterpretation(unittest.TestCase):
    def test_json_du_modele(self):
        q = Q.interpreter('{"categorie": "rappeler", "date": "2026-12-28", "motif": "rappel fin décembre"}', TODAY)
        self.assertEqual((q["categorie"], str(q["date"]), q["motif"]), ("rappeler", "2026-12-28", "rappel fin décembre"))
        self.assertEqual(Q.interpreter('{"categorie": "Fait lui-même"}', TODAY)["categorie"], "fait_lui_meme")
        self.assertEqual(Q.interpreter('{"categorie": "n_importe_quoi"}', TODAY)["categorie"], "autre")
        self.assertEqual(Q.interpreter("pas du json", TODAY), {"categorie": "autre", "date": None, "motif": ""})
        self.assertIsNone(Q.interpreter('{"categorie": "rappeler", "date": "2026-01-01"}', TODAY)["date"])   # date passée ignorée
        self.assertIsNone(Q.interpreter('{"categorie": "rappeler", "date": "bientôt"}', TODAY)["date"])

    def test_date_de_rappel_et_effet(self):
        self.assertEqual(str(Q.date_rappel("rappeler", None, TODAY)), "2027-01-15")                 # 3 mois par défaut
        self.assertEqual(str(Q.date_rappel("rappeler", datetime.date(2026, 12, 28), TODAY)), "2026-12-28")
        self.assertIsNone(Q.date_rappel("perdu", None, TODAY))
        self.assertEqual(Q.effet("perdu", "À requalifier"), "perdu")
        self.assertEqual(Q.effet("perdu", "Perdu"), "")                                             # déjà perdu : rien
        self.assertEqual(Q.effet("rappeler", ""), "rappeler")
        for cat in ("injoignable", "fait_lui_meme", "rendez_vous", "magasin", "autre"):
            self.assertEqual(Q.effet(cat, ""), "")                                                  # jamais de RDV ni de SMS décidé par le modèle
        self.assertEqual(Q.etiquette({"categorie": "rappeler", "date": datetime.date(2026, 12, 28), "motif": "fin décembre"}),
                         "rappeler (2026-12-28) : fin décembre")


class TestReponsesAChoix(unittest.TestCase):
    def test_effet_reponse(self):
        self.assertEqual(H.effet_reponse(H.PAS_DE_SYSTEME, "À requalifier"), {"custom_statut_relance": "Perdu"})
        self.assertEqual(H.effet_reponse(H.PAS_DE_SYSTEME, ""), {"custom_statut_relance": "Perdu"})
        self.assertEqual(H.effet_reponse(H.PAS_DE_SYSTEME, "Perdu"), {})
        self.assertEqual(H.effet_reponse("Rendez-vous pris", "À requalifier"), {"custom_statut_relance": ""})
        self.assertEqual(H.effet_reponse("Rendez-vous pris", ""), {})
        self.assertEqual(H.effet_reponse("Fait lui-même", "À requalifier"), {})
        self.assertEqual(H.effet_reponse("Autres reponses", "À requalifier"), {})                    # lue par l'IA le lendemain


class TestReveil(unittest.TestCase):
    def test_eligible(self):
        self.assertTrue(S.eligible("À requalifier", "Oui", "2025-10-01", None, TODAY))
        self.assertFalse(S.eligible("À requalifier", "Oui", "2026-06-01", None, TODAY))            # appel il y a 4 mois
        self.assertFalse(S.eligible("À requalifier", "Oui", "2025-10-01", "2026-01-01", TODAY))    # réveil il y a 9 mois
        self.assertTrue(S.eligible("À requalifier", "Oui", "2025-10-01", "2025-10-10", TODAY))     # réveil il y a plus d'un an
        self.assertFalse(S.eligible("Perdu", "Oui", "2025-10-01", None, TODAY))
        self.assertFalse(S.eligible("À requalifier", "Non", "2025-10-01", None, TODAY))
        self.assertFalse(S.eligible("À requalifier", "Oui", None, None, TODAY))                     # jamais de contact : pas un réveil

    def test_message(self):
        from customization_app.modeles_sms import DEFAUTS
        txt = S.message_reveil(DEFAUTS, "Ahmed", "votre osmoseur domestique", "https://x/rdv", "98511119")
        self.assertIn("plus d'un an que votre osmoseur domestique", txt)
        self.assertIn("https://x/rdv", txt)
        self.assertTrue(txt.endswith(DEFAUTS["signature"]))
        sans = S.message_reveil(DEFAUTS, "Ahmed", "votre osmoseur domestique", "", "98511119")
        self.assertNotIn("rdv", sans)
        self.assertIn("98511119", sans)
