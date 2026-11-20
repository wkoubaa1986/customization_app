"""Vérifications DEV avec MariaDB, données synthétiques annulées, réseau interdit."""
import unittest
from unittest.mock import patch

import frappe

from customization_app import geocodage_adresses as A
from customization_app import tournee_optimisation as T
from customization_app import geocodage_liens as L
from customization_app.tests.test_geocodage_liens import URL, PID, REPONSE
from customization_app.tests.test_geocodage_adresses import candidat


class TestAdresseDocument(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("geocodage_qualite_test")
        self.prefixe = "geocodage_qualite_test:" + frappe.generate_hash(length=12) + ":"
        for p in [patch.object(A, "PREFIXE", self.prefixe),
                  patch.object(A, "reglages", return_value={"actif": True, "nominatim": None}),
                  patch("requests.sessions.Session.request", side_effect=AssertionError("Réseau interdit"))]:
            p.start(); self.addCleanup(p.stop)
        self.a = frappe.get_doc({"doctype": "Address", "name": "ESSAI-GEO-" + frappe.generate_hash(length=12),
            "address_title": "Essai synthétique", "address_type": "Billing", "address_line1": "12 rue de Paris",
            "city": "Tunis", "state": "Tunis", "custom_secteur": "Secteur 4", "country": "Tunisia", "pincode": "1000"})
        self.a.db_insert()

    def tearDown(self):
        frappe.db.rollback(save_point="geocodage_qualite_test")
        frappe.cache().delete_keys(self.prefixe)

    def test_qualite_sur_document_natif(self):
        self.assertEqual(A.choisir({"google": {"candidats": [candidat()]}}, self.a)["statut"], "Fiable")

    def test_confirmation_google_puis_gps_premiere_main(self):
        with patch.object(A, "rechercher", return_value=A.choisir({"google": {"candidats": [candidat(partiel=True)]}}, self.a)):
            r = A.diagnostiquer(self.a.name)
        self.assertEqual(r["statut"], "À vérifier")
        A.confirmer(self.a.name, r["jeton"], 0)
        a = frappe.get_doc("Address", self.a.name)
        self.assertEqual(A.position_memorisee(a), (36.81, 10.18))
        self.assertEqual(a.custom_geocode_valide_par, "Administrator")
        self.assertTrue(a.custom_geocode_expire_le)
        tache = frappe._dict(select_address=a.name)
        self.assertEqual(T.coordonnees_tache(tache, {})[2], "Google Maps")
        with patch.object(A, "rechercher", side_effect=AssertionError("Position déjà confirmée")):
            self.assertEqual(A.resoudre_adresse(a.name)["point"], [36.81, 10.18])
        # Sauvegarde native : vérifie aussi le hook Address.validate.
        a.city = "Ariana"
        a.save()
        self.assertIsNone(A.position_memorisee(a))
        self.assertEqual(a.custom_geocode_statut, "À vérifier")
        A.enregistrer_gps(a.name, A.signature(a), a.modified, 36.82, 10.19)
        a.reload()
        self.assertEqual(A.position_memorisee(a), (36.82, 10.19))
        self.assertFalse(a.custom_geocode_expire_le)
        self.assertEqual(T.coordonnees_tache(tache, {})[2], "adresse")

    def test_confirmation_perimee_apres_modification(self):
        with patch.object(A, "rechercher", return_value=A.choisir({"google": {"candidats": [candidat()]}}, self.a)):
            r = A.diagnostiquer(self.a.name)
        self.a.city = "Ariana"
        self.a.save()
        with self.assertRaises(frappe.ValidationError):
            A.confirmer(self.a.name, r["jeton"], 0)
        self.a.reload()
        self.assertFalse(self.a.custom_latitude)

    def test_lien_lieu_prioritaire_sur_le_quartier_conserve_source_et_expiration(self):
        self.a.custom_lien_google_map = URL
        self.a.custom_latitude, self.a.custom_longitude = 36.76, 10.21
        self.a.custom_geocode_source = "texte ≈ (lien mort)"
        self.a.custom_geocode_statut = "À vérifier"
        self.a.db_update()
        r = L._decoder_lieu(REPONSE, PID)
        with patch.object(L, '_geocoder_lieu', return_value=r), patch.object(A, 'rechercher', side_effect=AssertionError('Pas de recherche du quartier')):
            self.assertEqual(A.resoudre_adresse(self.a.name)['point'], r['point'])
        self.a.reload()
        self.assertEqual(self.a.custom_geocode_source, 'lien google lieu')
        self.assertTrue(self.a.custom_geocode_expire_le)
        self.assertEqual(self.a.custom_geocode_statut, 'Fiable')
        with patch.object(L, 'resoudre_lien', side_effect=AssertionError('Position déjà connue')):
            point = T.coordonnees_tache(frappe._dict(select_address=self.a.name, google_map=URL), {})
        self.assertEqual(point[2], 'Google Maps')
        for actuel, attendu in zip(point[:2], r['point']):
            self.assertAlmostEqual(actuel, attendu, places=6)

    def test_confirmation_lien_lieu_ne_supprime_pas_expiration_google(self):
        with patch.object(L, 'resoudre_lien', return_value=L._decoder_lieu(REPONSE, PID)):
            A.enregistrer_gps(self.a.name, A.signature(self.a), self.a.modified, lien=URL)
        self.a.reload()
        self.assertEqual(self.a.custom_geocode_source, 'lien google lieu confirmé')
        self.assertTrue(self.a.custom_geocode_expire_le)
        for actuel, attendu in zip(A.position_memorisee(self.a), (36.7659806, 10.2009159)):
            self.assertAlmostEqual(actuel, attendu, places=6)

    def test_estimation_google_memorisee_sans_validation_et_reutilisable(self):
        c = candidat(precision='APPROXIMATE', partiel=True, obtenu_le=str(frappe.utils.now_datetime()))
        r = A.choisir({'google':{'candidats':[c]}}, self.a)
        with patch.object(A, 'rechercher', return_value=r):
            resultat = A.resoudre_pour_tournee(self.a.name, centres={})
        self.assertTrue(resultat['estimee'])
        self.a.reload()
        self.assertEqual(self.a.custom_geocode_source, 'texte google estimé')
        self.assertEqual(self.a.custom_geocode_statut, 'À vérifier')
        self.assertFalse(self.a.custom_geocode_valide_par)
        self.assertTrue(self.a.custom_geocode_expire_le)
        self.assertIsNone(A.position_memorisee(self.a))
        with patch.object(A, 'rechercher', side_effect=AssertionError('Estimation déjà enregistrée')):
            point = T.coordonnees_tache(frappe._dict(select_address=self.a.name), {})
        self.assertEqual(point, (36.81, 10.18, 'Google Maps estimé'))
        self.a.city = 'Ariana'
        self.a.save()
        self.assertIsNone(A.estimation_memorisee(self.a))
