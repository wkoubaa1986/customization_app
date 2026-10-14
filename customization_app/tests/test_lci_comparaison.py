"""Description du fournisseur en puces + comparaison (14/10/2026) — fonctions pures, sans IA ni base."""
import unittest

from customization_app import lci_comparaison as C


class TestTextes(unittest.TestCase):
    def test_reference_de_ligne_retiree(self):
        self.assertEqual(C.texte_fournisseur("L11 · Reverse Osmosis System: 5 Stages"), "Reverse Osmosis System: 5 Stages")
        self.assertEqual(C.texte_fournisseur("Pas de référence"), "Pas de référence")

    def test_puces_texte_ou_html(self):
        self.assertEqual(C.puces_de("• 3 opaque filter housings\n• Clover faucet\n\n"), ["3 opaque filter housings", "Clover faucet"])
        self.assertEqual(C.puces_de("• Réservoir : 3,2 G<br>• Purge manuelle"), ["Réservoir : 3,2 G", "Purge manuelle"])

    def test_texte_puces(self):
        self.assertEqual(C.texte_puces(["a", "b"]), "• a\n• b")


class TestNormaliser(unittest.TestCase):
    def test_statut_recale_sur_ce_qui_est_rempli(self):
        r = C.normaliser({"puces": ["• 3.2 G tank"], "comparaison": [
            {"notre": "Tank: 3.2 G", "fournisseur": "3.2 G tank", "statut": "identique"},
            {"notre": "Clover faucet", "fournisseur": None, "statut": "identique"},       # rien en face → absent
            {"notre": None, "fournisseur": "Extra membrane +5$", "statut": "manquant"},     # rien chez nous → en plus
            {"notre": "Metal base", "fournisseur": "Plastic base", "statut": "???"},       # inconnu → différent
            {"notre": "", "fournisseur": ""},                                              # vide → ignoré
        ]})
        self.assertEqual([c["statut"] for c in r["comparaison"]], ["identique", "manquant", "en_plus", "different"])
        self.assertEqual(r["puces"], ["3.2 G tank"])  # le « • » de l'IA est retiré

    def test_une_de_ses_puces_deja_en_face_n_est_pas_aussi_en_plus(self):
        """Vu sur SP-M : « …5 Stages (Metal base) » en face de « Metal base » ET listée « en plus »."""
        r = C.normaliser({"comparaison": [
            {"notre": "Metal base", "fournisseur": "RO system 5 stages (Metal base)", "statut": "identique"},
            {"notre": None, "fournisseur": "RO system 5 stages (metal base)", "statut": "en_plus"},
            {"notre": None, "fournisseur": "5 m PE pipe", "statut": "en_plus"}]})
        self.assertEqual([c["statut"] for c in r["comparaison"]], ["identique", "en_plus"])

    def test_note_seulement_pour_different_et_en_plus(self):
        r = C.normaliser({"comparaison": [
            {"notre": "a", "fournisseur": "a", "statut": "identique", "note": "inutile"},
            {"notre": None, "fournisseur": "b +5$", "statut": "en_plus", "note": "option à 5 $"}]})
        self.assertNotIn("note", r["comparaison"][0])
        self.assertEqual(r["comparaison"][1]["note"], "option à 5 $")

    def test_reponse_vide_ou_illisible(self):
        self.assertIsNone(C.normaliser({}))
        self.assertIsNone(C.normaliser("texte"))
        self.assertIsNone(C.normaliser({"puces": [], "comparaison": []}))

    def test_parenthese_coupee_recollee(self):
        self.assertEqual(C.recoller(["Add another Normal Faucet (7 cm", "length) +1.85$", "Drain valve"]),
                         ["Add another Normal Faucet (7 cm length) +1.85$", "Drain valve"])
        self.assertEqual(C.recoller(["Faucet (304)", "Drain valve (plastic)"]), ["Faucet (304)", "Drain valve (plastic)"])

    def test_bornes(self):
        r = C.normaliser({"puces": ["x" * 500] * 50})
        self.assertEqual(len(r["puces"]), C.MAX_PUCES)
        self.assertEqual(len(r["puces"][0]), C.MAX_CAR)

    def test_resume(self):
        self.assertEqual(C.resume([{"statut": "identique"}, {"statut": "en_plus"}, {"statut": "en_plus"}]),
                         {"identique": 1, "different": 0, "manquant": 0, "en_plus": 2})


class TestNotreCote(unittest.TestCase):
    def ligne(self, **kw):
        import frappe
        return frappe._dict(dict({"item_code": "SP-M", "item_name": "Osmoseur", "item_name_traduit": "RO system",
                                  "description": "• Réservoir : 3,2 G<br>• Purge manuelle",
                                  "description_traduite": "• Tank: 3.2 G\n• Manual flush"}, **kw))

    def test_liste_en_anglais_prend_la_traduction(self):
        n = C.notre_cote(self.ligne(), "English")
        self.assertEqual((n["designation"], n["puces"]), ("RO system", ["Tank: 3.2 G", "Manual flush"]))

    def test_liste_en_francais_ou_sans_traduction(self):
        self.assertEqual(C.notre_cote(self.ligne(), "Français")["puces"], ["Réservoir : 3,2 G", "Purge manuelle"])
        self.assertEqual(C.notre_cote(self.ligne(description_traduite=""), "English")["puces"],
                         ["Réservoir : 3,2 G", "Purge manuelle"])


if __name__ == "__main__":
    unittest.main()
