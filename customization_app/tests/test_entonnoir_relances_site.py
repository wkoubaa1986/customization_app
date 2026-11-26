"""Entonnoir des relances (lot 6.1 du 25-26/11/2026) — essais SUR LE SITE (copie de prod du 25/11/2026 12:26), sans trace :
commit neutralisé, SMS / e-mails interdits, alertes enregistrées au lieu d'être envoyées, tout annulé à la fin.

- la photo du jour se lit SANS RIEN ÉCRIRE (aucune requête d'écriture) et chaque partie se calcule ;
- ses nombres sont ceux de la base : visites échues, échéanciers sans visite à venir (dont ceux que la nuit doit
  ranimer = update_schedule.echeanciers_a_prolonger), brouillons, listes créées par type, SMS 1 / SMS 2 ;
- noter() et la photo enregistrée : un enregistrement par jour, fusion, alerte une seule fois par signal nouveau ;
  tant que la table « Entonnoir Relances » n'existe pas (migration à faire), noter rend False sans bruit ;
- la page « Journaux des relances » rend son entonnoir.
Un essai saute quand la copie n'a pas les données."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, getdate, nowdate

from customization_app import entonnoir_relances as ER
from customization_app import relances_config as RC
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

ECRITURES = ("insert", "update", "delete", "replace", "alter", "create", "drop", "truncate", "rename")


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("t_entonnoir")
        self.alertes = []
        self.patches = [
            mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
            mock.patch.object(SMS, "envoyer_sms_winsms", side_effect=AssertionError("SMS interdit pendant l'essai")),
            mock.patch("frappe.sendmail", side_effect=AssertionError("e-mail interdit pendant l'essai")),
            mock.patch.object(RC, "alerter", side_effect=lambda sujet, corps, cfg=None: self.alertes.append((sujet, corps)) or True),
        ]
        for p in self.patches:
            p.start()
        self.today = getdate(nowdate())

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        frappe.db.rollback(save_point="t_entonnoir")


class TestPhotoLectureSeule(_SansTrace):
    def test_aucune_ecriture_et_toutes_les_parties(self):
        requetes = []
        vrai_sql = frappe.db.sql

        def espion(query, *a, **k):
            requetes.append(str(query).strip().lower())
            return vrai_sql(query, *a, **k)

        with mock.patch.object(frappe.db, "sql", side_effect=espion), mock.patch.object(frappe, "log_error") as log:
            photo = ER.indicateurs(self.today, journaliser=False)
        ecrit = [q[:80] for q in requetes if q.split(None, 1)[0] in ECRITURES]
        self.assertEqual(ecrit, [], "la photo ne doit rien écrire")
        self.assertEqual(photo["erreurs_calcul"], [])
        log.assert_not_called()
        for partie in ("visites_echues", "appel", "sans_visite_a_venir", "brouillons", "flux"):
            self.assertIsNotNone(photo[partie], partie)
        self.assertEqual(photo["jour"], str(self.today))
        self.assertFalse(photo["reconstitue"])
        json.dumps(ER.propre(photo))

    def test_jour_passe_reconstitue_sans_entonnoir_des_appels(self):
        photo = ER.indicateurs(add_days(self.today, -2), journaliser=False)
        self.assertTrue(photo["reconstitue"])
        self.assertIsNone(photo["appel"])
        self.assertEqual(photo["erreurs_calcul"], [])
        # un jour futur est ramené à aujourd'hui
        self.assertEqual(ER.indicateurs(add_days(self.today, 5), journaliser=False)["jour"], str(self.today))


class TestNombresDeLaBase(_SansTrace):
    def test_visites_echues(self):
        v = ER.visites_echues(self.today)
        # « au matin du jour » : une visite faite aujourd'hui compte encore comme échue non faite
        direct = frappe.db.sql("""select count(*) from `tabMaintenance Schedule Detail` d
                                  join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
                                  where d.scheduled_date < %(j)s and (d.actual_date is null or d.actual_date >= %(j)s)
                                    and d.creation < %(l)s""", {"j": self.today, "l": add_days(self.today, 1)})[0][0]
        if not direct:
            self.skipTest("aucune visite échue sur cette copie")
        self.assertEqual(v["lignes"], direct)
        self.assertEqual(sum(x["lignes"] for x in v["par_statut"].values()), v["lignes"])
        self.assertEqual(sum(x["clients"] for x in v["par_statut"].values()), v["clients"])
        self.assertEqual(set(v["par_statut"]), set(ER.STATUTS))
        self.assertLessEqual(v["plus_6_mois"], v["lignes"])

    def test_echeanciers_sans_visite_et_la_nuit(self):
        from customization_app.Maintenance import update_schedule as US
        s = ER.echeanciers_sans_visite(self.today)
        self.assertEqual(s["echeanciers"], len(US.echeanciers_epuises(str(self.today))))
        if hasattr(US, "echeanciers_a_prolonger"):
            self.assertTrue(s["regle_gel"] or not s["echeanciers"])
            self.assertEqual(s["a_prolonger"], len(US.echeanciers_a_prolonger(str(self.today))))
        self.assertEqual(s["a_prolonger"] + s["geles"], s["echeanciers"])
        self.assertLessEqual(s["relancables"], s["clients"])

    def test_brouillons(self):
        bs = ER.brouillons(self.today, self.today)
        n = frappe.db.count("Liste Appelle Entretien", {"docstatus": 0})
        self.assertEqual(len(bs), n)
        for b in bs:
            self.assertGreaterEqual(b["age_jo"], b["inactif_jo"])
            self.assertLessEqual(b["appelees"], b["lignes"])
        hs = [b for b in bs if b["name"] == "LIST-06-11-2026-00004"]
        if hs and self.today >= getdate("2026-11-25"):
            self.assertGreaterEqual(hs[0]["inactif_jo"], 11)        # plus touchée depuis le 11/11

    def test_flux_listes_et_sms(self):
        debut = add_days(self.today, -6)
        fl = ER.flux(debut, self.today)
        self.assertEqual(len(fl), 7)
        listes = frappe.db.sql("""select date(l.creation), ifnull(l.type_liste, 'Normal'), count(a.name)
                                  from `tabListe Appelle Entretien` l
                                  left join `tabAppelle Client` a on a.parent = l.name and a.parenttype = 'Liste Appelle Entretien'
                                  where l.creation >= %s and l.docstatus < 2 group by 1, 2""", (debut,))
        for j, t, lignes in listes:
            self.assertEqual(fl[str(j)]["listes"][t]["lignes"], lignes, (j, t))
        # SMS par issue (relecture du 26/11/2026) : réussis = « Success » (ou vide, 2023), échecs = « Failed »
        for champ, statut, cle in (("custom_sms_1", "custom_sms1_status", "sms1"), ("custom_sms_2", "custom_sms2_status", "sms2")):
            for j, ok, ko, inv in frappe.db.sql("select " + champ + ", sum(ifnull(" + statut + ", '') in ('Success', '')), sum(" + statut
                                                + " = 'Failed'), sum(" + statut + " = 'Invalid Number') from `tabMaintenance Schedule Detail` where "
                                                + champ + " between %s and %s group by 1", (debut, self.today)):
                self.assertEqual(fl[str(j)][cle], ok, (champ, j))
                self.assertEqual(fl[str(j)][cle + "_echec"], ko or 0, (champ, j))
                self.assertEqual(fl[str(j)][cle + "_invalide"], inv or 0, (champ, j))
        for f in fl.values():
            m = f["machines"]
            self.assertEqual(m["lignes"], m["b2b"] + m["non"] + m["attendues"])
            self.assertLessEqual(m["suivies"], m["attendues"])
            self.assertEqual(f["nes"]["total"], f["nes"]["machine"] + f["nes"]["devine"])
            self.assertTrue(f["passages"], "les passages des crons manquent")

    def test_sms_en_echec_ne_compte_pas_comme_envoye(self):
        # Relecture du 26/11/2026 : marquer_envoi pose la date du SMS même quand WinSMS échoue (« Failed ») ; de décembre
        # 2025 à avril 2026, tous les envois de la copie le sont. Une visite « Failed » ne doit pas compter comme partie.
        jour = add_days(self.today, -3)
        row = frappe.db.sql("""select d.name from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                               where ms.docstatus = 1 and d.custom_sms_1 is null limit 1""")
        if not row:
            self.skipTest("aucune visite sans SMS 1 sur cette copie")
        avant = ER.flux(jour, jour)[str(jour)]
        frappe.db.sql("update `tabMaintenance Schedule Detail` set custom_sms_1 = %s, custom_sms1_status = 'Failed' where name = %s",
                      (jour, row[0][0]))
        apres = ER.flux(jour, jour)[str(jour)]
        self.assertEqual(apres["sms1"], avant["sms1"])                      # pas un SMS envoyé
        self.assertEqual(apres["sms1_echec"], avant["sms1_echec"] + 1)
        self.assertEqual(apres["sms_clients"], avant["sms_clients"])        # ni un client joint

    def test_rdv_du_calendrier_compte_comme_appel(self):
        # LIST-24-11-2026-00001 : la ligne 4396jao4f8 a pris son RDV (Tache-09562) le 24/11 à 10:03 par db.set_value ;
        # aucune Version ne la montre. Elle compte le 24/11, une seule fois.
        ligne = frappe.db.get_value("Appelle Client", "4396jao4f8", ["parent", "a_été_appelé", "tache_de_travail"], as_dict=True)
        if not ligne or not ligne.tache_de_travail or not ligne["a_été_appelé"]:
            self.skipTest("ligne d'exemple absente de cette copie")
        jour = getdate(frappe.db.get_value("Tache de travail", ligne.tache_de_travail, "creation"))
        fl = ER.flux(add_days(jour, -1), add_days(jour, 1))
        self.assertGreaterEqual(fl[str(jour)]["appelees_rdv"], 1)
        versions = frappe.db.sql("select creation, data from `tabVersion` where ref_doctype = 'Liste Appelle Entretien' and creation >= %s and creation < %s",
                                 (add_days(jour, -1), add_days(jour, 2)))
        seules = ER.appels_des_versions(versions)
        self.assertEqual(fl[str(jour)]["appelees"], len(seules.get(str(jour), set())) + fl[str(jour)]["appelees_rdv"])

    def test_entonnoir_des_appels(self):
        a = ER.entonnoir_appel(self.today)
        if not a["candidats"]:
            self.skipTest("aucun candidat aux listes sur cette copie")
        classes = sum(x["clients"] for x in a["a_appeler"])
        self.assertEqual(a["exclus"]["total"] + a["rdv"] + a["appel_recent"] + classes + a["autres_ecartes"], a["candidats"])
        self.assertEqual(sum(a["exclus"]["motifs"].values()), a["exclus"]["total"])
        for x in a["a_appeler"]:
            self.assertEqual(x["dans_un_brouillon"] + x["en_attente"], x["clients"])


class TestTableAbsente(_SansTrace):
    def test_table_absente_sans_bruit(self):
        if ER._table_prete():
            self.skipTest("table présente (migration faite)")
        with mock.patch.object(frappe, "log_error") as log:
            self.assertFalse(ER.noter("sms", {"envoyes": 3}))
            r = ER.enregistrer_photo()
        log.assert_not_called()
        self.assertFalse(r["enregistre"])
        self.assertEqual(self.alertes, [])


class TestNoterEtPhotoEnregistree(_SansTrace):
    """Sur la vraie table une fois migrée ; avant la migration, sur une table TEMPORAIRE de même forme (CREATE TEMPORARY
    TABLE : pas de commit implicite, invisible des autres connexions, supprimée à la fin) avec la méta et le contrôleur
    lus dans les fichiers du DocType — le chemin réel de noter() et de la photo est ainsi essayé dès le 26/11/2026."""

    def setUp(self):
        self.temporaire, self.patches_table = False, []
        if not ER._table_prete():
            self._table_temporaire()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self.patches_table):
            p.stop()
        if self.temporaire:
            frappe.db.rollback()
            frappe.db.sql("drop temporary table if exists `tab%s`" % ER.DOCTYPE)

    def _table_temporaire(self):
        import os

        import frappe.model.document as DOC
        from frappe.model import meta as M

        from customization_app.customize_erpnext.doctype.entonnoir_relances.entonnoir_relances import EntonnoirRelances
        chemin = os.path.join(os.path.dirname(ER.__file__), "customize_erpnext", "doctype", "entonnoir_relances", "entonnoir_relances.json")
        with open(chemin, encoding="utf-8") as f:
            dt = json.load(f)
        types = {"Date": "date", "Datetime": "datetime(6)", "Int": "int(11) not null default 0", "Code": "longtext"}
        cols = ", ".join("`%s` %s" % (x["fieldname"], types[x["fieldtype"]]) for x in dt["fields"] if x["fieldtype"] in types)
        frappe.db.sql("""create temporary table `tab%s` (name varchar(140) not null primary key, creation datetime(6),
                         modified datetime(6), modified_by varchar(140), owner varchar(140), docstatus int(1) not null default 0,
                         idx int(8) not null default 0, %s, _user_tags text, _comments text, _assign text, _liked_by text)""" % (ER.DOCTYPE, cols))
        self.temporaire = True
        colonnes = [r[0] for r in frappe.db.sql("show columns from `tab%s`" % ER.DOCTYPE)]
        meta = M.Meta(frappe.get_doc(dt))
        vrai_meta, vraies_cols, vrai_ctrl = M.get_meta, frappe.db.get_db_table_columns, DOC.get_controller
        self.patches_table = [
            mock.patch.object(M, "get_meta", side_effect=lambda d, cached=True: meta if d == ER.DOCTYPE else vrai_meta(d, cached)),
            mock.patch.object(frappe.db, "get_db_table_columns", side_effect=lambda t: colonnes if t == "tab" + ER.DOCTYPE else vraies_cols(t)),
            mock.patch.object(DOC, "get_controller", side_effect=lambda d: EntonnoirRelances if d == ER.DOCTYPE else vrai_ctrl(d)),
            mock.patch.object(ER, "_table_prete", return_value=True),
        ]
        for p in self.patches_table:
            p.start()

    def test_noter_un_enregistrement_par_jour(self):
        jour = str(add_days(self.today, -400))             # un jour sans enregistrement
        self.assertTrue(ER.noter("sms", {"envoyes": 12, "exclus_perdu": 2}, jour=jour))
        self.assertTrue(ER.noter("sms", {"envoyes": 3}, jour=jour))
        self.assertTrue(ER.noter("listes", {"liste_normal": 4}, jour=jour))
        self.assertEqual(frappe.db.count(ER.DOCTYPE, {"jour": jour}), 1)
        s = json.loads(frappe.db.get_value(ER.DOCTYPE, jour, "sources"))
        self.assertEqual(s["sms"]["passages"], 2)
        self.assertEqual(s["sms"]["compteurs"], {"envoyes": 15, "exclus_perdu": 2})
        self.assertEqual(s["sms"]["dernier_passage"], {"envoyes": 3})
        self.assertEqual(s["listes"]["compteurs"], {"liste_normal": 4})

    def test_noter_ne_leve_pas_sur_erreur_en_base(self):
        jour = str(add_days(self.today, -401))
        with mock.patch.object(frappe.db, "set_value", side_effect=RuntimeError("verrou")), \
                mock.patch.object(frappe, "log_error") as log:
            self.assertFalse(ER.noter("sms", {"envoyes": 1}, jour=jour))
        log.assert_called_once()
        self.assertFalse(frappe.db.exists(ER.DOCTYPE, jour))     # la ligne créée sous le savepoint est annulée avec lui
        self.assertTrue(ER.noter("sms", {"envoyes": 1}, jour=jour))       # et la transaction du cron continue

    def test_photo_enregistree_et_alerte_une_seule_fois(self):
        r1 = ER.enregistrer_photo()
        self.assertTrue(r1["enregistre"])
        row = frappe.db.get_value(ER.DOCTYPE, r1["jour"], ["nb_signaux", "visites_echues", "indicateurs", "signaux", "photo_le"], as_dict=True)
        self.assertEqual(row.nb_signaux, r1["signaux"])
        self.assertEqual(json.loads(row.indicateurs)["jour"], r1["jour"])
        self.assertIsNotNone(row.photo_le)
        n = len(self.alertes)
        self.assertEqual(n, 1 if r1["nouveaux"] else 0)
        r2 = ER.enregistrer_photo()
        self.assertEqual(r2["nouveaux"], 0)                  # les mêmes signaux : pas de deuxième e-mail
        self.assertEqual(len(self.alertes), n)
        self.assertEqual(frappe.db.count(ER.DOCTYPE, {"jour": r1["jour"]}), 1)

    def test_orange_puis_rouge_envoie_l_email(self):
        # Relecture du 26/11/2026 : même clé « brouillon:<nom> », orange (encore travaillé) puis rouge (immobile) : un e-mail.
        sig = lambda niveau: [{"cle": "brouillon:LIST-ESSAI", "niveau": niveau, "texte": "Brouillon LIST-ESSAI", "lien": "/app"}]
        suite = [sig("orange"), sig("rouge"), sig("rouge")]
        with mock.patch.object(ER, "signaux", side_effect=lambda *a, **k: suite.pop(0)):
            r = [ER.enregistrer_photo()["nouveaux"] for _ in range(3)]
        self.assertEqual(r, [0, 1, 0])
        self.assertEqual(len(self.alertes), 1)
        self.assertIn("1 signal(aux) rouge(s) nouveau(x)", self.alertes[0][0])

    def test_photo_garde_les_compteurs_notes(self):
        self.assertTrue(ER.noter("nuit", {"new_ms_created": 4}))
        ER.enregistrer_photo(alerter=False)
        s = json.loads(frappe.db.get_value(ER.DOCTYPE, str(self.today), "sources") or "{}")
        self.assertGreaterEqual(s["nuit"]["compteurs"]["new_ms_created"], 4)


class TestPage(_SansTrace):
    def test_vue_page(self):
        from customization_app import journaux_relances as JR
        v = JR.entonnoir()
        self.assertEqual(len(v["tendance"]), ER.reglages()["entonnoir_jours_tendance"])
        self.assertEqual(v["tendance"][0]["jour"], str(self.today))
        for cle in ("photo", "signaux", "sources", "table_prete", "reglages", "statuts", "libelles_sources"):
            self.assertIn(cle, v)
        self.assertEqual(v["photo"]["erreurs_calcul"], [])
        json.dumps(v)
        rouges = [s for s in v["signaux"] if s["niveau"] == "rouge"]
        self.assertEqual(v["signaux"][:len(rouges)], rouges)        # rouges d'abord

    def test_page_n_ecrit_aucune_erreur(self):
        # Relecture du 26/11/2026 : une erreur de la règle des gelés ou des passages écrivait un Error Log à chaque
        # ouverture de la page (« Entonnoir relances : … »), que la photo suivante comptait en orange.
        from customization_app import journaux_relances as JR
        if not ER.echeanciers_sans_visite(self.today)["echeanciers"]:
            self.skipTest("aucun échéancier sans visite à venir : la règle des gelés n'est pas appelée")
        with mock.patch.object(ER, "a_prolonger_selon_la_nuit", side_effect=RuntimeError("règle cassée")), \
                mock.patch.object(ER, "_charger_passages", side_effect=RuntimeError("journal illisible")), \
                mock.patch.object(frappe, "log_error") as log:
            v = JR.entonnoir()
        log.assert_not_called()
        texte = " | ".join(v["photo"]["erreurs_calcul"])
        self.assertIn("règle des gelés", texte)
        self.assertIn("passages des crons : journal illisible", texte)
        # la photo du cron, elle, journalise
        with mock.patch.object(ER, "_charger_passages", side_effect=RuntimeError("journal illisible")), \
                mock.patch.object(frappe, "log_error") as log:
            ER.flux(self.today, self.today)
        log.assert_called_once()

    def test_page_refusee_sans_role(self):
        from customization_app import journaux_relances as JR
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                JR.entonnoir()
        finally:
            frappe.set_user("Administrator")


if __name__ == "__main__":
    unittest.main()
