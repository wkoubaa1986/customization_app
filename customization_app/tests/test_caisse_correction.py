"""Corriger une dépense de caisse facturée a posteriori (demande utilisateur 30/10/2026).

Cas d'origine : ACC-JV-2026-00954 (chèque 4001060 émis net de retenue, écriture au brut) et
ACC-JV-2026-00937 (chèque 4001056 débité 841,100 pour 4 891,000).

Convention : `unittest.TestCase` pur — `lire_saisie` et `motif_non_corrigeable` sont des
fonctions pures ; le reste se vérifie sur le texte du dialogue.
"""
from __future__ import annotations

import inspect
import os
import unittest

from customization_app import caisse_depenses as CD

DIALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "customize_erpnext", "page", "caisse_journaliere",
                        "caisse_journaliere.js")

BANQUE = CD.COMPTE_BANQUE
RETENUE = CD.COMPTE_RETENUE_ACHAT

REMARQUE_00954 = "\n".join([
    "Fontaine inox 304L 90 litres/heure — Ste HEDI RIAHI — Fact. n°03/26",
    "Type : Facture d'achat", "Fournisseur : Ste HEDI RIAHI", "Facture n° 03/26",
    "Chq N° 4001060 - Bq Banque Zitouna", "Saisie caisse par sadokaquaworld@gmail.com"])
LIGNES_00954 = [{"account": BANQUE, "debit": 0, "credit": 4713.4},
                {"account": CD.COMPTE_CREDITEURS, "debit": 4713.4, "credit": 0}]

REMARQUE_00920 = "\n".join([
    "pièces automobiles et huile moteur — SOUTD TRADE COMPANY — Fact. n°FC20262476",
    "Type : Dépense avec facture", "Fournisseur : SOUTD TRADE COMPANY",
    "Facture n° FC20262476", "Retenue à la source achat : 16.129 sur 1613.969 TTC",
    "Chq N° 4001053 - Bq Banque Zitouna", "Saisie caisse par aquaworld.servicing@gmail.com"])
LIGNES_00920 = [{"account": BANQUE, "debit": 0, "credit": 1597.84},
                {"account": RETENUE, "debit": 0, "credit": 16.129},
                {"account": "TVA 19% - A&S", "debit": 258.534, "credit": 0},
                {"account": "Charges Diverses - A&S", "debit": 1355.435, "credit": 0}]


class TestRelectureDeLaSaisie(unittest.TestCase):
    def setUp(self):
        self._vrai = CD._
        CD._ = lambda message: message

    def tearDown(self):
        CD._ = self._vrai

    def test_facture_d_achat_au_brut(self):
        s = CD.lire_saisie(CD.PREFIXE_CAISSE + "Fontaine inox 304L 90 litres/heure — Ste HEDI RIAHI — Fact. n°03/26",
                           REMARQUE_00954, LIGNES_00954)
        self.assertEqual(s["type_depense"], "Facture d'achat")
        self.assertEqual(s["description"], "Fontaine inox 304L 90 litres/heure — Ste HEDI RIAHI — Fact. n°03/26")
        self.assertEqual(s["fournisseur"], "Ste HEDI RIAHI")
        self.assertEqual(s["numero_facture"], "03/26")
        self.assertEqual((s["montant"], s["retenue"], s["paye_banque"]), (4713.4, 0.0, 4713.4))
        self.assertEqual((s["mode"], s["n_cheque"], s["banque"]), ("Chèque", "4001060", "Banque Zitouna"))
        self.assertEqual(s["montant_paye"], 4713.4)      # ce que la fenêtre doit pré-remplir
        self.assertFalse(s["fractionne"])

    def test_depense_avec_facture_et_retenue(self):
        s = CD.lire_saisie(CD.PREFIXE_CAISSE + "pièces automobiles", REMARQUE_00920, LIGNES_00920)
        self.assertEqual(s["type_depense"], "Dépense avec facture")
        self.assertEqual((s["montant"], s["retenue"], s["paye_banque"]), (1613.969, 16.129, 1597.84))
        self.assertEqual((s["tva"], s["taux_tva"], s["compte"]), (258.534, 19, "Charges Diverses - A&S"))
        self.assertEqual(s["n_cheque"], "4001053")

    def test_especes_et_fractionne(self):
        lignes = [{"account": CD.COMPTE_ESPECES, "debit": 0, "credit": 100},
                  {"account": BANQUE, "debit": 0, "credit": 900},
                  {"account": "Charges Diverses - A&S", "debit": 1000, "credit": 0}]
        s = CD.lire_saisie(CD.PREFIXE_CAISSE + "x", "Type : Dépense avec facture\nChq N° 4001099 - Bq BIAT", lignes)
        self.assertTrue(s["fractionne"])
        self.assertEqual([r["mode"] for r in s["reglements"]], ["Espèces", "Chèque"])
        self.assertEqual(s["montant_paye"], 0.0)

    def test_virement_et_pas_paye(self):
        lignes = [{"account": BANQUE, "debit": 0, "credit": 500},
                  {"account": "Charges Diverses - A&S", "debit": 500, "credit": 0}]
        s = CD.lire_saisie(CD.PREFIXE_CAISSE + "x",
                           "Type : Dépense avec facture\nRéglé par virement | Réf de paiement : FT26300ABCDE", lignes)
        self.assertEqual((s["mode"], s["n_cheque"], s["montant_paye"]), ("Virement", "FT26300ABCDE", 500.0))
        lignes = [{"account": CD.COMPTE_DECOUVERT, "debit": 0, "credit": 500},
                  {"account": "Charges Diverses - A&S", "debit": 500, "credit": 0}]
        s = CD.lire_saisie(CD.PREFIXE_A_PAYER + "x", "Type : Dépense avec facture", lignes)
        self.assertEqual(s["mode"], CD.MODE_PAS_PAYE)
        self.assertEqual(s["description"], "x")


class TestGardeFous(unittest.TestCase):
    def setUp(self):
        self._vrai = CD._
        CD._ = lambda message: message

    def tearDown(self):
        CD._ = self._vrai

    def _infos(self, **k):
        base = {"name": "JE-1", "type_depense": "Facture d'achat", "fractionne": 0}
        base.update(k)
        return base

    def test_le_cas_nominal_passe(self):
        self.assertEqual(CD.motif_non_corrigeable(self._infos()), "")

    def test_seules_les_depenses_facturees(self):
        self.assertIn("facturées", CD.motif_non_corrigeable(self._infos(type_depense="Dépense non facturée")))

    def test_pas_de_fractionne(self):
        self.assertIn("fractionné", CD.motif_non_corrigeable(self._infos(fractionne=1)))

    def test_facture_deja_saisie(self):
        self.assertIn("ACC-PINV-1", CD.motif_non_corrigeable(self._infos(), fiche={"purchase_invoice": "ACC-PINV-1"}))

    def test_reglee_depuis(self):
        self.assertIn("JE-9", CD.motif_non_corrigeable(self._infos(), depense={"reglement_journal_entry": "JE-9"}))
        self.assertEqual(CD.motif_non_corrigeable(self._infos(), depense={"reglement_journal_entry": "JE-1"}), "")

    def test_traite_et_certificat(self):
        self.assertIn("traite", CD.motif_non_corrigeable(self._infos(), ordre_traite=True))
        self.assertIn("TEJ", CD.motif_non_corrigeable(self._infos(), certificat_statut="Émis"))
        self.assertEqual(CD.motif_non_corrigeable(self._infos(), certificat_statut="En file"), "")

    def test_ecriture_hors_caisse_ou_annulee(self):
        self.assertIn("caisse", CD.motif_non_corrigeable(self._infos(), origine=False))
        self.assertIn("validée", CD.motif_non_corrigeable(self._infos(), docstatus=2))


class TestBranchement(unittest.TestCase):
    def test_creer_sait_reprendre_une_fiche(self):
        self.assertIn("reprise", inspect.signature(CD.creer).parameters)
        src = inspect.getsource(CD.creer)
        self.assertIn('if reprise.get("fiche"):', src)
        self.assertIn('if reprise.get("depense"):', src)
        self.assertIn("Corrige l'écriture", src)

    def test_corriger_annule_puis_supprime_l_ancienne_et_reporte_les_mouvements(self):
        """L'ancienne écriture ne reste pas « Annulée » dans la liste (demande 30/10/2026) :
        annulée d'abord (un exercice clos arrête tout avant création), supprimée après que la
        nouvelle existe, avec sa ligne de file des certificats."""
        src = inspect.getsource(CD.corriger)
        self.assertIn("je.cancel()", src)
        self.assertIn('frappe.delete_doc("Journal Entry", je.name, ignore_permissions=True, force=True)', src)
        self.assertLess(src.index("je.cancel()"), src.index("resultat = creer("))
        self.assertLess(src.index("resultat = creer("), src.index('frappe.delete_doc("Journal Entry"'))
        self.assertIn("frappe.delete_doc(E.DOCTYPE, je.name", src)
        self.assertIn("UPDATE `tabBRS Bank Movement` SET document_name", src)
        self.assertIn('add_comment("Info", trace)', src)
        self.assertNotIn('("Journal Entry", je.name)', src[src.index("for dt, nom in"):])

    def test_le_dialogue_sait_corriger(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("function rcj_depense(rapport, initial)", js)
        self.assertIn('method: API + (initial ? ".corriger" : ".creer")', js)
        self.assertIn("function pre_remplir(i)", js)
        self.assertIn('class="btn btn-xs btn-default rcj-corriger"', js)
        self.assertIn('method: "customization_app.caisse_depenses.details_depense"', js)
        self.assertIn("rcj_depense(this, i);", js)


class TestLiensEtFormules(unittest.TestCase):
    """Bandeau de liens en correction et formules dans les champs de montant (30/10/2026)."""

    def setUp(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            self.js = f.read()

    def test_le_pre_remplissage_attend_les_set_value(self):
        self.assertIn("async function pre_remplir(i)", self.js)
        self.assertIn('await d.set_value("type_depense", i.type_depense);', self.js)
        self.assertIn('await d.set_value("mode", i.mode || "Espèces");', self.js)

    def test_le_bandeau_lie_ecriture_fiche_pieces_et_banque(self):
        self.assertIn('fieldname: "zone_correction"', self.js)
        self.assertIn('lien("Journal Entry", i.name)', self.js)
        self.assertIn("lien(i.fiche_doctype, i.fiche || i.depense)", self.js)
        self.assertIn('href="/app/identification-bancaire"', self.js)
        src = inspect.getsource(CD.details_depense)
        self.assertIn('"mouvements":', src)
        self.assertIn('"fiche_doctype":', src)

    def test_les_formules_sont_evaluees_sans_eval_libre(self):
        self.assertIn("function rcj_formule_montant(texte, base)", self.js)
        self.assertIn('appliquer_formule("retenue")', self.js)
        self.assertIn('appliquer_formule("montant_paye")', self.js)
        fn = self.js[self.js.index("function rcj_formule_montant"):][:1200]
        self.assertIn("if (!/^[0-9+\\-*/().]+$/.test(t)) return null;", fn)
        self.assertNotIn("eval(", fn)
