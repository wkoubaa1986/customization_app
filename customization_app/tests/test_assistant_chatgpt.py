"""Assistant ChatGPT : règles pures (contrôle d'une dépense, lignes d'écriture, schéma OpenAPI)."""
import unittest

from customization_app.assistant_chatgpt import PLAFOND_DT, lignes_depense, schema_openapi, verifier_depense

COMPTES = {"Frais de Marketing - A&S", "Frais Téléphoniques - A&S"}
T = "2026-11-02"


class TestVerifier(unittest.TestCase):
    def test_acceptee(self):
        self.assertEqual(verifier_depense("2026-11-01", "Pub Google", 119, "Frais de Marketing - A&S", "Espèces", 19, COMPTES, T), [])

    def test_refus(self):
        e = verifier_depense("2026-12-01", " ", 0, "Inconnu", "Bitcoin", -1, COMPTES, T)
        self.assertEqual(len(e), 6)                      # futur, libellé, montant, compte, mode, tva
        self.assertTrue(any("futur" in x for x in e))
        e = verifier_depense("2026-01-01", "x", PLAFOND_DT + 1, "Frais de Marketing - A&S", "Espèces", 0, COMPTES, T)
        self.assertTrue(any("90 jours" in x for x in e) and any("plafond" in x for x in e))
        self.assertTrue(verifier_depense("n'importe", "x", 10, "Frais de Marketing - A&S", "Espèces", 0, COMPTES, T))

    def test_lignes(self):
        l = lignes_depense(119, 19, 19, "Frais de Marketing - A&S", "Espèces")
        self.assertEqual([(x["account"], x.get("debit_in_account_currency"), x.get("credit_in_account_currency")) for x in l],
                         [("Frais de Marketing - A&S", 100, None), ("TVA 19% - A&S", 19, None), ("Espèces - A&S", None, 119)])
        l = lignes_depense(107, 7, 7, "Frais Téléphoniques - A&S", "Carte de crédit")
        self.assertEqual((l[1]["account"], l[2]["account"]), ("TVA 7% - A&S", "STE430127B - Zitouna - A&S"))
        self.assertEqual(len(lignes_depense(50, 0, 0, "Frais de Marketing - A&S", "Virement")), 2)

    def test_schema(self):
        s = schema_openapi("https://exemple.tn")
        self.assertEqual(s["servers"][0]["url"], "https://exemple.tn")
        ops = {list(v.values())[0]["operationId"] for v in s["paths"].values()}
        self.assertEqual(ops, {"moi", "comptes_de_charge", "chercher_fournisseur", "chercher_client", "stock_article", "saisir_depense", "mes_saisies",
                               "saisir_depense_euros", "banques", "verser_en_banque", "rendez_vous", "creneaux_rendez_vous", "creer_rendez_vous"})
        dep = s["paths"]["/api/method/customization_app.assistant_chatgpt.saisir_depense"]["post"]
        self.assertEqual(dep["requestBody"]["content"]["application/json"]["schema"]["required"], ["date", "libelle", "montant_ttc", "compte_charge"])
