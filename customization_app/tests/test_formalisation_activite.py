"""Parcours métier de formalisation. Vraies écritures annulées, fournisseur IA simulé."""
import json
from unittest.mock import patch

import frappe

from customization_app import formalisation_activite as FA
from customization_app import reunion_activite as RA
from customization_app import suivi_activite as SA
from customization_app.tests import test_suivi_activite as base


class TestFormalisation(base.TestAcces):
    def setUp(self):
        super().setUp()
        self._comme(self.ur)

    def proposer(self, reponse=None, **kwargs):
        reponse = reponse or {"titre": "Vérifier le stock", "objectif": "Fiabiliser les quantités",
                              "resultat_attendu": "Liste des écarts", "critere_fin": "Écarts vérifiés", "etapes": []}
        with patch.object(FA, "_appeler_ia", return_value=json.dumps(reponse)) as ia:
            p = FA.proposer(texte="Compter les filtres puis vérifier les écarts", **kwargs)
        self.assertEqual(ia.call_count, 1)
        return p

    def data(self, p, **kwargs):
        return {**p, "employes": [e["name"] for e in p["equipe"]], "description": "Objectif :\n" + p["objectif"],
                "appliquer_titre": 1, "appliquer_description": 1, "appliquer_date": 0, **kwargs}

    def test_proposition_sans_ecriture_et_sans_dates_inventees(self):
        avant = frappe.db.count(SA.DOCTYPE)
        p = self.proposer(mode="repartir", employes=[self.e1, self.e2], reponse={
            "titre": "Compter", "etapes": [{"libelle": "Compter", "responsable": "intrus", "date_prevue": "demain"}]})
        self.assertEqual(frappe.db.count(SA.DOCTYPE), avant)
        self.assertIsNone(p["etapes"][0]["responsable"])
        self.assertIsNone(p["etapes"][0]["date_prevue"])
        self.assertIsNone(p["date_prevue"])
        self.assertTrue(p["alertes"])
        self.assertTrue(p["points_a_preciser"])

    def test_ia_propose_et_enregistre_deux_responsables_sur_une_etape(self):
        p = self.proposer(mode="repartir", employes=[self.e1, self.e2], reponse={
            "titre": "Compter ensemble", "etapes": [{"libelle": "Compter les filtres", "responsables": [self.e1, self.e2]}]})
        self.assertEqual(p["etapes"][0]["responsables"], [self.e1, self.e2])
        r = FA.appliquer(self.data(p))
        doc = frappe.get_doc(SA.DOCTYPE, r["name"])
        self.assertEqual(SA.responsables_etape(doc.etapes[0]), [self.e1, self.e2])
        # Une nouvelle proposition qui omet les responsables ne perd pas le second.
        suivant = self.proposer(name=doc.name, mode="repartir", reponse={"titre": "Compter ensemble", "etapes": [
            {"libelle": "Compter par référence", "source": doc.etapes[0].name}]})
        self.assertEqual(suivant["etapes"][0]["responsables"], [self.e1, self.e2])

    def test_ia_ne_repropose_pas_un_compte_desactive(self):
        name = SA.enregistrer({"titre": "Activité partagée", "employes": [self.e1, self.e2]})["name"]
        frappe.db.set_value("User", self.u2, "enabled", 0)
        p = self.proposer(name=name, mode="repartir", reponse={"titre": "Compter", "etapes": [
            {"libelle": "Compter", "responsables": [self.e1, self.e2]}]})
        self.assertEqual([e["name"] for e in p["equipe"]], [self.e1])
        self.assertEqual(p["etapes"][0]["responsables"], [self.e1])
        r = FA.appliquer(self.data(p))
        doc = frappe.get_doc(SA.DOCTYPE, r["name"])
        self.assertEqual({r.employe for r in doc.affectations}, {self.e1, self.e2})
        self.assertEqual(SA.responsables_etape(doc.etapes[0]), [self.e1])

    def test_creation_partagee_relue_et_reessai_sans_doublon(self):
        p = self.proposer(employes=[self.e1, self.e2])
        data = self.data(p, titre="Titre corrigé par l'humain", description="Résultat corrigé",
                         etapes=[{"libelle": "Compter", "responsable": self.e2, "date_prevue": "2030-01-10"}])
        avant = frappe.db.count(SA.DOCTYPE)
        r = FA.appliquer(data)
        doc = frappe.get_doc(SA.DOCTYPE, r["name"])
        self.assertEqual(doc.titre, data["titre"])
        self.assertEqual(doc.description, data["description"])
        self.assertEqual({a.employe for a in doc.affectations}, {self.e1, self.e2})
        self.assertEqual(doc.etapes[0].responsable, self.e2)
        self.assertIn(p["texte_source"], doc.notes[-1].texte)
        self.assertEqual(FA.appliquer(data), {"name": doc.name, "deja_applique": True})
        self.assertEqual(frappe.db.count(SA.DOCTYPE), avant + 1)

    def test_repartition_conserve_etapes_faites_et_non_selectionnees(self):
        name = SA.enregistrer({"titre": "Inventaire", "employes": [self.e1, self.e2], "etapes": [
            {"libelle": "Préparer", "responsable": self.e1, "date_prevue": "2030-01-08"},
            {"libelle": "Compter", "responsable": self.e2, "date_prevue": "2030-01-09"}, "Vérifier"]})["name"]
        doc = frappe.get_doc(SA.DOCTYPE, name)
        SA.etape(name, "basculer", ligne=doc.etapes[0].name)
        doc.reload()
        fait = doc.etapes[0].as_dict()
        p = self.proposer(name=name, mode="repartir", reponse={"titre": "Inventaire", "etapes": [
            {"source": doc.etapes[1].name, "libelle": "Compter par référence"}]})
        self.assertEqual(p["etapes"][0]["responsable"], self.e2)
        self.assertEqual(p["etapes"][0]["date_prevue"], "2030-01-09")
        p["etapes"].append({"source": doc.etapes[0].name, "libelle": "Tentative de modifier une étape faite", "responsable": self.e2})
        FA.appliquer(self.data(p))
        doc.reload()
        for k in ("name", "libelle", "responsable", "date_prevue", "fait", "fait_le", "fait_par"):
            self.assertEqual(doc.etapes[0].get(k), fait[k], k)
        self.assertEqual([e.libelle for e in doc.etapes], ["Préparer", "Compter par référence", "Vérifier"])

    def test_decision_reunion_complete_activite_et_garde_contexte(self):
        name = SA.enregistrer({"titre": "Inventaire", "description": "Contexte initial", "employes": [self.e1]})["name"]
        reunion = RA.creer("2031-02-01", employes=[self.e1, self.e2])["name"]
        RA.noter(reunion, "Contrôler aussi les filtres réservés aux installations.")
        note = frappe.get_doc(RA.DOCTYPE, reunion).notes[-1]
        p = self.proposer(name=name, reunion=reunion, note=note.name, employes=[self.e2])
        FA.appliquer(self.data(p, appliquer_titre=0, etapes=[{"libelle": "Vérifier les réservations", "responsable": self.e2}]))
        doc = frappe.get_doc(SA.DOCTYPE, name)
        self.assertEqual(doc.titre, "Inventaire")
        self.assertIn("Contexte initial", doc.description)
        self.assertIn("Décision de la réunion", doc.description)
        self.assertIn(reunion, doc.notes[-1].texte)
        reu = frappe.get_doc(RA.DOCTYPE, reunion)
        self.assertEqual(reu.notes[-1].activite, name)
        self.assertIn(name, [a.activite for a in reu.activites_suivies])
        p2 = self.proposer(reunion=reunion, note=note.name)
        self.assertEqual(p2["name"], name)
        with self.assertRaises(frappe.ValidationError):
            FA.appliquer(self.data(p2, name=None))

    def test_note_modifiee_et_activite_modifiee_refusees(self):
        name = SA.enregistrer({"titre": "Inventaire", "employes": [self.e1]})["name"]
        p = self.proposer(name=name)
        SA.ajouter_note(name, "Travail fait par un collègue pendant la relecture")
        with self.assertRaises(frappe.TimestampMismatchError):
            FA.appliquer(self.data(p))
        self.assertEqual(frappe.db.get_value(SA.DOCTYPE, name, "titre"), "Inventaire")
        reunion = RA.creer("2031-02-02", employes=[self.e1])["name"]
        RA.noter(reunion, "Ancienne décision")
        reu = frappe.get_doc(RA.DOCTYPE, reunion)
        p = self.proposer(reunion=reunion, note=reu.notes[-1].name, employes=[self.e1])
        reu.notes[-1].texte = "Décision corrigée"
        reu.save()
        with self.assertRaises(frappe.ValidationError):
            FA.appliquer(self.data(p))

    def test_employe_ne_peut_ni_reaffecter_ni_formaliser_reunion(self):
        autre = SA.enregistrer({"titre": "Activité privée", "employes": [self.e2]})["name"]
        reunion = RA.creer("2031-02-03", employes=[self.e1])["name"]
        self._comme(self.u1)
        with self.assertRaises(frappe.PermissionError):
            self.proposer(name=autre)
        with self.assertRaises(frappe.PermissionError):
            self.proposer(reunion=reunion)
        p = self.proposer()
        with self.assertRaises(frappe.PermissionError):
            FA.appliquer(self.data(p, employes=[self.e2]))
        result = FA.appliquer(self.data(p))
        self.assertEqual([r.employe for r in frappe.get_doc(SA.DOCTYPE, result["name"]).affectations], [self.e1])

    def test_erreur_de_date_ou_responsable_ne_cree_rien(self):
        p = self.proposer(employes=[self.e1])
        avant = frappe.db.count(SA.DOCTYPE)
        for etape in ({"libelle": "A", "responsable": self.e2},
                      {"libelle": "A", "date_prevue": "hier"},
                      {"libelle": "A", "date_prevue": "2000-01-01"}):
            with self.assertRaises(frappe.ValidationError):
                FA.appliquer(self.data(p, etapes=[etape]))
        self.assertEqual(frappe.db.count(SA.DOCTYPE), avant)
        self.assertFalse(frappe.db.exists("Comment", FA._operation_name(p["operation_id"])))

    def test_echec_reunion_annule_creation_activite(self):
        reunion = RA.creer("2031-02-04", employes=[self.e1])["name"]
        p = self.proposer(reunion=reunion, employes=[self.e1])
        avant = frappe.db.count(SA.DOCTYPE)
        with patch.object(type(frappe.get_doc(RA.DOCTYPE, reunion)), "save", side_effect=frappe.ValidationError("échec simulé")):
            with self.assertRaises(frappe.ValidationError):
                FA.appliquer(self.data(p))
        self.assertEqual(frappe.db.count(SA.DOCTYPE), avant)

    def test_verification_et_clarification_ne_restructurent_pas(self):
        for mode in ("clarifier", "verifier"):
            p = self.proposer(mode=mode, employes=[self.e1], reponse={"titre": "Vérifier", "etapes": [{"libelle": "Étape inattendue"}]})
            self.assertEqual(p["etapes"], [])
        with self.assertRaises(frappe.ValidationError):
            FA.appliquer(self.data(p))

    def test_synthese_autosauvegardee_sans_ecraser_un_autre(self):
        reunion = RA.creer("2031-02-05", employes=[self.e1])["name"]
        RA.enregistrer_synthese(reunion, "Première synthèse", precedente="")
        RA.noter(reunion, "Une note ajoutée pendant la rédaction")
        RA.enregistrer_synthese(reunion, "Synthèse complétée", precedente="Première synthèse")
        with self.assertRaises(frappe.TimestampMismatchError):
            RA.enregistrer_synthese(reunion, "Ancien brouillon", precedente="Première synthèse")
        self.assertEqual(frappe.db.get_value(RA.DOCTYPE, reunion, "compte_rendu"), "Synthèse complétée")

    def test_decision_simple_conserve_texte_complet(self):
        reunion = RA.creer("2031-02-06", employes=[self.e1])["name"]
        texte = "Préparer la liste des filtres. " * 12
        result = RA.noter(reunion, texte, employes=[self.e1])
        self.assertIn(texte.strip(), frappe.db.get_value(SA.DOCTYPE, result["activite"], "description"))

    def test_clarification_ne_change_pas_un_planning_deja_incoherent(self):
        name = SA.enregistrer({"titre": "Inventaire", "employes": [self.e1], "date_prevue": "2030-01-10",
                               "etapes": [{"libelle": "Compter", "date_prevue": "2030-01-12"}]})["name"]
        p = self.proposer(name=name)
        FA.appliquer(self.data(p))
        doc = frappe.get_doc(SA.DOCTYPE, name)
        self.assertEqual(str(doc.date_prevue), "2030-01-10")
        self.assertEqual(str(doc.etapes[0].date_prevue), "2030-01-12")

    def test_activite_terminee_doit_etre_rouverte_avant_nouvelles_etapes(self):
        name = SA.enregistrer({"titre": "Inventaire", "employes": [self.e1], "statut": "Terminée"})["name"]
        p = self.proposer(name=name, mode="repartir")
        with self.assertRaises(frappe.ValidationError):
            FA.appliquer(self.data(p, etapes=[{"libelle": "Travail supplémentaire"}]))
        self.assertEqual(frappe.db.get_value(SA.DOCTYPE, name, "statut"), "Terminée")
