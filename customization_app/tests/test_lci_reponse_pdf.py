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


class TestResoudreChoix(unittest.TestCase):
	def test_une_source_par_ligne_meilleure_confiance(self):
		out = R.resoudre_choix([("a", "S1", 0.7), ("b", "S1", 0.9), ("c", "S2", 0.4), ("d", None, 0.9), ("e", "S3", 0.55)])
		self.assertEqual(out, {"b": ("S1", 0.9), "e": ("S3", 0.55)})
		self.assertEqual(R.resoudre_choix([]), {})


class TestHeritageFusion(unittest.TestCase):
	"""Cellules fusionnées d'un catalogue : les variantes de raccord héritent du produit au-dessus."""

	def _l(self, **k):
		base = {"code": "", "designation": "", "designation_base": "", "complement": "", "prix_unitaire": None,
		        "photo": "", "_photo": None, "moq": None, "pcs_carton": None, "volume_carton_m3": None, "volume_unitaire_m3": None}
		base.update(k)
		return base

	def test_variantes_heritent_code_description_photo(self):
		lignes = [
			self._l(code="NW-BR5B-PET", designation="Single stage 5 inch — 1/2\" Brass Port", designation_base="Single stage 5 inch",
			        complement="1/2\" Brass Port", prix_unitaire=2.99, _photo=b"jpg"),
			self._l(designation="3/4\" Brass Port", prix_unitaire=3.15),
			self._l(designation="1\" Brass Port", prix_unitaire=3.25),
			self._l(code="NW-BR7B", designation="7 inch", designation_base="7 inch", prix_unitaire=3.49),
			self._l(designation="A long description of another product without any code at all here", prix_unitaire=9.0),
		]
		out = R.heriter_fusion(lignes)
		self.assertEqual(out[0]["variante"], "1/2\" Brass Port")
		self.assertEqual(out[1]["code"], "NW-BR5B-PET")
		self.assertEqual(out[1]["designation"], "Single stage 5 inch — 3/4\" Brass Port")
		self.assertEqual(out[1]["variante"], "3/4\" Brass Port")
		self.assertEqual(out[1]["_photo"], b"jpg")
		self.assertEqual(out[2]["code"], "NW-BR5B-PET")
		self.assertEqual(out[3]["variante"], "")
		self.assertEqual(out[4]["code"], "")   # trop long pour une variante : produit à part

	def test_sans_prix_pas_de_variante(self):
		lignes = [self._l(code="X", designation="Base", designation_base="Base", prix_unitaire=1.0),
		          self._l(designation="note", prix_unitaire=None)]
		self.assertEqual(R.heriter_fusion(lignes)[1]["code"], "")


class TestPreselection(unittest.TestCase):
	def test_catalogue_compact_et_alias(self):
		libres = [{"id": "f.pdf › PDF p.1:3", "code": "A", "designation": "Big blue 20\"\nhousing", "prix_unitaire": 12.5},
		          {"id": "f.pdf › PDF p.1:4", "code": "", "designation": "x" * 500, "prix_unitaire": None}]
		texte, alias = R.catalogue_court(libres)
		self.assertEqual(alias, {"S1": "f.pdf › PDF p.1:3", "S2": "f.pdf › PDF p.1:4"})
		lignes = texte.split("\n")
		self.assertEqual(lignes[0], "S1 | A | Big blue 20\" housing | 12.5")
		self.assertTrue(lignes[1].startswith("S2 |  | xxx"))
		self.assertLess(len(lignes[1]), R.DESIG_MAX_PRESEL + 20)

	def test_lecture_reponse(self):
		alias = {"S1": "a", "S2": "b", "S3": "c"}
		rep = {"L1": ["S2", "S9", " S1", "S2"], "L2": [], "L3": "S1"}
		out = R.lire_preselection(rep, ["L1", "L2", "L3", "L4"], alias)
		self.assertEqual(out, {"L1": ["b", "a"], "L2": []})   # L3 illisible et L4 absente : pas de clé
		self.assertEqual(R.lire_preselection("n'importe quoi", ["L1"], alias), {})

	def test_alias_choisi(self):
		cands = [{"id": "x"}, {"id": "y"}]
		self.assertEqual(R.alias_choisi({"id": "C2", "confiance": 0.8}, cands), ("y", 0.8))
		self.assertEqual(R.alias_choisi({"id": "y", "confiance": "0.6"}, cands), ("y", 0.6))
		self.assertEqual(R.alias_choisi({"id": "C7", "confiance": 0.9}, cands), (None, 0.9))
		self.assertEqual(R.alias_choisi({"id": None}, cands), (None, 0))
		self.assertEqual(R.alias_choisi(None, cands), (None, 0))

	def test_payload_vision_alias_et_variante(self):
		import json
		row = _Row(name="r1", item_code="P", item_name="Porte filtre", item_name_traduit="", description="", qty=2, image=None)
		cands = [{"id": "long id", "code": "NW", "designation": "Base — 3/4\" Brass Port", "designation_base": "Base",
		          "variante": "3/4\" Brass Port", "prix_unitaire": 3.15}]
		p = json.loads(R._payload_image(row, cands))
		self.assertEqual(p["candidats"][0]["id"], "C1")
		self.assertEqual(p["candidats"][0]["designation"], "Base")
		self.assertEqual(p["candidats"][0]["variante"], "3/4\" Brass Port")


class TestPhotoPdf(unittest.TestCase):
	def test_ligne_pdf_survit_a_la_fusion_et_photo_decoupee(self):
		try:
			import pymupdf
		except ImportError:
			self.skipTest("PyMuPDF absent")
		d = pymupdf.open(); page = d.new_page(width=595, height=842)
		entete = ["Model", "Photo", "Description", "USD Price"]
		lignes = [["NW-1", "", "5 inch housing", "2.99"], ["", "", "3/4\" Brass Port", "3.15"]]
		xs = [40, 120, 220, 420, 560]
		y = 80
		for k, row in enumerate([entete] + lignes):
			h = 22 if k == 0 else 60
			for j, c in enumerate(row):
				if c:
					page.insert_text((xs[j] + 4, y + 14), c, fontsize=9)
			if k == 1:   # un « logo » dessiné dans la cellule photo
				page.draw_rect(pymupdf.Rect(xs[1] + 10, y + 10, xs[2] - 10, y + h - 10), color=(0, 0, 1), fill=(0, 0, 1))
			page.draw_line((xs[0], y), (xs[-1], y))
			y += h
		page.draw_line((xs[0], y), (xs[-1], y))
		for x in xs:
			page.draw_line((x, 80), (x, y))
		with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
			f.write(d.tobytes()); chemin = f.name
		try:
			g = R.lire_pdf(chemin, ia=False)
			self.assertIsInstance(g[0][1][1], R.LignePdf)
			self.assertIsNotNone(g[0][1][1].page)
			lu = R._lire_grilles_pdf(g)
		finally:
			os.unlink(chemin)
		self.assertIsNotNone(lu)
		lignes_lues = lu[4]
		self.assertEqual(len(lignes_lues), 2)
		self.assertEqual(lignes_lues[0]["code"], "NW-1")
		self.assertTrue(lignes_lues[0]["_photo"] and lignes_lues[0]["_photo"][:2] == b"\xff\xd8")   # JPEG
		self.assertEqual(lignes_lues[1]["code"], "NW-1")   # variante héritée
		self.assertEqual(lignes_lues[1]["designation"], "5 inch housing — 3/4\" Brass Port")
		self.assertEqual(lignes_lues[1]["_photo"], lignes_lues[0]["_photo"])
