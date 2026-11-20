"""Banc de comparaison : erreurs réelles simulées, droits et absence de mutation métier.

Exécution unittest sans site, sans réseau ni écriture de base.
"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import requests

from customization_app import geocodage_essais as G


class TestReponses(unittest.TestCase):
    def test_texte_transmis_sans_identite_ni_telephone(self):
        a = {"address_line1": "  10 rue   Exemple ", "city": "Tunis", "pincode": "1000",
             "name": "Client confidentiel", "phone": "12345678", "email_id": "client@example.invalid"}
        self.assertEqual(G.texte_adresse(a), "10 rue Exemple, 1000, Tunis, Tunisie")

    def test_google_correspondance_partielle_conservee(self):
        d = {"status": "OK", "results": [{"formatted_address": "Rue Exemple, Tunis", "partial_match": True,
             "geometry": {"location": {"lat": 36.8, "lng": 10.2}, "location_type": "APPROXIMATE"},
             "address_components": [{"types": ["country"], "short_name": "TN"}]}]}
        r = G.decoder("google", d)
        self.assertEqual((len(r), r[0]["precision"], r[0]["partiel"]), (1, "APPROXIMATE", True))

    def test_google_absence_resultat_pas_de_zero_fictif(self):
        self.assertEqual(G.decoder("google", {"status": "ZERO_RESULTS"}), [])
        self.assertEqual(G.decoder("google", {"status": "OK", "results": [{"geometry": {}}]}), [])

    def test_google_non_active_message_actionnable_sans_cle(self):
        with self.assertRaises(G.ServiceIndisponible) as e:
            G.decoder("google", {"status": "REQUEST_DENIED", "error_message": "This API is not activated. key=SECRET"})
        self.assertIn("non activée", str(e.exception))
        self.assertNotIn("SECRET", str(e.exception))

    def test_geoapify_precision_et_indice_distincts(self):
        r = G.decoder("geoapify", {"results": [{"lat": 36.8, "lon": 10.2, "formatted": "Tunis", "country_code": "tn",
                       "result_type": "city", "rank": {"confidence": 1}}]})
        self.assertEqual((r[0]["precision"], r[0]["confiance"]), ("city", 1))

    def test_positions_invalides_ou_pays_etranger_rejetes(self):
        rows = [{"lat": "nan", "lon": "10.2"}, {"lat": "100", "lon": "10"},
                {"lat": "36.8", "lon": "10.2", "address": {"country_code": "fr"}},
                {"lat": "36.8", "lon": "10.2", "address": {"country_code": "tn"}, "addresstype": "suburb"}]
        r = G.decoder("nominatim", rows[:3])
        self.assertEqual(r, [])
        self.assertEqual(G.decoder("locationiq", rows[3:])[0]["precision"], "suburb")

    def test_erreur_http_ne_retourne_pas_corps_ni_url(self):
        with patch.object(G.requests, "get", return_value=MagicMock(status_code=401, text="key=SECRET")) as get:
            with self.assertRaises(G.ServiceIndisponible) as e:
                G._requete("geoapify", "Tunis", "SECRET")
        self.assertIn("401", str(e.exception))
        self.assertNotIn("SECRET", str(e.exception))
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_erreur_reseau_ne_divulgue_pas_la_cle(self):
        with patch.object(G.requests, "get", side_effect=requests.Timeout("https://example.invalid/?key=SECRET")):
            with self.assertRaises(G.ServiceIndisponible) as e:
                G._requete("google", "Tunis", "SECRET")
        self.assertNotIn("SECRET", str(e.exception))

    def test_locationiq_404_est_une_adresse_introuvable(self):
        with patch.object(G.requests, "get", return_value=MagicMock(status_code=404)):
            self.assertEqual(G._requete("locationiq", "Tunis", "SECRET"), [])


class TestAccesEtLimites(unittest.TestCase):
    def setUp(self):
        flags = patch.object(frappe.local, "flags", frappe._dict(in_test=False), create=True)
        flags.start()
        self.addCleanup(flags.stop)

    def test_role_obligatoire_avant_lecture_des_cles(self):
        with patch.object(G.frappe, "only_for", side_effect=frappe.PermissionError), patch.object(G, "_cle") as cle:
            with self.assertRaises(frappe.PermissionError):
                G.etat_services()
            cle.assert_not_called()

    def test_cle_absente_aucun_appel_reseau(self):
        with patch.object(G, "_cle", return_value=None), patch.object(G.requests, "get") as get:
            with self.assertRaises(G.ServiceIndisponible):
                G.rechercher("geoapify", "Tunis")
            get.assert_not_called()

    def test_plafond_utilise_compteur_atomique(self):
        cache = MagicMock()
        cache.incr.return_value = G.MAX_APPELS_JOUR + 1
        with patch.object(G, "_compteur", return_value=(cache, b"compteur")):
            with self.assertRaises(G.ServiceIndisponible):
                G._consommer()
        cache.incr.assert_called_once_with(b"compteur")

    def test_cache_evite_un_nouvel_appel(self):
        cache = MagicMock()
        cache.get_value.return_value = []
        with patch.object(G.frappe, "cache", return_value=cache), patch.object(G, "_cle", return_value="SECRET"), patch.object(G, "_requete") as get:
            self.assertEqual(G.rechercher("google", "Tunis"), {"candidats": [], "cache": True})
            get.assert_not_called()
        self.assertNotIn("SECRET", cache.get_value.call_args.args[0])

    def test_changement_de_cle_invalide_le_cache(self):
        cache = MagicMock()
        cache.get_value.return_value = []
        with patch.object(G.frappe, "cache", return_value=cache), patch.object(G, "_cle", side_effect=["CLE_A", "CLE_B"]):
            G.rechercher("google", "Tunis")
            a = cache.get_value.call_args.args[0]
            G.rechercher("google", "Tunis")
            b = cache.get_value.call_args.args[0]
        self.assertNotEqual(a, b)

    def test_lot_invalide_rejete_avant_acces_adresses(self):
        with patch.object(G, "_autoriser"), patch.object(G.frappe, "throw", side_effect=frappe.ValidationError), patch.object(G.frappe, "get_doc") as doc:
            for noms, services in [([], ["google"]), (["A"] * (G.MAX_ADRESSES + 1), ["google"]), (["A"], ["inconnu"]), ([{}], ["google"]), (["A"], [{}])]:
                with self.assertRaises(frappe.ValidationError):
                    G.lancer(noms, services)
            doc.assert_not_called()

    def test_resultat_prive_au_demandeur(self):
        cache = MagicMock()
        cache.get_value.return_value = {"utilisateur": "autre@example.invalid", "lignes": [{"texte": "privé"}]}
        with patch.object(G, "_autoriser"), patch.object(G.frappe, "cache", return_value=cache), \
                patch.object(G.frappe, "session", SimpleNamespace(user="demandeur@example.invalid")), \
                patch.object(G.frappe, "throw", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                G.resultat("ID")


class TestExecution(unittest.TestCase):
    def test_lot_sans_ecriture_metier_et_arret_service_refuse(self):
        lot = {"id": "ID", "utilisateur": "Administrator", "statut": "en_attente", "services": ["google", "nominatim"],
               "fait": 0, "total": 4, "bilan": [], "lignes": [
                   {"adresse": "A", "texte": "A, Tunis", "reference": (36.8, 10.2), "resultats": {}},
                   {"adresse": "B", "texte": "B, Tunis", "reference": None, "resultats": {}}]}
        cache = MagicMock()
        cache.get_value.return_value = lot
        doc = MagicMock()
        snapshots = []

        def chercher(s, texte):
            if s == "google":
                raise G.ServiceIndisponible("API non activée")
            return {"candidats": [{"lat": 36.8, "lng": 10.2}], "cache": False}

        with patch.object(G, "_autoriser"), patch.object(G.frappe, "cache", return_value=cache), \
                patch.object(G.frappe, "set_user"), patch.object(G.frappe, "get_doc", return_value=doc), \
                patch.object(G, "rechercher", side_effect=chercher) as recherche, \
                patch.object(G, "_sauver", side_effect=lambda i, x: snapshots.append(copy.deepcopy(x))):
            G.executer("ID")
        doc.save.assert_not_called()
        doc.db_set.assert_not_called()
        self.assertEqual(doc.check_permission.call_count, 2)
        self.assertEqual([c.args[0] for c in recherche.call_args_list].count("google"), 1)
        r = snapshots[-1]
        self.assertEqual((r["statut"], r["fait"]), ("termine", 4))
        self.assertEqual(r["lignes"][0]["resultats"]["nominatim"]["ecart_m"], 0)
        self.assertIsNone(r["lignes"][1]["resultats"]["nominatim"]["ecart_m"])
        self.assertEqual(r["bilan"][1]["references_comparees"], 1)

    def test_medianes_excluent_les_erreurs_et_sans_reference(self):
        lignes = [{"resultats": {"google": {"statut": "trouve", "ecart_m": n}}} for n in (0, 100, 2000, None)]
        lignes.append({"resultats": {"google": {"statut": "indisponible", "ecart_m": None}}})
        r = G.bilan(lignes, ["google"])[0]
        self.assertEqual((r["trouves"], r["references_comparees"], r["ecart_median_m"], r["moins_200m"], r["plus_1000m"]), (4, 3, 100, 2, 1))
