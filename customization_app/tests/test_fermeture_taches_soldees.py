"""Clôture automatique des tâches en retard des commandes soldées (22/10/2026).

Cas réel : en validant l'encaissement Aramex ENC-21-10-2026-00001, deux pop-ups « Photos manquantes »
pour Tache-08149 — doublon ouvert depuis le 27/08 d'une livraison clôturée sous Tache-09059. Le refus
était rattrapé mais son message s'affichait, et chaque paiement le rejouait (43 Error Log depuis le 29/09).
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from customization_app import commande_alertes as CA


class _Tache:
    def __init__(self, nom, refus=None, panne=None):
        self.name, self.status, self.flags = nom, "Open", SimpleNamespace()
        self.refus, self.panne, self.commentaires = refus, panne, []

    def save(self):
        if self.refus:
            frappe.throw(self.refus, title="Photos manquantes")
        if self.panne:
            raise self.panne

    def add_comment(self, _type, texte):
        self.commentaires.append(texte)


class TestFermetureTachesSoldees(unittest.TestCase):

    def _fermer(self, taches, doublons=None):
        doublons = doublons or {}
        with patch.object(CA, "_taches_en_retard_soldees", return_value=list(taches)), \
             patch.object(CA, "_doublon_cloture", side_effect=lambda n: doublons.get(n)), \
             patch.object(CA.frappe, "get_doc", side_effect=lambda dt, n: taches[n]), \
             patch.object(CA, "_sur_erreur") as sur_erreur:
            return CA.fermer_taches_soldees(["X"]), sur_erreur

    def test_un_doublon_est_annule_pas_cloture(self):
        t = _Tache("Tache-08149")
        n, _ = self._fermer({"Tache-08149": t}, {"Tache-08149": "Tache-09059"})
        self.assertEqual((n, t.status), (1, "Cancelled"))
        self.assertIn("Tache-09059", t.commentaires[0])

    def test_un_refus_metier_ne_laisse_ni_message_ni_trace(self):
        avant = list(frappe.local.message_log)
        t = _Tache("Tache-08155", refus="📷 Photos obligatoires avant de clôturer cette tâche")
        n, sur_erreur = self._fermer({"Tache-08155": t})
        self.assertEqual(n, 0)
        self.assertEqual(frappe.local.message_log, avant)
        sur_erreur.assert_not_called()

    def test_une_panne_reste_tracee_sans_message(self):
        avant = list(frappe.local.message_log)
        t = _Tache("Tache-1", panne=RuntimeError("base injoignable"))
        n, sur_erreur = self._fermer({"Tache-1": t})
        self.assertEqual(n, 0)
        sur_erreur.assert_called_once_with("Tache-1")
        self.assertEqual(frappe.local.message_log, avant)

    def test_sans_doublon_la_tache_est_cloturee(self):
        t = _Tache("Tache-2")
        n, _ = self._fermer({"Tache-2": t})
        self.assertEqual((n, t.status, t.commentaires), (1, "Completed", []))


if __name__ == "__main__":
    unittest.main()
