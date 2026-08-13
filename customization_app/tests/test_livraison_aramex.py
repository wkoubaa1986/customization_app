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
