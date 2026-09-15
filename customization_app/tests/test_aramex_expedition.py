"""Tests de la creation de bordereau (aramex_expedition.py) — les fonctions PURES seulement.

Aucun appel a Aramex, aucune commande en base : construire le corps, refuser ce qui doit
l'etre, normaliser un telephone, decrire la marchandise.
"""
from __future__ import annotations

import datetime
import unittest

from customization_app import aramex_expedition as E

EXPEDITEUR = {"nom": "Magasin", "societe": "Aquaworld & Servicing", "telephone": "71000000",
              "email": "", "adresse": "Rue sidi Frej", "ville": "Soukra", "code_postal": "2036",
              "compte": "60519122"}
DESTINATAIRE = {"nom": "benbelgacem zied", "telephone": "24992312", "email": "zd@ex.com",
                "adresse": "Citee Amel", "ville": "Metlaoui", "gouvernorat": "Gafsa",
                "code_postal": "2130"}
COLIS = {"cod": 51.0, "poids": 0.5, "pieces": 1, "description": "Media Filtrante",
         "product_group": "DOM", "product_type": "ONP", "cheque_autorise": 0}
MAINTENANT = datetime.datetime(2026, 9, 14, 10, 0)


class TestConstruireExpedition(unittest.TestCase):
    def corps(self, **colis):
        return E.construire_expedition("SAL-ORD-1", EXPEDITEUR, DESTINATAIRE,
                                       dict(COLIS, **colis), MAINTENANT)

    def test_contre_remboursement_en_especes(self):
        c = self.corps()
        self.assertEqual(c["Details"]["CashOnDeliveryAmount"], {"CurrencyCode": "TND", "Value": 51.0})
        self.assertEqual(c["Details"]["Services"], "CODS")
        self.assertEqual(c["OperationsInstructions"], "")
        self.assertEqual(c["Comments"], "")

    def test_cheque_autorise_transmis_en_instruction(self):
        c = self.corps(cheque_autorise=1)
        self.assertIn("Chèque accepté", c["OperationsInstructions"])
        self.assertIn("Aquaworld & Servicing", c["OperationsInstructions"])
        self.assertEqual(c["Details"]["Services"], "CODS")

    def test_sans_reste_a_payer_pas_de_cod(self):
        c = self.corps(cod=0)
        self.assertIsNone(c["Details"]["CashOnDeliveryAmount"])
        self.assertEqual(c["Details"]["Services"], "")
        # Cheque autorise sans montant : rien a transmettre.
        self.assertEqual(self.corps(cod=0, cheque_autorise=1)["OperationsInstructions"], "")

    def test_parties(self):
        c = self.corps()
        self.assertEqual(c["Shipper"]["AccountNumber"], "60519122")
        self.assertEqual(c["Shipper"]["PartyAddress"]["City"], "Soukra")
        self.assertEqual(c["Consignee"]["PartyAddress"]["City"], "Metlaoui")
        self.assertEqual(c["Consignee"]["PartyAddress"]["StateOrProvinceCode"], "Gafsa")
        self.assertEqual(c["Consignee"]["PartyAddress"]["CountryCode"], "TN")
        self.assertEqual(c["Consignee"]["Contact"]["CellPhone"], "24992312")
        # Particulier : la societe = le nom. Aramex REFUSE une societe vide (ERR48).
        self.assertEqual(c["Consignee"]["Contact"]["CompanyName"], "benbelgacem zied")
        self.assertEqual(c["Shipper"]["Contact"]["CompanyName"], "Aquaworld & Servicing")
        self.assertEqual(c["Consignee"]["AccountNumber"], "")
        self.assertEqual(c["ThirdParty"]["Contact"]["PersonName"], "")

    def test_reference_et_dates(self):
        c = self.corps()
        self.assertEqual(c["Reference1"], "SAL-ORD-1")
        self.assertTrue(c["ShippingDateTime"].startswith("/Date("))
        self.assertEqual(c["ShippingDateTime"], c["DueDate"])
        self.assertEqual(c["Details"]["ActualWeight"], {"Unit": "KG", "Value": 0.5})
        self.assertEqual(c["Details"]["ProductGroup"], "DOM")
        self.assertEqual(c["Details"]["ProductType"], "ONP")
        self.assertEqual(c["Details"]["PaymentType"], "P")

    def test_adresse_tronquee_a_50(self):
        c = E.construire_expedition("x", EXPEDITEUR, dict(DESTINATAIRE, adresse="a" * 80),
                                    COLIS, MAINTENANT)
        self.assertEqual(len(c["Consignee"]["PartyAddress"]["Line1"]), 50)


class TestMotifsDeRefus(unittest.TestCase):
    def test_rien_a_redire(self):
        self.assertEqual(E.motifs_de_refus(DESTINATAIRE, COLIS), [])

    def test_chaque_manque_est_nomme(self):
        self.assertEqual(len(E.motifs_de_refus(dict(DESTINATAIRE, telephone="2499"), COLIS)), 1)
        self.assertEqual(len(E.motifs_de_refus(dict(DESTINATAIRE, adresse=" ", ville=""), COLIS)), 2)
        self.assertEqual(len(E.motifs_de_refus(dict(DESTINATAIRE, nom=""), COLIS)), 1)
        self.assertEqual(len(E.motifs_de_refus(DESTINATAIRE, dict(COLIS, cod=-1, poids=0))), 2)


class TestTelephone(unittest.TestCase):
    def test_formes_courantes(self):
        for brut in ("24992312", "24 992 312", "+216 24 992 312", "0021624992312",
                     "216-24992312"):
            self.assertEqual(E.normaliser_telephone(brut), "24992312", brut)

    def test_vide(self):
        self.assertEqual(E.normaliser_telephone(None), "")


VILLES = ["Ariana", "Raoued", "La Soukra", "Sfax", "Midoun", "Houmet Essouk", "Sousse",
          "Ksar Helal", "Jemmal", "Mannouba", "El Menzah", "Ghezala", "Bizerte", "Gafsa",
          "El Ksar", "Kalaat Landlous", "Souk El Ahad", "Cité El Khadra", "Le Kef", "Tunis",
          "Mejez El Bab", "Beja"]


class TestVilleAramex(unittest.TestCase):
    """Aramex n'accepte que ses 308 villes, a sa graphie : la ville de la commande doit y etre
    ramenee, et le degre de certitude doit etre DIT."""

    def v(self, ville, gouv=None):
        return E.ville_aramex(ville, gouv, VILLES)

    def test_exacte_a_la_graphie_pres(self):
        self.assertEqual(self.v("Ariana"), {"ville": "Ariana", "certitude": "exacte", "candidats": []})
        self.assertEqual(self.v("cité el-khadra")["ville"], "Cité El Khadra")
        self.assertEqual(self.v("MIDOUN")["certitude"], "exacte")

    def test_alias_connus(self):
        self.assertEqual(self.v("Soukra", "Ariana"), {"ville": "La Soukra", "certitude": "alias", "candidats": []})
        self.assertEqual(self.v("Djerba Houmt Souk")["ville"], "Houmet Essouk")
        self.assertEqual(self.v("Ksar Hellal")["ville"], "Ksar Helal")
        self.assertEqual(self.v("Majaz al Bab")["ville"], "Mejez El Bab")

    def test_el_ghazala_ne_devient_pas_ghezala(self):
        """⚠️ « Ghezala » (Bizerte) ressemble a « El Ghazala » (Ariana) : la comparaison floue
        y enverrait le colis a 60 km. L'alias explicite vers Raoued gagne."""
        r = self.v("El Ghazala", "Ariana")
        self.assertEqual(r["ville"], "Raoued")
        self.assertEqual(r["certitude"], "alias")

    def test_ville_contenue_en_mot_entier(self):
        self.assertEqual(self.v("Sfax Ville")["ville"], "Sfax")
        self.assertEqual(self.v("Djerba Midoun")["ville"], "Midoun")
        self.assertEqual(self.v("Sousse Jaouhara")["ville"], "Sousse")
        r = self.v("El Menzah 6")
        self.assertEqual((r["ville"], r["certitude"]), ("El Menzah", "contenue"))

    def test_approchee_est_une_proposition(self):
        r = self.v("Kalaat El Andalous", "Ariana")
        self.assertEqual((r["ville"], r["certitude"]), ("Kalaat Landlous", "approchee"))
        self.assertEqual(self.v("Souk El Ahed")["ville"], "Souk El Ahad")

    def test_repli_sur_le_gouvernorat(self):
        r = self.v("Mdhila", "Gafsa")
        self.assertEqual((r["ville"], r["certitude"]), ("Gafsa", "gouvernorat"))
        self.assertEqual(self.v("Kef East", "Kef")["ville"], "Le Kef")

    def test_rien(self):
        self.assertEqual(self.v("Zzzz", "Nulle part"), {"ville": None, "certitude": None, "candidats": []})
        self.assertEqual(self.v("", "Ariana")["ville"], None)

    def test_canonique(self):
        self.assertEqual(E._ville_canonique("la soukra", VILLES), "La Soukra")
        self.assertIsNone(E._ville_canonique("Soukra", VILLES))


class TestDescription(unittest.TestCase):
    def test_articles_et_quantites(self):
        items = [{"item_name": "Cartouche", "qty": 1}, {"item_name": "UDF 10'", "qty": 2},
                 {"item_name": "Livraison", "qty": 1}]
        self.assertEqual(E.description_marchandise(items, "x"), "Cartouche, UDF 10' ×2")

    def test_defaut_si_rien(self):
        self.assertEqual(E.description_marchandise([{"item_name": "Livraison", "qty": 1}], "Eau"),
                         "Eau")

    def test_troncature(self):
        items = [{"item_name": "Citerne 3.2G " * 10, "qty": 1}]
        d = E.description_marchandise(items, "x")
        self.assertLessEqual(len(d), E.LONGUEUR_DESCRIPTION)
        self.assertTrue(d.endswith("…"))


if __name__ == "__main__":
    unittest.main()
