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
    def heures(self, entree, demi, duree, secteur="Secteur 1"):
        return [x.strftime("%H:%M") for x in PL.heures_possibles(entree, JOUR, demi, secteur, duree, CONFIG)]

    def test_matin_vide_un_entretien_toutes_les_demi_heures_jusqu_a_midi(self):
        self.assertEqual(self.heures(None, "matin", 30), ["09:30", "10:00", "10:30", "11:00", "11:30", "12:00"])

    def test_une_installation_doit_finir_dans_la_fenetre(self):
        self.assertEqual(self.heures(None, "matin", 75)[-1], "11:00")       # 11:00 + 75 min = 12:15 ; 11:30 déborde

    def test_trente_minutes_de_battement_autour_d_une_intervention(self):
        entree = journee(matin=[(h(10, 30), h(11, 0))], secteurs_matin=["Secteur 1"])
        # avant : finir à 10:00 au plus tard → 09:30 seulement ; après : pas avant 11:30
        self.assertEqual(self.heures(entree, "matin", 30), ["09:30", "11:30", "12:00"])

    def test_la_premiere_heure_proposee_est_celle_du_creneau_automatique(self):
        entree = journee(matin=[(h(9, 30), h(10, 0))], secteurs_matin=["Secteur 1"])
        auto = PL._premier_creneau(entree["matin"], JOUR, "matin", 30)
        self.assertEqual(self.heures(entree, "matin", 30)[0], auto.strftime("%H:%M"))

    def test_un_autre_secteur_ferme_la_demi_journee(self):
        entree = journee(matin=[(h(9, 30), h(10, 0))], secteurs_matin=["Secteur 5"])
        self.assertEqual(self.heures(entree, "matin", 30), [])
        self.assertTrue(self.heures(entree, "apres_midi", 30))

    def test_journee_entiere_prise_aucune_heure(self):
        self.assertEqual(self.heures(journee(jour_entier=True), "matin", 30), [])


class TestCoutDUneHeureFixe(AvecFenetres):
    """Une heure fixe ne doit pas fermer la place d'un autre rendez-vous sans qu'on le sache."""

    def couts(self, entree, demi, duree=30):
        heures = PL.heures_possibles(entree, JOUR, demi, "Secteur 1", duree, CONFIG)
        return {x.strftime("%H:%M"): p for x, p in PL.cout_des_heures(entree, JOUR, demi, duree, heures).items()}

    def test_une_demi_journee_vide_contient_3_entretiens_le_matin_et_5_l_apres_midi(self):
        self.assertEqual(PL._places_restantes([], JOUR, "matin"), 3)
        self.assertEqual(PL._places_restantes([], JOUR, "apres_midi"), 5)

    def test_le_matin_aucune_heure_ne_coute_de_creneau(self):
        self.assertEqual(set(self.couts(None, "matin").values()), {0})

    def test_l_apres_midi_les_heures_piles_coutent_un_creneau(self):
        c = self.couts(None, "apres_midi")
        self.assertEqual([h for h, p in c.items() if p], ["14:00", "15:00", "16:00", "17:00"])
        self.assertEqual((c["13:30"], c["14:30"], c["17:30"]), (0, 0, 0))

    def test_quand_il_ne_reste_qu_un_trou_aucune_heure_n_est_penalisee(self):
        entree = journee(apres_midi=[(h(13, 30), h(16, 30))], secteurs_apres_midi=["Secteur 1"])
        self.assertEqual(self.couts(entree, "apres_midi"), {"17:00": 0, "17:30": 0})


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
