"""Tests des impressions Aramex de « Ma journée » — la partie PURE : une étiquette par pièce."""
from __future__ import annotations

import io
import unittest

from pypdf import PageObject, PdfReader, PdfWriter

from customization_app import aramex_journee as J


def _pdf(nb_pages=1, largeur=295, hauteur=432) -> bytes:
    w = PdfWriter()
    for _ in range(nb_pages):
        w.add_page(PageObject.create_blank_page(width=largeur, height=hauteur))
    tampon = io.BytesIO()
    w.write(tampon)
    return tampon.getvalue()


def _texte(page) -> str:
    return page.extract_text()


class TestEtiquettesParPiece(unittest.TestCase):
    def test_une_piece_une_etiquette_tamponnee_1_sur_1(self):
        pages = J.etiquettes_par_piece(_pdf(), 1)
        self.assertEqual(len(pages), 1)
        self.assertEqual(_texte(pages[0]).split(), ["Pièce", "1", "/", "1"])

    def test_trois_pieces_trois_etiquettes_numerotees(self):
        pages = J.etiquettes_par_piece(_pdf(), 3)
        self.assertEqual(len(pages), 3)
        self.assertEqual([_texte(p).split() for p in pages],
                         [["Pièce", "1", "/", "3"], ["Pièce", "2", "/", "3"], ["Pièce", "3", "/", "3"]])
        # Chaque copie garde la taille de l'étiquette (elle passe ensuite en grille A4).
        self.assertEqual((float(pages[2].mediabox.width), float(pages[2].mediabox.height)), (295.0, 432.0))

    def test_aramex_a_deja_rendu_une_page_par_piece(self):
        # Alors on ne touche a rien : il les numerote lui-meme.
        pages = J.etiquettes_par_piece(_pdf(nb_pages=2), 2)
        self.assertEqual(len(pages), 2)
        self.assertTrue(all("Pièce" not in _texte(p) for p in pages))

    def test_pieces_vides_ou_nulles_valent_une(self):
        self.assertEqual(_texte(J.etiquettes_par_piece(_pdf(), None)[0]).split(), ["Pièce", "1", "/", "1"])
        self.assertEqual(len(J.etiquettes_par_piece(_pdf(), 0)), 1)
        self.assertEqual(len(J.etiquettes_par_piece(_pdf(), "2")), 2)

    def test_les_copies_sont_independantes(self):
        # Trois PageObject distincts : les tamponner n'ecrit pas trois fois sur la meme page.
        pages = J.etiquettes_par_piece(_pdf(), 3)
        self.assertEqual(len({id(p) for p in pages}), 3)
        w = PdfWriter()
        for p in pages:
            w.add_page(p)
        tampon = io.BytesIO()
        w.write(tampon)
        relu = PdfReader(io.BytesIO(tampon.getvalue()))
        self.assertEqual(_texte(relu.pages[0]).split(), ["Pièce", "1", "/", "3"])
        self.assertEqual(_texte(relu.pages[2]).split(), ["Pièce", "3", "/", "3"])
