"""Régularisations de caisse et graphique de flux (25/11/2026) — fonctions pures."""
import unittest

from customization_app import argent_liquide as A
from customization_app import caisse_regularisations as R

SAUT = {"caisse": "Nejib Koubaa", "date": "2026-10-20", "cloture": "CLO-1055", "precedente": "CLO-1056",
        "date_precedente": "2026-10-19", "attendu": -3289.218, "ouverture": 2003.782, "saut": 5293.0}
ENTREES = [{"name": "ACC-JV-2026-00895", "date": "2026-10-19", "montant": 6000.0, "categorie": "Sabah Espèces",
            "depuis": "Sabah Espèces"}]
ACHAT = {"name": "ACC-JV-2026-01040", "date": "2026-11-09", "montant": 76.6, "depuis": "Compte temporaire",
         "remarque": "Achat divers Néjib", "mode": "Espèces", "saisie_par": "Nejib Koubaa"}
ANTIDATE = {"name": "ACC-PAY-2026-07592", "date": "2026-09-28", "saisi_le": "2026-10-06", "montant": 540.0,
            "client": "Mahdi Ayari", "saisi_par": "Akram"}


class TestAnomalies(unittest.TestCase):
    def setUp(self):
        self.a = R.construire_anomalies([SAUT], [ACHAT], [ANTIDATE], [], ENTREES, "Nejib Koubaa")

    def test_le_saut_du_20_10_et_son_explication(self):
        s = self.a[0]
        self.assertEqual((s["cle"], s["caisse"], s["poche"], s["montant"]), ("saut:CLO-1055", "Nejib Koubaa", "Caisse", 5293.0))
        self.assertIn("6 000,000 DT sont arrivés de « Sabah Espèces » (ACC-JV-2026-00895)", s["explication"])
        self.assertEqual([(o["code"], o["effet"]) for o in s["options"]], [("retirer", -5293.0), ("garder", 0.0)])

    def test_l_achat_saisi_a_l_envers_compte_double(self):
        j = self.a[1]
        self.assertEqual((j["cle"], j["poche"], j["caisse"]), ("jv:ACC-JV-2026-01040", "Collecte", "Nejib Koubaa"))
        self.assertIn("mode de l'écriture : Espèces", j["explication"])
        self.assertEqual({o["code"]: o["effet"] for o in j["options"]}, {"inversee": -153.2, "entree": 0.0})

    def test_un_antidate_ne_change_aucun_montant(self):
        self.assertEqual([(o["code"], o["effet"]) for o in self.a[2]["options"]], [("vu", 0.0)])
        self.assertIsNone(self.a[2]["caisse"])

    def test_decidee_ou_absorbee_elle_quitte_la_carte(self):
        regs = [{"name": "REG-00001", "cle": "jv:ACC-JV-2026-01040", "nature": "Entrée manuelle", "decision": "x"}]
        a_faire, traitees = R.trier(self.a, regs, "Nejib Koubaa")
        self.assertEqual([x["cle"] for x in a_faire], ["saut:CLO-1055", "antidate:ACC-PAY-2026-07592"])
        self.assertEqual(traitees[0]["traitee_par"], "REG-00001")
        # Un inventaire du collecteur absorbe SES anomalies antérieures — pas celles qui n'ont pas de caisse.
        inventaire = {"name": "REG-00002", "cle": "inventaire:x", "nature": R.INVENTAIRE, "caisse": "Nejib Koubaa",
                      "poche": "Collecte", "decide_le": "2026-11-26 10:00:00"}
        a_faire, traitees = R.trier(self.a, [inventaire], "Nejib Koubaa")
        self.assertEqual([x["cle"] for x in a_faire], ["antidate:ACC-PAY-2026-07592"])
        self.assertTrue(all("Absorbée par l'inventaire du 26/11/2026" in x["decision"] for x in traitees))
        # Une anomalie née APRÈS l'inventaire reste à décider.
        apres = R.construire_anomalies([dict(SAUT, date="2026-11-27", cloture="CLO-2000")], [], [], [], [], "Nejib Koubaa")
        self.assertEqual(len(R.trier(apres, [inventaire], "Nejib Koubaa")[0]), 1)

    def test_effets_et_inventaire(self):
        regs = [{"caisse": "Nejib Koubaa", "poche": "Caisse", "effet": -5293.0, "nature": "Saut de chaîne"},
                {"caisse": "Nejib Koubaa", "poche": "Collecte", "effet": -153.2, "nature": "Entrée manuelle"},
                {"caisse": "Nejib Koubaa", "poche": "Collecte", "effet": 40.0, "nature": R.INVENTAIRE, "compte": 2500.0,
                 "theorique": 2460.0, "decide_le": "2026-11-26 10:00:00", "name": "REG-3"},
                {"caisse": R.COFFRE, "poche": "Coffre", "effet": 0.0, "nature": R.INVENTAIRE_COFFRE, "compte": 60000.0,
                 "theorique": 61000.0, "decide_le": "2026-11-26 11:00:00", "name": "REG-4"},
                {"caisse": None, "poche": None, "effet": 0.0, "nature": "Paiement antidaté"}]
        self.assertEqual(R.effets(regs), {("Nejib Koubaa", "Caisse"): -5293.0, ("Nejib Koubaa", "Collecte"): -113.2})
        self.assertEqual(R.dernier_inventaire(regs, "Nejib Koubaa", "Collecte")["ecart"], 40.0)
        self.assertEqual(R.dernier_inventaire(regs, R.COFFRE, "Coffre")["ecart"], -1000.0)   # le coffre n'a pas d'effet sur la carte
        self.assertIsNone(R.dernier_inventaire(regs, "Sadok", "Collecte"))


class TestEntreeManuelle(unittest.TestCase):
    E = A.COMPTE_ESPECES

    def test_refuse_l_achat_a_l_envers_pas_le_transfert(self):
        achat = [{"account": self.E, "debit": 76.6, "credit": 0}, {"account": "Compte temporaire - A&S", "debit": 0, "credit": 76.6}]
        self.assertEqual(R.entree_manuelle(achat, self.E, A.categorie), 76.6)
        coffre = [{"account": self.E, "debit": 6000, "credit": 0}, {"account": A.COMPTE_SABAH, "debit": 0, "credit": 6000}]
        self.assertEqual(R.entree_manuelle(coffre, self.E, A.categorie), 0.0)
        banque = [{"account": self.E, "debit": 500, "credit": 0}, {"account": A.COMPTES_BANQUE[0], "debit": 0, "credit": 500}]
        self.assertEqual(R.entree_manuelle(banque, self.E, A.categorie), 0.0)
        # Une sortie (recette versée au coffre, dépense) n'est jamais concernée.
        recette = [{"account": self.E, "debit": 0, "credit": 2000}, {"account": A.COMPTE_SABAH, "debit": 2000, "credit": 0}]
        self.assertEqual(R.entree_manuelle(recette, self.E, A.categorie), 0.0)
        self.assertEqual(R.entree_manuelle([{"account": "Autre - A&S", "debit": 5, "credit": 0}], self.E, A.categorie), 0.0)


class TestFlux(unittest.TestCase):
    def test_un_paiement_un_transfert_une_depense(self):
        self.assertEqual(A.repartir_flux({"especes": 100.0}, "clients", "x"), [("clients", "especes", 100.0)])
        self.assertEqual(A.repartir_flux({"especes": -2000.0, "sabah": 2000.0}, "autres_entrees", "depenses"),
                         [("especes", "sabah", 2000.0)])
        self.assertEqual(A.repartir_flux({"especes": -35.5}, "autres_entrees", "depenses"), [("especes", "depenses", 35.5)])
        self.assertEqual(A.repartir_flux({"sabah": -8000.0, "banque": 8000.0}, "a", "b"), [("sabah", "banque", 8000.0)])
        self.assertEqual(A.repartir_flux({}, "a", "b"), [])

    def test_une_piece_a_plusieurs_branches_se_repartit(self):
        # 100 d'espèces : 60 au coffre, 40 de dépense — chaque endroit garde son compte.
        flux = dict(((d, v), m) for d, v, m in A.repartir_flux({"especes": -100.0, "sabah": 60.0}, "x", "depenses"))
        self.assertEqual(flux, {("especes", "sabah"): 60.0, ("especes", "depenses"): 40.0})


if __name__ == "__main__":
    unittest.main()
