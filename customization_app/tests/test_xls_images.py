"""Extraction des images d'un .xls (BIFF8 / Escher) — flux fabriqués à la main."""
from __future__ import annotations

import struct
import unittest

from customization_app import xls_images as X

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def biff(rid, data):
    return struct.pack("<HH", rid, len(data)) + data


def esch(rtype, data, version=0, instance=0):
    return struct.pack("<HHI", (instance << 4) | version, rtype, len(data)) + data


def conteneur(rtype, enfants, instance=0):
    return esch(rtype, b"".join(enfants), version=0xF, instance=instance)


def fbse(blip):
    return esch(X.ESCHER_FBSE, b"\x00" * 36 + blip)


def blip_png(data):
    return esch(0xF01E, b"\x11" * 16 + b"\xff" + data, instance=0x6E0)


def forme(pib, col, row, col2, row2, dx1=0, dy1=0, dx2=0, dy2=0):
    opt = esch(X.ESCHER_OPT, struct.pack("<HI", X.PROP_PIB | 0x0000, pib))
    ancre = esch(X.ESCHER_CLIENT_ANCHOR, struct.pack("<9H", 0, col, dx1, row, dy1, col2, dx2, row2, dy2))
    return conteneur(X.ESCHER_SP_CONTAINER, [opt, ancre])


def classeur(images_magasin, formes_feuille, decouper=False):
    """Un flux Workbook minimal : globals (BOF + magasin) puis une feuille (BOF + dessin)."""
    magasin = conteneur(X.ESCHER_BSTORE, [fbse(blip_png(d)) for d in images_magasin])
    dessin = b"".join(formes_feuille)
    if decouper:
        m = len(dessin) // 2
        dessins = biff(X.MSODRAWING, dessin[:m]) + biff(X.CONTINUE, dessin[m:])
    else:
        dessins = biff(X.MSODRAWING, dessin)
    return (biff(X.BOF, b"\x00" * 16) + biff(X.MSODRAWINGGROUP, magasin)
            + biff(X.BOF, b"\x00" * 16) + dessins + biff(0x000A, b""))


class TestExtraction(unittest.TestCase):
    def test_magasin_et_formes(self):
        flux = classeur([PNG, PNG + b"2"], [forme(1, 2, 6, 2, 6, dx1=100, dy1=50), forme(2, 1, 7, 3, 9)])
        groupe, feuilles = X.dessins_par_feuille(flux)
        store = X.magasin(groupe)
        self.assertEqual([s[0] for s in store], ["png", "png"])
        self.assertEqual(store[1][1], PNG + b"2")
        self.assertEqual(len(feuilles), 1)
        f = X.formes(feuilles[0])
        self.assertEqual(f[0]["pib"], 1)
        self.assertEqual((f[0]["col"], f[0]["row"], f[0]["dx1"], f[0]["dy1"]), (2, 6, 100, 50))
        self.assertEqual((f[1]["col"], f[1]["row"], f[1]["col2"], f[1]["row2"]), (1, 7, 3, 9))

    def test_continue_rattache_au_dessin(self):
        flux = classeur([PNG], [forme(1, 0, 0, 0, 0), forme(1, 0, 1, 0, 1)], decouper=True)
        _g, feuilles = X.dessins_par_feuille(flux)
        self.assertEqual(len(X.formes(feuilles[0])), 2)

    def test_pib_hors_magasin_ignore(self):
        # une forme qui référence une image absente n'est pas rendue
        flux = classeur([PNG], [forme(5, 0, 0, 0, 0)])
        groupe, feuilles = X.dessins_par_feuille(flux)
        store = X.magasin(groupe)
        f = X.formes(feuilles[0])[0]
        self.assertFalse(0 < f["pib"] <= len(store))

    def test_images_depuis_flux_garde_les_decalages(self):
        flux = classeur([PNG], [forme(1, 2, 6, 2, 6, dx1=156, dy1=64, dx2=878, dy2=232)])
        feuilles = X.images_depuis_flux(flux)
        self.assertEqual(len(feuilles), 1)
        im = feuilles[0][0]
        self.assertEqual((im["col"], im["row"], im["dx1"], im["dy1"], im["dx2"], im["dy2"]), (2, 6, 156, 64, 878, 232))
        self.assertEqual((im["type"], im["data"]), ("png", PNG))
        # et la boîte qui en découle n'est pas dégénérée
        self.assertGreater(X.boite_pixels(im, [50, 60, 356], [20] * 6 + [476])[2], 200)

    def test_blip_sans_signature(self):
        self.assertIsNone(X._image_dans(b"\x00" * 100))
        self.assertEqual(X._image_dans(b"\x11" * 17 + b"\xff\xd8\xff\xe0abc")[0], "jpeg")


class TestBoite(unittest.TestCase):
    def test_image_dans_une_seule_cellule(self):
        # cellule C7 de 100 px × 400 px, image décalée d'un dixième, jusqu'aux 9/10
        boite = X.boite_pixels({"col": 2, "row": 6, "col2": 2, "row2": 6, "dx1": 102, "dy1": 26, "dx2": 922, "dy2": 230},
                               [50, 60, 100], [20] * 6 + [400])
        self.assertEqual(boite, (10.0, 40.6, 80.1, 318.8))

    def test_image_sur_plusieurs_cellules(self):
        boite = X.boite_pixels({"col": 1, "row": 0, "col2": 3, "row2": 2, "dx1": 0, "dy1": 0, "dx2": 512, "dy2": 128},
                               [50, 60, 70, 80], [10, 20, 30])
        # largeur : cols 1 et 2 entières (130) + moitié de la col 3 (40) ; hauteur : lignes 0 et 1 (30) + moitié de la 2 (15)
        self.assertEqual(boite, (0.0, 0.0, 170.0, 45.0))

    def test_cellule_inconnue_prend_le_defaut(self):
        boite = X.boite_pixels({"col": 9, "row": 9, "col2": 9, "row2": 9, "dx2": 1024, "dy2": 256}, [], [])
        self.assertEqual(boite, (0.0, 0.0, 64.0, 20.0))

    def test_conversions(self):
        self.assertEqual(X.largeur_colonne_px(2560), 75.0)
        self.assertEqual(X.hauteur_ligne_px(600), 40.0)
        self.assertEqual(X.largeur_colonne_px(None), 64.0)
        self.assertEqual(X.hauteur_ligne_px(0), 20.0)
