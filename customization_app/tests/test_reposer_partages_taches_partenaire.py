"""La validation d'une liste d'appels ne doit plus retirer au partenaire le client de sa tâche ouverte.

Convention de l'app : `unittest.TestCase` pur, accès injectés, aucune base.
"""
from __future__ import annotations

import unittest

from customization_app import hooks
from customization_app.patches import reposer_partages_taches_partenaire as P


class TestRetraitDebranche(unittest.TestCase):
    def test_la_validation_d_une_liste_ne_retire_plus_aucun_partage(self):
        evenements = hooks.doc_events.get("Liste Appelle Entretien", {})
        self.assertNotIn("on_submit", evenements)
        self.assertNotIn("customization_app.partage_partenaire.retirer_partages",
                         [v for v in evenements.values() if isinstance(v, str)])


class TestAPartager(unittest.TestCase):
    def test_client_adresses_tache_et_commande_avec_les_droits_du_server_script(self):
        tache = {"name": "Tache-1", "custom_client": "CLI-1", "commande_client": "SAL-ORD-1"}
        out = P.a_partager(tache, lambda c: ["CLI-1-Facturation", "CLI-1-Livraison"])
        self.assertEqual([(d, n) for d, n, _ in out], [
            ("Tache de travail", "Tache-1"), ("Customer", "CLI-1"),
            ("Address", "CLI-1-Facturation"), ("Address", "CLI-1-Livraison"), ("Sales Order", "SAL-ORD-1"),
        ])
        droits = {d: p for d, _n, p in out}
        self.assertEqual(droits["Customer"], {"read": 1, "write": 0, "submit": 0, "share": 1})
        self.assertEqual(droits["Address"], {"read": 1, "write": 0, "submit": 0, "share": 1})
        self.assertEqual(droits["Sales Order"], {"read": 1, "write": 1, "submit": 1, "share": 0})
        self.assertEqual(droits["Tache de travail"]["write"], 1)

    def test_tache_sans_client_ni_commande(self):
        out = P.a_partager({"name": "Tache-2", "custom_client": None, "commande_client": None},
                           lambda c: self.fail("pas de client, pas d'adresses à chercher"))
        self.assertEqual([(d, n) for d, n, _ in out], [("Tache de travail", "Tache-2")])

    def test_client_sans_adresse(self):
        out = P.a_partager({"name": "Tache-3", "custom_client": "CLI-3"}, lambda c: [])
        self.assertEqual([(d, n) for d, n, _ in out], [("Tache de travail", "Tache-3"), ("Customer", "CLI-3")])


if __name__ == "__main__":
    unittest.main()
