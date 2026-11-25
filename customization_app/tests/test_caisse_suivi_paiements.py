"""Chaque paiement pris par UNE clôture, compter dans l'ordre, montants des chèques et traites (25/11/2026).

Lancer dans le site (jamais `bench run-tests`) : `unittest customization_app.tests.test_caisse_suivi_paiements` depuis
frappe-bench/sites, avec frappe.db.commit neutralisé (essai sans trace) : le scénario crée des paiements et des
clôtures sur une journée sans activité, puis tout est annulé par le rollback du lanceur."""
import json
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from customization_app import caisse_cloture as CL
from customization_app import caisse_collecte as CC
from customization_app import caisse_paiements as CP


class TestPur(unittest.TestCase):
    def test_lire_montants(self):
        self.assertEqual(CL.lire_montants("455+955"), ([455.0, 955.0], 1410.0))
        self.assertEqual(CL.lire_montants("455,500 + 955"), ([455.5, 955.0], 1410.5))
        self.assertEqual(CL.lire_montants(""), ([], 0.0))
        for mauvais in ("455+abc", "455+-3", "1.200,5", "0"):
            with self.assertRaises(ValueError):
                CL.lire_montants(mauvais)
        self.assertEqual(CL.texte_montants([455, 1200.5]), "455,000 + 1\xa0200,500")
        # Ce que le serveur écrit, il sait le relire (la carte de collecte le repropose tel quel).
        self.assertEqual(CL.lire_montants(CL.texte_montants([455, 1200.5]))[1], 1655.5)

    def test_remise_identique_avec_montants(self):
        d = {"especes": 100, "nb_cheques": 2, "nb_traites": 0, "montant_cheques": 1410.0}
        self.assertTrue(CC.remise_identique(d, dict(d)))
        self.assertFalse(CC.remise_identique(d, dict(d, montant_cheques=1400.0)))
        self.assertTrue(CC.remise_identique({"especes": 1, "nb_cheques": 1, "nb_traites": 0},
                                            {"especes": 1, "nb_cheques": 1, "nb_traites": 0, "montant_cheques": 9}))

    def test_a_sa_place(self):
        l = {"cloture": "CLO-1", "caisse": "Salma", "date": "2026-11-23"}
        self.assertTrue(CP.a_sa_place(None, "X", "2026-11-23", "2026-11-23"))
        self.assertTrue(CP.a_sa_place(l, "Salma", "2026-11-20", "2026-11-23"))
        self.assertFalse(CP.a_sa_place(l, "Radhouan", "2026-11-23", "2026-11-23"))   # FAYCEL DRISS : une seule fois
        self.assertFalse(CP.a_sa_place(l, "Salma", "2026-11-21", "2026-11-21"))

    def test_caisse_de_reprise(self):
        p = [("Nejib", "2026-10-28")]
        self.assertEqual(CP.caisse_de_reprise(p, {("Nejib", "2026-10-28")}, getdate("2026-10-28")), "Nejib")  # 280 DT
        self.assertIsNone(CP.caisse_de_reprise(p, set(), getdate("2026-10-28")))       # pas encore compté : il le prendra
        deux = [("Radhouan", "2026-11-21"), ("Salma", "2026-11-23")]
        self.assertEqual(CP.caisse_de_reprise(deux, set(deux), getdate("2026-11-23")), "Salma")   # le jour de saisie
        self.assertIsNone(CP.caisse_de_reprise(deux, {deux[0]}, getdate("2026-11-23")))

    def test_appliquer_clotures(self):
        employes = [{"employe": "Radhouan", "total": 112.0, "totaux_par_mode": {"Espèces": 112.0}, "orders": [
            {"payments": [{"name": "PE-1", "mode": "Espèces", "amount": 112.0}]}]}]
        anciens = {"paiements": [{"name": "PE-2", "mode": "Espèces", "amount": 5.0, "saisi_par": "Salma"}]}
        grand = {"Espèces": 112.0}
        lien = {"PE-1": {"cloture": "CLO-1814", "caisse": "Salma", "date": "2026-11-23"}}
        with mock.patch.object(CP, "_champ_existe", return_value=True), \
                mock.patch.object(CP, "liens", return_value=lien), \
                mock.patch.object(frappe.db, "sql", return_value=[]):
            CP.appliquer_clotures(employes, anciens, grand, "2026-11-21", "2026-11-21")
        self.assertEqual(employes[0]["orders"][0]["payments"][0]["deja_compte"]["cloture"], "CLO-1814")
        self.assertEqual(employes[0]["totaux_par_mode"]["Espèces"], 0)
        self.assertEqual(grand["Espèces"], 0)
        self.assertEqual(anciens["total"], 5.0)

    def test_rappel_ne_reclame_plus_un_jour_avant_le_dernier_comptage(self):
        a = [{"caisse": "J", "date": "2026-11-10", "etat": "non_comptee"}, {"caisse": "J", "date": "2026-11-24", "etat": "non_comptee"},
             {"caisse": "K", "date": "2026-11-10", "etat": "non_comptee"}]
        par = CC.non_comptees_par_caisse(a, (), {"J": "2026-11-23", "K": None})
        self.assertEqual({k: [x["date"] for x in v] for k, v in par.items()}, {"J": ["2026-11-24"], "K": ["2026-11-10"]})

    def test_pris_par(self):
        data = {"employees": [{"employe": "S", "orders": [{"payments": [
                    {"name": "A", "mode": "Espèces", "amount": 10}, {"name": "B", "mode": "Espèces", "amount": 3, "hors_periode": True},
                    {"name": "C", "mode": "Chèque", "amount": 455, "deja_compte": {"cloture": "X"}}]}]},
                                 {"employe": "T", "orders": [{"payments": [{"name": "D", "mode": "Espèces", "amount": 1}]}]}],
                "anciens": {"paiements": [{"name": "E", "mode": "Traite bancaire LC", "amount": 955, "saisi_par": "S"},
                                          {"name": "F", "mode": "Espèces", "amount": 2, "saisi_par": "S", "exclu": True}]}}
        self.assertEqual([x["name"] for x in CP.pris_par(data, "S")], ["A", "E"])
        self.assertEqual(CL.pieces_attendues(data, "S"), {"Chèque": {"nb": 0, "total": 0.0}, "Traite": {"nb": 1, "total": 955.0}})


class TestScenarioSite(unittest.TestCase):
    """Le circuit complet sur une journée vide du dev, au nom d'une employée (rollback du lanceur)."""

    @classmethod
    def setUpClass(cls):
        if getattr(frappe.local, "db", None) is None:
            raise unittest.SkipTest("besoin d'un site")
        if getattr(frappe.db.commit, "__name__", "") != "<lambda>":
            raise unittest.SkipTest("à lancer avec frappe.db.commit neutralisé (essai sans trace)")
        cls.user = frappe.db.get_value("Employee", {"employee_name": "Salma Ben Saïd", "status": "Active"}, "user_id")
        if not cls.user or not CP.suivi_depuis():
            raise unittest.SkipTest("Salma ou la mise en route manquent")
        cls.caisse = "Salma Ben Saïd"
        derniere = frappe.db.sql("SELECT MAX(date_cloture) FROM `tabCloture Caisse` WHERE caisse = %s AND docstatus < 2",
                                 cls.caisse)[0][0]
        # Un paiement saisi après le comptage de SON jour (date comptable = jour de saisie) : le cas réel.
        cls.j1 = max(getdate(nowdate()), getdate(add_days(derniere, 1)))
        cls.j2 = getdate(add_days(cls.j1, 1))

    def setUp(self):
        frappe.set_user(self.user)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _paiement(self, montant, jour):
        from erpnext.accounts.party import get_party_account
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        societe = frappe.db.get_value("Company", {}, "name")
        pe = frappe.get_doc({
            "doctype": "Payment Entry", "payment_type": "Receive", "company": societe, "posting_date": jour,
            "mode_of_payment": "Espèces", "party_type": "Customer", "party": client,
            "paid_from": get_party_account("Customer", client, societe),
            "paid_to": "Espèces - A&S", "paid_amount": montant, "received_amount": montant,
            "source_exchange_rate": 1, "target_exchange_rate": 1, "remarks": "ESSAI suivi des paiements par clôture"})
        pe.flags.ignore_permissions = True
        pe.insert(ignore_permissions=True)
        pe.submit()
        return pe

    def test_circuit(self):
        j1, j2, caisse = str(self.j1), str(self.j2), self.caisse
        with mock.patch.object(CC, "_activite_du_jour", return_value={}):   # jours d'avant : vides pour ce scénario
            pe1 = self._paiement(100, j1)
            r1 = CL.valider(caisse, j1, especes_comptees=100, especes_remises=100, justifications="{}",
                            cheques_montants="455+955")
            c1 = frappe.get_doc("Cloture Caisse", r1["name"])
            self.assertEqual(frappe.db.get_value("Payment Entry", pe1.name, "custom_cloture_caisse"), c1.name)
            self.assertIn(pe1.name, [x["name"] for x in json.loads(c1.paiements_pris)])
            self.assertEqual((c1.nb_cheques_remis, flt(c1.montant_cheques_remis), c1.detail_cheques_remis),
                             (2, 1410.0, "455,000 + 955,000"))
            self.assertEqual(flt(c1.especes_remises), 100)          # la régression du 24/11 (remise à 0) : plus jamais

            # Saisi APRÈS le comptage de son jour : jamais pris… puis repris par le comptage suivant.
            pe2 = self._paiement(50, j1)
            frappe.db.set_value("Payment Entry", pe2.name, "creation", frappe.utils.add_to_date(c1.compte_le, seconds=5),
                                update_modified=False)
            e = CL.etat(caisse, j2)
            self.assertEqual([x["name"] for x in e["repris"]], [pe2.name])
            self.assertEqual(flt(e["encaissements_especes"]), 50)
            r2 = CL.valider(caisse, j2, especes_comptees=50, especes_remises=50, justifications="{}")
            self.assertEqual(frappe.db.get_value("Payment Entry", pe2.name, "custom_cloture_caisse"), r2["name"])
            # Le rapport : pe2 compte au j2 (repris), plus au j1 (déjà compté).
            d1 = CL.get_data(j1, j1, employe=caisse)
            d2 = CL.get_data(j2, j2, employe=caisse)
            self.assertEqual(flt(d1["recap"]["par_mode"].get("Espèces")), 100)
            self.assertEqual(flt(d2["recap"]["par_mode"].get("Espèces")), 50)
            self.assertTrue(next(p for p in d1["anciens"]["paiements"] if p["name"] == pe2.name).get("deja_compte"))

            # Compter dans l'ordre / collecter dans l'ordre.
            self.assertIn("déjà comptée", CL.controle_ordre(caisse, add_days(j1, -1)) or "")
            self.assertIn("ne le rouvre plus", CL.controle_ordre(caisse, j1, rouvrir=True) or "")
            self.assertIn("Collectez d'abord", CC.bloque_collecte(frappe.get_doc("Cloture Caisse", r2["name"])) or "")
            self.assertIsNone(CC.bloque_collecte(c1))

            # Annulé après comptage, sans réémission : un point à justifier au comptage suivant.
            pe1.reload()
            pe1.cancel()
            points = CP.disparus(caisse, add_days(j2, 1))
            self.assertEqual([p["paiement"] for p in points], [pe1.name])
            self.assertIn("annulé le", points[0]["libelle"])
            CP.verifier_annulation(pe1.name)                          # la notification ne casse rien
            # Réémis à l'identique : il hérite de la clôture, plus rien à justifier.
            pe1b = frappe.copy_doc(pe1)
            pe1b.amended_from = pe1.name
            pe1b.insert(ignore_permissions=True)
            pe1b.submit()
            self.assertEqual(frappe.db.get_value("Payment Entry", pe1b.name, "custom_cloture_caisse"), c1.name)
            self.assertEqual(CP.disparus(caisse, add_days(j2, 1)), [])
