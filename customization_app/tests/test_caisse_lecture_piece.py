"""Lecture d'un chèque / d'une traite photographié (demande utilisateur 30/10/2026).

Trois règles :
  1. l'aperçu de la pièce reste VERROUILLÉ tant que la lecture automatique n'a pas abouti ;
  2. une lecture qui échoue (panne, photo illisible, rien de lisible) rend la main à l'employé
     SANS rien pré-remplir — saisie manuelle seule ;
  3. le montant lu est confronté au montant saisi, à chaque point de capture, et la dépense de
     caisse offre de reprendre le montant lu comme montant payé.

Convention : `unittest.TestCase` pur, sur le texte du dialogue (aucune base).
"""
from __future__ import annotations

import os
import unittest

DIALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "customize_erpnext", "page", "caisse_journaliere",
                        "caisse_journaliere.js")


def _js():
    with open(DIALOGUE, encoding="utf-8") as f:
        return f.read()


def _fonction(js, nom):
    a = js.index("function %s(" % nom)
    b = js.index("\nfunction ", a + 1)
    return js[a:b]


class TestApercuVerrouille(unittest.TestCase):
    def setUp(self):
        self.js = _js()
        self.fn = _fonction(self.js, "rcj_apercu_fichier")

    def test_l_apercu_sait_se_verrouiller(self):
        self.assertIn("function rcj_apercu_fichier(dataUrl, nom, info_html, options)", self.fn)
        self.assertIn("static: !!options.verrou", self.fn)
        self.assertIn("liberer };", self.fn)
        self.assertIn("d.get_close_btn().show();", self.fn)

    def test_la_lecture_verrouille_puis_libere(self):
        fn = _fonction(self.js, "rcj_photo_piece_ajoutee")
        self.assertIn("{ verrou: true }", fn)
        # Libéré dans les trois issues : lecture réussie, lecture échouée, panne réseau.
        self.assertGreaterEqual(fn.count("liberer();"), 2)
        self.assertIn('error: () => manuel(', fn)


class TestRepliManuel(unittest.TestCase):
    def setUp(self):
        self.fn = _fonction(_js(), "rcj_photo_piece_ajoutee")

    def test_une_lecture_qui_echoue_ne_pre_remplit_rien(self):
        self.assertIn("if (lu.erreur) return manuel(lu.erreur);", self.fn)
        self.assertIn("if (lu.lisible === false || (!lu.numero && !lu.montant)) return manuel(", self.fn)
        # Les sorties « manuel » précèdent toute application des champs lus.
        self.assertLess(self.fn.index("return manuel("), self.fn.index("appliquer(lu)"))
        self.assertIn("aucun champ pré-rempli", self.fn)


class TestComparaisonDuMontant(unittest.TestCase):
    def setUp(self):
        self.js = _js()
        self.fn = _fonction(self.js, "rcj_photo_piece_ajoutee")

    def test_la_tolerance_de_lecture(self):
        self.assertIn("const RCJ_TOLERANCE_LECTURE = 0.05;", self.js)
        self.assertIn("lu.ecart_montant = flt(flt(lu.montant) - att, 3);", self.fn)
        self.assertIn("Math.abs(lu.ecart_montant) <= RCJ_TOLERANCE_LECTURE", self.fn)

    def test_le_montant_attendu_se_lit_avant_d_appliquer(self):
        self.assertLess(self.fn.index("const att = "), self.fn.index("appliquer(lu)"))

    def test_chaque_point_de_capture_passe_son_montant(self):
        # 4 lignes de règlement (dettes, Aramex, fractionné, règlement fournisseur), le
        # dialogue « Payer une dépense », la dépense de caisse.
        self.assertEqual(self.js.count("}, () => flt(p.montant));"), 4)
        self.assertEqual(self.js.count('}, () => flt(d.get_value("montant")));'), 1)
        self.assertEqual(self.js.count("}, attendu_piece));"), 1)

    def test_la_depense_offre_de_reprendre_le_montant_lu(self):
        bloc = _fonction(self.js, "rcj_depense")
        self.assertIn("rcj-reprendre-lu", bloc)
        self.assertIn('d.set_value("montant_paye", flt(lu.montant));', bloc)
        self.assertIn("Le chèque semble au BRUT", bloc)
        self.assertIn("function attendu_piece()", bloc)
