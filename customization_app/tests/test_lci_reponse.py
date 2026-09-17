"""Tests de l'import de la réponse fournisseur dans une Liste Commande Import
(demande utilisateur 16/09/2026).

Convention : `unittest.TestCase` pur, aucune base. Ce qui se teste ici est la
partie qui se trompe en silence : lire un nombre écrit à l'européenne ou à
l'américaine, reconnaître la bonne colonne quand deux mots-clés se disputent
un en-tête, et surtout N'APPARIER QUE CE QUI EST PROUVÉ — un prix collé sur le
mauvais article ne se voit qu'à la facture.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from openpyxl import Workbook

import frappe

from customization_app import lci_reponse as R


def _classeur(lignes, titre="QUOTATION", avant=2, autres=None):
    """Écrit un vrai .xlsx temporaire et rend ses feuilles, comme à l'import.

    Passer par le disque plutôt que par un classeur en mémoire fait traverser
    aux tests le même chemin que le fichier reçu du fournisseur.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = titre
    for i in range(avant):
        ws.append([f"préambule {i + 1}"])
    for l in lignes:
        ws.append(l)
    for nom, contenu in (autres or []):
        ws2 = wb.create_sheet(nom, 0)
        for l in contenu:
            ws2.append(l)
    fd, chemin = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(chemin)
        return R._feuilles(chemin)
    finally:
        os.unlink(chemin)


def _ligne(name, idx, code="", nom="", qty=0.0, traduit=""):
    return frappe._dict(name=name, idx=idx, item_code=code, item_name=nom,
                        item_name_traduit=traduit, description="", qty=qty)


class TestLectureDesNombres(unittest.TestCase):
    def test_les_deux_conventions_decimales(self):
        self.assertEqual(R._num("1,234.56"), 1234.56)   # américaine
        self.assertEqual(R._num("1 234,56"), 1234.56)   # européenne
        self.assertEqual(R._num("0,38"), 0.38)          # virgule décimale
        self.assertEqual(R._num("1,234"), 1234.0)       # virgule de millier

    def test_le_prix_se_lit_malgre_la_devise_et_l_unite(self):
        self.assertEqual(R._num("USD 0.45"), 0.45)
        self.assertEqual(R._num("$0,45/pc"), 0.45)

    def test_une_cellule_sans_nombre_ne_rend_rien(self):
        for v in ("", None, "N/A", "à confirmer"):
            self.assertIsNone(R._num(v), v)

    def test_zero_reste_zero(self):
        # 0 n'est pas « pas de valeur » : un prix à 0 doit remonter tel quel
        self.assertEqual(R._num(0), 0.0)
        self.assertEqual(R._num("0"), 0.0)


class TestVolumeDepuisDimensions(unittest.TestCase):
    def test_les_ecritures_courantes_du_carton(self):
        for texte in ("54*36*40", "54*36*40cm", "54 x 36 x 40 cm",
                      "540*360*400mm", "0.54 x 0.36 x 0.40 m"):
            self.assertAlmostEqual(R._volume_depuis_dimensions(texte), 0.07776, places=5)

    def test_sans_unite_l_ordre_de_grandeur_tranche(self):
        # au-delà de 5 la mesure est en cm, en deçà elle est déjà en mètres
        self.assertAlmostEqual(R._volume_depuis_dimensions("100*100*100"), 1.0, places=6)
        self.assertAlmostEqual(R._volume_depuis_dimensions("0.5*0.4*0.3"), 0.06, places=6)

    def test_un_texte_sans_dimensions_ne_rend_rien(self):
        self.assertIsNone(R._volume_depuis_dimensions("carton standard"))


class TestReperageDuTableau(unittest.TestCase):
    def test_l_entete_est_trouve_sous_le_preambule(self):
        feuilles = _classeur([
            ["NO.", "Model No.", "Description", "Q'ty", "Unit Price(USD)"],
            [1, "A-1", "Elbow", 10, 0.5],
            [2, "A-2", "Tee", 10, 0.6],
        ])
        feuille, i, grille = R._trouver_tableau(feuilles)
        self.assertEqual(feuille, "QUOTATION")
        self.assertEqual(i, 2)   # 0-based : les deux lignes de préambule sautées
        self.assertEqual(R._txt(grille[i][0]), "NO.")

    def test_la_feuille_de_garde_est_ignoree(self):
        feuilles = _classeur(
            [["NO.", "Model No.", "Description", "Q'ty", "Unit Price(USD)"],
             [1, "A-1", "Elbow", 10, 0.5]],
            autres=[("Cover", [["ANG RAN WATER TREATMENT CO., LTD"]])])
        self.assertEqual(R._trouver_tableau(feuilles)[0], "QUOTATION")


class TestMappageDesColonnes(unittest.TestCase):
    def _map(self, entete, exemples=None):
        return R._mapper_colonnes(entete, exemples or [])

    def test_un_entete_de_fournisseur_chinois(self):
        m = self._map(["NO.", "Model No.", "Description", "Q'ty", "Unit Price(USD)",
                       "Amount", "MOQ", "PCS/CTN", "Carton Size(cm)", "Remark"])
        self.assertEqual(m["ref_ligne"], 0)
        self.assertEqual(m["code"], 1)
        self.assertEqual(m["designation"], 2)
        self.assertEqual(m["qty"], 3)
        self.assertEqual(m["prix_unitaire"], 4)
        self.assertEqual(m["total"], 5)
        self.assertEqual(m["moq"], 6)
        self.assertEqual(m["pcs_carton"], 7)
        self.assertEqual(m["dimensions_carton"], 8)
        self.assertEqual(m["remarque"], 9)

    def test_le_diese_de_notre_propre_fichier(self):
        # « # » ne survit pas à la normalisation : sans traitement dédié, la
        # colonne de numéro de NOTRE fichier renvoyé passerait inaperçue.
        m = self._map(["#", "Image", "Item name", "Description", "Quantity",
                       "UOM", "Unit volume (m³)", "Line volume (m³)",
                       "Target price (USD)", "Unit price (USD)", "Total"])
        self.assertEqual(m["ref_ligne"], 0)
        # « Target price » est NOTRE colonne : le prix du fournisseur est la
        # suivante. Les confondre rendrait un écart nul sur toute la liste.
        self.assertEqual(m["prix_cible_envoye"], 8)
        self.assertEqual(m["prix_unitaire"], 9)

    def test_le_mot_cle_le_plus_long_l_emporte(self):
        # « Unit volume » doit battre « volume », sinon le volume unitaire
        # serait pris pour un volume total et divisé par la quantité.
        m = self._map(["Item name", "Unit volume (m³)", "Line volume (m³)",
                       "Unit price"])
        self.assertEqual(m.get("volume_unitaire_m3"), 1)
        self.assertNotEqual(m.get("volume_total_m3"), 1)
        # « Total CBM » est un volume, pas un montant
        m2 = self._map(["Item", "Qty", "Unit Price", "Total CBM"])
        self.assertEqual(m2.get("volume_total_m3"), 3)
        self.assertIsNone(m2.get("total"))

    def test_une_colonne_ne_sert_qu_a_un_role(self):
        m = self._map(["Description", "Item name", "Price"])
        self.assertEqual(len(set(m.values())), len(m))


class TestExtraction(unittest.TestCase):
    def _lire(self, lignes):
        feuille, i, grille = R._trouver_tableau(_classeur(lignes))
        m = R._mapper_colonnes(grille[i], grille[i + 1:i + 6])
        return R._extraire(grille, i, m, feuille)

    def test_le_carton_donne_le_volume(self):
        out = self._lire([
            ["NO.", "Model No.", "Description", "Q'ty", "Unit Price(USD)",
             "PCS/CTN", "Carton Size(cm)"],
            [1, "A-1", "Elbow", 150, 0.35, 200, "54*36*40"],
        ])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["volume_carton_m3"], 0.07776, places=5)
        self.assertEqual(out[0]["pcs_carton"], 200)

    def test_la_ligne_de_total_n_est_pas_un_article(self):
        out = self._lire([
            ["NO.", "Model No.", "Description", "Q'ty", "Unit Price(USD)"],
            [1, "A-1", "Elbow", 150, 0.35],
            ["", "", "TOTAL", "", ""],
            ["", "", "", "", ""],
        ])
        self.assertEqual([o["code"] for o in out], ["A-1"])

    def test_un_cbm_global_se_ramene_a_l_unite(self):
        out = self._lire([
            ["NO.", "Model No.", "Description", "Q'ty", "Unit Price(USD)", "CBM"],
            [1, "A-1", "Elbow", 100, 0.35, 5.0],
        ])
        self.assertAlmostEqual(out[0]["volume_unitaire_m3"], 0.05, places=6)


class TestAppariement(unittest.TestCase):
    """L'IA est neutralisée : on vérifie les règles déterministes seules."""

    def setUp(self):
        self._ia = R._apparier_ia
        R._apparier_ia = lambda rows, libres: {}

    def tearDown(self):
        R._apparier_ia = self._ia

    def _src(self, i, code="", desig="", prix=1.0, ref=None):
        return {"id": f"S:{i}", "feuille": "S", "ligne": i, "ref_ligne": ref,
                "code": code, "designation": desig, "qty": None,
                "prix_unitaire": prix, "total": None, "moq": None,
                "pcs_carton": None, "volume_carton_m3": None,
                "volume_unitaire_m3": None, "remarque": ""}

    def test_le_code_article_prime(self):
        rows = [_ligne("r1", 1, code="Acc004-E", nom="Coude"),
                _ligne("r2", 2, code="Acc008-D", nom="Connecteur")]
        srcs = [self._src(5, "Acc008-D", "Straight connector"),
                self._src(6, "acc004-e", "Elbow with joint")]
        res, libres = R._apparier(rows, srcs)
        self.assertEqual(res["r1"][0]["ligne"], 6)   # casse ignorée
        self.assertEqual(res["r2"][0]["ligne"], 5)
        self.assertEqual(libres, [])

    def test_la_numerotation_seule_ne_suffit_pas(self):
        # le fournisseur numérote SON tableau : aligner par position collerait
        # des prix sur les mauvais articles.
        rows = [_ligne(f"r{i}", i, nom=n) for i, n in enumerate(
            ["Pompe doseuse", "Membrane 100 GPD", "Vanne 3 voies", "Charbon actif"], 1)]
        srcs = [self._src(i + 4, f"X-{i}", d, ref=i) for i, d in enumerate(
            ["Steel bracket", "Rubber gasket", "Plastic clip", "Copper wire"], 1)]
        res, libres = R._apparier(rows, srcs)
        self.assertEqual(res, {})
        self.assertEqual(len(libres), 4)

    def test_la_numerotation_suivie_quand_le_contenu_la_confirme(self):
        # notre fichier nous revient : mêmes numéros ET mêmes désignations
        noms = ["Pompe doseuse", "Membrane 100 GPD", "Vanne 3 voies", "Charbon actif"]
        rows = [_ligne(f"r{i}", i, nom=n) for i, n in enumerate(noms, 1)]
        srcs = [self._src(i + 4, "", n, ref=i) for i, n in enumerate(noms, 1)]
        res, libres = R._apparier(rows, srcs)
        self.assertEqual(len(res), 4)
        self.assertEqual({m for _s, m, _c in res.values()}, {"numéro de ligne"})
        self.assertEqual(libres, [])

    def test_une_designation_approchante_est_acceptee(self):
        rows = [_ligne("r1", 1, nom="Elbow with joint 3/8 - M 1/2")]
        srcs = [self._src(5, "", "Elbow with joint 3/8-M1/2")]
        res, _libres = R._apparier(rows, srcs)
        self.assertEqual(res["r1"][1], "désignation approchante")

    def test_deux_candidats_equivalents_sont_refuses(self):
        # deux articles qui ne diffèrent que par un chiffre : sans certitude,
        # mieux vaut ne rien écrire et laisser l'utilisateur trancher.
        rows = [_ligne("r1", 1, nom="Connecteur droit 1/4")]
        srcs = [self._src(5, "", "Connecteur droit 1/2"),
                self._src(6, "", "Connecteur droit 3/8")]
        res, libres = R._apparier(rows, srcs)
        self.assertEqual(res, {})
        self.assertEqual(len(libres), 2)

    def test_une_ligne_fournisseur_ne_sert_qu_une_fois(self):
        rows = [_ligne("r1", 1, nom="Elbow with joint"),
                _ligne("r2", 2, nom="Elbow with joint")]
        srcs = [self._src(5, "", "Elbow with joint")]
        res, libres = R._apparier(rows, srcs)
        self.assertEqual(len(res), 1)
        self.assertEqual(libres, [])

    def test_la_traduction_sert_a_l_appariement(self):
        rows = [_ligne("r1", 1, nom="Coude fileté avec joint",
                       traduit="Elbow with joint 1/4-M1/2")]
        srcs = [self._src(5, "", "Elbow with joint 1/4-M1/2")]
        res, _libres = R._apparier(rows, srcs)
        self.assertEqual(res["r1"][0]["ligne"], 5)


class TestGardeDesChampsEcrits(unittest.TestCase):
    def test_seuls_les_champs_de_la_reponse_sont_ecrivables(self):
        # l'aperçu vient du navigateur : il ne doit pas pouvoir écrire ailleurs
        self.assertEqual(R.CHAMPS_APPLICABLES, {
            "prix_fournisseur", "qty_fournisseur", "moq", "qty_par_carton",
            "volume_carton_m3", "volume_unitaire_m3", "remarque_fournisseur",
            "total_fichier"})
        # « qty_cible » et « prix_cible_negocie » sont NOS arbitrages : un
        # fichier fournisseur ne doit jamais pouvoir les écrire.
        for interdit in ("qty", "item_code", "prix_cible", "decision", "idx",
                         "qty_cible", "prix_cible_negocie", "observation"):
            self.assertNotIn(interdit, R.CHAMPS_APPLICABLES)


class TestFactureReelle(unittest.TestCase):
    """Pièges relevés sur la facture Ang Ran du 01/09/2026 (.xls, 105 lignes).

    Aucun code article, un code DOUANIER identique partout, le modèle rangé
    sous un en-tête « Picture », le CBM donné pour la ligne entière, et un
    pied de tableau dont les cellules valent « / ».
    """

    ENTETE = ["", "HS code", "Picture", "Description of Goods", "Pieces",
              "Unit Price\n(USD)", "Total Amount\n(USD)", "CBM", "Weight", ""]
    LIGNES = [
        ["", "8421211000", "\nMODEL A.1\n-5 STAGES", "Reverse Osmosis System",
         150, 36.3, 5445.0, 8.4693, 1350, 150],
        ["", "8421211000", "\nMODEL B.2\n7 STAGES", "Reverse Osmosis System",
         600, 40.1, 24060, 33.8772, 6900, 600],
        ["", "8421991000", "", "Flow meter 100 L/h 1/2\" thread", 10, 10.8, 108, 0.01, 4, 1],
        ["", "8421991000", "", "1\" Y-Strainer", 50, "", 0, "", "", ""],
        ["", "Total ", "/", "", 38093, "/", 116566.2, 165.16, 30498.1, 2474.3],
    ]

    def setUp(self):
        feuilles = _classeur([self.ENTETE] + self.LIGNES)
        self.feuille, self.i, self.grille = R._trouver_tableau(feuilles)
        self.mapping = R._mapper_colonnes(self.grille[self.i],
                                          self.grille[self.i + 1:self.i + 6])
        self.rows = R._extraire(self.grille, self.i, self.mapping, self.feuille)

    def test_l_entete_n_est_pas_la_premiere_ligne_de_donnees(self):
        # « # » se normalise en chaîne vide et « "" in n » est toujours vrai :
        # sans garde-fou, chaque cellule remplie marquait un point et la ligne
        # la plus remplie — une ligne de DONNÉES — devenait l'en-tête.
        self.assertEqual(R._txt(self.grille[self.i][1]), "HS code")

    def test_le_code_douanier_n_est_pas_un_code_article(self):
        self.assertEqual(self.mapping.get("hs_code"), 1)
        self.assertIsNone(self.mapping.get("code"))
        self.assertEqual([r["code"] for r in self.rows], ["", "", "", ""])

    def test_pieces_est_une_quantite(self):
        self.assertEqual(self.mapping["qty"], 4)
        self.assertEqual(self.rows[0]["qty"], 150)

    def test_le_modele_egare_complete_la_designation(self):
        # « Description of Goods » répète « Reverse Osmosis System » : sans le
        # modèle, deux articles distincts seraient indiscernables.
        self.assertIn(2, R._colonnes_texte_libres(self.grille, self.i, self.mapping))
        self.assertIn("MODEL A.1", self.rows[0]["designation"])
        self.assertIn("MODEL B.2", self.rows[1]["designation"])
        self.assertNotEqual(self.rows[0]["designation"], self.rows[1]["designation"])

    def test_le_cbm_de_la_ligne_donne_le_volume_unitaire(self):
        self.assertAlmostEqual(self.rows[0]["volume_unitaire_m3"], 8.4693 / 150, places=6)

    def test_le_pied_de_tableau_n_est_pas_un_article(self):
        # ses cellules valent « / » : repris tels quels, ils feraient une ligne
        # de 38 093 pièces sans prix.
        self.assertEqual(len(self.rows), 4)
        self.assertNotIn(38093, [r["qty"] for r in self.rows])

    def test_une_ligne_non_chiffree_reste_sans_prix(self):
        y = [r for r in self.rows if "Y-Strainer" in r["designation"]][0]
        self.assertIsNone(y["prix_unitaire"])
        self.assertEqual(y["qty"], 50)


class TestEstTexte(unittest.TestCase):
    def test_une_reference_alphanumerique_est_du_texte(self):
        # `_num` rend 1.0 pour « MODEL A.1 » : il ne peut pas servir de test
        for v in ("MODEL A.1 -5 STAGES", "Flow meter 100 L/h", "PP 10\" 5µ"):
            self.assertTrue(R._est_texte(v), v)

    def test_un_nombre_ou_un_tiret_n_est_pas_du_texte(self):
        for v in ("150", "36.3", "1 234,56", "/", "-", "", None, 150):
            self.assertFalse(R._est_texte(v), v)
