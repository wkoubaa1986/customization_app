"""Copie de lignes LCI : les saisies suivent, la réponse du fournisseur reste (pur)."""
from __future__ import annotations

import unittest

from customization_app import lci_copie as C


class TestLigneCopiee(unittest.TestCase):
	ligne = {"name": "r1", "idx": 3, "parent": "LCI-1", "item_code": "SP-M", "item_name": "Osmoseur", "item_group": "RO",
	         "qty": 150, "uom": "Pièce", "volume_unitaire_m3": 0.0565, "description": "<p>3 porte-filtres</p>",
	         "item_name_traduit": "Domestic RO unit", "articles_additionnels": "[]", "prix_cible": 33.3, "moq": 0,
	         "prix_fournisseur": 36.3, "total_fournisseur": 5445, "ecart_pct": 9, "decision": "Accepté",
	         "remarque_fournisseur": "ok", "qty_fournisseur": 150, "qty_cible": 150, "observation": "x",
	         "repartition_conteneurs": "[1]", "volume_estime": 1}

	def test_saisies_copiees_reponse_non(self):
		c = C.ligne_copiee(self.ligne)
		self.assertEqual(c["item_code"], "SP-M")
		self.assertEqual(c["qty"], 150)
		self.assertEqual(c["prix_cible"], 33.3)
		self.assertEqual(c["volume_estime"], 1)
		for absent in ("name", "idx", "parent", "prix_fournisseur", "total_fournisseur", "ecart_pct", "decision",
		               "remarque_fournisseur", "qty_fournisseur", "qty_cible", "observation", "repartition_conteneurs"):
			self.assertNotIn(absent, c)
		self.assertEqual(c["moq"], 0)

	def test_article_libre(self):
		c = C.ligne_copiee({"item_code": None, "item_name": "Membrane 80 GPD", "qty": 10})
		self.assertEqual(c, {"item_name": "Membrane 80 GPD", "qty": 10})

	def test_ordre_de_la_liste(self):
		lignes = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
		self.assertEqual([l["name"] for l in C.lignes_dans_l_ordre(lignes, ["c", "a", "zz"])], ["a", "c"])
		self.assertEqual(C.lignes_dans_l_ordre(lignes, []), [])
