"""Tests des deux options de comptage du Bilan Vente.

Convention : `unittest.TestCase` pur, aucune base — on éprouve ici les RÈGLES qui
décident quelles commandes entrent dans le mois, pas les chiffres eux-mêmes.

Contexte (03/09/2026) : le bilan comptait chaque commande sur sa DATE DE LIVRAISON.
Sur août 2026, quatre commandes livrées fin août portaient une tâche datée de
septembre et toujours ouverte — 3 001 DT de vente annoncés sur du travail qui
n'avait pas eu lieu, et un solde Economiq qui changeait de camp selon la règle
retenue. D'où deux réglages, offerts à l'écran.
"""
from __future__ import annotations

import unittest

from customization_app import bilan_vente as BV


class TestBaseDeComptage(unittest.TestCase):
    """Sur quelle date une commande est rattachée à un mois."""

    def test_la_livraison_reste_le_defaut(self):
        """⚠️ Le bilan d'août 2026 a déjà été lu et discuté avec cette base. Basculer
        le défaut changerait sous les pieds de l'utilisateur des chiffres qu'il a
        commentés — le nouveau comportement doit être un CHOIX, jamais une surprise."""
        self.assertEqual(BV._base_valide(None), BV.BASE_LIVRAISON)
        self.assertEqual(BV._base_valide(""), BV.BASE_LIVRAISON)

    def test_les_deux_bases_sont_acceptees(self):
        self.assertEqual(BV._base_valide("tache"), BV.BASE_TACHE)
        self.assertEqual(BV._base_valide("livraison"), BV.BASE_LIVRAISON)

    def test_la_casse_et_les_espaces_ne_genent_pas(self):
        self.assertEqual(BV._base_valide("  TACHE "), BV.BASE_TACHE)

    def test_une_base_inconnue_retombe_sur_la_livraison(self):
        """Un paramètre venu de l'URL ou d'un export ne doit jamais produire un
        périmètre indéterminé — ni une erreur SQL par interpolation."""
        for bidon in ("autre", "delivery_date", "1; DROP TABLE", "tâche"):
            self.assertEqual(BV._base_valide(bidon), BV.BASE_LIVRAISON, bidon)


class TestFragmentSql(unittest.TestCase):
    """Le fragment de date est INTERPOLÉ dans la requête : il ne peut venir que
    d'ici, jamais de l'appelant."""

    def test_la_base_livraison_ne_joint_aucune_tache(self):
        date, jointure = BV._repere(BV.BASE_LIVRAISON)
        self.assertEqual(date, "so.delivery_date")
        self.assertEqual(jointure, "")

    def test_la_base_tache_joint_et_retombe_sur_la_livraison(self):
        """La commande SANS tâche garde sa date de livraison : c'est le seul
        événement daté qu'elle porte. Une jointure stricte l'aurait fait
        disparaître du bilan (SAL-ORD-2026-02930, 60 DT en août 2026)."""
        date, jointure = BV._repere(BV.BASE_TACHE)
        self.assertIn("COALESCE", date)
        self.assertIn("so.delivery_date", date)
        self.assertIn("Tache de travail", jointure)

    def test_seules_les_taches_vivantes_datent_la_commande(self):
        """Une tâche annulée ne dit rien de la date du travail."""
        self.assertIn("docstatus < 2", BV._repere(BV.BASE_TACHE)[1])

    def test_la_premiere_tache_fait_foi(self):
        """MIN : une commande qui traîne sur deux mois appartient au mois où le
        travail a COMMENCÉ, pas au dernier passage."""
        self.assertIn("MIN(starts_on)", BV._repere(BV.BASE_TACHE)[1])

    def test_un_fragment_inconnu_ne_peut_pas_etre_injecte(self):
        date, jointure = BV._repere("'; DROP TABLE `tabSales Order`; --")
        self.assertEqual(date, "so.delivery_date")
        self.assertEqual(jointure, "")


class TestTacheOuverte(unittest.TestCase):
    """Ce qui fait dire qu'une commande n'est pas finie."""

    def test_une_tache_ouverte_suffit(self):
        self.assertTrue(BV._a_une_tache_ouverte([{"status": "Open"}]))

    def test_une_seule_ouverte_parmi_plusieurs_suffit(self):
        """Une installation faite mais un entretien encore à venir : la commande
        n'est pas soldée."""
        self.assertTrue(BV._a_une_tache_ouverte(
            [{"status": "Completed"}, {"status": "Open"}]))

    def test_tout_termine_ne_declenche_rien(self):
        self.assertFalse(BV._a_une_tache_ouverte(
            [{"status": "Completed"}, {"status": "Completed"}]))

    def test_une_tache_annulee_n_est_pas_ouverte(self):
        """Annulée = le travail n'aura pas lieu, pas « en attente ». L'exclure
        retirerait du bilan une vente pourtant acquise."""
        self.assertFalse(BV._a_une_tache_ouverte([{"status": "Cancelled"}]))

    def test_une_commande_sans_tache_n_est_jamais_ecartee(self):
        """Rien à attendre : une vente au comptoir n'a pas de tâche, et elle est
        bien du chiffre d'affaires du mois."""
        self.assertFalse(BV._a_une_tache_ouverte([]))
        self.assertFalse(BV._a_une_tache_ouverte(None))

    def test_une_ligne_vide_ne_leve_pas(self):
        self.assertFalse(BV._a_une_tache_ouverte([None, {}]))
