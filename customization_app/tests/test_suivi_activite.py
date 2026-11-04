"""Suivi d'activité : isolation entre employés, droits du responsable, rôles depuis le réglage,
avancement, pièces, lecture tolérante de l'IA. Vrais documents dans un savepoint (tout est annulé)."""
from __future__ import annotations

import unittest

from customization_app import suivi_activite as SA


class TestIA(unittest.TestCase):
    def test_lecture_tolerante(self):
        # Ancien format (chaînes) toujours lu, sans jour.
        p = SA.lire_reponse_ia('```json\n{"titre": "Remplacer la membrane", "description": "D.", '
                               '"etapes": ["a", " ", "b"], "duree_jours": "3"}\n```')
        self.assertEqual((p["titre"], [e["libelle"] for e in p["etapes"]], p["duree_jours"]), ("Remplacer la membrane", ["a", "b"], 3))
        # Nouveau format : étapes datées par jour ouvré ; duree_jours = au moins le dernier jour.
        p = SA.lire_reponse_ia('{"titre": "T", "etapes": [{"etape": "Relever", "jour": 1, "responsable": "Akram", "source": 2}, '
                               '{"etape": "Commander", "jour": "3", "responsable": null}, {"etape": "Poser", "jour": "x", "source": "z"}], "duree_jours": 1}')
        self.assertEqual([(e["libelle"], e["jour"]) for e in p["etapes"]], [("Relever", 1), ("Commander", 3), ("Poser", 0)])
        self.assertEqual([(e["responsable_nom"], e["source"]) for e in p["etapes"]], [("Akram", 2), (None, 0), (None, 0)])
        self.assertEqual(p["duree_jours"], 3)
        for brut in ("", "null", "[]", "pas du json", '{"etapes": "x", "duree_jours": "beaucoup"}'):
            p = SA.lire_reponse_ia(brut)
            self.assertEqual((p["etapes"], p["duree_jours"]), ([], 0), brut)

    def test_etapes_datees_en_jours_ouvres(self):
        # Samedi 24/10/2026 + 1 jour ouvré = lundi 26 (dimanche chômé).
        self.assertEqual(str(SA.ajouter_jours_ouvres("2026-10-24", 1)), "2026-10-26")
        self.assertEqual(str(SA.ajouter_jours_ouvres("2026-10-19", 0)), "2026-10-19")
        etapes = SA.dater_etapes([{"libelle": "a", "jour": 1}, {"libelle": "b", "jour": 0}, {"libelle": "c", "jour": 7},
                                  {"libelle": "d", "jour": 2}], "2026-10-19")           # lundi
        self.assertEqual([e["date_prevue"] for e in etapes], ["2026-10-19", "2026-10-19", "2026-10-26", "2026-10-26"])
        self.assertEqual([e["jour"] for e in etapes], [1, 1, 7, 7])                     # jamais de date qui recule

    def test_prompt(self):
        system, user = SA.prompt_amelioration("ranger stock", "", "Akram", ["note 1"], "2026-10-19",
                                              [{"libelle": "Vider", "fait": 1, "responsable_nom": "Akram"}, {"libelle": "Ranger"}], ["Akram", "Sadok"])
        self.assertIn("JSON", system)
        self.assertIn("RESTRUCTURE", system)
        self.assertIn("1. Vider [FAITE] [Akram]", user)
        self.assertIn("Akram | Sadok", user)
        self.assertIn('"jour"', system)
        self.assertIn("ranger stock", user)
        self.assertIn("note 1", user)
        self.assertIn("19/10/2026 (jour 1)", user)


class TestAcces(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("suivi")
        emps = frappe.get_all("Employee", filters={"status": "Active", "user_id": ["is", "set"]},
                              fields=["name", "user_id"], order_by="name", limit=3)
        if len(emps) < 3:
            self.skipTest("il faut 3 employés actifs avec compte")
        (self.e1, self.u1), (self.e2, self.u2), (self.er, self.ur) = [(e.name, e.user_id) for e in emps]
        self._config([(self.e1, 0), (self.e2, 0), (self.er, 1)])

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="suivi")
        frappe.clear_cache()

    def _config(self, lignes):
        import frappe
        frappe.set_user("Administrator")
        cfg = frappe.get_doc(SA.DOCTYPE_CONFIG)
        cfg.set("employes", [{"employee": e, "responsable": r} for e, r in lignes])
        cfg.save(ignore_permissions=True)

    def _comme(self, user):
        import frappe
        frappe.set_user(user)

    def test_roles_synchronises_par_le_reglage(self):
        import frappe
        self.assertIn(SA.ROLE_EMPLOYE, frappe.get_roles(self.u1))
        self.assertNotIn(SA.ROLE_RESPONSABLE, frappe.get_roles(self.u1))
        self.assertIn(SA.ROLE_RESPONSABLE, frappe.get_roles(self.ur))
        self._config([(self.e2, 0), (self.er, 1)])          # e1 retiré
        frappe.clear_cache(user=self.u1)
        self.assertNotIn(SA.ROLE_EMPLOYE, frappe.get_roles(self.u1))
        self._comme(self.u1)
        with self.assertRaises(frappe.PermissionError):
            SA.get_activites()

    def test_chacun_ne_voit_que_ses_activites(self):
        import frappe
        self._comme(self.u1)
        a1 = SA.enregistrer({"titre": "Activité de e1", "date_prevue": "2030-01-10"})["name"]
        # Un employé qui tente d'affecter d'autres : ignoré, l'activité est pour lui seul.
        x = SA.enregistrer({"titre": "Pour d'autres", "employes": [self.e2, self.er]})["name"]
        self.assertEqual([r.employe for r in frappe.get_doc(SA.DOCTYPE, x).affectations], [self.e1])
        self._comme(self.u2)
        a2 = SA.enregistrer({"titre": "Activité de e2"})["name"]
        noms = [a.name for a in SA.get_activites()["activites"]]
        self.assertIn(a2, noms)
        self.assertNotIn(a1, noms)
        self.assertNotIn(a1, frappe.get_list(SA.DOCTYPE, pluck="name"))     # la vue liste aussi
        with self.assertRaises(frappe.PermissionError):
            SA.get_activite(a1)
        with self.assertRaises(frappe.PermissionError):
            SA.ajouter_note(a1, "intrusion")
        with self.assertRaises(frappe.PermissionError):
            SA.supprimer(a2)                                                   # un employé ne supprime pas
        # Le responsable voit tout, change l'affectation et supprime.
        self._comme(self.ur)
        noms = [a.name for a in SA.get_activites()["activites"]]
        self.assertTrue({a1, a2} <= set(noms))
        SA.enregistrer({"name": a1, "employes": [self.e2]})
        self.assertEqual(frappe.db.get_value(SA.DOCTYPE, a1, "noms_employes"), frappe.db.get_value("Employee", self.e2, "employee_name"))
        SA.supprimer(a1)
        self.assertFalse(frappe.db.exists(SA.DOCTYPE, a1))

    def test_activite_partagee_entre_plusieurs_employes(self):
        import frappe
        self._comme(self.ur)
        with self.assertRaises(frappe.ValidationError):
            SA.enregistrer({"titre": "Sans personne", "employes": []})
        a = SA.enregistrer({"titre": "Installation à deux", "employes": [self.e1, self.e2, self.e1]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, a)
        self.assertEqual([r.employe for r in doc.affectations], [self.e1, self.e2])     # doublon retiré
        self.assertEqual([r.utilisateur for r in doc.affectations], [self.u1, self.u2])
        ligne = {x.name: x for x in SA.get_activites(employe=self.e2)["activites"]}[a]
        self.assertEqual([e["employe"] for e in ligne.employes], [self.e1, self.e2])
        self.assertNotIn(a, [x.name for x in SA.get_activites(employe=self.er)["activites"]])
        # Les deux la voient et y travaillent : une note de e1 est lue par e2, e2 la termine.
        self._comme(self.u1)
        SA.ajouter_note(a, "J'ai pris le matériel")
        self._comme(self.u2)
        self.assertIn(a, [x.name for x in SA.get_activites()["activites"]])
        self.assertEqual(SA.get_activite(a)["notes"][0]["texte"], "J'ai pris le matériel")
        SA.changer_statut(a, "Terminée")
        self._comme(self.u1)
        self.assertEqual(SA.get_activite(a)["statut"], "Terminée")
        # Un employé affecté ne peut pas retirer son collègue.
        doc = frappe.get_doc(SA.DOCTYPE, a)
        doc.set("affectations", [{"employe": self.e1}])
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_employe_ne_reaffecte_pas(self):
        import frappe
        self._comme(self.u1)
        a = SA.enregistrer({"titre": "Mienne"})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, a)
        doc.append("affectations", {"employe": self.e2})
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_avancement_statut_et_dates(self):
        import frappe
        self._comme(self.u1)
        a = SA.enregistrer({"titre": "Avec étapes", "etapes": ["un", "deux"]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, a)
        self.assertEqual((doc.statut, doc.avancement), ("À faire", 0))
        r = SA.etape(a, "basculer", ligne=doc.etapes[0].name)
        self.assertEqual((r["avancement"], r["statut"]), (50, "En cours"))
        SA.etape(a, "supprimer", ligne=doc.etapes[1].name)
        SA.etape(a, "ajouter", libelle="trois")
        doc.reload()
        self.assertEqual([e.libelle for e in doc.etapes], ["un", "trois"])
        SA.changer_statut(a, "Terminée")
        doc.reload()
        self.assertEqual((doc.avancement, str(doc.date_fin)), (100, frappe.utils.nowdate()))
        SA.changer_statut(a, "En cours")
        doc.reload()
        self.assertIsNone(doc.date_fin)
        with self.assertRaises(frappe.ValidationError):
            SA.enregistrer({"name": a, "date_debut": "2030-02-10", "date_prevue": "2030-02-01"})

    def test_pieces_et_notes(self):
        import frappe
        self._comme(self.u1)
        a = SA.enregistrer({"titre": "Pièces"})["name"]
        SA.ajouter_fichier(a, "/private/files/chantier.JPG")
        SA.ajouter_fichier(a, "/private/files/devis.pdf", "Devis fournisseur")
        self.assertTrue(SA.ajouter_fichier(a, "/private/files/devis.pdf")["deja"])
        SA.ajouter_note(a, "Commencé ce matin")
        d = SA.get_activite(a)
        self.assertEqual([f["type_fichier"] for f in d["fichiers"]], ["Photo", "Document"])
        self.assertEqual(d["notes"][0]["auteur"], self.u1)
        liste = {x.name: x for x in SA.get_activites()["activites"]}
        self.assertEqual((liste[a].nb_fichiers, liste[a].nb_notes, liste[a].vignette), (2, 1, "/private/files/chantier.JPG"))
        # Le responsable retire n'importe quelle pièce ; un autre employé n'a même pas accès.
        self._comme(self.ur)
        SA.supprimer_fichier(a, d["fichiers"][1]["name"])
        self.assertEqual(len(frappe.get_doc(SA.DOCTYPE, a).fichiers), 1)

    def test_retard(self):
        import frappe
        self._comme(self.u1)
        a = SA.enregistrer({"titre": "En retard", "date_debut": "2020-01-01", "date_prevue": "2020-01-05"})["name"]
        b = SA.enregistrer({"titre": "Close", "date_debut": "2020-01-01", "date_prevue": "2020-01-05", "statut": "Terminée"})["name"]
        res = SA.get_activites()
        liste = {x.name: x for x in res["activites"]}
        self.assertTrue(liste[a].en_retard)
        self.assertFalse(liste[b].en_retard)
        self.assertGreaterEqual(res["kpis"]["En retard"], 1)


class TestEtapesDatees(TestAcces):
    def test_etape_datee_retard_et_date_activite(self):
        import frappe
        from frappe.utils import add_days, nowdate
        self._comme(self.ur)
        name = SA.enregistrer({"titre": "Inventaire", "employes": [self.e1], "date_debut": add_days(nowdate(), -10),
                               "etapes": [{"libelle": "Zone Hall", "date_prevue": add_days(nowdate(), -2)}, "Étagères"]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, name)
        self.assertEqual([(e.libelle, str(e.date_prevue or "")) for e in doc.etapes], [("Zone Hall", add_days(nowdate(), -2)), ("Étagères", "")])
        self.assertEqual(str(doc.date_prevue), add_days(nowdate(), -2))     # l'activité prend la dernière date d'étape
        SA.etape(name, "dater", ligne=doc.etapes[1].name, date=add_days(nowdate(), 3))
        SA.etape(name, "ajouter", libelle="Compter", date=add_days(nowdate(), 5))
        rows = {r["name"]: r for r in SA.get_activites()["activites"]}
        self.assertEqual(rows[name]["etapes_retard"], 1)
        self.assertEqual(rows[name]["prochaine"]["libelle"], "Zone Hall")
        SA.etape(name, "basculer", ligne=doc.etapes[0].name)
        rows = {r["name"]: r for r in SA.get_activites()["activites"]}
        self.assertEqual(rows[name]["etapes_retard"], 0)
        self.assertEqual(rows[name]["prochaine"]["libelle"], "Étagères")


class TestReunions(TestAcces):
    def setUp(self):
        super().setUp()
        import frappe
        from customization_app import reunion_activite as RA
        self.RA = RA
        cfg = frappe.get_doc(SA.DOCTYPE_CONFIG)
        cfg.update({"reunion_active": 1, "reunion_jour": "Mercredi", "reunion_toutes_les": 2, "reunion_premiere_date": "2026-10-07",
                    "reunion_heure": "09:00:00", "reunion_duree": 45, "reunion_lieu": "Magasin", "reunion_visio": 1,
                    "reunion_creer_jours_avant": 7, "reunion_rappel_sms": 0})
        cfg.save(ignore_permissions=True)

    def test_config_et_prochaine_date(self):
        cfg = self.RA.config()
        self.assertEqual((cfg["reunion_jour"], cfg["reunion_toutes_les"], cfg["reunion_heure"]), ("Mercredi", 2, "09:00:00"))
        # Repère 07/10 (mercredi), toutes les 2 semaines : 07/10, 21/10, 04/11...
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-01")), "2026-10-07")
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-07")), "2026-10-07")
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-08")), "2026-10-21")
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-21")), "2026-10-21")
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-22")), "2026-11-04")
        # Sans repère : la dernière réunion + 2 semaines, sinon le prochain mercredi.
        cfg["reunion_premiere_date"] = None
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-22", derniere="2026-10-21")), "2026-11-04")
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-22")), "2026-10-28")
        # Repère un lundi alors que le jour voulu est mercredi : aligné sur le mercredi suivant.
        cfg["reunion_premiere_date"] = "2026-10-05"
        self.assertEqual(str(self.RA.prochaine_date(cfg, "2026-10-01")), "2026-10-07")

    def test_cron_cree_la_prochaine_une_seule_fois(self):
        import frappe
        from frappe.utils import add_days, nowdate
        # Repère = aujourd'hui + 3 jours (dans la fenêtre de 7 jours) sur le jour voulu.
        d = add_days(nowdate(), 3)
        frappe.db.set_single_value(SA.DOCTYPE_CONFIG, "reunion_premiere_date", d)
        frappe.db.set_single_value(SA.DOCTYPE_CONFIG, "reunion_jour", self.RA.JOURS[frappe.utils.getdate(d).weekday()])
        name = self.RA.cron_creer_prochaine()
        self.assertTrue(name)
        doc = frappe.get_doc(self.RA.DOCTYPE, name)
        self.assertEqual((str(doc.date), str(doc.heure), doc.statut, doc.creee_par_cron), (d, "9:00:00", "Prévue", 1))
        self.assertEqual({p.employe for p in doc.participants}, {self.e1, self.e2, self.er})
        self.assertTrue(doc.lien_visio.startswith("https://meet.jit.si/aquaworld-reunion-%s-" % d))
        self.assertIsNone(self.RA.cron_creer_prochaine())            # déjà créée : rien de plus
        # Trop loin : rien.
        frappe.db.set_single_value(SA.DOCTYPE_CONFIG, "reunion_premiere_date", add_days(nowdate(), 20))
        frappe.db.set_single_value(SA.DOCTYPE_CONFIG, "reunion_jour", self.RA.JOURS[frappe.utils.getdate(add_days(nowdate(), 20)).weekday()])
        self.assertIsNone(self.RA.cron_creer_prochaine())

    def test_decision_devient_activite_et_ordre_du_jour(self):
        import frappe
        from frappe.utils import add_days, nowdate
        self._comme(self.ur)
        # Une activité en retard de e1, une terminée hier de e2, une réunion précédente tenue il y a 14 jours.
        SA.enregistrer({"titre": "Dashboard Aramex", "employes": [self.e1], "date_debut": add_days(nowdate(), -10),
                        "date_prevue": add_days(nowdate(), -5)})
        fini = SA.enregistrer({"titre": "Stock installateurs", "employes": [self.e2]})["name"]
        SA.changer_statut(fini, "Terminée")
        prec = self.RA.creer_reunion(add_days(nowdate(), -14))
        self.RA.noter(prec.name, "Finir l'inventaire", employes=[self.e1, self.e2], date_prevue=add_days(nowdate(), 7))
        frappe.db.set_value(SA.DOCTYPE, frappe.get_doc(self.RA.DOCTYPE, prec.name).notes[0].activite,
                            {"date_debut": add_days(nowdate(), -14), "date_prevue": add_days(nowdate(), -1)})
        self.RA.changer_statut(prec.name, "Tenue")
        reu = self.RA.creer_reunion(nowdate())
        self.assertEqual(reu.precedente, prec.name)
        d = self.RA.get_reunion(reu.name)
        odj = d["ordre_du_jour"]
        par = {e["employe"]: e for e in odj["employes"]}
        # Les employés de test portent aussi leurs vraies activités de la base : on regarde celles du test.
        titres = [a["titre"] for a in par[self.e1]["ouvertes"]]
        self.assertLess(titres.index("Dashboard Aramex"), titres.index("Finir l'inventaire"))   # retard le plus grand d'abord
        self.assertEqual(next(a for a in par[self.e1]["ouvertes"] if a["titre"] == "Dashboard Aramex")["retard_jours"], 5)
        self.assertIn("Stock installateurs", [a["titre"] for a in par[self.e2]["terminees"]])
        self.assertGreaterEqual(odj["kpis"]["retard"], 2)
        self.assertGreaterEqual(odj["kpis"]["terminees"], 1)
        self.assertEqual([(x["texte"], x["en_retard"]) for x in odj["decisions_precedente"]], [("Finir l'inventaire", True)])
        # Une décision prise maintenant : activité créée pour e2, datée, visible dans sa liste.
        r = self.RA.noter(reu.name, "Compléter le catalogue", employes=[self.e2], date_prevue=add_days(nowdate(), 7))
        act = frappe.get_doc(SA.DOCTYPE, r["activite"])
        self.assertEqual(([a.employe for a in act.affectations], str(act.date_prevue), act.statut), ([self.e2], add_days(nowdate(), 7), "À faire"))
        self.assertIn(reu.name, act.description)
        self._comme(self.u2)
        self.assertIn(act.name, [x["name"] for x in SA.get_activites()["activites"]])
        # L'employé voit la réunion (participant), marque SA présence seulement, note sans décision.
        d2 = self.RA.get_reunion(reu.name)
        self.assertFalse(d2["peut_gerer"])
        self.RA.presence(reu.name, self.e2, "Présent")
        with self.assertRaises(frappe.PermissionError):
            self.RA.presence(reu.name, self.e1, "Présent")
        with self.assertRaises(frappe.PermissionError):
            self.RA.noter(reu.name, "x", employes=[self.e1])
        self.RA.noter(reu.name, "Photos des filtres manquantes")
        from unittest.mock import patch
        # L'horaire du test est fixé avant la réunion, quel que soit le moment où la suite tourne.
        with patch.object(self.RA, "now_datetime", return_value=frappe.utils.get_datetime(nowdate() + " 08:00:00")):
            liste = self.RA.get_reunions()                   # (d'autres réunions réelles de la base peuvent s'y trouver)
        mienne = next(x for x in liste["a_venir"] if x["name"] == reu.name)
        self.assertEqual(mienne["nb_decisions"], 1)
        self.assertFalse(liste["peut_gerer"])

    def test_etat_horaire_limites_et_reunion_qui_passe_minuit(self):
        from datetime import timedelta
        r = {"date": "2031-02-01", "heure": timedelta(hours=23, minutes=30), "duree_min": 90, "statut": "Prévue"}
        for maintenant, attendu in [("2031-02-01 23:29:59", "Prévue"), ("2031-02-01 23:30:00", "En cours"),
                                    ("2031-02-02 00:59:59", "En cours"), ("2031-02-02 01:00:00", "Terminée")]:
            self.assertEqual(self.RA.etat_horaire(r, maintenant)["statut_affiche"], attendu)
        for statut, attendu in [("Annulée", "Annulée"), ("Tenue", "Terminée")]:
            r["statut"] = statut
            self.assertEqual(self.RA.etat_horaire(r, "2031-02-01 20:00:00")["statut_affiche"], attendu)

    def test_ancienne_reunion_classee_terminee_sans_cloturer_compte_rendu(self):
        import frappe
        from unittest.mock import patch
        from frappe.utils import get_datetime
        self._comme(self.ur)
        finie = self.RA.creer_reunion("2031-02-01", heure="09:00:00", employes=[self.e1])
        encours = self.RA.creer_reunion("2031-02-01", heure="10:00:00", employes=[self.e1])
        future = self.RA.creer_reunion("2031-02-01", heure="14:00:00", employes=[self.e1])
        annulee = self.RA.creer_reunion("2031-02-01", heure="15:00:00", employes=[self.e1])
        self.RA.changer_statut(annulee.name, "Annulée")
        self._comme(self.u1)
        with patch.object(self.RA, "now_datetime", return_value=get_datetime("2031-02-01 10:15:00")):
            liste = self.RA.get_reunions()
            ouvertes = {r.name: r.statut_affiche for r in liste["a_venir"]}
            passees = {r.name: r.statut_affiche for r in liste["passees"]}
            self.assertNotIn(finie.name, ouvertes)
            self.assertEqual(ouvertes[encours.name], "En cours")
            self.assertEqual(ouvertes[future.name], "Prévue")
            self.assertEqual(passees[finie.name], "Terminée")
            self.assertEqual(passees[annulee.name], "Annulée")
            self.assertEqual(self.RA.get_reunion(finie.name)["statut_affiche"], "Terminée")
            self._comme(self.u2)
            autres = self.RA.get_reunions()
            self.assertNotIn(finie.name, {r.name for r in autres["a_venir"] + autres["passees"]})
        finie.reload()
        self.assertEqual(finie.statut, "Prévue")
        self.assertIsNone(finie.tenue_le)
        self._comme(self.ur)
        self.RA.noter(finie.name, "Compte rendu complété après la réunion")
        self.assertEqual(frappe.get_doc(self.RA.DOCTYPE, finie.name).notes[-1].texte, "Compte rendu complété après la réunion")
        # Une réunion déplacée dans le futur revient dans la liste à venir.
        self.RA.deplacer(finie.name, "2031-02-02", "09:00:00")
        with patch.object(self.RA, "now_datetime", return_value=get_datetime("2031-02-01 10:15:00")):
            self.assertIn(finie.name, {r.name for r in self.RA.get_reunions()["a_venir"]})

    def test_calendrier_marque_une_reunion_passee_terminee(self):
        import frappe
        from unittest.mock import patch
        from customization_app.api import _ajouter_reunions
        self._comme(self.ur)
        reu = self.RA.creer_reunion("2031-02-03", heure="09:00:00", employes=[self.e1])
        events = []
        with patch("frappe.utils.now_datetime", return_value=frappe.utils.get_datetime("2031-02-03 10:00:00")):
            _ajouter_reunions(events, "2031-02-03", "2031-02-03", [])
        event = next(e for e in events if e.name == reu.name)
        self.assertEqual((event.status, event.color), ("Completed", "#22c55e"))

    def test_rappel_sans_sms(self):
        import frappe
        from frappe.utils import nowdate
        reu = self.RA.creer_reunion(nowdate())
        bilan = self.RA.rappeler(reu, "jour")
        self.assertEqual((bilan["notifies"], bilan["sms"]), (3, 0))
        self.assertEqual(frappe.db.count("Notification Log", {"document_name": reu.name}), 3)
        reu = frappe.get_doc(self.RA.DOCTYPE, reu.name)                  # heure relue en base = timedelta « 9:00:00 »
        self.assertEqual(self.RA.heure_hm(reu.heure), "09:00")
        texte = self.RA.texte_rappel(reu, "veille")
        self.assertIn("demain a 09:00", texte)
        self.assertIn(reu.lien_visio, texte)

    def test_reunions_par_equipe_ou_individuelles(self):
        import frappe
        from frappe.utils import add_days, nowdate
        self._comme(self.ur)
        # Un point individuel avec e1, un point à deux ; la précédente est celle des MÊMES participants.
        seul = self.RA.creer(add_days(nowdate(), -7), employes=[self.e1])["name"]
        duo = self.RA.creer(add_days(nowdate(), -7), employes=[self.e1, self.e2])["name"]     # même jour, équipe différente : OK
        with self.assertRaises(frappe.ValidationError):
            self.RA.creer(add_days(nowdate(), -7), employes=[self.e2, self.e1])               # mêmes participants : refusée
        self.RA.changer_statut(seul, "Tenue"); self.RA.changer_statut(duo, "Tenue")
        d_seul = frappe.get_doc(self.RA.DOCTYPE, seul)
        self.assertTrue(d_seul.titre.startswith("Point avec "))
        self.assertEqual([p.employe for p in d_seul.participants], [self.e1])
        suivant = self.RA.creer(nowdate(), employes=[self.e1], titre="Point Sadok", lieu="Bureau")["name"]
        d = frappe.get_doc(self.RA.DOCTYPE, suivant)
        self.assertEqual((d.precedente, d.titre, d.lieu), (seul, "Point Sadok", "Bureau"))
        # L'ordre du jour ne parle que de e1.
        odj = self.RA.get_reunion(suivant)["ordre_du_jour"]
        self.assertEqual([e["employe"] for e in odj["employes"]], [self.e1])
        # Changer les participants recalcule la précédente.
        self.RA.modifier(suivant, {"employes": [self.e1, self.e2]})
        self.assertEqual(frappe.db.get_value(self.RA.DOCTYPE, suivant, "precedente"), duo)
        # Un employé non participant ne voit pas le point individuel.
        self._comme(self.u2)
        with self.assertRaises(frappe.PermissionError):
            self.RA.get_reunion(seul)

    def test_serie_et_activites_suivies(self):
        import frappe
        from frappe.utils import add_days, nowdate
        self._comme(self.ur)
        a1 = SA.enregistrer({"titre": "Catalogue produits", "employes": [self.e1]})["name"]
        a2 = SA.enregistrer({"titre": "Inventaire magasin", "employes": [self.e1, self.e2]})["name"]
        SA.enregistrer({"titre": "Autre chose de e1", "employes": [self.e1]})
        ouvertes = self.RA.activites_de([self.e1])
        self.assertTrue({a1, a2} <= {x["name"] for x in ouvertes})
        # Série : toutes les 2 semaines, 5 semaines → 3 réunions, chacune précédente de la suivante, un lien chacune.
        precedente = self.RA._derniere_avec({self.e1}, nowdate())
        r = self.RA.creer(nowdate(), employes=[self.e1], toutes_les=2, jusquau=add_days(nowdate(), 35), activites=[a1, a2])
        self.assertEqual(len(r["noms"]), 3)
        docs = [frappe.get_doc(self.RA.DOCTYPE, n) for n in r["noms"]]
        self.assertEqual([str(d.date) for d in docs], [nowdate(), add_days(nowdate(), 14), add_days(nowdate(), 28)])
        self.assertEqual(len({d.serie for d in docs}), 1)
        self.assertEqual(len({d.lien_visio for d in docs}), 3)
        self.assertEqual([d.precedente for d in docs], [precedente.name if precedente else None, docs[0].name, docs[1].name])
        self.assertEqual([x.activite for x in docs[0].activites_suivies], [a1, a2])
        self.assertEqual(docs[0].seulement_suivies, 1)
        # L'ordre du jour ne parle que des activités suivies.
        odj = self.RA.get_reunion(docs[0].name)["ordre_du_jour"]
        self.assertEqual(odj["suivies"], [a1, a2])
        self.assertEqual({x["name"] for x in odj["employes"][0]["ouvertes"]}, {a1, a2})
        # Ajouter une activité d'un NON-participant (e2 seul) sans restreindre : elle apparaît sous « Autres ».
        a3 = SA.enregistrer({"titre": "Activité de e2", "employes": [self.e2]})["name"]
        self.RA.modifier(docs[0].name, {"activites": [a1, a2, a3], "seulement_suivies": 0})
        odj = self.RA.get_reunion(docs[0].name)["ordre_du_jour"]
        self.assertTrue({x["name"] for x in odj["employes"][0]["ouvertes"]} >= {a1, a2})     # e1 retrouve toutes les siennes
        autres = [g for g in odj["employes"] if g["employe"] is None]
        self.assertEqual([x["name"] for x in autres[0]["ouvertes"]], [a3])
        tous = self.RA.activites_de([self.e1], toutes=1)
        self.assertEqual([x["des_participants"] for x in tous][:1], [1])
        self.assertIn(a3, [x["name"] for x in tous if not x["des_participants"]])
        self.assertEqual(len(self.RA.get_reunion(docs[0].name)["serie_reunions"]), 3)
        # Annuler à partir de la 2e : la 1re reste prévue.
        self.RA.annuler_serie_suivantes(docs[1].name)
        self.assertEqual([frappe.db.get_value(self.RA.DOCTYPE, d.name, "statut") for d in docs], ["Prévue", "Annulée", "Annulée"])
        with self.assertRaises(frappe.ValidationError):
            self.RA.creer(nowdate(), employes=[self.e1], toutes_les=1, jusquau=add_days(nowdate(), -1))

    def test_participants_desactives_masques_dans_le_futur_historique_conserve(self):
        import frappe
        from frappe.utils import add_days, nowdate
        future = self.RA.creer_reunion(add_days(nowdate(), 5), employes=[self.e1, self.e2])
        passee = self.RA.creer_reunion(add_days(nowdate(), -5), employes=[self.e1, self.e2])
        frappe.db.set_value("User", self.u1, "enabled", 0)
        for liste, name, attendus in (("a_venir", future.name, {self.e2}), ("passees", passee.name, {self.e1, self.e2})):
            d = self.RA.get_reunion(name)
            self.assertEqual({p.employe for p in d["participants"]}, attendus)
            self.assertEqual({p["employe"] for p in d["ordre_du_jour"]["employes"]}, attendus)
            resume = next(r for r in self.RA.get_reunions()[liste] if r.name == name)
            self.assertEqual(len(resume.participants), len(attendus))
        # La lecture ne réécrit pas les participants enregistrés.
        self.assertEqual(len(frappe.get_doc(self.RA.DOCTYPE, future.name).participants), 2)
        frappe.db.set_value("Employee", self.e2, "status", "Inactive")
        self.assertEqual(self.RA.get_reunion(future.name)["participants"], [])
        self.assertEqual(self.RA.get_reunion(future.name)["ordre_du_jour"]["employes"], [])

    def test_rappel_ignore_compte_desactive(self):
        import frappe
        from frappe.utils import add_days, nowdate
        reu = self.RA.creer_reunion(add_days(nowdate(), 5), employes=[self.e1, self.e2])
        frappe.db.set_value("User", self.u1, "enabled", 0)
        bilan = self.RA.rappeler(reu, "veille", dict(self.RA.config(), reunion_rappel_sms=0))
        self.assertEqual(bilan["notifies"], 1)
        self.assertEqual(frappe.get_all("Notification Log", filters={"document_name": reu.name}, pluck="for_user"), [self.u2])

    def test_nettoyage_participants_futurs_persistant_et_idempotent(self):
        import frappe
        from frappe.utils import add_days, nowdate
        from customization_app.patches.reunions_participants_actifs import execute
        future = self.RA.creer_reunion(add_days(nowdate(), 5), employes=[self.e1, self.e2])
        seule = self.RA.creer_reunion(add_days(nowdate(), 6), employes=[self.e1])
        passee = self.RA.creer_reunion(add_days(nowdate(), -5), employes=[self.e1, self.e2])
        annulee = self.RA.creer_reunion(add_days(nowdate(), 7), employes=[self.e1, self.e2])
        self.RA.changer_statut(annulee.name, "Annulée")
        self.RA.noter(future.name, "Note à conserver")
        hist = {n: frappe.get_doc(self.RA.DOCTYPE, n).as_dict() for n in (passee.name, annulee.name)}
        frappe.db.set_value("User", self.u1, "enabled", 0)
        resultats = execute()
        self.assertTrue({future.name, seule.name} <= {r["reunion"] for r in resultats})
        corrigee = frappe.get_doc(self.RA.DOCTYPE, future.name)
        self.assertEqual([p.employe for p in corrigee.participants], [self.e2])
        self.assertEqual(corrigee.notes[0].texte, "Note à conserver")
        self.assertEqual(frappe.get_doc(self.RA.DOCTYPE, seule.name).participants, [])
        for n, original in hist.items():
            self.assertEqual(frappe.get_doc(self.RA.DOCTYPE, n).as_dict(), original)
        self.assertEqual(execute(), [])

    def test_sauvegarde_reunion_future_retire_ancienne_affectation_desactivee(self):
        import frappe
        from frappe.utils import add_days, nowdate
        future = self.RA.creer_reunion(add_days(nowdate(), 5), employes=[self.e1, self.e2])
        frappe.db.set_value("User", self.u1, "enabled", 0)
        self.RA.noter(future.name, "Mise à jour après désactivation")
        self.assertEqual([p.employe for p in frappe.get_doc(self.RA.DOCTYPE, future.name).participants], [self.e2])
        with self.assertRaises(frappe.ValidationError):
            self.RA.modifier(future.name, {"employes": [self.e1, self.e2]})

    def test_modifier_serie_suite_seulement_et_preserver_historique(self):
        import frappe
        from frappe.utils import add_days, nowdate
        noms = self.RA.creer_serie(add_days(nowdate(), -7), add_days(nowdate(), 35), 1, employes=[self.e1, self.e2])
        # Inclut une ancienne occurrence encore Prévue, une clôturée et une annulée.
        self.RA.changer_statut(noms[1], "Tenue", "Compte rendu passé")
        self.RA.changer_statut(noms[4], "Annulée")
        avant = {n: frappe.get_doc(self.RA.DOCTYPE, n).as_dict() for n in noms}
        self.RA.noter(noms[3], "Note propre à cette occurrence")
        frappe.db.set_value("User", self.u1, "enabled", 0)
        info = self.RA.get_reunion(noms[2])
        self.assertTrue(info["peut_modifier_serie"])
        self.assertEqual(info["nb_serie_modifiables"], 4)
        a = SA.enregistrer({"titre": "Activité de la nouvelle série", "employes": [self.e2]})["name"]
        resultat = self.RA.modifier(noms[2], {"titre": "Nouveau point", "date": add_days(nowdate(), 8),
            "heure": "15:30:00", "duree_min": 60, "lieu": "Bureau", "employes": [self.e2],
            "activites": [a], "seulement_suivies": 1, "toutes_les_semaines": 2,
            "lien_visio": "https://example.org/ne-pas-copier"}, portee="suivantes", modified=info["modified"])
        selection = [noms[i] for i in (2, 3, 5, 6)]
        self.assertEqual(resultat["modifiees"], selection)
        for i, n in enumerate(selection):
            d = frappe.get_doc(self.RA.DOCTYPE, n)
            self.assertEqual(str(d.date), add_days(nowdate(), 8 + i * 14))
            self.assertEqual((d.titre, str(d.heure), d.lieu, d.duree_min, d.toutes_les_semaines), ("Nouveau point", "15:30:00", "Bureau", 60, 2))
            self.assertEqual([p.employe for p in d.participants], [self.e2])
            self.assertEqual([r.activite for r in d.activites_suivies], [a])
            self.assertEqual((d.lien_visio, d.precedente), (avant[n].lien_visio, avant[n].precedente))
        for n in (noms[0], noms[1], noms[4]):
            self.assertEqual(frappe.get_doc(self.RA.DOCTYPE, n).as_dict(), avant[n])
        self.assertEqual(frappe.get_doc(self.RA.DOCTYPE, noms[3]).notes[0].texte, "Note propre à cette occurrence")

    def test_modifier_une_occurrence_puis_decaler_la_suite(self):
        import frappe
        from frappe.utils import add_days, nowdate
        noms = self.RA.creer_serie(add_days(nowdate(), 10), add_days(nowdate(), 24), 1, employes=[self.e1])
        self.RA.modifier(noms[1], {"date": add_days(nowdate(), 18), "titre": "Exception"})
        self.assertEqual([str(frappe.db.get_value(self.RA.DOCTYPE, n, "date")) for n in noms],
                         [add_days(nowdate(), j) for j in (10, 18, 24)])
        self.RA.modifier(noms[0], {"date": add_days(nowdate(), 12), "toutes_les_semaines": 1}, portee="suivantes")
        # Fréquence inchangée : les exceptions gardent leur décalage.
        self.assertEqual([str(frappe.db.get_value(self.RA.DOCTYPE, n, "date")) for n in noms],
                         [add_days(nowdate(), j) for j in (12, 20, 26)])
        self.assertEqual(frappe.db.get_value(self.RA.DOCTYPE, noms[1], "titre"), "Exception")

    def test_modification_serie_validee_et_reservee_aux_responsables(self):
        import frappe
        from frappe.utils import add_days, nowdate
        noms = self.RA.creer_serie(add_days(nowdate(), -7), add_days(nowdate(), 14), 1, employes=[self.e1])
        with self.assertRaises(frappe.ValidationError):
            self.RA.modifier(noms[0], {"titre": "Depuis le passé"}, portee="suivantes")
        self._comme(self.u1)
        with self.assertRaises(frappe.PermissionError):
            self.RA.modifier(noms[2], {"titre": "Sans droit"}, portee="suivantes")
        self._comme("Administrator")
        for data in ({"toutes_les_semaines": 0}, {"toutes_les_semaines": 1.5}, {"date": add_days(nowdate(), -1)},
                     {"employes": []}, {"compte_rendu": "À ne pas dupliquer"}, {"duree_min": 0}):
            with self.assertRaises(frappe.ValidationError):
                self.RA.modifier(noms[2], data, portee="suivantes")
        vieux = self.RA.get_reunion(noms[2])["modified"]
        self.RA.modifier(noms[2], {"titre": "Déjà modifié"})
        with self.assertRaises(frappe.TimestampMismatchError):
            self.RA.modifier(noms[2], {"titre": "Ancien écran"}, portee="suivantes", modified=vieux)
        frappe.db.set_value("User", self.u1, "enabled", 0)
        with self.assertRaises(frappe.ValidationError):
            self.RA.modifier(noms[2], {"employes": [self.e1]}, portee="suivantes")

    def test_modification_serie_annule_tout_si_une_occurrence_echoue(self):
        import frappe
        from frappe.utils import add_days, nowdate
        from unittest.mock import patch
        noms = self.RA.creer_serie(add_days(nowdate(), 10), add_days(nowdate(), 24), 1, employes=[self.e1])
        titres = [frappe.db.get_value(self.RA.DOCTYPE, n, "titre") for n in noms]
        original = self.RA._modifier_occurrence
        def modifier_ou_echouer(doc, data):
            if doc.name == noms[1]:
                raise RuntimeError("Échec simulé")
            original(doc, data)
        with patch.object(self.RA, "_modifier_occurrence", side_effect=modifier_ou_echouer):
            with self.assertRaises(RuntimeError):
                self.RA.modifier(noms[0], {"titre": "Ne pas garder une série partielle"}, portee="suivantes")
        self.assertEqual([frappe.db.get_value(self.RA.DOCTYPE, n, "titre") for n in noms], titres)


class TestEtapesResponsablesOrdre(TestAcces):
    def test_plusieurs_responsables_et_compatibilite_ancien_appel(self):
        import frappe
        import json
        self._comme(self.ur)
        name = SA.enregistrer({"titre": "Comptage à deux", "employes": [self.e1, self.e2], "etapes": [
            {"libelle": "Compter les filtres", "responsables": [self.e1, self.e2, self.e1], "date_prevue": "2030-01-12"}]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, name)
        ligne = doc.etapes[0].name
        self.assertEqual(SA.responsables_etape(doc.etapes[0]), [self.e1, self.e2])
        self.assertEqual(doc.etapes[0].responsable, self.e1)
        self.assertEqual(SA.get_activite(name)["etapes"][0]["responsables"], [self.e1, self.e2])
        for employee in [self.e1, self.e2]:
            self.assertIn(frappe.db.get_value("Employee", employee, "employee_name"), doc.etapes[0].responsable_nom)
        SA.etape(name, "affecter", ligne=ligne, employes=json.dumps([self.e2, self.e1]))
        doc.reload()
        self.assertEqual(SA.responsables_etape(doc.etapes[0]), [self.e2, self.e1])
        self.assertEqual(str(doc.etapes[0].date_prevue), "2030-01-12")
        SA.etape(name, "affecter", ligne=ligne, employe=self.e1)
        self.assertEqual(SA.get_activite(name)["etapes"][0]["responsables"], [self.e1])
        SA.etape(name, "affecter", ligne=ligne, employes=[])
        self.assertEqual(SA.get_activite(name)["etapes"][0]["responsables"], [])
        SA.etape(name, "ajouter", libelle="Vérifier ensemble", employes=[self.e1, self.e2])
        self.assertEqual(SA.get_activite(name)["etapes"][-1]["responsables"], [self.e1, self.e2])

    def test_reprise_attributions_ancienne_version_idempotente(self):
        import frappe
        from customization_app.patches.etapes_plusieurs_responsables import reprendre_attributions
        self._comme(self.ur)
        name = SA.enregistrer({"titre": "Anciennes étapes", "employes": [self.e1, self.e2], "etapes": [
            {"libelle": "Ancienne", "responsable": self.e1}, {"libelle": "Déjà multiple", "responsables": [self.e1, self.e2]}]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, name)
        frappe.db.set_value("Activite Etape", doc.etapes[0].name, "responsables_json", None)
        doc.reload()
        self.assertEqual(SA.responsables_etape(doc.etapes[0]), [self.e1])
        reprendre_attributions()
        reprendre_attributions()
        doc.reload()
        self.assertEqual([SA.responsables_etape(e) for e in doc.etapes], [[self.e1], [self.e1, self.e2]])

    def test_employes_inactifs_et_comptes_desactives_exclus_des_choix(self):
        import frappe
        from customization_app import reunion_activite as RA
        self._comme(self.ur)
        name = SA.enregistrer({"titre": "Historique conservé", "employes": [self.e1, self.e2], "etapes": [
            {"libelle": "Travail déjà fait", "responsables": [self.e1, self.e2]}]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, name)
        SA.etape(name, "basculer", ligne=doc.etapes[0].name)
        reunion = RA.creer_reunion("2032-01-01", employes=[self.e1, self.e2])
        frappe.db.set_value("User", self.u2, "enabled", 0)
        frappe.db.set_value("Employee", self.e1, "status", "Inactive")
        self.assertEqual({e["name"] for e in SA.get_context()["employes"]}, {self.er})
        d = SA.get_activite(name)
        self.assertTrue(all(not r["actif"] for r in d["affectations"]))
        self.assertEqual({e["name"] for e in d["employes_disponibles"]}, {self.er})
        self.assertEqual(d["etapes"][0]["responsables"], [])
        self.assertEqual(d["etapes"][0]["responsables_historique"], [self.e1, self.e2])
        with self.assertRaises(frappe.ValidationError):
            SA.enregistrer({"titre": "Interdit", "employes": [self.e2]})
        with self.assertRaises(frappe.ValidationError):
            SA.etape(name, "affecter", ligne=doc.etapes[0].name, employes=[self.e2])
        with self.assertRaises(frappe.ValidationError):
            RA.creer_reunion("2032-01-02", employes=[self.e2])
        self.assertTrue(all(not p["actif"] for p in RA.get_reunion(reunion.name)["participants"]))
        SA.enregistrer({"name": name, "description": "Une note modifiée ne supprime pas l'historique"})
        doc.reload()
        self.assertEqual(SA.responsables_etape(doc.etapes[0]), [self.e1, self.e2])
        self.assertTrue(doc.etapes[0].fait)

    def test_responsables_hors_equipe_refuses_par_le_modele(self):
        import frappe
        self._comme(self.ur)
        name = SA.enregistrer({"titre": "Équipe limitée", "employes": [self.e1], "etapes": ["Compter"]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, name)
        doc.etapes[0].update(SA.champs_responsables([self.e1, self.e2]))
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_responsable_ordre_et_restructuration(self):
        import frappe
        self._comme(self.ur)
        name = SA.enregistrer({"titre": "Dashboard", "employes": [self.e1, self.e2],
                               "etapes": [{"libelle": "A", "responsable": self.e1}, "B", {"libelle": "C", "responsable": self.e2}]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, name)
        self.assertEqual([(e.libelle, e.responsable) for e in doc.etapes], [("A", self.e1), ("B", None), ("C", self.e2)])
        self.assertTrue(doc.etapes[0].responsable_nom)
        # affecter / refuser un étranger
        SA.etape(name, "affecter", ligne=doc.etapes[1].name, employe=self.e2)
        with self.assertRaises(frappe.ValidationError):
            SA.etape(name, "affecter", ligne=doc.etapes[1].name, employe=self.er)
        # ordre : C monte deux fois → C A B ; A descend → C B A
        SA.etape(name, "monter", ligne=doc.etapes[2].name); SA.etape(name, "monter", ligne=doc.etapes[2].name)
        SA.etape(name, "monter", ligne=doc.etapes[2].name)                       # déjà en tête : sans effet
        self.assertEqual([e.libelle for e in frappe.get_doc(SA.DOCTYPE, name).etapes], ["C", "A", "B"])
        SA.etape(name, "descendre", ligne=doc.etapes[0].name)
        self.assertEqual([e.libelle for e in frappe.get_doc(SA.DOCTYPE, name).etapes], ["C", "B", "A"])
        # Un employé retiré de l'activité ne reste pas responsable d'une étape.
        SA.enregistrer({"name": name, "employes": [self.e1]})
        self.assertEqual([e.responsable for e in frappe.get_doc(SA.DOCTYPE, name).etapes], [None, None, self.e1])
        # Restructuration par l'IA : C est faite ; la liste est remplacée, l'étape issue de C (source 1) reste faite.
        doc = frappe.get_doc(SA.DOCTYPE, name)
        SA.etape(name, "basculer", ligne=doc.etapes[0].name)
        SA.enregistrer({"name": name, "remplacer_etapes": 1, "etapes": [
            {"libelle": "C reformulée", "source": 1, "date_prevue": "2026-10-20"},
            {"libelle": "A et B regroupées", "source": 3, "responsable": self.e1, "date_prevue": "2026-10-22"},
            {"libelle": "Contrôle final", "source": 0, "date_prevue": "2026-10-23"}]})
        doc = frappe.get_doc(SA.DOCTYPE, name)
        self.assertEqual([(e.libelle, e.fait, e.responsable) for e in doc.etapes],
                         [("C reformulée", 1, None), ("A et B regroupées", 0, self.e1), ("Contrôle final", 0, None)])
        self.assertTrue(doc.etapes[0].fait_le)
        self.assertEqual(doc.avancement, 33)

    def test_calendrier_reunion_visible_par_ses_participants(self):
        import frappe
        from frappe.utils import nowdate
        from customization_app.api import get_custom_tache_events
        from customization_app import reunion_activite as RA
        self._comme(self.ur)
        seul = RA.creer(nowdate(), employes=[self.e1])["name"]
        noms = lambda: [e["name"] for e in get_custom_tache_events(nowdate(), nowdate()) if e.get("doctype") == RA.DOCTYPE]
        self.assertIn(seul, noms())                     # responsable : tout
        self._comme(self.u1)
        self.assertIn(seul, noms())                     # participant : oui
        self._comme(self.u2)
        self.assertNotIn(seul, noms())                  # pas participant : non
        self._comme(self.ur)
        RA.changer_statut(seul, "Tenue")
        ev = next(e for e in get_custom_tache_events(nowdate(), nowdate()) if e.get("name") == seul)
        self.assertEqual((ev["color"], ev["status"]), ("#22c55e", "Completed"))       # clôturée = verte
