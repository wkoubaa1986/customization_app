"""Avoir du client dans le bloc de paiement de la commande (18/11/2026) : ajustement automatique piloté par la
case « Paiement avec Avoir », jamais un moyen de paiement à choisir ; le Client Script en base rend la main.
Patch pur + branchements."""
from __future__ import annotations

import os
import unittest

from customization_app.patches import avoir_bloc_paiement as P

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = ("frappe.ui.form.on('Sales Order', {\r\n     custom_paiement_avec_avoir: function(frm) {\r\n"
          "        if (frm.doc.custom_paiement_avec_avoir === 'Oui') {\r\n            adjust_payment_schedule(frm);\r\n"
          "        } else {\r\n            frm._avoir_applied=false;\r\n            if (frm.doc.payment_terms_template) {\r\n"
          "            }\r\n        }\r\n    },\r\n});\r\n"
          "// function adjust_payment_schedule(frm) {\r\n//     ancienne version\r\n// }\r\n"
          "function adjust_payment_schedule(frm) {\r\n    const use_credit_note = true;\r\n}\r\n")


class TestLeScriptRendLaMain(unittest.TestCase):
    def test_les_deux_gardes_sont_posees(self):
        nouveau, fait = P.corriger(SCRIPT)
        self.assertEqual(fait, "modifié")
        self.assertIn("function adjust_payment_schedule(frm) { " + P.GARDE + "\r\n", nouveau)      # « Oui »
        self.assertIn("frm._avoir_applied=false; " + P.GARDE + "\r\n", nouveau)                    # « Non »
        self.assertEqual(nouveau.count(P.GARDE), 2)

    def test_la_version_commentee_n_est_pas_touchee(self):
        nouveau, _ = P.corriger(SCRIPT)
        self.assertIn("// function adjust_payment_schedule(frm) {\r\n", nouveau)

    def test_fins_de_ligne_intactes_et_rejouable(self):
        nouveau, _ = P.corriger(SCRIPT)
        self.assertEqual(nouveau.count("\r\n"), SCRIPT.count("\r\n"))
        self.assertEqual(nouveau.count("\n"), SCRIPT.count("\n"))
        self.assertEqual(P.corriger(nouveau), (nouveau, "déjà fait"))

    def test_un_script_inconnu_est_laisse_tel_quel(self):
        self.assertEqual(P.corriger("autre chose"), ("autre chose", "jeton introuvable"))


class TestLeBloc(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(RACINE, "public", "js", "sales_order_paiement.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_le_bloc_se_signale_au_script(self):
        self.assertIn("window.__so_paiement_bloc = true;", self.js)
        self.assertIn("window.__so_paiement_bloc", P.GARDE)
        with open(os.path.join(RACINE, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("customization_app.patches.avoir_bloc_paiement", f.read())

    def test_l_avoir_n_est_pas_un_moyen_de_paiement_a_choisir(self):
        modes = self.js[self.js.index("const MODES = ["):self.js.index("];", self.js.index("const MODES = ["))]
        self.assertNotIn("Avoir", modes)                                    # ni bouton « ＋ », ni choix dans la liste
        ligne = self.js[self.js.index("if (estAvoir(r)) {"):self.js.index("const regle = REGLES[r.mode_of_payment] || {}, d = defauts(frm, r), st = etat(frm);")]
        self.assertIn("disabled", ligne)                                    # montant non modifiable
        self.assertNotIn("sp-suppr", ligne)                                 # pas de ✕
        self.assertNotIn("sp-in-mode", ligne)

    def test_la_case_pilote_l_ajustement(self):
        self.assertIn("[CHOIX_AVOIR](frm) { suivreAvoir(frm); },", self.js)
        self.assertIn('const voulu = oui ? arrondi(Math.max(Math.min(flt(st.avoir), cible), 0)) : 0;', self.js)
        # le total change (ristourne, article) : l'avoir se recale d'abord
        self.assertIn("if (ajusterAvoir(frm)) { apresGeste(frm); return; }", self.js)

    def test_l_avoir_ne_sert_jamais_a_equilibrer_et_un_doublon_est_fusionne(self):
        self.assertIn("const autres = noms.filter((n) => n !== garde && !avoirs.has(n));", self.js)
        self.assertIn("avoirs.slice(1).forEach((r) => { m.delete(r.name); oterLigne(frm, r.name); });", self.js)


if __name__ == "__main__":
    unittest.main()
