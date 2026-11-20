"""Liens de lieux Google : aucune recherche textuelle et aucun réseau réel."""
import copy
import unittest
from unittest.mock import MagicMock, patch

from customization_app import geocodage_liens as L, geocodage_adresses as A, geocodage_essais as E

PID = "ChIJYY2Jf0g3_RIRABvtRlnQw-w"
URL = "https://www.google.com/maps/place/Steg+jbal+jloud,+Tunis/data=!4m2!3m1!1s0x12fd37487f898d61:0xecc3d05946ed1b00!18m1!1e1"
REPONSE = {"status": "OK", "results": [{"place_id": PID, "formatted_address": "Tunis, Tunisie",
    "types": ["establishment", "point_of_interest"], "geometry": {
        "location": {"lat": 36.7659806, "lng": 10.2009159}, "location_type": "GEOMETRIC_CENTER"}}]}


class TestIdentifiants(unittest.TestCase):
    def test_identifiant_du_lien_de_lieu(self):
        self.assertEqual(L.identifiant_lieu(URL), PID)
        self.assertEqual(L.identifiant_lieu('https://www.google.com/maps/search/?api=1&query=test&query_place_id='+PID), PID)
        self.assertEqual(L.identifiant_lieu('https://maps.google.com/?q=place_id:'+PID), PID)

    def test_camera_ne_remplace_pas_le_lieu(self):
        self.assertEqual(L._cible_directe(URL.replace('/data=', '/@36.9,10.3,14z/data=')), {'place_id': PID})
        self.assertIsNone(L._cible_directe('https://www.google.com/maps/dir/A/B/@36.9,10.3,14z'))

    def test_urls_non_google_et_identifiants_multiples_refuses(self):
        self.assertIsNone(L.identifiant_lieu(URL.replace('www.google.com','google.com.example.org')))
        self.assertIsNone(L.identifiant_lieu(URL+'!1s0x12fd37487f898d61:0xecc3d05946ed1b01'))
        for url in ['http://169.254.169.254/maps', 'https://google.com:9000/maps', 'https://user:pass@google.com/maps', 'file:///tmp/maps']:
            self.assertFalse(L.lien_google(url))

    def test_lien_gps_direct_sans_api(self):
        with patch.object(L.requests, 'head') as head, patch.object(L.requests, 'get') as get:
            self.assertEqual(L.resoudre_lien('https://maps.google.com/?q=36.81,10.18')['point'], [36.81,10.18])
            head.assert_not_called(); get.assert_not_called()

    def test_lien_court_redirige_vers_lieu(self):
        cache = MagicMock(); cache.get_value.return_value = None
        with patch.object(L.frappe, 'cache', return_value=cache), patch.object(L.requests, 'head', return_value=MagicMock(status_code=302, headers={'location':URL})) as head:
            self.assertEqual(L.cible_lien('https://maps.app.goo.gl/exemple'), {'place_id':PID})
            self.assertEqual(head.call_count, 1)

    def test_redirection_externe_non_suivie(self):
        cache = MagicMock(); cache.get_value.return_value = None
        with patch.object(L.frappe, 'cache', return_value=cache), patch.object(L.requests, 'head', return_value=MagicMock(status_code=302, headers={'location':'http://169.254.169.254/'})) as head:
            self.assertIsNone(L.cible_lien('https://maps.app.goo.gl/exemple'))
            self.assertEqual(head.call_count, 1)


class TestGeocodageLieu(unittest.TestCase):
    def test_identite_google_confirmee_point_du_lieu(self):
        r = L._decoder_lieu(REPONSE, PID)
        self.assertEqual(r['point'], [36.7659806,10.2009159])
        self.assertEqual(r['choix']['origine'], 'lien Google Maps')
        self.assertTrue(r['choix']['obtenu_le'])

    def test_pas_de_repli_sur_quartier_autre_id_ou_reponse_partielle(self):
        for changement in [{'place_id':'AUTRE'}, {'types':['neighborhood','political']}, {'partial_match':True},
                            {'geometry':{'location':{'lat':48.8,'lng':2.3},'location_type':'ROOFTOP'}}]:
            d = copy.deepcopy(REPONSE); d['results'][0].update(changement)
            self.assertIsNone(L._decoder_lieu(d, PID))
        self.assertIsNone(L._decoder_lieu({'status':'ZERO_RESULTS'}, PID))

    def test_api_appelee_par_id_jamais_par_nom_avec_plafond_partage(self):
        cache = MagicMock(); cache.get_value.return_value = None
        with patch.object(A, 'reglages', return_value={'actif':True}), patch.object(L.frappe, 'cache', return_value=cache), \
                patch.object(E, '_cle', return_value='SECRET'), patch.object(E, '_consommer') as quota, \
                patch.object(L.requests, 'get', return_value=MagicMock(status_code=200, json=lambda:REPONSE)) as get:
            self.assertIsNotNone(L._geocoder_lieu(PID))
            self.assertEqual(get.call_args.kwargs['params'], {'key':'SECRET','place_id':PID,'language':'fr'})
            quota.assert_called_once()

    def test_quota_epuise_et_desactivation_sans_appel(self):
        cache = MagicMock(); cache.get_value.return_value = None
        with patch.object(A, 'reglages', return_value={'actif':True}), patch.object(L.frappe, 'cache', return_value=cache), \
                patch.object(E, '_cle', return_value='SECRET'), patch.object(E, '_consommer', side_effect=E.ServiceIndisponible('Plafond atteint')), \
                patch.object(L.requests, 'get') as get:
            self.assertIsNone(L._geocoder_lieu(PID)); get.assert_not_called()
        with patch.object(A, 'reglages', return_value={'actif':False}), patch.object(E,'_cle') as cle:
            self.assertIsNone(L._geocoder_lieu(PID)); cle.assert_not_called()

    def test_cache_utilisable_sans_reseau_et_expiration_non_renouvelee(self):
        resultat = L._decoder_lieu(REPONSE, PID)
        cache = MagicMock(); cache.get_value.return_value = resultat
        with patch.object(A, 'reglages', return_value={'actif':True}), patch.object(L.frappe, 'cache', return_value=cache), \
                patch.object(E, '_cle', return_value='SECRET'), patch.object(E, '_consommer') as quota, patch.object(L.requests, 'get') as get:
            self.assertEqual(L._geocoder_lieu(PID, reseau=False), resultat)
            quota.assert_not_called(); get.assert_not_called()
            cache.get_value.return_value = None
            self.assertIsNone(L._geocoder_lieu(PID, reseau=False))
            quota.assert_not_called(); get.assert_not_called()
