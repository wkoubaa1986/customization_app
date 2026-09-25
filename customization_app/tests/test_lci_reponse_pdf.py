"""Listes de prix PDF : fusion des tableaux, normalisation de la transcription IA, lecture d'un PDF."""
from __future__ import annotations

import os
import tempfile
import unittest

from customization_app import lci_reponse as R


class TestFusion(unittest.TestCase):
	def test_entete_repete_et_suite(self):
		t1 = (1, [["Model", "Description", "Unit price"], ["A-1", "Filter", "1.20"]])
		t2 = (2, [["Model", "Description", "Unit price"], ["A-2", "Housing", "3.50"]])      # en-tête répété
		t3 = (3, [["A-3", "Valve", "0.40"]])                                                 # suite sans en-tête
		t4 = (4, [["Terms", "Value"], ["Delivery", "30 days"]])                               # autre tableau
		g = R.fusionner_tables([t1, t2, t3, t4])
		self.assertEqual([n for n, _ in g], ["PDF p.1-3", "PDF p.4"])
		self.assertEqual(g[0][1], [["Model", "Description", "Unit price"], ["A-1", "Filter", "1.20"], ["A-2", "Housing", "3.50"], ["A-3", "Valve", "0.40"]])

	def test_vide(self):
		self.assertEqual(R.fusionner_tables([(1, [[None, ""]])]), [])

	def test_grille_ia(self):
		self.assertEqual(R.grille_ia({"header": ["Model", "Price"], "rows": [["A", "1"], ["B"]]}), [["Model", "Price"], ["A", "1"], ["B", ""]])
		self.assertEqual(R.grille_ia({"header": [], "rows": []}), [])
		self.assertEqual(R.grille_ia("n/a"), [])


class TestLecturePdf(unittest.TestCase):
	def test_tableau_dans_le_texte(self):
		try:
			import pymupdf
		except ImportError:
			self.skipTest("PyMuPDF absent")
		d = pymupdf.open(); page = d.new_page(width=595, height=842)
		entete = ["No", "Model", "Unit price USD", "Unit volume CBM"]
		lignes = [["1", "P-F-10'-O", "4.20", "0.0048"], ["2", "P-F-10'-T", "4.60", "0.0048"]]
		xs = [40, 80, 260, 400, 560]
		y = 80
		for k, row in enumerate([entete] + lignes):
			for j, c in enumerate(row):
				page.insert_text((xs[j] + 4, y + 14), c, fontsize=9)
			page.draw_line((xs[0], y), (xs[-1], y))
			y += 22
		page.draw_line((xs[0], y), (xs[-1], y))
		for x in xs:
			page.draw_line((x, 80), (x, y))
		with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
			f.write(d.tobytes()); chemin = f.name
		try:
			g = R.lire_pdf(chemin, ia=False)
		finally:
			os.unlink(chemin)
		self.assertEqual(len(g), 1)
		grille = g[0][1]
		self.assertEqual([R._txt(c) for c in grille[0]], entete)
		self.assertEqual(R._txt(grille[1][1]), "P-F-10'-O")
		self.assertEqual(len(grille), 3)


class _Row:
	def __init__(self, **k):
		self.__dict__.update(k)


class TestCandidatsImage(unittest.TestCase):
	def test_prefiltre_par_similarite(self):
		row = _Row(item_code="P-F-T-10'-O-SC", item_name="Porte filtre, triple bleu, 10' (Sans cartouches)", item_name_traduit="Triple blue housing 10\"")
		libres = [{"id": str(i), "code": "X%d" % i, "designation": "Widget %d" % i, "photo": ""} for i in range(30)]
		libres.append({"id": "T", "code": "PF-T-10-O-SC", "designation": "Triple stage housing blue 10\"", "photo": "three blue housings"})
		c = R._candidats_pour(row, libres, n=5)
		self.assertEqual(len(c), 5)
		self.assertEqual(c[0]["id"], "T")
		self.assertEqual(len(R._candidats_pour(row, libres[:3], n=5)), 3)
