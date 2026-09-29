"""Tests du partage d'une Liste Appelle Entretien avec le partenaire.

Convention de l'app : `unittest.TestCase` pur, accès injectés, aucune base.
"""
from __future__ import annotations

import unittest

from customization_app import partage_partenaire as P


class Row(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


class Doc:
    def __init__(self, name, clients):
        self.name = name
        self.clients = [Row(c) for c in clients]

    def get(self, k, default=None):
        return getattr(self, k, default)


def _acces(links=None, primaires=None, commandes=None, existe=True):
    links = links or {}
    primaires = primaires or {}
    commandes = commandes or {}

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None):
        if doctype == "Dynamic Link":
            return links.get(filters["link_name"], [])
        if doctype == "Sales Order":
            return commandes.get(filters["customer"], [])
        return []

    def get_value(doctype, name, field):
        return primaires.get((name, field))

    def exists(doctype, name):
        return existe

    return get_all, get_value, exists


class TestDocumentsLies(unittest.TestCase):
    def test_la_liste_l_echeancier_le_client_ses_contacts_et_ses_commandes(self):
        get_all, get_value, exists = _acces(
            links={"CLI-1": [{"parent": "CT-1", "parenttype": "Contact"},
                             {"parent": "AD-1", "parenttype": "Address"}]},
            primaires={("CLI-1", "customer_primary_contact"): "CT-1",
                       ("CLI-1", "customer_primary_address"): "AD-2"},
            commandes={"CLI-1": ["SAL-ORD-1", "SAL-ORD-2"]},
        )
        doc = Doc("LIST-1", [{"client": "CLI-1", "échéancier_dentretien": "MS-1"}])
        out = P.documents_lies(doc, get_all, get_value, exists, echeancier_doctype="Maintenance Schedule")
        self.assertEqual([(d, n) for d, n, _ in out], [
            ("Liste Appelle Entretien", "LIST-1"), ("Maintenance Schedule", "MS-1"),
            ("Customer", "CLI-1"), ("Contact", "CT-1"), ("Address", "AD-1"),
            ("Address", "AD-2"), ("Sales Order", "SAL-ORD-1"), ("Sales Order", "SAL-ORD-2"),
        ])

    def test_les_commandes_sont_en_lecture_seule_le_reste_en_ecriture(self):
        get_all, get_value, exists = _acces(commandes={"CLI-1": ["SAL-ORD-1"]})
        doc = Doc("LIST-1", [{"client": "CLI-1"}])
        perms = {(d, n): p for d, n, p in P.documents_lies(doc, get_all, get_value, exists, "MS")}
        self.assertEqual(perms[("Sales Order", "SAL-ORD-1")]["write"], 0)
        self.assertEqual(perms[("Customer", "CLI-1")]["write"], 1)

    def test_un_client_disparu_ne_casse_rien(self):
        get_all, get_value, exists = _acces(existe=False)
        doc = Doc("LIST-1", [{"client": "CLI-X", "échéancier_dentretien": "MS-1"}])
        out = P.documents_lies(doc, get_all, get_value, exists, "MS")
        self.assertEqual([(d, n) for d, n, _ in out], [("Liste Appelle Entretien", "LIST-1"), ("MS", "MS-1")])

    def test_deux_lignes_du_meme_client_ne_partagent_qu_une_fois(self):
        get_all, get_value, exists = _acces()
        doc = Doc("LIST-1", [{"client": "CLI-1"}, {"client": "CLI-1"}])
        out = P.documents_lies(doc, get_all, get_value, exists, "MS")
        self.assertEqual(len([1 for d, n, _ in out if d == "Customer"]), 1)


class TestPermsPour(unittest.TestCase):
    def test_soumettable_en_ecriture_donne_submit(self):
        self.assertEqual(P.perms_pour("Sales Order", {"read": 1, "write": 1}, True),
                         {"read": 1, "write": 1, "submit": 1})

    def test_lecture_seule_jamais_de_submit(self):
        self.assertEqual(P.perms_pour("Sales Order", {"read": 1, "write": 0}, True),
                         {"read": 1, "write": 0, "submit": 0})

    def test_non_soumettable_pas_de_submit(self):
        self.assertEqual(P.perms_pour("Customer", {"read": 1, "write": 1}, False),
                         {"read": 1, "write": 1, "submit": 0})
