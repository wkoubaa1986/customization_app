"""Type de transaction de la commande (18/11/2026) : « Vente directe sans BL et Facture » par défaut, le choix
répété dans le bloc de paiement et lié à celui du haut, le dinar de timbre sur le premier paiement. Branchements."""
from __future__ import annotations

import os
import unittest

from customization_app.patches import type_transaction_defaut as P

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTypeDeTransaction(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(RACINE, "public", "js", "sales_order_paiement.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_la_vente_directe_est_le_type_par_defaut(self):
        self.assertEqual((P.CHAMP, P.DEFAUT), ("Sales Order-custom_type_de_transaction", "Vente directe sans BL et Facture"))
        with open(os.path.join(RACINE, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("customization_app.patches.type_transaction_defaut", f.read())
        # commande neuve : vente directe = sans timbre, le modèle de taxes par défaut (avec timbre) est remplacé
        self.assertIn('frm.doc[TYPE] === DIRECTE && frm.doc.taxes_and_charges === TAXES_AVEC', self.js)
        self.assertIn('frm.set_value("taxes_and_charges", TAXES_SANS)', self.js)

    def test_le_choix_du_bloc_n_ecrit_que_dans_le_champ_du_haut(self):
        """Deux champs, une seule valeur : il ne peut pas y avoir deux types différents."""
        self.assertIn('if (frm.doc[TYPE] !== $(this).val()) frm.set_value(TYPE, $(this).val());', self.js)
        self.assertIn('[TYPE](frm) { rendre(frm);', self.js)                 # le champ du haut change : le bloc suit

    def test_le_dinar_de_timbre_va_au_premier_paiement(self):
        self.assertIn("function versLePremier(frm, m, cible)", self.js)
        self.assertIn("suivreLeTimbre(frm)", self.js)
        # la carte comprise : le serveur refait la majoration sur la nouvelle part
        corps = self.js[self.js.index("if (st.timbre) {"):self.js.index("if (carteEnJeu(frm)) { st.suite = 0; demander(frm); return; }")]
        self.assertIn("versLePremier(frm, m, cible);", corps)
        self.assertIn("apresGeste(frm);", corps)

    def test_l_etat_du_bloc_ne_passe_pas_d_une_commande_a_l_autre(self):
        self.assertIn("frm.__paiement.commande !== frm.doc.name", self.js)


if __name__ == "__main__":
    unittest.main()
