"""Options du magasin dans l'app de rendez-vous (19/11/2026) : technicien imposé, heure promise.

`unittest.TestCase` pur, aucune base : les fenêtres horaires sont fournies au moteur, les jours fériés acceptés.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest import mock

from customization_app import portail_rdv_planning as PL

JOUR = date(2026, 12, 3)            # un jeudi
DIMANCHE = date(2026, 12, 6)
FENETRES = {"matin": ("09:30:00", "12:30:00"), "apres_midi": ("13:30:00", "18:15:00")}
CONFIG = {"accepter_feries": 1}     # pas de lecture de la liste des jours fériés


def h(heure, minute=0, jour=JOUR):
    return datetime(jour.year, jour.month, jour.day, heure, minute)


def journee(matin=(), apres_midi=(), secteurs_matin=(), secteurs_apres_midi=(), jour_entier=False):
    return {"matin": list(matin), "apres_midi": list(apres_midi),
            "secteurs": {"matin": set(secteurs_matin), "apres_midi": set(secteurs_apres_midi)},
            "gouvernorats": set(), "jour_entier": jour_entier}


class AvecFenetres(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(PL, "fenetres", return_value=FENETRES)
        p.start()
        self.addCleanup(p.stop)


class TestHeuresPossibles(AvecFenetres):
    """Les heures fixes proposées : celles qui ne font perdre aucun créneau — 30 min après la dernière intervention,
    puis de « durée + 30 min » en « durée + 30 min » selon le type."""

    def heures(self, entree, demi, duree, secteur="Secteur 1"):
        return [x.strftime("%H:%M") for x in PL.heures_possibles(entree, JOUR, demi, secteur, duree, CONFIG)]

    def test_entretiens_d_une_demi_journee_vide(self):
        self.assertEqual(self.heures(None, "matin", 30), ["09:30", "10:30", "11:30"])
        self.assertEqual(self.heures(None, "apres_midi", 30), ["13:30", "14:30", "15:30", "16:30", "17:30"])

    def test_installations_selon_leur_duree(self):
        # 75 min + 30 de battement : de 1 h 45 en 1 h 45
        self.assertEqual(self.heures(None, "matin", 75), ["09:30", "11:15"])
        self.assertEqual(self.heures(None, "apres_midi", 75), ["13:30", "15:15", "17:00"])

    def test_on_repart_30_minutes_apres_la_derniere_intervention(self):
        entree = journee(apres_midi=[(h(13, 30), h(14, 45))], secteurs_apres_midi=["Secteur 1"])   # une installation
        self.assertEqual(self.heures(entree, "apres_midi", 30), ["15:15", "16:15", "17:15"])
        self.assertEqual(self.heures(entree, "apres_midi", 75), ["15:15", "17:00"])

    def test_avant_une_intervention_il_faut_la_duree_et_le_battement(self):
        entree = journee(apres_midi=[(h(15, 0), h(15, 30))], secteurs_apres_midi=["Secteur 1"])
        # avant 15:00 : un seul entretien tient (13:30 ; 14:30 finirait à 15:00 sans battement) ; après : 16:00, 17:00
        self.assertEqual(self.heures(entree, "apres_midi", 30), ["13:30", "16:00", "17:00"])

    def test_un_seul_trou_en_fin_de_journee(self):
        entree = journee(apres_midi=[(h(13, 30), h(16, 30))], secteurs_apres_midi=["Secteur 1"])
        self.assertEqual(self.heures(entree, "apres_midi", 30), ["17:00"])

    def test_la_premiere_heure_proposee_est_celle_du_creneau_automatique(self):
        entree = journee(matin=[(h(9, 30), h(10, 0))], secteurs_matin=["Secteur 1"])
        auto = PL._premier_creneau(entree["matin"], JOUR, "matin", 30)
        self.assertEqual(self.heures(entree, "matin", 30)[0], auto.strftime("%H:%M"))

    def test_fixer_une_heure_proposee_ne_change_pas_le_nombre_de_rendez_vous_possibles(self):
        """Quelle que soit l'heure fixée parmi celles proposées, la demi-journée contient toujours 5 entretiens."""
        for choisie in PL.heures_possibles(None, JOUR, "apres_midi", "Secteur 1", 30, CONFIG):
            entree = journee(apres_midi=[(choisie, choisie.replace(minute=choisie.minute) + PL.datetime.timedelta(minutes=30))],
                             secteurs_apres_midi=["Secteur 1"])
            self.assertEqual(1 + len(self.heures(entree, "apres_midi", 30)), 5, choisie)

    def test_un_autre_secteur_ferme_la_demi_journee(self):
        entree = journee(matin=[(h(9, 30), h(10, 0))], secteurs_matin=["Secteur 5"])
        self.assertEqual(self.heures(entree, "matin", 30), [])
        self.assertTrue(self.heures(entree, "apres_midi", 30))

    def test_journee_entiere_prise_aucune_heure(self):
        self.assertEqual(self.heures(journee(jour_entier=True), "matin", 30), [])


class TestTechnicienImpose(AvecFenetres):
    LISTE = ["A", "B", "C"]

    def pool(self, taches=None, jour=JOUR, conges=frozenset(), employe=None, config=None):
        return PL._pool_du_jour(config or dict(CONFIG, nb_employes=1), self.LISTE, taches or {}, jour, conges,
                                employe=employe)

    def test_sans_choix_le_plafond_ne_garde_que_le_premier(self):
        self.assertEqual(self.pool(), ["A"])

    def test_le_technicien_impose_passe_devant_l_ordre_et_le_plafond(self):
        # A est déjà sorti ce jour-là : avec un plafond à 1, l'app ne proposerait que lui.
        taches = {("A", JOUR): journee(matin=[(h(9, 30), h(10, 0))], secteurs_matin=["Secteur 1"])}
        self.assertEqual(self.pool(taches), ["A"])
        self.assertEqual(self.pool(taches, employe="C"), ["C"])

    def test_en_conge_il_n_est_pas_propose(self):
        self.assertEqual(self.pool(conges={("C", JOUR)}, employe="C"), [])

    def test_journee_pleine_ou_prise_il_n_est_pas_propose(self):
        self.assertEqual(self.pool({("C", JOUR): journee(jour_entier=True)}, employe="C"), [])

    def test_dimanche_ferme_meme_pour_lui(self):
        self.assertEqual(self.pool(jour=DIMANCHE, employe="C"), [])

    def test_dimanche_ouvert_seulement_pour_l_employe_du_dimanche(self):
        config = dict(CONFIG, autoriser_dimanche=1, employe_dimanche="C")
        self.assertEqual(self.pool(jour=DIMANCHE, employe="C", config=config), ["C"])
        self.assertEqual(self.pool(jour=DIMANCHE, employe="B", config=config), [])

    def test_zone_partenaire_le_dimanche_suit_son_reglage(self):
        ctx = {"employes": ["P"], "delai_jours": 3, "dimanche": False}
        self.assertEqual(PL._pool_du_jour(CONFIG, ["P"], {}, DIMANCHE, contexte=ctx, employe="P"), [])
        self.assertEqual(PL._pool_du_jour(CONFIG, ["P"], {}, DIMANCHE, contexte=dict(ctx, dimanche=True),
                                          employe="P"), ["P"])


class TestEmployesFixables(unittest.TestCase):
    def test_en_zone_partenaire_seul_le_partenaire(self):
        self.assertEqual(PL.employes_fixables({}, {"employes": ["P"], "delai_jours": 3, "dimanche": False}), ["P"])

    def test_sinon_la_liste_du_reglage(self):
        with mock.patch.object(PL, "_liste_employes", return_value=["A", "B"]):
            self.assertEqual(PL.employes_fixables({}), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
