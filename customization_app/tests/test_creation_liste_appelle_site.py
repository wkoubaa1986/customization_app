"""Listes d'appels, lot du 25/11/2026 — essai SUR LE SITE (copie de prod), sans trace : commit neutralisé, SMS / e-mails
interceptés, alertes enregistrées au lieu d'être envoyées, Server Scripts en base court-circuités, tout annulé à la fin.

- la liste Hors secteur est plafonnée à `plafond_hors_secteur` (30), le reste annoncé « en attente » ;
- un brouillon qui bloque son type et n'a pas bougé depuis 3 jours ouvrés déclenche UNE alerte par jour ;
- le journal compte les exclusions par motif réel (le 25/11 : 99 « partenaire », dont 0 du partenaire) ;
- le passage de 07:00 remet ses compteurs à l'entonnoir (entonnoir_relances.noter, contrat partagé).
Un essai saute quand la copie n'a pas les données."""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, getdate, nowdate

from customization_app import partenaire_clients
from customization_app import relances_config as RC
from customization_app.Maintenance import creation_liste_appelle as L
from customization_app.Maintenance import relance_maintenance_sms as SMS


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

DOCTYPE = L.DOCTYPE
BROUILLON_HORS_SECTEUR = "LIST-06-11-2026-00004"


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("t_creation")
        self.alertes, self.journal, self.erreurs = [], [], []
        self.patches = [
            mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
            # Error Log est en MyISAM : le rollback ne l'efface pas.
            mock.patch.object(frappe, "log_error", side_effect=lambda *a, **k: self.erreurs.append((a, k))),
            mock.patch.object(SMS, "envoyer_sms_winsms", side_effect=AssertionError("SMS interdit pendant l'essai")),
            mock.patch("frappe.sendmail", side_effect=AssertionError("e-mail interdit pendant l'essai")),
            mock.patch.object(RC, "alerter", side_effect=lambda sujet, corps, cfg=None: self.alertes.append((sujet, corps)) or True),
            mock.patch("frappe.model.document.run_server_script_for_doc_event", lambda *a, **k: None),
            mock.patch.object(L, "log", side_effect=self.journal.append),
        ]
        for p in self.patches:
            p.start()
        self.today, self.cfg = nowdate(), RC.config()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        frappe.db.rollback(save_point="t_creation")

    def repartition(self, stats=None):
        clients = L.echeances_a_appeler(self.today, self.cfg, stats=stats)
        exclus = RC.clients_exclus_relance(self.cfg)
        motifs = L.motifs_des_exclus(set(clients) & set(exclus), self.cfg, self.today)
        zones = partenaire_clients.zones_partenaires()
        normal, zone, urgence, ecartes, hors = L.repartir(clients, self.today, self.cfg, exclus, zones,
                                                          lambda c: partenaire_clients.zone_partenaire_du_client(c, zones),
                                                          RC.clients_avec_rdv(self.cfg), [], False, motifs=motifs)
        return clients, exclus, motifs, ecartes, normal, hors


class TestPlafondHorsSecteur(_SansTrace):
    def test_liste_hors_secteur_plafonnee(self):
        _c, _e, _m, _ec, _n, hors = self.repartition()
        plafond = L.plafond_du_type(L.TYPE_HORS_SECTEUR, self.cfg)
        if not plafond or len(hors) <= plafond:
            self.skipTest("pas plus de %s clients hors secteur sur cette copie" % plafond)
        r = L._create_liste_appel_doc(self.today, hors, L.TYPE_HORS_SECTEUR, cfg=self.cfg)
        self.assertEqual(r["count_clients"], plafond)
        self.assertEqual(r["en_attente"], len(hors) - plafond)
        doc = frappe.get_doc(DOCTYPE, r["name"])
        self.assertEqual(len(doc.clients), plafond)
        self.assertTrue(doc.titre.endswith("(+%d en attente)" % (len(hors) - plafond)))
        # Les autres types gardent plafond_liste.
        self.assertEqual(L.plafond_du_type(L.TYPE_NORMAL, self.cfg), RC.config()["plafond_liste"])


class TestAlerteBrouillon(_SansTrace):
    def _brouillon_vieux(self, jours=10):
        clients, _e, _m, _ec, normal, hors = self.repartition()
        noms = sorted(normal) or sorted(hors)
        if not noms:
            self.skipTest("aucun client à appeler sur cette copie")
        r = L._create_liste_appel_doc(self.today, {noms[0]: clients[noms[0]]}, L.TYPE_REQUALIFICATION, cfg=self.cfg)
        vieux = str(add_days(self.today, -jours)) + " 09:00:00"
        frappe.db.sql("update `tabListe Appelle Entretien` set creation = %s, modified = %s where name = %s", (vieux, vieux, r["name"]))
        return r["name"]

    def test_une_alerte_par_brouillon_et_par_jour(self):
        nom = self._brouillon_vieux()
        b1 = L.alerter_brouillons_immobiles(self.today, self.cfg, {L.TYPE_REQUALIFICATION: 7})
        self.assertIn(nom, b1["listes"])
        mien = [c for s, c in self.alertes if nom in s]
        self.assertEqual(len(mien), 1)
        self.assertIn("<b>0</b> ligne(s) appelée(s) sur <b>1</b>", mien[0])
        self.assertIn("7 client(s) attendent", mien[0])
        self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": DOCTYPE, "reference_name": nom, "comment_type": "Info",
                                                     "content": ["like", L.ALERTE_BROUILLON + "%"]}))
        self.assertEqual(frappe.db.get_value(DOCTYPE, nom, "modified").date(), getdate(add_days(self.today, -10)))   # le commentaire ne le « réveille » pas
        self.assertTrue(any("Brouillon bloquant " + nom in l and "alerte envoyée" in l for l in self.journal))
        n = len(self.alertes)
        b2 = L.alerter_brouillons_immobiles(self.today, self.cfg)
        self.assertEqual(len(self.alertes), n)                   # rien de plus le même jour
        self.assertGreaterEqual(b2["deja"], 1)

    def test_le_brouillon_hors_secteur_du_06_11(self):
        b = frappe.db.get_value(DOCTYPE, BROUILLON_HORS_SECTEUR, ["docstatus", "modified"], as_dict=True)
        if not b or b.docstatus != 0 or L.jours_ouvres_ecoules(b.modified, self.today) < 3:
            self.skipTest("%s n'est plus un brouillon immobile sur cette copie" % BROUILLON_HORS_SECTEUR)
        _c, _e, _m, _ec, _n, hors = self.repartition()
        L.alerter_brouillons_immobiles(self.today, self.cfg, {L.TYPE_HORS_SECTEUR: set(hors)})
        corps = [c for s, c in self.alertes if BROUILLON_HORS_SECTEUR in s]
        self.assertEqual(len(corps), 1)
        appeles = frappe.db.count("Appelle Client", {"parent": BROUILLON_HORS_SECTEUR, "a_été_appelé": 1})
        total = frappe.db.count("Appelle Client", {"parent": BROUILLON_HORS_SECTEUR})
        self.assertIn("<b>%d</b> ligne(s) appelée(s) sur <b>%d</b>" % (appeles, total), corps[0])
        # Derrière le brouillon = les candidats Hors secteur qui n'y sont pas déjà (le 25/11 : 183 − 54 = 129).
        dedans = set(frappe.get_all("Appelle Client", filters={"parent": BROUILLON_HORS_SECTEUR}, pluck="client"))
        derriere = len(set(hors) - dedans)
        if derriere:
            self.assertIn("%d client(s) attendent déjà derrière elle" % derriere, corps[0])
        self.assertEqual(L.attente_derriere(BROUILLON_HORS_SECTEUR, set(hors)), derriere)
        self.assertEqual(L.attente_derriere(BROUILLON_HORS_SECTEUR, 7), 7)
        self.assertIsNone(L.attente_derriere(BROUILLON_HORS_SECTEUR, None))

    def test_en_attente_compte_ceux_qui_sont_derriere_le_brouillon(self):
        """26/11/2026 (relecture) : « attente_hors_secteur » comptait aussi les candidats encore dans le brouillon
        bloquant (183 au lieu des 129 de l'alerte, le 25/11). Le passage de 07:00 sépare maintenant « derrière » et
        « déjà dedans », et l'entonnoir reçoit le même chiffre que l'alerte."""
        b = frappe.db.get_value(DOCTYPE, BROUILLON_HORS_SECTEUR, ["docstatus", "type_liste"], as_dict=True)
        if not b or b.docstatus != 0:
            self.skipTest("%s n'est plus un brouillon sur cette copie" % BROUILLON_HORS_SECTEUR)
        res = L._generate_normal_urgence_list(self.today, RC.clients_avec_rdv(self.cfg), create_normal=False, create_urgence=False,
                                              ignore_draft_normal=False, create_zone=False, cfg=self.cfg, create_hors=True)
        if L.TYPE_HORS_SECTEUR not in res["bloques"]:
            self.skipTest("aucun candidat Hors secteur sur cette copie")
        candidats = res["bloques_clients"][L.TYPE_HORS_SECTEUR]
        dedans = L.clients_en_brouillon(L.TYPE_HORS_SECTEUR)
        self.assertEqual(res["bloques"][L.TYPE_HORS_SECTEUR], len(candidats - dedans))
        self.assertEqual(res["deja_en_brouillon"][L.TYPE_HORS_SECTEUR], len(candidats & dedans))
        self.assertEqual(res["bloques"][L.TYPE_HORS_SECTEUR], L.attente_derriere(BROUILLON_HORS_SECTEUR, candidats))   # = l'alerte
        c = L.compteurs_du_passage({"normal_urgence": res})
        self.assertEqual(c["attente_hors_secteur"], len(candidats - dedans))
        self.assertEqual(c["attente_hors_secteur"] + c["en_brouillon_hors_secteur"], len(candidats))
        self.assertTrue(any(l.startswith("[HORS SECTEUR]") and "derrière le brouillon" in l for l in self.journal))

    def test_reglage_a_zero_coupe_l_alerte(self):
        self._brouillon_vieux()
        b = L.alerter_brouillons_immobiles(self.today, dict(self.cfg, jours_brouillon_alerte=0))
        self.assertEqual((b["immobiles"], self.alertes), (0, []))


class TestMotifsDuJournal(_SansTrace):
    def test_un_compteur_par_motif_reel(self):
        stats = {}
        clients, exclus, motifs, ecartes, _n, _h = self.repartition(stats)
        if not clients:
            self.skipTest("aucun candidat sur cette copie")
        candidats_exclus = set(clients) & set(exclus)
        self.assertEqual(ecartes["partenaire"], len(candidats_exclus & partenaire_clients.clients_geres()))
        self.assertEqual(sum(ecartes[m] for m in L.MOTIFS_EXCLUSION) + ecartes.get("autre", 0), len(candidats_exclus))
        self.assertEqual(ecartes.get("autre", 0), 0, "la règle commune d'exclusion a un motif que le journal ne connaît pas")
        self.assertEqual(stats["a_repartir"], len(clients))
        self.assertEqual(stats["clients"], stats["a_repartir"] + stats["intervention_recente"])


class TestPassage7h(_SansTrace):
    def test_compteurs_remis_a_l_entonnoir(self):
        faux = types.ModuleType("customization_app.entonnoir_relances")
        faux.noter = mock.Mock()
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": faux}):
            results = L._run_cron_internal()
        faux.noter.assert_called_once()
        source, compteurs = faux.noter.call_args[0]
        self.assertEqual(source, "listes")
        self.assertEqual(faux.noter.call_args[1]["jour"], self.today)
        for cle in ("visites_candidates", "exclus_partenaire", "exclus_a_requalifier", "ecartes_appel_recent", "attente_hors_secteur",
                    "liste_normal", "brouillons_immobiles", "brouillons_alertes"):
            self.assertIn(cle, compteurs)
        self.assertEqual(compteurs, L.compteurs_du_passage(results))
        self.assertTrue(any(l.startswith("[REPARTITION]") and "a_requalifier" in l for l in self.journal))

    def test_un_passage_qui_plante_est_note(self):
        faux = types.ModuleType("customization_app.entonnoir_relances")
        faux.noter = mock.Mock()
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": faux}), \
                mock.patch.object(L, "_generate_normal_urgence_list", side_effect=RuntimeError("panne")):
            with self.assertRaises(RuntimeError):
                L._run_cron_internal()                  # run_safely journalise et alerte, comme avant
        self.assertEqual(faux.noter.call_args[0][1]["erreur"], 1)

    def test_module_entonnoir_absent_ne_bloque_pas(self):
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": None}):
            self.assertFalse(L._noter_entonnoir({}, self.today))
        self.assertTrue(any("entonnoir_relances absent" in l for l in self.journal))


if __name__ == "__main__":
    unittest.main()
