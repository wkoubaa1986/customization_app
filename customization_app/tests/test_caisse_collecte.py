"""Double validation de la caisse — logique pure (sans site) + garde-fous de câblage.

Le circuit : l'employé compte et REMET, le responsable de collecte REÇOIT ; un délégué collecte
par passation. Ces tests couvrent les calculs et les règles qui ne touchent pas la base, et
vérifient que hooks.py et patches.txt sont bien câblés (le contournement « soumettre depuis le
formulaire » vécu sur les transferts de stock ne doit pas se reproduire sur la caisse).
"""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app


def _lire(*chemin):
    base = os.path.dirname(inspect.getfile(customization_app))
    with open(os.path.join(base, *chemin), encoding="utf-8") as fh:
        return fh.read()


class TestCalculsRemise(unittest.TestCase):

    def setUp(self):
        from customization_app import caisse_collecte as CC
        self.CC = CC

    def test_ecart_remise_est_recu_moins_declare(self):
        self.assertEqual(self.CC.ecart_remise(1300, 1250), -50.0)
        self.assertEqual(self.CC.ecart_remise(100.5, 100.5), 0.0)
        self.assertEqual(self.CC.ecart_remise(None, 20), 20.0)

    def test_remise_identique_exige_especes_et_nombres(self):
        d = {"especes": 1000.0, "nb_cheques": 2, "nb_traites": 0}
        self.assertTrue(self.CC.remise_identique(d, {"especes": 1000.0004, "nb_cheques": 2, "nb_traites": 0}))
        self.assertFalse(self.CC.remise_identique(d, {"especes": 999.0, "nb_cheques": 2, "nb_traites": 0}))
        self.assertFalse(self.CC.remise_identique(d, {"especes": 1000.0, "nb_cheques": 1, "nb_traites": 0}))
        self.assertFalse(self.CC.remise_identique(d, {"especes": 1000.0, "nb_cheques": 2, "nb_traites": 1}))

    def test_fond_conserve(self):
        self.assertEqual(self.CC.fond_conserve(1315.5, 1300), 15.5)
        self.assertEqual(self.CC.fond_conserve(1315.5, None), 1315.5)

    def test_controler_remise(self):
        self.CC.controler_remise(100, 100)
        self.CC.controler_remise(100, 0)
        with self.assertRaises(ValueError):
            self.CC.controler_remise(None, 10)          # pas compté
        with self.assertRaises(ValueError):
            self.CC.controler_remise(100, None)         # remise non renseignée
        with self.assertRaises(ValueError):
            self.CC.controler_remise(100, -1)           # négative
        with self.assertRaises(ValueError):
            self.CC.controler_remise(100, 100.01)       # dépasse le comptage


class TestPlancherReport(unittest.TestCase):
    """Date de départ (Config Caisse) : à partir de ce jour, les clôtures antérieures ne font plus report."""

    def setUp(self):
        from customization_app.caisse_collecte import plancher_report
        self.f = plancher_report

    def test_sans_date_de_depart_tout_l_historique_compte(self):
        self.assertIsNone(self.f("2026-10-06", None))

    def test_avant_la_date_de_depart_rien_ne_change(self):
        self.assertIsNone(self.f("2026-10-04", "2026-10-05"))

    def test_a_partir_de_la_date_seules_les_clotures_posterieures_comptent(self):
        import datetime
        self.assertEqual(self.f("2026-10-05", "2026-10-05"), datetime.date(2026, 10, 5))
        self.assertEqual(self.f("2026-10-20", "2026-10-05"), datetime.date(2026, 10, 5))


class TestJustificationsDepuisControles(unittest.TestCase):

    def test_inverse_du_format_stocke(self):
        from customization_app.caisse_collecte import justifications_depuis_controles as f
        texte = "Tâche ouverte — SAL-ORD-1 · X\n  → client absent\nPaiement P exclu\n  → doublon"
        self.assertEqual(f(texte), {"Tâche ouverte — SAL-ORD-1 · X": "client absent", "Paiement P exclu": "doublon"})
        self.assertEqual(f(""), {})
        self.assertEqual(f(None), {})


class TestDateDepartParCaisse(unittest.TestCase):

    def test_la_date_de_la_caisse_prime_sur_la_generale(self):
        from customization_app.caisse_collecte import date_depart_pour
        cfg = {"date_depart": "2026-10-05", "departs": {"Salma Ben Saïd": "2026-10-10"}}
        self.assertEqual(date_depart_pour("Salma Ben Saïd", cfg), "2026-10-10")
        self.assertEqual(date_depart_pour("Akram", cfg), "2026-10-05")
        self.assertIsNone(date_depart_pour("Akram", {"date_depart": None, "departs": {}}))
        self.assertEqual(date_depart_pour(" Salma Ben Saïd ", cfg), "2026-10-10")


class TestPeriodeDelegation(unittest.TestCase):
    """Un délégué ne l'est que pendant sa période (Du / Au) ; bornes vides = sans limite."""

    def setUp(self):
        from customization_app.caisse_collecte import delegation_active
        self.f = delegation_active

    def test_sans_bornes(self):
        self.assertTrue(self.f(None, None, "2026-10-04"))

    def test_dans_la_periode_bornes_incluses(self):
        self.assertTrue(self.f("2026-10-04", "2026-10-10", "2026-10-04"))
        self.assertTrue(self.f("2026-10-04", "2026-10-10", "2026-10-10"))

    def test_hors_periode(self):
        self.assertFalse(self.f("2026-10-05", "2026-10-10", "2026-10-04"))
        self.assertFalse(self.f("2026-10-01", "2026-10-03", "2026-10-04"))

    def test_une_seule_borne(self):
        self.assertTrue(self.f("2026-10-01", None, "2026-12-31"))
        self.assertFalse(self.f(None, "2026-10-03", "2026-10-04"))


class TestRoles(unittest.TestCase):

    def setUp(self):
        from customization_app.caisse_collecte import _role
        self._role = _role

    def test_titulaire_prime_sur_direction(self):
        self.assertEqual(self._role("jamel", ["jamel"], ["hedi"], True), "titulaire")

    def test_co_titulaire_est_titulaire(self):
        # « Met-moi en plus responsable de collecte » (04/10) : même rang, pas de passation.
        self.assertEqual(self._role("wassim", ["nejib", "wassim"], ["jamel"], True), "titulaire")

    def test_delegue(self):
        self.assertEqual(self._role("hedi", ["jamel"], ["hedi"], False), "delegue")

    def test_direction_sans_titre_collecte_comme_delegue(self):
        # L'argent passe par ses mains : passation vers le titulaire.
        self.assertEqual(self._role("wassim", ["jamel"], ["hedi"], True), "direction")

    def test_employe_simple(self):
        self.assertIsNone(self._role("akram", ["jamel"], ["hedi"], False))

    def test_periode_depassee_prime_sur_la_direction(self):
        # Jamel (direction codée en dur) délégué « jusqu'à hier » : plus aucun droit aujourd'hui.
        self.assertIsNone(self._role("jamel", ["nejib"], [], True, hors_periode=["jamel"]))
        # Un membre de la direction NON inscrit garde la collecte par passation.
        self.assertEqual(self._role("wassim", ["nejib"], [], True, hors_periode=["jamel"]), "direction")

    def test_sans_titulaire_configure_la_direction_collecte_en_titulaire(self):
        # Personne ne pourrait recevoir une passation : pas de passation.
        self.assertEqual(self._role("wassim", [], [], True), "titulaire")
        self.assertIsNone(self._role("akram", [], [], False))


class TestCablage(unittest.TestCase):

    def test_hooks_before_submit(self):
        hooks = _lire("hooks.py")
        self.assertIn('"Cloture Caisse": {', hooks)
        self.assertIn("customization_app.caisse_collecte.cloture_caisse_before_submit", hooks)
        self.assertIn("customization_app.caisse_collecte.passation_caisse_before_submit", hooks)

    def test_patch_enregistre_avec_saut_de_ligne_final(self):
        pt = _lire("patches.txt")
        self.assertIn("customization_app.patches.reprise_clotures_collecte\n", pt)
        self.assertIn("customization_app.patches.config_caisse_date_depart\n", pt)
        self.assertTrue(pt.endswith("\n"))

    def test_page_js_branche_la_collecte(self):
        js = _lire("customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.js")
        # Les méthodes sont appelées via la constante API = "customization_app.caisse_collecte".
        self.assertIn('const API = "customization_app.caisse_collecte";', js)
        for morceau in ("caisse_collecte.contexte", '".a_collecter"', '".collecter"',
                        "caisse_collecte.repondre_ecart", '".passations"', '".valider_passation"',
                        '".remettre_passation"', '".repondre_ecart_passation"',
                        "caisse_collecte.annuler_comptage", "especes_remises:", "rcj-btn-collecte"):
            self.assertIn(morceau, js, morceau)
        html = _lire("customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.html")
        self.assertIn('id="rcj-btn-collecte"', html)
        self.assertIn('id="rcj-collecte-banner"', html)
        self.assertIn('id="rcj-btn-config"', html)
        self.assertIn('frappe.set_route("Form", "Config Caisse")', js)
        # Collecte en un geste : « Reçu conforme » + « autre montant » (dialogue de collecte ET passation).
        for morceau in ("rcj-cc-ok", "rcj-cc-autre", "rcj-cc-ecart", "rcj-pas-conforme", "rcj-pas-autre", "restreint",
                        "data-rouvrir-comptage", "rouvrir: reprise ? reprise.name : null"):
            self.assertIn(morceau, js, morceau)


if __name__ == "__main__":
    unittest.main()
