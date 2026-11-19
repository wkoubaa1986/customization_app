"""Échange d'articles par Aramex (19/11/2026) — règles pures, sans la base."""
import unittest

from customization_app import echange_aramex as E


class TestLignes(unittest.TestCase):
    def test_lignes_vides_et_quantites_nulles_ecartees(self):
        self.assertEqual(E.lignes_propres([{"item_code": "Acc020-A", "qty": 3}, {"item_code": "", "qty": 2},
                                           {"item_code": "X", "qty": 0}, None]),
                         [{"item_code": "Acc020-A", "qty": 3.0}])

    def test_un_article_repete_s_additionne(self):
        self.assertEqual(E.lignes_propres([{"item_code": "Acc020-A", "qty": 1}, {"item_code": "Acc020-A", "qty": 2}]),
                         [{"item_code": "Acc020-A", "qty": 3.0}])

    def test_accepte_le_json_de_l_ecran(self):
        self.assertEqual(E.lignes_propres('[{"item_code": "Acc020-B", "qty": "3"}]'), [{"item_code": "Acc020-B", "qty": 3.0}])
        self.assertEqual(E.lignes_propres(""), [])

    def test_texte(self):
        self.assertEqual(E.texte_lignes([{"item_code": "Acc020-A", "qty": 3.0}, {"item_code": "T33", "qty": 1.5}]),
                         "3 × Acc020-A, 1.5 × T33")


class TestBonDeLivraison(unittest.TestCase):
    """L'utilisateur choisit le bon de livraison que l'échange corrige."""

    LIGNES = [{"bl": "MAT-DN-2", "posting_date": "2026-11-07", "commande": "WEB1-008697", "item_code": "Acc020-B", "item_name": "Robinet", "qty": 3},
              {"bl": "MAT-DN-1", "posting_date": "2026-10-01", "commande": "WEB1-008500", "item_code": "T33", "item_name": "T33", "qty": 1},
              {"bl": "MAT-DN-1", "posting_date": "2026-10-01", "commande": "WEB1-008500", "item_code": "Membrane", "item_name": "Membrane", "qty": 2}]

    def test_un_bloc_par_bon_dans_l_ordre_recu(self):
        r = E.livraisons_du_client(self.LIGNES)
        self.assertEqual([x["bl"] for x in r], ["MAT-DN-2", "MAT-DN-1"])
        self.assertEqual(r[0]["resume"], "3 × Acc020-B")
        self.assertEqual(r[1]["resume"], "1 × T33, 2 × Membrane")
        self.assertEqual(r[0]["commande"], "WEB1-008697")

    def test_ce_qui_revient_doit_venir_du_bon_choisi(self):
        du_bl = E.livraisons_du_client(self.LIGNES)[0]["articles"]
        self.assertEqual(E.ecarts_avec_le_bl([{"item_code": "Acc020-B", "qty": 3}], du_bl), [])
        self.assertEqual(E.ecarts_avec_le_bl([{"item_code": "Acc020-B", "qty": 2}], du_bl), [])      # une partie seulement
        self.assertEqual(len(E.ecarts_avec_le_bl([{"item_code": "Acc020-B", "qty": 4}], du_bl)), 1)  # plus que livré
        self.assertEqual(len(E.ecarts_avec_le_bl([{"item_code": "Acc010-A", "qty": 1}], du_bl)), 1)  # pas sur ce bon


class TestEntrepotDOrigine(unittest.TestCase):
    """Ce qui revient est proposé dans l'entrepôt d'où il était sorti sur son bon de livraison d'origine."""

    BL = [("Acc020-B", "Magasins - A&S"), ("Liv", "Magasins - A&S"), ("T33", "Hall - A&S")]

    def test_l_entrepot_du_bon_d_origine(self):
        self.assertEqual(E.entrepot_d_origine(self.BL, ["Acc020-B"]), "Magasins - A&S")
        self.assertEqual(E.entrepot_d_origine(self.BL, ["T33"]), "Hall - A&S")

    def test_plusieurs_entrepots_on_ne_devine_pas(self):
        self.assertEqual(E.entrepot_d_origine(self.BL, ["Acc020-B", "T33"]), "")

    def test_article_absent_du_bon_ou_bon_inconnu(self):
        self.assertEqual(E.entrepot_d_origine(self.BL, ["Acc010-A"]), "")
        self.assertEqual(E.entrepot_d_origine([], ["Acc020-B"]), "")


class TestDateDeValidation(unittest.TestCase):
    """La sortie de stock est datée de la VALIDATION de la commande (docstatus 0 → 1 dans son historique)."""

    def version(self, le, changes):
        import json
        return {"creation": le, "data": json.dumps({"changed": changes})}

    def test_le_cas_de_warghui_ridha(self):
        v = [self.version("2026-11-14 08:23:27.123456", [["status", "Brouillon", "À Livrer et Facturer"], ["docstatus", 0, 1]]),
             self.version("2026-11-18 13:02:12.000000", [["status", "À Livrer et Facturer", "À Facturer"]])]
        self.assertEqual(E.date_de_validation(v), "2026-11-14 08:23:27")

    def test_une_modification_sans_validation_ne_compte_pas(self):
        self.assertIsNone(E.date_de_validation([self.version("2026-11-13 13:53:05", [["delivery_date", "a", "b"]])]))
        self.assertIsNone(E.date_de_validation([]))

    def test_la_premiere_validation_fait_foi_meme_si_l_historique_est_dans_le_desordre(self):
        v = [self.version("2026-11-16 10:00:00", [["docstatus", 0, 1]]), self.version("2026-11-14 08:23:27", [["docstatus", 0, 1]])]
        self.assertEqual(E.date_de_validation(v), "2026-11-14 08:23:27")

    def test_une_annulation_n_est_pas_une_validation(self):
        self.assertIsNone(E.date_de_validation([self.version("2026-11-15 09:00:00", [["docstatus", 1, 2]])]))

    def test_un_historique_illisible_est_ignore(self):
        self.assertIsNone(E.date_de_validation([{"creation": "2026-11-14 08:00:00", "data": "pas du json"}]))


class TestReprise(unittest.TestCase):
    """Le patch de reprise ne touche la correction manuelle du 18/11 que si elle est exactement celle qu'on connaît."""

    def setUp(self):
        from customization_app.patches import reprise_echange_warghui_photo_nabil as P
        self.P = P

    def test_la_correction_connue(self):
        self.assertTrue(self.P.correction_attendue([("Acc020-A", 3.0, "Hall - A&S", None), ("Acc020-B", 3, None, "Hall - A&S")]))
        self.assertTrue(self.P.correction_attendue([("Acc020-B", 3, "", "Hall - A&S"), ("Acc020-A", 3, "Hall - A&S", "")]))

    def test_une_ecriture_modifiee_n_est_pas_touchee(self):
        self.assertFalse(self.P.correction_attendue([("Acc020-A", 2.0, "Hall - A&S", None), ("Acc020-B", 3, None, "Hall - A&S")]))
        self.assertFalse(self.P.correction_attendue([("Acc020-A", 3.0, "Magasins - A&S", None), ("Acc020-B", 3, None, "Hall - A&S")]))
        self.assertFalse(self.P.correction_attendue([("Acc020-A", 3.0, "Hall - A&S", None), ("Acc020-B", 3, None, "Hall - A&S"), ("T33", 1, "Hall - A&S", None)]))
        self.assertFalse(self.P.correction_attendue([]))


class TestRetard(unittest.TestCase):
    """La pochette devrait être revenue 7 jours après la livraison au client."""

    def test_pas_encore_livre_pas_de_retard(self):
        self.assertEqual(E.jours_de_retard(None, "2026-11-30"), 0)

    def test_dans_les_sept_jours_pas_de_retard(self):
        self.assertEqual(E.jours_de_retard("2026-11-16", "2026-11-18"), 0)
        self.assertEqual(E.jours_de_retard("2026-11-16", "2026-11-23"), 0)

    def test_au_dela_on_compte_les_jours(self):
        self.assertEqual(E.jours_de_retard("2026-11-16", "2026-11-26"), 3)
        self.assertEqual(E.jours_de_retard("2026-11-16 15:08:00", "2026-11-24"), 1)


if __name__ == "__main__":
    unittest.main()
