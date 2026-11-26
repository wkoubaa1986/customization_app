"""SMS de réveil (25/11/2026) sur la base : à 180 j, le stock « À requalifier » appelé en octobre 2026 se réveille au
printemps 2027 (à 365 j, personne avant octobre 2027), puis pas avant un an (relecture du 26/11/2026). La date du jour
est simulée ; envoi en mode simulation, SMS et e-mail réels simulés pour LEVER, point de sauvegarde annulé, aucun commit."""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, getdate

from customization_app import relances_config as RC
from customization_app.Maintenance import sms_reveil as S


# 26/11/2026 : les documents créés par ces essais n'entrent pas dans la file de recherche globale (Redis) — le rollback ne
# l'annule pas, et le dev en gardait des centaines d'entrées vers des documents qui n'existent plus.
_RECHERCHE = {}


def setUpModule():
    try:
        _RECHERCHE["avant"] = frappe.local.conf.get("disable_global_search")
        frappe.local.conf["disable_global_search"] = 1
    except Exception:
        pass


def tearDownModule():
    try:
        if _RECHERCHE.get("avant") is None:
            frappe.local.conf.pop("disable_global_search", None)
        else:
            frappe.local.conf["disable_global_search"] = _RECHERCHE["avant"]
    except Exception:
        pass

PRINTEMPS = "2027-04-30"


class TestReveilSite(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if not frappe.db.has_column("Customer", "custom_sms_reveil_le"):
            self.skipTest("champ custom_sms_reveil_le absent")
        self.faux = types.ModuleType("customization_app.entonnoir_relances")
        self.faux.noter = mock.Mock()
        self._patches = [
            mock.patch.object(frappe.db, "commit", side_effect=lambda *a, **k: None),
            mock.patch("frappe.sendmail", side_effect=AssertionError("aucun e-mail en test")),
            mock.patch("customization_app.Maintenance.relance_maintenance_sms.envoyer_sms_winsms",
                       side_effect=AssertionError("aucun SMS en test")),
            mock.patch("customization_app.Maintenance.relance_maintenance_sms.simulation_envois", return_value=True),
            mock.patch.object(RC, "alerter", side_effect=AssertionError("aucune alerte en test")),
            mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": self.faux}),
        ]
        for p in self._patches:
            p.start()
        frappe.local._config_relances = None
        frappe.db.savepoint("reveil_site")

    def tearDown(self):
        frappe.db.rollback(save_point="reveil_site")
        for p in reversed(self._patches):
            p.stop()
        frappe.local._config_relances = None

    def _cfg(self, jours):
        return dict(RC.config(), jours_reveil_requalifies=jours)

    def test_candidats_respectent_le_delai(self):
        c180 = S.candidats(PRINTEMPS, self._cfg(180), plafond=10000)
        c365 = S.candidats(PRINTEMPS, self._cfg(365), plafond=10000)
        if not c180:
            self.skipTest("aucun « À requalifier » éligible au printemps 2027")
        limite = getdate(add_days(PRINTEMPS, -180))
        for c in c180:
            for d in (c.dernier_appel, c.dernier_reveil):
                self.assertTrue(not d or getdate(d) <= limite, c.name)
        self.assertTrue({c.name for c in c365} <= {c.name for c in c180})
        self.assertLess(len(c365), len(c180))                                   # le délai de 365 j attendait encore
        self.assertEqual(len(S.candidats(PRINTEMPS, self._cfg(180))), min(S.REVEIL_PAR_JOUR, len(c180)))   # plafond du jour

    def test_envoi_simule_trace_et_pas_deux_fois(self):
        with mock.patch("customization_app.Maintenance.sms_reveil.nowdate", return_value=PRINTEMPS), \
                mock.patch.object(RC, "config", return_value=self._cfg(180)):
            prevus = S.candidats(PRINTEMPS, self._cfg(180))
            if not prevus:
                self.skipTest("aucun « À requalifier » éligible au printemps 2027")
            resume = S.envoyer_reveils()
            self.assertEqual(resume["candidats"], len(prevus))
            self.assertEqual(resume["envoyes"] + resume["sans_numero"] + resume["echecs"], len(prevus))
            envoyes = [c.name for c in prevus if str(frappe.db.get_value("Customer", c.name, "custom_sms_reveil_le") or "") == PRINTEMPS]
            self.assertEqual(len(envoyes), resume["envoyes"])
            for nom in envoyes:
                self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Customer", "reference_name": nom, "comment_type": "Info",
                                                             "content": ["like", "📨 SMS de réveil envoyé le 30/04/2027 (aucun contact depuis au moins 180 j)%"]}))
            self.faux.noter.assert_called_once()
            self.assertEqual(self.faux.noter.call_args[0][0], "reveil")
            self.assertEqual(self.faux.noter.call_args[0][1]["envoyes"], resume["envoyes"])
            self.assertEqual((self.faux.noter.call_args[0][1]["delai"], self.faux.noter.call_args[0][1]["mode"]), ("180 j", "simulé"))
            # Le même jour, les réveillés ne sont plus candidats : pas un SMS par matin.
            self.assertFalse({c.name for c in S.candidats(PRINTEMPS, self._cfg(180), plafond=10000)} & set(envoyes))
            if not envoyes:
                return
            # Ni 180 j plus tard (la règle d'avant les réveillait de nouveau : deux SMS par an), mais bien un an après.
            automne, an = add_days(PRINTEMPS, 180), add_days(PRINTEMPS, 365)
            self.assertFalse({c.name for c in S.candidats(automne, self._cfg(180), plafond=10000)} & set(envoyes))
            self.assertTrue(set(envoyes) <= {c.name for c in S.candidats(an, self._cfg(180), plafond=10000)})

    def test_essai_a_blanc_n_ecrit_rien(self):
        with mock.patch("customization_app.Maintenance.sms_reveil.nowdate", return_value=PRINTEMPS), \
                mock.patch.object(RC, "config", return_value=self._cfg(180)):
            prevus = S.candidats(PRINTEMPS, self._cfg(180))
            avant = {c.name: frappe.db.get_value("Customer", c.name, "custom_sms_reveil_le") for c in prevus}
            resume = S.envoyer_reveils(dry_run=True)
        self.assertEqual(resume["envoyes"], 0)
        self.assertEqual({n: frappe.db.get_value("Customer", n, "custom_sms_reveil_le") for n in avant}, avant)
        self.faux.noter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
