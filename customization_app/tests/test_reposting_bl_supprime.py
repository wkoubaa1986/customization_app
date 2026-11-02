"""`paires_stock` : seuls les articles de stock, un couple article/entrepôt une fois, ordre d'apparition."""
import unittest

from customization_app.reposting_bl_supprime import paires_stock


class TestPairesStock(unittest.TestCase):
    def test_ignore_la_main_d_oeuvre_et_les_doublons(self):
        lignes = [
            {"item_code": "M-E-OD", "warehouse": "Magasins - A&S"},          # main d'œuvre, sans stock
            {"item_code": "F-T33-C", "warehouse": "Magasins - A&S"},
            {"item_code": "F-T33-C", "warehouse": "Magasins - A&S"},         # doublon
            {"item_code": "F-T33-C", "warehouse": "Hall - A&S"},             # autre entrepôt : gardé
            {"item_code": "", "warehouse": "Magasins - A&S"},                # ligne vide
            {"item_code": "PO-75", "warehouse": None},                       # sans entrepôt
        ]
        stock = {"F-T33-C", "PO-75"}
        self.assertEqual(paires_stock(lignes, lambda c: c in stock),
                         [("F-T33-C", "Magasins - A&S"), ("F-T33-C", "Hall - A&S")])

    def test_accepte_des_objets_et_une_liste_vide(self):
        class Ligne:
            def __init__(self, c, w):
                self.item_code, self.warehouse = c, w
        self.assertEqual(paires_stock([Ligne("PO-75", "Hall - A&S")], lambda c: True), [("PO-75", "Hall - A&S")])
        self.assertEqual(paires_stock(None, lambda c: True), [])
