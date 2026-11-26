"""Réponses à l'appel (lot 3 + lot 5.1, 25/11/2026) — sur la base (copie de prod), dans un point de sauvegarde annulé :
« Fait lui-même » solde et décale de vrais échéanciers (une seule fois par liste), « magasin » reporte la relance sans
réactiver (pas d'aller-retour au jour du rappel), le compteur de lassitude de vrais clients n'augmente pas à cause des
vieilles visites (elles sont couvertes, pas marquées) et la VRAIE requête des listes ne les reprend plus, une panne du
marquage n'empêche plus « Perdu », et le patch des réglages entiers survit à un enregistrement du formulaire
(relecture du 26/11/2026). Aucun SMS, aucun e-mail, aucune alerte (simulés pour lever), aucun commit."""
from __future__ import annotations

import copy
import json
import unittest
from collections import Counter
from unittest import mock

import frappe
from frappe.utils import add_days, getdate, nowdate

from customization_app import liste_appels as LA, relances_config as RC
from customization_app.Maintenance import creation_liste_appelle as CL, update_schedule as U
from customization_app.patches import config_relances_reglages_entiers as P


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

CHAMPS = ("name", "parent", "idx", "item_code", "scheduled_date", "actual_date", "completion_status", "custom_sms_1", "custom_sms_2",
          "custom_sms1_status", "custom_sms2_status", "custom_appelle", "custom_1er_appel")
COMPARES = ("scheduled_date", "actual_date", "completion_status", "custom_sms_1", "custom_sms_2", "custom_sms1_status", "custom_sms2_status")


def _visites(client, articles=None) -> dict:
    cond, params = "", [client]
    if articles:
        cond, params = " and d.item_code in %s", [client, tuple(articles)]
    rows = frappe.db.sql("select %s from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent "
                         "where ms.customer = %%s and ms.docstatus = 1%s order by d.parent, d.idx"
                         % (", ".join("d." + c for c in CHAMPS), cond), params, as_dict=True)
    return {r.name: r for r in rows}


def _nb_commentaires(doctype, nom, prefixe, liste) -> int:
    return frappe.db.count("Comment", {"reference_doctype": doctype, "reference_name": nom, "comment_type": "Info",
                                       "content": ["like", "%s%%liste %s%%" % (prefixe, liste)]})


def _cycles(client) -> int:
    cfg = RC.config()
    return U.cycles_sans_reponse_par_client(cfg, "").get(client, 0) + U.cycles_sans_reponse_par_client(cfg, "À requalifier").get(client, 0)


def _ligne(client, issue, articles, liste):
    return frappe._dict(client=client, a_été_appelé=1, resume_appel=issue, parent=liste,
                        detail_articles=json.dumps({k: str(v) for k, v in articles.items()}),
                        intéressé_par_le_service_dentretien=None, intéressé_par_le_service_de_relance=None)


# La requête des visites candidates de creation_liste_appelle.echeances_a_appeler (26/11/2026), pour un client.
CANDIDATES = """select d.name, d.parent, d.item_code, d.scheduled_date from `tabMaintenance Schedule Detail` d
                join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1 and ms.status = 'Submitted'
                where ms.customer = %(client)s and d.scheduled_date < %(today)s and d.custom_sms_1 is not null
                  and d.custom_sms_2 is not null and d.custom_sms_2 <= %(sms2_max)s and d.actual_date is null
                  and d.custom_appelle is null"""


def _candidates(client, today, couvert=True) -> dict:
    p = {"client": client, "today": today, "sms2_max": add_days(today, -RC.config()["delai_apres_sms2_jours"])}
    sql = CANDIDATES + (LA.SQL_VISITE_NON_COUVERTE if couvert else "")
    return {r.name: r for r in frappe.db.sql(sql, p, as_dict=True)}


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.today = getdate(nowdate())
        self._patches = [
            mock.patch.object(frappe.db, "commit", side_effect=lambda *a, **k: None),
            mock.patch("frappe.sendmail", side_effect=AssertionError("aucun e-mail en test")),
            mock.patch("customization_app.Maintenance.relance_maintenance_sms.envoyer_sms_winsms",
                       side_effect=AssertionError("aucun SMS en test")),
            mock.patch.object(RC, "alerter", side_effect=AssertionError("aucune alerte en test")),
        ]
        for p in self._patches:
            p.start()
        frappe.local._config_relances = None
        frappe.db.savepoint("reponses_site")

    def tearDown(self):
        frappe.db.rollback(save_point="reponses_site")
        for p in reversed(self._patches):
            p.stop()
        frappe.local._config_relances = None

    def _statut(self, client, statut):
        if not frappe.db.has_column("Customer", "custom_statut_relance"):
            self.skipTest("champ custom_statut_relance absent")
        frappe.db.set_value("Customer", client, "custom_statut_relance", statut)


class TestFaitLuiMeme(_Base):
    def _cas(self, plusieurs=False):
        """Un client réel : un article avec une visite échue non faite et au moins une visite suivante, sur des
        échéanciers dont les visites faites ont date prévue = date réelle (le Server Script « Update Schedule based on
        Real Date » ne bouge alors rien d'autre à l'enregistrement)."""
        rows = frappe.db.sql("""
            select ms.customer, d.item_code, min(d.scheduled_date) premiere, count(distinct d.parent) nb
            from `tabMaintenance Schedule Detail` d
            join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
            join tabCustomer c on c.name = ms.customer
            where d.actual_date is null and ifnull(d.completion_status, '') != 'Fully Completed' and d.scheduled_date <= %(today)s
              and ifnull(c.custom_statut_relance, '') <> 'Perdu'
              and exists (select 1 from `tabMaintenance Schedule Detail` y where y.parent = d.parent and y.item_code = d.item_code
                          and y.actual_date is null and y.scheduled_date > d.scheduled_date)
            group by ms.customer, d.item_code
            having nb {op} 1
            order by ms.customer limit 40""".replace("{op}", ">" if plusieurs else "="), {"today": self.today}, as_dict=True)
        for r in rows:
            mal = frappe.db.sql("""select count(*) from `tabMaintenance Schedule Detail` x join `tabMaintenance Schedule` ms on ms.name = x.parent
                                   where ms.customer = %s and ms.docstatus = 1 and x.actual_date is not null and x.actual_date <> x.scheduled_date""",
                                (r.customer,))[0][0]
            if not mal:
                return r
        self.skipTest("aucun client réel dans ce cas")

    def _attendu(self, avant, item):
        """L'état attendu des visites, calculé par la règle pure sur une copie."""
        attendu = copy.deepcopy(avant)
        par_ech = {}
        for r in attendu.values():
            if r.item_code == item:
                par_ech.setdefault(r.parent, []).append(r)
        for rows in par_ech.values():
            LA.solder_fait_lui_meme(sorted(rows, key=lambda r: r.idx), self.today)
        return attendu

    def _verifier(self, cas, liste):
        avant = _visites(cas.customer)
        attendu = self._attendu(avant, cas.item_code)
        LA._synchroniser_ligne(_ligne(cas.customer, LA.FAIT_LUI_MEME, {cas.item_code: cas.premiere}, liste), liste)
        apres = _visites(cas.customer)
        for nom, r in attendu.items():
            self.assertEqual([apres[nom][c] for c in COMPARES], [r[c] for c in COMPARES], nom)
        for nom in set(apres) - set(avant):                                     # prolongation éventuelle
            self.assertGreater(getdate(apres[nom].scheduled_date), self.today)
        soldees = [n for n in avant if not avant[n].actual_date and apres[n].actual_date]
        self.assertTrue(soldees)
        self.assertTrue(all(str(apres[n].actual_date) == str(self.today) and apres[n].completion_status == "Fully Completed" for n in soldees))
        self.assertEqual(len({apres[n].parent for n in soldees}), len(soldees))  # une visite par échéancier et par article
        return avant, apres, soldees

    def test_solde_decale_trace_et_reactive(self):
        cas = self._cas()
        liste = "LIST-TEST-REPONSES-FLM1"
        self._statut(cas.customer, "À requalifier")
        _avant, apres, soldees = self._verifier(cas, liste)
        ech = apres[soldees[0]].parent
        self.assertEqual(_nb_commentaires("Maintenance Schedule", ech, LA.TRACE_FAIT_LUI_MEME, liste), 1)
        self.assertEqual(_nb_commentaires("Customer", cas.customer, LA.TRACE_FAIT_LUI_MEME, liste), 1)       # témoin
        self.assertEqual(frappe.db.get_value("Customer", cas.customer, "custom_statut_relance") or "", "")    # réactivé
        self.assertEqual(_nb_commentaires("Customer", cas.customer, LA.TRACE_REACTIVATION, liste), 1)
        # Le compteur de lassitude repart de zéro : plus rien d'échu après la visite faite du jour.
        self.assertEqual(_cycles(cas.customer), 0)

        # IDEMPOTENT : réenregistrer la liste, le jour même puis trois jours plus tard, ne solde ni ne décale rien.
        for jour in (self.today, add_days(self.today, 3)):
            with mock.patch("customization_app.liste_appels.nowdate", return_value=str(jour)):
                LA._synchroniser_ligne(_ligne(cas.customer, LA.FAIT_LUI_MEME, {cas.item_code: cas.premiere}, liste), liste)
            encore = _visites(cas.customer)
            self.assertEqual({n: [r[c] for c in ("scheduled_date", "actual_date", "completion_status")] for n, r in encore.items()},
                             {n: [r[c] for c in ("scheduled_date", "actual_date", "completion_status")] for n, r in apres.items()}, jour)
            self.assertEqual(_nb_commentaires("Maintenance Schedule", ech, LA.TRACE_FAIT_LUI_MEME, liste), 1)
            self.assertEqual(_nb_commentaires("Customer", cas.customer, LA.TRACE_FAIT_LUI_MEME, liste), 1)

    def test_tous_les_echeanciers_du_client_qui_portent_l_article(self):
        cas = self._cas(plusieurs=True)
        _avant, _apres, soldees = self._verifier(cas, "LIST-TEST-REPONSES-FLM2")
        self.assertGreaterEqual(len(soldees), 2)

    def test_rien_d_echu_ni_solde_ni_reactivation(self):
        """Un article sans visite échue : rien n'est soldé, le témoin le dit, et le client « À requalifier » le reste (le
        réactiver sans rien solder, c'est le requalifier la nuit même)."""
        r = frappe.db.sql("""
            select ms.customer, d.item_code from `tabMaintenance Schedule Detail` d
            join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
            join tabCustomer c on c.name = ms.customer
            where ifnull(c.custom_statut_relance, '') <> 'Perdu'
            group by ms.customer, d.item_code
            having sum(d.actual_date is null and ifnull(d.completion_status, '') != 'Fully Completed' and d.scheduled_date <= %s) = 0
               and sum(d.actual_date is null and d.scheduled_date > %s) > 0
            limit 1""", (self.today, self.today), as_dict=True)
        if not r:
            self.skipTest("aucun article sans visite échue")
        client, item, liste = r[0].customer, r[0].item_code, "LIST-TEST-REPONSES-FLM4"
        self._statut(client, "À requalifier")
        avant = _visites(client)
        LA._synchroniser_ligne(_ligne(client, LA.FAIT_LUI_MEME, {item: self.today}, liste), liste)
        apres = _visites(client)
        self.assertEqual({n: [x[c] for c in COMPARES] for n, x in apres.items()}, {n: [x[c] for c in COMPARES] for n, x in avant.items()})
        self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Customer", "reference_name": client, "comment_type": "Info",
                                                     "content": ["like", "%%liste %s : aucune visite échue à solder." % liste]}))
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_statut_relance"), "À requalifier")
        self.assertEqual(_nb_commentaires("Customer", client, LA.TRACE_REACTIVATION, liste), 0)

    def test_reglage_coupe(self):
        cas = self._cas()
        liste = "LIST-TEST-REPONSES-FLM3"
        self._statut(cas.customer, "À requalifier")
        cfg = dict(RC.config(), fait_lui_meme_solde=0)
        avant = _visites(cas.customer)
        with mock.patch.object(RC, "config", return_value=cfg):
            LA._synchroniser_ligne(_ligne(cas.customer, LA.FAIT_LUI_MEME, {cas.item_code: cas.premiere}, liste), liste)
        apres = _visites(cas.customer)
        self.assertEqual({n: [r[c] for c in COMPARES] for n, r in apres.items()}, {n: [r[c] for c in COMPARES] for n, r in avant.items()})
        self.assertEqual(_nb_commentaires("Customer", cas.customer, LA.TRACE_FAIT_LUI_MEME, liste), 0)
        self.assertEqual(frappe.db.get_value("Customer", cas.customer, "custom_statut_relance"), "À requalifier")   # pas de va-et-vient


class TestMagasin(_Base):
    def _client(self):
        if not frappe.db.has_column("Customer", "custom_relance_reportee_au"):
            self.skipTest("champ custom_relance_reportee_au absent")
        c = frappe.db.sql("""select ms.customer from `tabMaintenance Schedule` ms join tabCustomer c on c.name = ms.customer
                             where ms.docstatus = 1 and ifnull(c.custom_statut_relance, '') <> 'Perdu' order by ms.modified desc limit 1""")
        if not c:
            self.skipTest("aucun échéancier soumis")
        return c[0][0]

    def test_report_de_quinze_jours_une_seule_fois(self):
        client, liste = self._client(), "LIST-TEST-REPONSES-MAG1"
        frappe.db.set_value("Customer", client, "custom_relance_reportee_au", None)
        self._statut(client, "À requalifier")
        LA._synchroniser_ligne(_ligne(client, LA.MAGASIN, {}, liste), liste)
        attendu = add_days(self.today, 15)
        self.assertEqual(getdate(frappe.db.get_value("Customer", client, "custom_relance_reportee_au")), getdate(attendu))
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_statut_relance"), "À requalifier")   # pas réactivé
        self.assertEqual(_nb_commentaires("Customer", client, LA.TRACE_REACTIVATION, liste), 0)
        self.assertIn(client, RC.clients_reportes(self.today))                  # ni SMS, ni appel, ni requalification d'ici là
        # Réenregistrée trois jours plus tard : la date ne glisse pas.
        with mock.patch("customization_app.liste_appels.nowdate", return_value=str(add_days(self.today, 3))):
            LA._synchroniser_ligne(_ligne(client, LA.MAGASIN, {}, liste), liste)
        self.assertEqual(getdate(frappe.db.get_value("Customer", client, "custom_relance_reportee_au")), getdate(attendu))
        self.assertEqual(_nb_commentaires("Customer", client, LA.TRACE_MAGASIN, liste), 1)

    def test_report_plus_lointain_garde_et_reglage_a_zero(self):
        client = self._client()
        loin = add_days(self.today, 40)
        frappe.db.set_value("Customer", client, "custom_relance_reportee_au", loin)
        self._statut(client, "À requalifier")
        LA._synchroniser_ligne(_ligne(client, LA.MAGASIN, {}, "LIST-TEST-REPONSES-MAG2"), "LIST-TEST-REPONSES-MAG2")
        self.assertEqual(getdate(frappe.db.get_value("Customer", client, "custom_relance_reportee_au")), getdate(loin))
        self.assertEqual(_nb_commentaires("Customer", client, LA.TRACE_MAGASIN, "LIST-TEST-REPONSES-MAG2"), 1)   # témoin « inchangée »
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_statut_relance"), "À requalifier")   # jamais réactivé
        # Réglage à 0 : aucun report, aucune réactivation.
        frappe.db.set_value("Customer", client, "custom_relance_reportee_au", None)
        self._statut(client, "À requalifier")
        with mock.patch.object(RC, "config", return_value=dict(RC.config(), jours_rappel_magasin=0)):
            LA._synchroniser_ligne(_ligne(client, LA.MAGASIN, {}, "LIST-TEST-REPONSES-MAG3"), "LIST-TEST-REPONSES-MAG3")
        self.assertIsNone(frappe.db.get_value("Customer", client, "custom_relance_reportee_au"))
        self.assertEqual(_nb_commentaires("Customer", client, LA.TRACE_MAGASIN, "LIST-TEST-REPONSES-MAG3"), 0)
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_statut_relance"), "À requalifier")
        self.assertEqual(_nb_commentaires("Customer", client, LA.TRACE_REACTIVATION, "LIST-TEST-REPONSES-MAG3"), 0)


    def _a_requalifier_au_seuil(self):
        """Un vrai « À requalifier » qui a atteint le seuil, sans rendez-vous, sans report, pas « Non » : celui que la nuit
        requalifierait s'il était réactivé (274 sur la copie du 25/11/2026)."""
        self._client()
        cfg = RC.config()
        n = cfg["cycles_sans_reponse"]
        hors = RC.clients_avec_rdv(cfg) | RC.clients_reportes(self.today) | U.clients_non_interesses(cfg)
        for c, cycles in sorted(U.cycles_sans_reponse_par_client(cfg, "À requalifier").items()):
            if cycles >= n > 0 and c not in hors:
                return c
        self.skipTest("aucun « À requalifier » au seuil")

    def test_pas_d_aller_retour_au_jour_du_rappel(self):
        """Relecture du 26/11/2026 : réactivé par « magasin », le client était requalifié la nuit du jour du rappel (le
        report ne le protège plus, ses cycles n'ont pas bougé) — 15 sur 15 sur la copie. Il reste « À requalifier », et
        ce jour-là la liste Requalification le reprend : c'est le rappel voulu."""
        client, liste = self._a_requalifier_au_seuil(), "LIST-TEST-REPONSES-MAG4"
        LA._synchroniser_ligne(_ligne(client, LA.MAGASIN, {}, liste), liste)
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_statut_relance"), "À requalifier")
        self.assertEqual(_nb_commentaires("Customer", client, LA.TRACE_REACTIVATION, liste), 0)
        rappel = add_days(self.today, 15)
        self.assertEqual(getdate(frappe.db.get_value("Customer", client, "custom_relance_reportee_au")), getdate(rappel))
        with mock.patch.object(U, "nowdate", return_value=str(rappel)), mock.patch.object(RC, "nowdate", return_value=str(rappel)):
            cfg = RC.config()
            self.assertNotIn(client, U.clients_a_requalifier(cfg))             # aucun « ⏸️ Relance suspendue » la nuit venue
            self.assertIn(client, CL.clients_requalification_a_lister(rappel))  # rappelé à la date dite
            # Ce que faisait la réactivation : relancé, il repassait « À requalifier » cette nuit-là.
            frappe.db.set_value("Customer", client, "custom_statut_relance", "")
            self.assertIn(client, U.clients_a_requalifier(cfg))


class TestEffetsIsolesSite(_Base):
    """Relecture du 26/11/2026 : une panne du marquage des visites n'empêche plus le statut de l'appel."""

    def _visite(self):
        r = frappe.db.sql("""select ms.customer, d.item_code, d.scheduled_date from `tabMaintenance Schedule Detail` d
                             join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
                             join tabCustomer c on c.name = ms.customer
                             where d.actual_date is null and d.scheduled_date <= %s and ifnull(c.custom_statut_relance, '') = ''
                             order by d.scheduled_date desc limit 1""", (self.today,), as_dict=True)
        if not r:
            self.skipTest("aucune visite échue")
        return r[0]

    def test_pas_de_systeme_passe_perdu_meme_si_le_marquage_leve(self):
        v, liste = self._visite(), "LIST-TEST-REPONSES-ISO1"
        self._statut(v.customer, "")
        with mock.patch.object(LA, "_marquer_visites", side_effect=RuntimeError("verrou simulé")), \
                mock.patch.object(frappe, "log_error") as journal:
            LA._synchroniser_ligne(_ligne(v.customer, LA.PAS_DE_SYSTEME, {v.item_code: v.scheduled_date}, liste), liste)
        self.assertEqual(frappe.db.get_value("Customer", v.customer, "custom_statut_relance"), "Perdu")
        self.assertEqual(journal.call_count, 1)
        self.assertIn("marquage des visites", journal.call_args.args[1])

    def test_rendez_vous_reactive_meme_si_le_marquage_leve(self):
        v, liste = self._visite(), "LIST-TEST-REPONSES-ISO2"
        self._statut(v.customer, "À requalifier")
        with mock.patch.object(LA, "_marquer_visites", side_effect=RuntimeError("verrou simulé")), \
                mock.patch.object(frappe, "log_error"):
            LA._synchroniser_ligne(_ligne(v.customer, LA.RENDEZ_VOUS, {v.item_code: v.scheduled_date}, liste), liste)
        self.assertEqual(frappe.db.get_value("Customer", v.customer, "custom_statut_relance") or "", "")
        self.assertEqual(_nb_commentaires("Customer", v.customer, LA.TRACE_REACTIVATION, liste), 1)


class _VieuxCycles(_Base):
    """Population : de vrais clients relancés qui ont, sur un même (échéancier, article), au moins deux visites échues
    relancées (SMS 1 + SMS 2) jamais appelées — exactement ceux que le marquage de toutes les visites gonflait, et ceux
    que leurs vieilles visites ramenaient dans une liste au bout de 60 j."""

    def _clients(self, limite=12):
        rows = frappe.db.sql("""
            select ms.customer, d.item_code, min(d.scheduled_date) premiere
            from `tabMaintenance Schedule Detail` d
            join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
            join tabCustomer c on c.name = ms.customer
            where d.actual_date is null and ifnull(d.completion_status, '') != 'Fully Completed' and d.scheduled_date < %(today)s
              and d.custom_sms_1 is not null and d.custom_sms_2 is not null and d.custom_appelle is null
              and ifnull(c.custom_statut_relance, '') = '' and ifnull(c.customer_group, '') not in %(b2b)s
              and d.scheduled_date > ifnull((select max(d2.actual_date) from `tabMaintenance Schedule Detail` d2
                                              join `tabMaintenance Schedule` m2 on m2.name = d2.parent where m2.customer = ms.customer), '1900-01-01')
            group by ms.customer, d.parent, d.item_code
            having count(*) >= 2
            order by ms.customer""", {"today": self.today, "b2b": tuple(RC.config()["groupes_b2b_liste"]) or ("",)}, as_dict=True)
        out = {}
        for r in rows:
            if r.customer not in out and len(out) >= limite:
                break
            arts = out.setdefault(r.customer, {})
            if r.item_code not in arts or getdate(r.premiere) < getdate(arts[r.item_code]):
                arts[r.item_code] = r.premiere                                   # la date que porterait la liste (la plus ancienne)
        if not out:
            self.skipTest("aucun client réel avec de vieux cycles non appelés")
        return out


class TestLassitudeVieuxCycles(_VieuxCycles):
    """La CONTRAINTE du lot 5.1 : l'appel ne doit pas faire monter le compteur de lassitude à cause des vieilles visites."""

    def _marquage_naif(self, client, articles) -> int:
        """Ce que donnerait « marquer toutes les visites échues » : le compteur, mesuré puis annulé."""
        frappe.db.savepoint("naif")
        try:
            frappe.db.sql("""update `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                             set d.custom_appelle = %s
                             where ms.customer = %s and ms.docstatus = 1 and d.item_code in %s and d.scheduled_date <= %s
                               and d.actual_date is null and ifnull(d.completion_status, '') != 'Fully Completed' and d.custom_appelle is null""",
                          (self.today, client, tuple(articles), self.today))
            return _cycles(client)
        finally:
            frappe.db.rollback(save_point="naif")

    def test_compteur_avant_apres_sur_clients_reels(self):
        clients = self._clients()
        bilan = []
        for client, articles in clients.items():
            avant = _cycles(client)
            naif = self._marquage_naif(client, articles)
            v_avant = _visites(client, articles)
            LA._synchroniser_ligne(_ligne(client, LA.NE_REPOND_PAS, articles, "LIST-TEST-REPONSES-LASS"), "LIST-TEST-REPONSES-LASS")
            v_apres = _visites(client, articles)
            nouvelles = [n for n in v_apres if not v_avant[n].custom_appelle and v_apres[n].custom_appelle]
            # Une seule visite marquée par (échéancier, article) : celle de la liste, comme avant le 25/11/2026.
            self.assertTrue(all(k == 1 for k in Counter((v_apres[n].parent, v_apres[n].item_code) for n in nouvelles).values()), client)
            apres = _cycles(client)
            mois_marques = {str(v_apres[n].scheduled_date)[:7] for n in nouvelles}
            self.assertLessEqual(apres - avant, len(mois_marques), client)        # au plus le cycle de l'appel lui-même
            self.assertLessEqual(apres, naif, client)
            # Les autres visites échues de l'article restent sans marque, mais sont COUVERTES par l'appel.
            par_paire = {}
            for r in v_apres.values():
                par_paire.setdefault((r.parent, r.item_code), []).append(r)
            for (parent, item), rows in par_paire.items():
                if not any(r.name in nouvelles for r in rows):
                    continue
                for r in rows:
                    if (r.custom_sms_1 and r.custom_sms_2 and not r.custom_appelle and not LA.visite_faite(r)
                            and getdate(r.scheduled_date) < self.today):
                        self.assertTrue(LA.visite_couverte(r, rows), r.name)
            # Et la liste d'appels, avec la clause de couverture, ne reprend plus aucune visite de ces articles : le
            # client ne revient pas au bout de 60 j sur une vieille échéance. Sans la clause, il revenait.
            appelees = {(v_apres[n].parent, v_apres[n].item_code) for n in nouvelles}
            sans = [r for r in _candidates(client, self.today, couvert=False).values() if (r.parent, r.item_code) in appelees]
            avec = [r for r in _candidates(client, self.today).values() if (r.parent, r.item_code) in appelees]
            self.assertEqual(avec, [], client)
            bilan.append((client, avant, apres, naif, len(sans)))
        # Depuis la relecture finale du 26/11/2026, un cycle ne compte jamais plus de dates d'appel distinctes : même le
        # marquage « naïf » de toutes les vieilles visites n'ajoute plus qu'UN cycle par appel (il en ajoutait 2 ou 3).
        self.assertTrue(all(naif - avant <= 1 for _c, avant, _a, naif, _s in bilan), bilan)
        # … et sur des clients que leurs vieilles visites auraient ramenés dans une liste.
        self.assertTrue(any(s for *_x, s in bilan), bilan)


class TestCouvertureSQL(_Base):
    def test_meme_regle_que_la_fonction_pure(self):
        cfg = RC.config()
        base = CANDIDATES.replace("ms.customer = %(client)s and ", "")
        p = {"today": self.today, "sms2_max": add_days(self.today, -cfg["delai_apres_sms2_jours"])}
        sans = {r.name: r for r in frappe.db.sql(base, p, as_dict=True)}
        avec = {r.name for r in frappe.db.sql(base + LA.SQL_VISITE_NON_COUVERTE, p, as_dict=True)}
        if not sans:
            self.skipTest("aucune visite candidate")
        self.assertTrue(avec <= set(sans))
        parents = tuple({r.parent for r in sans.values()})
        toutes = {}
        for r in frappe.db.sql("select %s from `tabMaintenance Schedule Detail` d where d.parent in %%s" % ", ".join("d." + c for c in CHAMPS),
                               (parents,), as_dict=True):
            toutes.setdefault((r.parent, r.item_code), []).append(r)
        for nom, r in sans.items():
            rows = toutes[(r.parent, r.item_code)]
            visite = next(x for x in rows if x.name == nom)
            self.assertEqual(LA.visite_couverte(visite, rows), nom not in avec, nom)


class TestCouvertureDansLesListes(_VieuxCycles):
    """Relecture du 26/11/2026 : le lot 5.1 prouvé sur le VRAI chemin des listes (creation_liste_appelle.echeances_a_appeler),
    pas sur une copie de sa requête. Des clients réels avec de vieilles visites relancées jamais appelées sont appelés ;
    la requête du matin ne doit plus reprendre aucun (échéancier, article) appelé. ÉCHOUE tant que la clause
    SQL_VISITE_NON_COUVERTE n'est pas collée dans echeances_a_appeler (ajouts_ailleurs)."""

    @staticmethod
    def _paires(echeances, clients) -> set:
        return {(ms, item) for c in clients for ms, arts in (echeances.get(c) or {}).get("echeanciers", {}).items() for item in arts}

    def test_la_requete_des_listes_ne_reprend_pas_les_vieilles_visites(self):
        clients = self._clients(limite=8)
        cfg = RC.config()
        avant = self._paires(CL.echeances_a_appeler(self.today, cfg), clients)
        appelees = set()
        for client, articles in clients.items():
            v_avant = _visites(client, articles)
            LA._synchroniser_ligne(_ligne(client, LA.NE_REPOND_PAS, articles, "LIST-TEST-REPONSES-REQ"), "LIST-TEST-REPONSES-REQ")
            v_apres = _visites(client, articles)
            appelees |= {(v_apres[n].parent, v_apres[n].item_code) for n in v_apres if not v_avant[n].custom_appelle and v_apres[n].custom_appelle}
        if not avant & appelees:
            self.skipTest("aucune paire appelée n'était dans la requête des listes avant l'appel")
        apres = self._paires(CL.echeances_a_appeler(self.today, cfg), clients)
        self.assertEqual(sorted(apres & appelees), [],
                         "la requête des listes reprend encore de vieilles visites d'articles appelés aujourd'hui : "
                         "coller liste_appels.SQL_VISITE_NON_COUVERTE dans creation_liste_appelle.echeances_a_appeler")


class TestPatchReglagesEntiers(_Base):
    """Relecture du 26/11/2026 : sur un Single déjà enregistré, un réglage entier ajouté sans ligne passait à 0 (= fonction
    coupée) au premier enregistrement du formulaire. Le patch écrit le défaut, ne touche à rien d'autre."""
    CLES = tuple(P.DEFAUTS_REPONSES)

    def _lignes(self) -> dict:
        return dict(frappe.db.sql("select field, value from `tabSingles` where doctype = %s", (P.DOCTYPE,)))

    def _effacer(self, cles):
        frappe.db.sql("delete from `tabSingles` where doctype = %s and field in %s", (P.DOCTYPE, tuple(cles)))

    def test_ecrit_les_defauts_manquants_seulement(self):
        if not self._lignes():
            self.skipTest("Config Relances jamais enregistré")
        self._effacer(self.CLES)
        frappe.db.set_single_value(P.DOCTYPE, "jours_reveil_requalifies", 0, update_modified=False)   # un 0 choisi à la main
        avant = self._lignes()
        with mock.patch("builtins.print"):
            poses = P.execute()
        apres = self._lignes()
        self.assertEqual({k: apres[k] for k in self.CLES}, {"fait_lui_meme_solde": "1", "jours_rappel_magasin": "15", "jours_reveil_requalifies": "0"})
        # Rien d'existant ne bouge, sauf `modified` qui avance exprès (relecture finale : un formulaire ouvert avant le
        # déploiement est ainsi refusé au lieu d'écrire 0 dans les réglages neufs).
        self.assertEqual({k: v for k, v in apres.items() if k in avant and k != "modified"},
                         {k: v for k, v in avant.items() if k != "modified"})
        self.assertGreater(apres["modified"], avant["modified"])
        self.assertEqual(set(apres) - set(avant), set(poses))
        self.assertTrue(all(cle in apres for cle, v in RC.DEFAUTS.items() if isinstance(v, int)))
        with mock.patch("builtins.print"):
            self.assertEqual(P.execute(), {})                                   # rejouable sans effet

    def test_single_jamais_enregistre_laisse_tel_quel(self):
        frappe.db.sql("delete from `tabSingles` where doctype = %s", (P.DOCTYPE,))
        with mock.patch("builtins.print"):
            self.assertEqual(P.execute(), {})
        self.assertEqual(self._lignes(), {})

    def test_le_formulaire_enregistre_garde_les_defauts(self):
        """Le scénario du relecteur, une fois les champs ajoutés au DocType (sauté avant la migration)."""
        meta = frappe.get_meta(P.DOCTYPE)
        if not all(meta.has_field(k) for k in self.CLES):
            self.skipTest("champs pas encore ajoutés à Config Relances (migration à venir)")
        self._effacer(self.CLES)
        frappe.get_doc(P.DOCTYPE).save(ignore_permissions=True)
        # Sans le patch : la case reste à 0 (l'écran, lui, l'affiche cochée grâce à onload), les nombres gardent leur défaut
        # grâce à ConfigRelances.validate (relecture finale du 26/11/2026 ; avant : 0 partout = fonctions coupées).
        self.assertEqual({k: self._lignes().get(k) for k in self.CLES},
                         {"fait_lui_meme_solde": "0", "jours_rappel_magasin": "15", "jours_reveil_requalifies": "180"})
        self._effacer(self.CLES)
        with mock.patch("builtins.print"):
            P.execute()
        frappe.get_doc(P.DOCTYPE).save(ignore_permissions=True)
        self.assertEqual({k: self._lignes().get(k) for k in self.CLES},
                         {"fait_lui_meme_solde": "1", "jours_rappel_magasin": "15", "jours_reveil_requalifies": "180"})


class TestListeReelle(_Base):
    def test_hook_idempotent_sur_une_liste_validee(self):
        nom = frappe.db.sql("""select l.name from `tabListe Appelle Entretien` l where l.docstatus = 1
                               and exists (select 1 from `tabAppelle Client` a where a.parent = l.name and a.a_été_appelé = 1)
                               order by l.date desc, l.creation desc limit 1""")
        if not nom:
            self.skipTest("aucune liste validée")
        doc = frappe.get_doc("Liste Appelle Entretien", nom[0][0])
        clients = sorted({l.client for l in doc.clients if l.a_été_appelé and l.client})
        erreurs = frappe.db.count("Error Log")

        def photo():
            v = {}
            for c in clients:
                v.update({n: tuple(r[k] for k in CHAMPS) for n, r in _visites(c).items()})
            fiches = frappe.get_all("Customer", filters={"name": ["in", clients]},
                                    fields=["name", "custom_statut_relance", "custom_relance_reportee_au"], order_by="name")
            notes = frappe.db.count("Comment", {"reference_doctype": "Customer", "reference_name": ["in", clients], "comment_type": "Info"})
            return v, fiches, notes

        LA.synchroniser_appels(doc)
        une = photo()
        LA.synchroniser_appels(doc)
        self.assertEqual(photo(), une)
        self.assertEqual(frappe.db.count("Error Log"), erreurs)                 # aucune ligne en erreur


if __name__ == "__main__":
    unittest.main()
