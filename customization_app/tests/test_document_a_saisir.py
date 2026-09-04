"""Tests du panneau « Voir le document » pendant la saisie d'une facture ou commande d'achat.

⚠️ LE SCAN N'EST PAS SUR LA PIÈCE QU'ON SAISIT. Il a été pris en caisse et attaché à la fiche de
la file (« Facture Achat a Saisir ») ; la facture d'achat, elle, naît vide. Le serveur fait donc
le chemin inverse — et surtout AVANT enregistrement, moment où on en a le plus besoin.
"""
from __future__ import annotations

import inspect
import unittest

from customization_app import caisse_depenses as C


class TestRechercheDuScan(unittest.TestCase):

    def source(self):
        return inspect.getsource(C.scans_a_saisir)

    def test_trois_chemins_dans_cet_ordre(self):
        src = self.source()
        self.assertIn("custom_fiche_caisse", inspect.getsource(C))
        self.assertIn("_CHAMP_FICHE.get(doctype)", src)
        self.assertIn('"numero_facture": (bill_no or "").strip()', src)

    def test_la_piece_PAS_ENCORE_enregistree_est_le_cas_qui_compte(self):
        """Le bouton « Créer la facture d'achat » ne transmet que fournisseur, n° et date —
        aucun lien. Or c'est PENDANT la frappe qu'on a besoin du scan, pas après."""
        self.assertIn("if not fiches and (bill_no", self.source())

    def test_seules_les_fiches_A_SAISIR_sont_retenues_par_numero(self):
        """Un numéro peut se répéter d'une année sur l'autre ; seule celle en attente est la
        bonne."""
        self.assertIn('"statut": "À saisir"', self.source())

    def test_le_nom_est_facultatif(self):
        """Une pièce neuve n'a pas encore de nom."""
        self.assertIsNone(inspect.signature(C.scans_a_saisir).parameters["name"].default)

    def test_seuls_les_formats_affichables_sortent(self):
        """Un .docx ne s'ouvre pas dans un onglet du navigateur."""
        self.assertEqual(C._AFFICHABLES,
                         (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"))

    def test_aucun_doublon_de_fichier(self):
        """La caisse attache le même scan à deux endroits : le montrer deux fois n'aide pas."""
        self.assertIn("f.file_url in vus", self.source())

    def test_les_trois_pieces_d_achat_sont_couvertes(self):
        self.assertEqual(set(C._CHAMP_FICHE),
                         {"Purchase Invoice", "Purchase Order", "Purchase Receipt"})


class TestUneSeuleAffectationDeDoctypeJs(unittest.TestCase):
    """⚠️ IL Y EN AVAIT DEUX, ET PYTHON GARDE LA DERNIÈRE.

    `doctype_js` était défini une fois pour la facture d'achat, une fois pour l'article : le
    bouton « 📦 Rattacher des BL » n'a jamais été chargé depuis. Vérifié le 04/09/2026 sur le
    script réellement injecté — 51 237 caractères, et pas une ligne de
    `purchase_invoice_caisse`. Ce test empêche que ça recommence.
    """

    def test_une_seule_affectation_active(self):
        from customization_app import hooks

        src = inspect.getsource(hooks)
        actives = [l for l in src.splitlines() if l.startswith("doctype_js")]
        self.assertEqual(len(actives), 1, actives)

    def test_les_cinq_doctypes_y_sont(self):
        from customization_app import hooks

        for dt in ("Purchase Invoice", "Purchase Order", "Purchase Receipt",
                   "Facture Achat a Saisir", "Item"):
            self.assertIn(dt, hooks.doctype_js, dt)


class TestFermetureDuPanneau(unittest.TestCase):
    """⚠️ UN PANNEAU QU'ON NE PEUT PAS FERMER EST UN PANNEAU CASSÉ.

    Posé à `top: 0`, ses quarante premiers pixels passaient DERRIÈRE la barre de navigation de
    Frappe : l'en-tête et son bouton de fermeture étaient invisibles, et il ne restait qu'à
    recharger la page (constaté le 04/09/2026).
    """

    def js(self):
        import os

        chemin = os.path.join(os.path.dirname(inspect.getfile(C)), "public", "js",
                              "document_a_saisir.js")
        with open(chemin, encoding="utf-8") as fh:
            return fh.read()

    def test_le_panneau_commence_sous_la_barre_de_navigation(self):
        self.assertIn("top: var(--navbar-height, 60px)", self.js())

    def test_echap_ferme_aussi(self):
        """Le jour où l'en-tête est masqué — par un thème, une fenêtre étroite — Échap reste le
        seul moyen de fermer sans recharger."""
        js = self.js()
        self.assertIn('e.key === "Escape"', js)
        self.assertIn('document.addEventListener("keydown", sur_echap)', js)
        self.assertIn('document.removeEventListener("keydown", sur_echap)', js)

    def test_le_bouton_de_fermeture_porte_un_mot(self):
        """Un ✕ seul se cherche ; « ✕ Fermer » se voit."""
        self.assertIn('✕ ${__("Fermer")}', self.js())

    def test_aucun_backtick_dans_le_bloc_de_style(self):
        """⚠️ Un backtick dans un commentaire CSS ferme le template literal qui porte TOUT le
        style — le fichier ne compile plus. C'est arrivé en écrivant ce correctif."""
        js = self.js()
        style = js.split("st.textContent = `")[1].split("`;")[0]
        self.assertNotIn("`", style)

    def test_la_facture_d_achat_garde_ses_DEUX_scripts(self):
        from customization_app import hooks

        self.assertEqual(len(hooks.doctype_js["Purchase Invoice"]), 2)
