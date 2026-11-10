"""Départ d'un employé (10/11/2026) : logique pure et câblage. Tourne sans site."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import depart_employe as D


def _t(nom, debut, type_="Entretien"):
    return {"name": nom, "starts_on": debut, "custom_type_dintervention": type_}


class TestClassement(unittest.TestCase):

    def test_a_venir_passees_absences(self):
        taches = [_t("T1", "2026-11-10 09:00:00"), _t("T2", "2026-11-09 18:00:00"), _t("T3", "2027-01-05 08:00:00"),
                  _t("R1", "2026-11-20 08:00:00", "Jour de récupération"), _t("C1", "2026-10-01 08:00:00", "Congé"),
                  _t("T4", None)]
        c = D.classer_taches(taches, "2026-11-10")
        self.assertEqual([t["name"] for t in c["a_venir"]], ["T1", "T3", "T4"])   # aujourd'hui compte comme à venir
        self.assertEqual([t["name"] for t in c["passees"]], ["T2"])
        # Un congé passé ne s'annule pas : seules les absences à venir sortent du calendrier.
        self.assertEqual([t["name"] for t in c["absences"]], ["R1"])

    def test_lignes_a_retirer(self):
        lignes = [{"name": "a", "employe": "HR-EMP-00006"}, {"name": "b", "employe": "HR-EMP-00010"}, {"name": "c"}]
        self.assertEqual([l["name"] for l in D.lignes_a_retirer(lignes, "employe", "HR-EMP-00006")], ["a"])
        self.assertEqual(D.lignes_a_retirer(lignes, "employe", None), [])


class TestSalaireBanque(unittest.TestCase):
    LIGNES = [{"name": "a", "type": "Salaire", "libelle": "Salaire Med Hédi Chouchène"},
              {"name": "b", "type": "Salaire", "libelle": "Salaire Akram"},
              {"name": "c", "type": "Salaire", "libelle": "Salaire Koubaâ Néjib"},
              {"name": "d", "type": "Loyer", "libelle": "Loyer Akram"}]

    def test_nom_de_famille_a_l_accent_pres(self):
        self.assertEqual([l["name"] for l in D.lignes_salaire_de("Mohamed Hedi Chouchane", self.LIGNES)], ["a"])
        self.assertEqual([l["name"] for l in D.lignes_salaire_de("Akram", self.LIGNES)], ["b"])
        self.assertEqual([l["name"] for l in D.lignes_salaire_de("Nejib Koubaa", self.LIGNES)], ["c"])

    def test_le_prenom_seul_ne_suffit_pas(self):
        self.assertEqual(D.lignes_salaire_de("Hedi ibidhii", self.LIGNES), [])
        self.assertEqual(D.lignes_salaire_de("", self.LIGNES), [])


class TestReste(unittest.TestCase):

    def test_rien(self):
        self.assertEqual(D.reste_a_faire({"taches_a_venir": [], "reglages": [], "stock": {"quantite": 0}}), [])

    def test_tout(self):
        reste = D.reste_a_faire({"taches_a_venir": [1, 2], "conges_a_venir": [1], "reglages": [1, 2, 3],
                                 "portail_defaut": True, "verifications_ouvertes": [1], "stock": {"quantite": 5}})
        self.assertEqual(len(reste), 6)
        self.assertIn("2 rendez-vous à venir", reste)
        self.assertIn("3 réglage(s) qui le citent", reste)
        self.assertIn("du stock dans son entrepôt", reste)

    def test_journal(self):
        self.assertEqual(D.texte_journal(["a", "b"]), "• a\n• b")


class TestCablage(unittest.TestCase):

    def setUp(self):
        self.base = os.path.dirname(inspect.getfile(customization_app))

    def _lire(self, *chemin):
        with open(os.path.join(self.base, *chemin), encoding="utf-8") as fh:
            return fh.read()

    def test_hooks(self):
        hooks = self._lire("hooks.py")
        for morceau in ('"validate": "customization_app.depart_employe.employee_validate"',
                        '"Verification Stock": {"on_update": "customization_app.depart_employe.stock_apres_verification"}',
                        '"customization_app.depart_employe.alerte_quotidienne"',
                        '"Employee": "public/js/employee_depart.js"'):
            self.assertIn(morceau, hooks, morceau)
        self.assertIn("customization_app.patches.depart_employe_champs", self._lire("patches.txt"))

    def test_bouton(self):
        js = self._lire("public", "js", "employee_depart.js")
        for morceau in ('frappe.ui.form.on("Employee"', ".inventaire", ".executer", "remplacant_portail", "annuler_passees",
                        "custom_depart_verification"):
            self.assertIn(morceau, js, morceau)

    def test_les_reglages_sont_retires_pas_remplaces(self):
        src = inspect.getsource(D._retirer_des_reglages)
        self.assertIn("if r.name not in noms", src)
        self.assertNotIn("remplacant", src)

    def test_fermeture_passe_par_le_drapeau(self):
        src = inspect.getsource(D.executer)
        self.assertIn("emp.flags.depart_employe = True", src)
        self.assertIn('emp.status = "Left"', src)
        self.assertIn("user.enabled = 0", src)
        self.assertIn("_couper_salaire_banque", src)
