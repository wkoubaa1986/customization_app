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
        self.name = kw.get("name", "r")
        self.decision = ""
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
        for cle in ("photo", "retirees", "additionnels", "note_xls", "note_masquees"):
            self.assertIn(cle, attendu)


class TestLignesRetirees(unittest.TestCase):
    def test_seules_les_abandonnees_appariees_sont_retirees(self):
        articles = [Row(name="a", decision="Abandonné"), Row(name="b", decision="Accepté"),
                    Row(name="c", decision="Abandonné"), Row(name="d", decision="Abandonné")]
        corresp = {"a": 5, "b": 6, "c": 9}          # d n'a pas de place dans son tableau
        self.assertEqual(E.lignes_supprimees(articles, corresp), [5, 9])

    def test_remapper_apres_suppression_physique(self):
        corresp = {"a": 5, "b": 6, "c": 9, "e": 12}
        self.assertEqual(E.remapper_correspondances(corresp, [5, 9]), {"b": 5, "e": 10})

    def test_remapper_sans_suppression(self):
        self.assertEqual(E.remapper_correspondances({"a": 3}, []), {"a": 3})


class TestLigneModifiee(unittest.TestCase):
    def test_quantite_differente(self):
        self.assertTrue(E.est_modifiee(120, 150, 0, 36.3))

    def test_prix_cible_different(self):
        self.assertTrue(E.est_modifiee(150, 150, 33.3, 36.3))

    def test_identique_non_modifiee(self):
        self.assertFalse(E.est_modifiee(150, 150, 0, 36.3))
        self.assertFalse(E.est_modifiee(150, 150, 36.3, 36.3))

    def test_quantite_non_tranchee_ne_compte_pas(self):
        self.assertFalse(E.est_modifiee(0, 150, 0, 36.3))

    def test_additionnels_rendent_la_ligne_modifiee(self):
        self.assertTrue(E.est_modifiee(150, 150, 0, 36.3, [{"item_code": "M"}]))


class TestObservation(unittest.TestCase):
    L = E.LABELS["English"]

    def test_additionnels_decrits_avec_la_quantite_totale(self):
        adds = [{"item_name": "Membrane 1812-80 GPD", "brand": "Vontron", "qty_par_pack": 1},
                {"item_name_traduit": "Luxury faucet", "qty_par_pack": 2}]
        self.assertEqual(E.texte_additionnels(adds, 150, self.L),
                         "Additional items: + 150 × Membrane 1812-80 GPD (Vontron) ; + 300 × Luxury faucet")

    def test_observation_et_additionnels_empiles(self):
        t = E.texte_observation("Too expensive", [{"item_name": "Tap", "qty_par_pack": 1}], 10, self.L)
        self.assertEqual(t, "Too expensive\nAdditional items: + 10 × Tap")

    def test_sans_additionnels_l_observation_reste_telle_quelle(self):
        self.assertEqual(E.texte_observation("  ok ", [], 10, self.L), "ok")
        self.assertEqual(E.texte_additionnels([], 10, self.L), "")


if __name__ == "__main__":
    unittest.main()
