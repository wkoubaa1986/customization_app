"""Deux règles du 18/11/2026 sur les tâches de travail :
  - une tâche « Autre » ne se clôture pas sans compte rendu (« il faut ajouter le compte rendu comme obligatoire ») ;
  - choisir le client d'une tâche PAS ENCORE ENREGISTRÉE ne doit plus afficher « Pas trouvé »."""
from __future__ import annotations

import inspect
import json
import os
import unittest

import customization_app
from customization_app import api
from customization_app import cloture_tache as C

BASE = os.path.dirname(inspect.getfile(customization_app))


class TestCompteRenduAutre(unittest.TestCase):
    def test_autre_exige_le_compte_rendu_comme_les_interventions(self):
        self.assertEqual(C.TYPES_RAPPORT, ("Entretien", "Réparation", "Installation", "Visite", "Autre"))
        self.assertIn("rapport_requis = type_i in TYPES_RAPPORT", inspect.getsource(C.exigences))

    def test_la_fiche_dit_la_meme_chose(self):
        """Le champ de la fiche et le dialogue de clôture ne doivent pas se contredire."""
        with open(os.path.join(BASE, "customize_erpnext", "doctype", "tache_de_travail", "tache_de_travail.json"), encoding="utf-8") as f:
            champ = [x for x in json.load(f)["fields"] if x["fieldname"] == "rapport_visite"][0]
        for cle in ("mandatory_depends_on", "depends_on"):
            for type_i in C.TYPES_RAPPORT:
                self.assertIn('"%s"' % type_i, champ[cle], (cle, type_i))
            self.assertNotIn('"Livraison"', champ[cle])                    # la livraison n'en demande toujours pas


class TestClotureDepuisMaJournee(unittest.TestCase):
    """18/11/2026 : « pour Autre, j'ai ajouté le rapport et clôturé, et la tâche est restée la même dans Ma journée ».
    Le dialogue enchaîne sur `reload_doc().then(…)` ; la façade de Ma journée ne rendait pas de promesse, l'enchaînement
    cassait en silence et la clôture n'était jamais demandée au serveur."""

    def _lire(self, *chemin):
        with open(os.path.join(BASE, *chemin), encoding="utf-8") as f:
            return f.read()

    def test_la_facade_rend_une_promesse(self):
        js = self._lire("public", "js", "ma_journee.js")
        self.assertIn("reload_doc: () => Promise.resolve(moi.charger()),", js)
        self.assertNotIn("reload_doc: () => moi.charger(),", js)

    def test_le_dialogue_ne_depend_plus_de_ce_que_rend_le_rechargement(self):
        js = self._lire("public", "js", "tache_photos_cloture.js")
        self.assertIn("Promise.resolve(frm.reload_doc()).then(() => {", js)
        self.assertNotIn("\n                    frm.reload_doc().then(() => {", js)


class TestNouvelleTache(unittest.TestCase):
    def test_une_tache_non_enregistree_ne_demande_pas_de_droit(self):
        source = inspect.getsource(api.get_customer_booking_info)
        existe = 'if task_name and not frappe.db.exists("Tache de travail", task_name):'
        droit = 'if task_name and not frappe.has_permission("Tache de travail", "read", doc=task_name):'
        self.assertIn(existe, source)
        self.assertLess(source.index(existe), source.index(droit))        # d'abord : existe-t-elle ? ensuite : a-t-il le droit ?
        self.assertIn('if not frappe.has_permission("Tache de travail", "read"):', source)   # le contrôle général reste


if __name__ == "__main__":
    unittest.main()
