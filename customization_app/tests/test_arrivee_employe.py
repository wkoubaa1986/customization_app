"""Arrivée d'un technicien (10/11/2026) : logique pure et câblage. Tourne sans site."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import arrivee_employe as A


class TestPur(unittest.TestCase):

    def test_decouper_nom(self):
        self.assertEqual(A.decouper_nom("  Mohamed   Hedi Chouchane "), ("Mohamed", "Hedi Chouchane"))
        self.assertEqual(A.decouper_nom("Akram"), ("Akram", None))

    def test_ligne_existe_et_cles(self):
        lignes = [{"employe": "HR-EMP-00001", "nom_employe": "Sadok"}, {"user": "x@y.z"}]
        self.assertTrue(A.ligne_existe(lignes, {"employe": "HR-EMP-00001", "nom_employe": "autre"}, ("employe",)))
        self.assertFalse(A.ligne_existe(lignes, {"employe": "HR-EMP-00002"}, ("employe",)))
        self.assertEqual(A.cles_identite({"employee": "x", "mode": "Ventes"}), ("employee",))
        self.assertEqual(A.cles_identite({"caisse": "Sadok", "date_depart": "2026-11-10"}), ("caisse",))
        self.assertEqual(A.cles_identite({"user": "x@y.z", "weight": 1}), ("user",))

    def test_profil_technicien(self):
        """Les rôles de Mohamed Hedi Chouchane (relevés le 10/11/2026) + Technicien, pas de rôle d'administration."""
        for r in ("Employee", "Maintenance Manager", "POS Manager", "Raven User", "Sales Manager", "Sales User", "Stock User",
                  "Technicien"):
            self.assertIn(r, A.ROLES_TECHNICIEN)
        for r in ("System Manager", "Responsable magasin", "Sous-traitant", "Congés RH"):
            self.assertNotIn(r, A.ROLES_TECHNICIEN)

    def test_reglages_vehicule_facultatif(self):
        emp = type("E", (), {"name": "HR-EMP-00099", "employee_name": "Test Tech"})()
        fabriques = {enfant: f for _s, enfant, f in A.REGLAGES}
        self.assertIsNone(fabriques["Config Flotte GPS Vehicule"](emp, "t@x.y", {"vehicule": "", "cbox": ""}))
        self.assertEqual(fabriques["Config Flotte GPS Vehicule"](emp, "t@x.y", {"vehicule": "123 TU 1", "cbox": "999"})["actif"], 1)
        self.assertIsNone(fabriques["Assignment Rule User"](emp, None, {}))
        self.assertEqual(fabriques["Config Caisse Depart"](emp, "t@x.y", {"date_entree": "2026-11-10"}),
                         {"caisse": "Test Tech", "date_depart": "2026-11-10"})


class TestCablage(unittest.TestCase):

    def setUp(self):
        self.base = os.path.dirname(inspect.getfile(customization_app))

    def _lire(self, *chemin):
        with open(os.path.join(self.base, *chemin), encoding="utf-8") as fh:
            return fh.read()

    def test_hooks_et_patch(self):
        self.assertIn('"Employee": "public/js/employee_list_arrivee.js"', self._lire("hooks.py"))
        self.assertIn("customization_app.patches.arrivee_technicien", self._lire("patches.txt"))
        self.assertIn('"role": "Technicien"', self._lire("customize_erpnext", "page", "agenda_sous_traitant", "agenda_sous_traitant.json"))

    def test_bouton(self):
        js = self._lire("public", "js", "employee_list_arrivee.js")
        for morceau in ("arrivee_employe.creer_technicien", "ecran_simplifie", "premier_jour_recup", "domicile_lien"):
            self.assertIn(morceau, js, morceau)

    def test_ecran_simplifie_decouple_de_l_argent(self):
        """Les droits et la caisse ne regardent que le sous-traitant ; l'écran regarde les deux."""
        from customization_app import sous_traitant as ST
        for f in (ST.permission_query_conditions_tache, ST.has_permission_tache, ST.permission_query_conditions_verification,
                  ST.has_permission_verification, ST.permission_query_conditions_cloture_caisse, ST.has_permission_cloture_caisse):
            self.assertIn("sous_traitant_courant(user)", inspect.getsource(f), f.__name__)
        self.assertIn("CHAMP_ECRAN", inspect.getsource(ST.employe_courant))
        self.assertNotIn("CHAMP_ECRAN", inspect.getsource(ST.sous_traitant_courant))
        self.assertIn("sous_traitant_courant()", self._lire("caisse_cloture.py"))
        self.assertIn("frappe.boot.ecran_simplifie || frappe.boot.sous_traitant", self._lire("public", "js", "sous_traitant.js"))
