"""Tests de l'écran « Ma journée ».

Convention : `unittest.TestCase` pur. On teste ce qui se teste sans base — la
lecture des photos, la liste des résultats d'appel, et surtout LE PÉRIMÈTRE DE
SUPERVISION, qui décide qui voit la journée de qui.
"""
from __future__ import annotations

import unittest

import frappe

from customization_app import planning_employe as P


class _Doc:
    """Le minimum que `_photos_de` interroge : deux champs texte."""

    def __init__(self, avant="", apres=""):
        self._v = {"liste_photos_avant": avant, "liste_photos_apres": apres}

    def get(self, champ):
        return self._v.get(champ)


class TestPhotosDeLaTache(unittest.TestCase):
    """Les photos servent à vérifier le bordereau Aramex : les rater, c'est
    refuser un numéro parfaitement valide."""

    def test_le_format_pose_par_l_application(self):
        d = _Doc(avant='📁 "/private/files/a.png"\n📁 "/private/files/b.png"')
        self.assertEqual(P._photos_de(d),
                         ["/private/files/a.png", "/private/files/b.png"])

    def test_les_deux_champs_sont_lus(self):
        d = _Doc(avant='📁 "/private/files/a.png"', apres='📁 "/private/files/b.png"')
        self.assertEqual(len(P._photos_de(d)), 2)

    def test_une_url_nue_privee_est_reconnue(self):
        """Repli : une URL sans guillemets, et PRIVÉE — les photos de clôture
        le sont toutes. Le repli d'origine ne voyait que `/files/`."""
        self.assertEqual(P._photos_de(_Doc(avant="/private/files/c.jpg")),
                         ["/private/files/c.jpg"])

    def test_un_champ_vide_ne_leve_pas(self):
        self.assertEqual(P._photos_de(_Doc()), [])

    def test_une_ligne_parasite_est_ignoree(self):
        d = _Doc(avant='note du technicien\n📁 "/private/files/a.png"')
        self.assertEqual(P._photos_de(d), ["/private/files/a.png"])


class TestSupervision(unittest.TestCase):
    """QUI voit la journée d'un autre — le point le plus sensible de l'écran."""

    def test_seul_system_manager_supervise(self):
        """⚠️ MESURÉ SUR LA BASE RÉELLE (02/09/2026) : « Sales Manager »,
        « Maintenance Manager » et « Gestionaire Activité » sont portés par les
        TECHNICIENS eux-mêmes. Les inclure ouvrirait la journée de chacun à
        tout le monde — l'inverse de ce qui est demandé."""
        self.assertEqual(set(P.ROLES_SUPERVISION), {"System Manager"})

    def test_les_roles_de_terrain_ne_supervisent_pas(self):
        for role in ("Sales Manager", "Maintenance Manager", "Gestionaire Activité",
                     "Sales User", "Employee", "Stock User"):
            self.assertNotIn(role, P.ROLES_SUPERVISION, role)


class TestResultatsAppel(unittest.TestCase):
    """L'écran demande le résultat APRÈS l'appel : « on a composé le numéro »
    ne dit pas si le client a décroché, et c'est cela qui décide de la suite."""

    def test_les_trois_suites_possibles(self):
        self.assertEqual(list(P.RESULTATS_APPEL),
                         ["Répondu", "Sans réponse", "À rappeler"])

    def test_le_prefixe_permet_de_retrouver_les_appels(self):
        """La trace vit dans un commentaire : sans préfixe stable, l'historique
        des appels devient irretrouvable."""
        self.assertTrue(P.PREFIXE_APPEL.strip())
        self.assertIn("Appel", P.PREFIXE_APPEL)
