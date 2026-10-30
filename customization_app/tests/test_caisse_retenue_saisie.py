"""Saisie de la retenue à la source depuis la fenêtre « Dépense de caisse » (30/10/2026).

Cas réel : chèque 4001060 (Ste HEDI RIAHI) émis à 4 666,266 pour 4 713,400 TTC. Le montant,
rempli par l'analyse de la facture, n'a jamais recalculé la retenue (`set_value` ne déclenche
pas `change`), la fenêtre a envoyé « 0 » au serveur, et l'écriture est partie au brut.

Convention : `unittest.TestCase` pur, aucune base — on éprouve la logique serveur (via
bank_retenue_sync) et la présence des garde-fous dans le dialogue.
"""
from __future__ import annotations

import inspect
import os
import re
import unittest

from customization_app import caisse_depenses as CD

DIALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "customize_erpnext", "page", "caisse_journaliere",
                        "caisse_journaliere.js")


def _bloc_depense():
    with open(DIALOGUE, encoding="utf-8") as f:
        js = f.read()
    a = js.index("function rcj_depense(rapport, initial) {")
    b = js.index("RapportCaisseJournaliere.prototype._render_depenses", a)
    return js[a:b]


class TestLogiqueServeur(unittest.TestCase):
    def test_les_types_qui_partent_du_paiement(self):
        self.assertEqual(CD.TYPES_PAIEMENT_NET, ("Dépense avec facture", "Facture d'achat"))
        self.assertEqual(CD.MODES_PAIEMENT_NET, ("Chèque", "Virement"))

    def test_le_montant_paye_donne_la_retenue(self):
        c = CD._controle_retenue("Facture d'achat", 4713.4, paye=4666.266)
        self.assertEqual((c["retenue"], c["net"]), (47.134, 4666.266))
        self.assertFalse(c["alerte_sans_retenue"])

    def test_zero_sur_une_depense_assujettie_est_une_alerte(self):
        c = CD._controle_retenue("Facture d'achat", 4713.4, retenue=0)
        self.assertTrue(c["alerte_sans_retenue"])

    def test_creer_accepte_le_paiement_net_et_la_confirmation(self):
        params = inspect.signature(CD.creer).parameters
        for p in ("montant_paye", "timbre", "sans_retenue_confirme"):
            self.assertIn(p, params)
        self.assertIn("paye", inspect.signature(CD.proposition_retenue).parameters)

    def test_creer_refuse_une_depense_assujettie_sans_retenue_ni_confirmation(self):
        src = inspect.getsource(CD.creer)
        self.assertIn('if ctrl["alerte_sans_retenue"] and not cint(sans_retenue_confirme):', src)
        self.assertIn("confirmé par", src)

    def test_l_analyse_rend_le_timbre(self):
        self.assertIn('"timbre": flt(d.get("stamp_duty"), 3)', inspect.getsource(CD.analyser))

    def test_la_remarque_de_retenue_reste_lisible_par_la_file_des_certificats(self):
        """bank_retenue_sync lit « Retenue à la source achat : X sur Y TTC » ; la mention
        ajoutée (« Retenue déduite du montant payé ») ne doit pas la parasiter."""
        from bank_retenue_sync.tej import emis_journal as F
        remarque = "\n".join([
            "Fontaine inox — Ste HEDI RIAHI — Fact. n°03/26",
            "Retenue à la source achat : 47.134 sur 4713.4 TTC",
            "Retenue déduite du montant payé (4666.266)",
            "Chq N° 4001060 - Bq Banque Zitouna",
        ])
        m = F._RETENUE.search(remarque)
        self.assertEqual((m.group(1).strip(), m.group(2).strip()), ("47.134", "4713.4"))


class TestDialogue(unittest.TestCase):
    def setUp(self):
        self.js = _bloc_depense()

    def test_le_champ_montant_paye_existe_pour_cheque_et_virement(self):
        self.assertIn('fieldname: "montant_paye"', self.js)
        self.assertIn('["Chèque","Virement"].includes(doc.mode) && !doc.fractionne', self.js)

    def test_l_analyse_recalcule_la_retenue(self):
        """`set_value` ne déclenche pas `change` : l'appel doit être explicite, après le
        remplissage du montant et du fournisseur."""
        i = self.js.index("etat.analyse_faite = true;")
        suite = self.js[i:i + 400]
        self.assertIn("maj_retenue();", suite)
        self.assertIn("etat.timbre = flt(m.timbre || 0);", self.js)

    def test_le_serveur_ne_recoit_plus_zero_par_defaut(self):
        self.assertNotIn("retenue: flt(v.retenue || 0),", self.js)
        self.assertIn("(retenue_touchee || paye_touche || paiements) ? flt(v.retenue || 0) : null", self.js)
        self.assertIn("sans_retenue_confirme: confirme ? 1 : 0", self.js)
        self.assertIn("montant_paye: paye_net ? flt(v.montant_paye) : null", self.js)

    def test_le_garde_fou_demande_confirmation(self):
        self.assertIn("frappe.confirm(", self.js)
        self.assertIn("envoyer(1)", self.js)
        self.assertRegex(self.js, re.compile(r"etat\.assujettie\s*&&\s*flt\(v\.retenue \|\| 0\) <= 0"))

    def test_le_mode_et_le_fractionne_recalculent(self):
        self.assertIn('maj_libelles_mode(); maj_retenue(); });', self.js)
        self.assertIn('rendre_paiements(); maj_retenue(); });', self.js)
        self.assertIn('d.fields_dict.montant_paye.$input.on("input"', self.js)
