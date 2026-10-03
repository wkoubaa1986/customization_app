"""Listes d'appels (revue 03/10/2026) : regroupement par client, règle des 160 jours, répartition, tri des secteurs — règles pures."""
from __future__ import annotations

import datetime
import unittest

from customization_app import relances_config as RC
from customization_app.Maintenance import creation_liste_appelle as L

CFG = dict(RC.DEFAUTS, **{k + "_liste": RC.liste(v) for k, v in RC.DEFAUTS.items() if isinstance(v, str) and "\n" in v})
D = datetime.date


class TestRegroupement(unittest.TestCase):
    def test_un_client_une_entree_et_regle_160_jours(self):
        lignes = [{"customer": "C1", "schedule": "MS1", "item_code": "A", "scheduled_date": D(2026, 8, 1), "secteurs": "Secteur 2"},
                  {"customer": "C1", "schedule": "MS2", "item_code": "B", "scheduled_date": D(2026, 6, 1), "secteurs": "Secteur 2"},
                  {"customer": "C2", "schedule": "MS3", "item_code": "A", "scheduled_date": D(2026, 7, 1), "secteurs": "Hors Secteur"},
                  {"customer": "C3", "schedule": "MS4", "item_code": "A", "scheduled_date": D(2026, 7, 1), "secteurs": "Secteur 1"}]
        dernieres = {("MS3", "A"): D(2026, 9, 1),      # intervention il y a 1 mois → C2 écarté
                     ("MS4", "A"): D(2026, 1, 1)}      # il y a 9 mois → C3 gardé
        r = L.regrouper_par_client(lignes, dernieres, CFG, D(2026, 10, 3))
        self.assertEqual(set(r), {"C1", "C3"})
        self.assertEqual(set(r["C1"]["echeanciers"]), {"MS1", "MS2"})         # 2 échéanciers, UNE entrée
        self.assertEqual(r["C1"]["premiere"], D(2026, 6, 1))

    def test_tri_des_secteurs_numerique(self):
        self.assertEqual(sorted(["Secteur 10", "Secteur 2", "Zone partenaire - X", "Secteur 1"], key=L.cle_secteur),
                         ["Secteur 1", "Secteur 2", "Secteur 10", "Zone partenaire - X"])


class TestRepartition(unittest.TestCase):
    def _clients(self):
        base = {"echeanciers": {"MS": {"A": D(2026, 6, 1)}}, "premiere": D(2026, 6, 1), "nb_appels": 0, "dernier_appel": None}
        return {"tunis": dict(base, secteurs="Secteur 2"), "deux_adresses": dict(base, secteurs="Secteur 7, Secteur 2"),
                "sousse": dict(base, secteurs="Hors Secteur"), "gafsa": dict(base, secteurs="Hors Secteur"),
                "partenaire": dict(base, secteurs="Secteur 1"), "rdv": dict(base, secteurs="Secteur 1"),
                "recent": dict(base, secteurs="Secteur 1", dernier_appel=D(2026, 9, 20))}

    def test_classes(self):
        zone = lambda c: {"employe": "HR-EMP-00007", "nom": "EAS"} if c == "sousse" else None
        normal, z, urg, ecartes = L.repartir(self._clients(), D(2026, 10, 3), CFG, exclus={"partenaire"}, zones={"x": 1},
                                             zone_du_client=zone, rdv_clients={"rdv"}, secteur_urgence=["Secteur 7"], urgence_active=True)
        self.assertEqual(set(normal), {"tunis"})
        self.assertEqual(set(urg), {"deux_adresses"})                          # « Secteur 7, Secteur 2 » → urgence (plus la chaîne entière)
        self.assertEqual(set(z), {"sousse"}) ; self.assertTrue(z["sousse"]["secteurs"].startswith("Zone partenaire - EAS"))
        self.assertEqual(ecartes, {"partenaire": 1, "rdv": 1, "recent": 1, "hors_secteur": 1})

    def test_urgence_detectee_mais_non_creee_garde_les_clients(self):
        normal, _z, urg, _e = L.repartir(self._clients(), D(2026, 10, 3), CFG, exclus=set(), zones={}, zone_du_client=lambda c: None,
                                         rdv_clients=set(), secteur_urgence=["Secteur 7"], urgence_active=False)
        self.assertIn("deux_adresses", normal) ; self.assertEqual(urg, {})

    def test_resume(self):
        self.assertIn("Normal : 3 client(s) (+5 en attente)", L.resume_resultats({"normal_urgence": {"normal": {"created": True, "count_clients": 3, "en_attente": 5}}}))
        self.assertIn("Aucune liste", L.resume_resultats({}))
