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
                               "saisir_depense_euros", "banques", "verser_en_banque", "annuler_ecriture", "corriger_ecriture", "rendez_vous", "creneaux_rendez_vous", "creer_rendez_vous"})
        dep = s["paths"]["/api/method/customization_app.assistant_chatgpt.saisir_depense"]["post"]
        self.assertEqual(dep["requestBody"]["content"]["application/json"]["schema"]["required"], ["date", "libelle", "montant_ttc", "compte_charge"])


class TestLireEcriture(unittest.TestCase):
    """Relecture d'une écriture pour la corriger : de l'assistant ou non, versement / dépense / euros / inconnue."""
    BANQUES, CAISSES = ["BIAT - A&S", "STE430127B - Zitouna - A&S"], ["Sabah Espèces - A&S", "Espèces - A&S"]

    def _je(self, cheque_no, remarque, lignes, docstatus=1):
        from customization_app.assistant_chatgpt import lire_ecriture
        return lire_ecriture({"name": "ACC-JV-1", "docstatus": docstatus, "posting_date": "2026-11-02", "cheque_no": cheque_no,
                              "user_remark": remarque, "accounts": lignes, "_banques": self.BANQUES, "_caisses": self.CAISSES})

    def test_versement_de_l_assistant_et_versement_etranger(self):
        r = self._je("Versement espèces BIAT — réf. TT123", "Versement espèces BIAT — réf. TT123\nSaisie via ChatGPT par x",
                     [{"account": "BIAT - A&S", "debit": 500, "credit": 0}, {"account": "Sabah Espèces - A&S", "debit": 0, "credit": 500}])
        self.assertEqual((r["type"], r["montant"], r["banque"], r["reference"], r["de_l_assistant"]), ("versement", 500, "BIAT - A&S", "TT123", True))
        r = self._je("VERSEMENT ESPECES AGENCE DENDEN", "Ref : 1234",
                     [{"account": "STE430127B - Zitouna - A&S", "debit": 800, "credit": 0}, {"account": "Sabah Espèces - A&S", "debit": 0, "credit": 800}])
        self.assertEqual((r["type"], r["montant"], r["de_l_assistant"], r["statut"]), ("versement", 800, False, "validee"))

    def test_depense_et_euros(self):
        r = self._je("Dépense caisse — Pub Google", "Pub Google — Google\nSaisie via ChatGPT par x (Carte de crédit, TVA 19.0 à 19.0 %)",
                     [{"account": "Frais de Marketing - A&S", "debit": 100, "credit": 0}, {"account": "TVA 19% - A&S", "debit": 19, "credit": 0},
                      {"account": "STE430127B - Zitouna - A&S", "debit": 0, "credit": 119}], docstatus=0)
        self.assertEqual((r["type"], r["libelle"], r["montant_ttc"], r["tva"], r["taux_tva"], r["mode"], r["statut"]),
                         ("depense", "Pub Google", 119, 19, 19, "Carte de crédit", "brouillon"))
        r = self._je("Facebook", "Dépense en euros : Facebook — 185.73 € × 3.34 = 620.338 DT",
                     [{"account": "Frais de Marketing - A&S", "debit": 620.338, "credit": 0},
                      {"account": "Compte Wassim Euros - A&S", "debit": 0, "credit": 620.338, "credit_in_account_currency": 185.73, "exchange_rate": 3.34, "account_currency": "EUR"}])
        self.assertEqual((r["type"], r["montant_eur"], r["taux"], r["compte_charge"]), ("depense_euros", 185.73, 3.34, "Frais de Marketing - A&S"))

    def test_structure_inconnue(self):
        r = self._je("Recette au 31-10", "Recette", [{"account": "Sabah Espèces - A&S", "debit": 800, "credit": 0},
                                                   {"account": "Espèces - A&S", "debit": 0, "credit": 800}])
        self.assertEqual(r["type"], "inconnu")
        r = self._je("Salaires", "", [{"account": "Salaire - A&S", "debit": 500, "credit": 0}, {"account": "Loyer du Bureau - A&S", "debit": 300, "credit": 0},
                                      {"account": "Espèces - A&S", "debit": 0, "credit": 800}])
        self.assertEqual((r["type"], len(r["lignes"])), ("inconnu", 3))
