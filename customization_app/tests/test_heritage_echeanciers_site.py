"""Données héritées des échéanciers (26/11/2026) — essai SUR LE SITE (copie de prod), sans trace : commit neutralisé,
Journal des erreurs capturé (tabError Log est en MyISAM : le rollback ne l'annulerait pas), SMS / e-mails interdits,
alertes coupées, tout annulé à la fin de chaque test. Sauté quand la copie ne contient plus le cas.

- (a) les visites faites restées « Pending » passent « Fully Completed », sans rien changer aux relances ;
- (b) les échéanciers « sel / porte-membrane » : annulés ou convertis en devinette, puis un achat de sel solde la
  devinette au lieu d'en deviner une autre ;
- (c) le recalage sur la date réelle : la reprise et le Server Script disent la même chose sur toute la base, un
  passage de nuit n'a rien à recaler, une saisie à la main enregistrée est recalée et tracée ; le patch désactive le
  script seulement si le hook est branché.
Relecture du 26/11/2026 : une liste d'appels en brouillon qui cite un échéancier hérité reste enregistrable après le
patch (b) (CancelledLinkError avant), ses articles suivent la conversion, une ligne relue par le 2e appel aussi (pas
l'historique), la trace 🧹 se lit dans « Historique client », les convertis qui ont une visite réalisée sont « à
surveiller »."""
import copy
import json
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, getdate

from customization_app import historique_client as HC
from customization_app import relances_config as RC
from customization_app.Maintenance import echeancier_hooks as H
from customization_app.Maintenance import relance_maintenance_sms as S
from customization_app.Maintenance import update_schedule as U
from customization_app.Maintenance.creation_liste_appelle import echeances_a_appeler
from customization_app.patches import echeanciers_sel_porte_membrane as SP
from customization_app.patches import script_date_reelle_dans_l_app as SS
from customization_app.patches import visites_faites_restees_en_attente as VF


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

JOUR = getdate("2026-11-26")


def _interdit(*a, **k):
    raise RuntimeError("envoi interdit pendant les tests")


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("heritage")
        self.journal = []
        self.mocks = [mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
                      mock.patch.object(frappe, "log_error", lambda *a, **k: self.journal.append(k.get("title") or (a[1] if len(a) > 1 else ""))),
                      mock.patch.object(frappe, "sendmail", _interdit),
                      mock.patch.object(S, "envoyer_sms_winsms", _interdit),
                      mock.patch.object(RC, "alerter", lambda *a, **k: None),
                      mock.patch.object(U, "log", lambda *a, **k: None)]       # ni le journal de nuit (fichier)
        for m in self.mocks:
            m.start()

    def tearDown(self):
        frappe.db.rollback(save_point="heritage")
        for m in self.mocks:
            m.stop()


def _relances(jour):
    """Ce que les relances voient : échéanciers épuisés, cycles de lassitude, visites à appeler."""
    cfg = RC.config()
    return (sorted(U.echeanciers_epuises(jour)), U.cycles_sans_reponse_par_client(cfg, ""),
            U.cycles_sans_reponse_par_client(cfg, "À requalifier"), sorted(echeances_a_appeler(jour, cfg)))


def _en_attente():
    return frappe.db.sql_list("""select d.name from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                                 where ms.docstatus = 1 and d.actual_date is not null and d.actual_date <= %s
                                   and ifnull(d.completion_status, '') != 'Fully Completed'""", (JOUR,))


class TestVisitesFaitesSite(_SansTrace):
    def test_statut_corrige_sans_rien_changer_aux_relances(self):
        visees = _en_attente()
        if not visees:
            self.skipTest("plus aucune visite faite restée « Pending » sur cette copie")
        dates = frappe.db.sql("select name, scheduled_date, actual_date, custom_sms_1, custom_appelle, modified from `tabMaintenance Schedule Detail` where name in %s",
                              (tuple(visees),), as_dict=True)
        avant = _relances(JOUR)
        r = VF.execute(aujourd_hui=JOUR)
        self.assertEqual(r["corrigees"], len(visees))
        self.assertEqual(_en_attente(), [])
        apres = frappe.db.sql("select name, scheduled_date, actual_date, custom_sms_1, custom_appelle, modified from `tabMaintenance Schedule Detail` where name in %s",
                              (tuple(visees),), as_dict=True)
        self.assertEqual(sorted(map(tuple, (d.values() for d in dates))), sorted(map(tuple, (d.values() for d in apres))))  # rien d'autre
        self.assertEqual(_relances(JOUR), avant)          # les relances lisaient déjà la date réelle
        self.assertIn("Visites faites restées « Pending » : statut corrigé", self.journal)
        self.assertEqual(VF.execute(aujourd_hui=JOUR)["corrigees"], 0)     # rejouable


class TestSelPorteMembraneSite(_SansTrace):
    def _heritages(self):
        return {n: e for n, e in SP._charger(RC.familles(), {}).items() if not e["familles"]}

    def test_annules_convertis_puis_un_achat_de_sel_solde_la_devinette(self):
        avant = self._heritages()
        if not any(SP.famille_visee(e["items"]) for e in avant.values()):
            self.skipTest("plus d'échéancier « sel / porte-membrane » sur cette copie")
        bilan = SP.executer(JOUR)
        self.assertEqual(bilan["erreurs"], [])
        annules = [l.split(" ")[0] for l in bilan["annules"]]
        convertis = [l.split(" ")[0] for l in bilan["remplaces"]]
        self.assertTrue(annules or convertis)
        for nom in annules:
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", nom, "docstatus"), 2, nom)       # annulé, pas supprimé
            self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Maintenance Schedule", "reference_name": nom,
                                                         "content": ["like", "🧹 Échéancier hérité%"]}), nom)
        # Après : il ne reste « sans famille » que les doutes, chaque client ne suit plus qu'une fois la famille visée.
        restes = self._heritages()
        self.assertEqual(sorted(restes), sorted(l.split(" ")[0] for l in bilan["laisses"]))
        for nom in convertis:
            ms = frappe.get_doc("Maintenance Schedule", nom)
            self.assertEqual(len({i.item_code for i in ms.items}), 1)
            self.assertEqual({r.item_code for r in ms.schedules}, {ms.items[0].item_code})
            fam = U.famille_machine(U._item(ms.items[0].item_code), RC.familles())
            self.assertIsNotNone(fam, nom)
            self.assertEqual([n for n, fs in U.familles_des_echeanciers(ms.customer).items() if fam in fs], [nom])
            # dates et relances intouchées
            self.assertEqual(len(ms.schedules), frappe.db.count("Maintenance Schedule Detail", {"parent": nom}))
        self.assertEqual(SP.executer(JOUR)["annules"], [])                 # rejouable

    def test_un_achat_de_sel_solde_la_devinette_convertie(self):
        if not any(SP.famille_visee(e["items"]) == "ADOUCISSEUR" for e in self._heritages().values()):
            self.skipTest("plus d'échéancier « sel » sur cette copie")
        convertis = [l.split(" ")[0] for l in SP.executer(JOUR)["remplaces"]]
        # Un client converti, une visite échue ouverte, et une de ses commandes qui n'est ni l'origine de l'échéancier
        # ni déjà posée sur une visite (sinon ligne_a_marquer la refuse, à juste titre).
        for nom in convertis:
            ms = frappe.get_doc("Maintenance Schedule", nom)
            if ms.items[0].item_code != "Ad-MB-30L" or not any(not r.actual_date and getdate(r.scheduled_date) < JOUR for r in ms.schedules):
                continue
            prises = {i.sales_order for i in ms.items} | {r.custom_sales_order for r in ms.schedules} | {""}
            commande = next((c for c in frappe.get_all("Sales Order", filters={"customer": ms.customer, "docstatus": 1}, pluck="name")
                             if c not in prises), None)
            if commande:
                break
        else:
            self.skipTest("aucun client converti avec une visite échue et une autre commande")
        nb = frappe.db.count("Maintenance Schedule", {"customer": ms.customer, "docstatus": 1})
        with mock.patch.object(U, "_reactiver_client", lambda *a, **k: None):
            r = U.update_maintenance_schedule({"customer": ms.customer, "sales_order": commande, "delivery_date": JOUR, "items": "S-S25"})
        self.assertEqual(r, "update")
        self.assertEqual(frappe.db.count("Maintenance Schedule", {"customer": ms.customer, "docstatus": 1}), nb)   # rien de deviné
        self.assertTrue(frappe.db.exists("Maintenance Schedule Detail", {"parent": nom, "custom_sales_order": commande, "actual_date": JOUR}))


class TestListesDAppelSite(_SansTrace):
    """Relecture du 26/11/2026 — les listes d'appels vivantes au moment du patch (b)."""

    def _decouvrir(self):
        """Ce que le patch ferait (essai dans un point de sauvegarde, annulé) : {annulé: client}, {converti: client}."""
        frappe.db.savepoint("decouverte")
        try:
            bilan = SP.executer(JOUR)
        finally:
            frappe.db.rollback(save_point="decouverte")
        nom = lambda l: l.split(" ")[0]
        return bilan, [nom(l) for l in bilan["annules"]], [nom(l) for l in bilan["remplaces"]]

    def _liste(self, lignes, docstatus=0, type_liste="Normal"):
        doc = frappe.get_doc({"doctype": "Liste Appelle Entretien", "type_liste": type_liste, "date": JOUR, "clients": lignes})
        doc.flags.ignore_permissions = True
        doc.insert()
        if docstatus:      # une liste déjà validée, sans passer par sa validation (hors sujet ici)
            frappe.db.sql("update `tabListe Appelle Entretien` set docstatus = 1 where name = %s", doc.name)
            frappe.db.sql("update `tabAppelle Client` set docstatus = 1 where parent = %s", doc.name)
        return doc.name

    def _ligne(self, ms, date="2026-11-01", **k):
        code = frappe.db.get_value("Maintenance Schedule Item", {"parent": ms}, "item_code")
        return dict({"client": frappe.db.get_value("Maintenance Schedule", ms, "customer"), "échéancier_dentretien": ms,
                     "detail_articles": json.dumps({code: date})}, **k), code

    def test_un_brouillon_qui_cite_un_heritage_reste_enregistrable(self):
        bilan, annules, convertis = self._decouvrir()
        if not annules or not convertis:
            self.skipTest("aucun échéancier hérité à annuler ou à convertir sur cette copie")
        l1, ancien = self._ligne(annules[0])
        l2, ancien2 = self._ligne(convertis[0], "2026-11-10")
        brouillon = self._liste([l1, l2])
        source = self._liste([dict(self._ligne(convertis[0])[0], a_été_appelé=1, resume_appel="Ne répond pas 1er appel")], docstatus=1)
        histo = self._liste([dict(self._ligne(convertis[0])[0], a_été_appelé=1, resume_appel="Rendez-vous pris")], docstatus=1)
        bilan = SP.executer(JOUR)
        self.assertEqual(bilan["erreurs"], [])
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", annules[0], "docstatus"), 2)
        doc = frappe.get_doc("Liste Appelle Entretien", brouillon)
        garde = doc.clients[0].échéancier_dentretien
        self.assertNotEqual(garde, annules[0])
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", garde, "docstatus"), 1)
        nouveau = frappe.db.get_value("Maintenance Schedule Item", {"parent": convertis[0]}, "item_code")
        self.assertEqual(json.loads(doc.clients[1].detail_articles), {nouveau: "2026-11-10"})
        # Ce qui bloquait : « Cannot link cancelled document » à l'enregistrement et à la validation de 08:00 / 19:00.
        doc.flags.ignore_permissions = True
        doc.save()
        doc._action = "submit"
        doc._validate_links()
        # Les articles de la ligne renvoyée sont ceux du gardé : la marque « appelé » le trouvera.
        portes = set(frappe.get_all("Maintenance Schedule Detail", filters={"parent": garde}, pluck="item_code"))
        self.assertTrue(set(json.loads(doc.clients[0].detail_articles)) <= portes | {ancien})
        # La ligne relue par le 2e appel suit la conversion ; l'historique ne bouge pas.
        self.assertEqual(set(json.loads(frappe.db.get_value("Appelle Client", {"parent": source}, "detail_articles"))), {nouveau})
        self.assertEqual(set(json.loads(frappe.db.get_value("Appelle Client", {"parent": histo}, "detail_articles"))), {ancien2})
        self.assertTrue(any(l.startswith(brouillon) for l in bilan["lignes_appel"]))
        self.assertFalse(any(l.startswith(histo) for l in bilan["lignes_appel"]))

    def test_la_trace_se_lit_dans_l_historique_client_et_a_surveiller(self):
        bilan = SP.executer(JOUR)
        annules = [l.split(" ")[0] for l in bilan["annules"]]
        if not annules:
            self.skipTest("aucun échéancier hérité à annuler sur cette copie")
        for ms in annules:
            client = frappe.db.get_value("Maintenance Schedule", ms, "customer")
            journal = HC.echeancier(client)["journal"]
            self.assertTrue(any(j["texte"].startswith("🧹") and ms in j["texte"] for j in journal), ms)
        # Un converti qui porte déjà une visite réalisée ne sera pas retiré par la nuit à la vente d'une machine.
        for l in bilan["remplaces"]:
            ms = l.split(" ")[0]
            realisee = frappe.db.exists("Maintenance Schedule Detail", {"parent": ms, "actual_date": ["is", "set"]})
            self.assertEqual(any(x.startswith(ms + " ") for x in bilan["a_surveiller"]), bool(realisee), ms)


class TestRecalageSite(_SansTrace):
    def _lignes(self):
        par = {}
        for r in frappe.db.sql("""select d.name, d.parent, d.idx, d.item_code, d.scheduled_date, d.actual_date, d.completion_status,
                                         d.custom_sms_1, d.custom_sms_2, d.custom_sms1_status, d.custom_sms2_status, d.custom_appelle,
                                         d.custom_1er_appel, d.custom_sales_order
                                  from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                                  where ms.docstatus = 1 order by d.parent, d.idx""", as_dict=True):
            par.setdefault(r.parent, []).append(r)
        return par

    def test_la_reprise_et_le_script_disent_la_meme_chose(self):
        code = frappe.db.get_value("Server Script", SS.NOM, "script") or SS.CODE_ATTENDU
        par = self._lignes()
        self.assertGreater(len(par), 0)
        par_le_script, par_la_reprise = set(), set()
        for nom, lignes in par.items():
            a = copy.deepcopy(lignes)
            exec(code, {"doc": frappe._dict(schedules=a), "frappe": frappe})
            if [str(r.scheduled_date) for r in a] != [str(r.scheduled_date) for r in lignes]:
                par_le_script.add(nom)
            if H.plan_recalage(copy.deepcopy(lignes))["dates"]:
                par_la_reprise.add(nom)
        self.assertEqual(par_la_reprise, par_le_script)       # le 26/11/2026 : aucun des deux (0 sur 3 263)

    def test_un_passage_de_nuit_n_a_rien_a_recaler(self):
        noms = frappe.db.sql_list("""select distinct d.parent from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                                     where ms.docstatus = 1 and d.actual_date is null and ifnull(d.completion_status, '') != 'Fully Completed'
                                       and d.scheduled_date < %s order by d.parent limit 40""", (JOUR,))
        if not noms:
            self.skipTest("aucune visite échue ouverte")
        vus, heritees = 0, set(_en_attente())
        with mock.patch.object(U, "_reactiver_client", lambda *a, **k: None), mock.patch.object(U, "_tracer", lambda *a, **k: None):
            for nom in noms:
                ms = frappe.get_doc("Maintenance Schedule", nom)
                ms.save = lambda *a, **k: None                    # en mémoire seulement
                if U.shift_schedule_for_delivery(ms, JOUR, "SO-ESSAI", None, aujourd_hui=JOUR):
                    vus += 1
                    plan = H.plan_recalage(ms.schedules)
                    self.assertEqual(plan["dates"], [], nom)
                    # seules les visites héritées restées « Pending » (patch visites_faites_restees_en_attente) y figurent
                    self.assertTrue({r.name for r in plan["statuts"]} <= heritees, nom)
        self.assertGreater(vus, 0)

    def test_saisie_a_la_main_enregistree(self):
        nom = frappe.db.sql_list("""select d.parent from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                                    where ms.docstatus = 1 and ms.status = 'Submitted' and d.actual_date is null
                                      and ifnull(d.completion_status, '') != 'Fully Completed' and d.scheduled_date < %s
                                    group by d.parent having count(distinct d.item_code) = 1 order by d.parent limit 1""", (JOUR,))
        if not nom:
            self.skipTest("aucun échéancier à une machine avec une visite échue")
        ms = frappe.get_doc("Maintenance Schedule", nom[0])
        ouvertes = sorted((r for r in ms.schedules if not r.actual_date and r.completion_status != "Fully Completed"
                           and getdate(r.scheduled_date) < JOUR), key=lambda r: (getdate(r.scheduled_date), r.idx))
        ref = ouvertes[0]
        prevue = getdate(ref.scheduled_date)
        ref.actual_date = add_days(prevue, 20)
        attendu = copy.deepcopy(list(ms.schedules))
        H.appliquer_recalage(H.plan_recalage(attendu))
        attendu = {x.name: (str(x.scheduled_date), x.completion_status, x.custom_sms_1, x.custom_appelle) for x in attendu}
        H.before_update_after_submit(ms)                          # ce que fera le hook branché, avant l'enregistrement
        ms.flags.ignore_permissions = True
        ms.save()
        ms.reload()
        r = next(x for x in ms.schedules if x.name == ref.name)
        self.assertEqual((getdate(r.scheduled_date), r.completion_status), (getdate(add_days(prevue, 20)), "Fully Completed"))
        self.assertEqual({x.name: (str(x.scheduled_date), x.completion_status, x.custom_sms_1, x.custom_appelle) for x in ms.schedules}, attendu)
        self.assertGreater(sum(1 for x in ms.schedules if getdate(x.scheduled_date) > prevue and not x.actual_date), 0)
        self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Maintenance Schedule", "reference_name": ms.name,
                                                     "content": ["like", "📅 Visite %réalisée le % (date réelle saisie sur l’échéancier)%"]}))

    def test_patch_du_script(self):
        script = frappe.db.get_value("Server Script", SS.NOM, ["script", "reference_doctype", "doctype_event", "disabled"], as_dict=True)
        if not script or script.disabled or SS.normaliser(script.script) != SS.normaliser(SS.CODE_ATTENDU):
            self.skipTest("Server Script absent, déjà éteint ou modifié sur cette copie")
        vrai = frappe.get_hooks

        def hooks(evenements):
            return lambda nom=None, *a, **k: evenements if nom == "doc_events" else vrai(nom, *a, **k)
        with mock.patch.object(frappe, "clear_cache", lambda *a, **k: None):
            with mock.patch.object(frappe, "get_hooks", hooks({})):
                self.assertIn("pas branché", SS.execute())
            self.assertEqual(frappe.db.get_value("Server Script", SS.NOM, "disabled"), 0)
            branche = {"Maintenance Schedule": {"before_update_after_submit": [SS.HOOK]}}
            with mock.patch.object(frappe, "get_hooks", hooks(branche)):
                self.assertIn("désactivé", SS.execute())
                self.assertEqual(frappe.db.get_value("Server Script", SS.NOM, "disabled"), 1)
                self.assertIn("déjà désactivé", SS.execute())     # rejouable


if __name__ == "__main__":
    unittest.main()
