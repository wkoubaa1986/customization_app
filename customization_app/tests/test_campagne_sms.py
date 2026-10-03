"""Assistant Campagne SMS (03/10/2026) : classement des clients (pur), ciblage avec consentement, aperçu/coût, enregistrement
d'un brouillon, envoi unitaire simulé en dev. Rien ne part : simulation_dev() en developer_mode."""
from __future__ import annotations

import json
import unittest

import frappe

from customization_app import campagne_sms as CS


def _c(**kw):
    base = {"name": "C1", "customer_group": "Individuel", "custom_liste_telephone": "25511119", "custom_envoi_sms": "Oui",
            "custom_intéressé_par_le_service_entretien": "Oui", "custom_autoriser_accès_fiche_client": 1}
    base.update(kw)
    return frappe._dict(base)


class TestClassement(unittest.TestCase):
    F = {"consent": True, "autorisation": True, "exclure_partenaire": True, "interesse": "", "secteurs": []}

    def test_motifs(self):
        self.assertIsNone(CS.classer_client(_c(), "Secteur 1", self.F, set()))
        self.assertEqual(CS.classer_client(_c(custom_envoi_sms="Non"), "", self.F, set()), "a refusé les SMS")
        self.assertEqual(CS.classer_client(_c(custom_autoriser_accès_fiche_client=0), "", self.F, set()), "fiche client non autorisée")
        self.assertEqual(CS.classer_client(_c(), "", self.F, {"C1"}), "géré par le partenaire")
        self.assertEqual(CS.classer_client(_c(custom_liste_telephone="71 123 456"), "", self.F, set()), "aucun numéro mobile valide")   # fixe
        self.assertEqual(CS.classer_client(_c(), "Secteur 2", dict(self.F, secteurs=["Secteur 1"]), set()), "hors secteurs choisis")
        self.assertEqual(CS.classer_client(_c(), "", dict(self.F, interesse="Non"), set()), "intéressé entretien ≠ Non")
        self.assertIsNone(CS.classer_client(_c(custom_envoi_sms="Non"), "", dict(self.F, consent=False), set()))     # refus ignoré volontairement


class TestBase(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("campagne_sms")
        self.assertTrue(CS.simulation_dev(), "les tests n'envoient jamais : developer_mode attendu")

    def tearDown(self):
        frappe.db.rollback(save_point="campagne_sms")

    def test_cibler_apercu_enregistrer_test(self):
        groupe = frappe.db.get_value("Customer", {"custom_envoi_sms": "Oui", "custom_liste_telephone": ["like", "%2%"], "disabled": 0}, "customer_group")
        f = {"groupes": [groupe], "secteurs": [], "consent": True, "autorisation": True, "exclure_partenaire": True, "interesse": "", "clients": []}
        c = CS.cibler(json.dumps(f))
        self.assertTrue(c["stats"]["cibles"] > 0)
        self.assertTrue(all(l["exclu"] or l["numeros"] for l in c["lignes"]))
        refus = [l for l in c["lignes"] if l["exclu"] == "a refusé les SMS"]
        self.assertTrue(all(not l["numeros"] for l in refus))
        lignes = [l for l in c["lignes"] if not l["exclu"]][:2]
        msg = "Bonjour {{ nom_client }}, promo filtres — 10 % jusqu'à vendredi. Aqua World"
        a = CS.apercu(msg, json.dumps(lignes))
        self.assertEqual(len(a["apercus"]), len(lignes))
        self.assertIn(lignes[0]["nom"], a["apercus"][0]["texte"])
        self.assertEqual(a["segments"], a["segments_par_sms"] * a["numeros"])
        r = CS.enregistrer("Essai test", msg, json.dumps(f), json.dumps(lignes))
        doc = frappe.get_doc("Compagne SMS", r["name"])
        self.assertEqual((doc.statut, doc.docstatus, len(doc.liste_des_clients)), ("Brouillon", 0, len(lignes)))
        self.assertTrue(all(l.envoyer for l in doc.liste_des_clients))
        t = CS.envoyer_test("25511119", msg, lignes[0]["client"], lignes[0]["nom"], lignes[0]["groupe"])
        self.assertEqual(t["statut"], "Simulé")
        self.assertEqual(CS._envoyer_un("25511119", "x"), ("Simulé", "🧪 dev"))
        with self.assertRaises(frappe.ValidationError):
            CS.envoyer_test("71123456", msg)                                   # fixe refusé
        with self.assertRaises(frappe.ValidationError):
            CS.enregistrer("x", "{{ nom_client ", json.dumps(f), json.dumps(lignes))   # balise cassée
