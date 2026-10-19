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
        p = SA.lire_reponse_ia('{"titre": "T", "etapes": [{"etape": "Relever", "jour": 1}, {"etape": "Commander", "jour": "3"}, '
                               '{"etape": "Poser", "jour": "x"}], "duree_jours": 1}')
        self.assertEqual([(e["libelle"], e["jour"]) for e in p["etapes"]], [("Relever", 1), ("Commander", 3), ("Poser", 0)])
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
        system, user = SA.prompt_amelioration("ranger stock", "", "Akram", ["note 1"], "2026-10-19")
        self.assertIn("JSON", system)
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
        liste = self.RA.get_reunions()                       # (d'autres réunions réelles de la base peuvent s'y trouver)
        mienne = next(x for x in liste["a_venir"] if x["name"] == reu.name)
        self.assertEqual(mienne["nb_decisions"], 1)
        self.assertFalse(liste["peut_gerer"])

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
