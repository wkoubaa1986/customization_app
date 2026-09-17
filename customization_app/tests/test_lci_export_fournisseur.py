"""Tests du renvoi au fournisseur de son propre classeur, annoté.

Convention de l'app : `unittest.TestCase` pur, données injectées, aucun accès
réseau ni base. Seules les règles de remplissage sont testées ici — l'écriture
du classeur elle-même est vérifiée à la main sur la facture Ang Ran.
"""
from __future__ import annotations

import json
import unittest

from customization_app import lci_export_fournisseur as E


class Row:
    def __init__(self, **kw):
        self.prix_cible = 0
        self.prix_cible_negocie = 0
        self.prix_fournisseur = 0
        self.repartition_conteneurs = ""
        self.__dict__.update(kw)

    def get(self, k, default=None):
        return self.__dict__.get(k, default)


class Doc:
    def __init__(self, langue):
        self.langue_cible = langue


class TestPrixEnvoye(unittest.TestCase):
    def test_la_contre_proposition_passe_avant_tout(self):
        self.assertEqual(E._prix_cible_export(
            Row(prix_cible=33.3, prix_cible_negocie=34.0, prix_fournisseur=36.3)), 34.0)

    def test_sans_contre_proposition_on_renvoie_notre_cible_initiale(self):
        """Renvoyer SON prix dans une colonne « prix cible » contredirait
        l'observation qui, juste à côté, le dit trop cher."""
        self.assertEqual(E._prix_cible_export(
            Row(prix_cible=33.3, prix_fournisseur=36.3)), 33.3)

    def test_sans_cible_la_colonne_reste_vide(self):
        self.assertEqual(E._prix_cible_export(Row(prix_fournisseur=36.3)), 0.0)


class TestConteneurs(unittest.TestCase):
    def test_une_ligne_dans_un_seul_conteneur(self):
        self.assertEqual(E._conteneurs_ligne(
            Row(repartition_conteneurs=json.dumps([{"no": 2, "qty": 500}]))), "C2")

    def test_une_ligne_scindee_montre_ses_quantites(self):
        self.assertEqual(E._conteneurs_ligne(Row(repartition_conteneurs=json.dumps(
            [{"no": 1, "qty": 310}, {"no": 2, "qty": 590}]))), "C1 (310) + C2 (590)")

    def test_pas_de_plan_pas_de_colonne(self):
        self.assertEqual(E._conteneurs_ligne(Row()), "")

    def test_un_json_abime_ne_fait_pas_echouer_l_export(self):
        self.assertEqual(E._conteneurs_ligne(Row(repartition_conteneurs="{oups")), "")


class TestLangue(unittest.TestCase):
    def test_les_decisions_partent_dans_la_langue_du_fournisseur(self):
        self.assertEqual(E._decision(Doc("English"), "Abandonné"), "Dropped")
        self.assertEqual(E._decision(Doc("Deutsch"), "Accepté"), "Angenommen")

    def test_en_francais_la_decision_est_rendue_telle_quelle(self):
        self.assertEqual(E._decision(Doc("Français"), "À négocier"), "À négocier")

    def test_langue_inconnue_repli_francais_pour_les_entetes(self):
        self.assertEqual(E._labels(Doc("Klingon"))["qty"], "Qté retenue")

    def test_chaque_langue_declare_toutes_les_etiquettes(self):
        attendu = set(E.LABELS["Français"])
        for langue, mots in E.LABELS.items():
            self.assertEqual(set(mots), attendu, langue)


if __name__ == "__main__":
    unittest.main()
