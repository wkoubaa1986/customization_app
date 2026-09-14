"""Tests du bouton « Enlever les doublons » de la Liste Commande Import.

Convention de l'app : `unittest.TestCase` pur, données injectées, aucun accès réseau ni base.
Les lignes ressemblent à celles d'une liste d'import : même article ajouté plusieurs fois.
"""
from __future__ import annotations

import json
import unittest

from customization_app import lci_doublons as D


def ligne(name, item_code, qty, uom="Pièce", item_name="", adds=None):
    return {"name": name, "item_code": item_code, "item_name": item_name or item_code or "",
            "qty": qty, "uom": uom,
            "articles_additionnels": json.dumps(adds) if adds is not None else ""}


class TestRegroupement(unittest.TestCase):
    def test_deux_lignes_du_meme_article_n_en_font_plus_qu_une(self):
        res = D.regrouper([ligne("a", "M-400-A-HF", 60), ligne("b", "M-400-A-HF", 20)])
        self.assertEqual(res["supprimer"], ["b"])
        self.assertEqual(res["doublons"], 1)
        self.assertEqual([(l["name"], l["qty"]) for l in res["conserver"]], [("a", 80.0)])
        self.assertEqual(res["conserver"][0]["fusionnees"], 2)

    def test_la_ligne_conservee_est_la_premiere_et_garde_sa_place(self):
        """Elle garde sa position, donc son image, sa description et sa traduction."""
        res = D.regrouper([ligne("a", "ART-1", 1), ligne("b", "ART-2", 2), ligne("c", "ART-1", 4)])
        self.assertEqual([l["name"] for l in res["conserver"]], ["a", "b"])
        self.assertEqual([l["qty"] for l in res["conserver"]], [5.0, 2.0])
        self.assertEqual(res["supprimer"], ["c"])

    def test_des_articles_distincts_restent_intacts_et_dans_l_ordre(self):
        res = D.regrouper([ligne("a", "ART-1", 1), ligne("b", "ART-2", 2), ligne("c", "ART-3", 3)])
        self.assertEqual(res["doublons"], 0)
        self.assertEqual(res["supprimer"], [])
        self.assertEqual([l["name"] for l in res["conserver"]], ["a", "b", "c"])
        self.assertTrue(all(l["fusionnees"] == 1 for l in res["conserver"]))
        self.assertEqual(res["non_fusionnees"], [])

    def test_plusieurs_groupes_se_fusionnent_du_meme_coup(self):
        res = D.regrouper([ligne("a", "ART-1", 1), ligne("b", "ART-2", 2), ligne("c", "ART-1", 1),
                           ligne("d", "ART-2", 3), ligne("e", "ART-1", 1)])
        self.assertEqual(res["doublons"], 3)
        self.assertEqual(sorted(res["supprimer"]), ["c", "d", "e"])
        self.assertEqual([(l["name"], l["qty"]) for l in res["conserver"]],
                         [("a", 3.0), ("b", 5.0)])

    def test_une_liste_sans_ligne_ne_fait_rien(self):
        for vide in ([], None):
            res = D.regrouper(vide)
            self.assertEqual(res["doublons"], 0)
            self.assertEqual(res["conserver"], [])
            self.assertEqual(res["non_fusionnees"], [])

    def test_les_quantites_decimales_se_somment_au_millieme(self):
        """⚠️ Additionner des flottants sans arrondir écrit 2.9999999999999996 dans la case."""
        res = D.regrouper([ligne("a", "ART-1", 0.1), ligne("b", "ART-1", 0.2)])
        self.assertEqual(res["conserver"][0]["qty"], 0.3)


class TestLignesLibres(unittest.TestCase):
    """Une ligne « Article libre » n'a pas de code : sa désignation est son identité."""

    def test_deux_lignes_libres_a_la_meme_designation_se_fusionnent(self):
        res = D.regrouper([ligne("a", "", 2, item_name="Joint torique 12 mm"),
                           ligne("b", None, 3, item_name="  joint torique   12 MM ")])
        self.assertEqual(res["supprimer"], ["b"])
        self.assertEqual(res["conserver"][0]["qty"], 5.0)

    def test_une_ligne_libre_ne_se_confond_pas_avec_un_article_du_catalogue(self):
        res = D.regrouper([ligne("a", "ART-1", 1, item_name="Membrane"),
                           ligne("b", "", 1, item_name="Membrane")])
        self.assertEqual(res["doublons"], 0)

    def test_une_ligne_sans_code_ni_designation_n_est_jamais_regroupee(self):
        res = D.regrouper([ligne("a", None, 1), ligne("b", "", 2), ligne("c", None, 3)])
        self.assertEqual(res["doublons"], 0)
        self.assertEqual([l["name"] for l in res["conserver"]], ["a", "b", "c"])
        self.assertEqual(res["non_fusionnees"], [])


class TestCeQuiNeSeFusionnePas(unittest.TestCase):
    """⚠️ Sommer deux lignes du même article en « Pièce » et en « Carton » inventerait une
    quantité qui n'existe dans aucune unité. Elles restent, et le résultat le SIGNALE."""

    def test_deux_unites_differentes_ne_se_fusionnent_pas(self):
        res = D.regrouper([ligne("a", "ART-1", 1, uom="Pièce"), ligne("b", "ART-1", 2, uom="Carton")])
        self.assertEqual(res["doublons"], 0)
        self.assertEqual([l["name"] for l in res["conserver"]], ["a", "b"])
        self.assertEqual(res["non_fusionnees"],
                         [{"article": "ART-1", "lignes": 2, "motif": "unité"}])

    def test_un_pack_avec_additionnels_n_est_pas_le_pack_nu(self):
        adds = [{"item_code": "ADD-1", "qty_par_pack": 2}]
        res = D.regrouper([ligne("a", "PACK-1", 1), ligne("b", "PACK-1", 1, adds=adds)])
        self.assertEqual(res["doublons"], 0)
        self.assertEqual(res["non_fusionnees"],
                         [{"article": "PACK-1", "lignes": 2, "motif": "additionnels"}])

    def test_deux_packs_aux_memes_additionnels_se_fusionnent_quel_que_soit_l_ordre(self):
        a1 = [{"item_code": "ADD-1", "qty_par_pack": 2}, {"item_code": "ADD-2", "qty_par_pack": 1}]
        a2 = [{"item_code": "ADD-2", "qty_par_pack": 1, "image": "/x.png"},
              {"item_code": "ADD-1", "qty_par_pack": 2}]
        res = D.regrouper([ligne("a", "PACK-1", 1, adds=a1), ligne("b", "PACK-1", 2, adds=a2)])
        self.assertEqual(res["doublons"], 1)
        self.assertEqual(res["conserver"][0]["qty"], 3.0)

    def test_unite_et_additionnels_differents_se_disent_tous_les_deux(self):
        res = D.regrouper([ligne("a", "ART-1", 1, uom="Pièce"),
                           ligne("b", "ART-1", 2, uom="Carton",
                                 adds=[{"item_code": "ADD-1", "qty_par_pack": 1}])])
        self.assertEqual(res["non_fusionnees"],
                         [{"article": "ART-1", "lignes": 2, "motif": "unité et additionnels"}])

    def test_on_fusionne_ce_qui_se_fusionne_et_on_signale_le_reste(self):
        """Trois lignes, deux en Pièce : celles-là se regroupent, la troisième est signalée."""
        res = D.regrouper([ligne("a", "ART-1", 1), ligne("b", "ART-1", 2),
                           ligne("c", "ART-1", 4, uom="Carton")])
        self.assertEqual(res["supprimer"], ["b"])
        self.assertEqual([(l["name"], l["qty"]) for l in res["conserver"]], [("a", 3.0), ("c", 4.0)])
        self.assertEqual(res["non_fusionnees"],
                         [{"article": "ART-1", "lignes": 2, "motif": "unité"}])

    def test_une_ligne_libre_signalee_l_est_par_sa_designation(self):
        res = D.regrouper([ligne("a", "", 1, uom="Pièce", item_name="Joint"),
                           ligne("b", "", 1, uom="Sachet", item_name="joint")])
        self.assertEqual(res["non_fusionnees"][0]["article"], "joint")


if __name__ == "__main__":
    unittest.main()
