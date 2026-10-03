"""Clients gérés par le partenaire & zones partenaires (03/10/2026). Règles pures d'abord, puis le marquage, la page et
le hook dans un savepoint (le champ doit exister : patch ensure_partenaire_fields)."""
from __future__ import annotations

import unittest

import frappe

from customization_app import partenaire_clients as PC
from customization_app import modeles_sms as MS
from customization_app.Maintenance import relance_maintenance_sms as REL

ZONES = {"HR-EMP-00007": {"nom": "Economic Aqua Solution", "gouvernorats": {"Sousse", "Monastir", "Mahdia"}}}


class TestRegles(unittest.TestCase):
    def test_classer(self):
        self.assertEqual(PC.classer("Secteur 1", False, None), "secteur")
        self.assertEqual(PC.classer("Hors Secteur, Secteur 3", False, None), "secteur")      # une vraie adresse suffit
        self.assertEqual(PC.classer("Hors Secteur", False, {"nom": "X"}), "zone_partenaire")
        self.assertEqual(PC.classer("Hors Secteur", False, None), "hors_secteur")
        self.assertEqual(PC.classer("", False, None), "hors_secteur")
        self.assertEqual(PC.classer("Secteur 1", True, None), "exclu")                        # géré par lui : toujours exclu

    def test_zone_partenaire(self):
        norm = lambda g: {"monastire": "Monastir", "sousse": "Sousse"}.get((g or "").strip().lower(), (g or "").strip())
        self.assertEqual(PC.zone_partenaire(["Tunis", "Monastire"], ZONES, norm)["nom"], "Economic Aqua Solution")
        self.assertIsNone(PC.zone_partenaire(["Tunis", "Gafsa"], ZONES, norm))
        self.assertIsNone(PC.zone_partenaire([], ZONES, norm))
        self.assertIsNone(PC.zone_partenaire(["Sousse"], {}, norm))

    def test_sms_zone_partenaire(self):
        T = dict(MS.DEFAUTS)
        m = REL.message_relance(T, nom_client="Ali", appareil="votre osmoseur", cout=40, telephones="98 511 119", lien_boutique="https://b",
                                secteur="Hors Secteur", lien_rdv="https://x/rdv", partenaire="Economic Aqua Solution")
        self.assertIn("notre equipe partenaire Economic Aqua Solution", m)
        self.assertIn("Prenez RDV en ligne: https://x/rdv", m)
        self.assertNotIn("Cout main-d'oeuvre", m)                                               # hors secteur : jamais de coût
        # sans partenaire nommé, le texte générique ; dans nos secteurs, jamais le texte de zone
        self.assertNotIn("partenaire", REL.message_relance(T, nom_client="Ali", appareil="x", cout=40, telephones="t", lien_boutique="b",
                                                           secteur="Hors Secteur", lien_rdv="https://x/rdv"))
        self.assertNotIn("partenaire", REL.message_relance(T, nom_client="Ali", appareil="x", cout=40, telephones="t", lien_boutique="b",
                                                           secteur="Secteur 1", lien_rdv="https://x/rdv", partenaire="EAS"))
        self.assertFalse(MS.analyser(MS.DEFAUTS["relance_zone_partenaire"])["unicode"])


class TestMarquage(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("partenaire_clients")
        if not PC.champs_presents():
            self.skipTest("champs absents (patch ensure_partenaire_fields non joué)")
        if not frappe.db.exists("User", PC.PARTNER_USER):
            self.skipTest("compte partenaire absent")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="partenaire_clients")

    def _client(self, user):
        frappe.set_user(user)
        c = frappe.get_doc({"doctype": "Customer", "customer_name": "Essai partenaire %s" % frappe.generate_hash(length=5),
                            "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
                            "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"), "customer_type": "Individual"})
        c.flags.ignore_permissions = True
        c.insert()
        frappe.set_user("Administrator")
        return c

    def test_cree_par_le_partenaire_est_marque_et_exclu(self):
        c = self._client(PC.PARTNER_USER)
        self.assertEqual(c.get(PC.CHAMP), 1)
        self.assertEqual(c.get(PC.CHAMP_PARTENAIRE), PC.employe_partenaire())
        self.assertIn(c.name, PC.clients_geres())
        e = PC.etat(c.name)
        self.assertEqual((e["gere"], e["cree_par_partenaire"]), (1, True))
        self.assertIn(c.name, [l["name"] for l in PC.liste("geres")])
        r = PC.basculer([c.name], 0)                                                             # repris chez nous
        self.assertEqual(r["clients"], [c.name])
        self.assertNotIn(c.name, PC.clients_geres())
        self.assertIn(c.name, [l["name"] for l in PC.liste("a_verifier")])                       # créé par lui, non marqué → à vérifier
        self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Customer", "reference_name": c.name, "content": ["like", "%Repris par nous%"]}))

    def test_cree_par_nous_reste_a_nous(self):
        c = self._client("Administrator")
        self.assertFalse(c.get(PC.CHAMP))
        self.assertNotIn(c.name, PC.clients_geres())
        PC.basculer([c.name], 1)
        self.assertIn(c.name, PC.clients_geres())
        self.assertEqual(frappe.db.get_value("Customer", c.name, PC.CHAMP_PARTENAIRE), PC.employe_partenaire())

    def test_resume_et_acces(self):
        r = PC.resume()
        self.assertTrue(r["champs"])
        self.assertTrue(all(k in r for k in ("geres", "a_verifier", "nos_en_zone", "zones")))
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            PC.resume()
