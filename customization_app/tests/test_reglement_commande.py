"""Le règlement dit en bas de la commande et du bon de livraison (customization_app.reglement_commande) :
moyens de paiement, majoration montrée sur la carte, avoir utilisé, ristourne appliquée. Pur + branchements."""
from __future__ import annotations

import json
import os
import unittest

from customization_app import reglement_commande as R

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = os.path.dirname(os.path.dirname(RACINE))


def ech(mode, montant, piece=None, banque=None):
    return {"mode_of_payment": mode, "payment_amount": montant, R.CHAMP_PIECE: piece, R.CHAMP_BANQUE: banque}


class TestLignesDeReglement(unittest.TestCase):
    def test_la_majoration_est_montree_sur_le_paiement_par_carte(self):
        """La commande SAL-ORD-2026-04740 : reste à payer, carte 100 + 2, avoir 78."""
        l = R.lignes_de_reglement([ech("Dette non payée", 84.083), ech("Carte de crédit", 102.0), ech("Avoir client", 78.0)], 2.0, 2.0)
        self.assertEqual([(x["libelle"], x["montant_txt"], x["detail"]) for x in l], [
            ("Reste à payer", "84.083", ""),
            ("Carte bancaire", "102.000", "100.000 + majoration de 2 % : 2.000"),
            ("Avoir client utilisé", "78.000", ""),
        ])

    def test_la_piece_et_la_banque_suivent_le_paiement(self):
        l = R.lignes_de_reglement([ech("Chèque", 40, "1234567", "BIAT"), ech("Carte de crédit", 116.133, "112233")], 2.3, 2)
        self.assertEqual(l[0]["detail"], "n° 1234567, BIAT")
        self.assertEqual(l[1]["detail"], "113.833 + majoration de 2 % : 2.300 ; n° 112233")

    def test_sans_carte_aucune_majoration_n_est_dite(self):
        l = R.lignes_de_reglement([ech("Espèces", 285.5)], 2.0, 2.0)
        self.assertEqual([(x["libelle"], x["montant_txt"], x["detail"]) for x in l], [("Espèces", "285.500", "")])

    def test_une_carte_sans_majoration_reste_une_ligne_simple(self):
        """Commandes d'avant la majoration, ou taux réglé à 0."""
        l = R.lignes_de_reglement([ech("Carte de crédit", 285.0, "797632")], 0, 0)
        self.assertEqual(l[0]["detail"], "n° 797632")

    def test_deux_cartes_se_partagent_la_majoration_sans_perdre_un_millime(self):
        l = R.lignes_de_reglement([ech("Carte de crédit", 100.0), ech("Carte de crédit", 53.0)], 3.0, 2.0)
        self.assertEqual(l[0]["detail"], "98.039 + majoration de 2 % : 1.961")
        self.assertEqual(l[1]["detail"], "51.961 + majoration de 2 % : 1.039")
        self.assertAlmostEqual(1.961 + 1.039, 3.0, places=3)

    def test_les_echeances_a_zero_sont_passees(self):
        self.assertEqual(R.lignes_de_reglement([ech("Espèces", 0), ech("Chèque", None)], 0, 0), [])
        self.assertEqual(R.lignes_de_reglement(None), [])

    def test_un_moyen_inconnu_garde_son_nom(self):
        self.assertEqual(R.lignes_de_reglement([ech("Virement", 10)])[0]["libelle"], "Virement")

    def test_le_reste_a_payer_ne_montre_pas_de_numero(self):
        self.assertEqual(R.lignes_de_reglement([ech("Dette non payée", 10, "X1")])[0]["detail"], "")

    def test_un_taux_a_virgule(self):
        l = R.lignes_de_reglement([ech("Carte de crédit", 101.5)], 1.5, 1.5)
        self.assertEqual(l[0]["detail"], "100.000 + majoration de 1,5 % : 1.500")


class TestRistourne(unittest.TestCase):
    def test_la_ristourne_est_la_remise_quand_le_choix_est_oui(self):
        self.assertEqual(R.ristourne_appliquee("Oui", 37.916555), 37.917)

    def test_pas_de_ristourne_si_non_ou_sans_remise(self):
        self.assertEqual(R.ristourne_appliquee("Non", 37.9), 0.0)
        self.assertEqual(R.ristourne_appliquee(None, 37.9), 0.0)
        self.assertEqual(R.ristourne_appliquee("Oui", 0), 0.0)


class TestBonDeLivraison(unittest.TestCase):
    """Demande du 18/11/2026 : un BL de même valeur que sa commande ne la cite pas ; un BL partiel la cite, avec son total."""

    def test_un_bl_de_meme_valeur_dit_juste_reglement(self):
        self.assertTrue(R.meme_valeur(264.083, 264.083))
        self.assertTrue(R.meme_valeur(264.08, 264.083))          # arrondi de taxes
        t = R.titres("SAL-ORD-2026-04740", 264.083, True)
        self.assertEqual(t["titre"], "Règlement")
        self.assertEqual(t["titre_ristourne"], "Ristourne appliquée")
        self.assertEqual(t["note_ristourne"], "déjà déduite du total")

    def test_un_bl_partiel_cite_la_commande_et_son_total(self):
        self.assertFalse(R.meme_valeur(120.0, 264.083))
        t = R.titres("SAL-ORD-2026-04740", 264.083, False)
        self.assertEqual(t["titre"], "Ce BL fait partie de la commande SAL-ORD-2026-04740 (total 264.083), réglée ainsi")
        self.assertEqual(t["titre_ristourne"], "Ristourne appliquée sur la commande SAL-ORD-2026-04740")
        self.assertEqual(t["note_ristourne"], "déjà déduite du total de la commande")

    def test_la_commande_elle_meme_dit_reglement(self):
        doc = _Doc(doctype="Sales Order", name="C1", docstatus=1, grand_total=50.0, custom_appliquer_ristourne="Oui",
                   discount_amount=5.0, payment_schedule=[_Doc(mode_of_payment="Espèces", payment_amount=50.0)])
        r = R.resume(doc)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["titre"], r[0]["titre_ristourne"], r[0]["ristourne_txt"], r[0]["entier"]),
                         ("Règlement", "Ristourne appliquée", "5.000", True))
        self.assertEqual([(l["libelle"], l["montant_txt"]) for l in r[0]["lignes"]], [("Espèces", "50.000")])


class _Doc(dict):
    """Un document minimal : attributs et .get() comme un document Frappe."""
    __getattr__ = dict.get


class TestResume(unittest.TestCase):
    def test_une_facture_n_a_pas_de_bloc(self):
        self.assertEqual(R.resume(_Doc(doctype="Sales Invoice", items=[])), [])

    def test_un_retour_de_livraison_n_a_pas_de_bloc(self):
        self.assertEqual(R.resume(_Doc(doctype="Delivery Note", is_return=1, items=[])), [])

    def test_une_commande_annulee_ou_sans_rien_a_dire_ne_rend_rien(self):
        self.assertEqual(R.resume(_Doc(doctype="Sales Order", name="C1", docstatus=2, payment_schedule=[])), [])
        self.assertEqual(R.resume(_Doc(doctype="Sales Order", name="C1", docstatus=0, payment_schedule=[])), [])

    def test_une_impression_ne_tombe_jamais_pour_ce_bloc(self):
        self.assertEqual(R.resume(object()), [])


class TestBranchements(unittest.TestCase):
    def test_la_fonction_est_offerte_aux_modeles_d_impression(self):
        with open(os.path.join(RACINE, "hooks.py"), encoding="utf-8") as f:
            self.assertIn('"customization_app.jinja_methods.reglement_commande"', f.read())
        from customization_app import jinja_methods
        self.assertTrue(callable(jinja_methods.reglement_commande))

    def test_la_commande_et_le_bl_l_impriment_la_facture_non(self):
        chemin = os.path.join(APPS, "booking_ristourne", "booking_ristourne", "fixtures", "print_format.json")
        if not os.path.exists(chemin):
            self.skipTest("booking_ristourne absent")
        with open(chemin, encoding="utf-8") as f:
            formats = {d["name"]: (d.get("html") or "") for d in json.load(f)}
        for nom in ("Aqua World BC", "Aqua World BL"):
            self.assertIn("reglement_commande(doc)", formats[nom], nom)
            self.assertIn("if reglement_commande is defined", formats[nom], nom)   # sans customization_app : rien
            self.assertIn("{{ rg.titre }}", formats[nom], nom)
            self.assertIn("{{ rg.titre_ristourne }} : {{ rg.ristourne_txt }} ({{ rg.note_ristourne }})", formats[nom], nom)
        self.assertNotIn("reglement_commande", formats["Aqua World Facture"])


if __name__ == "__main__":
    unittest.main()
