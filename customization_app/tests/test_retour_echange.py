"""Tests de la reprise automatique d'échange (retour_echange.py).

Cas d'origine : un kit AP-M livré avec robinet trèfle chromé, le client veut un
luxe inox — commande avec la ligne « E-TC-LI » (bundle Rob-TC × −1, Rob-LI × +1).

Convention : `unittest.TestCase` pur, aucune base. Ce qui se teste est la
DÉCISION (quelles pièces rentrent, contre quel BL), pas l'enregistrement.
"""
from __future__ import annotations

import unittest
from datetime import datetime

from customization_app.retour_echange import choisir_bl_origine, pieces_reprises

BUNDLES = {
    "E-TC-LI": [{"item_code": "Rob-TC", "qty": -1}, {"item_code": "Rob-LI", "qty": 1}],
    "E-C-3.2-4": [{"item_code": "C-4", "qty": 1}, {"item_code": "C-3.2", "qty": -1}],
    # Un kit normal : que du positif.
    "AP-M-AJ-6-A-SM": [{"item_code": "AP-M", "qty": 1}, {"item_code": "M-75-AJ", "qty": 1}],
}
WH = "Magasins - A&S"


def _ligne(code, qty=1, wh=WH):
    return {"item_code": code, "qty": qty, "warehouse": wh}


class TestPiecesReprises(unittest.TestCase):
    def test_un_echange_de_robinet_reprend_le_trefle(self):
        pieces = pieces_reprises([_ligne("AP-M-AJ-6-A-SM"), _ligne("E-TC-LI")], BUNDLES)
        self.assertEqual(pieces, [{"item_code": "Rob-TC", "qty": 1.0, "warehouse": WH}])

    def test_un_bl_sans_echange_ne_reprend_rien(self):
        self.assertEqual(pieces_reprises([_ligne("AP-M-AJ-6-A-SM"), _ligne("M-I-OD")], BUNDLES), [])

    def test_la_piece_remise_n_est_pas_reprise(self):
        codes = [p["item_code"] for p in pieces_reprises([_ligne("E-TC-LI")], BUNDLES)]
        self.assertNotIn("Rob-LI", codes)

    def test_deux_echanges_donnent_deux_pieces(self):
        pieces = pieces_reprises([_ligne("E-TC-LI"), _ligne("E-C-3.2-4")], BUNDLES)
        self.assertEqual({(p["item_code"], p["qty"]) for p in pieces}, {("Rob-TC", 1.0), ("C-3.2", 1.0)})

    def test_la_quantite_de_la_ligne_multiplie(self):
        pieces = pieces_reprises([_ligne("E-TC-LI", qty=3)], BUNDLES)
        self.assertEqual(pieces[0]["qty"], 3.0)

    def test_la_meme_piece_sur_deux_lignes_se_cumule(self):
        pieces = pieces_reprises([_ligne("E-TC-LI"), _ligne("E-TC-LI", qty=2)], BUNDLES)
        self.assertEqual(pieces, [{"item_code": "Rob-TC", "qty": 3.0, "warehouse": WH}])

    def test_deux_entrepots_font_deux_lignes(self):
        pieces = pieces_reprises([_ligne("E-TC-LI"), _ligne("E-TC-LI", wh="Camion - A&S")], BUNDLES)
        self.assertEqual(len(pieces), 2)

    def test_une_ligne_a_quantite_nulle_ne_reprend_rien(self):
        self.assertEqual(pieces_reprises([_ligne("E-TC-LI", qty=0)], BUNDLES), [])


def _c(nom, instant, meme_commande=False):
    return {"name": nom, "instant": instant, "meme_commande": meme_commande}


class TestChoisirBlOrigine(unittest.TestCase):
    RETOUR = datetime(2026, 9, 30, 10, 0)

    def test_le_bl_de_la_meme_commande_passe_avant_un_plus_recent(self):
        candidats = [_c("DN-RECENT", datetime(2026, 9, 29, 9, 0)),
                     _c("DN-KIT", datetime(2026, 8, 24, 9, 0), meme_commande=True)]
        self.assertEqual(choisir_bl_origine(candidats, self.RETOUR), "DN-KIT")

    def test_sans_meme_commande_le_plus_recent(self):
        candidats = [_c("DN-VIEUX", datetime(2026, 1, 1)), _c("DN-RECENT", datetime(2026, 9, 1))]
        self.assertEqual(choisir_bl_origine(candidats, self.RETOUR), "DN-RECENT")

    def test_un_bl_posterieur_au_retour_est_ecarte(self):
        candidats = [_c("DN-FUTUR", datetime(2026, 10, 2), meme_commande=True),
                     _c("DN-AVANT", datetime(2026, 9, 1))]
        self.assertEqual(choisir_bl_origine(candidats, self.RETOUR), "DN-AVANT")

    def test_le_meme_instant_est_accepte(self):
        self.assertEqual(choisir_bl_origine([_c("DN-MEME", self.RETOUR)], self.RETOUR), "DN-MEME")

    def test_aucun_candidat_valable_donne_none(self):
        self.assertIsNone(choisir_bl_origine([_c("DN-FUTUR", datetime(2026, 10, 2))], self.RETOUR))
        self.assertIsNone(choisir_bl_origine([], self.RETOUR))


class TestPackedItemsAGarder(unittest.TestCase):
    def test_tout_le_bundle_d_echange_part_le_kit_reste(self):
        # La pièce reprise rentre par le retour, la pièce remise sort par sa
        # ligne : aucun packed item du bundle d'échange ne mouvemente le stock.
        from customization_app.retour_echange import bundles_echange, packed_items_a_garder
        packed = [{"item_code": "Rob-TC", "qty": -1, "parent_item": "E-TC-LI"},
                  {"item_code": "Rob-LI", "qty": 1, "parent_item": "E-TC-LI"},
                  {"item_code": "AP-M", "qty": 1, "parent_item": "AP-M-AJ-6-A-SM"}]
        garder = packed_items_a_garder(packed, bundles_echange(BUNDLES))
        self.assertEqual([p["item_code"] for p in garder], ["AP-M"])

    def test_sans_echanges_connus_le_negatif_part_quand_meme(self):
        from customization_app.retour_echange import packed_items_a_garder
        packed = [{"item_code": "Rob-TC", "qty": -1, "parent_item": "E-TC-LI"},
                  {"item_code": "Rob-LI", "qty": 1, "parent_item": "E-TC-LI"}]
        self.assertEqual([p["item_code"] for p in packed_items_a_garder(packed)], ["Rob-LI"])

    def test_sans_negatif_rien_ne_change(self):
        from customization_app.retour_echange import bundles_echange, packed_items_a_garder
        packed = [{"item_code": "AP-M", "qty": 1, "parent_item": "AP-M-AJ-6-A-SM"},
                  {"item_code": "M-75-AJ", "qty": 2, "parent_item": "AP-M-AJ-6-A-SM"}]
        self.assertEqual(packed_items_a_garder(packed, bundles_echange(BUNDLES)), packed)

    def test_bundles_echange_reconnait_le_negatif(self):
        from customization_app.retour_echange import bundles_echange
        self.assertEqual(bundles_echange(BUNDLES), {"E-TC-LI", "E-C-3.2-4"})


class TestComposantsAttendus(unittest.TestCase):
    def test_la_piece_remise_devient_une_ligne_du_bl(self):
        from customization_app.retour_echange import composants_attendus
        attendues = composants_attendus([_ligne("AP-M-AJ-6-A-SM"), _ligne("E-TC-LI")], BUNDLES)
        self.assertEqual(attendues, [{"echange": "E-TC-LI", "item_code": "Rob-LI", "warehouse": WH, "qty": 1}])

    def test_un_kit_ordinaire_ne_donne_aucune_ligne(self):
        from customization_app.retour_echange import composants_attendus
        self.assertEqual(composants_attendus([_ligne("AP-M-AJ-6-A-SM", 3)], BUNDLES), [])

    def test_la_quantite_de_la_ligne_multiplie(self):
        from customization_app.retour_echange import composants_attendus
        self.assertEqual(composants_attendus([_ligne("E-C-3.2-4", 2)], BUNDLES)[0]["qty"], 2)

    def test_deux_lignes_du_meme_echange_se_cumulent(self):
        from customization_app.retour_echange import composants_attendus
        attendues = composants_attendus([_ligne("E-TC-LI"), _ligne("E-TC-LI")], BUNDLES)
        self.assertEqual([(a["item_code"], a["qty"]) for a in attendues], [("Rob-LI", 2)])

    def test_une_ligne_a_zero_ne_donne_rien(self):
        from customization_app.retour_echange import composants_attendus
        self.assertEqual(composants_attendus([_ligne("E-TC-LI", 0)], BUNDLES), [])


class TestPlanComposants(unittest.TestCase):
    ATTENDUE = {"echange": "E-TC-LI", "item_code": "Rob-LI", "warehouse": WH, "qty": 1}

    def test_rien_sur_le_bl_tout_est_a_ajouter(self):
        from customization_app.retour_echange import plan_composants
        plan = plan_composants([], [self.ATTENDUE])
        self.assertEqual(plan, {"ajouter": [self.ATTENDUE], "ajuster": [], "retirer": []})

    def test_deja_en_place_rien_a_faire(self):
        from customization_app.retour_echange import plan_composants
        existante = dict(self.ATTENDUE, nom="row1")
        plan = plan_composants([existante], [self.ATTENDUE])
        self.assertEqual(plan, {"ajouter": [], "ajuster": [], "retirer": []})

    def test_la_quantite_de_l_echange_a_change_la_ligne_s_ajuste(self):
        from customization_app.retour_echange import plan_composants
        existante = dict(self.ATTENDUE, nom="row1", qty=1)
        plan = plan_composants([existante], [dict(self.ATTENDUE, qty=2)])
        self.assertEqual(plan["ajuster"], [("row1", 2)])
        self.assertEqual(plan["ajouter"], [])

    def test_l_echange_retire_du_bl_sa_ligne_part(self):
        from customization_app.retour_echange import plan_composants
        existante = dict(self.ATTENDUE, nom="row1")
        self.assertEqual(plan_composants([existante], []), {"ajouter": [], "ajuster": [], "retirer": ["row1"]})

    def test_un_autre_entrepot_est_une_autre_ligne(self):
        from customization_app.retour_echange import plan_composants
        existante = dict(self.ATTENDUE, nom="row1", warehouse="Hall - A&S")
        plan = plan_composants([existante], [self.ATTENDUE])
        self.assertEqual(plan["retirer"], ["row1"])
        self.assertEqual(plan["ajouter"], [self.ATTENDUE])
