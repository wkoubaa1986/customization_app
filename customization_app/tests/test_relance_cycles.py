"""Un seul cycle de relance SMS par article (02/11/2026) et marquage de l'appel sur la bonne visite."""
import unittest

from customization_app.Maintenance.relance_maintenance_sms import choisir_relances
from customization_app.liste_appels import ligne_a_marquer

T = "2026-10-24"


def L(date, s1=None, s2=None, appel=None, fait=None, item="AP-6-DF"):
    return {"item_code": item, "scheduled_date": date, "custom_sms_1": s1, "custom_sms_2": s2, "custom_appelle": appel,
            "actual_date": fait, "completion_status": "Fully Completed" if fait else "Pending"}


class TestChoisirRelances(unittest.TestCase):
    def test_sms1_sur_la_derniere_echue_seulement(self):
        sms1, sms2 = choisir_relances([L("2026-04-23"), L("2026-10-22"), L("2027-04-22")], T, 7, 60)
        self.assertEqual((sms1, sms2), ({"AP-6-DF": __import__("datetime").date(2026, 10, 22)}, {}))

    def test_pas_de_nouveau_cycle_sur_une_ancienne_echue(self):
        # La dernière échue (16/09) a eu son cycle ; les échues plus anciennes ne relancent plus (cas MAT-MSH-2023-00602).
        sms1, sms2 = choisir_relances([L("2025-09-17"), L("2026-03-18"), L("2026-09-16", s1="2026-10-16", s2="2026-10-23")], T, 7, 60)
        self.assertEqual((sms1, sms2), ({}, {}))

    def test_lassitude_bloque_le_sms1_apres_un_sms_ou_un_appel_recent(self):
        # Ali Jlassi : avril relancée le 16/10 et appelée le 26/10 ; la visite d'octobre attend 60 jours.
        lignes = [L("2026-04-23", s1="2026-10-16", s2="2026-10-23", appel="2026-10-26"), L("2026-10-22")]
        self.assertEqual(choisir_relances(lignes, "2026-10-27", 7, 60), ({}, {}))
        self.assertEqual(choisir_relances(lignes, "2026-12-26", 7, 60)[0], {"AP-6-DF": __import__("datetime").date(2026, 10, 22)})

    def test_sms2_apres_le_delai(self):
        lignes = [L("2026-10-22", s1="2026-10-23")]
        self.assertEqual(choisir_relances(lignes, "2026-10-29", 7, 60)[1], {})
        self.assertEqual(choisir_relances(lignes, "2026-10-30", 7, 60)[1], {"AP-6-DF": __import__("datetime").date(2026, 10, 22)})

    def test_visite_realisee_et_futur_ignores(self):
        self.assertEqual(choisir_relances([L("2026-10-22", fait="2026-10-23"), L("2027-04-22")], T, 7, 60), ({}, {}))
        self.assertEqual(choisir_relances([], T, 7, 60), ({}, {}))


class TestLigneAMarquer(unittest.TestCase):
    def test_date_exacte_sinon_derniere_echue(self):
        rows = [{"name": "a", "scheduled_date": "2026-04-23"}, {"name": "b", "scheduled_date": "2026-10-22"}, {"name": "c", "scheduled_date": "2027-04-22"}]
        self.assertEqual(ligne_a_marquer(rows, "2026-04-23", T)["name"], "a")
        self.assertEqual(ligne_a_marquer(rows, "2026-04-25", T)["name"], "b")       # date décalée → dernière échue
        self.assertIsNone(ligne_a_marquer([{"name": "c", "scheduled_date": "2027-04-22"}], "2026-04-25", T))
        self.assertIsNone(ligne_a_marquer([{"name": "b", "scheduled_date": "2026-10-22", "actual_date": "2026-10-23"}], "2026-04-25", T))
