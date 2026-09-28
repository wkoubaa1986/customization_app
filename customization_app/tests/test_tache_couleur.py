"""Tests de la couleur d'une Tache de travail (`compute_tache_color`).

La couleur du calendrier n'est pas décorative : c'est elle qui dit d'un coup
d'œil à QUEL TECHNICIEN la tâche revient. Le cyan « partenaire » s'appliquait
dès que le CLIENT avait été créé par le compte partenaire, ce qui repeignait
aussi les rendez-vous pris par le magasin pour ce client. Il marque désormais
la tâche prise par le partenaire, et rien d'autre.

Convention : `unittest.TestCase` pur, aucune base.
"""
from __future__ import annotations

import unittest

from customization_app.api import (
    PARTNER_COLOR,
    PARTNER_USER,
    STAFF_COLOR_DEFAULT,
    compute_tache_color,
)

JAMEL = "HR-EMP-00002"
ORANGE_JAMEL = "#ECAD4B"


def _tache(**kw):
    base = {
        "status": "Open",
        "owner": "magasin@example.com",
        "custom_client": "CLI-1",
        "custom_choix_du_staff": JAMEL,
    }
    base.update(kw)
    return base


class TestLeStatutPasseAvantTout(unittest.TestCase):
    def test_terminee_est_verte(self):
        self.assertEqual(compute_tache_color(_tache(status="Completed")), "#32CD32")

    def test_annulee_est_grise(self):
        self.assertEqual(compute_tache_color(_tache(status="Cancelled")), "#DCDCDC")

    def test_terminee_par_le_partenaire_reste_verte(self):
        self.assertEqual(
            compute_tache_color(_tache(status="Completed", owner=PARTNER_USER)),
            "#32CD32",
        )


class TestLeCyanPartenaire(unittest.TestCase):
    def test_une_tache_prise_par_le_partenaire_est_cyan(self):
        self.assertEqual(
            compute_tache_color(_tache(owner=PARTNER_USER)), PARTNER_COLOR
        )

    def test_le_createur_du_client_n_influe_plus(self):
        """Le cas du ticket : rendez-vous pris par le magasin depuis la commande,
        pour un client saisi jadis par le partenaire. Il doit porter la couleur
        de l'employé affecté, pas le cyan."""
        self.assertEqual(
            compute_tache_color(_tache(owner="jamel@example.com",
                                       custom_client="CLI-PARTENAIRE")),
            ORANGE_JAMEL,
        )


class TestLaCouleurParEmploye(unittest.TestCase):
    def test_jamel_est_orange(self):
        self.assertEqual(
            compute_tache_color(_tache(custom_choix_du_staff=JAMEL)), ORANGE_JAMEL
        )

    def test_un_staff_inconnu_tombe_sur_le_gris_par_defaut(self):
        self.assertEqual(
            compute_tache_color(_tache(custom_choix_du_staff="HR-EMP-99999")),
            STAFF_COLOR_DEFAULT,
        )

    def test_sans_staff_le_gris_par_defaut(self):
        self.assertEqual(
            compute_tache_color(_tache(custom_choix_du_staff=None)), STAFF_COLOR_DEFAULT
        )

    def test_un_statut_inconnu_ne_court_circuite_pas_la_couleur_employe(self):
        self.assertEqual(compute_tache_color(_tache(status="Working")), ORANGE_JAMEL)

    def test_un_document_vide_ne_leve_pas(self):
        self.assertEqual(compute_tache_color({}), STAFF_COLOR_DEFAULT)


if __name__ == "__main__":
    unittest.main()


class TestLesNouveauxEmployesDu28092026(unittest.TestCase):
    """Salma (HR-EMP-00011) et Hedi (HR-EMP-00012) : sans entrée dans le barème,
    ils tomberaient tous deux sur le gris par défaut, celui de HR-EMP-00007."""

    def test_salma_a_sa_couleur(self):
        self.assertEqual(compute_tache_color(_tache(custom_choix_du_staff="HR-EMP-00011")), "#0F766E")

    def test_hedi_a_sa_couleur(self):
        self.assertEqual(compute_tache_color(_tache(custom_choix_du_staff="HR-EMP-00012")), "#8B4513")

    def test_aucune_couleur_du_bareme_n_est_partagee(self):
        from customization_app.api import STAFF_COLORS, STATUS_COLORS
        couleurs = [c.lower() for c in STAFF_COLORS.values()]
        couleurs += [c.lower() for c in STATUS_COLORS.values()] + [PARTNER_COLOR.lower()]
        # le gris de HR-EMP-00007 est aussi le défaut : c'est le seul doublon toléré
        self.assertEqual(len(couleurs), len(set(couleurs)))
        self.assertNotIn(STAFF_COLORS["HR-EMP-00011"].lower(), (STAFF_COLOR_DEFAULT.lower(),))
        self.assertNotIn(STAFF_COLORS["HR-EMP-00012"].lower(), (STAFF_COLOR_DEFAULT.lower(),))
