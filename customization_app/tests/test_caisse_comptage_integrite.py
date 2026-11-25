"""Remises cumulées et comptage obligatoire : régressions du 25/11/2026.

À lancer avec un site DEV initialisé. Les clôtures de test sont insérées dans un point de reprise puis
annulées ; la validation est interceptée avant toute création, notification ou génération de PDF.
"""
from contextlib import ExitStack
from datetime import date
import unittest
from unittest import mock
from uuid import uuid4

import frappe

from customization_app import argent_liquide as AL
from customization_app import caisse_cloture as CL
from customization_app import caisse_collecte as CC


class TestRemisesCumulees(unittest.TestCase):
    def setUp(self):
        if not getattr(frappe.local, "db", None) or not frappe.conf.get("developer_mode"):
            self.skipTest("site DEV requis")
        self.caisse = "TEST-REMISES-" + uuid4().hex[:12]
        self.savepoint = "remises_" + uuid4().hex[:12]
        frappe.db.savepoint(self.savepoint)
        self.addCleanup(lambda: frappe.db.rollback(save_point=self.savepoint))
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(mock.patch.object(CC, "noms_caisses", return_value=[self.caisse]))
        self.stack.enter_context(mock.patch.object(CC, "caisses_partenaires", return_value=set()))
        self.stack.enter_context(mock.patch("customization_app.rapport_caisse_journaliere.get_data", return_value={}))

    def cloture(self, jour, remise, fond=0, statut=CC.STATUT_A_COLLECTER, docstatus=0):
        nom = "TEST-CLO-" + uuid4().hex[:12]
        frappe.db.sql(
            """INSERT INTO `tabCloture Caisse`
               (name, creation, caisse, date_cloture, docstatus, statut, especes_remises, fond_conserve)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (nom, "2026-11-25 12:00:00", self.caisse, jour, docstatus, statut, remise, fond))
        return nom

    def situation(self):
        return AL._employes(date(2026, 11, 25), {"date_depart": "2026-11-01"})[0]

    def test_deux_jours_attendent_puis_sont_collectes_separement(self):
        premier = self.cloture("2026-11-24", 100, fond=10)
        dernier = self.cloture("2026-11-25", 50, fond=20)
        s = self.situation()
        self.assertEqual((s["a_remettre"], s["fond"], s["total"]), (150, 20, 170))
        self.assertEqual(s["derniere"]["name"], dernier)
        # La dernière journée collectée ne doit pas masquer l'argent de la veille encore en attente.
        frappe.db.set_value("Cloture Caisse", dernier, {"docstatus": 1, "statut": CC.STATUT_VALIDEE})
        s = self.situation()
        self.assertEqual((s["a_remettre"], s["fond"], s["total"]), (100, 20, 120))
        frappe.db.set_value("Cloture Caisse", premier, {"docstatus": 1, "statut": CC.STATUT_VALIDEE})
        self.assertEqual(self.situation()["total"], 20)

    def test_statuts_et_periode(self):
        self.cloture("2026-10-31", 1000)  # avant le départ
        self.cloture("2026-11-21", 1000, docstatus=2)  # annulée
        self.cloture("2026-11-22", 1000, statut=CC.STATUT_VALIDEE, docstatus=1)
        self.cloture("2026-11-23", 30, statut=CC.STATUT_ECART)
        self.cloture("2026-11-24", 20, statut=CC.STATUT_JUSTIF)
        self.cloture("2026-11-25", 10, fond=5)
        self.cloture("2026-11-26", 1000)  # après la date consultée
        s = self.situation()
        self.assertEqual((s["a_remettre"], s["fond"], s["total"]), (60, 5, 65))


class _AvantCreation(Exception):
    pass


class TestComptageObligatoire(unittest.TestCase):
    def setUp(self):
        if not getattr(frappe.local, "db", None):
            self.skipTest("site initialisé requis")
        self.stack = self.enterContext(ExitStack())
        self.valeurs = None
        mesures = {"encaissements_especes": 0, "depenses_especes": 0, "total_cheques": 0,
                   "total_autres_modes": 0, "debut": "2026-11-25", "data": {}}
        patches = [
            mock.patch.object(frappe, "only_for"),
            mock.patch.object(CL, "_controler_droits"),
            mock.patch.object(frappe.db, "get_value", return_value=None),
            mock.patch.object(CL, "controle_ordre", return_value=None),
            mock.patch.object(CL, "_mesures", return_value=mesures),
            mock.patch.object(CL, "points_controle", return_value=[]),
            mock.patch.object(CL, "verifier_justifications", return_value=("", [])),
            mock.patch.object(CL, "_ouverture", return_value=100),
            mock.patch.object(CL, "_rapprochement", return_value=None),
            mock.patch.object(CL, "compte_pour_autrui", return_value=None),
            mock.patch.object(CC, "config", return_value={"photo_obligatoire": 0}),
            mock.patch.object(frappe, "get_doc", side_effect=self.intercepter),
        ]
        for p in patches:
            self.stack.enter_context(p)

    def intercepter(self, valeurs):
        self.valeurs = valeurs
        raise _AvantCreation()

    def test_vide_refuse_pour_tous_les_modes(self):
        for mode, caisse in (("seule", "Responsable"), ("double", "Employé"), ("seule", CL.CAISSE_GLOBALE)):
            for valeur in (None, "", "   "):
                with self.subTest(mode=mode, caisse=caisse, valeur=valeur), \
                        mock.patch.object(CC, "mode_validation", return_value=mode):
                    with self.assertRaisesRegex(frappe.ValidationError, "réellement comptées"):
                        CL.valider(caisse, "2026-11-25", especes_comptees=valeur, especes_remises=0)
                    self.assertIsNone(self.valeurs)

    def test_zero_explicitement_compte_conserve_lecart(self):
        for mode in ("seule", "double"):
            for valeur in (0, "0"):
                with self.subTest(mode=mode, valeur=valeur), mock.patch.object(CC, "mode_validation", return_value=mode):
                    with self.assertRaises(_AvantCreation):
                        CL.valider("Caisse de test", "2026-11-25", especes_comptees=valeur, especes_remises=0)
                    self.assertEqual((self.valeurs["especes_comptees"], self.valeurs["ecart"],
                                      self.valeurs["fond_conserve"]), (0, -100, 0))

    def test_la_remise_declaree_reste_distincte_du_comptage(self):
        with mock.patch.object(CC, "mode_validation", return_value="double"):
            with self.assertRaises(_AvantCreation):
                CL.valider("Caisse de test", "2026-11-25", especes_comptees=100, especes_remises=80)
        self.assertEqual((self.valeurs["especes_comptees"], self.valeurs["especes_remises"],
                          self.valeurs["fond_conserve"], self.valeurs["ecart"]), (100, 80, 20, 0))
