"""Listes d'appels reprises dans l'app le 25/11/2026 — essai SUR LE SITE (copie de prod), sans trace : commit neutralisé,
SMS / e-mails / alertes / Error Log interceptés, Server Scripts en base court-circuités et hooks de liste_validation branchés le
temps de l'essai (hooks.py ne les porte pas encore), tout annulé à la fin (savepoint).

Ce que l'on prouve :
- une liste se valide avec des lignes non appelées, et ces clients REVIENNENT dans la prochaine liste de leur type
  (Hors secteur / Normal par la visite restée sans `custom_appelle`, Relance et Requalification par la reprise) ;
- « pas de système » annule l'échéancier (il existe toujours) et Non / Non le garde, chacun avec son commentaire ;
- le contrôle d'enregistrement se comporte comme le Server Script ; le passage de 08:00 / 19:00 valide un brouillon
  entièrement appelé et tient le compteur « Appel journalier » ;
- le patch n'éteint les trois Server Scripts que si leur code n'a pas changé, et se rejoue sans effet.
Un essai saute quand la copie n'a pas les données (aucun client à appeler, document absent)."""
from __future__ import annotations

import datetime
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, cint, getdate, nowdate

from customization_app import liste_validation as V
from customization_app import partenaire_clients
from customization_app import relances_config as RC
from customization_app.Maintenance import creation_liste_appelle as L
from customization_app.Maintenance import relance_maintenance_sms as SMS
from customization_app.patches import listes_appels_dans_l_app as P


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

DOCTYPE = V.DOCTYPE
HOOKS = {"validate": "customization_app.liste_validation.controle_saisie",
         "before_submit": "customization_app.liste_validation.validation_finale"}


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("t_listes")
        origine = frappe.get_doc_hooks()
        hooks = {dt: {ev: list(v) for ev, v in evs.items()} for dt, evs in origine.items()}
        evs = hooks.setdefault(DOCTYPE, {})
        for ev, methode in HOOKS.items():
            if methode not in evs.setdefault(ev, []):
                evs[ev].append(methode)
        self.alertes, self.journal, self.erreurs = [], [], []
        self.patches = [
            mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
            # Error Log est en MyISAM : le rollback ne l'efface pas (16 lignes laissées par les essais du 25-26/11/2026).
            mock.patch.object(frappe, "log_error", side_effect=lambda *a, **k: self.erreurs.append((a, k))),
            mock.patch.object(SMS, "envoyer_sms_winsms", side_effect=AssertionError("SMS interdit pendant l'essai")),
            mock.patch("frappe.sendmail", side_effect=AssertionError("e-mail interdit pendant l'essai")),
            mock.patch.object(RC, "alerter", side_effect=lambda sujet, corps, cfg=None: self.alertes.append((sujet, corps)) or True),
            mock.patch("frappe.model.document.run_server_script_for_doc_event", lambda *a, **k: None),
            mock.patch("frappe.get_doc_hooks", lambda: hooks),
            mock.patch.object(L, "log", side_effect=self.journal.append),
            mock.patch.object(V, "log", side_effect=self.journal.append),
        ]
        for p in self.patches:
            p.start()
        self.today, self.cfg = nowdate(), RC.config()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        frappe.db.rollback(save_point="t_listes")

    # ── données de la copie ──────────────────────────────────────────────────
    def repartition(self):
        clients = L.echeances_a_appeler(self.today, self.cfg)
        zones = partenaire_clients.zones_partenaires()
        normal, zone, _u, _e, hors = L.repartir(clients, self.today, self.cfg, RC.clients_exclus_relance(self.cfg), zones,
                                                lambda c: partenaire_clients.zone_partenaire_du_client(c, zones),
                                                RC.clients_avec_rdv(self.cfg), [], False)
        return clients, normal, zone, hors

    def candidats(self, n=1):
        clients, normal, zone, hors = self.repartition()
        noms = sorted(hors) + sorted(normal) + sorted(zone)
        noms = [c for c in noms if c not in partenaire_clients.clients_geres()]
        if len(noms) < n:
            self.skipTest("pas assez de clients à appeler sur cette copie")
        return clients, noms[:n], {"hors": set(hors), "normal": set(normal), "zone": set(zone)}

    def liste(self, clients, noms, type_liste):
        r = L._create_liste_appel_doc(self.today, {c: clients[c] for c in noms}, type_liste, cfg=self.cfg)
        self.assertTrue(r["created"])
        return frappe.get_doc(DOCTYPE, r["name"])


class TestRetourDesLignesNonAppelees(_SansTrace):
    def test_hors_secteur_et_normal_reviennent_avec_le_meme_rang(self):
        clients, (c,), groupes = self.candidats()
        type_liste = L.TYPE_HORS_SECTEUR if c in groupes["hors"] else (L.TYPE_NORMAL if c in groupes["normal"] else L.TYPE_ZONE)
        avant = dict(clients[c])
        doc = self.liste(clients, [c], type_liste)
        doc.submit()                                     # la ligne n'est pas appelée : validation acceptée
        self.assertEqual(doc.docstatus, 1)
        self.assertEqual(str(doc.date_fin), self.today)
        apres, normal, zone, hors = self.repartition()
        self.assertIn(c, apres)                          # toujours candidat : la visite n'est pas marquée appelée
        self.assertEqual(apres[c]["nb_appels"], avant["nb_appels"])          # une ligne non appelée n'est pas un appel
        self.assertEqual(apres[c]["dernier_appel"], avant["dernier_appel"])
        self.assertIn(c, {L.TYPE_HORS_SECTEUR: hors, L.TYPE_NORMAL: normal, L.TYPE_ZONE: zone}[type_liste])
        self.assertTrue(any("non appelée" in l for l in self.journal))

    def test_la_prochaine_liste_du_meme_type_les_reprend(self):
        """Chemin réel du passage de 07:00 (_generate_normal_urgence_list, brouillons respectés) : le client en tête de
        son type, laissé NON APPELÉ dans une liste validée, est dans la liste suivante de ce type — y compris Hors
        secteur plafonnée à 30. Les autres brouillons du type sont écartés le temps de l'essai (annulé à la fin)."""
        _clients, normal, zone, hors = self.repartition()
        geres = partenaire_clients.clients_geres()
        rdv = RC.clients_avec_rdv(self.cfg)
        cle = lambda kv: (kv[1].get("nb_appels", 0), L.cle_secteur(kv[1]["secteurs"]), str(kv[1]["premiere"]))
        essais = [(L.TYPE_HORS_SECTEUR, hors, "hors_secteur"), (L.TYPE_NORMAL, normal, "normal"), (L.TYPE_ZONE, zone, "zone_partenaire")]
        faits = 0
        for type_liste, groupe, resultat in essais:
            ordre = sorted(((c, v) for c, v in groupe.items() if c not in geres), key=cle)
            if not ordre:
                continue
            c = ordre[0][0]
            r = L._create_liste_appel_doc(self.today, {c: groupe[c]}, type_liste, cfg=self.cfg)
            doc = frappe.get_doc(DOCTYPE, r["name"])
            doc.submit()                                         # validée, la ligne n'est pas appelée
            self.assertEqual(int(doc.clients[0].a_été_appelé or 0), 0)
            for n in frappe.get_all(DOCTYPE, filters={"docstatus": 0, "type_liste": type_liste}, pluck="name"):
                frappe.db.set_value(DOCTYPE, n, "docstatus", 2, update_modified=False)
            res = L._generate_normal_urgence_list(
                self.today, rdv, create_normal=type_liste == L.TYPE_NORMAL, create_urgence=False, ignore_draft_normal=False,
                create_zone=type_liste == L.TYPE_ZONE, cfg=self.cfg, create_hors=type_liste == L.TYPE_HORS_SECTEUR)
            suivante = res.get(resultat) or {}
            self.assertTrue(suivante.get("created"), "%s : la liste suivante n'a pas été créée" % type_liste)
            self.assertNotEqual(suivante["name"], doc.name)
            dedans = frappe.get_all("Appelle Client", filters={"parent": suivante["name"], "parenttype": DOCTYPE}, pluck="client")
            self.assertIn(c, dedans, "%s : %s non appelé n'est pas revenu dans %s" % (type_liste, c, suivante["name"]))
            faits += 1
        if not faits:
            self.skipTest("aucun client à appeler sur cette copie")

    def test_urgence_revient_pour_le_meme_rendez_vous(self):
        """26/11/2026 (relecture) : une Urgence validée avec des lignes non appelées ne couvre plus le passage du
        technicien. Au passage suivant, une nouvelle Urgence est créée pour CE rendez-vous et reprend le client en tête.
        Avant, le rendez-vous restait « couvert » et ces clients partaient dans la Normal, après le passage. Une Urgence
        validée entièrement appelée couvre toujours. Les Urgence existantes sont écartées le temps de l'essai."""
        for n in frappe.get_all(DOCTYPE, filters={"type_liste": L.TYPE_URGENCE, "docstatus": ["!=", 2]}, pluck="name"):
            frappe.db.set_value(DOCTYPE, n, "docstatus", 2, update_modified=False)
        if not L.get_urgence_context(self.today, self.cfg)["gener_urgence"]:
            self.skipTest("aucun rendez-vous à venir dans un secteur d'urgence sur cette copie")
        rdv = RC.clients_avec_rdv(self.cfg)
        generer = lambda: L._generate_normal_urgence_list(self.today, rdv, create_normal=False, create_urgence=True, ignore_draft_normal=False,
                                                          create_zone=False, cfg=self.cfg, create_hors=False).get("urgence") or {}
        r1 = generer()
        if not r1.get("created"):
            self.skipTest("aucun client à échéance dans les secteurs des rendez-vous lointains")
        self.assertFalse(L.get_urgence_context(self.today, self.cfg)["gener_urgence"])        # brouillon : le passage est couvert
        u1 = frappe.get_doc(DOCTYPE, r1["name"])
        premier = u1.clients[0].client
        u1.submit()                                                                            # validée sans un appel
        self.assertEqual(u1.docstatus, 1)
        self.assertTrue(L.get_urgence_context(self.today, self.cfg)["gener_urgence"])         # plus couvert
        r2 = generer()
        self.assertTrue(r2.get("created"), "la nouvelle Urgence n'a pas été créée")
        self.assertNotEqual(r2["name"], u1.name)
        self.assertIn(premier, frappe.get_all("Appelle Client", filters={"parent": r2["name"], "parenttype": DOCTYPE}, pluck="client"))
        self.assertEqual(frappe.db.get_value(DOCTYPE, r2["name"], "liste_maintenance"), frappe.db.get_value(DOCTYPE, u1.name, "liste_maintenance"))
        # Entièrement appelée puis validée : elle couvre (écriture directe, sans les effets d'appel, annulée à la fin).
        frappe.db.sql("update `tabAppelle Client` set a_été_appelé = 1 where parent in %s and parenttype = %s", ((u1.name, r2["name"]), DOCTYPE))
        frappe.db.set_value(DOCTYPE, r2["name"], "docstatus", 1, update_modified=False)
        self.assertFalse(L.get_urgence_context(self.today, self.cfg)["gener_urgence"])

    def test_relance_reprend_ses_lignes_non_appelees(self):
        clients, (c,), _g = self.candidats()
        source = self.liste(clients, [c], L.TYPE_NORMAL)
        source.clients[0].a_été_appelé, source.clients[0].resume_appel = 1, L.NE_REPOND_PAS
        source.save()
        source.submit()
        frappe.db.set_value(DOCTYPE, source.name, "date_fin", add_days(self.today, -self.cfg["delai_2e_appel_jours"]), update_modified=False)
        r1 = L._generate_second_call_list(self.today, set(), self.cfg)
        rel1 = frappe.db.get_value("Appelle Client", {"client": c, "parenttype": DOCTYPE,
                                                      "parent": ["in", [x["name"] for x in r1.get("listes") or []] or [""]]}, "parent")
        self.assertTrue(rel1, "le 1er appel sans réponse doit partir en Relance")
        frappe.get_doc(DOCTYPE, rel1).submit()           # Relance validée sans avoir appelé
        self.assertIn(rel1, L._reprises_relance())
        r2 = L._generate_second_call_list(self.today, set(), self.cfg)
        rel2 = frappe.db.get_value("Appelle Client", {"client": c, "parenttype": DOCTYPE,
                                                      "parent": ["in", [x["name"] for x in r2.get("listes") or []] or [""]]}, "parent")
        self.assertTrue(rel2, "la ligne non appelée de la Relance doit revenir dans la Relance suivante")
        self.assertNotEqual(rel1, rel2)
        self.assertEqual(frappe.db.get_value(DOCTYPE, rel1, "liste_2iéme_relance"), 1)
        self.assertGreaterEqual(r2["reprises"], 1)
        self.assertNotIn(rel1, L._reprises_relance())    # reprise une seule fois

    def test_requalification_reprend_ses_lignes_non_appelees(self):
        if not frappe.db.has_column("Customer", "custom_statut_relance"):
            self.skipTest("champ custom_statut_relance absent")
        clients, (c,), _g = self.candidats()
        frappe.db.set_value("Customer", c, {"custom_statut_relance": "À requalifier", "custom_relance_reportee_au": None})
        self.assertIn(c, L.clients_requalification_a_lister(self.today))
        r = L._generate_requalification_list(self.today, self.cfg)
        self.assertTrue(r and r.get("created"))
        doc = frappe.get_doc(DOCTYPE, r["name"])
        self.assertIn(c, [l.client for l in doc.clients])
        self.assertNotIn(c, L.clients_requalification_a_lister(self.today))     # brouillon : l'appel est en cours
        doc.submit()                                                           # validée sans l'avoir appelé
        self.assertIn(c, L.clients_requalification_a_lister(self.today))        # il revient


class TestEcheanciersALaValidation(_SansTrace):
    def test_pas_de_systeme_annule_non_non_conserve(self):
        clients, (c1, c2), _g = self.candidats(2)
        doc = self.liste(clients, [c1, c2], L.TYPE_NORMAL)
        l1, l2 = (next(l for l in doc.clients if l.client == c) for c in (c1, c2))
        l1.update({"a_été_appelé": 1, "resume_appel": V.PAS_DE_SYSTEME, "intéressé_par_le_service_dentretien": "Non",
                   "intéressé_par_le_service_de_relance": "Non"})
        l2.update({"a_été_appelé": 1, "resume_appel": "Autres reponses", "reponse_client": "a vendu la maison",
                   "intéressé_par_le_service_dentretien": "Non", "intéressé_par_le_service_de_relance": "Non"})
        doc.save()
        doc.submit()
        ms1, ms2 = l1.échéancier_dentretien, l2.échéancier_dentretien
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms1, "docstatus"), 2)       # annulé, PLUS supprimé
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms1, "status"), "Cancelled")
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms2, "docstatus"), 1)       # conservé
        for ms, morceau in ((ms1, "Échéancier annulé"), (ms2, "Échéancier conservé")):
            com = frappe.get_all("Comment", filters={"reference_doctype": "Maintenance Schedule", "reference_name": ms,
                                                     "content": ["like", "%" + doc.name + "%"]}, pluck="content")
            self.assertTrue(com and morceau in com[0], ms)
        self.assertIn("a vendu la maison", frappe.get_all("Comment", filters={"reference_name": ms2, "reference_doctype": "Maintenance Schedule"},
                                                         pluck="content", order_by="creation desc", limit=1)[0])
        self.assertEqual(frappe.db.get_value("Customer", c1, "custom_statut_relance"), "Perdu")   # liste_appels (on_update), inchangé

    def test_pas_de_systeme_annule_les_autres_echeanciers_du_client(self):
        """26/11/2026 (relecture) : la réponse vaut pour le client. Ses autres échéanciers soumis qui portent un article
        de la ligne sont annulés et commentés aussi (le script n'annulait que l'échéancier principal)."""
        clients = L.echeances_a_appeler(self.today, self.cfg)
        geres = partenaire_clients.clients_geres()
        c = next((n for n in sorted(clients) if len(clients[n]["echeanciers"]) > 1 and n not in geres), None)
        if not c:
            self.skipTest("aucun client à appeler avec plusieurs échéanciers sur cette copie")
        doc = self.liste(clients, [c], L.TYPE_NORMAL)
        l = doc.clients[0]
        self.assertIn("Autres échéanciers", l.info or "")
        l.update({"a_été_appelé": 1, "resume_appel": V.PAS_DE_SYSTEME, "intéressé_par_le_service_dentretien": "Non",
                  "intéressé_par_le_service_de_relance": "Non"})
        doc.save()
        autres = set(V._autres_echeanciers(l))
        self.assertTrue(set(clients[c]["echeanciers"]) - {l.échéancier_dentretien} <= autres)
        doc.submit()
        for ms in {l.échéancier_dentretien} | autres:
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms, "docstatus"), 2, ms)      # annulé, pas supprimé
            com = frappe.get_all("Comment", filters={"reference_doctype": "Maintenance Schedule", "reference_name": ms,
                                                     "content": ["like", "%" + doc.name + "%"]}, pluck="content")
            self.assertTrue(any("Échéancier annulé" in x for x in com), ms)
            if ms != l.échéancier_dentretien:
                self.assertTrue(any("la ligne portait %s" % l.échéancier_dentretien in x for x in com), ms)

    def test_non_non_garde_son_echeancier_apres_le_passage_de_nuit(self):
        """26/11/2026 (relecture) : la décision « Non / Non → échéancier conservé » ne tient qu'avec le lot 4 du passage
        de nuit. La version commitée (98e2a3c) de verify_data_base annulait puis supprimait ces échéanciers la nuit même.
        Ce test enchaîne la validation puis la nuit : il ÉCHOUE si le lot « listes » part sans le lot 4."""
        from customization_app.Maintenance import update_schedule as US
        clients, noms, _g = self.candidats(10)
        b2b, proteges = set(self.cfg["groupes_b2b_liste"]), set(self.cfg["clients_proteges_liste"])
        c = next((n for n in noms if n not in proteges and frappe.db.get_value("Customer", n, "customer_group") not in b2b), None)
        if not c:
            self.skipTest("aucun client particulier à appeler sur cette copie")
        doc = self.liste(clients, [c], L.TYPE_NORMAL)
        l = doc.clients[0]
        l.update({"a_été_appelé": 1, "resume_appel": "Autres reponses", "reponse_client": "ne veut plus rien",
                  "intéressé_par_le_service_dentretien": "Non", "intéressé_par_le_service_de_relance": "Non"})
        doc.save()
        doc.submit()
        ms = l.échéancier_dentretien
        self.assertEqual(frappe.db.get_value("Customer", c, ["custom_intéressé_par_le_service_entretien", "custom_envoi_sms"]), ("Non", "Non"))
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms, "docstatus"), 1)
        with mock.patch.object(US, "log"):
            US.verify_data_base(self.cfg)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms, "docstatus"), 1,
                         "le passage de nuit a retiré l'échéancier Non / Non : livrer le lot 4 avec ce lot")

    def test_controle_saisie_comme_le_script(self):
        clients, (c,), _g = self.candidats()
        doc = self.liste(clients, [c], L.TYPE_NORMAL)
        doc.clients[0].a_été_appelé = 1
        with self.assertRaises(frappe.ValidationError) as e:
            doc.save()
        self.assertIn("Merci d'indiquer le Résumé d'appel pour le client: %s dans la ligne: 1" % c, str(e.exception))
        doc = frappe.get_doc(DOCTYPE, doc.name)
        doc.clients[0].a_été_appelé, doc.clients[0].resume_appel = 1, V.RDV_PRIS
        doc.save()
        self.assertEqual(int(doc.nb_r_c), V._rendez_vous(c, doc.date, "Entretien"))


class TestPassage8h19h(_SansTrace):
    def test_valide_le_brouillon_appele_et_tient_le_compteur(self):
        if not frappe.db.exists("Appel journalier", V.APPEL_JOURNALIER):
            self.skipTest("document Appel journalier absent")
        clients, (c,), _g = self.candidats()
        doc = self.liste(clients, [c], L.TYPE_NORMAL)
        doc.clients[0].a_été_appelé, doc.clients[0].resume_appel = 1, "Fait lui-même"
        doc.nb_appels_restant = "0"
        doc.save()
        faux = self.liste(clients, [c], L.TYPE_URGENCE)        # compteur à 0 mais ligne non appelée : laissée
        frappe.db.set_value(DOCTYPE, faux.name, "nb_appels_restant", "0", update_modified=False)
        attendu = sum(V._entier(v) or 0 for n, v in frappe.get_all(DOCTYPE, filters={"docstatus": 0}, fields=["name", "nb_appels_restant"], as_list=True))
        jour = getdate(self.today)
        with mock.patch("customization_app.api.recolorer_taches_partenaire", return_value=["Tache-1 -> #fff"]):
            matin = V._passage_8h_19h(datetime.datetime.combine(jour, datetime.time(8, 0)))
            soir = V._passage_8h_19h(datetime.datetime.combine(jour, datetime.time(19, 0)))
        self.assertIn(doc.name, matin["validees"])
        self.assertIn(faux.name, matin["ecartees"])
        self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "docstatus"), 1)
        self.assertEqual(frappe.db.get_value(DOCTYPE, faux.name, "docstatus"), 0)
        self.assertEqual(matin["recolorees"], 1)
        self.assertEqual(matin["restant"], attendu)
        self.assertEqual((matin["stats"]["matin"], soir["stats"]["soir"]), (attendu, attendu))
        self.assertEqual(soir["stats"]["nombre_appels"], 0)
        ligne = next(r for r in frappe.get_doc("Appel journalier", V.APPEL_JOURNALIER).stats_journaliere if r.date and getdate(r.date) == jour)
        self.assertEqual((ligne.matin, ligne.soir), (attendu, attendu))

    def test_lignes_laissees_par_une_validation_ne_sont_pas_des_appels(self):
        """26/11/2026 (relecture) : une liste brouillon à 08:00, une ligne appelée puis validée à 10:00 avec l'autre
        laissée. Le compteur du soir dit 1 appel, et non 2 (matin − soir)."""
        if not frappe.db.exists("Appel journalier", V.APPEL_JOURNALIER):
            self.skipTest("document Appel journalier absent")
        clients, (c1, c2), _g = self.candidats(2)
        jour = getdate(self.today)
        doc = self.liste(clients, [c1, c2], L.TYPE_NORMAL)
        frappe.db.sql("update `tabListe Appelle Entretien` set creation = %s, modified = %s where name = %s",
                      ("%s 07:00:00" % jour, "%s 07:00:00" % jour, doc.name))
        avant = V._lignes_laissees_du_jour(jour)
        with mock.patch("customization_app.api.recolorer_taches_partenaire", return_value=[]):
            matin = V._passage_8h_19h(datetime.datetime.combine(jour, datetime.time(8, 0)))
            doc = frappe.get_doc(DOCTYPE, doc.name)
            doc.clients[0].a_été_appelé, doc.clients[0].resume_appel = 1, "Autres reponses"
            doc.nb_appels_restant = "1"
            doc.save()
            doc.submit()                                    # une ligne laissée non appelée
            frappe.db.sql("update `tabListe Appelle Entretien` set modified = %s where name = %s", ("%s 10:00:00" % jour, doc.name))
            self.assertEqual(V._lignes_laissees_du_jour(jour), avant + 1)
            soir = V._passage_8h_19h(datetime.datetime.combine(jour, datetime.time(19, 0)))
        self.assertEqual(matin["stats"]["laissees"], 0)
        self.assertEqual(soir["stats"]["laissees"], avant + 1)
        self.assertEqual(matin["restant"] - soir["restant"], 2)          # la liste a quitté les brouillons avec ses 2 lignes…
        self.assertEqual(soir["stats"]["nombre_appels"], 1 - avant)      # … mais un seul appel a été passé
        self.assertTrue(any("laissée(s) non appelée(s)" in j for j in self.journal))

    def test_ancien_script_reactive_signale(self):
        """26/11/2026 : la fixture réimporte « ajuster rendez vous pris par partenaire » actif à chaque migrate."""
        nom = "ajuster rendez vous pris par partenaire"
        if not frappe.db.exists("Server Script", nom):
            self.skipTest("Server Script « %s » absent de cette copie" % nom)
        for n in V.ANCIENS_SCRIPTS:
            if frappe.db.exists("Server Script", n):
                frappe.db.set_value("Server Script", n, "disabled", 1, update_modified=False)
        frappe.db.set_value("Server Script", nom, "disabled", 0, update_modified=False)
        moment = datetime.datetime.combine(getdate(self.today), datetime.time(8, 0))
        with mock.patch("customization_app.api.recolorer_taches_partenaire", return_value=[]), \
                mock.patch.object(V, "_noter_appel_journalier", return_value=None):
            r = V._passage_8h_19h(moment)
            self.assertEqual(r["anciens_scripts_actifs"], [nom])
            self.assertTrue(any(l.startswith("[ATTENTION]") and nom in l for l in self.journal))
            frappe.db.set_value("Server Script", nom, "disabled", 1, update_modified=False)
            del self.journal[:]
            r = V._passage_8h_19h(moment)
        self.assertEqual(r["anciens_scripts_actifs"], [])
        self.assertFalse(any(l.startswith("[ATTENTION]") for l in self.journal))

    def test_sans_aucun_brouillon_pas_d_erreur(self):
        for n in frappe.get_all(DOCTYPE, filters={"docstatus": 0}, pluck="name"):
            frappe.db.set_value(DOCTYPE, n, "docstatus", 2, update_modified=False)
        with mock.patch("customization_app.api.recolorer_taches_partenaire", return_value=[]):
            r = V._passage_8h_19h(datetime.datetime.combine(getdate(self.today), datetime.time(8, 0)))
        self.assertEqual((r["validees"], r["erreurs"], r["restant"]), ([], [], 0))


class TestPatchServerScripts(_SansTrace):
    def tearDown(self):
        super().tearDown()
        frappe.cache.delete_value("server_script_map")     # jamais de carte bâtie sur l'état annulé

    def test_eteint_seulement_le_code_porte_et_se_rejoue(self):
        for nom in P.SCRIPTS:
            if not frappe.db.exists("Server Script", nom):
                self.skipTest("Server Script « %s » absent de cette copie" % nom)
        frappe.db.set_value("Server Script", "Valider Liste appelle", "disabled", 0)
        code = frappe.db.get_value("Server Script", "validation finale liste appelle", "script")
        frappe.db.set_value("Server Script", "validation finale liste appelle", {"disabled": 0, "script": code + "\n# modifié en prod"})
        with mock.patch.object(frappe, "clear_cache"):
            b = P.execute()
            self.assertIn("validation finale liste appelle", b["laisses_actifs"])
            self.assertEqual(frappe.db.get_value("Server Script", "validation finale liste appelle", "disabled"), 0)
            self.assertTrue(any(k.get("title") == "Patch listes d'appels : Server Script laissé actif" for _a, k in self.erreurs))
            self.assertEqual(frappe.db.get_value("Server Script", "Valider Liste appelle", "disabled"), 1)
            nom_cron = "ajuster rendez vous pris par partenaire"
            self.assertEqual(frappe.db.get_value("Server Script", nom_cron, "disabled"), 1)
            self.assertFalse(frappe.get_all("Scheduled Job Type", filters={"server_script": nom_cron}))   # supprimée, plus arrêtée
            b2 = P.execute()                                # rejoué : plus rien à faire
            self.assertEqual((b2["eteints"], b2["jobs_supprimes"]), ([], []))
            frappe.db.set_value("Server Script", "validation finale liste appelle", "script", code)
            b3 = P.execute()                                # le code redevient celui porté : éteint
            self.assertEqual(b3["eteints"], ["validation finale liste appelle"])

    def _singles(self):
        return dict(frappe.db.sql("select field, value from tabSingles where doctype = %s", (P.CONFIG,)))

    def test_valeurs_initiales_des_reglages_ecrites_une_fois(self):
        """26/11/2026 (relecture) : Config Relances est déjà enregistré ; les trois réglages du lot y sont écrits
        (1 / 30 / 3) seulement s'ils manquent, sans toucher `modified`, et une valeur posée reste."""
        if not self._singles():
            self.skipTest("Config Relances jamais enregistré sur cette copie")
        frappe.db.sql("delete from tabSingles where doctype = %s and field in %s", (P.CONFIG, tuple(P.VALEURS_INITIALES)))
        modifie = self._singles().get("modified")
        with mock.patch.object(frappe, "clear_cache"):
            b = P.execute()
            self.assertEqual(b["reglages_initialises"], P.VALEURS_INITIALES)
            lus = self._singles()
            self.assertEqual({k: str(lus.get(k)) for k in P.VALEURS_INITIALES},
                             {"valider_avec_lignes_non_appelees": "1", "plafond_hors_secteur": "30", "jours_brouillon_alerte": "3"})
            self.assertEqual(lus.get("modified"), modifie)
            self.assertEqual(P.execute()["reglages_initialises"], {})          # rejoué : rien
            frappe.db.set_single_value(P.CONFIG, "plafond_hors_secteur", 12, update_modified=False)
            P.execute()
            self.assertEqual(str(self._singles()["plafond_hors_secteur"]), "12")
        frappe.local._config_relances = None

    def test_un_enregistrement_du_reglage_garde_les_valeurs(self):
        """Le défaut corrigé : sans valeur initiale, le premier enregistrement de Config Relances écrit 0 dans les
        champs neufs. Se lance quand les champs sont dans le DocType (après l'intégration et le migrate)."""
        meta = frappe.get_meta(P.CONFIG)
        if not all(meta.has_field(k) for k in P.VALEURS_INITIALES):
            self.skipTest("champs du lot pas encore dans le DocType Config Relances")
        frappe.db.sql("delete from tabSingles where doctype = %s and field in %s", (P.CONFIG, tuple(P.VALEURS_INITIALES)))
        with mock.patch.object(frappe, "clear_cache"):
            P.execute()
        frappe.get_doc(P.CONFIG).save(ignore_permissions=True)
        frappe.local._config_relances = None
        cfg = RC.config()
        self.assertEqual((cint(cfg.get("valider_avec_lignes_non_appelees", 1)), L.plafond_du_type(L.TYPE_HORS_SECTEUR, cfg),
                          cint(cfg.get("jours_brouillon_alerte", 3))), (1, 30, 3))
        frappe.local._config_relances = None

    def test_les_scripts_repris_ne_sont_plus_des_fixtures(self):
        """Relecture finale du 26/11/2026 : réimporté de force APRÈS les patches à chaque migrate, « ajuster rendez vous
        pris par partenaire » écrasait une retouche faite en prod, et sa tâche planifiée faisait échouer le migrate
        (LinkValidationError) dès qu'elle existait (script laissé actif par le patch, ou recoché). Il n'est plus une
        fixture, ni « cancel sales order payment » : seul le patch décide, avec son empreinte."""
        import json
        chemin = frappe.get_app_path("customization_app", "fixtures", "server_script.json")
        with open(chemin, encoding="utf-8") as fh:
            exportes = {d.get("name") for d in json.load(fh)}
        filtre = next(f for f in frappe.get_hooks("fixtures") if isinstance(f, dict) and f.get("doctype") == "Server Script")
        noms = set(filtre["filters"][0][2])
        for nom in ("ajuster rendez vous pris par partenaire", "cancel sales order payment"):
            self.assertNotIn(nom, exportes)
            self.assertNotIn(nom, noms)
        self.assertTrue(exportes <= noms)                       # le fichier ne contient que des scripts du filtre

if __name__ == "__main__":
    unittest.main()
