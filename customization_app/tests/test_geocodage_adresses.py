"""Sélection, concurrence, autorisation et conservation des positions : sans réseau réel."""
import datetime
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from customization_app import geocodage_adresses as A
from customization_app import geocodage_essais as E
from customization_app import tournee_optimisation as T


def adresse(**kwargs):
    d = frappe._dict(name="ADDR-TEST", address_line1="12 rue de Paris", address_line2="", city="Tunis", country="Tunisia",
                     pincode="1000", custom_lien_google_map="", modified="2026-11-20 10:00:00")
    d.update(kwargs)
    return d


def candidat(**kwargs):
    d = dict(lat=36.81, lng=10.18, precision="ROOFTOP", libelle="12 rue de Paris, Tunis", partiel=False,
             adresse={"numero": "12", "rue": "Rue de Paris", "villes": ["Tunis"], "code_postal": "1000"})
    d.update(kwargs)
    return d


class TestChoix(unittest.TestCase):
    def test_batiment_et_adresse_concordants(self):
        r = A.choisir({"google": {"candidats": [candidat()]}}, adresse())
        self.assertEqual(r["statut"], "Fiable")
        self.assertEqual(r["point"], [36.81, 10.18])

    def test_accord_de_deux_centres_de_ville_ne_valide_pas(self):
        r = A.choisir({"google": {"candidats": [candidat(precision="APPROXIMATE")]},
                       "nominatim": {"candidats": [candidat(precision="city")]}}, adresse())
        self.assertEqual(r["statut"], "À vérifier")
        self.assertIsNone(r["point"])

    def test_pas_de_moyenne_et_nominatim_peut_etre_choisi(self):
        n = candidat(precision="house", lat=36.8102)
        r = A.choisir({"google": {"candidats": [candidat(partiel=True)]}, "nominatim": {"candidats": [n]}}, adresse())
        self.assertEqual(r["statut"], "Fiable")
        self.assertEqual(r["point"], [n["lat"], n["lng"]])
        self.assertEqual(r["choix"]["service"], "nominatim")

    def test_desaccord_bloque_meme_avec_batiments(self):
        r = A.choisir({"google": {"candidats": [candidat()]}, "nominatim": {"candidats": [candidat(lat=36.86, precision="house")]}}, adresse())
        self.assertEqual(r["statut"], "À vérifier")
        self.assertGreater(r["distance_services_m"], 200)
        self.assertIsNone(r["point"])

    def test_plusieurs_batiments_google_restent_ambigus(self):
        r = A.choisir({"google": {"candidats": [candidat(), candidat(lat=36.86)]}}, adresse())
        self.assertEqual(r["statut"], "À vérifier")

    def test_numero_absent_ne_confirme_pas_un_batiment_choisi_par_google(self):
        r = A.choisir({"google": {"candidats": [candidat()]}}, adresse(address_line1="rue de Paris"))
        self.assertEqual(r["statut"], "À vérifier")
        self.assertIsNone(r["point"])

    def test_numero_ville_et_partial_match_sont_controles(self):
        for c in [candidat(partiel=True), candidat(adresse={"numero": "99", "rue": "Rue de Paris", "villes": ["Tunis"]}),
                  candidat(adresse={"numero": "12", "rue": "Rue de Paris", "villes": ["Sousse"]}),
                  candidat(adresse={"numero": "12", "rue": "Rue de Rome", "villes": ["Tunis"]})]:
            self.assertEqual(A.choisir({"google": {"candidats": [c]}}, adresse())["statut"], "À vérifier")

    def test_aucun_resultat_ou_coordonnees_invalides(self):
        for c in [candidat(lat=float("nan")), candidat(lat=48), candidat(lng=0)]:
            r = A.choisir({"google": {"candidats": [c]}}, adresse())
            self.assertEqual(r["statut"], "Introuvable")
        self.assertEqual(A.choisir({"google": {"message": "quota atteint"}}, adresse())["statut"], "Indisponible")


class TestEtat(unittest.TestCase):
    def test_lien_historique_preserve_et_texte_historique_a_revoir(self):
        a = adresse(custom_latitude=36.81, custom_longitude=10.18, custom_geocode_source="lien adresse")
        self.assertEqual(A.position_memorisee(a), (36.81, 10.18))
        a.custom_geocode_source = "texte ≈"
        self.assertIsNone(A.position_memorisee(a))

    def test_expiration_google_et_changement_adresse(self):
        a = adresse(custom_latitude=36.81, custom_longitude=10.18, custom_geocode_source="texte google fiable",
                    custom_geocode_statut="Fiable", custom_geocode_expire_le="2026-12-01 00:00:00")
        a.custom_geocode_signature = A.signature(a)
        with patch.object(A, "now_datetime", return_value=datetime.datetime(2026, 11, 20)):
            self.assertEqual(A.position_memorisee(a), (36.81, 10.18))
            a.city = "Sousse"
            self.assertIsNone(A.position_memorisee(a))
        a.city = "Tunis"
        with patch.object(A, "now_datetime", return_value=datetime.datetime(2026, 12, 2)):
            self.assertIsNone(A.position_memorisee(a))

    def test_fiche_connue_ne_declenche_aucun_reseau(self):
        a = adresse(custom_latitude=36.81, custom_longitude=10.18, custom_geocode_source="lien adresse")
        with patch.object(A.frappe, "get_doc", return_value=a), patch.object(A, "rechercher") as recherche:
            self.assertEqual(A.resoudre_adresse(a.name)["point"], [36.81, 10.18])
            recherche.assert_not_called()

    def test_adresse_ambigue_attend_une_correction_sans_rappeler_google(self):
        a = adresse(custom_geocode_statut="À vérifier", custom_geocode_motif="Numéro absent")
        a.custom_geocode_signature = A.signature(a)
        with patch.object(A.frappe, "get_doc", return_value=a), patch.object(A, "rechercher", return_value={"statut":"Introuvable"}) as recherche:
            self.assertEqual(A.resoudre_adresse(a.name)["statut"], "À vérifier")
            recherche.assert_called_once_with(a, reseau=False)

    def test_pas_d_ecrasement_si_gps_confirme_pendant_la_recherche(self):
        a = adresse()
        courant = adresse(custom_latitude=36.81, custom_longitude=10.18, custom_geocode_source="position validée")
        with patch.object(A.frappe, "has_permission", return_value=True), patch.object(A, "_verrouiller", return_value=courant), \
                patch.object(A.frappe, "db", MagicMock()) as db:
            self.assertFalse(A._memoriser(a, {"statut": "À vérifier", "motif": "texte ambigu", "point": None}))
            db.set_value.assert_not_called()

    def test_point_grossier_et_rendez_vous_epingle_bloquent(self):
        t = {"custom_type_dintervention": "Entretien", "custom_tournee_fixe": 1, "custom_tournee_employe_fixe": 1}
        cfg = {"types": ["Entretien", "Réparation"]}
        self.assertTrue(T.position_a_verifier(t, None, cfg))
        self.assertTrue(T.position_a_verifier(t, (36.8, 10.2, "secteur"), cfg))
        self.assertTrue(T.position_a_verifier(t, (36.8, 10.2, T.GROSSIERE), cfg))
        self.assertFalse(T.position_a_verifier(t, (36.8, 10.2, "adresse"), cfg))
        self.assertFalse(T.position_a_verifier({"custom_type_dintervention": "Réparation", "dans_local": "Oui"}, None, cfg))


class TestServices(unittest.TestCase):
    def test_reevaluation_sans_cache_ne_declenche_pas_de_reseau(self):
        cache = MagicMock(); cache.get_value.return_value = None
        with patch.object(A, "reglages", return_value={"actif":True,"nominatim":None}), \
                patch.object(A.frappe, "cache", return_value=cache), patch.object(E,"_cle",return_value="SECRET"), \
                patch.object(E,"_consommer") as quota, patch.object(E,"_requete") as req:
            A.rechercher(adresse(), reseau=False)
            quota.assert_not_called(); req.assert_not_called()
    def test_autre_pays_sans_requete(self):
        with patch.object(A, "reglages", return_value={"actif": True, "nominatim": None}), patch.object(E, "_requete") as req:
            self.assertEqual(A.rechercher(adresse(country="France"))["statut"], "Indisponible")
            req.assert_not_called()

    def test_verrou_occupe_sans_erreur_serveur(self):
        cache = MagicMock()
        cache.get_value.return_value = None
        cache.lock.return_value.__enter__.side_effect = A.LockError("busy")
        with patch.object(A, "reglages", return_value={"actif": True, "nominatim": None}), \
                patch.object(A.frappe, "cache", return_value=cache), patch.object(E, "_cle", return_value="SECRET"), \
                patch.object(E, "_consommer") as quota, patch.object(E, "_requete") as req:
            self.assertEqual(A.rechercher(adresse())["statut"], "Indisponible")
            quota.assert_not_called()
            req.assert_not_called()

    def test_service_public_refuse_et_prive_facultatif(self):
        self.assertIsNone(A.url_nominatim(""))
        self.assertEqual(A.url_nominatim("http://127.0.0.1:8085"), "http://127.0.0.1:8085/search")
        for url in ["https://nominatim.openstreetmap.org/search", "https://user:secret@example.com", "file:///etc/passwd", "http://169.254.169.254"]:
            with self.assertRaises(ValueError):
                A.url_nominatim(url)

    def test_desactive_aucun_reseau_ni_lecture_de_cle(self):
        with patch.object(A, "reglages", return_value={"actif": False, "nominatim": None}), \
                patch.object(E, "_requete") as requete, patch.object(E, "_cle") as cle:
            r = A.rechercher(adresse())
            self.assertEqual(r["statut"], "Indisponible")
            requete.assert_not_called()
            cle.assert_not_called()

    def test_google_seul_si_pas_de_serveur_prive(self):
        cache = MagicMock()
        cache.get_value.return_value = None
        with patch.object(A, "reglages", return_value={"actif": True, "nominatim": None}), \
                patch.object(A.frappe, "cache", return_value=cache), patch.object(E, "_cle", return_value="SECRET"), \
                patch.object(E, "_consommer"), patch.object(E, "_requete", return_value=[candidat()]) as req:
            self.assertEqual(A.rechercher(adresse())["statut"], "Fiable")
            self.assertEqual(req.call_count, 1)
            self.assertEqual(req.call_args.args[0], "google")

    def test_deux_fournisseurs_concurrents_sans_frappe_dans_les_threads(self):
        cache = MagicMock()
        cache.get_value.return_value = None
        barriere = threading.Barrier(2)
        threads, consommations = [], []
        principal = threading.get_ident()

        def requete(s, texte, cle, endpoint):
            threads.append(threading.get_ident())
            barriere.wait(timeout=3)
            return [candidat(precision="house" if s == "nominatim" else "ROOFTOP")]

        with patch.object(A, "reglages", return_value={"actif": True, "nominatim": "http://127.0.0.1:8085/search"}), \
                patch.object(A.frappe, "cache", return_value=cache), patch.object(E, "_cle", return_value="SECRET"), \
                patch.object(E, "_consommer", side_effect=lambda: consommations.append(threading.get_ident())), \
                patch.object(E, "_requete", side_effect=requete):
            r = A.rechercher(adresse())
        self.assertEqual(r["statut"], "Fiable")
        self.assertEqual(len(set(threads)), 2)
        self.assertNotIn(principal, threads)
        self.assertEqual(consommations, [principal, principal])

    def test_cache_et_quota_conservent_les_protections(self):
        cache = MagicMock()
        cache.get_value.return_value = {"candidats": [candidat()]}
        with patch.object(A, "reglages", return_value={"actif": True, "nominatim": None}), \
                patch.object(A.frappe, "cache", return_value=cache), patch.object(E, "_cle", return_value="SECRET"), \
                patch.object(E, "_consommer") as quota, patch.object(E, "_requete") as req:
            self.assertEqual(A.rechercher(adresse())["statut"], "Fiable")
            quota.assert_not_called(); req.assert_not_called()
        cache.get_value.return_value = None
        with patch.object(A, "reglages", return_value={"actif": True, "nominatim": None}), \
                patch.object(A.frappe, "cache", return_value=cache), patch.object(E, "_cle", return_value="SECRET"), \
                patch.object(E, "_consommer", side_effect=E.ServiceIndisponible("Plafond atteint")), patch.object(E, "_requete") as req:
            self.assertEqual(A.rechercher(adresse())["statut"], "Indisponible")
            req.assert_not_called()


class TestValidation(unittest.TestCase):
    def setUp(self):
        flags = patch.object(frappe.local, "flags", frappe._dict(in_test=False), create=True)
        flags.start(); self.addCleanup(flags.stop)

    def test_jeton_autre_utilisateur_refuse(self):
        cache = MagicMock()
        cache.get_value.return_value = {"adresse": "ADDR-TEST", "utilisateur": "autre"}
        with patch.object(A, "_acces", return_value=adresse()), patch.object(A.frappe, "cache", return_value=cache), \
                patch.object(A.frappe, "session", SimpleNamespace(user="moi")), patch.object(A.frappe, "throw", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                A.confirmer("ADDR-TEST", "JETON", 0)

    def test_gps_ne_remplace_pas_une_modification_recente(self):
        a = adresse()
        nouveau = adresse(modified="2026-11-20 11:00:00")
        with patch.object(A, "_acces", return_value=a), patch.object(A, "_verrouiller", return_value=nouveau), \
                patch.object(A.frappe, "throw", side_effect=frappe.ValidationError), patch.object(A.frappe, "db", MagicMock()) as db:
            with self.assertRaises(frappe.ValidationError):
                A.enregistrer_gps(a.name, A.signature(a), a.modified, 36.81, 10.18)
            db.set_value.assert_not_called()

    def test_changement_texte_invalide_la_validation_sans_effacer_gps(self):
        avant = adresse(custom_latitude=36.81, custom_longitude=10.18)
        apres = adresse(city="Ariana", custom_latitude=36.81, custom_longitude=10.18)
        doc = MagicMock()
        doc.get.side_effect = apres.get
        doc.get_doc_before_save.return_value = avant
        A.adresse_modifiee(doc)
        self.assertEqual(doc.custom_geocode_statut, "À vérifier")
        self.assertEqual(doc.custom_geocode_signature, "")


class TestIntegrationTournee(unittest.TestCase):
    def setUp(self):
        flags = patch.object(frappe.local, "flags", frappe._dict(in_test=False), create=True)
        flags.start(); self.addCleanup(flags.stop)
        self.tache = frappe._dict(name="T-TEST", select_address="ADDR-TEST", custom_type_dintervention="Entretien",
            status="Open", custom_choix_du_staff="EMP-TEST", starts_on="2026-11-20 09:00:00", ends_on="2026-11-20 09:30:00",
            custom_tournee_fixe=1, custom_tournee_employe_fixe=1)
        self.cfg = {"depot": (36.87, 10.19), "exclus": set(), "types": ["Entretien"], "debut": 480, "fin": 1080}

    def test_position_introuvable_conserve_la_journee_sans_inventer_de_trajet(self):
        with patch.object(T, "_superviseur"), patch.object(T, "config", return_value=self.cfg), \
                patch.object(T, "_champs_epingles", return_value=[]), patch.object(T.frappe, "get_all", return_value=[self.tache]), \
                patch.object(T, "_centres_secteurs", return_value={}), patch.object(T, "controle_localisation_actif", return_value=True), \
                patch.object(T, "coordonnees_tache", return_value=None), patch.object(T, "matrice") as matrice, \
                patch.object(T.frappe, "db", MagicMock()) as db:
            db.get_value.return_value = "Adresse sans numéro : bâtiment à confirmer."
            r = T.proposer("2026-11-20")
        self.assertEqual(r["employes"], [])
        self.assertEqual(r["conservees_sans_position"][0]["tache"], "T-TEST")
        self.assertNotIn("positions_a_verifier", r)
        matrice.assert_not_called()

    def test_application_refuse_une_position_expiree_sans_ecriture(self):
        with patch.object(T, "_superviseur"), patch.object(T, "config", return_value=self.cfg), \
                patch.object(T.frappe, "get_doc", return_value=self.tache), patch.object(T, "_centres_secteurs", return_value={}), \
                patch.object(T, "controle_localisation_actif", return_value=True), patch.object(T, "coordonnees_tache", return_value=None), \
                patch.object(T.frappe, "throw", side_effect=frappe.ValidationError), patch.object(T.frappe, "db", MagicMock()) as db:
            with self.assertRaises(frappe.ValidationError):
                T.appliquer("2026-11-20", [{"tache": "T-TEST", "starts_on": self.tache.starts_on}])
            db.set_value.assert_not_called()
        self.assertFalse(frappe.flags.get("tournee_sans_reseau"))



class TestEstimationAutomatique(unittest.TestCase):
    def setUp(self):
        self.instant = datetime.datetime(2026, 11, 20, 12)
        p = patch.object(A, 'now_datetime', return_value=self.instant)
        p.start(); self.addCleanup(p.stop)
        self.a = adresse(custom_secteur='Secteur 1')
        self.c = candidat(precision='APPROXIMATE', partiel=True, service='google', niveau=1, rang=0,
                          obtenu_le='2026-11-20 10:00:00', motif='Quartier seulement')
        self.r = dict(statut='À vérifier', point=None, choix=self.c, candidats=[self.c], motif='Quartier seulement')

    def test_estimation_continue_sans_confirmer_ni_moyenner(self):
        with patch.object(A.frappe, 'get_doc', return_value=self.a), patch.object(A, 'resoudre_adresse', return_value=self.r), \
                patch.object(A, '_memoriser', return_value=True) as enregistrer:
            r = A.resoudre_pour_tournee(self.a.name, centres={})
        self.assertEqual(r['point'], [36.81, 10.18])
        self.assertTrue(r['estimee'])
        self.assertEqual(r['statut'], 'À vérifier')
        self.assertEqual(r['source'], 'texte google estimé')
        self.assertTrue(enregistrer.call_args.args[1]['estimee'])
        self.assertNotIn('confirme', enregistrer.call_args.kwargs)

    def test_gps_confirme_prioritaire_sans_recherche(self):
        self.a.update(custom_latitude=36.82, custom_longitude=10.19, custom_geocode_source='position validée')
        with patch.object(A.frappe, 'get_doc', return_value=self.a), patch.object(A, 'rechercher') as req:
            r = A.resoudre_pour_tournee(self.a.name)
        self.assertFalse(r['estimee']); self.assertEqual(r['point'], [36.82, 10.19])
        req.assert_not_called()

    def test_estimation_memorisee_sans_reseau_et_expiration(self):
        self.a.update(custom_latitude=36.81, custom_longitude=10.18, custom_geocode_source='texte google estimé',
                      custom_geocode_statut='À vérifier', custom_geocode_expire_le='2026-12-01 00:00:00')
        self.a.custom_geocode_signature = A.signature(self.a)
        with patch.object(A.frappe, 'get_doc', return_value=self.a), patch.object(A, 'rechercher') as req:
            r = A.resoudre_pour_tournee(self.a.name, reseau=False, centres={})
        self.assertTrue(r['estimee']); req.assert_not_called()
        self.assertIsNone(A.position_memorisee(self.a))
        self.a.custom_geocode_expire_le = '2026-11-19 00:00:00'
        self.assertIsNone(A.estimation_memorisee(self.a))
        self.a.custom_geocode_expire_le = '2026-12-01 00:00:00'
        self.a.city = 'Sousse'
        self.assertIsNone(A.estimation_memorisee(self.a))

    def test_hors_secteur_hors_pays_et_donnees_expirees_ecartees(self):
        for c in [dict(self.c, lat=48), dict(self.c, lat=35), dict(self.c, obtenu_le='2026-10-01 00:00:00')]:
            self.assertIsNone(A.meilleur_point_estime([c], self.a, centres={'Secteur 1':(36.81, 10.18)}))

    def test_ville_concordante_avant_resultat_lointain_ou_generique(self):
        autre = dict(self.c, lat=36.85, rang=0, adresse={'villes':['Ariana']})
        attendu = dict(self.c, lat=36.82, rang=1)
        self.assertIs(A.meilleur_point_estime([autre, attendu], self.a, centres={}), attendu)

    def test_cache_absent_relance_automatiquement_la_recherche_bornee(self):
        with patch.object(A.frappe, 'get_doc', return_value=self.a), \
                patch.object(A, 'resoudre_adresse', return_value=dict(self.r, candidats=[], choix=None)), \
                patch.object(A, 'rechercher', return_value=self.r) as req, patch.object(A, '_memoriser', return_value=True):
            r = A.resoudre_pour_tournee(self.a.name, centres={})
        req.assert_called_once_with(self.a, reseau=True)
        self.assertTrue(r['estimee'])

    def test_adresse_modifiee_pendant_recherche_ne_recoit_pas_ancienne_estimation(self):
        courant = adresse(city='Sousse')
        with patch.object(A.frappe, 'get_doc', side_effect=[self.a, courant]), \
                patch.object(A, 'resoudre_adresse', return_value=self.r), patch.object(A, '_memoriser', return_value=False):
            r = A.resoudre_pour_tournee(self.a.name, centres={})
        self.assertIsNone(r['point'])

    def test_preflight_estimation_autorisee_et_epingles_preservees(self):
        # Le plan peut avancer avec une estimation ; les épingles posées entre le
        # calcul et Appliquer continuent à protéger l'heure et l'employé.
        t = MagicMock()
        t.name='T-TEST'; t.status='Open'; t.starts_on='2026-11-20 09:00:00'; t.ends_on='2026-11-20 09:30:00'
        t.custom_choix_du_staff='EMP-TEST'
        t.get.side_effect = {'custom_type_dintervention':'Entretien', T.HEURE_FIXE:1, T.EMPLOYE_FIXE:1}.get
        with patch.object(frappe.local, 'flags', frappe._dict(), create=True), patch.object(T, '_superviseur'), \
                patch.object(T, 'config', return_value={'types':['Entretien'],'prevenir':{}}), \
                patch.object(T.frappe,'get_doc',return_value=t), patch.object(T.frappe,'db',MagicMock()), \
                patch.object(T,'controle_localisation_actif',return_value=True), \
                patch.object(T,'_centres_secteurs',return_value={}), \
                patch.object(T,'coordonnees_tache',return_value=(36.81,10.18,'Google Maps estimé')):
            r=T.appliquer('2026-11-20',[{'tache':t.name,'employe':'AUTRE','starts_on':'2026-11-20 11:00:00','ends_on':'2026-11-20 11:30:00'}])
        self.assertEqual(r['modifiees'],[])
        t.save.assert_not_called()
