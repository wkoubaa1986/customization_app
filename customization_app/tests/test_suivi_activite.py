"""Suivi d'activité : isolation entre employés, droits du responsable, rôles depuis le réglage,
avancement, pièces, lecture tolérante de l'IA. Vrais documents dans un savepoint (tout est annulé)."""
from __future__ import annotations

import unittest

from customization_app import suivi_activite as SA


class TestIA(unittest.TestCase):
    def test_lecture_tolerante(self):
        p = SA.lire_reponse_ia('```json\n{"titre": "Remplacer la membrane", "description": "D.", '
                               '"etapes": ["a", " ", "b"], "duree_jours": "3"}\n```')
        self.assertEqual((p["titre"], p["etapes"], p["duree_jours"]), ("Remplacer la membrane", ["a", "b"], 3))
        for brut in ("", "null", "[]", "pas du json", '{"etapes": "x", "duree_jours": "beaucoup"}'):
            p = SA.lire_reponse_ia(brut)
            self.assertEqual((p["etapes"], p["duree_jours"]), ([], 0), brut)

    def test_prompt(self):
        system, user = SA.prompt_amelioration("ranger stock", "", "Akram", ["note 1"])
        self.assertIn("JSON", system)
        self.assertIn("ranger stock", user)
        self.assertIn("note 1", user)


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
