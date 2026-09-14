"""Le « ✅ Valider » du dialogue de clôture doit être branché DANS LES DEUX ZONES.

⚠️ LE BUG : le bouton était affiché sur la ligne « Commande liée » (zone `zone`) dès que la
commande était en brouillon, mais le seul `on("click")` vivait dans rendre_infos(), donc sur
`zone_infos` — c'est-à-dire uniquement sur les boutons des bons de livraison. Or tant que la
commande est en brouillon il n'y a PAS encore de BL (c'est sa validation qui le crée) : le
partenaire ne voyait qu'un bouton inerte et la commande restait invalidable depuis son
téléphone (régression v5.51.2, constatée en prod le 14/09/2026).

Ces tests lisent le SOURCE du script — il n'y a pas de site Frappe ici, et c'est le
branchement lui-même qui doit être garanti.
"""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app


def js() -> str:
    chemin = os.path.join(os.path.dirname(inspect.getfile(customization_app)), "public", "js",
                          "tache_photos_cloture.js")
    with open(chemin, encoding="utf-8") as fh:
        return fh.read()


class TestBoutonValiderBrancheDansLesDeuxZones(unittest.TestCase):

    def test_la_ligne_commande_est_branchee(self):
        """`zone` porte le bouton quand la commande est en brouillon — et rien d'autre."""
        self.assertIn(
            'd.fields_dict.zone.$wrapper.find("[data-valider-docs]").on("click", '
            "valider_documents)",
            js(),
        )

    def test_la_ligne_bl_est_branchee(self):
        self.assertIn(
            'zi.$wrapper.find("[data-valider-docs]").on("click", valider_documents)', js()
        )

    def test_une_seule_fonction_partagee(self):
        """Deux copies de l'appel serveur, c'est deux comportements qui divergent."""
        src = js()
        self.assertEqual(src.count("function valider_documents("), 1)
        self.assertEqual(
            src.count("customization_app.cloture_partenaire.valider_documents"), 1, src
        )

    def test_un_clic_un_appel(self):
        """Les deux boutons vivent dans DEUX wrappers distincts (`zone` et `zone_infos`) :
        chaque branchement ne voit que le sien, un clic ne part donc qu'une fois."""
        src = js()
        self.assertEqual(src.count('.find("[data-valider-docs]").on("click"'), 2, src)

    def test_l_appel_fige_l_ecran_puis_rafraichit(self):
        """Sans le gel, un double tap sur téléphone soumettrait deux fois ; sans le
        rafraîchissement, le dialogue continuerait d'afficher « Brouillon — à valider »."""
        debut = js().index("function valider_documents(")
        corps = js()[debut:debut + 1200]
        self.assertIn("freeze: true", corps)
        self.assertIn('freeze_message: __("Validation en cours…")', corps)
        self.assertIn("rafraichir();", corps)

    def test_le_bouton_est_bien_rendu_sur_la_ligne_commande(self):
        """Le branchement ne sert à rien si le bouton disparaît du HTML."""
        src = js()
        self.assertIn("ex.commande_brouillon", src)
        self.assertEqual(src.count('data-valider-docs="1"'), 2, src)


if __name__ == "__main__":
    unittest.main()
