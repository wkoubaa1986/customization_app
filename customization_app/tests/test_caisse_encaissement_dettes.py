"""Tests de l'encaissement des dettes depuis la caisse journalière (ticket #8).

Convention : `unittest.TestCase` pur, aucune base — comme `test_annulation_facture`.
Trois choses se testent ici, et ce sont les endroits où le bug vivait :

  - la RÈGLE qui écarte une dette dont la commande (ou la facture) est annulée,
    et l'ORDRE des refus opposés à la sélection ;
  - le Server Script « Traitement des encaissement » lu dans la fixture : il doit
    compiler, et ne plus recopier un lien BL/commande sans regarder le docstatus ;
  - le CONTRÔLE PRÉALABLE de ce même script, extrait de la fixture et EXÉCUTÉ sur
    un `frappe` factice — c'est le seul moyen de vérifier son comportement (une
    facture soumise que le traitement annulerait doit être refusée) sans site.

Le reste (échéanciers, paiements, recréation de facture) touche la comptabilité et
se vérifie en recette sur des cas réels.
"""
from __future__ import annotations

import ast
import json
import os
import types
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


def _bloc_controle(arbre):
    """Le contrôle préalable du script : le `if name:` qui refuse la dette dont le
    document est annulé — ou le sera par le traitement lui-même."""
    for noeud in ast.walk(arbre):
        if (isinstance(noeud, ast.If) and isinstance(noeud.test, ast.Name)
                and noeud.test.id == "name" and "statut_cible" in ast.unparse(noeud)):
            return ast.unparse(noeud)
    raise AssertionError("Le contrôle préalable est introuvable dans le script.")


class Refus(Exception):
    """Ce que `frappe.throw` lève dans le script."""


class _FrappeFactice:
    """Le strict nécessaire au contrôle préalable : `frappe.db.get_value` et
    `frappe.throw`. Les documents sont donnés en dur — aucune base n'est ouverte."""

    def __init__(self, documents):
        self.documents = documents          # {(doctype, nom): {champ: valeur}}
        self.db = self                      # `frappe.db.get_value` retombe ici

    def get_value(self, doctype, nom, champ):
        return (self.documents.get((doctype, nom)) or {}).get(champ)

    def throw(self, message):
        raise Refus(message)


def _executer_controle(documents, cible, dette="ACC-PAY-2026-00123",
                       enc="ENC-2026-00042"):
    """Joue le contrôle préalable du script de la fixture sur une dette donnée."""
    bloc = _bloc_controle(ast.parse(_script(), NOM_SCRIPT))
    espace = {"frappe": _FrappeFactice(documents), "name": cible,
              "ipay": types.SimpleNamespace(ref_paiement=dette),
              "doc": types.SimpleNamespace(name=enc)}
    exec(compile(bloc, "controle prealable", "exec"), espace)  # noqa: S102


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


def _dette(nom, montant=100.0, motif="", commande="SAL-ORD-2026-03325"):
    return {"name": nom, "montant": montant, "motif": motif, "commande": commande}


class TestTriDeLaSelection(unittest.TestCase):
    """L'ORDRE des refus : le motif détaillé passe avant « aucune dette encaissable »."""

    def test_la_selection_de_l_employe_est_respectee(self):
        a, b = _dette("PE-1"), _dette("PE-2")
        choisies, refus = CED._trier_selection([a, b], ["PE-2"])
        self.assertEqual(choisies, [b])
        self.assertIsNone(refus)

    def test_sans_selection_tout_l_encaissable_est_pris(self):
        a, b = _dette("PE-1"), _dette("PE-2", motif=CED.MOTIF_COMMANDE_ANNULEE)
        choisies, refus = CED._trier_selection([a, b], [])
        self.assertEqual(choisies, [a])
        self.assertIsNone(refus)

    def test_une_dette_annulee_cochee_est_refusee_avec_son_motif(self):
        bloquee = _dette("PE-2", motif=CED.MOTIF_COMMANDE_ANNULEE)
        choisies, refus = CED._trier_selection([_dette("PE-1"), bloquee], ["PE-1", "PE-2"])
        self.assertEqual(choisies, [])
        self.assertEqual(refus, (CED.REFUS_BLOQUEES, [bloquee]))

    def test_l_unique_dette_annulee_d_un_client_est_nommee(self):
        """Le défaut d'origine : le refus générique « aucune dette encaissable »
        partait le premier et l'employé n'apprenait ni laquelle ni pourquoi."""
        bloquee = _dette("PE-1", motif=CED.MOTIF_FACTURE_ANNULEE,
                         commande="ACC-SINV-2026-01068")
        choisies, refus = CED._trier_selection([bloquee], ["PE-1"])
        self.assertEqual(choisies, [])
        self.assertEqual(refus[0], CED.REFUS_BLOQUEES)
        self.assertEqual(refus[1], [bloquee])

    def test_un_client_sans_dette_recoit_le_refus_generique(self):
        self.assertEqual(CED._trier_selection([], []), ([], (CED.REFUS_AUCUNE, [])))

    def test_toutes_bloquees_sans_selection_donne_le_refus_generique(self):
        """Rien n'a été coché : aucune dette à nommer, le message générique suffit."""
        _, refus = CED._trier_selection(
            [_dette("PE-1", motif=CED.MOTIF_COMMANDE_ANNULEE)], [])
        self.assertEqual(refus, (CED.REFUS_AUCUNE, []))


class TestControlePrealableDuScript(unittest.TestCase):
    """Le contrôle préalable de la fixture, EXÉCUTÉ : ce qu'il laisse passer et ce
    qu'il refuse, avec le message rendu à l'employé."""

    def test_une_commande_soumise_passe(self):
        _executer_controle({("Sales Order", "SAL-ORD-2026-03325"): {"docstatus": 1}},
                           "SAL-ORD-2026-03325")

    def test_une_commande_annulee_est_refusee(self):
        with self.assertRaises(Refus) as levee:
            _executer_controle({("Sales Order", "SAL-ORD-2026-03325"): {"docstatus": 2}},
                               "SAL-ORD-2026-03325")
        self.assertIn("SAL-ORD-2026-03325", str(levee.exception))
        self.assertIn("ACC-PAY-2026-00123", str(levee.exception))
        self.assertIn("annule", str(levee.exception))

    def test_une_facture_d_ouverture_soumise_passe(self):
        """Le cas nominal d'une dette sans commande : le traitement n'y touche pas."""
        _executer_controle({("Sales Invoice", "ACC-SINV-2026-00007"):
                            {"docstatus": 1, "is_opening": "Yes"}},
                           "ACC-SINV-2026-00007")

    def test_une_facture_soumise_que_le_traitement_annulerait_est_refusee(self):
        """LE cas resté ouvert : la facture est encore soumise à l'entrée, mais le
        bloc « Cancel invoices » l'annule et la recrée avant la création des
        paiements — qui la référenceraient annulée. On refuse avant toute écriture,
        en nommant la dette, la facture et l'encaissement."""
        with self.assertRaises(Refus) as levee:
            _executer_controle({("Sales Invoice", "ACC-SINV-2026-01068"):
                                {"docstatus": 1, "is_opening": "No"}},
                               "ACC-SINV-2026-01068")
        message = str(levee.exception)
        self.assertIn("ACC-PAY-2026-00123", message)      # la dette
        self.assertIn("ACC-SINV-2026-01068", message)     # la facture
        self.assertIn("ENC-2026-00042", message)          # l'encaissement

    def test_une_facture_deja_annulee_est_refusee(self):
        with self.assertRaises(Refus) as levee:
            _executer_controle({("Sales Invoice", "ACC-SINV-2026-01068"):
                                {"docstatus": 2, "is_opening": "No"}},
                               "ACC-SINV-2026-01068")
        self.assertIn("ACC-SINV-2026-01068", str(levee.exception))

    def test_une_cible_inconnue_ne_bloque_pas(self):
        """Ni commande ni facture retrouvée : rien ne prouve qu'il y ait un problème."""
        _executer_controle({}, "VIEUX-REF-42")


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
