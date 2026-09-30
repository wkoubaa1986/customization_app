"""Tests de « la dette suit le livré » (livraison_partielle.py).

Cas d'origine, SAL-ORD-2026-03111 (30/09/2026) : 3 850 DT commandés, 1 910 DT
sortis en cinq BL, 1 700 DT réglés au fil des sorties — et une ligne « Dette
non payée » de 2 150 DT, qui compte 1 940 DT de marchandise jamais livrée
comme une dette du client.

Convention : `unittest.TestCase` pur, aucune base. Ce qui se teste est la
DÉCISION (le bilan, les lignes ramenées au livré, l'échéancier réécrit, les
refus), pas le SQL ni l'enregistrement.
"""
from __future__ import annotations

import unittest
from datetime import date

from customization_app.livraison_partielle import (
    MODE_DETTE,
    MODE_PERTE,
    bilan,
    erreur_regularisation,
    ligne_livraison_partielle,
    plan_articles,
    plan_echeancier,
)

COMMANDE_OK = {"docstatus": 1, "status": "To Deliver and Bill", "per_billed": 0}

# La commande 03111 telle qu'en base.
LIGNES_03111 = [
    {"name": "6vpvmrkun6", "item_code": "AP-M-AJ-5-SM", "qty": 10, "delivered_qty": 8,
     "rate": 220, "uom": "Nos", "conversion_factor": 1, "delivery_date": date(2026, 8, 20)},
    {"name": "6vpvjscqrv", "item_code": "AP-C-EP-5-AM", "qty": 5, "delivered_qty": 0,
     "rate": 330, "uom": "Nos", "conversion_factor": 1, "delivery_date": date(2026, 8, 20)},
]
BL_03111 = [
    {"so_detail": "6vpvmrkun6", "qty": 1, "amount": 220},
    {"so_detail": "6vpvmrkun6", "qty": 3, "amount": 690},
    {"so_detail": "6vpvmrkun6", "qty": 2, "amount": 500},
    {"so_detail": "6vpvmrkun6", "qty": 1, "amount": 250},
    {"so_detail": "6vpvmrkun6", "qty": 1, "amount": 250},
]


def _ligne(montant, mode="Espèces", jour=1, nom=None):
    return {"nom": nom or "PS-%s-%s" % (mode, jour), "idx": jour, "mode_of_payment": mode,
            "payment_amount": montant, "due_date": date(2026, 9, jour)}


ECHEANCIER_03111 = [
    _ligne(2150, MODE_DETTE, 1, "DETTE"),
    _ligne(220, jour=2), _ligne(400, "Chèque", 3), _ligne(60, jour=4),
    _ligne(500, jour=5), _ligne(250, jour=6), _ligne(270, jour=7),
]


class TestBilan(unittest.TestCase):
    def test_03111_la_dette_affichee_compte_le_non_livre(self):
        b = bilan(3850, 1910, 1700, 2150)
        self.assertEqual(b["dette_reelle"], 210.0)
        self.assertEqual(b["non_livre"], 1940.0)
        self.assertEqual(b["trop_percu"], 0.0)
        self.assertTrue(b["partielle"])
        self.assertTrue(b["surevaluee"])

    def test_une_dette_egale_au_reste_du_n_est_pas_surevaluee(self):
        b = bilan(3850, 1910, 1700, 210)
        self.assertTrue(b["partielle"])
        self.assertFalse(b["surevaluee"])

    def test_la_marge_d_un_dinar_ne_leve_rien(self):
        self.assertFalse(bilan(1000, 600, 400, 200.9)["surevaluee"])
        self.assertTrue(bilan(1000, 600, 400, 201.1)["surevaluee"])

    def test_rien_de_livre_n_est_pas_une_livraison_partielle(self):
        b = bilan(1000, 0, 0, 1000)
        self.assertFalse(b["partielle"])
        self.assertFalse(b["surevaluee"])

    def test_tout_livre_n_est_pas_une_livraison_partielle(self):
        b = bilan(1000, 1000, 500, 500)
        self.assertFalse(b["partielle"])
        self.assertFalse(b["surevaluee"])
        # L'arrondi d'un BL (999,999) compte comme tout livré.
        self.assertFalse(bilan(1000, 999.5, 500, 500)["partielle"])

    def test_un_client_qui_a_paye_plus_que_le_livre_ne_doit_rien(self):
        b = bilan(1000, 400, 600, 400)
        self.assertEqual(b["dette_reelle"], 0.0)
        self.assertEqual(b["trop_percu"], 200.0)
        self.assertTrue(b["surevaluee"])


class TestPlanArticles(unittest.TestCase):
    def test_03111_ramenee_aux_huit_unites_au_tarif_des_bl(self):
        plan = plan_articles(LIGNES_03111, BL_03111)
        self.assertNotIn("erreur", plan)
        self.assertEqual(plan["total"], 1910.0)
        self.assertEqual(plan["supprimees"], ["AP-C-EP-5-AM"])
        self.assertEqual(len(plan["articles"]), 1)
        art = plan["articles"][0]
        self.assertEqual(art["docname"], "6vpvmrkun6")
        self.assertEqual(art["qty"], 8.0)
        # 1 910 / 8 : le tarif moyen réellement sorti, pas les 220 de la commande.
        self.assertEqual(art["rate"], 238.75)
        self.assertEqual(art["delivery_date"], "2026-08-20")

    def test_une_ligne_entierement_livree_garde_sa_quantite(self):
        lignes = [{"name": "L1", "item_code": "A", "qty": 2, "delivered_qty": 2, "rate": 100,
                   "uom": "Nos", "conversion_factor": 1, "delivery_date": None},
                  {"name": "L2", "item_code": "B", "qty": 3, "delivered_qty": 1, "rate": 50,
                   "uom": "Nos", "conversion_factor": 1, "delivery_date": None}]
        bl = [{"so_detail": "L1", "qty": 2, "amount": 200}, {"so_detail": "L2", "qty": 1, "amount": 50}]
        plan = plan_articles(lignes, bl)
        self.assertEqual([(a["docname"], a["qty"], a["rate"]) for a in plan["articles"]],
                         [("L1", 2.0, 100.0), ("L2", 1.0, 50.0)])
        self.assertEqual(plan["total"], 250.0)
        self.assertEqual(plan["supprimees"], [])

    def test_un_bl_sans_ligne_de_commande_refuse(self):
        plan = plan_articles(LIGNES_03111, [{"so_detail": None, "qty": 1, "amount": 100}])
        self.assertIn("échange", plan["erreur"])

    def test_un_bl_qui_pointe_une_ligne_inconnue_refuse(self):
        plan = plan_articles(LIGNES_03111, BL_03111 + [{"so_detail": "AUTRE", "qty": 1, "amount": 1}])
        self.assertIn("absentes", plan["erreur"])

    def test_quantites_bl_et_commande_en_desaccord_refuse(self):
        lignes = [dict(LIGNES_03111[0], delivered_qty=7)]
        plan = plan_articles(lignes, BL_03111)
        self.assertIn("manuelle", plan["erreur"])

    def test_rien_de_livre_refuse(self):
        self.assertIn("rien", plan_articles(LIGNES_03111, [])["erreur"].lower())

    def test_le_tarif_moyen_est_arrondi_au_millime(self):
        lignes = [{"name": "L1", "item_code": "A", "qty": 5, "delivered_qty": 3, "rate": 100,
                   "uom": "Nos", "conversion_factor": 1, "delivery_date": None}]
        plan = plan_articles(lignes, [{"so_detail": "L1", "qty": 3, "amount": 1000}])
        self.assertEqual(plan["articles"][0]["rate"], 333.333)
        self.assertEqual(plan["total"], 999.999)


class TestPlanEcheancier(unittest.TestCase):
    def test_03111_la_dette_devient_210_sur_la_meme_ligne(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1910)
        self.assertNotIn("erreur", plan)
        self.assertEqual(plan["dette"], 210.0)
        self.assertEqual(plan["perte"], 0.0)
        self.assertEqual(plan["ligne_dette"], "DETTE")
        self.assertEqual(plan["mode"], MODE_DETTE)
        self.assertEqual(plan["montant_ligne"], 210.0)
        self.assertIsNone(plan["date_dette"])
        self.assertEqual(plan["nouvelles"], [])
        self.assertEqual(plan["supprimees"], [])
        self.assertEqual(len(plan["conservees"]), 6)
        self.assertEqual(sum(l["payment_amount"] for l in plan["conservees"]) + plan["dette"], 1910.0)

    def test_tout_regle_la_ligne_de_dette_disparait(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1700)
        self.assertEqual(plan["dette"], 0.0)
        self.assertEqual(plan["perte"], 0.0)
        self.assertIsNone(plan["ligne_dette"])
        self.assertEqual(plan["supprimees"], ["DETTE"])

    def test_un_millime_de_reste_ne_fait_pas_une_dette(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1700.001)
        self.assertEqual(plan["dette"], 0.0)
        self.assertEqual(plan["supprimees"], ["DETTE"])

    def test_sans_ligne_de_dette_une_ligne_neuve_est_creee(self):
        lignes = [_ligne(500, jour=1), _ligne(300, jour=2)]
        plan = plan_echeancier(lignes, 900, aujourd_hui=date(2026, 9, 1))
        self.assertEqual(plan["dette"], 100.0)
        self.assertIsNone(plan["ligne_dette"])
        self.assertEqual(len(plan["nouvelles"]), 1)
        neuve = plan["nouvelles"][0]
        self.assertEqual(neuve["mode_of_payment"], MODE_DETTE)
        self.assertEqual(neuve["payment_amount"], 100.0)
        self.assertEqual(neuve["invoice_portion"], 0)
        # Le 1er et le 2 sont pris : la première date libre est le 3.
        self.assertEqual(neuve["due_date"], date(2026, 9, 3))

    def test_deux_lignes_de_dette_la_premiere_reste_l_autre_part(self):
        lignes = [_ligne(100, MODE_DETTE, 1, "D1"), _ligne(200, MODE_DETTE, 2, "D2"), _ligne(50, jour=3)]
        plan = plan_echeancier(lignes, 120)
        self.assertEqual(plan["dette"], 70.0)
        self.assertEqual(plan["ligne_dette"], "D1")
        self.assertEqual(plan["supprimees"], ["D2"])

    def test_plus_regle_que_le_total_refuse(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1600)
        self.assertIn("trop-perçu", plan["erreur"])
        self.assertIn("100,000 DT", plan["erreur"])


class TestPlanEcheancierPerte(unittest.TestCase):
    """La part du reste dû que l'on n'attend plus : 0, une partie, ou tout."""

    def test_tout_en_perte_la_ligne_de_dette_passe_en_perte_datee_du_jour(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1910, aujourd_hui=date(2026, 9, 30), perte=210)
        self.assertEqual(plan["dette"], 0.0)
        self.assertEqual(plan["perte"], 210.0)
        self.assertEqual(plan["ligne_dette"], "DETTE")   # même uid : le tandem remplace la PE
        self.assertEqual(plan["mode"], MODE_PERTE)
        self.assertEqual(plan["montant_ligne"], 210.0)
        self.assertEqual(plan["date_dette"], date(2026, 9, 30))
        self.assertEqual(plan["nouvelles"], [])

    def test_une_partie_en_perte_la_dette_garde_sa_ligne_la_perte_en_prend_une_neuve(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1910, aujourd_hui=date(2026, 9, 30), perte=100)
        self.assertEqual(plan["dette"], 110.0)
        self.assertEqual(plan["perte"], 100.0)
        self.assertEqual(plan["ligne_dette"], "DETTE")
        self.assertEqual(plan["mode"], MODE_DETTE)
        self.assertEqual(plan["montant_ligne"], 110.0)
        self.assertIsNone(plan["date_dette"])   # la dette garde sa date
        self.assertEqual(len(plan["nouvelles"]), 1)
        neuve = plan["nouvelles"][0]
        self.assertEqual(neuve["mode_of_payment"], MODE_PERTE)
        self.assertEqual(neuve["payment_amount"], 100.0)
        self.assertEqual(neuve["due_date"], date(2026, 9, 30))
        # L'échéancier somme toujours au TTC réduit.
        total = sum(l["payment_amount"] for l in plan["conservees"]) + plan["montant_ligne"] \
            + sum(n["payment_amount"] for n in plan["nouvelles"])
        self.assertEqual(total, 1910.0)

    def test_la_perte_evite_la_date_de_la_dette_conservee(self):
        # La dette est datée du 1er ; une perte « du 1er » se décale au 8 (2 à 7 pris aussi).
        plan = plan_echeancier(ECHEANCIER_03111, 1910, aujourd_hui=date(2026, 9, 1), perte=50)
        self.assertEqual(plan["nouvelles"][0]["due_date"], date(2026, 9, 8))

    def test_sans_ligne_de_dette_partage_cree_deux_lignes_neuves(self):
        lignes = [_ligne(500, jour=1), _ligne(300, jour=2)]
        plan = plan_echeancier(lignes, 900, aujourd_hui=date(2026, 9, 1), perte=40)
        self.assertIsNone(plan["ligne_dette"])
        modes = [(n["mode_of_payment"], n["payment_amount"], n["due_date"]) for n in plan["nouvelles"]]
        self.assertEqual(modes, [(MODE_DETTE, 60.0, date(2026, 9, 3)),
                                 (MODE_PERTE, 40.0, date(2026, 9, 4))])

    def test_sans_ligne_de_dette_tout_en_perte_cree_une_ligne_de_perte(self):
        lignes = [_ligne(500, jour=1), _ligne(300, jour=2)]
        plan = plan_echeancier(lignes, 900, aujourd_hui=date(2026, 9, 1), perte=100)
        self.assertEqual(len(plan["nouvelles"]), 1)
        self.assertEqual(plan["nouvelles"][0]["mode_of_payment"], MODE_PERTE)
        self.assertEqual(plan["nouvelles"][0]["payment_amount"], 100.0)

    def test_perte_sans_reste_ne_cree_rien(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1700, perte=0)
        self.assertEqual(plan["dette"], 0.0)
        self.assertEqual(plan["perte"], 0.0)
        self.assertEqual(plan["nouvelles"], [])
        self.assertEqual(plan["supprimees"], ["DETTE"])

    def test_perte_au_dessus_du_reste_du_refuse(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1910, perte=210.5)
        self.assertIn("dépasse le reste dû", plan["erreur"])
        self.assertIn("210,000 DT", plan["erreur"])

    def test_perte_negative_refuse(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1910, perte=-5)
        self.assertIn("négatif", plan["erreur"])

    def test_un_millime_de_dette_restante_part_en_perte(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1910, aujourd_hui=date(2026, 9, 30), perte=209.9995)
        self.assertEqual(plan["dette"], 0.0)
        self.assertEqual(plan["perte"], 210.0)
        self.assertEqual(plan["mode"], MODE_PERTE)

    def test_un_millime_de_perte_reste_une_dette(self):
        plan = plan_echeancier(ECHEANCIER_03111, 1910, perte=0.0004)
        self.assertEqual(plan["dette"], 210.0)
        self.assertEqual(plan["perte"], 0.0)
        self.assertEqual(plan["nouvelles"], [])


class TestErreurRegularisation(unittest.TestCase):
    def test_03111_passe(self):
        self.assertIsNone(erreur_regularisation(COMMANDE_OK, bilan(3850, 1910, 1700, 2150)))

    def test_commande_non_validee(self):
        self.assertIn("validée", erreur_regularisation(
            dict(COMMANDE_OK, docstatus=0), bilan(3850, 1910, 1700, 2150)))

    def test_commande_fermee(self):
        self.assertIn("fermée", erreur_regularisation(
            dict(COMMANDE_OK, status="Closed"), bilan(3850, 1910, 1700, 2150)))

    def test_commande_facturee(self):
        self.assertIn("facturée", erreur_regularisation(
            dict(COMMANDE_OK, per_billed=100), bilan(13051, 11310, 12920.5, 130.5)))

    def test_rien_de_livre(self):
        self.assertIn("Rien", erreur_regularisation(COMMANDE_OK, bilan(1000, 0, 0, 1000)))

    def test_tout_livre(self):
        self.assertIn("entièrement", erreur_regularisation(COMMANDE_OK, bilan(1000, 1000, 500, 500)))

    def test_trop_percu(self):
        self.assertIn("trop-perçu", erreur_regularisation(COMMANDE_OK, bilan(1000, 400, 600, 400)))

    def test_echeancier_au_dessus_du_livre(self):
        self.assertIn("échéancier", erreur_regularisation(
            COMMANDE_OK, bilan(1000, 400, 0, 0), regle_echeancier=500))

    def test_echeancier_sous_le_livre_passe(self):
        self.assertIsNone(erreur_regularisation(
            COMMANDE_OK, bilan(1000, 400, 0, 600), regle_echeancier=100))


if __name__ == "__main__":
    unittest.main()


# ── Retours partiels : une partie de la marchandise est revenue ──────────────
# SAL-ORD-2026-03713 (Anis Jaâfar) : 3 tubes sortis, 1 rendu — la ligne garde 2.
LIGNES_03713 = [
    {"name": "ekh12v5tue", "item_code": "C-10'-PP-5m", "qty": 3, "delivered_qty": 2,
     "rate": 3.6, "uom": "Nos", "conversion_factor": 1, "delivery_date": date(2026, 9, 14)},
    {"name": "ekhuqkju0v", "item_code": "C-10'-UDF", "qty": 1, "delivered_qty": 1,
     "rate": 7, "uom": "Nos", "conversion_factor": 1, "delivery_date": date(2026, 9, 14)},
    {"name": "ekhlcf7r4j", "item_code": "C-10'-CTO", "qty": 1, "delivered_qty": 1,
     "rate": 7, "uom": "Nos", "conversion_factor": 1, "delivery_date": date(2026, 9, 14)},
]
BL_03713 = [
    {"so_detail": "ekhuqkju0v", "qty": 1, "amount": 7},
    {"so_detail": "ekhlcf7r4j", "qty": 1, "amount": 7},
    {"so_detail": "ekh12v5tue", "qty": 3, "amount": 10.8},
    {"so_detail": "ekh12v5tue", "qty": -1, "amount": -3.6},   # BL de retour
]

# WEB1-007881 (Patrick Patrickav) : l'appareil (120) est revenu, la livraison (10) reste.
LIGNES_007881 = [
    {"name": "nhon7n9444", "item_code": "TDS-PH", "qty": 1, "delivered_qty": 0,
     "rate": 120, "uom": "Nos", "conversion_factor": 1, "delivery_date": date(2026, 7, 28)},
    {"name": "nhovllqai3", "item_code": "Liv", "qty": 1, "delivered_qty": 1,
     "rate": 10, "uom": "Nos", "conversion_factor": 1, "delivery_date": date(2026, 7, 28)},
]
BL_007881 = [
    {"so_detail": "nhon7n9444", "qty": 1, "amount": 120},
    {"so_detail": "nhovllqai3", "qty": 1, "amount": 10},
    {"so_detail": "nhon7n9444", "qty": -1, "amount": -120},  # BL de retour
]


class TestPlanArticlesAvecRetour(unittest.TestCase):
    def test_03713_le_tube_rendu_sort_de_la_ligne(self):
        plan = plan_articles(LIGNES_03713, BL_03713)
        self.assertNotIn("erreur", plan)
        tubes = next(a for a in plan["articles"] if a["docname"] == "ekh12v5tue")
        self.assertEqual(tubes["qty"], 2)
        self.assertEqual(tubes["rate"], 3.6)
        self.assertEqual(plan["total"], 21.2)
        self.assertEqual(plan["supprimees"], [])

    def test_007881_l_article_entierement_revenu_disparait(self):
        plan = plan_articles(LIGNES_007881, BL_007881)
        self.assertNotIn("erreur", plan)
        self.assertEqual(plan["supprimees"], ["TDS-PH"])
        self.assertEqual([a["item_code"] for a in plan["articles"]], ["Liv"])
        self.assertEqual(plan["total"], 10)

    def test_un_retour_sans_ligne_de_commande_refuse(self):
        self.assertIn("erreur", plan_articles(
            LIGNES_007881, BL_007881 + [{"so_detail": None, "qty": -1, "amount": -5}]))

    def test_tout_revenu_rien_a_regulariser(self):
        self.assertIn("Aucune ligne", plan_articles(LIGNES_007881, [
            {"so_detail": "nhon7n9444", "qty": 1, "amount": 120},
            {"so_detail": "nhovllqai3", "qty": 1, "amount": 10},
            {"so_detail": "nhon7n9444", "qty": -1, "amount": -120},
            {"so_detail": "nhovllqai3", "qty": -1, "amount": -10},
        ])["erreur"])


RETOUR_03713 = {
    "name": "SAL-ORD-2026-03713", "customer": "Anis", "customer_name": "Anis Jaâfar",
    "transaction_date": date(2026, 9, 14), "status": "To Deliver and Bill", "docstatus": 1,
    "per_billed": 0, "currency": "TND",
    "total": 24.8, "livre": 21.2, "retourne": 3.6, "regle": 0, "dette_ligne": 0,
}


class TestLigneLivraisonPartielle(unittest.TestCase):
    def test_03111_sans_retour_figure_dans_la_liste_et_se_regularise(self):
        ligne = ligne_livraison_partielle(dict(
            RETOUR_03713, name="SAL-ORD-2026-03111", total=3850, livre=1910, retourne=0,
            regle=1700, dette_ligne=2150, regle_echeancier=1700))
        self.assertTrue(ligne["partielle"])
        self.assertTrue(ligne["surevaluee"])
        self.assertTrue(ligne["regularisable"])
        self.assertEqual(ligne["retourne"], 0)
        self.assertEqual(ligne["dette_reelle"], 210)

    def test_une_dette_juste_reste_a_regulariser_quand_le_client_ne_prendra_pas_le_reste(self):
        # Dette affichée = livré − réglé : pas d'anomalie, mais la commande
        # reste à ramener au livré si le reste ne sortira jamais.
        ligne = ligne_livraison_partielle(dict(
            RETOUR_03713, total=1000, livre=400, retourne=0, regle=100, dette_ligne=300,
            regle_echeancier=100))
        self.assertFalse(ligne["surevaluee"])
        self.assertTrue(ligne["regularisable"])

    def test_03713_est_une_livraison_partielle_regularisable(self):
        ligne = ligne_livraison_partielle(RETOUR_03713)
        self.assertTrue(ligne["partielle"])
        self.assertTrue(ligne["regularisable"])
        self.assertIsNone(ligne["empechement"])
        self.assertEqual(ligne["retourne"], 3.6)
        self.assertEqual(ligne["non_livre"], 3.6)
        self.assertEqual(ligne["dette_reelle"], 21.2)
        self.assertEqual(ligne["customer_name"], "Anis Jaâfar")
        self.assertEqual(ligne["date"], "2026-09-14")
        self.assertEqual(ligne["devise"], "TND")

    def test_une_dette_nulle_n_est_pas_surevaluee_mais_reste_a_regulariser(self):
        # Le motif d'anomalie ne la voit pas (dette affichée 0) : la liste, si.
        ligne = ligne_livraison_partielle(RETOUR_03713)
        self.assertFalse(ligne["surevaluee"])
        self.assertTrue(ligne["regularisable"])

    def test_commande_fermee_dit_pourquoi(self):
        ligne = ligne_livraison_partielle(dict(RETOUR_03713, status="Closed"))
        self.assertFalse(ligne["regularisable"])
        self.assertIn("fermée", ligne["empechement"])

    def test_commande_facturee_dit_pourquoi(self):
        ligne = ligne_livraison_partielle(dict(RETOUR_03713, per_billed=100))
        self.assertFalse(ligne["regularisable"])
        self.assertIn("facturée", ligne["empechement"])

    def test_007852_paye_en_entier_puis_rendu_140_est_un_trop_percu(self):
        # Sahbi Chouchane : 230 réglés à la livraison, 140 revenus — il faut
        # d'abord traiter le trop-perçu (avoir), pas régulariser.
        ligne = ligne_livraison_partielle(dict(
            RETOUR_03713, name="WEB1-007852", total=230, livre=90, retourne=140, regle=230))
        self.assertEqual(ligne["trop_percu"], 140)
        self.assertFalse(ligne["regularisable"])
        self.assertIn("trop-perçu", ligne["empechement"])

    def test_03713_payee_par_avoir_l_echeancier_depasse_le_livre(self):
        # En base : une ligne « Avoir client » de 24,8 (pas de Payment Entry,
        # SQL_REGLE ne la voit pas) pour 21,2 gardés — 3,6 à rendre d'abord.
        ligne = ligne_livraison_partielle(dict(RETOUR_03713, regle_echeancier=24.8))
        self.assertFalse(ligne["regularisable"])
        self.assertIn("3,600 DT", ligne["empechement"])
        self.assertIn("avoirs", ligne["empechement"])

    def test_un_echeancier_au_niveau_du_livre_passe(self):
        ligne = ligne_livraison_partielle(dict(RETOUR_03713, regle_echeancier=21.2))
        self.assertTrue(ligne["regularisable"])

    def test_le_nom_du_client_retombe_sur_son_code(self):
        ligne = ligne_livraison_partielle(dict(RETOUR_03713, customer_name=None))
        self.assertEqual(ligne["customer_name"], "Anis")
