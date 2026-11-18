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
        corps = self.js[self.js.index("if (st.timbreJusqua && Date.now() < st.timbreJusqua) {"):
                        self.js.index("if (carteEnJeu(frm)) {\n            // Le total a bougé")]
        self.assertIn("versLePremier(frm, m, cible);", corps)
        self.assertIn("apresGeste(frm);", corps)

    def test_le_total_affiche_suit_la_commande_meme_sans_paiement(self):
        """Constat du 18/11/2026 : sans paiement saisi, « Total à régler » restait sur l'ancienne valeur après le
        passage à « Autre — avec timbre fiscal ». Le bloc veille le total de la commande et se redessine."""
        self.assertIn("window.__so_paiement_veille = setInterval(veiller, 500)", self.js)
        self.assertIn("if (!rs.length) { rendre(frm); return; }", self.js)
        self.assertIn("if (!st.application && Math.abs(t - st.dernierTotal) >= 0.001) { suivreLeTotal(frm); return; }", self.js)

    def test_l_etat_du_bloc_ne_passe_pas_d_une_commande_a_l_autre(self):
        self.assertIn("frm.__paiement.commande !== frm.doc.name", self.js)


if __name__ == "__main__":
    unittest.main()


class TestTaxesRepliables(unittest.TestCase):
    """Commande : la partie « Taxes » est repliable ; le tableau et le total des taxes (sections sans titre)
    se replient avec elle (18/11/2026)."""

    def test_le_reglage_et_le_script_sont_branches(self):
        from customization_app.patches import taxes_repliables_commande as T
        self.assertEqual(T.SECTION, "taxes_section")
        with open(os.path.join(RACINE, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("customization_app.patches.taxes_repliables_commande", f.read())
        with open(os.path.join(RACINE, "hooks.py"), encoding="utf-8") as f:
            self.assertIn('"public/js/sales_order_taxes_repliables.js"', f.read())
        with open(os.path.join(RACINE, "public", "js", "sales_order_taxes_repliables.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('const TETE = "taxes_section", SUITE = ["section_break_40", "section_break_43"];', js)
        self.assertIn("$(s.wrapper).toggle(!fermee)", js)
