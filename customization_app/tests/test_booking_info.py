"""Tests des bandeaux « autres rendez-vous du client » de la Tache de travail.

Le Server Script d'origine composait le libellé d'un rendez-vous en collant ses
champs bout à bout : un rendez-vous sans employé, sans type ou sans date levait
« can only concatenate str (not "NoneType") » à CHAQUE ouverture de la fiche.
Les tests portent donc d'abord sur les champs manquants.

Convention : `unittest.TestCase` pur, aucune base.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import Mock, patch

import frappe

from customization_app import api
from customization_app.api import _creneaux_se_chevauchent, _libelle_rdv


def _rdv(**kw):
    base = {
        "name": "TACHE-1",
        "custom_type_dintervention": "Entretien",
        "custom_employé": "Jamel Bouzid",
        "starts_on": datetime(2026, 9, 3, 9, 30),
        "ends_on": datetime(2026, 9, 3, 10, 0),
    }
    base.update(kw)
    return base


class TestLibelleDuRendezVous(unittest.TestCase):
    def test_un_rendez_vous_complet(self):
        self.assertEqual(
            _libelle_rdv(_rdv()),
            "Entretien avec Jamel Bouzid le 03/09/2026 09:30 à 10:00",
        )

    def test_une_date_en_texte_est_acceptee(self):
        """Frappe rend `starts_on` tantôt en datetime, tantôt en chaîne."""
        self.assertEqual(
            _libelle_rdv(_rdv(starts_on="2026-09-03 09:30:00",
                              ends_on="2026-09-03 10:00:00")),
            "Entretien avec Jamel Bouzid le 03/09/2026 09:30 à 10:00",
        )

    def test_sans_employe_le_reste_est_annonce(self):
        self.assertEqual(
            _libelle_rdv(_rdv(custom_employé=None)),
            "Entretien le 03/09/2026 09:30 à 10:00",
        )

    def test_sans_type_le_rendez_vous_reste_nomme(self):
        self.assertEqual(
            _libelle_rdv(_rdv(custom_type_dintervention=None)),
            "Rendez-vous avec Jamel Bouzid le 03/09/2026 09:30 à 10:00",
        )

    def test_sans_heure_de_fin(self):
        self.assertEqual(
            _libelle_rdv(_rdv(ends_on=None)),
            "Entretien avec Jamel Bouzid le 03/09/2026 09:30",
        )

    def test_sans_date_du_tout(self):
        self.assertEqual(
            _libelle_rdv(_rdv(starts_on=None, ends_on=None)),
            "Entretien avec Jamel Bouzid",
        )

    def test_tous_les_champs_vides_ne_levent_pas(self):
        """L'erreur exacte du ticket : ici on veut un libellé, pas une exception."""
        self.assertEqual(
            _libelle_rdv({"custom_type_dintervention": None, "custom_employé": None,
                          "starts_on": None, "ends_on": None}),
            "Rendez-vous",
        )

    def test_un_dictionnaire_vide_ne_leve_pas(self):
        self.assertEqual(_libelle_rdv({}), "Rendez-vous")

    def test_une_date_illisible_est_ignoree(self):
        self.assertEqual(_libelle_rdv(_rdv(starts_on="pas une date", ends_on=None)),
                         "Entretien avec Jamel Bouzid")


H9 = datetime(2026, 9, 3, 9, 0)
H10 = datetime(2026, 9, 3, 10, 0)
H11 = datetime(2026, 9, 3, 11, 0)
H12 = datetime(2026, 9, 3, 12, 0)


class TestChevauchementDeCreneaux(unittest.TestCase):
    """Le bandeau rouge n'a de sens que s'il est rare : un faux conflit par jour
    et plus personne ne le lit."""

    def test_deux_creneaux_disjoints(self):
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, H11, H12))

    def test_deux_creneaux_adjacents_ne_se_chevauchent_pas(self):
        """9h-10h puis 10h-11h : c'est une journée normale, pas un conflit."""
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, H10, H11))

    def test_deux_creneaux_qui_se_recouvrent(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H11, H10, H12))

    def test_un_creneau_inclus_dans_l_autre(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H12, H10, H11))

    def test_deux_creneaux_identiques(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H10, H9, H10))

    def test_l_ordre_des_arguments_est_indifferent(self):
        self.assertTrue(_creneaux_se_chevauchent(H10, H12, H9, H11))

    def test_sans_debut_pas_de_conflit(self):
        """On ne crie pas au conflit sur une donnée qu'on n'a pas."""
        self.assertFalse(_creneaux_se_chevauchent(None, H10, H9, H11))
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, None, H11))

    def test_sans_fin_le_creneau_se_reduit_a_son_debut(self):
        self.assertTrue(_creneaux_se_chevauchent(H9, H12, H10, None))
        self.assertFalse(_creneaux_se_chevauchent(H9, H10, H11, None))


# ---------------------------------------------------------------------------
# La méthode elle-même, avec les lectures Frappe simulées.
# ---------------------------------------------------------------------------
MAINTENANT = datetime(2026, 9, 3, 8, 0)
TACHE_COURANTE = {"starts_on": datetime(2026, 9, 3, 9, 0),
                  "ends_on": datetime(2026, 9, 3, 10, 0)}


def _throw(message, exc=None, **kwargs):
    """`frappe.throw` hors site : on ne garde que ce qui nous intéresse ici,
    la classe d'exception levée."""
    raise (exc or frappe.ValidationError)(message)


@contextmanager
def _frappe_simule(rdvs=(), tache=None, droit=True, droit_sur_la_tache=True):
    """Isole les seules lectures que la méthode effectue.

    `droit` couvre le contrôle au niveau du doctype, `droit_sur_la_tache` celui
    sur la tâche ouverte."""
    def has_permission(doctype, ptype="read", doc=None, **kwargs):
        return droit_sur_la_tache if doc else droit

    # Hors site, `frappe.local.flags` n'existe pas ; le décorateur
    # `@frappe.whitelist()` le consulte pour valider les types des arguments.
    flags_absents = not hasattr(frappe.local, "flags")
    if flags_absents:
        frappe.local.flags = frappe._dict(in_test=False)

    reponse = {}
    lectures = Mock()
    lectures.get_list.return_value = [dict(r) for r in rdvs]
    lectures.db.get_value.return_value = tache
    lectures.log_error.return_value = None

    with patch.object(api, "_", lambda texte: texte), \
            patch.object(api.frappe, "response", reponse), \
            patch.object(api.frappe, "has_permission", has_permission), \
            patch.object(api.frappe, "throw", _throw), \
            patch.object(api.frappe, "get_list", lectures.get_list), \
            patch.object(api.frappe, "db", lectures.db), \
            patch.object(api.frappe, "log_error", lectures.log_error), \
            patch.object(api.frappe.utils, "now_datetime", lambda: MAINTENANT):
        try:
            yield reponse, lectures
        finally:
            if flags_absents:
                del frappe.local.flags


CHEVAUCHANT = _rdv(name="TACHE-CHEVAUCHE", custom_type_dintervention="Réparation",
                   custom_employé="Akram",
                   starts_on=datetime(2026, 9, 3, 9, 30),
                   ends_on=datetime(2026, 9, 3, 10, 30))
PLUS_TARD = _rdv(name="TACHE-APRES", custom_type_dintervention="Livraison",
                 custom_employé="Jamel Bouzid",
                 starts_on=datetime(2026, 9, 3, 14, 0),
                 ends_on=datetime(2026, 9, 3, 15, 0))
PASSE = _rdv(name="TACHE-HIER", starts_on=datetime(2026, 9, 1, 9, 0),
             ends_on=datetime(2026, 9, 1, 9, 30))


class TestDroitDeLecture(unittest.TestCase):
    """La méthode est whitelistée : elle accepte n'importe quel n° de client
    venu du navigateur. Sans contrôle, elle devient un annuaire des rendez-vous
    de tous les clients pour n'importe quel compte connecté."""

    def test_sans_droit_sur_les_taches_rien_n_est_lu_ni_rendu(self):
        with _frappe_simule(rdvs=[CHEVAUCHANT], droit=False) as (reponse, lectures):
            with self.assertRaises(frappe.PermissionError):
                api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
            self.assertEqual(reponse["result"], [[], []])
            lectures.get_list.assert_not_called()
            lectures.db.get_value.assert_not_called()

    def test_sans_droit_sur_la_tache_ouverte_rien_n_est_lu_ni_rendu(self):
        with _frappe_simule(rdvs=[CHEVAUCHANT],
                            droit_sur_la_tache=False) as (reponse, lectures):
            with self.assertRaises(frappe.PermissionError):
                api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
            self.assertEqual(reponse["result"], [[], []])
            lectures.get_list.assert_not_called()
            lectures.db.get_value.assert_not_called()

    def test_un_refus_de_droit_n_est_pas_avale_par_le_filet(self):
        """Le try/except qui protège l'ouverture de la fiche ne doit pas
        transformer un refus en bandeau vide et silencieux."""
        with _frappe_simule(droit=False) as (_reponse, lectures):
            with self.assertRaises(frappe.PermissionError):
                api.get_customer_booking_info("CLI-1")
            lectures.log_error.assert_not_called()

    def test_la_lecture_respecte_les_permissions_de_l_appelant(self):
        """`get_list` et non `get_all` : le bandeau n'annonce que les rendez-vous
        que cet utilisateur voit déjà dans son calendrier."""
        with _frappe_simule(rdvs=[PLUS_TARD]) as (_reponse, lectures):
            api.get_customer_booking_info("CLI-1")
        self.assertEqual(lectures.get_list.call_args.args[0], "Tache de travail")


class TestLesDeuxBandeaux(unittest.TestCase):
    def test_la_structure_est_toujours_deux_listes(self):
        with _frappe_simule() as (reponse, _lectures):
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        self.assertEqual(reponse["result"], [[], []])

    def test_sans_client_aucune_lecture(self):
        with _frappe_simule(rdvs=[PLUS_TARD]) as (reponse, lectures):
            api.get_customer_booking_info(None, "TACHE-COURANTE")
        self.assertEqual(reponse["result"], [[], []])
        lectures.get_list.assert_not_called()

    def test_seuls_les_rendez_vous_ouverts_du_client_sont_demandes(self):
        with _frappe_simule() as (_reponse, lectures):
            api.get_customer_booking_info("CLI-1")
        filtres = lectures.get_list.call_args.kwargs["filters"]
        self.assertEqual(filtres["custom_client"], "CLI-1")
        self.assertEqual(filtres["status"], ["not in", ["Completed", "Cancelled"]])

    def test_la_tache_ouverte_ne_se_signale_pas_elle_meme(self):
        courant = _rdv(name="TACHE-COURANTE", starts_on=TACHE_COURANTE["starts_on"],
                       ends_on=TACHE_COURANTE["ends_on"])
        with _frappe_simule(rdvs=[courant], tache=TACHE_COURANTE) as (reponse, _l):
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        self.assertEqual(reponse["result"], [[], []])

    def test_un_rendez_vous_a_venir_et_disjoint_va_au_bandeau_jaune(self):
        with _frappe_simule(rdvs=[PLUS_TARD], tache=TACHE_COURANTE) as (reponse, _l):
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        ouverts, conflits = reponse["result"]
        self.assertEqual(ouverts,
                         ["Livraison avec Jamel Bouzid le 03/09/2026 14:00 à 15:00"])
        self.assertEqual(conflits, [])

    def test_un_rendez_vous_qui_chevauche_va_au_bandeau_rouge(self):
        with _frappe_simule(rdvs=[CHEVAUCHANT], tache=TACHE_COURANTE) as (reponse, _l):
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        ouverts, conflits = reponse["result"]
        self.assertEqual(conflits,
                         ["Réparation avec Akram le 03/09/2026 09:30 à 10:30"])
        self.assertEqual(ouverts, [])

    def test_un_rendez_vous_passe_n_encombre_aucun_bandeau(self):
        with _frappe_simule(rdvs=[PASSE], tache=TACHE_COURANTE) as (reponse, _l):
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        self.assertEqual(reponse["result"], [[], []])

    def test_les_trois_cas_ensemble(self):
        with _frappe_simule(rdvs=[CHEVAUCHANT, PLUS_TARD, PASSE],
                            tache=TACHE_COURANTE) as (reponse, _l):
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        ouverts, conflits = reponse["result"]
        self.assertEqual(len(conflits), 1)
        self.assertEqual(len(ouverts), 1)

    def test_sans_tache_ouverte_personne_n_est_en_conflit(self):
        """Fiche encore non enregistrée : pas de créneau de référence, donc
        aucun conflit possible — mais les rendez-vous à venir sont annoncés."""
        with _frappe_simule(rdvs=[CHEVAUCHANT, PLUS_TARD]) as (reponse, lectures):
            api.get_customer_booking_info("CLI-1")
        ouverts, conflits = reponse["result"]
        self.assertEqual(conflits, [])
        self.assertEqual(len(ouverts), 2)
        lectures.db.get_value.assert_not_called()


class TestRobustesse(unittest.TestCase):
    def test_un_rendez_vous_incomplet_ne_fait_pas_tomber_la_fiche(self):
        """Le bug d'origine : un rendez-vous sans employé, sans type et sans
        heure de fin."""
        bancal = _rdv(name="TACHE-BANCALE", custom_type_dintervention=None,
                      custom_employé=None, starts_on=datetime(2026, 9, 3, 16, 0),
                      ends_on=None)
        with _frappe_simule(rdvs=[bancal], tache=TACHE_COURANTE) as (reponse, _l):
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        self.assertEqual(reponse["result"][0], ["Rendez-vous le 03/09/2026 16:00"])

    def test_une_tache_courante_introuvable_ne_leve_pas(self):
        with _frappe_simule(rdvs=[PLUS_TARD], tache=None) as (reponse, _l):
            api.get_customer_booking_info("CLI-1", "TACHE-DISPARUE")
        self.assertEqual(len(reponse["result"][0]), 1)

    def test_une_lecture_en_erreur_laisse_la_fiche_s_ouvrir(self):
        with _frappe_simule() as (reponse, lectures):
            lectures.get_list.side_effect = RuntimeError("base indisponible")
            api.get_customer_booking_info("CLI-1", "TACHE-COURANTE")
        self.assertEqual(reponse["result"], [[], []])
        lectures.log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
