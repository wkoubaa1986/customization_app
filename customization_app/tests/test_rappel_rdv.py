"""Tests du rappel de rendez-vous du soir.

Convention : `unittest.TestCase` pur, aucune base. Les deux seuls accès
extérieurs — la fiche employé et l'échéancier de la commande — sont injectés.

Ces tests portent surtout sur les REFUS et les données manquantes : c'est ce
qui faisait tomber l'ancien script sept soirs sur trente, et privait de rappel
tous les clients situés après le rendez-vous fautif.
"""
from __future__ import annotations

import unittest
from datetime import datetime

import frappe

from customization_app import rappel_rdv as R


def _tache(**kw):
    base = {
        "name": "Tache-1", "status": "Open", "custom_client": "CLI-1",
        "nom_client": "Ahmed Farhat", "custom_type_dintervention": "Entretien",
        "starts_on": datetime(2026, 9, 3, 9, 30), "ends_on": datetime(2026, 9, 3, 10, 0),
        "custom_choix_du_staff": "HR-EMP-00002", "commande_client": None,
        "dans_local": "",
    }
    base.update(kw)
    return frappe._dict(base)


class TestNumeros(unittest.TestCase):
    """L'ancien code calculait le numéro nettoyé puis renvoyait la ligne BRUTE,
    et plantait sur toute ligne non conforme (`itel_T[0]`)."""

    def test_un_numero_simple(self):
        self.assertEqual(R._numeros("98511119"), ["98511119"])

    def test_les_espaces_et_tirets_disparaissent(self):
        self.assertEqual(R._numeros("98 511 119"), ["98511119"])
        self.assertEqual(R._numeros("98-511-119"), ["98511119"])

    def test_l_indicatif_pays_est_retire(self):
        self.assertEqual(R._numeros("+216 98511119"), ["98511119"])

    def test_plusieurs_lignes(self):
        self.assertEqual(R._numeros("98511119\n22611902"), ["98511119", "22611902"])

    def test_un_fixe_est_ecarte(self):
        """Un rappel par SMS sur un fixe ne sert à rien."""
        self.assertEqual(R._numeros("71234567"), [])

    def test_une_ligne_illisible_ne_fait_pas_tomber_le_reste(self):
        """LE défaut d'origine : cette ligne suffisait à tuer la tournée."""
        self.assertEqual(R._numeros("bureau\n98511119\n---"), ["98511119"])

    def test_un_doublon_n_envoie_pas_deux_fois(self):
        self.assertEqual(R._numeros("98511119\n98 511 119"), ["98511119"])

    def test_un_champ_vide_ne_leve_pas(self):
        self.assertEqual(R._numeros(None), [])
        self.assertEqual(R._numeros(""), [])


class TestMessages(unittest.TestCase):
    def setUp(self):
        self._vrai = R._technicien
        R._technicien = lambda t: ("Jamel Aloui", "51511918")

    def tearDown(self):
        R._technicien = self._vrai

    def test_le_rappel_nomme_le_technicien_et_son_numero(self):
        """Demande du 02/09/2026 : le client doit pouvoir joindre celui qui vient."""
        m = R.message_rendez_vous(_tache())
        self.assertIn("Jamel Aloui", m)
        self.assertIn("51511918", m)

    def test_l_heure_est_annoncee_comme_indicative(self):
        """La moitié des rendez-vous vient du portail, où le client a réservé une
        DEMI-JOURNÉE : annoncer 09:30-10:00 comme ferme serait une promesse
        que le magasin n'a jamais faite."""
        m = R.message_rendez_vous(_tache())
        self.assertIn("09:30", m)
        self.assertIn(R.AVERTISSEMENT, m)

    def test_le_type_apparait_dans_le_rappel(self):
        for type_i in R.TYPES_RAPPELES:
            self.assertIn(type_i, R.message_rendez_vous(
                _tache(custom_type_dintervention=type_i)))

    def test_sans_technicien_le_message_reste_valide(self):
        """29 tâches en 60 jours n'ont pas de nom d'employé : l'ancien script y
        collait `None` et levait un TypeError qui emportait la tournée."""
        R._technicien = lambda t: ("", "")
        m = R.message_rendez_vous(_tache())
        self.assertNotIn("None", m)
        self.assertIn("Entretien", m)

    def test_la_livraison_a_son_propre_message(self):
        """Le client attend un passage, pas une intervention."""
        m = R.message_livraison(_tache(custom_type_dintervention="Livraison"))
        self.assertIn("livree demain", m)
        self.assertIn("Livreur", m)

    def test_l_avis_aramex_porte_le_numero_de_suivi(self):
        m = R.message_aramex(_tache(), "51330112912")
        self.assertIn("ARAMEX", m)
        self.assertIn("51330112912", m)

    def test_l_avis_aramex_sans_bordereau_le_dit(self):
        """Mieux vaut l'annoncer que d'envoyer « N de suivi : » suivi de rien."""
        m = R.message_aramex(_tache(), "")
        self.assertNotIn("N de suivi", m)
        self.assertIn("communique", m)

    def test_le_message_tient_en_deux_segments(self):
        """L'ancien en coûtait trois : son paragraphe d'excuses valait un SMS
        entier sur chaque rappel."""
        self.assertLessEqual(len(R.message_rendez_vous(_tache())), 306)


class TestPerimetre(unittest.TestCase):
    """Qui reçoit un rappel, et qui n'en reçoit pas."""

    def setUp(self):
        self._tech, self._aramex = R._technicien, R._est_aramex
        R._technicien = lambda t: ("Jamel Aloui", "51511918")
        R._est_aramex = lambda c: c == "WEB1-ARAMEX"

    def tearDown(self):
        R._technicien, R._est_aramex = self._tech, self._aramex

    def test_les_quatre_types_prevus_sont_rappeles(self):
        for type_i in R.TYPES_RAPPELES:
            texte, motif = R.preparer(_tache(custom_type_dintervention=type_i))
            self.assertIsNone(motif, type_i)
            self.assertTrue(texte)

    def test_autre_n_est_pas_rappele(self):
        """Il l'était par accident : l'ancien script rappelait tout ce qui ne
        s'appelait pas « Livraison ». Décision du 02/09/2026 : non."""
        texte, motif = R.preparer(_tache(custom_type_dintervention="Autre"))
        self.assertIsNone(texte)
        self.assertIn("non rappelé", motif)

    def test_un_type_inconnu_n_envoie_rien(self):
        """Un type créé demain ne doit pas se mettre à écrire aux clients."""
        texte, motif = R.preparer(_tache(custom_type_dintervention="Diagnostic"))
        self.assertIsNone(texte)

    def test_la_livraison_de_notre_equipe_est_rappelee(self):
        texte, motif = R.preparer(_tache(custom_type_dintervention="Livraison",
                                         commande_client="SAL-ORD-1"))
        self.assertIsNone(motif)
        self.assertIn("livree demain", texte)

    def test_la_livraison_aramex_n_est_pas_rappelee_la_veille(self):
        """On ne sait pas quel jour Aramex présentera le colis : l'avis part le
        soir de la remise, pas la veille d'une date qu'on ne tient pas."""
        texte, motif = R.preparer(_tache(custom_type_dintervention="Livraison",
                                         commande_client="WEB1-ARAMEX"))
        self.assertIsNone(texte)
        self.assertIn("Aramex", motif)

    def test_une_livraison_sans_commande_ne_fait_pas_tomber_la_tournee(self):
        """Le plantage du 01/09 et du 30/08 : « Sales Order None not found »."""
        texte, motif = R.preparer(_tache(custom_type_dintervention="Livraison",
                                         commande_client=None))
        self.assertIsNone(motif)          # traitée comme livraison de notre équipe
        self.assertTrue(texte)

    def test_une_tache_sans_date_est_sautee_proprement(self):
        texte, motif = R.preparer(_tache(starts_on=None))
        self.assertIsNone(texte)
        self.assertEqual(motif, "sans date")

    def test_l_avis_aramex_refuse_une_livraison_de_notre_equipe(self):
        texte, motif = R.preparer(_tache(custom_type_dintervention="Livraison",
                                         commande_client="SAL-ORD-1"), aramex=True)
        self.assertIsNone(texte)
        self.assertIn("pas Aramex", motif)
