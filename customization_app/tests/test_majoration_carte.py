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
    """2 % de la part carte, arrondis AU DIXIÈME DE DINAR SUPÉRIEUR par défaut (demande du 18/11/2026) ; le pas et
    le sens de l'arrondi sont des réglages."""

    def test_deux_pour_cent_au_dixieme_superieur(self):
        self.assertEqual(M.montant_majoration(200.0, 2), 4.0)                # 4,000 tombe juste
        self.assertEqual(M.montant_majoration(285.0, 2), 5.7)                # 5,700 tombe juste
        self.assertEqual(M.montant_majoration(368.597, 2), 7.4)              # 7,372 -> 7,400
        self.assertEqual(M.montant_majoration(276.962, 2), 5.6)              # 5,539 -> 5,600
        self.assertEqual(M.montant_majoration(365.05, 2), 7.4)               # 7,301 -> 7,400 : toujours vers le haut
        self.assertEqual(M.montant_majoration(12.0, 2), 0.3)                 # 0,240 -> 0,300
        self.assertEqual(M.montant_majoration(20.0, 2), 0.4)

    def test_une_valeur_deja_ronde_ne_monte_pas(self):
        for v in (0.1, 0.2, 0.3, 0.7, 1.1, 5.7, 40.0, 58.5):
            self.assertEqual(M.arrondir_au_pas(v), v)
        self.assertEqual(M.arrondir_au_pas(0.101), 0.2)

    def test_les_autres_reglages(self):
        self.assertEqual(M.montant_majoration(285.0, 2, pas=0.5, superieur=False), 5.5)   # demi-dinar le plus proche
        self.assertEqual(M.montant_majoration(285.0, 2, pas=0.5, superieur=True), 6.0)    # demi-dinar supérieur
        self.assertEqual(M.montant_majoration(365.05, 2, pas=0.1, superieur=False), 7.3)  # dixième le plus proche
        self.assertEqual(M.montant_majoration(368.597, 2, pas=1, superieur=True), 8.0)    # dinar supérieur
        self.assertEqual(M.montant_majoration(368.597, 2, pas=0), 7.372)                  # pas d'arrondi : au millime
        self.assertEqual(M.arrondir_au_pas(0.25, 0.5, superieur=False), 0.5)              # la moitié monte

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

    def test_prix_majore_recalcule_par_erpnext_a_un_millime_pres(self):
        """Constat du 18/11/2026. Ligne à règle de prix : ERPNext garde la remise en % (3 décimales) et recalcule le
        prix — un osmoseur à 650 avec 5 % de remise, majoré à 629,812, revient à 629,811. Ce n'est PAS un prix
        retapé : la base reste 617,500, sinon la commande est majorée deux fois."""
        remise = round((1 - 629.812 / 650.0) * 100, 3)
        recalcule = round(650.0 * (1 - remise / 100), 3)
        self.assertEqual(recalcule, 629.811)
        self.assertEqual(M.prix_de_base(recalcule, 617.5, 629.812), 617.5)
        self.assertEqual(M.prix_de_base(629.816, 617.5, 629.812), 617.5)      # jusqu'à 5 millimes (ou 0,002 %)
        self.assertEqual(M.prix_de_base(2000.03, 1960.0, 2000.0), 1960.0)     # prix élevé : 0,002 % = 0,040

    def test_prix_retape_a_la_main(self):
        """Ni la base ni le prix majoré : quelqu'un a retapé le prix, c'est la nouvelle base."""
        self.assertEqual(M.prix_de_base(120.0, 105.0, 107.114), 120.0)


class TestPrixTenable(unittest.TestCase):
    """Sur une ligne à règle de prix, on ne pose que les prix qu'ERPNext sait garder (remise en %, 3 décimales)."""

    def test_le_prix_tenable_est_celui_qu_erpnext_recalcule(self):
        self.assertEqual(M.prix_tenable(629.812, 650.0), 629.811)
        self.assertEqual(M.prix_tenable(28.558, 35.0), 28.558)              # déjà tenable
        self.assertEqual(M.prix_tenable(M.prix_tenable(629.812, 650.0), 650.0), 629.811)   # point fixe

    def test_au_dessus_du_prix_de_liste_rien_a_faire(self):
        """Au-dessus du prix de liste, le prix est posé en marge (montant) : pas de remise en %, pas d'arrondi."""
        self.assertEqual(M.prix_tenable(662.962, 650.0), 662.962)
        self.assertEqual(M.prix_tenable(10.0, 0), 10.0)

    def test_la_repartition_tombe_juste_avec_une_ligne_a_regle_de_prix(self):
        lignes = [{"base": 393.49, "qty": 3, "prix_liste": 414.2, "regle": True},      # 5 % de remise (règle)
                  {"base": 305.9, "qty": 5, "prix_liste": 322.0, "regle": True},
                  {"base": 105.0, "qty": 1}, {"base": 4.0, "qty": 20}]
        base = total(lignes, [l["base"] for l in lignes])
        majoration = M.montant_majoration(base, 2)
        prix = M.repartir(lignes, majoration)
        self.assertEqual(total(lignes, prix), round(base + majoration, 3))
        for l, p in zip(lignes[:2], prix[:2]):
            self.assertEqual(p, M.prix_tenable(p, l["prix_liste"]))          # ERPNext ne les bougera pas


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

    def test_deux_lignes_se_partagent_les_millimes_quand_aucune_ne_suffit(self):
        """Quantités 3 et 7, majoration de 1,500 : aucune des deux ne divise l'écart, mais ensemble elles tombent juste."""
        lignes = [{"base": 20.0, "qty": 3}, {"base": 10.0, "qty": 7}]
        prix = M.repartir(lignes, 1.5)
        self.assertEqual(total(lignes, prix), 131.5)                         # 130 + 1,5, au millime
        for l, p in zip(lignes, prix):
            self.assertAlmostEqual(p / l["base"], 1 + 1.5 / 130.0, places=2)   # reste proportionnel, à quelques millimes

    def test_majoration_sur_la_commande_reelle(self):
        """285,000 par carte : majoration de 5,700, total des articles 289,700 exactement."""
        self.assertEqual(M.montant_majoration(285.0, 2), 5.7)
        self.assertEqual(total(self.COMMANDE, M.repartir(self.COMMANDE, 5.7)), 289.7)

    def test_un_seul_article_en_34_exemplaires_reste_au_plus_pres(self):
        """4,000 ne se divise pas par 34 au millime : impossible de tomber juste avec un prix unitaire."""
        lignes = [{"base": 10.0, "qty": 34}]
        self.assertLessEqual(abs(total(lignes, M.repartir(lignes, 4.0)) - 344.0), 0.017)

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

    def test_le_taux_et_l_arrondi_viennent_du_reglage_bancaire(self):
        self.assertEqual((M.REGLAGE, M.CHAMP_TAUX_REGLAGE), ("Bank Retenue Sync Settings", "majoration_carte_taux"))
        self.assertEqual((M.CHAMP_PAS_REGLAGE, M.CHAMP_SENS_REGLAGE), ("majoration_carte_arrondi", "majoration_carte_arrondi_sens"))
        self.assertEqual((M.PAS_ARRONDI, M.SENS_SUPERIEUR), (0.1, "Supérieur"))      # à défaut de réglage
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

    def test_la_majoration_ne_s_ajoute_que_sur_la_ligne_carte(self):
        """Demandes du 18/11/2026 : « la part réglée par carte doit prendre en compte la majoration », puis « la
        majoration doit s'ajouter que sur la ligne carte, pas espèces ». On tape la PART de la commande (200) : les
        autres lignes ne voient que cette part, et la ligne carte affiche part + majoration (204)."""
        js = self.lire("public", "js", "sales_order_paiement.js")
        self.assertIn('estCarte(r) ? "Montant par carte" : "Montant"', js)
        self.assertIn("format_number(estCarte(r) ? flt(r.payment_amount) : part, null, 3)", js)
        self.assertIn("estCarte(r) ? avecMajoration(frm, m.get(r.name)) : arrondi(m.get(r.name))", js)
        self.assertNotIn("sansMajoration", js)                    # le montant tapé n'est plus « majoration comprise »
        self.assertIn('data-part="${format_number(part, null, 3)}"', js)   # en reprenant le champ : la part tapée
        self.assertIn('"customization_app.majoration_carte.taux_pour_ecran"', js)
        self.assertIn("Math.ceil(crans - 1e-9)", js)                       # même arrondi (supérieur) que le serveur
        self.assertIn("reglage.superieur === false", js)                   # ... ou au plus proche, selon le réglage

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
