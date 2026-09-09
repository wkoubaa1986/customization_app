"""Tests du règlement des factures fournisseurs depuis la caisse (ticket #10).

Convention : `unittest.TestCase` pur, aucune base — comme
`test_caisse_encaissement_dettes`. Ce qui se teste ici est exactement ce qui
décide de l'écriture comptable :

  - la RÉPARTITION FIFO, qui dit quel Payment Entry référence quelle facture et
    pour combien (« un paiement -> plusieurs (facture, montant) ») ;
  - le CONTRÔLE DE LA SÉLECTION : liste périmée, mélange de fournisseurs ;
  - la VALIDATION des lignes de règlement (chèque, virement, mode inconnu) et le
    refus du dépassement.

La création des Payment Entry eux-mêmes touche la comptabilité et se vérifie en
recette (chèque couvrant deux factures + espèces, cf. les risques du ticket).
"""
from __future__ import annotations

import os
import types
import unittest

import frappe

from customization_app import caisse_depenses as CD
from customization_app.patches import ensure_reglement_caisse_field as PATCH

DIALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "customize_erpnext", "page", "caisse_journaliere",
                        "caisse_journaliere.js")


class Refus(Exception):
    """Ce que `frappe.throw` lève ici — hors site, il ne lève rien d'exploitable."""


class _SansSite:
    """`_()`, `flt()` et `frappe.throw` ont besoin d'un site : on les neutralise,
    seule la LOGIQUE se teste (même parti pris que le reste du dépôt)."""

    def setUp(self):
        self._vrais = (CD._, CD.flt, frappe.throw)
        CD._ = lambda message: message
        CD.flt = lambda valeur, precision=None: round(float(valeur or 0), 3)
        frappe.throw = lambda message, *a, **k: (_ for _ in ()).throw(Refus(message))

    def tearDown(self):
        CD._, CD.flt, frappe.throw = self._vrais


def _facture(nom, date, reste, supplier="AQUA FROID"):
    return {"name": nom, "posting_date": date, "reste": reste, "supplier": supplier}


def _reglement(mode, montant, numero="", banque=""):
    return {"mode": mode, "montant": montant, "numero": numero, "banque": banque}


def _refs(repartition):
    """La répartition sous une forme lisible : [(facture, montant), …] par pièce."""
    return [[(x["facture"], x["montant"]) for x in r["references"]]
            for r in repartition]


class TestRepartitionFifo(unittest.TestCase):
    """LE CŒUR DU TICKET : les règlements s'affectent de la facture la plus
    ANCIENNE à la plus récente, et une pièce peut en couvrir plusieurs."""

    def setUp(self):
        # Les deux factures du critère d'acceptation.
        self.vieille = _facture("PINV-01", "2026-06-01", 300.0)
        self.recente = _facture("PINV-02", "2026-06-15", 500.0)

    def test_une_piece_sur_deux_factures_donne_deux_references(self):
        """Un chèque de 500 : 300 sur la facture du 01/06, 200 sur celle du 15/06
        — UN SEUL paiement, deux lignes de référence."""
        rep = CD._repartir_fifo([self.vieille, self.recente],
                                [_reglement("Chèque", 500.0, "1234567", "Zitouna")])
        self.assertEqual(len(rep), 1)
        self.assertEqual(_refs(rep), [[("PINV-01", 300.0), ("PINV-02", 200.0)]])
        self.assertEqual(rep[0]["alloue"], 500.0)
        self.assertEqual(rep[0]["non_alloue"], 0)

    def test_la_facture_la_plus_recente_reste_partiellement_due(self):
        rep = CD._repartir_fifo([self.vieille, self.recente],
                                [_reglement("Chèque", 500.0, "1234567", "Zitouna")])
        alloue = sum(x["montant"] for r in rep for x in r["references"]
                     if x["facture"] == "PINV-02")
        self.assertEqual(round(self.recente["reste"] - alloue, 3), 300.0)

    def test_deux_pieces_sur_la_meme_selection(self):
        """Chèque 300 + espèces 300 : le chèque solde la facture du 01/06, les
        espèces attaquent celle du 15/06 — deux paiements, une référence chacun."""
        rep = CD._repartir_fifo(
            [self.vieille, self.recente],
            [_reglement("Chèque", 300.0, "1234567", "Zitouna"),
             _reglement("Espèces", 300.0)])
        self.assertEqual(_refs(rep), [[("PINV-01", 300.0)], [("PINV-02", 300.0)]])

    def test_deux_pieces_sur_une_seule_facture(self):
        """Une facture couverte par deux pièces apparaît dans DEUX paiements."""
        rep = CD._repartir_fifo(
            [self.recente],
            [_reglement("Espèces", 200.0), _reglement("Virement", 300.0, "VIR-88")])
        self.assertEqual(_refs(rep), [[("PINV-02", 200.0)], [("PINV-02", 300.0)]])

    def test_l_ordre_de_saisie_des_factures_ne_change_rien(self):
        """La sélection arrive dans l'ordre de l'écran ; c'est la DATE qui décide."""
        rep = CD._repartir_fifo([self.recente, self.vieille],
                                [_reglement("Espèces", 400.0)])
        self.assertEqual(_refs(rep), [[("PINV-01", 300.0), ("PINV-02", 100.0)]])

    def test_a_date_egale_le_nom_departage(self):
        a = _facture("PINV-A", "2026-06-01", 100.0)
        b = _facture("PINV-B", "2026-06-01", 100.0)
        rep = CD._repartir_fifo([b, a], [_reglement("Espèces", 150.0)])
        self.assertEqual(_refs(rep), [[("PINV-A", 100.0), ("PINV-B", 50.0)]])

    def test_un_reglement_partiel_laisse_le_reste_du(self):
        rep = CD._repartir_fifo([self.vieille, self.recente],
                                [_reglement("Espèces", 120.0)])
        self.assertEqual(_refs(rep), [[("PINV-01", 120.0)]])

    def test_les_millimes_sont_arrondis_ligne_par_ligne(self):
        """ERPNext contrôle CHAQUE `allocated_amount` : un arrondi global ferait
        échouer la soumission sur des millimes."""
        rep = CD._repartir_fifo(
            [_facture("PINV-01", "2026-06-01", 33.333),
             _facture("PINV-02", "2026-06-02", 66.667)],
            [_reglement("Espèces", 100.0)])
        montants = [x["montant"] for r in rep for x in r["references"]]
        self.assertEqual(montants, [33.333, 66.667])
        self.assertEqual(round(sum(montants), 3), 100.0)

    def test_l_excedent_reste_non_alloue(self):
        """`payer_factures` refuse le dépassement en amont ; la répartition, elle,
        ne fabrique jamais d'allocation en trop."""
        rep = CD._repartir_fifo([self.vieille], [_reglement("Espèces", 400.0)])
        self.assertEqual(_refs(rep), [[("PINV-01", 300.0)]])
        self.assertEqual(rep[0]["alloue"], 300.0)
        self.assertEqual(rep[0]["non_alloue"], 100.0)


class TestSelectionDesFactures(_SansSite, unittest.TestCase):
    """La sélection est relue en base avant toute écriture : une facture soldée
    entre-temps, ou un mélange de fournisseurs, ne doit RIEN créer."""

    def setUp(self):
        super().setUp()
        self.a = _facture("PINV-01", "2026-06-01", 300.0)
        self.b = _facture("PINV-02", "2026-06-15", 500.0)

    def test_la_selection_est_retenue_telle_quelle(self):
        retenues, refus = CD._trier_factures([self.a, self.b],
                                             ["PINV-01", "PINV-02"], "AQUA FROID")
        self.assertEqual([f["name"] for f in retenues], ["PINV-01", "PINV-02"])
        self.assertIsNone(refus)

    def test_une_facture_soldee_entre_temps_rend_la_liste_perimee(self):
        """Elle n'est plus rendue par la relecture (encours nul) : on refuse TOUT."""
        retenues, refus = CD._trier_factures([self.b], ["PINV-01", "PINV-02"],
                                             "AQUA FROID")
        self.assertEqual(retenues, [])
        self.assertEqual(refus, (CD.REFUS_PERIMEE, ["PINV-01"]))

    def test_une_selection_multi_fournisseurs_est_refusee(self):
        autre = _facture("PINV-09", "2026-06-20", 100.0, supplier="SOTUVER")
        retenues, refus = CD._trier_factures([self.a, autre],
                                             ["PINV-01", "PINV-09"], "AQUA FROID")
        self.assertEqual(retenues, [])
        self.assertEqual(refus, (CD.REFUS_MULTI, ["SOTUVER"]))

    def test_sans_fournisseur_rien_n_est_retenu(self):
        _retenues, refus = CD._trier_factures([self.a], ["PINV-01"], "")
        self.assertEqual(refus, (CD.REFUS_MULTI, ["AQUA FROID"]))

    def test_sans_selection_rien_n_est_pris(self):
        """Contrairement aux dettes, il n'y a PAS de repli « tout payer » : un
        règlement se décide facture par facture."""
        self.assertEqual(CD._trier_factures([self.a], [], "AQUA FROID"),
                         ([], (CD.REFUS_AUCUNE, [])))


class TestValidationDesReglements(_SansSite, unittest.TestCase):
    """Les lignes du dialogue de règlement, avant toute écriture."""

    def _valider(self, *lignes):
        return CD._valider_reglements(list(lignes))

    def test_une_ligne_especes_suffit(self):
        lignes = self._valider({"mode": "Espèces", "montant": 300})
        self.assertEqual(lignes[0]["mode"], "Espèces")
        self.assertEqual(lignes[0]["montant"], 300.0)

    def test_un_mode_inconnu_est_refuse(self):
        for mode in ("Carte de crédit", "Traite bancaire", "Pas payé", ""):
            with self.assertRaises(Refus, msg=mode):
                self._valider({"mode": mode, "montant": 100})

    def test_un_montant_nul_ou_negatif_est_refuse(self):
        for montant in (0, -10):
            with self.assertRaises(Refus):
                self._valider({"mode": "Espèces", "montant": montant})

    def test_un_cheque_complet_passe(self):
        lignes = self._valider({"mode": "Chèque", "montant": 500, "n_piece": "1234567",
                                "banque": "Zitouna", "photo": "data:image/jpeg;base64,xx"})
        self.assertEqual(lignes[0]["numero"], "1234567")
        self.assertEqual(lignes[0]["banque"], "Zitouna")

    def test_un_cheque_sans_numero_a_sept_chiffres_est_refuse(self):
        for numero in ("", "12345", "12345678", "abcdefg"):
            with self.assertRaises(Refus, msg=numero):
                self._valider({"mode": "Chèque", "montant": 500, "n_piece": numero,
                               "banque": "Zitouna", "photo": "data:image/jpeg;base64,xx"})

    def test_un_cheque_sans_banque_est_refuse(self):
        with self.assertRaises(Refus):
            self._valider({"mode": "Chèque", "montant": 500, "n_piece": "1234567",
                           "banque": "", "photo": "data:image/jpeg;base64,xx"})

    def test_un_cheque_sans_photo_est_refuse(self):
        with self.assertRaises(Refus):
            self._valider({"mode": "Chèque", "montant": 500, "n_piece": "1234567",
                           "banque": "Zitouna"})

    def test_deux_fois_le_meme_cheque_est_refuse(self):
        """(n°, banque) est la clé du chèque : deux lignes identiques seraient deux
        paiements pour un seul papier."""
        ligne = {"mode": "Chèque", "montant": 100, "n_piece": "1234567",
                 "banque": "Zitouna", "photo": "data:image/jpeg;base64,xx"}
        with self.assertRaises(Refus):
            self._valider(ligne, dict(ligne))

    def test_un_virement_sans_reference_est_refuse(self):
        """ERPNext exige `reference_no` sur un paiement bancaire."""
        with self.assertRaises(Refus):
            self._valider({"mode": "Virement", "montant": 500})

    def test_un_virement_avec_reference_passe_sans_photo(self):
        lignes = self._valider({"mode": "Virement", "montant": 500, "n_piece": "VIR-2026-88"})
        self.assertEqual(lignes[0]["numero"], "VIR-2026-88")
        self.assertIsNone(lignes[0]["photo"])

    def test_plusieurs_cheques_et_virements_cohabitent(self):
        lignes = self._valider(
            {"mode": "Espèces", "montant": 100},
            {"mode": "Chèque", "montant": 200, "n_piece": "1111111",
             "banque": "Zitouna", "photo": "data:image/jpeg;base64,xx"},
            {"mode": "Chèque", "montant": 300, "n_piece": "2222222",
             "banque": "BIAT", "photo": "data:image/jpeg;base64,xx"},
            {"mode": "Virement", "montant": 400, "n_piece": "VIR-1"},
            {"mode": "Virement", "montant": 500, "n_piece": "VIR-2"})
        self.assertEqual([l["mode"] for l in lignes],
                         ["Espèces", "Chèque", "Chèque", "Virement", "Virement"])

    def test_une_liste_vide_est_refusee(self):
        with self.assertRaises(Refus):
            CD._valider_reglements([])

    def test_le_json_du_dialogue_est_accepte(self):
        lignes = CD._valider_reglements('[{"mode": "Espèces", "montant": 12.5}]')
        self.assertEqual(lignes[0]["montant"], 12.5)


class TestControleDuTotal(_SansSite, unittest.TestCase):
    """On ne crée jamais d'avance : le total réglé ne peut pas dépasser la sélection."""

    def test_le_depassement_est_refuse(self):
        with self.assertRaises(Refus):
            CD._controler_total(800.01, 800.0)

    def test_le_reglement_exact_passe(self):
        CD._controler_total(800.0, 800.0)

    def test_un_reglement_partiel_passe(self):
        CD._controler_total(100.0, 800.0)

    def test_les_millimes_ne_bloquent_pas(self):
        CD._controler_total(800.0005, 800.0)


class TestChampReglementAbsent(_SansSite, unittest.TestCase):
    """LE PIÈGE SILENCIEUX. Frappe n'enregistre que les champs connus du DocType :
    avant le patch `ensure_reglement_caisse_field`, poser `custom_reglement_caisse`
    sur le paiement ne le persiste pas. Le règlement partirait quand même — argent
    sorti du tiroir — mais le rapport de caisse ne le compterait jamais, même après
    la migration (la colonne naît à 0), et le solde théorique de clôture serait
    surévalué sans rien pour le rattraper. On refuse AVANT toute écriture.
    """

    def setUp(self):
        super().setUp()
        self.champ = None            # le patch n'a pas tourné
        self.colonne = True
        self.crees = []              # les Payment Entry qu'on aurait créés
        self.requetes = []
        self._vrais_frappe = (frappe.only_for, frappe.get_meta, frappe.db,
                              frappe.new_doc)
        frappe.only_for = lambda *a, **k: None
        frappe.get_meta = lambda doctype: types.SimpleNamespace(
            get_field=lambda champ: self.champ)
        frappe.db = types.SimpleNamespace(
            has_column=lambda doctype, colonne: self.colonne,
            sql=lambda *a, **k: self.requetes.append(a) or [],
            commit=lambda: None)
        frappe.new_doc = lambda doctype: self.crees.append(doctype) or None
        # `@frappe.whitelist()` enveloppe la méthode dans un contrôle de typage qui
        # lit `frappe.local.flags` : hors site, ce drapeau n'existe pas.
        self._sans_flags = not hasattr(frappe.local, "flags")
        if self._sans_flags:
            frappe.local.flags = frappe._dict(in_test=False)

    def tearDown(self):
        (frappe.only_for, frappe.get_meta, frappe.db,
         frappe.new_doc) = self._vrais_frappe
        if self._sans_flags:
            del frappe.local.flags
        super().tearDown()

    def _payer(self):
        return CD.payer_factures(
            "AQUA FROID", '["PINV-01"]',
            '[{"mode": "Espèces", "montant": 300}]')

    def test_sans_le_champ_le_reglement_est_refuse(self):
        with self.assertRaises(Refus) as levee:
            self._payer()
        self.assertIn("custom_reglement_caisse", str(levee.exception))

    def test_sans_le_champ_aucun_paiement_n_est_cree(self):
        """Et rien n'est même lu : le refus vient avant toute écriture."""
        with self.assertRaises(Refus):
            self._payer()
        self.assertEqual(self.crees, [])
        self.assertEqual(self.requetes, [])

    def test_le_champ_declare_mais_sans_colonne_est_refuse_aussi(self):
        """DocType à jour, base pas encore migrée : le paiement ne garderait pas
        davantage son drapeau."""
        self.champ = types.SimpleNamespace(fieldname=CD.CHAMP_REGLEMENT)
        self.colonne = False
        with self.assertRaises(Refus):
            self._payer()
        self.assertEqual(self.crees, [])

    def test_avec_le_champ_le_controle_laisse_passer(self):
        self.champ = types.SimpleNamespace(fieldname=CD.CHAMP_REGLEMENT)
        self.colonne = True
        CD._controler_champ_reglement()          # ne lève pas


def _copier_comme_frappe(doc, champs, from_amend=False):
    """La règle de Frappe rejouée telle quelle sur un document donné.

    `frappe.model.copy_doc(doc, from_amend)` — apps/frappe/frappe/public/js/
    frappe/model/create_new.js:281 — écarte les champs `no_copy` d'une
    DUPLICATION (`is_no_copy = !from_amend && cint(df.no_copy) == 1`, l.291) et
    les GARDE en amendement (« Amend » appelle `copy_doc(fn, 1)`, form.js:1035).
    `name` et `amended_from` ne suivent jamais.

    Le `no_copy` du champ vient de la VRAIE déclaration du patch : si quelqu'un
    l'y retire, ces tests tombent.
    """
    jamais = {"name", "amended_from", "amendment_date", "cancel_reason"}
    return {cle: valeur for cle, valeur in doc.items()
            if cle not in jamais
            and not (not from_amend and champs.get(cle, {}).get("no_copy"))}


class TestDrapeauNonTransmisALaCopie(unittest.TestCase):
    """Le drapeau désigne un paiement SORTI DU TIROIR. Dupliquer dans ERPNext un
    règlement de caisse ne doit pas en fabriquer un second : le champ est en
    lecture seule, personne ne pourrait le décocher sur la copie, et le rapport
    compterait comme dépense de caisse un paiement saisi ailleurs — en espèces,
    le solde théorique de la clôture baisserait sans qu'un billet ne bouge.
    """

    def setUp(self):
        self.champs = {c["fieldname"]: c for c in [PATCH.CHAMP]}
        self.paiement = {
            "name": "ACC-PAY-2026-04001",
            "payment_type": "Pay",
            "party": "AQUA FROID",
            "paid_amount": 300.0,
            CD.CHAMP_REGLEMENT: 1,
        }

    def test_le_patch_declare_le_champ_no_copy(self):
        self.assertEqual(PATCH.CHAMP["fieldname"], CD.CHAMP_REGLEMENT)
        self.assertEqual(PATCH.CHAMP["no_copy"], 1)

    def test_le_champ_reste_en_lecture_seule_et_decoche_par_defaut(self):
        """Personne ne le pose à la main : seul `payer_factures` le coche."""
        self.assertEqual(PATCH.CHAMP["read_only"], 1)
        self.assertEqual(PATCH.CHAMP["default"], "0")
        self.assertEqual(PATCH.CHAMP["fieldtype"], "Check")

    def test_une_duplication_ordinaire_perd_le_drapeau(self):
        copie = _copier_comme_frappe(self.paiement, self.champs)
        self.assertNotIn(CD.CHAMP_REGLEMENT, copie)
        self.assertEqual(copie["paid_amount"], 300.0)   # le reste suit bien

    def test_un_amendement_garde_le_drapeau(self):
        """Corriger un règlement RÉELLEMENT issu de la caisse doit donner un
        paiement qui en reste un — sinon la dépense disparaîtrait du rapport."""
        amende = _copier_comme_frappe(self.paiement, self.champs, from_amend=True)
        self.assertEqual(amende[CD.CHAMP_REGLEMENT], 1)
        self.assertNotIn("name", amende)

    def test_un_paiement_ordinaire_ne_gagne_jamais_le_drapeau(self):
        ordinaire = {"name": "ACC-PAY-2026-04002", "paid_amount": 50.0}
        for from_amend in (False, True):
            copie = _copier_comme_frappe(ordinaire, self.champs, from_amend)
            self.assertNotIn(CD.CHAMP_REGLEMENT, copie)


class TestDialogueDeReglement(unittest.TestCase):
    """Le contrat entre la page et le serveur : ce que le dialogue envoie."""

    def setUp(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            self.js = f.read()

    def test_le_dialogue_appelle_payer_factures_avec_les_trois_arguments(self):
        appel = self.js[self.js.index('method: API + ".payer_factures"'):][:600]
        for argument in ("supplier:", "factures:", "paiements:"):
            self.assertIn(argument, appel)

    def test_les_trois_modes_de_reglement_sont_proposes(self):
        bloc = self.js[self.js.index("function rcj_reglement_fournisseur"):][:400]
        self.assertIn('["Espèces", "Chèque", "Virement"]', bloc)

    def test_seules_les_factures_ont_une_case_a_cocher(self):
        """Les captures caisse (fiches FAS) ne se règlent pas ici : elles n'ont
        pas encore de facture d'achat à référencer."""
        liste = self.js[self.js.index("function rcj_factures_a_payer"):
                        self.js.index("function rcj_reglement_fournisseur")]
        debut = liste.index("Captures caisse non payées")
        captures = liste[debut:liste.index("</table>", debut)]
        self.assertIn("rcj-fp-choix", liste[:debut])   # les factures, elles, en ont
        self.assertNotIn("rcj-fp-choix", captures)


if __name__ == "__main__":
    unittest.main()
