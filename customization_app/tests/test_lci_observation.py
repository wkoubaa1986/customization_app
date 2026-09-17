"""Tests des observations de ligne d'une Liste Commande Import.

Convention de l'app : `unittest.TestCase` pur, données injectées, aucun accès
réseau ni base. Une ligne est un dict (trace) ou un petit objet (écriture).
"""
from __future__ import annotations

import json
import unittest

from customization_app import lci_observation as O


def ligne(**kw):
    base = {"qty": 0, "qty_fournisseur": 0, "qty_cible": 0, "prix_cible": 0,
            "prix_fournisseur": 0, "prix_cible_negocie": 0, "total_fichier": 0,
            "decision": ""}
    base.update(kw)
    return base


class Row:
    """Ligne inscriptible, comme un Document Frappe pour ce que le code en fait."""

    def __init__(self, **kw):
        self.__dict__.update(ligne(**kw))
        self.observation = kw.get("observation", "")
        self.observation_auto = kw.get("observation_auto", "")
        self.trace_changements = ""

    def get(self, k, default=None):
        return self.__dict__.get(k, default)


class TestValeursRetenues(unittest.TestCase):
    def test_sans_contre_proposition_ce_sont_nos_valeurs_qui_font_foi(self):
        r = ligne(qty=900, qty_fournisseur=600, prix_fournisseur=40.1)
        self.assertEqual(O.qty_retenue(r), 900)
        self.assertEqual(O.prix_retenu(r), 40.1)

    def test_la_contre_proposition_prime(self):
        r = ligne(qty=900, qty_cible=750, prix_fournisseur=40.1, prix_cible_negocie=38)
        self.assertEqual(O.qty_retenue(r), 750)
        self.assertEqual(O.prix_retenu(r), 38)

    def test_le_total_du_fichier_utilise_sa_quantite_pas_la_notre(self):
        """Le cœur du « la somme ne correspond pas à l'Excel »."""
        r = ligne(qty=900, qty_fournisseur=600, prix_fournisseur=40.1)
        self.assertEqual(O.total_fichier_ligne(r), 600 * 40.1)

    def test_sa_colonne_montant_prime_sur_le_produit(self):
        r = ligne(qty=100, qty_fournisseur=50, prix_fournisseur=0.42, total_fichier=20.5)
        self.assertEqual(O.total_fichier_ligne(r), 20.5)

    def test_une_ligne_non_cotee_ne_pese_rien_dans_le_fichier(self):
        self.assertEqual(O.total_fichier_ligne(ligne(qty=100)), 0)


class TestTrace(unittest.TestCase):
    def test_quantite_cotee_differente_de_la_demande(self):
        t = O.trace_ligne(ligne(qty=900, qty_fournisseur=600, prix_fournisseur=40.1))
        self.assertEqual([c["type"] for c in t], ["qty_fournisseur"])
        self.assertEqual((t[0]["de"], t[0]["a"]), (900, 600))

    def test_meme_quantite_aucune_trace(self):
        self.assertEqual(O.trace_ligne(ligne(qty=100, qty_fournisseur=100,
                                             prix_fournisseur=1.5)), [])

    def test_quantite_retenue_se_compare_a_la_sienne_quand_il_a_cote(self):
        t = O.trace_ligne(ligne(qty=900, qty_fournisseur=600, qty_cible=750,
                                prix_fournisseur=40.1))
        types = [c["type"] for c in t]
        self.assertEqual(types, ["qty_fournisseur", "qty_retenue"])
        self.assertEqual((t[1]["de"], t[1]["a"]), (600, 750))

    def test_prix_contre_propose_porte_son_pourcentage(self):
        t = O.trace_ligne(ligne(qty=10, prix_fournisseur=40.0, prix_cible_negocie=38.0))
        self.assertEqual(t[0]["type"], "prix")
        self.assertAlmostEqual(t[0]["pct"], -5.0)

    def test_prix_au_dessus_de_la_cible_tant_qu_on_n_a_pas_contre_propose(self):
        t = O.trace_ligne(ligne(qty=10, prix_cible=35.0, prix_fournisseur=40.0))
        self.assertEqual([c["type"] for c in t], ["au_dessus_cible"])
        self.assertAlmostEqual(t[0]["pct"], 100 * 5 / 35)

    def test_la_contre_proposition_remplace_le_rappel_de_cible(self):
        t = O.trace_ligne(ligne(qty=10, prix_cible=35.0, prix_fournisseur=40.0,
                                prix_cible_negocie=36.0))
        self.assertEqual([c["type"] for c in t], ["prix"])

    def test_ligne_cotee_alors_qu_elle_n_etait_pas_demandee(self):
        t = O.trace_ligne(ligne(qty=0, qty_fournisseur=20, prix_fournisseur=2.9))
        self.assertEqual([c["type"] for c in t], ["qty_non_demandee"])

    def test_abandon_annonce_en_premier(self):
        t = O.trace_ligne(ligne(qty=100, qty_fournisseur=50, prix_fournisseur=1,
                                decision="Abandonné"))
        self.assertEqual(t[0]["type"], "abandon")

    def test_accepte_sans_ecart_le_dit_quand_meme(self):
        t = O.trace_ligne(ligne(qty=100, qty_fournisseur=100, prix_fournisseur=1,
                                decision="Accepté"))
        self.assertEqual([c["type"] for c in t], ["accepte"])


class TestTexte(unittest.TestCase):
    def test_les_chiffres_sont_repris_tels_quels(self):
        t = O.trace_ligne(ligne(qty=900, qty_fournisseur=600, prix_fournisseur=40.1,
                                prix_cible_negocie=38))
        txt = O.texte_trace(t, "Français", "USD")
        self.assertIn("600", txt)
        self.assertIn("900", txt)
        self.assertIn("38", txt)
        self.assertIn("-5.2 %", txt)

    def test_la_langue_du_fournisseur_est_respectee(self):
        t = O.trace_ligne(ligne(qty=900, qty_fournisseur=600, prix_fournisseur=1))
        self.assertIn("instead", O.texte_trace(t, "English", "USD"))
        self.assertIn("statt", O.texte_trace(t, "Deutsch", "USD"))
        self.assertIn("بدل", O.texte_trace(t, "العربية", "USD"))

    def test_langue_inconnue_repli_francais(self):
        t = O.trace_ligne(ligne(qty=10, qty_fournisseur=5, prix_fournisseur=1))
        self.assertIn("au lieu", O.texte_trace(t, "Klingon", "USD"))

    def test_les_petits_prix_gardent_leurs_decimales(self):
        t = O.trace_ligne(ligne(qty=1, prix_fournisseur=0.195, prix_cible_negocie=0.175))
        self.assertIn("0.175", O.texte_trace(t, "Français", "USD"))
        self.assertIn("0.195", O.texte_trace(t, "Français", "USD"))


class TestEcriture(unittest.TestCase):
    def test_observation_vide_est_remplie(self):
        r = Row(qty=900, qty_fournisseur=600, prix_fournisseur=40.1)
        self.assertTrue(O.maj_ligne(r, "Français", "USD"))
        self.assertIn("600", r.observation)
        self.assertEqual(r.observation, r.observation_auto)
        self.assertEqual([c["type"] for c in json.loads(r.trace_changements)],
                         ["qty_fournisseur"])

    def test_un_texte_ecrit_a_la_main_n_est_jamais_ecrase(self):
        r = Row(qty=900, qty_fournisseur=600, prix_fournisseur=40.1,
                observation="Vu avec M. Chen, on prend 600.", observation_auto="")
        self.assertFalse(O.maj_ligne(r, "Français", "USD"))
        self.assertEqual(r.observation, "Vu avec M. Chen, on prend 600.")

    def test_le_texte_automatique_suit_les_nouveaux_ecarts(self):
        r = Row(qty=900, qty_fournisseur=600, prix_fournisseur=40.1)
        O.maj_ligne(r, "Français", "USD")
        r.prix_cible_negocie = 38.0
        self.assertTrue(O.maj_ligne(r, "Français", "USD"))
        self.assertIn("38", r.observation)

    def test_sans_ecart_l_observation_automatique_disparait(self):
        r = Row(qty=900, qty_fournisseur=600, prix_fournisseur=40.1)
        O.maj_ligne(r, "Français", "USD")
        r.qty_fournisseur = 900
        O.maj_ligne(r, "Français", "USD")
        self.assertEqual(r.observation, "")
        self.assertEqual(r.trace_changements, "")

    def test_deux_passages_de_suite_ne_changent_rien(self):
        r = Row(qty=900, qty_fournisseur=600, prix_fournisseur=40.1)
        O.maj_ligne(r, "Français", "USD")
        self.assertFalse(O.maj_ligne(r, "Français", "USD"))


if __name__ == "__main__":
    unittest.main()
