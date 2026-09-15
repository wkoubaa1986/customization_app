"""Tests du client de l'API Aramex (aramex_api.py).

Convention : `unittest.TestCase` pur — zero reseau, zero base. Les historiques sont ceux
REELLEMENT rendus par `TrackShipments` le 14/09/2026 (compte 60519122), reduits aux champs
utiles : c'est sur eux que les regles ont ete ecrites, ils sont donc la reference.
"""
from __future__ import annotations

import datetime
import unittest

from customization_app import aramex_api as A


def ev(code, description, date, lieu="Tunis, Tunisia", comments="", probleme=""):
    return {"WaybillNumber": "x", "UpdateCode": code, "UpdateDescription": description,
            "UpdateDateTime": date, "UpdateLocation": lieu, "Comments": comments,
            "ProblemCode": probleme, "GrossWeight": "0.5", "ChargeableWeight": "0.5",
            "WeightUnit": "Kg", "UpdateTimeZone": "GMT+00:00"}


# 48812240761 — juste cree, pas encore remis a Aramex.
CREE = [ev("SH014", "Record created.", "/Date(1789143900000+0200)/")]

# 51330112761 — livre, puis fonds remis ; avec deux mises en attente et un echec d'adresse
# en cours de route (A16).
LIVRE = [
    ev("SH239", "Shipment charges paid", "/Date(1788968880000+0200)/"),
    ev("SH005", "Delivered", "/Date(1788954720000+0200)/", "Branch Bizerte, Tunisia",
       "Y Delivered by (Hamdi Werghmi) || CourierID: (177318)"),
    ev("SH003", "Out for Delivery", "/Date(1788937680000+0200)/", "Branch Bizerte, Tunisia"),
    ev("SH008", "Shipment on Hold", "/Date(1788858660000+0200)/", "Branch Bizerte, Tunisia"),
    ev("SH033", "Attempted Delivery - Awaiting Correct Delivery Address",
       "/Date(1788854880000+0200)/", "Branch Bizerte, Tunisia", probleme="A16"),
    ev("SH003", "Out for Delivery", "/Date(1788850200000+0200)/", "Branch Bizerte, Tunisia"),
    ev("SH001", "Under processing at operations facility", "/Date(1788243540000+0200)/",
       "Branch Bizerte, Tunisia"),
    ev("SH022", "Departed Operations facility – In Transit", "/Date(1788181260000+0200)/",
       "Branch Lac, Tunisia"),
    ev("SH047", "Received at Origin Facility", "/Date(1788180600000+0200)/",
       "Branch Lac, Tunisia"),
    ev("SH014", "Record created.", "/Date(1787768160000+0200)/"),
]

# 51330112960 — paiement refuse par le destinataire (A18) puis retour a l'expediteur.
RETOUR = [
    ev("SH069", "Returned to Shipper", "/Date(1789130700000+0200)/", "Tunis, Tunisia",
       "Rtn HAWB 50919594574 "),
    ev("SH498", "Customer Contact Attempts Completed - Pending Return to Shipper",
       "/Date(1789114860000+0200)/"),
    ev("SH033", "Attempted Delivery - Payment was Declined by Consignee / Awaiting Shipper "
       "Instructions", "/Date(1789049580000+0200)/", "Branch Bizerte, Tunisia", probleme="A18"),
    ev("SH003", "Out for Delivery", "/Date(1789024320000+0200)/", "Branch Bizerte, Tunisia"),
    ev("SH294", "On Hold - Attempting to Contact Customer / No Answer on Phone",
       "/Date(1788959700000+0200)/", "Branch Bizerte, Tunisia", probleme="U13"),
    ev("SH022", "Departed Operations facility – In Transit", "/Date(1788823620000+0200)/",
       "Aramex Tunisia, Tunisia"),
    ev("SH047", "Received at Origin Facility", "/Date(1788823440000+0200)/",
       "Aramex Tunisia, Tunisia"),
    ev("SH014", "Record created.", "/Date(1788521400000+0200)/"),
]


class TestDatesWcf(unittest.TestCase):
    def test_lecture_avec_decalage(self):
        # 1789143900000 ms = 2026-09-11 16:25:00 UTC ; +0200 -> 18:25 heure locale annoncee.
        self.assertEqual(A.parse_date_wcf("/Date(1789143900000+0200)/"),
                         datetime.datetime(2026, 9, 11, 18, 25))

    def test_lecture_sans_decalage(self):
        self.assertEqual(A.parse_date_wcf("/Date(0)/"), datetime.datetime(1970, 1, 1))

    def test_texte_illisible(self):
        for vide in (None, "", "2026-09-11", "/Date()/"):
            self.assertIsNone(A.parse_date_wcf(vide), vide)

    def test_aller_retour(self):
        dt = datetime.datetime(2026, 9, 14, 9, 30)
        texte = A.date_wcf(dt)
        self.assertTrue(texte.endswith("+0100)/"), texte)
        self.assertEqual(A.parse_date_wcf(texte), dt)


class TestNormaliserSuivi(unittest.TestCase):
    """Le dictionnaire rendu est CELUI que le scraper rendait : memes cles, memes sens."""

    def test_cree(self):
        s = A.normaliser_suivi("48812240761", CREE)
        self.assertEqual(s["statut"], "Créé")
        self.assertFalse(s["livre"])
        self.assertEqual(s["etapes_franchies"], 1)
        self.assertEqual(s["etapes_total"], len(A.JALONS))
        self.assertEqual(s["derniere_maj"]["description"],
                         "Bordereau créé, colis pas encore remis à Aramex")
        # L'historique brut garde le texte d'Aramex.
        self.assertEqual(s["evenements"][0]["description"], "Record created.")
        self.assertEqual(s["derniere_maj"]["date"], "11/09/2026 18:25")
        self.assertEqual(s["destination"], {"pays": "Tunisia", "ville": "Tunis"})
        self.assertIn("48812240761", s["url"])
        self.assertEqual(s["source"], "api")
        self.assertEqual(s["code"], "SH014")
        self.assertFalse(s["frais_payes"])
        self.assertTrue(s["etapes"][0]["courante"])
        self.assertFalse(s["etapes"][1]["franchie"])

    def test_livre_et_fonds_remis(self):
        s = A.normaliser_suivi("51330112761", LIVRE)
        self.assertEqual(s["statut"], "Livré")
        self.assertTrue(s["livre"])
        self.assertTrue(s["frais_payes"])
        self.assertEqual(s["etapes_franchies"], A.ETAPE_LIVRE)
        self.assertTrue(all(e["franchie"] for e in s["etapes"]))
        # L'historique est trie du plus recent au plus ancien, quel que soit l'ordre recu.
        self.assertEqual([e["code"] for e in s["evenements"]][:2], ["SH239", "SH005"])
        self.assertEqual(s["evenements"][-1]["code"], "SH014")

    def test_un_echec_passe_n_alerte_plus_une_fois_livre(self):
        """L'A16 du 06/09 est derriere lui : le statut est « Livré », pas « Échec »."""
        s = A.normaliser_suivi("51330112761", LIVRE)
        self.assertNotIn("chec", s["statut"])

    def test_retour(self):
        s = A.normaliser_suivi("51330112960", RETOUR)
        self.assertEqual(s["statut"], "Returned")
        self.assertFalse(s["livre"])
        self.assertEqual(s["code"], "SH069")
        # Le commentaire d'Aramex (numero du colis retour) reste lisible dans la description.
        self.assertIn("Rtn HAWB 50919594574", s["derniere_maj"]["description"])

    def test_echec_en_cours(self):
        """Avant le retour : le dernier evenement est le refus de paiement (A18)."""
        s = A.normaliser_suivi("x", RETOUR[2:])
        self.assertTrue(s["statut"].startswith("Échec de livraison — "))
        self.assertIn("Payment was Declined", s["statut"])
        # La raison d'Aramex reste dans la description : c'est elle qui dit quoi faire.
        self.assertIn("Payment was Declined", s["derniere_maj"]["description"])
        self.assertEqual(s["probleme"], "A18")
        self.assertFalse(s["livre"])
        # Le jalon ne recule pas : le colis a ete « en cours de livraison ».
        self.assertEqual(s["etapes_franchies"], 5)

    def test_retour_en_cours(self):
        s = A.normaliser_suivi("x", RETOUR[1:])
        self.assertTrue(s["statut"].startswith("Retour en cours — "))

    def test_en_route(self):
        s = A.normaliser_suivi("x", LIVRE[7:])
        self.assertEqual(s["statut"], "Statut en transit")
        self.assertEqual(s["etapes_franchies"], 3)

    def test_le_jalon_ne_recule_pas(self):
        """« Under processing » a l'agence de destination arrive APRES le transit : le
        statut change, le jalon reste a 3."""
        s = A.normaliser_suivi("x", LIVRE[6:])
        self.assertEqual(s["statut"], "En traitement en agence")
        self.assertEqual(s["etapes_franchies"], 3)

    def test_code_inconnu(self):
        s = A.normaliser_suivi("x", [ev("SH999", "Something new", "/Date(1789143900000+0200)/")])
        self.assertEqual(s["statut"], "Something new")
        self.assertEqual(s["etapes_franchies"], 0)

    def test_sans_evenement(self):
        s = A.normaliser_suivi("x", [])
        self.assertIsNone(s["statut"])
        self.assertFalse(s["livre"])
        self.assertEqual(s["evenements"], [])


class TestLibellesCompatibles(unittest.TestCase):
    """⚠️ LES LIBELLES SONT LUS PAR DES MOTS-CLES AILLEURS. `livraison_aramex.alerte` et
    `retour_aramex._MOTS_RETOUR` doivent reconnaitre ce que l'API rend, sans modification."""

    def test_alerte_sur_echec_et_retour(self):
        from customization_app import livraison_aramex as L

        self.assertIsNotNone(L.alerte(A.normaliser_suivi("x", RETOUR)))
        self.assertIsNotNone(L.alerte(A.normaliser_suivi("x", RETOUR[2:])))
        self.assertIsNone(L.alerte(A.normaliser_suivi("x", LIVRE)))
        self.assertIsNone(L.alerte(A.normaliser_suivi("x", CREE)))
        self.assertIsNone(L.alerte(A.normaliser_suivi("x", LIVRE[7:])))

    def test_retour_reconnu(self):
        from customization_app.retour_aramex import _MOTS_RETOUR

        statut = A.normaliser_suivi("x", RETOUR)["statut"].lower()
        self.assertTrue(any(m in statut for m in _MOTS_RETOUR))

    def test_une_mise_en_attente_simple_n_alerte_pas(self):
        from customization_app import livraison_aramex as L

        s = A.normaliser_suivi("x", LIVRE[3:])
        self.assertEqual(s["statut"], "En attente en agence")
        self.assertIsNone(L.alerte(s))


class TestNotifications(unittest.TestCase):
    def test_texte(self):
        self.assertEqual(A._texte_notifications(
            {"Notifications": [{"Code": "ERR75", "Message": "Failed to login"}]}),
            "ERR75 — Failed to login")

    def test_sans_notification(self):
        self.assertEqual(A._texte_notifications({"Notifications": []}), "erreur sans détail")

    def test_le_detail_de_l_expedition_remonte(self):
        """Reponse reelle du 15/09/2026 : rien en tete, ERR48 dans l'expedition."""
        brut = {"HasErrors": True, "Notifications": [],
                "Shipments": [{"HasErrors": True, "ID": None,
                               "Notifications": [{"Code": "ERR48",
                                                  "Message": "Consignee name is required"}]}]}
        original = A._poster
        try:
            A._poster = lambda url, corps, timeout=60: {"erreur": "erreur sans détail", "reponse": brut}
            cfg = type("Cfg", (), {"get": lambda self, k, d=None: {"api_active": 1, "api_username": "u",
                                                                   "api_account_number": "1"}.get(k, d),
                                   "get_password": lambda self, k, raise_exception=False: "x"})()
            res = A.create_shipment({"Reference1": "X"}, cfg=cfg)
        finally:
            A._poster = original
        self.assertEqual(res["erreur"], "ERR48 — Consignee name is required")


if __name__ == "__main__":
    unittest.main()


class TestRepliScrapingReferencesEntieres(unittest.TestCase):
    """Bug du 15/09/2026 en prod : Aramex repond 503, le repli logge la liste des bordereaux
    avec ", ".join(...) et plante sur un numero arrive en entier depuis l'ecran (TypeError),
    ce qui prive du suivi au lieu de basculer sur le scraping."""

    def test_par_api_tolere_les_entiers_quand_l_api_tombe(self):
        from unittest.mock import patch
        from customization_app import aramex_api, livraison_aramex

        with patch.object(aramex_api, "suivi_par_api", return_value=True), \
             patch.object(aramex_api, "track_shipments",
                          side_effect=aramex_api.AramexIndisponible("HTTP 503")), \
             patch.object(livraison_aramex.frappe, "log_error") as log:
            self.assertIsNone(livraison_aramex._par_api([51330112061, "48812240761"], 30))
        self.assertIn("51330112061, 48812240761", log.call_args.kwargs["message"])


class TestPosterReessaieLes503(unittest.TestCase):
    """Prod 15/09/2026 : Aramex repond HTTP 503 par intermittence (2 appels identiques sur 4).
    Un seul 503 basculait tout le paquet sur le scraping (~2 min pour 21 colis → erreur 500 au
    clic « Interroger »). `_poster` reessaie donc les 5xx et les erreurs reseau, pas les 4xx."""

    def _reponse(self, status, json_=None, text=""):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.status_code = status
        r.text = text
        r.json.return_value = json_ if json_ is not None else {}
        return r

    def test_un_503_puis_200_rend_la_reponse(self):
        from unittest.mock import patch
        from customization_app import aramex_api as A
        import requests
        reponses = [self._reponse(503, text="Service Unavailable"), self._reponse(200, {"HasErrors": False, "ok": 1})]
        with patch.object(requests, "post", side_effect=reponses) as post, patch.object(A, "PAUSE_TENTATIVE", 0):
            r = A._poster("http://aramex.test", {})
        self.assertEqual(r.get("ok"), 1)
        self.assertEqual(post.call_count, 2)

    def test_trois_503_rendent_l_erreur(self):
        from unittest.mock import patch
        from customization_app import aramex_api as A
        import requests
        with patch.object(requests, "post", return_value=self._reponse(503, text="Service Unavailable")) as post, \
             patch.object(A, "PAUSE_TENTATIVE", 0):
            r = A._poster("http://aramex.test", {})
        self.assertIn("HTTP 503", r["erreur"])
        self.assertEqual(post.call_count, A.TENTATIVES)

    def test_un_4xx_ne_reessaie_pas(self):
        from unittest.mock import patch
        from customization_app import aramex_api as A
        import requests
        with patch.object(requests, "post", return_value=self._reponse(401, text="Unauthorized")) as post, \
             patch.object(A, "PAUSE_TENTATIVE", 0):
            r = A._poster("http://aramex.test", {})
        self.assertIn("HTTP 401", r["erreur"])
        self.assertEqual(post.call_count, 1)

    def test_erreur_reseau_puis_200(self):
        from unittest.mock import patch
        from customization_app import aramex_api as A
        import requests
        with patch.object(requests, "post", side_effect=[requests.ConnectionError("boom"), self._reponse(200, {"HasErrors": False})]) as post, \
             patch.object(A, "PAUSE_TENTATIVE", 0):
            r = A._poster("http://aramex.test", {})
        self.assertNotIn("erreur", r)
        self.assertEqual(post.call_count, 2)
