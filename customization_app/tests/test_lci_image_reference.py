"""Image de référence d'une ligne LCI (03/11/2026) : « ajouter une image à un article pour servir de
référence, surtout dans la génération de PDF ». Elle s'ajoute à la photo, elle ne la remplace pas, et
elle est étiquetée dans la langue du fournisseur. Pur : libellés, cellule HTML, champs copiés."""
from __future__ import annotations

import pathlib
import re
import unittest

from customization_app import lci_copie, lci_export_fournisseur, lci_pdf_conteneurs, liste_commande_import as LCI

JS = pathlib.Path(__file__).resolve().parents[1] / "customize_erpnext/doctype/liste_commande_import/liste_commande_import.js"
JSON_ARTICLE = (pathlib.Path(__file__).resolve().parents[1]
                / "customize_erpnext/doctype/liste_commande_import_article/liste_commande_import_article.json")


class TestLibelles(unittest.TestCase):
	def test_reference_dans_les_cinq_langues_des_trois_exports(self):
		for nom, labels in (("cotation", LCI.EXPORT_LABELS), ("conteneurs", lci_pdf_conteneurs.LABELS),
		                    ("fournisseur", lci_export_fournisseur.LABELS)):
			self.assertEqual(len(labels), 5, nom)
			for langue, lab in labels.items():
				self.assertTrue(lab.get("reference"), f"{nom} / {langue}")

	def test_etiquette_courte_en_arabe_et_en_francais(self):
		self.assertEqual(LCI.EXPORT_LABELS["Français"]["reference"], "Réf.")
		self.assertEqual(LCI.EXPORT_LABELS["العربية"]["reference"], "مرجع")


class TestCelluleHtml(unittest.TestCase):
	def test_sans_image_rien(self):
		self.assertEqual(LCI._imgs_html(None, None, "Réf."), "")

	def test_photo_seule_inchangee(self):
		html = LCI._imgs_html("data:a", None, "Réf.")
		self.assertEqual(html.count("<img"), 1)
		self.assertNotIn("Réf.", html)

	def test_reference_ajoutee_et_etiquetee_pas_substituee(self):
		html = LCI._imgs_html("data:a", "data:b", "Réf.", 60)
		self.assertEqual(html.count("<img"), 2)
		self.assertLess(html.index("data:a"), html.index("data:b"))
		self.assertIn("Réf.", html)
		self.assertIn("max-width:60px", html)

	def test_reference_sans_photo(self):
		html = LCI._imgs_html(None, "data:b", "Ref.")
		self.assertEqual(html.count("<img"), 1)
		self.assertIn("Ref.", html)

	def test_etiquette_echappee(self):
		self.assertIn("&lt;b&gt;", LCI._imgs_html(None, "data:b", "<b>"))


class TestChampEtCopie(unittest.TestCase):
	def test_champ_du_doctype(self):
		texte = JSON_ARTICLE.read_text()
		self.assertIn('"fieldname": "image_reference"', texte)
		self.assertIn('"fieldtype": "Attach Image"', texte)

	def test_copie_de_ligne_emporte_la_reference(self):
		self.assertIn("image_reference", lci_copie.CHAMPS_COPIES)
		c = lci_copie.ligne_copiee({"item_name": "X", "image": "/files/a.jpg", "image_reference": "/files/ref.jpg"})
		self.assertEqual(c["image_reference"], "/files/ref.jpg")

	def test_miroir_js(self):
		js = JS.read_text()
		m = re.search(r"const LCI_CHAMPS_COPIES = \[(.*?)\];", js, re.S)
		self.assertIsNotNone(m)
		self.assertEqual(tuple(re.findall(r'"([^"]+)"', m.group(1))), lci_copie.CHAMPS_COPIES)
		self.assertIn('data-act="imgref"', js)
		self.assertIn('"image_reference"', js)
