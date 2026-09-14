"""Tests de l'imputation d'un avoir client sur une commande (ticket #23).

Un retour BL donnait bien un avoir — une écriture de journal flottante — mais
personne ne savait quoi en faire : l'avoir n'apparaissait ni sur la commande ni
dans les connexions de la fiche Client, et l'imputer voulait dire rééquilibrer
l'échéancier à la main.

Convention : `unittest.TestCase` pur, aucune base. Ce qui se teste est la
DÉCISION (combien peut-on imputer, sur quelle ligne, avec quel refus), pas le
SQL ni l'enregistrement — les Server Scripts font l'écriture comptable.
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import frappe

from customization_app import api
from customization_app.avoir_client import (
    MODE_AVOIR,
    MODE_DETTE,
    date_echeance_libre,
    erreur_application,
    index_ligne_a_reduire,
    montant_imputable,
    plan_application_avoir,
)

COMMANDE_OK = {
    "docstatus": 1,
    "status": "To Deliver and Bill",
    "per_billed": 0,
    "grand_total": 1000.0,
    "advance_paid": 400.0,
}


def _ligne(montant, mode=MODE_DETTE, jour=1, portion=0):
    return {
        "nom": "PS-%s-%s" % (mode, jour),
        "idx": jour,
        "mode_of_payment": mode,
        "payment_amount": montant,
        "invoice_portion": portion,
        "due_date": date(2026, 9, jour),
    }


class TestMontantImputable(unittest.TestCase):
    """Le montant proposé : jamais plus que l'avoir, jamais plus que le dû."""

    def test_l_avoir_limite_quand_il_est_le_plus_petit(self):
        self.assertEqual(montant_imputable(150, 1000, 400), 150)

    def test_le_reste_a_payer_limite_quand_l_avoir_est_plus_gros(self):
        self.assertEqual(montant_imputable(900, 1000, 400), 600)

    def test_une_commande_deja_reglee_ne_propose_rien(self):
        self.assertEqual(montant_imputable(500, 1000, 1000), 0)

    def test_une_commande_sur_payee_ne_propose_pas_de_negatif(self):
        self.assertEqual(montant_imputable(500, 1000, 1200), 0)


class TestLigneAReduire(unittest.TestCase):
    """La ligne diminuée par défaut ne doit jamais être de l'argent encaissé."""

    def test_la_dette_non_payee_est_choisie_avant_les_encaissements(self):
        lignes = [_ligne(700, "Espèces", 1), _ligne(300, MODE_DETTE, 2)]
        self.assertEqual(index_ligne_a_reduire(lignes), 1)

    def test_entre_deux_dettes_la_plus_grosse_l_emporte(self):
        """Elle a le plus de chances de couvrir l'avoir à elle seule."""
        lignes = [_ligne(200, MODE_DETTE, 1), _ligne(800, MODE_DETTE, 2)]
        self.assertEqual(index_ligne_a_reduire(lignes), 1)

    def test_sans_dette_c_est_la_derniere_ligne_hors_avoir(self):
        lignes = [_ligne(500, "Espèces", 1), _ligne(300, "Chèque", 2), _ligne(200, MODE_AVOIR, 3)]
        self.assertEqual(index_ligne_a_reduire(lignes), 1)

    def test_les_lignes_a_zero_sont_ignorees(self):
        lignes = [_ligne(1000, "Espèces", 1), _ligne(0, MODE_DETTE, 2)]
        self.assertEqual(index_ligne_a_reduire(lignes), 0)

    def test_un_echeancier_entierement_en_avoir_ne_propose_rien(self):
        self.assertIsNone(index_ligne_a_reduire([_ligne(300, MODE_AVOIR, 1)]))

    def test_un_echeancier_vide_ne_propose_rien(self):
        self.assertIsNone(index_ligne_a_reduire([]))


class TestDateEcheanceLibre(unittest.TestCase):
    """ERPNext refuse deux échéances à la même date."""

    def test_une_date_libre_est_gardee(self):
        self.assertEqual(
            date_echeance_libre([date(2026, 9, 1)], date(2026, 9, 5)), date(2026, 9, 5))

    def test_une_date_prise_est_decalee_au_jour_suivant(self):
        self.assertEqual(
            date_echeance_libre([date(2026, 9, 5)], date(2026, 9, 5)), date(2026, 9, 6))

    def test_le_decalage_saute_les_jours_deja_pris(self):
        prises = [date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 7)]
        self.assertEqual(date_echeance_libre(prises, date(2026, 9, 5)), date(2026, 9, 8))

    def test_les_dates_vides_n_empechent_rien(self):
        self.assertEqual(
            date_echeance_libre([None, ""], date(2026, 9, 5)), date(2026, 9, 5))


class TestPlanApplicationAvoir(unittest.TestCase):
    """Le rééquilibrage : ce que la ligne choisie perd, l'avoir le gagne."""

    def test_le_total_de_l_echeancier_ne_bouge_pas(self):
        lignes = [_ligne(700, "Espèces", 1), _ligne(300, MODE_DETTE, 2)]
        plan = plan_application_avoir(lignes, 1, 120)
        total = (700 + plan["nouveau_montant_ligne"] + plan["ligne_avoir"]["payment_amount"])
        self.assertEqual(total, 1000)
        self.assertEqual(plan["nouveau_montant_ligne"], 180)
        self.assertEqual(plan["ligne_avoir"]["mode_of_payment"], MODE_AVOIR)

    def test_la_ligne_epuisee_disparait(self):
        """Une échéance à 0 ferait échouer la génération du paiement."""
        lignes = [_ligne(700, "Espèces", 1), _ligne(300, MODE_DETTE, 2)]
        plan = plan_application_avoir(lignes, 1, 300)
        self.assertTrue(plan["supprimer_ligne"])
        self.assertEqual(plan["nouveau_montant_ligne"], 0)
        self.assertEqual(plan["ligne_avoir"]["payment_amount"], 300)

    def test_la_ligne_supprimee_rend_sa_date_a_l_avoir(self):
        lignes = [_ligne(700, "Espèces", 1), _ligne(300, MODE_DETTE, 2)]
        plan = plan_application_avoir(lignes, 1, 300)
        self.assertEqual(plan["ligne_avoir"]["due_date"], date(2026, 9, 2))

    def test_la_ligne_conservee_pousse_l_avoir_au_jour_suivant(self):
        lignes = [_ligne(700, "Espèces", 1), _ligne(300, MODE_DETTE, 2)]
        plan = plan_application_avoir(lignes, 1, 120)
        self.assertEqual(plan["ligne_avoir"]["due_date"], date(2026, 9, 3))

    def test_la_portion_est_partagee_au_prorata_des_montants(self):
        """ERPNext recalcule payment_amount depuis invoice_portion à chaque
        save : laisser la portion inchangée annulerait la réduction."""
        lignes = [_ligne(700, "Espèces", 1, portion=70), _ligne(300, MODE_DETTE, 2, portion=30)]
        plan = plan_application_avoir(lignes, 1, 120)
        self.assertEqual(plan["ligne_avoir"]["invoice_portion"], 12)
        self.assertEqual(plan["nouvelle_portion_ligne"], 18)

    def test_sans_portion_l_avoir_n_en_invente_pas(self):
        lignes = [_ligne(700, "Espèces", 1), _ligne(300, MODE_DETTE, 2)]
        plan = plan_application_avoir(lignes, 1, 120)
        self.assertEqual(plan["ligne_avoir"]["invoice_portion"], 0)

    def test_une_ligne_unique_entierement_soldee_par_l_avoir(self):
        lignes = [_ligne(300, MODE_DETTE, 1, portion=100)]
        plan = plan_application_avoir(lignes, 0, 300)
        self.assertTrue(plan["supprimer_ligne"])
        self.assertEqual(plan["ligne_avoir"]["invoice_portion"], 100)


class TestErreurApplication(unittest.TestCase):
    """Les refus : un message français, jamais une écriture bancale."""

    def test_rien_ne_bloque_une_commande_eligible(self):
        self.assertIsNone(erreur_application(COMMANDE_OK, 500, 300, _ligne(300)))

    def test_une_commande_brouillon_est_refusee(self):
        commande = dict(COMMANDE_OK, docstatus=0, status="Draft")
        self.assertIn("validée", erreur_application(commande, 500, 300))

    def test_une_commande_fermee_renvoie_vers_le_rapprochement(self):
        commande = dict(COMMANDE_OK, status="Closed")
        message = erreur_application(commande, 500, 300)
        self.assertIn("fermée", message)
        self.assertIn("Rapprochement de paiement", message)

    def test_une_commande_facturee_renvoie_vers_le_rapprochement(self):
        commande = dict(COMMANDE_OK, per_billed=100)
        message = erreur_application(commande, 500, 300)
        self.assertIn("facturée", message)
        self.assertIn("Rapprochement de paiement", message)

    def test_un_client_sans_avoir_ne_peut_rien_imputer(self):
        """Le cas de la deuxième utilisation : le disponible est épuisé."""
        self.assertIn("aucun avoir", erreur_application(COMMANDE_OK, 0, 100))

    def test_une_commande_deja_reglee_n_a_rien_a_imputer(self):
        commande = dict(COMMANDE_OK, advance_paid=1000)
        self.assertIn("entièrement réglée", erreur_application(commande, 500, 100))

    def test_un_montant_nul_est_refuse(self):
        self.assertIn("supérieur à 0", erreur_application(COMMANDE_OK, 500, 0))

    def test_un_montant_au_dela_du_disponible_est_refuse(self):
        self.assertIn("dépasse l'avoir disponible",
                      erreur_application(COMMANDE_OK, 200, 300))

    def test_un_montant_au_dela_du_reste_a_payer_est_refuse(self):
        """1000 de TTC dont 400 déjà payés : 600 au maximum."""
        self.assertIn("dépasse le reste à payer",
                      erreur_application(COMMANDE_OK, 900, 700))

    def test_une_ligne_trop_petite_est_refusee(self):
        message = erreur_application(COMMANDE_OK, 500, 300, _ligne(200))
        self.assertIn("ne couvre pas", message)

    def test_l_eligibilite_seule_ignore_le_montant(self):
        """Sans montant, on ne juge que la commande : c'est ce qui décide
        d'afficher le bouton."""
        self.assertIsNone(erreur_application(COMMANDE_OK, 500))

    def test_le_millime_d_arrondi_ne_bloque_pas(self):
        self.assertIsNone(erreur_application(COMMANDE_OK, 300, 300.0004, _ligne(300)))


class TestTableauDeBordClient(unittest.TestCase):
    """La fiche Client doit montrer les écritures de journal : c'est le seul
    endroit où un avoir, créé comme utilisé, est visible."""

    def setUp(self):
        # Hors site, `frappe.local.flags` n'existe pas ; le décorateur
        # `@frappe.whitelist()` le consulte pour valider les types des arguments.
        self.flags_absents = not hasattr(frappe.local, "flags")
        if self.flags_absents:
            frappe.local.flags = frappe._dict(in_test=False)

    def tearDown(self):
        if self.flags_absents:
            del frappe.local.flags

    def _data(self):
        with patch.object(api, "_", lambda texte: texte):
            return api.get_data()

    def test_l_ecriture_de_journal_figure_dans_les_paiements(self):
        groupes = {g["label"]: g["items"] for g in self._data()["transactions"]}
        self.assertIn("Journal Entry", groupes["Payments"])

    def test_le_client_est_lu_sur_la_table_enfant(self):
        """Le tiers d'une écriture vit dans « Journal Entry Account »."""
        self.assertEqual(self._data()["non_standard_fieldnames"]["Journal Entry"], "party")

    def test_les_autres_connexions_sont_inchangees(self):
        data = self._data()
        groupes = {g["label"]: g["items"] for g in data["transactions"]}
        self.assertEqual(groupes["Orders"], ["Sales Order", "Delivery Note", "Sales Invoice"])
        self.assertEqual(groupes["Payments"][0], "Payment Entry")
        self.assertEqual(data["fieldname"], "customer")
