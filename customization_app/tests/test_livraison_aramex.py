"""Tests du suivi des livraisons Aramex (livraison_aramex.py).

Convention : `unittest.TestCase` pur — seules les regles se testent ici, pas le reseau.
"""
from __future__ import annotations

import unittest

from customization_app import livraison_aramex as A


class TestReferenceAramex(unittest.TestCase):
    """Le bordereau vit dans le libelle du paiement, pas dans un champ a lui."""

    def test_le_libelle_reel_donne_le_bordereau(self):
        self.assertEqual(A.reference_aramex("Aramex N: 51330112234"), "51330112234")

    def test_le_placeholder_ne_donne_rien(self):
        """« Aramex N: 000 » = colis parti sans bordereau saisi. Le service repond 400 : autant ne
        pas l'appeler, et le signaler a l'ecran."""
        self.assertIsNone(A.reference_aramex("Aramex N: 000"))

    def test_les_libelles_vides(self):
        for vide in (None, "", "Aramex", "Aramex N:"):
            self.assertIsNone(A.reference_aramex(vide), vide)

    def test_un_numero_nu_reste_lisible(self):
        self.assertEqual(A.reference_aramex("51330112153"), "51330112153")


class TestAlerte(unittest.TestCase):
    """⚠️ TOUT CE QUI N'EST PAS LIVRE N'EST PAS UNE ALERTE. Un colis en transit se laisse attendre ;
    un client absent ou un refus ne se resoudra pas tout seul. Seul le second appelle quelqu'un."""

    def suivi(self, description, livre=False):
        return {"livre": livre, "derniere_maj": {"description": description}}

    def test_un_transit_normal_n_alerte_pas(self):
        self.assertIsNone(A.alerte(self.suivi("Statut en transit")))

    def test_une_tentative_echouee_alerte(self):
        texte = "Tentative de livraison - client non disponible - livraison reportée"
        self.assertEqual(A.alerte(self.suivi(texte)), texte)

    def test_un_refus_ou_un_retour_alerte(self):
        for texte in ("Colis refusé par le client", "Retour à l'expéditeur",
                      "Destinataire injoignable"):
            self.assertIsNotNone(A.alerte(self.suivi(texte)), texte)

    def test_un_colis_livre_n_alerte_jamais(self):
        """Meme si la derniere etape mentionne une tentative : elle est derriere lui."""
        self.assertIsNone(A.alerte(self.suivi("Tentative puis livré", livre=True)))

    def test_sans_suivi_aucune_alerte(self):
        self.assertIsNone(A.alerte(None))
        self.assertIsNone(A.alerte({}))


class TestKpis(unittest.TestCase):
    def colis(self, montant, suivi=None, alerte=None, reference="513301"):
        return {"montant": montant, "suivi": suivi, "alerte": alerte, "reference": reference}

    def test_chez_aramex_ne_compte_que_ce_qui_n_est_pas_livre(self):
        """C'est le seul chiffre qui parle d'argent : ce que le transporteur detient encore."""
        k = A.kpis([self.colis(100.0, {"livre": True}),
                    self.colis(50.0, {"livre": False}),
                    self.colis(25.0, None)])
        self.assertEqual(k["chez_aramex"], 75.0)
        self.assertEqual((k["livres"], k["en_route"]), (1, 1))

    def test_un_colis_sans_bordereau_est_compte_a_part(self):
        k = A.kpis([self.colis(30.0, None, reference=None)])
        self.assertEqual(k["sans_reference"], 1)
        self.assertEqual(k["suivi_inconnu"], 0)

    def test_un_suivi_jamais_demande_se_distingue_d_un_suivi_absent(self):
        k = A.kpis([self.colis(30.0, None)])
        self.assertEqual((k["sans_reference"], k["suivi_inconnu"]), (0, 1))

    def test_un_suivi_en_erreur_n_est_ni_livre_ni_en_route(self):
        """« Je ne sais pas » n'est pas « rien a signaler »."""
        k = A.kpis([self.colis(40.0, {"erreur": "502 — service indisponible"})])
        self.assertEqual((k["livres"], k["en_route"]), (0, 0))


if __name__ == "__main__":
    unittest.main()


class TestInformationDuClient(unittest.TestCase):
    """Un SMS, un seul, et seulement quand il apprend quelque chose au client."""

    def suivi(self, statut="Statut en transit", livre=False, franchies=3, erreur=None):
        d = {"statut": statut, "livre": livre, "etapes_franchies": franchies, "etapes_total": 7}
        if erreur:
            d["erreur"] = erreur
        return d

    def test_un_colis_en_route_avec_un_numero_declenche_le_sms(self):
        self.assertTrue(A.doit_prevenir(self.suivi(), False, "98366053"))

    def test_jamais_deux_fois_le_meme_bordereau(self):
        """Le colis peut rester quinze jours au tableau et la synchro passe chaque soir : sans
        cette regle, quinze SMS pour un seul colis."""
        self.assertFalse(A.doit_prevenir(self.suivi(), True, "98366053"))

    def test_jamais_pour_un_colis_livre(self):
        """Le client l'a en main : le prevenir serait le deranger pour rien."""
        self.assertFalse(A.doit_prevenir(self.suivi(statut="Livré", livre=True, franchies=7),
                                         False, "98366053"))

    def test_jamais_avant_la_deuxieme_etape(self):
        """« Créé » = le bordereau existe, le colis est encore chez nous. Annoncer un départ qui
        n'a pas eu lieu fait rappeler le client le lendemain."""
        self.assertFalse(A.doit_prevenir(self.suivi(statut="Créé", franchies=1), False, "98366053"))

    def test_jamais_quand_aramex_ignore_le_bordereau(self):
        """Notre probleme de saisie ne se transmet pas au client."""
        self.assertFalse(A.doit_prevenir(self.suivi(erreur="404 — aucune expedition"), False,
                                         "98366053"))

    def test_sans_numero_rien_a_envoyer(self):
        self.assertFalse(A.doit_prevenir(self.suivi(), False, ""))

    def test_le_message_porte_la_reference_le_statut_et_le_total(self):
        colis = {"customer_name": "Haifa Zaghouani", "reference": "51330108583", "montant": 106.0,
                 "suivi": self.suivi()}
        texte = A.message_sms(colis)
        for attendu in ("Haifa Zaghouani", "51330108583", "106.0", "Statut en transit"):
            self.assertIn(attendu, texte)
