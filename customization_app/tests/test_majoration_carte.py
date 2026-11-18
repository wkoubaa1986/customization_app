"""Majoration « paiement par carte » (customization_app.majoration_carte) : la part réglée par carte est
majorée du taux réglé, la majoration répartie sur les prix de tous les articles. Fonctions pures + branchements."""
from __future__ import annotations

import os
import unittest

from customization_app import majoration_carte as M

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def total(lignes, prix):
    return round(sum(round(p * l["qty"], 3) for l, p in zip(lignes, prix)), 3)


class TestMontant(unittest.TestCase):
    def test_deux_pour_cent_de_la_part_carte(self):
        self.assertEqual(M.montant_majoration(285.0, 2), 5.7)
        self.assertEqual(M.montant_majoration(1000.0, 2), 20.0)
        self.assertEqual(M.montant_majoration(185.0, 2), 3.7)
        self.assertEqual(M.montant_majoration(276.962, 2), 5.539)          # au millime

    def test_rien_a_majorer(self):
        self.assertEqual(M.montant_majoration(0, 2), 0.0)
        self.assertEqual(M.montant_majoration(-10, 2), 0.0)
        self.assertEqual(M.montant_majoration(285.0, 0), 0.0)               # taux réglé à 0 : fonction éteinte
        self.assertEqual(M.montant_majoration(None, None), 0.0)


class TestPrixDeBase(unittest.TestCase):
    def test_ligne_jamais_majoree(self):
        self.assertEqual(M.prix_de_base(105.0, 0, 0), 105.0)

    def test_ligne_majoree_inchangee(self):
        self.assertEqual(M.prix_de_base(107.114, 105.0, 107.114), 105.0)

    def test_prix_retombe_a_la_base(self):
        """ERPNext a ré-appliqué la règle de prix : le prix affiché est redevenu celui d'avant majoration."""
        self.assertEqual(M.prix_de_base(105.0, 105.0, 107.114), 105.0)

    def test_prix_retape_a_la_main(self):
        """Ni la base ni le prix majoré : quelqu'un a retapé le prix, c'est la nouvelle base."""
        self.assertEqual(M.prix_de_base(120.0, 105.0, 107.114), 120.0)


class TestRepartition(unittest.TestCase):
    # SAL-ORD-2026-04724 : 284,000 d'articles (+ 1,000 de timbre), tout par carte -> majoration 5,700.
    COMMANDE = [{"base": 105.0, "qty": 1}, {"base": 4.0, "qty": 20}, {"base": 35.0, "qty": 1}, {"base": 6.0, "qty": 2},
                {"base": 10.0, "qty": 1}, {"base": 6.0, "qty": 3}, {"base": 8.0, "qty": 3}]

    def test_le_total_tombe_juste(self):
        prix = M.repartir(self.COMMANDE, 5.7)
        self.assertEqual(total(self.COMMANDE, prix), 289.7)                 # 284 + 5,7, au millime

    def test_tous_les_articles_augmentent_dans_la_meme_proportion(self):
        prix = M.repartir(self.COMMANDE, 5.7)
        for l, p in zip(self.COMMANDE, prix):
            self.assertGreater(p, l["base"])
            self.assertAlmostEqual(p / l["base"], 1 + 5.7 / 284.0, places=3)

    def test_les_millimes_d_arrondi_vont_sur_une_seule_ligne(self):
        prix = M.repartir(self.COMMANDE, 5.7)
        exacts = [round(l["base"] * (1 + 5.7 / 284.0), 3) for l in self.COMMANDE]
        self.assertEqual(sum(1 for p, e in zip(prix, exacts) if p != e), 1)
        self.assertEqual(prix[0], 107.114)                                   # la plus grosse ligne à l'unité

    def test_sans_majoration_les_prix_de_base(self):
        self.assertEqual(M.repartir(self.COMMANDE, 0), [l["base"] for l in self.COMMANDE])
        self.assertEqual(M.repartir([{"base": 0.0, "qty": 1}], 5.0), [0.0])  # rien sur quoi répartir

    def test_une_seule_ligne_en_plusieurs_exemplaires(self):
        """Quantité 3 : un reste d'un millime ne se divise pas par 3, le total approche la cible au millime."""
        lignes = [{"base": 10.0, "qty": 3}]
        prix = M.repartir(lignes, 0.61)
        self.assertLessEqual(abs(total(lignes, prix) - 30.61), 0.002)

    def test_ligne_negative_d_un_echange(self):
        """La pièce reprise (ligne négative) suit la même proportion : la commande augmente bien de la majoration."""
        lignes = [{"base": 100.0, "qty": 1}, {"base": -20.0, "qty": 1}]
        prix = M.repartir(lignes, 1.6)
        self.assertEqual(total(lignes, prix), 81.6)
        self.assertLess(prix[1], -20.0)


class TestBranchements(unittest.TestCase):
    def lire(self, *chemin):
        with open(os.path.join(RACINE, *chemin), encoding="utf-8") as f:
            return f.read()

    def test_le_calcul_passe_apres_la_marge_minimale(self):
        hooks = self.lire("hooks.py")
        marge = hooks.index('"customization_app.marge_minimale.sales_order_validate"')
        carte = hooks.index('"customization_app.majoration_carte.sales_order_validate"')
        self.assertLess(marge, carte)
        self.assertIn('"customization_app.majoration_carte.memoriser_echeancier"', hooks)
        self.assertIn('"customization_app.majoration_carte.avertir_apres_validation"', hooks)
        self.assertIn('"customization_app.jinja_methods.majoration_carte"', hooks)
        self.assertIn('"public/js/sales_order_paiement.js"', hooks)

    def test_le_taux_vient_du_reglage_bancaire(self):
        self.assertEqual((M.REGLAGE, M.CHAMP_TAUX_REGLAGE), ("Bank Retenue Sync Settings", "majoration_carte_taux"))
        self.assertEqual(M.MODE_CARTE, "Carte de crédit")

    def test_le_patch_cree_les_champs(self):
        patch = self.lire("patches", "majoration_carte.py")
        for champ in (M.CHAMP_MONTANT, M.CHAMP_TAUX, M.CHAMP_AVANT, M.CHAMP_AVEC):
            self.assertIn('"fieldname": "%s"' % champ, patch)
        self.assertIn("customization_app.patches.majoration_carte", self.lire("patches.txt"))

    def test_l_ecran_demande_l_apercu_au_serveur_et_l_applique(self):
        js = self.lire("public", "js", "sales_order_paiement.js")
        self.assertIn('"customization_app.majoration_carte.apercu"', js)
        self.assertIn("montant à passer sur le terminal", js)
        # temps réel : les prix rendus par l'aperçu sont posés sur les lignes, sans attendre l'enregistrement
        self.assertIn('frappe.model.set_value(ligne.doctype, ligne.name, "rate", l.rate)', js)
        apercu = self.lire("majoration_carte.py")
        self.assertIn('etat["lignes"] = ', apercu)
        self.assertIn('etat["echeances"] = ', apercu)

    def test_la_ligne_carte_affiche_le_montant_majoration_comprise(self):
        """Demande du 18/11/2026 : « la part réglée par carte doit prendre en compte la majoration, tout le temps
        actualisée ». Le champ montre ce que porte le tableau (part + majoration), et le montant tapé est compris
        de la même façon."""
        js = self.lire("public", "js", "sales_order_paiement.js")
        self.assertIn('estCarte(r) ? "Montant par carte" : "Montant"', js)
        self.assertIn("format_number(estCarte(r) ? flt(r.payment_amount) : part, null, 3)", js)
        self.assertIn("if (r && estCarte(r)) v = sansMajoration(frm, v);", js)
        self.assertIn("estCarte(r) ? avecMajoration(frm, m.get(r.name)) : arrondi(m.get(r.name))", js)
        self.assertIn('"customization_app.majoration_carte.taux_pour_ecran"', js)
        self.assertEqual(M.taux_pour_ecran.__name__, "taux_pour_ecran")

    def test_le_bloc_ecrit_dans_le_tableau_standard(self):
        """Aucune donnée de plus : le bloc lit et écrit `payment_schedule`, que tout le reste du système lit."""
        js = self.lire("public", "js", "sales_order_paiement.js")
        self.assertIn('frm.add_child("payment_schedule"', js)
        self.assertIn('frm.refresh_field("payment_schedule")', js)
        for regle in ("7 chiffres", "Banque à choisir", "Référence de la transaction par carte à indiquer",
                      "Référence de la livraison Aramex à indiquer"):
            self.assertIn(regle, js)


if __name__ == "__main__":
    unittest.main()
