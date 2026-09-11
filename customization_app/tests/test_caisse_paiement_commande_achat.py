"""Tests des paiements faits sur une COMMANDE d'achat dans la caisse (ticket #16).

Le bug : un paiement fournisseur créé depuis le formulaire d'une commande
d'achat (« Créer > Paiement ») n'a ni fiche « Facture Achat a Saisir » ni
drapeau `custom_reglement_caisse` — `_depenses_caisse` l'ignorait, l'argent
sortait du tiroir sans que le rapport ni le solde théorique de la clôture ne le
voient.

Convention : `unittest.TestCase` pur, aucune base — comme
`test_caisse_paiement_fournisseurs`. `frappe.db.sql` est remplacé par un aiguilleur
qui rend, pour chacune des quatre sources de dépenses, des lignes données en dur :
ce qui se teste est la LECTURE (quel paiement entre, avec quel type, quel mode,
quel auteur) et la DÉDUPLICATION, pas le SQL lui-même — celui-ci se vérifie en
recette.
"""
from __future__ import annotations

import types
import unittest

import frappe

from customization_app import rapport_caisse_journaliere as RCJ
from customization_app.caisse_depenses import TYPE_AVANCE_COMMANDE, TYPE_REGLEMENT

CAISSE = "Espèces - A&S"
BANQUE = "STE430127B - Zitouna - A&S"
NOMS = {"akram@aquaworld.com": "Akram", "jimmyks007@gmail.com": "Jamel Aloui"}


def _avance(name="ACC-PAY-2026-09001", owner="akram@aquaworld.com",
            paid_from=CAISSE, mode_of_payment="Espèces", paid_amount=250.0,
            party="AQUA FROID", party_name=None, commandes="PUR-ORD-2026-00042",
            reference_no=None, posting_date="2026-09-11"):
    """Une ligne telle que la requête « paiements sur commande d'achat » la rend."""
    return frappe._dict({
        "name": name, "posting_date": posting_date, "owner": owner,
        "paid_amount": paid_amount, "paid_from": paid_from,
        "mode_of_payment": mode_of_payment, "reference_no": reference_no,
        "party": party, "party_name": party_name, "commandes": commandes,
    })


def _paiement_de_fiche(name, **extra):
    """Une ligne de la source « fiche Facture Achat a Saisir »."""
    ligne = frappe._dict({
        "name": name, "posting_date": "2026-09-11", "owner": "akram@aquaworld.com",
        "paid_amount": 250.0, "paid_from": CAISSE, "mode_of_payment": "Espèces",
        "remarks": None, "reference_no": None, "party": "AQUA FROID",
        "fiche_description": "Achat plomberie",
    })
    ligne.update(extra)
    return ligne


def _reglement_marque(name, **extra):
    """Une ligne de la source « drapeau custom_reglement_caisse »."""
    ligne = frappe._dict({
        "name": name, "posting_date": "2026-09-11", "owner": "akram@aquaworld.com",
        "paid_amount": 250.0, "paid_from": CAISSE, "mode_of_payment": "Espèces",
        "remarks": None, "reference_no": None, "party": "AQUA FROID",
    })
    ligne.update(extra)
    return ligne


class _SansSite:
    """`flt` et les accès base ont besoin d'un site : on les neutralise, seule la
    LOGIQUE de `_depenses_caisse` se teste (même parti pris que le reste du dépôt)."""

    def setUp(self):
        self.ecritures = []          # « Dépense caisse — … » (Journal Entry)
        self.fiches = []             # paiements désignés par une fiche
        self.reglements = []         # paiements portant le drapeau de caisse
        self.avances = []            # paiements référençant une commande d'achat
        self.colonne_drapeau = True
        self.requetes = []           # (sql, params) dans l'ordre d'exécution

        self._vrais = (RCJ.flt, frappe.db, frappe.get_all)
        RCJ.flt = lambda valeur, precision=None: round(float(valeur or 0), 3)
        frappe.db = types.SimpleNamespace(
            sql=self._sql,
            has_column=lambda doctype, colonne: self.colonne_drapeau)
        frappe.get_all = lambda *a, **k: []      # aucune pièce jointe

    def tearDown(self):
        RCJ.flt, frappe.db, frappe.get_all = self._vrais

    def _sql(self, requete, params=None, as_dict=False):
        self.requetes.append((requete, params))
        if "tabJournal Entry" in requete:
            return list(self.ecritures)
        if "tabFacture Achat a Saisir" in requete:
            return list(self.fiches)
        if "custom_reglement_caisse" in requete:
            return list(self.reglements)
        if "'Purchase Order'" in requete:
            return list(self.avances)
        raise AssertionError("Requête inattendue : %s" % requete)

    def _depenses(self, d1="2026-09-11", d2="2026-09-11"):
        return RCJ._depenses_caisse(d1, d2, dict(NOMS))


class TestPaiementSurCommandeDetecte(_SansSite, unittest.TestCase):
    """LE CŒUR DU TICKET : le paiement d'une commande d'achat devient une dépense."""

    def test_une_ligne_par_paiement(self):
        self.avances = [_avance()]
        lignes = self._depenses()
        self.assertEqual(len(lignes), 1)
        ligne = lignes[0]
        self.assertEqual(ligne["name"], "ACC-PAY-2026-09001")
        self.assertEqual(ligne["doctype"], "Payment Entry")
        self.assertEqual(ligne["date"], "2026-09-11")
        self.assertEqual(ligne["montant"], 250.0)

    def test_le_type_affiche_est_avance_commande_d_achat(self):
        self.avances = [_avance()]
        self.assertEqual(self._depenses()[0]["type"], TYPE_AVANCE_COMMANDE)
        self.assertEqual(TYPE_AVANCE_COMMANDE, "Avance commande d'achat")

    def test_le_paiement_est_attribue_a_son_auteur(self):
        self.avances = [_avance(owner="jimmyks007@gmail.com")]
        self.assertEqual(self._depenses()[0]["saisi_par"], "Jamel Aloui")

    def test_un_auteur_inconnu_reste_identifie_par_son_compte(self):
        """Le filtre par caisse compare des NOMS : un compte sans fiche ni User
        connu ne doit pas se retrouver attribué à quelqu'un d'autre."""
        self.avances = [_avance(owner="inconnu@aquaworld.com")]
        self.assertEqual(self._depenses()[0]["saisi_par"], "inconnu@aquaworld.com")

    def test_sans_paiement_sur_commande_rien_ne_change(self):
        """Un paiement de facture d'achat saisi ailleurs reste invisible."""
        self.assertEqual(self._depenses(), [])

    def test_la_periode_borne_la_requete(self):
        self.avances = [_avance()]
        self._depenses("2026-09-01", "2026-09-11")
        params = [p for sql, p in self.requetes if "'Purchase Order'" in sql]
        self.assertEqual(params, [("2026-09-01", "2026-09-11")])

    def test_plusieurs_paiements_donnent_plusieurs_lignes(self):
        self.avances = [_avance(name="ACC-PAY-2026-09001"),
                        _avance(name="ACC-PAY-2026-09002", paid_amount=80.0)]
        lignes = self._depenses()
        self.assertEqual(sorted(l["name"] for l in lignes),
                         ["ACC-PAY-2026-09001", "ACC-PAY-2026-09002"])
        self.assertEqual(sum(l["montant"] for l in lignes), 330.0)


class TestModeDuPaiementSurCommande(_SansSite, unittest.TestCase):
    """Seule la part ESPÈCES pèse sur le solde théorique de la clôture : le mode
    se lit d'abord sur le COMPTE débité, comme pour les règlements de caisse."""

    def test_paye_depuis_la_caisse_le_mode_est_especes(self):
        self.avances = [_avance(paid_from=CAISSE, mode_of_payment="Espèces")]
        self.assertEqual(self._depenses()[0]["mode"], "Espèces")

    def test_un_compte_bancaire_garde_le_mode_du_paiement(self):
        self.avances = [_avance(paid_from=BANQUE, mode_of_payment="Chèque")]
        self.assertEqual(self._depenses()[0]["mode"], "Chèque")

    def test_sans_mode_de_paiement_c_est_un_virement(self):
        self.avances = [_avance(paid_from=BANQUE, mode_of_payment=None)]
        self.assertEqual(self._depenses()[0]["mode"], "Virement")

    def test_le_compte_de_caisse_l_emporte_sur_le_mode_saisi(self):
        """L'argent sort bien du tiroir, quel que soit le libellé choisi."""
        self.avances = [_avance(paid_from=CAISSE, mode_of_payment="Virement")]
        self.assertEqual(self._depenses()[0]["mode"], "Espèces")


class TestDescriptionDuPaiementSurCommande(_SansSite, unittest.TestCase):
    """Fournisseur + commande(s) référencée(s) + n° de pièce."""

    def test_fournisseur_commande_et_numero_de_piece(self):
        self.avances = [_avance(party="FOUR-0001", party_name="AQUA FROID",
                                commandes="PUR-ORD-2026-00042",
                                reference_no="1234567")]
        self.assertEqual(self._depenses()[0]["description"],
                         "AQUA FROID — PUR-ORD-2026-00042 — 1234567")

    def test_sans_numero_de_piece_la_description_ne_traine_pas_de_tiret(self):
        self.avances = [_avance(party_name="AQUA FROID", reference_no=None)]
        self.assertEqual(self._depenses()[0]["description"],
                         "AQUA FROID — PUR-ORD-2026-00042")

    def test_sans_nom_de_fournisseur_l_identifiant_suffit(self):
        self.avances = [_avance(party="FOUR-0001", party_name=None)]
        self.assertEqual(self._depenses()[0]["description"],
                         "FOUR-0001 — PUR-ORD-2026-00042")

    def test_plusieurs_commandes_sont_toutes_citees(self):
        self.avances = [_avance(party_name="AQUA FROID",
                                commandes="PUR-ORD-2026-00042, PUR-ORD-2026-00043")]
        self.assertIn("PUR-ORD-2026-00043", self._depenses()[0]["description"])


class TestDeduplication(_SansSite, unittest.TestCase):
    """Un même paiement vu par deux sources ne doit compter QU'UNE fois — sinon
    la dépense serait doublée et le solde théorique faux d'autant."""

    def test_un_paiement_ne_depasse_jamais_une_ligne_par_fiche(self):
        """L'avance née d'un BL de caisse (`po_convertir_avances`) est déjà portée
        par sa fiche : elle référence pourtant bien une commande d'achat."""
        self.fiches = [_paiement_de_fiche("ACC-PAY-2026-09001")]
        self.avances = [_avance(name="ACC-PAY-2026-09001")]
        lignes = self._depenses()
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["type"], "Facture d'achat")
        self.assertEqual(lignes[0]["description"], "Achat plomberie")

    def test_un_paiement_marque_caisse_garde_son_type_de_reglement(self):
        self.reglements = [_reglement_marque("ACC-PAY-2026-09001")]
        self.avances = [_avance(name="ACC-PAY-2026-09001")]
        lignes = self._depenses()
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["type"], TYPE_REGLEMENT)

    def test_le_meme_paiement_rendu_deux_fois_ne_compte_qu_une_fois(self):
        self.avances = [_avance(), _avance()]
        self.assertEqual(len(self._depenses()), 1)

    def test_les_autres_paiements_ne_sont_pas_perdus_par_la_dedup(self):
        self.fiches = [_paiement_de_fiche("ACC-PAY-2026-09001")]
        self.avances = [_avance(name="ACC-PAY-2026-09001"),
                        _avance(name="ACC-PAY-2026-09002", paid_amount=80.0)]
        lignes = self._depenses()
        self.assertEqual(sorted(l["name"] for l in lignes),
                         ["ACC-PAY-2026-09001", "ACC-PAY-2026-09002"])
        self.assertEqual(sum(l["montant"] for l in lignes), 330.0)


class TestSourcesPreexistantes(_SansSite, unittest.TestCase):
    """La quatrième source ne doit rien casser des trois autres (ticket #10)."""

    def test_le_drapeau_de_caisse_reste_lu_seulement_si_la_colonne_existe(self):
        self.colonne_drapeau = False
        self.reglements = [_reglement_marque("ACC-PAY-2026-09001")]
        self.assertEqual(self._depenses(), [])
        self.assertFalse([sql for sql, _p in self.requetes
                          if "custom_reglement_caisse" in sql])

    def test_les_paiements_sur_commande_sont_lus_meme_sans_la_colonne(self):
        """La colonne naît d'un patch : entre le déploiement et la migration, la
        détection des commandes d'achat doit fonctionner quand même."""
        self.colonne_drapeau = False
        self.avances = [_avance()]
        self.assertEqual(len(self._depenses()), 1)

    def test_les_quatre_sources_cohabitent(self):
        self.ecritures = [frappe._dict({
            "name": "ACC-JV-2026-00010", "posting_date": "2026-09-11",
            "owner": "akram@aquaworld.com",
            "cheque_no": "Dépense caisse — Gasoil", "user_remark": "Type : Carburant",
            "compte_credit": CAISSE, "credit": 40.0})]
        self.fiches = [_paiement_de_fiche("ACC-PAY-2026-09001")]
        self.reglements = [_reglement_marque("ACC-PAY-2026-09002", paid_amount=100.0)]
        self.avances = [_avance(name="ACC-PAY-2026-09003", paid_amount=80.0)]
        lignes = self._depenses()
        self.assertEqual(sorted(l["type"] for l in lignes),
                         sorted(["Carburant", "Facture d'achat", TYPE_REGLEMENT,
                                 TYPE_AVANCE_COMMANDE]))
        self.assertEqual(sum(l["montant"] for l in lignes), 470.0)


if __name__ == "__main__":
    unittest.main()
