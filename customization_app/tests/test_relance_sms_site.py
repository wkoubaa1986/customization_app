"""Relance SMS d'entretien, revue du 25/11/2026 — essai SUR LE SITE (copie de prod du 25/11 12:26), sans trace :
commit neutralisé, envoi SMS / e-mail / alerte interdits (ils lèvent une erreur), envoi simulé (sauf le test de la vraie
boucle d'envoi, où la passerelle et l'e-mail sont des Mock qui lèvent toujours), journal maintenance_sms neutralisé,
tout annulé.

Jour simulé : le 26/11/2026 (le passage de 10:00 du 25 est déjà dans la copie). Sur cette copie, A R S D (adoucisseur
+ fontaine, deux échéanciers) recevait deux SMS1 ce jour-là. Un test se saute quand la copie ne contient plus son cas."""
from __future__ import annotations

import sys
import types
import unittest
from collections import Counter
from unittest import mock

import frappe

from customization_app import relances_config as RC
from customization_app.Maintenance import relance_maintenance_sms as R


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

JOUR = "2026-11-26"


def interdit(*a, **k):
    raise AssertionError("ENVOI RÉEL TENTÉ")


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("sms_lot4")
        frappe.local._config_relances = None
        self._patches = [
            mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
            mock.patch.object(R, "envoyer_sms_winsms", interdit),
            mock.patch.object(frappe, "sendmail", interdit),
            mock.patch.object(RC, "alerter", interdit),
            mock.patch.object(R, "simulation_envois", lambda: True),
            mock.patch.object(R, "nowdate", lambda: JOUR),
            mock.patch.object(RC, "nowdate", lambda: JOUR),
            mock.patch.object(R, "log", lambda *a, **k: None),             # le journal maintenance_sms du dev reste propre
            mock.patch.object(R, "_logger", lambda: mock.Mock()),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        frappe.db.rollback(save_point="sms_lot4")
        frappe.local._config_relances = None

    def reglage(self, valeur, plafond=None):
        """Le réglage « un_sms_par_client_par_jour » pour ce test (posé dans le mémo de config de la requête) ; `plafond`
        remplace max_sms_par_jour (un très grand plafond = aucun secteur coupé, comparaisons dans le même périmètre)."""
        frappe.local._config_relances = None
        cfg = RC.config()
        cfg["un_sms_par_client_par_jour"] = valeur
        if plafond is not None:
            cfg["max_sms_par_jour"] = plafond
        return cfg

    def envoi_de(self, p):
        """L'envoi réel (regrouper_envois) d'une entrée d'aperçu : ses visites rangées en paquets (échéancier × type)
        comme les fabrique le cron, donc même principale, mêmes familles du message, même nb_paquets."""
        paquets = {}
        for v in p["visites"]:
            paquets.setdefault((v["maintenance"], v["sms_type"]), []).append(dict(v, customer=p["customer"], secteur=p["secteur"]))
        envoi = R.regrouper_envois(list(paquets.values()), [f["code"] for f in RC.familles()], True)[0]
        self.assertEqual(envoi["nb_paquets"], p["nb_sms_avant"])
        return envoi


class TestApercuSite(_SansTrace):
    def test_une_entree_par_client_avec_ses_visites(self):
        self.reglage(1)
        res = R.envoyer_relances_maintenance(dry_run=True)
        prev, s = res["preview"], res["summary"]
        if not prev:
            self.skipTest("aucun SMS dû le %s sur cette copie" % JOUR)
        clients = [p["customer"] for p in prev]
        self.assertEqual(len(clients), len(set(clients)))
        self.assertEqual(s["envois_candidats"], s["clients_candidats"])
        self.assertEqual(s["sms_evites"], s["total_sms_candidates"] - s["envois_candidats"])
        self.assertLessEqual(len(prev), s["total_sms_prevus"])
        if not s["erreurs"] and not s["sms_hors_plafond"]:
            self.assertEqual(len(prev), s["total_sms_prevus"])
            self.assertEqual(sum(len(p["visites"]) for p in prev), s["visites_candidates"])
        for p in prev:
            couvertes = {(v["maintenance"], v["sms_type"]) for v in p["visites"]}
            self.assertIn((p["maintenance"], p["sms_type"]), couvertes)          # la principale est une des visites
            self.assertTrue(all(v["item_code"] and v["scheduled_date"] for v in p["visites"]))
        self.assertTrue(res["log"][0].startswith("[SUMMARY] date=%s, total_ms=" % JOUR))
        self.assertIn("sms_evites=%s" % s["sms_evites"], res["log"][0])

    def test_client_a_plusieurs_sms_un_seul_message_qui_couvre_tout(self):
        """Relecture du 25/11/2026 : `multi` (aperçu, APRÈS le plafond) et clients_plusieurs_sms (paquets, AVANT le
        plafond) ne se comparent que si aucun secteur n'est coupé — plafond neutralisé dans les deux passages."""
        self.reglage(0, plafond=10 ** 6)
        avant = R.envoyer_relances_maintenance(dry_run=True)
        par_client = Counter(p["customer"] for p in avant["preview"])
        multi = [c for c, n in par_client.items() if n > 1]
        if not multi:
            self.skipTest("aucun client à plusieurs SMS le %s sur cette copie" % JOUR)
        self.assertEqual(avant["summary"]["sms_evites"], 0)                       # réglage 0 : l'envoi d'avant
        self.reglage(1, plafond=10 ** 6)
        apres = R.envoyer_relances_maintenance(dry_run=True)
        self.assertEqual((avant["summary"]["sms_hors_plafond"], apres["summary"]["sms_hors_plafond"]), (0, 0))
        self.assertEqual(len(apres["preview"]), len(par_client))
        self.assertEqual(apres["summary"]["sms_evites"], len(avant["preview"]) - len(apres["preview"]))
        self.assertEqual(apres["summary"]["clients_plusieurs_sms"], len(multi))
        for c in multi:
            entrees = [p for p in apres["preview"] if p["customer"] == c]
            self.assertEqual(len(entrees), 1)
            e = entrees[0]
            self.assertEqual({(v["maintenance"], v["sms_type"]) for v in e["visites"]},
                             {(p["maintenance"], p["sms_type"]) for p in avant["preview"] if p["customer"] == c})
            self.assertEqual(e["nb_sms_avant"], par_client[c])
            # Le texte : celui du paquet de la famille prioritaire, avec son coût — mot pour mot l'un de ceux d'avant
            fam, cout = RC.cout_entretien(e["familles"])
            self.assertEqual(e["primary_family"], fam)
            self.assertIn(R.texte_appareil(e["familles_message"]), e["message"])
            self.assertIn(e["message"], [p["message"] for p in avant["preview"] if p["customer"] == c])
            self.assertEqual(e["cout"], float(cout) if cout is not None else None)

    def test_le_plafond_ne_change_pas_le_compte_des_clients_a_plusieurs_sms(self):
        """clients_plusieurs_sms est compté sur les paquets, AVANT le plafond : un secteur coupé ne le change pas."""
        self.reglage(1, plafond=10 ** 6)
        libre = R.envoyer_relances_maintenance(dry_run=True)["summary"]
        if not libre["envois_candidats"]:
            self.skipTest("aucun SMS dû le %s sur cette copie" % JOUR)
        self.reglage(1, plafond=0)
        coupe = R.envoyer_relances_maintenance(dry_run=True)["summary"]
        self.assertEqual(coupe["clients_plusieurs_sms"], libre["clients_plusieurs_sms"])
        self.assertEqual(coupe["sms_hors_plafond"] + coupe["total_sms_prevus"], libre["envois_candidats"])


class TestTextesSite(_SansTrace):
    def test_chaque_sms_est_mot_pour_mot_un_de_ceux_d_avant(self):
        """Relecture du 25/11/2026 : avec le réglage à 1, un client à UN seul SMS garde EXACTEMENT le texte d'avant
        (Sté LMM le 28/11, adoucisseur + UV : « vos équipements de traitement d'eau ») ; un client regroupé reçoit l'un de
        ses SMS d'avant (celui de la famille prioritaire). Deux jours de la copie : le 26 (A R S D regroupé), le 28."""
        vus_multi = 0
        for jour in (JOUR, "2026-11-28"):
            with mock.patch.object(R, "nowdate", lambda j=jour: j), mock.patch.object(RC, "nowdate", lambda j=jour: j):
                self.reglage(0, plafond=10 ** 6)
                avant = R.envoyer_relances_maintenance(dry_run=True)["preview"]
                self.reglage(1, plafond=10 ** 6)
                apres = R.envoyer_relances_maintenance(dry_run=True)["preview"]
            textes = {}
            for p in avant:
                textes.setdefault(p["customer"], []).append(p["message"])
            self.assertEqual({p["customer"] for p in apres}, set(textes))
            for e in apres:
                self.assertIn(e["message"], textes[e["customer"]], "%s le %s" % (e["customer"], jour))
                if e["nb_sms_avant"] == 1:
                    self.assertEqual([e["message"]], textes[e["customer"]])
                    if len(e["familles"]) > 1:
                        vus_multi += 1
                        self.assertIn(R.APPAREIL_PLUSIEURS_FAMILLES, e["message"])
        if not vus_multi:
            self.skipTest("aucun client à un seul SMS à plusieurs familles ces jours-là (texte vérifié quand même)")


class TestVraiPassageSite(_SansTrace):
    def test_toutes_les_visites_marquees_chacune_son_champ(self):
        self.reglage(1)
        prev = R.envoyer_relances_maintenance(dry_run=True)["preview"]
        p = next((p for p in prev if len(p["visites"]) > 1), None) or (prev[0] if prev else None)
        if not p:
            self.skipTest("aucun SMS dû le %s sur cette copie" % JOUR)
        envoi = self.envoi_de(p)
        visites = envoi["visites"]
        _preview, stats = R.envoyer_et_marquer_sms([envoi], [p["secteur"]], JOUR, dry_run=False)
        self.assertEqual(stats["sms_sent_success"] + stats["sms_invalid_number"] + stats["sms_failed"], 1)   # UN message
        self.assertEqual((stats["visites_marquees"], stats["erreurs"]), (len(visites), 0))
        statut = "Success" if p["phones"] else "Invalid Number"
        for v in visites:
            champ, champ_statut = (("custom_sms_1", "custom_sms1_status") if v["sms_type"] == "SMS1"
                                   else ("custom_sms_2", "custom_sms2_status"))
            row = frappe.db.get_value("Maintenance Schedule Detail", {"parent": v["maintenance"], "item_code": v["item_code"],
                                                                      "scheduled_date": v["scheduled_date"]},
                                      [champ, champ_statut], as_dict=True)
            self.assertEqual((str(row[champ]), row[champ_statut]), (JOUR, statut))
        # Relancé le même jour (deuxième passage, bouton) : plus rien pour ce client
        self.reglage(1)
        encore = R.envoyer_relances_maintenance(dry_run=True)["preview"]
        self.assertNotIn(p["customer"], [x["customer"] for x in encore])

    def test_un_seul_message_part_vraiment_par_client_regroupe(self):
        """Relecture du 25/11/2026 : la VRAIE boucle d'envoi (simulation coupée). La passerelle SMS et l'e-mail sont des
        Mock qui LÈVENT toujours (rien ne part, statut « Failed ») mais comptent leurs appels : un client regroupé reçoit
        un SMS par numéro et au plus un e-mail, pas un par visite ni par échéancier."""
        self.reglage(1)
        prev = R.envoyer_relances_maintenance(dry_run=True)["preview"]
        avec_tel = [p for p in prev if p["phones"]]
        p = next((p for p in avec_tel if p["nb_sms_avant"] > 1), None) or (avec_tel[0] if avec_tel else None)
        if not p:
            self.skipTest("aucun SMS dû à un client avec téléphone le %s sur cette copie" % JOUR)
        envoi = self.envoi_de(p)
        sms = mock.Mock(side_effect=AssertionError("ENVOI RÉEL TENTÉ"))
        mail = mock.Mock(side_effect=AssertionError("E-MAIL RÉEL TENTÉ"))
        with mock.patch.object(R, "simulation_envois", lambda: False), \
                mock.patch.object(R, "envoyer_sms_winsms", sms), mock.patch.object(frappe, "sendmail", mail):
            _preview, stats = R.envoyer_et_marquer_sms([envoi], [p["secteur"]], JOUR, dry_run=False)
        self.assertEqual(sms.call_count, len(p["phones"]))
        self.assertEqual({c.args[1] for c in sms.call_args_list}, {p["message"]})       # le même texte que l'aperçu
        self.assertEqual(mail.call_count, 1 if p["emails"] else 0)
        self.assertEqual((stats["sms_failed"], stats["sms_sent_success"], stats["sms_invalid_number"]), (1, 0, 0))
        self.assertEqual(stats["emails_failed"], 1 if p["emails"] else 0)
        self.assertEqual((stats["visites_marquees"], stats["maintenances_extended"], stats["erreurs"]),
                         (len(p["visites"]), 0, 0))                                     # « Failed » : pas de prolongation
        for v in p["visites"]:
            champ = "custom_sms1_status" if v["sms_type"] == "SMS1" else "custom_sms2_status"
            self.assertEqual(frappe.db.get_value("Maintenance Schedule Detail", {
                "parent": v["maintenance"], "item_code": v["item_code"], "scheduled_date": v["scheduled_date"]}, champ), "Failed")

    def test_sauvegarde_refusee_apres_l_envoi_visite_marquee_quand_meme(self):
        """Relecture du 25/11/2026 : le SMS est parti, puis la fiche refuse save() — la visite est marquée sans
        validation (marquer_en_base), sinon choisir_relances la retrouvait due et le même SMS repartait chaque jour."""
        row = frappe.db.sql("""SELECT d.parent, d.item_code, d.scheduled_date, ms.customer, ms.modified
                               FROM `tabMaintenance Schedule Detail` d JOIN `tabMaintenance Schedule` ms ON ms.name = d.parent
                               WHERE ms.status = 'Submitted' AND d.custom_sms_1 IS NULL AND d.scheduled_date < %s
                                 AND ifnull(d.item_code, '') != ''
                               ORDER BY d.scheduled_date DESC LIMIT 1""", (JOUR,), as_dict=True)
        if not row:
            self.skipTest("aucune visite échue sans SMS sur cette copie")
        row = row[0]
        nb_lignes = frappe.db.count("Maintenance Schedule Detail", {"parent": row.parent})
        attendue = frappe.db.get_value("Maintenance Schedule Detail", {"parent": row.parent, "item_code": row.item_code,
                                                                       "scheduled_date": row.scheduled_date}, "name",
                                       order_by="idx asc")
        envoi = R.regrouper_envois([[{"customer": row.customer, "maintenance": row.parent, "item_code": row.item_code,
                                      "sms_type": "SMS1", "scheduled_date": row.scheduled_date, "secteur": "Secteur 1",
                                      "famille": None}]], [], True)[0]
        fiche = frappe.get_doc("Maintenance Schedule", row.parent)

        def refuse(*a, **k):
            raise frappe.ValidationError("refusé après l'envoi")

        fiche.save = refuse
        cache, stats = {row.parent: fiche}, {"visites_marquees": 0, "erreurs": 0, "maintenances_extended": 0}
        with mock.patch.object(R, "extend_schedule_for_sms", interdit), \
                mock.patch.object(frappe, "log_error") as log_error:
            R.marquer_envoi(envoi, "Success", frappe.utils.getdate(JOUR), cache, set(), stats)
        self.assertEqual((stats["visites_marquees"], stats["erreurs"], stats["marquages_secours"]), (1, 1, 1))
        marque = frappe.db.get_value("Maintenance Schedule Detail", attendue, ["custom_sms_1", "custom_sms1_status"], as_dict=True)
        self.assertEqual((str(marque.custom_sms_1), marque.custom_sms1_status), (JOUR, "Success"))
        self.assertEqual(frappe.db.count("Maintenance Schedule Detail", {"parent": row.parent}), nb_lignes)   # pas prolongé
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", row.parent, "modified"), row.modified)
        self.assertEqual(log_error.call_args.kwargs["title"], "SMS Maintenance — marquage %s" % row.parent)
        self.assertIn("1 visite(s) marquée(s) sans validation", log_error.call_args.kwargs["message"])
        # Le lendemain, cette visite n'est plus due en SMS1
        lignes = frappe.get_all("Maintenance Schedule Detail", filters={"parent": row.parent},
                                fields=["item_code", "scheduled_date", "custom_sms_1", "custom_sms_2", "custom_appelle",
                                        "actual_date", "completion_status"], order_by="idx asc")
        sms1, _sms2 = R.choisir_relances(lignes, "2026-11-27", 7, 0)
        self.assertNotEqual(sms1.get(row.item_code), frappe.utils.getdate(row.scheduled_date))

    def test_un_echeancier_qui_refuse_la_sauvegarde_n_empeche_pas_les_autres(self):
        class Echeancier:
            def __init__(self, nom, casse):
                self.name, self.casse, self.saves = nom, casse, 0
                self.schedules = [frappe._dict(item_code="X", scheduled_date=frappe.utils.getdate("2026-05-20"))]

            def save(self):
                if self.casse:
                    raise frappe.ValidationError("refusé")
                self.saves += 1

        bon, casse = Echeancier("MS-BON", False), Echeancier("MS-CASSE", True)
        envoi = R.regrouper_envois([[{"customer": "C", "maintenance": "MS-CASSE", "item_code": "X", "sms_type": "SMS1",
                                      "scheduled_date": "2026-05-20", "secteur": "Secteur 1", "famille": None}],
                                    [{"customer": "C", "maintenance": "MS-BON", "item_code": "X", "sms_type": "SMS2",
                                      "scheduled_date": "2026-05-20", "secteur": "Secteur 1", "famille": None}]], [], True)[0]
        stats = {"visites_marquees": 0, "erreurs": 0, "maintenances_extended": 0}
        cache = {"MS-BON": bon, "MS-CASSE": casse}
        with mock.patch.object(R, "extend_schedule_for_sms", lambda ms: 0), \
                mock.patch.object(frappe, "log_error") as log_error:
            R.marquer_envoi(envoi, "Success", frappe.utils.getdate(JOUR), cache, set(), stats)
        self.assertEqual((stats["visites_marquees"], stats["erreurs"], bon.saves), (1, 1, 1))
        self.assertEqual(str(bon.schedules[0].custom_sms_2), JOUR)
        self.assertNotIn("MS-CASSE", cache)                                      # rechargé propre au besoin
        self.assertEqual(log_error.call_args.kwargs["title"], "SMS Maintenance — marquage MS-CASSE")


class TestRefusSmsSite(_SansTrace):
    """Relecture finale du 26/11/2026 : un client « Non aux SMS » ne reçoit rien, mais ses visites sont marquées « Refus
    SMS » aux dates d'un SMS — sinon il n'entrerait plus dans aucune liste d'appel (elles partent des visites relancées)."""

    def test_rien_ne_part_les_visites_sont_marquees_et_il_reste_appelable(self):
        self.reglage(1, plafond=100000)
        prev = R.envoyer_relances_maintenance(dry_run=True)["preview"]
        p = next((p for p in prev if p["phones"]), None)
        if not p:
            self.skipTest("aucun SMS dû à un client avec téléphone le %s sur cette copie" % JOUR)
        envoi = self.envoi_de(p)
        sms = mock.Mock(side_effect=AssertionError("ENVOI RÉEL TENTÉ"))
        mail = mock.Mock(side_effect=AssertionError("E-MAIL RÉEL TENTÉ"))
        with mock.patch.object(R, "simulation_envois", lambda: False), \
                mock.patch.object(R, "envoyer_sms_winsms", sms), mock.patch.object(frappe, "sendmail", mail):
            _preview, stats = R.envoyer_et_marquer_sms([envoi], [], JOUR, dry_run=False, refus={p["customer"]})
        self.assertEqual((sms.call_count, mail.call_count), (0, 0))                 # rien n'est parti…
        self.assertEqual((stats["refus_sms"], stats["sms_hors_plafond"], stats["sms_sent_success"], stats["sms_failed"]),
                         (1, 0, 0, 0))                                            # … et hors plafond (aucun secteur autorisé)
        self.assertEqual(stats["visites_marquees"], len(p["visites"]))
        for v in p["visites"]:
            champ, statut = (("custom_sms_1", "custom_sms1_status") if v["sms_type"] == "SMS1" else ("custom_sms_2", "custom_sms2_status"))
            row = frappe.db.get_value("Maintenance Schedule Detail", {"parent": v["maintenance"], "item_code": v["item_code"],
                                                                      "scheduled_date": v["scheduled_date"]}, [champ, statut], as_dict=True)
            self.assertEqual((str(row[champ]), row[statut]), (JOUR, R.STATUT_REFUS_SMS))
        from customization_app import entonnoir_relances as E
        self.assertEqual(E.classe_statut_sms(R.STATUT_REFUS_SMS), "invalide")      # pas compté comme une panne d'envoi

    def test_le_passage_ne_l_exclut_plus_et_ne_compte_pas_dans_le_plafond(self):
        refus = RC.clients_refus_sms()
        self.reglage(1)
        with mock.patch.object(RC, "clients_refus_sms", lambda: set(refus)):
            s = R.envoyer_relances_maintenance(dry_run=True)["summary"]
        self.assertNotIn("refus_sms", s["exclus_par_motif"])
        self.assertGreaterEqual(s.get("refus_sms", 0), 0)


class TestEchecClientSite(_SansTrace):
    def test_une_fiche_client_disparue_n_arrete_pas_les_autres(self):
        """Avant le 25/11/2026, un frappe.get_doc en erreur (client supprimé, article disparu) arrêtait tout le passage."""
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        if not client:
            self.skipTest("aucun client sur cette copie")

        def envoi(c):
            return R.regrouper_envois([[{"customer": c, "maintenance": "MS-%s" % c, "item_code": "X", "sms_type": "SMS1",
                                         "scheduled_date": "2026-05-20", "secteur": "Secteur 1", "famille": None}]], [], True)[0]

        with mock.patch.object(frappe, "log_error") as log_error:
            preview, stats = R.envoyer_et_marquer_sms([envoi("CLIENT-QUI-N-EXISTE-PAS-25112026"), envoi(client)],
                                                      ["Secteur 1"], JOUR, dry_run=True)
        self.assertEqual(stats["erreurs"], 1)
        self.assertEqual([p["customer"] for p in preview], [client])
        self.assertEqual(log_error.call_args.kwargs["title"], "SMS Maintenance — message CLIENT-QUI-N-EXISTE-PAS-25112026")


class TestResumeSite(_SansTrace):
    def _passage(self, dry_run=True, calcul=None):
        with mock.patch.object(R, "calculer_sms_pour_maintenance", calcul or (lambda **kw: ({}, {}))):
            return R.envoyer_relances_maintenance(dry_run=dry_run)

    def test_exclus_partenaire_ne_compte_que_les_clients_du_partenaire(self):
        from customization_app import partenaire_clients as PC

        res = self._passage()
        s = res["summary"]
        cfg = RC.config()
        clients = set(frappe.db.sql_list("""select distinct customer from `tabMaintenance Schedule`
                                            where status = 'Submitted' and ifnull(customer, '') != ''"""))
        self.assertEqual(s["clients_avec_echeancier"], len(clients))
        self.assertEqual(s["exclus_partenaire"], len(clients & set(PC.clients_geres())))
        self.assertEqual(s["exclus_partenaire"], s["exclus_par_motif"]["partenaire"])
        self.assertEqual(s["clients_exclus"], sum(s["exclus_par_motif"].values()))
        self.assertEqual(s["clients_exclus"], len(clients & (RC.clients_exclus_relance(cfg) | RC.clients_avec_rdv(cfg))))
        self.assertEqual(s["exclus_par_motif"]["autre"], 0)       # tout exclu a un motif connu (sinon : motif à ajouter)
        self.assertGreaterEqual(s["echeanciers_exclus"], s["clients_exclus"])
        self.assertIn("exclus_partenaire=%s" % s["exclus_partenaire"], res["log"][0])

    def test_entonnoir_apres_un_vrai_passage_seulement(self):
        module = types.ModuleType("customization_app.entonnoir_relances")
        module.noter = mock.Mock()
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": module}):
            self._passage(dry_run=True)
            module.noter.assert_not_called()
            res = self._passage(dry_run=False)
        module.noter.assert_called_once()
        args, kwargs = module.noter.call_args
        self.assertEqual((args[0], kwargs), ("sms", {"jour": JOUR}))
        self.assertEqual(args[1]["clients_exclus"], res["summary"]["clients_exclus"])
        self.assertEqual(args[1]["exclus_partenaire"], res["summary"]["exclus_partenaire"])
        self.assertTrue(all(isinstance(v, int) for v in args[1].values()))

    def test_entonnoir_reel(self):
        """Avec le VRAI module de l'entonnoir : table absente (avant migration) = rien noté, sans Error Log ni erreur au
        résumé ; table présente = la source « sms » du jour porte les compteurs du passage (annulé en fin de test)."""
        import json

        try:
            from customization_app import entonnoir_relances as ER
        except ImportError:
            self.skipTest("module entonnoir_relances pas encore livré")
        with mock.patch.object(frappe, "log_error") as log_error:
            res = self._passage(dry_run=False)
        self.assertEqual(res["summary"]["erreurs"], 0)
        if not frappe.db.table_exists(ER.DOCTYPE):
            self.assertEqual([c for c in log_error.call_args_list if "entonnoir" in str(c).lower()], [])
            self.assertFalse(R.noter_entonnoir(res["summary"]))
            return
        sources = json.loads(frappe.db.get_value(ER.DOCTYPE, {"jour": JOUR}, "sources") or "{}")
        self.assertIn("sms", sources)
        self.assertIn('"clients_exclus": %s' % res["summary"]["clients_exclus"], json.dumps(sources["sms"]))

    def test_entonnoir_absent_le_passage_continue(self):
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": None}):
            res = self._passage(dry_run=False)
        self.assertEqual(res["summary"]["erreurs"], 0)

    def test_un_echeancier_en_erreur_n_arrete_pas_le_passage(self):
        vus = []

        def calcul(**kw):
            vus.append(kw["maint_name"])
            if len(vus) == 1:
                raise frappe.DoesNotExistError("échéancier disparu")
            return {}, {}

        with mock.patch.object(frappe, "log_error") as log_error:
            s = self._passage(calcul=calcul)["summary"]
        self.assertGreater(len(vus), 1)
        self.assertEqual(s["erreurs"], 1)
        self.assertTrue(log_error.call_args.kwargs["title"].startswith("SMS Maintenance — échéancier "))


if __name__ == "__main__":
    unittest.main()
