"""Le bouton « 👥 Réunion » du calendrier des tâches crée une réunion du Suivi d'activité, avec son lien visio
(demande du 18/11/2026 : « comme à partir de l'onglet Réunion, avec lien, comme en suivi d'activité »)."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import reunion_activite as R

BASE = os.path.dirname(inspect.getfile(customization_app))


class TestBoutonReunion(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(BASE, "public", "js", "tache_reunion.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_le_bouton_passe_par_le_service_des_reunions_du_suivi(self):
        self.assertIn('const API = "customization_app.reunion_activite";', self.js)
        self.assertIn('method: API + ".preparer_creation"', self.js)
        self.assertIn('method: API + ".creer"', self.js)
        # plus de tâche « Autre » par participant
        self.assertNotIn("taches_groupe", self.js.split("customization_app.reunion = ", 1)[1])

    def test_le_dialogue_reprend_les_champs_de_l_onglet(self):
        for champ in ('fieldname: "date"', 'fieldname: "heure"', 'fieldname: "titre"', 'fieldname: "lieu"',
                      'fieldname: "repete"', 'fieldname: "jusquau"', 'fieldname: "participants"'):
            self.assertIn(champ, self.js)
        self.assertIn('"Non", "Chaque semaine", "Toutes les 2 semaines", "Toutes les 3 semaines", "Toutes les 4 semaines"', self.js)
        self.assertIn('class="rn-emp" value="${esc(e.name)}" checked', self.js)          # toute l'équipe cochée, comme l'onglet

    def test_le_lien_est_montre_apres_la_creation(self):
        self.assertIn("m.lien_visio", self.js)
        self.assertIn("frappe.utils.copy_to_clipboard(lien)", self.js)
        self.assertIn("frappe.route_options = { reunion: m.name };", self.js)            # « Ouvrir la réunion » : sa page
        self.assertIn('frappe.set_route("suivi-activite")', self.js)

    def test_un_compte_non_responsable_est_prevenu_sans_erreur(self):
        self.assertIn("if (!c.peut_gerer) {", self.js)
        source = inspect.getsource(R.preparer_creation)
        self.assertIn('return {"peut_gerer": False}', source)
        self.assertNotIn("frappe.throw", source)

    def test_le_bouton_reste_sur_la_liste_et_le_calendrier_des_taches(self):
        self.assertIn('if (this.doctype !== "Tache de travail") return;', self.js)
        self.assertIn('customization_app.reunion.ouvrir(() => this.refresh())', self.js)  # le calendrier se recharge


class TestService(unittest.TestCase):
    def test_la_creation_rend_le_lien_visio(self):
        source = inspect.getsource(R.creer)
        self.assertIn('"lien_visio": doc.lien_visio', source)
        self.assertIn("_gerer()", source)                                                # même droit que l'onglet

    def test_les_valeurs_par_defaut_sont_celles_du_reglage(self):
        source = inspect.getsource(R.preparer_creation)
        for cle in ('"employes"', '"heure"', '"lieu"', '"visio"', '"prochaine_date"'):
            self.assertIn(cle, source)
        self.assertIn("SA.employes_parametres()", source)
        self.assertTrue(getattr(R.preparer_creation, "__wrapped__", None) or R.preparer_creation in frappe_whitelisted())


def frappe_whitelisted():
    import frappe
    return frappe.whitelisted


if __name__ == "__main__":
    unittest.main()
