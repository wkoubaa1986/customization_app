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
