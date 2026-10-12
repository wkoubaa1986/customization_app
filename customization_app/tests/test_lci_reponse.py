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


class TestCaracteristiques(unittest.TestCase):
    """12/10/2026 : la ressemblance des lettres ne voit pas qu'un chiffre change le produit (1/4" ≠ 3/8" à 88 %)."""

    def test_valeurs_lues(self):
        self.assertEqual(R.specs('Électrovanne 24 V tube 1/4" x 1/4"'), {"V": {"24"}, "pouce": {"1/4"}})
        self.assertEqual(R.specs("AutoFlush 24V 300 cc retard 18 s"), {"V": {"24"}, "cc": {"300"}})
        self.assertEqual(R.specs("Membrane 75 GPD, 5 microns, 6 stages, UV 6W, tank 3.2 L"),
                         {"GPD": {"75"}, "µm": {"5"}, "étages": {"6"}, "W": {"6"}, "L": {"3.2"}})
        self.assertEqual(R.specs("livré le 12/10/2026, code L153"), {})              # ni date ni code

    def test_conflits(self):
        fr = R.specs('Électrovanne 24 V tube 1/4" x 1/4"')
        self.assertEqual(R.conflits_specs(fr, R.specs('24V inlet solenoid valve, 3/8" x 3/8"')), ['1/4" ≠ 3/8"'])
        self.assertEqual(R.conflits_specs(R.specs("AutoFlush 24 V 300 cc"), R.specs("AutoFlush valve 24V 200cc")), ["300 cc ≠ 200 cc"])
        # une valeur commune suffit ; une grandeur dite d'un seul côté ne prouve rien
        self.assertEqual(R.conflits_specs(R.specs('Clé porte-filtre 10"'), R.specs('10" filter housing wrench for 1/4" RO')), [])
        self.assertEqual(R.conflits_specs(R.specs("Cartouche sédiment 10\""), R.specs("PP cartridge 5 micron")), [])


class TestApprochanteEtValeurs(unittest.TestCase):
    def setUp(self):
        self._ia = R._apparier_ia
        R._apparier_ia = lambda rows, libres: {}

    def tearDown(self):
        R._apparier_ia = self._ia

    def _src(self, i, desig):
        return {"id": f"S:{i}", "feuille": "S", "ligne": i, "ref_ligne": None, "code": "", "designation": desig,
                "qty": None, "prix_unitaire": 1.0, "total": None, "moq": None, "pcs_carton": None,
                "volume_carton_m3": None, "volume_unitaire_m3": None, "remarque": ""}

    def test_la_mauvaise_variante_seule_n_est_pas_prise(self):
        rows = [_ligne("r1", 1, nom='Électrovanne 24 V tube 1/4" x 1/4"', traduit='24V solenoid valve tube 1/4" x 1/4"')]
        res, libres = R._apparier(rows, [self._src(7, '24V solenoid valve tube 3/8" x 3/8"')])
        self.assertEqual(res, {})
        self.assertEqual(len(libres), 1)

    def test_la_bonne_variante_gagne(self):
        rows = [_ligne("r1", 1, nom='Électrovanne 24 V tube 1/4" x 1/4"', traduit='24V solenoid valve tube 1/4 x 1/4 inch')]
        res, _l = R._apparier(rows, [self._src(7, '24V solenoid valve tube 3/8" x 3/8"'), self._src(8, '24V solenoid valve, tube 1/4" x 1/4"')])
        self.assertEqual(res["r1"][0]["ligne"], 8)


class TestComparaison(unittest.TestCase):
    """Ce qu'il cote est-il ce que nous avons demandé ? IA recoupée par les valeurs chiffrées (IA simulée)."""

    ROW = _ligne("r1", 1, nom="AutoFlush 24 V 300 cc retard 18 s", traduit="AutoFlush 24V 300cc 18s delay")

    def test_fusion(self):
        self.assertIsNone(R.fusionner_controle(None, []))
        self.assertEqual(R.fusionner_controle(None, ["300 cc ≠ 200 cc"])["verdict"], "ecart")
        c = R.fusionner_controle({"verdict": "conforme", "note": "même produit"}, ["300 cc ≠ 200 cc"])
        self.assertEqual((c["verdict"], c["ecarts"]), ("incertain", ["300 cc ≠ 200 cc"]))      # jamais « conforme »
        self.assertEqual(R.fusionner_controle({"verdict": "ecart", "ecarts": ["sans support"]}, [])["ecarts"], ["sans support"])
        self.assertEqual(R.texte_controle({"verdict": "ecart", "ecarts": ["300 cc ≠ 200 cc"], "note": ""}), "⚠️ 300 cc ≠ 200 cc")
        self.assertTrue(R.texte_controle({"verdict": "conforme", "ecarts": [], "note": ""}).startswith("✓"))

    def test_sans_ia_les_valeurs_seules(self):
        from unittest.mock import patch
        src = {"designation": "AutoFlush valve 24V 200 cc", "code": ""}
        with patch("customization_app.liste_commande_import._contexte_ia", side_effect=Exception("pas de clé")), \
                patch.object(frappe, "log_error"):
            out = R.comparer([("r1", self.ROW, src)])
        self.assertEqual((out["r1"]["verdict"], out["r1"]["ecarts"], out["r1"]["source"]), ("ecart", ["300 cc ≠ 200 cc"], "valeurs"))

    def test_avec_ia(self):
        from unittest.mock import patch
        vu = []

        def faux(contexte, system, user):
            vu.append(user)
            return {"resultats": [{"cle": "r1", "verdict": "conforme", "ecarts": [], "note": "même vanne"},
                                  {"cle": "r2", "verdict": "ecart", "ecarts": ["sans raccords"], "note": ""}]}
        r2 = _ligne("r2", 2, nom="Pompe booster avec raccords")
        with patch("customization_app.liste_commande_import._contexte_ia", return_value=None), \
                patch("customization_app.liste_commande_import._completion_json", side_effect=faux):
            out = R.comparer([("r1", self.ROW, {"designation": "AutoFlush 24V 300cc 18 s"}),
                              ("r2", r2, {"designation": "Booster pump 75 GPD"})])
        self.assertEqual(out["r1"]["verdict"], "conforme")
        self.assertEqual((out["r2"]["verdict"], out["r2"]["ecarts"]), ("ecart", ["sans raccords"]))
        self.assertEqual(len(vu), 1)                                       # un seul lot pour deux paires
        self.assertIn("AutoFlush 24V 300cc 18 s", vu[0])


class TestAjoutDeLignes(unittest.TestCase):
    """Une ligne du fournisseur sans correspondance, ajoutée à la liste avec ses prix (vraie base, nettoyée)."""

    def setUp(self):
        from customization_app.liste_commande_import import ALLOWED_USER
        if not frappe.db.exists("User", ALLOWED_USER):
            self.skipTest("utilisateur LCI absent")
        frappe.set_user(ALLOWED_USER)
        self.doc = frappe.get_doc({"doctype": R.DOCTYPE, "titre": "TEST ajout réponse fournisseur",
                                   "articles": [{"item_name": "Coude fileté", "qty": 10}]}).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.delete_doc(R.DOCTYPE, self.doc.name, force=True, ignore_permissions=True)
        frappe.db.commit()

    def test_ajout_et_controle_ecrits(self):
        r1 = self.doc.articles[0].name
        long = "AR05-B Purium: Compact 6 Stages with Pump " + "x" * 200
        res = R.appliquer_reponse(self.doc.name, [
            {"row": r1, "valeurs": {"prix_fournisseur": 0.8}, "source_label": "L5 · Elbow", "source_id": "S:5",
             "controle": "⚠️ 1/4\" ≠ 3/8\""}],
            ajouts=[{"source_id": "S:18", "source_label": "L18 · AR05-B", "designation": long, "code": "AR05-B",
                     "qty": 5, "valeurs": {"prix_fournisseur": 64.35, "moq": 50, "qty_par_carton": 1}}])
        self.assertEqual((res["maj"], res["ajoutees"]), (2, 1))
        doc = frappe.get_doc(R.DOCTYPE, self.doc.name)
        self.assertEqual(len(doc.articles), 2)
        a, b = doc.articles
        self.assertEqual((a.prix_fournisseur, a.controle_description), (0.8, "⚠️ 1/4\" ≠ 3/8\""))
        self.assertTrue(b.item_name.startswith("AR05-B Purium") and len(b.item_name) <= 140)
        self.assertEqual((b.item_code, b.qty, b.prix_fournisseur, b.moq, b.reponse_source, b.decision),
                         (None, 5.0, 64.35, 50.0, "L18 · AR05-B", "À négocier"))
        self.assertIn("AR05-B — AR05-B Purium", b.description)


class TestRattrapageParQuantite(unittest.TestCase):
    """Le fournisseur répond à NOTRE demande : la quantité est un indice (136 paires sur 141 identiques, 12/10/2026)."""

    def _src(self, i, desig, qty):
        return {"id": f"S:{i}", "ligne": i, "code": "", "designation": desig, "qty": qty, "variante": ""}

    def _l(self, name, idx, code, nom, qty, traduit=""):
        return _ligne(name, idx, code=code, nom=nom, qty=qty, traduit=traduit)

    def test_doublon_la_quantite_choisit_le_bon_exemplaire(self):
        r8 = self._l("r8", 8, "AP-C+", "Osmoseur compact+ 7 étages", 4)
        r9 = self._l("r9", 9, "AP-C+", "Osmoseur compact+ 7 étages", 150)
        l19 = self._src(19, "Compact 7 Stages with Pump", 150)
        res, libres = {"r8": (l19, "IA", 0.8)}, []
        R._rattraper_par_quantite([r8, r9], res, libres)
        self.assertEqual(list(res), ["r9"])
        self.assertEqual(res["r9"][1], "IA + quantité")

    def test_meme_quantite_et_meme_famille(self):
        r7 = self._l("r7", 7, "AP-C-EP-7", "Osmoseur compact pompe 75 GPD 7 étages", 200,
                     traduit="Compact reverse osmosis system with pump 75 GPD 7 stages")
        l18 = self._src(18, "AR05-B Purium: Compact 6 Stages with Pump, RO Membrane (75 GPD)", 200)
        lampe = self._src(71, "UV lamp 6 W - 4pins", 200)
        res, libres = {}, [lampe, l18]
        R._rattraper_par_quantite([r7], res, libres)
        self.assertIs(res["r7"][0], l18)                                   # pas la lampe, de même quantité
        self.assertEqual(res["r7"][1], "quantité identique")
        self.assertEqual(libres, [lampe])

    def test_ni_autre_quantite_ni_egalite(self):
        r = self._l("r62", 62, "L-UV-6w", "Lampe UV 6W", 50, traduit="UV lamp 6W")
        res, libres = {}, [self._src(71, "UV lamp 6 W - 4pins", 50), self._src(72, "UV lamp 6 W - 4pins", 50)]
        R._rattraper_par_quantite([r], res, libres)
        self.assertEqual(res, {})                                          # deux candidates identiques : à vous de choisir
        res, libres = {}, [self._src(71, "UV lamp 6 W - 4pins", 40)]
        R._rattraper_par_quantite([r], res, libres)
        self.assertEqual(res, {})                                          # autre quantité : rien

    def test_correction_quand_la_quantite_et_les_valeurs_disent_autre_chose(self):
        r = self._l("r124", 124, "Acc013-E", 'Vanne arrêt rapide M 1/4" x tube 3/8"', 150,
                    traduit='Quick shut-off valve M 1/4" x tube 3/8"')
        l136 = self._src(136, 'Quick shut-off valve, M1/2" x tube 3/8"', 200)
        l134 = self._src(134, 'Quick shut-off valve M 1/4" x tube 3/8" • Male thread: 1/4" • Tube fitting: 3/8"', 150)
        res, libres = {"r124": (l136, "IA", 0.8)}, [l134]
        R._rattraper_par_quantite([r], res, libres)
        self.assertIs(res["r124"][0], l134)
        self.assertEqual(res["r124"][1], "quantité et valeurs")
        self.assertEqual(libres, [l136])                                   # la mauvaise variante redevient libre

