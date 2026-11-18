"""Tout employé peut LIRE toute tâche de travail ; listes et écriture gardent leur limite (customization_app.tache_lecture).
Demande du 18/11/2026 : « tous les techniciens et les employés peuvent voir tout ce qui était fait avant, même si
l'employé est parti et qu'il n'a pas l'autorisation » — Radhouan recevait « Pas permis » sur une tâche de Mohamed Hedi."""
from __future__ import annotations

import inspect
import json
import os
import unittest

import customization_app
from customization_app import sous_traitant as ST
from customization_app import tache_lecture as L

BASE = os.path.dirname(inspect.getfile(customization_app))
RADHOUAN, MOHAMED_HEDI = "HR-EMP-00013", "HR-EMP-00006"


class TestLecture(unittest.TestCase):
    def test_lire_la_tache_d_un_employe_parti_est_permis(self):
        for ptype in ("read", "print", "email", "select", None):
            self.assertTrue(L.peut(ptype, MOHAMED_HEDI, [RADHOUAN]), ptype)

    def test_la_modifier_ne_l_est_pas(self):
        for ptype in ("write", "delete", "create", "submit", "cancel", "share"):
            self.assertFalse(L.peut(ptype, MOHAMED_HEDI, [RADHOUAN]), ptype)

    def test_ses_propres_taches_restent_modifiables(self):
        for ptype in ("read", "write", "delete"):
            self.assertTrue(L.peut(ptype, RADHOUAN, [RADHOUAN]), ptype)

    def test_un_compte_sans_liste_n_a_aucune_limite(self):
        self.assertTrue(L.peut("write", MOHAMED_HEDI, None))
        self.assertTrue(L.peut("delete", None, None))

    def test_une_tache_sans_employe_suit_le_reglage_strict(self):
        self.assertTrue(L.peut("write", None, [RADHOUAN], strict=False))
        self.assertFalse(L.peut("write", "", [RADHOUAN], strict=True))

    def test_un_compte_externe_ne_lit_que_sa_liste(self):
        """Partenaire, sous-traitant : rien ne change pour eux."""
        self.assertFalse(L.peut("read", MOHAMED_HEDI, ["HR-EMP-00007"], lecture_ouverte=False))
        self.assertTrue(L.peut("read", "HR-EMP-00007", ["HR-EMP-00007"], lecture_ouverte=False))
        self.assertEqual(L.ROLES_EXTERNES, ("Partenaire", "Sous-traitant"))


class TestListes(unittest.TestCase):
    """Le calendrier et les listes : comme avant, les tâches des employés de sa liste."""

    def test_sans_liste_pas_de_filtre(self):
        self.assertEqual(L.condition_liste(None), "")

    def test_les_taches_des_employes_de_sa_liste(self):
        c = L.condition_liste([RADHOUAN, "HR-EMP-00001"], strict=True)
        self.assertEqual(c, "`tabTache de travail`.`custom_choix_du_staff` in ('HR-EMP-00013', 'HR-EMP-00001')")

    def test_hors_mode_strict_les_taches_sans_employe_aussi(self):
        c = L.condition_liste([RADHOUAN])
        self.assertEqual(c, "(ifnull(`tabTache de travail`.`custom_choix_du_staff`, '') = '' or "
                            "`tabTache de travail`.`custom_choix_du_staff` in ('HR-EMP-00013'))")

    def test_les_valeurs_sont_echappees(self):
        self.assertIn("'O''Brien'", L.condition_liste(["O'Brien"]))
        self.assertIn("in ('')", L.condition_liste([], strict=True))          # liste vide : rien, pas une erreur SQL


class TestBranchements(unittest.TestCase):
    def test_le_champ_est_dispense_des_permissions_utilisateur(self):
        with open(os.path.join(BASE, "customize_erpnext", "doctype", "tache_de_travail", "tache_de_travail.json"), encoding="utf-8") as f:
            champ = [x for x in json.load(f)["fields"] if x["fieldname"] == "custom_choix_du_staff"][0]
        self.assertEqual(champ.get("ignore_user_permissions"), 1)

    def test_les_deux_regles_maison_reprennent_la_limite(self):
        """Sans elles, la dispense ouvrirait aussi les listes et l'écriture."""
        self.assertIn("tache_lecture.condition_liste_pour(user)", inspect.getsource(ST.permission_query_conditions_tache))
        source = inspect.getsource(ST.has_permission_tache)
        self.assertIn("tache_lecture.autorise(doc, user,", source)
        self.assertIn("ptype=None", source)                                    # le nom que Frappe transmet
        with open(os.path.join(BASE, "hooks.py"), encoding="utf-8") as f:
            hooks = f.read()
        self.assertIn('"Tache de travail": "customization_app.sous_traitant.has_permission_tache"', hooks)
        self.assertIn('"Tache de travail": "customization_app.sous_traitant.permission_query_conditions_tache"', hooks)

    def test_le_sous_traitant_passe_avant(self):
        source = inspect.getsource(ST.has_permission_tache)
        self.assertLess(source.index('doc.get("custom_choix_du_staff") == emp.name'), source.index("tache_lecture.autorise"))

    def test_la_fiche_se_fige_en_consultation_seule(self):
        with open(os.path.join(BASE, "public", "js", "tache_lecture.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('"customization_app.tache_lecture.droits"', js)
        self.assertIn("frm.disable_form();", js)
        self.assertIn("Consultation seule", js)
        with open(os.path.join(BASE, "hooks.py"), encoding="utf-8") as f:
            self.assertIn('_js("tache_lecture.js")', f.read())
        self.assertIn('doc.check_permission("read")', inspect.getsource(L.droits))


if __name__ == "__main__":
    unittest.main()
