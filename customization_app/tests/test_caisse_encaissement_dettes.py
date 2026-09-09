"""Tests de l'encaissement des dettes depuis la caisse journalière (ticket #8).

Convention : `unittest.TestCase` pur, aucune base — comme `test_annulation_facture`.
Deux choses se testent ici, et ce sont les deux endroits où le bug vivait :

  - la RÈGLE qui écarte une dette dont la commande (ou la facture) est annulée ;
  - le Server Script « Traitement des encaissement » lu dans la fixture : il doit
    compiler, et ne plus recopier un lien BL/commande sans regarder le docstatus.

Le reste (échéanciers, paiements, recréation de facture) touche la comptabilité et
se vérifie en recette sur des cas réels.
"""
from __future__ import annotations

import ast
import json
import os
import unittest

from customization_app import caisse_encaissement_dettes as CED

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "server_script.json")
NOM_SCRIPT = "Traitement des encaissement"


def _script():
    """Le code du Server Script tel qu'il partira en production (fixture)."""
    with open(FIXTURE, encoding="utf-8") as f:
        for s in json.load(f):
            if s.get("name") == NOM_SCRIPT:
                return s["script"]
    raise AssertionError("Server Script « %s » absent de la fixture." % NOM_SCRIPT)


def _lignes_de_facture(arbre):
    """Le dict qui recopie une ligne dans la facture recréée, repéré par `dn_detail`."""
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Dict) and any(
                isinstance(c, ast.Constant) and c.value == "dn_detail" for c in noeud.keys):
            return {c.value: v for c, v in zip(noeud.keys, noeud.values)}
    raise AssertionError("Le bloc de recopie des lignes de facture est introuvable.")


class TestMotifNonEncaissable(unittest.TestCase):
    """La règle : une dette dont le document est ANNULÉ ne s'encaisse pas — son
    paiement porterait un lien vers lui et Frappe le refuse."""

    def test_une_commande_soumise_est_encaissable(self):
        self.assertEqual(CED._motif_non_encaissable("SAL-ORD-2026-03325", "Sales Order", 1), "")

    def test_une_commande_annulee_donne_un_motif(self):
        self.assertEqual(CED._motif_non_encaissable("SAL-ORD-2026-03325", "Sales Order", 2),
                         CED.MOTIF_COMMANDE_ANNULEE)

    def test_une_facture_annulee_donne_son_propre_motif(self):
        """Une dette d'ouverture pointe une facture, pas une commande : le motif
        affiché doit parler de la facture."""
        self.assertEqual(CED._motif_non_encaissable("ACC-SINV-2026-01068", "Sales Invoice", 2),
                         CED.MOTIF_FACTURE_ANNULEE)

    def test_le_docstatus_en_chaine_est_compris(self):
        """`frappe.db.get_value` peut rendre le docstatus en chaîne."""
        self.assertEqual(CED._motif_non_encaissable("WEB1-007819", "Sales Order", "2"),
                         CED.MOTIF_COMMANDE_ANNULEE)

    def test_une_commande_brouillon_n_est_pas_bloquee(self):
        """Un brouillon n'est pas annulé : il ne sert pas de lien `bl` (c'est
        `encaisser` qui s'en charge), mais il n'interdit pas l'encaissement."""
        self.assertEqual(CED._motif_non_encaissable("WEB1-007819", "Sales Order", 0), "")

    def test_une_dette_sans_commande_reste_encaissable(self):
        """Le script sait la traiter par la référence de son paiement."""
        self.assertEqual(CED._motif_non_encaissable("", "", None), "")
        self.assertEqual(CED._motif_non_encaissable(None, None, None), "")

    def test_une_commande_introuvable_reste_encaissable(self):
        """Aucun doctype reconnu : rien ne prouve qu'elle soit annulée, on ne
        bloque pas l'employé sur une supposition."""
        self.assertEqual(CED._motif_non_encaissable("VIEUX-REF-42", "", None), "")


class TestFixtureTraitementDesEncaissements(unittest.TestCase):
    """Le Server Script de la fixture — le code qui a levé « Impossible de lier le
    document annulé » en production."""

    def setUp(self):
        self.script = _script()
        self.arbre = ast.parse(self.script, NOM_SCRIPT)

    def test_le_script_compile(self):
        compile(self.script, NOM_SCRIPT, "exec")

    def test_les_liens_bl_et_commande_passent_par_une_variable(self):
        """Recopier `item.delivery_note` tel quel est ce qui reliait un BL annulé."""
        lignes = _lignes_de_facture(self.arbre)
        for champ in ("sales_order", "delivery_note"):
            self.assertIsInstance(
                lignes[champ], ast.Name,
                "Le lien %s doit passer par une variable filtrée sur le docstatus." % champ)

    def test_le_lien_n_est_recopie_que_si_le_document_est_soumis(self):
        affectations = {n.targets[0].id: ast.unparse(n.value)
                        for n in ast.walk(self.arbre)
                        if isinstance(n, ast.Assign) and len(n.targets) == 1
                        and isinstance(n.targets[0], ast.Name)}
        for champ, doctype in (("sales_order", "Sales Order"),
                               ("delivery_note", "Delivery Note")):
            source = affectations[_lignes_de_facture(self.arbre)[champ].id]
            self.assertIn(doctype, source)
            self.assertIn("docstatus", source)
            self.assertIn("== 1", source)

    def test_les_details_de_ligne_suivent_leur_parent(self):
        """`so_detail`/`dn_detail` sont des Data : gardés seuls, ils désigneraient
        une ligne d'un document que la facture ne référence plus."""
        lignes = _lignes_de_facture(self.arbre)
        for champ in ("so_detail", "dn_detail"):
            self.assertIsInstance(lignes[champ], ast.IfExp,
                                  "%s doit être conditionné au lien de son parent." % champ)

    def test_le_script_refuse_une_dette_dont_le_document_est_annule(self):
        """Le contrôle vient AVANT toute écriture, et nomme la dette et le document."""
        self.assertIn("est annule, il ne peut plus recevoir de paiement", self.script)
        controle = self.script.index("statut_cible")
        self.assertLess(controle, self.script.index("Payement_left={}"),
                        "Le contrôle doit précéder la réécriture des échéanciers.")


if __name__ == "__main__":
    unittest.main()
