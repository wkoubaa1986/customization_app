"""Tests du choix de l'employé par le portail de rendez-vous.

Convention : `unittest.TestCase` pur, aucune base — on n'éprouve ici que la
RÈGLE D'AFFECTATION, celle qui décide qui prend le rendez-vous.

Contexte (02/09/2026) : l'utilisateur constatait plus d'employés servis que le
nombre réglé, et une charge très inégale — le 14/09, Akram avait 7 rendez-vous
quand Sadok en avait 2. Deux mécanismes distincts l'expliquaient : le
remplacement d'un employé plein par le suivant de la liste, et le fait que le
moteur EMPILE sur le premier éligible au lieu de répartir. Le second est
désormais un réglage.
"""
from __future__ import annotations

import unittest
from datetime import datetime

from customization_app import portail_rdv_planning as PL


def _creneau(h_debut, minutes):
    debut = datetime(2026, 9, 14, h_debut, 0)
    return (debut, debut.replace(hour=h_debut + minutes // 60,
                                 minute=minutes % 60))


class TestChargeDemiJournee(unittest.TestCase):
    """La charge se compte par DEMI-JOURNÉE : c'est l'unité que le client
    réserve, et un technicien chargé le matin peut être le plus libre
    l'après-midi."""

    def test_sans_rien_la_charge_est_nulle(self):
        self.assertEqual(PL._charge_demi(None, "matin"), 0)
        self.assertEqual(PL._charge_demi({}, "matin"), 0)

    def test_un_creneau_compte_ses_minutes(self):
        entree = {"matin": [_creneau(9, 30)]}
        self.assertEqual(PL._charge_demi(entree, "matin"), 30)

    def test_les_creneaux_s_additionnent(self):
        entree = {"matin": [_creneau(9, 30), _creneau(10, 75)]}
        self.assertEqual(PL._charge_demi(entree, "matin"), 105)

    def test_l_autre_demi_journee_n_est_pas_comptee(self):
        entree = {"matin": [_creneau(9, 30)], "apres_midi": [_creneau(14, 120)]}
        self.assertEqual(PL._charge_demi(entree, "matin"), 30)
        self.assertEqual(PL._charge_demi(entree, "apres_midi"), 120)


class TestReglageRepartition(unittest.TestCase):
    """Le mode vit dans Config Portail RDV : c'est un RÉGLAGE, pas un choix
    figé dans le code (demande de l'utilisateur)."""

    def test_par_defaut_on_garde_la_priorite_de_la_liste(self):
        """Réglage absent = comportement historique. Un correctif qui
        redistribuerait la charge de tout le monde sans qu'on l'ait demandé
        serait une mauvaise surprise."""
        self.assertFalse(PL._repartir({}))
        self.assertFalse(PL._repartir(None))

    def test_le_libelle_du_reglage_declenche_la_repartition(self):
        self.assertTrue(PL._repartir({"mode_affectation": PL.MODE_REPARTITION}))

    def test_un_libelle_inconnu_ne_repartit_pas(self):
        """Si quelqu'un renomme l'option, on retombe sur l'ancien
        comportement — jamais sur un comportement indéterminé."""
        self.assertFalse(PL._repartir({"mode_affectation": "Autre chose"}))

    def test_la_priorite_reste_le_libelle_de_l_autre_option(self):
        self.assertFalse(PL._repartir(
            {"mode_affectation": "Priorité à l'ordre de la liste"}))


class TestOrdreDeChoix(unittest.TestCase):
    """Ce que produit le tri, à partir des charges — le cœur de la demande."""

    def _ordonner(self, charges, demi="matin"):
        taches = {(e, "J"): {demi: [_creneau(9, m)] if m else []}
                  for e, m in charges.items()}
        pool = list(charges)
        return sorted(pool, key=lambda e: PL._charge_demi(taches.get((e, "J")), demi))

    def test_le_moins_charge_passe_devant(self):
        self.assertEqual(self._ordonner({"A": 150, "B": 0, "C": 75}),
                         ["B", "C", "A"])

    def test_a_charge_egale_l_ordre_de_la_liste_est_conserve(self):
        """Le tri est STABLE : à égalité, la priorité historique reprend la
        main. On ne perd pas l'ordre de la liste, on ne l'applique qu'ensuite."""
        self.assertEqual(self._ordonner({"A": 60, "B": 60, "C": 60}),
                         ["A", "B", "C"])

    def test_un_employe_vide_passe_devant_un_employe_charge(self):
        """Cas réel du 07/09/2026 : la priorité donnait le rendez-vous à Sadok
        (75 min déjà posées), la répartition le donne à Jamel (0)."""
        self.assertEqual(self._ordonner({"Sadok": 75, "Jamel": 0})[0], "Jamel")


class TestPlafondDuJour(unittest.TestCase):
    """`nb_employes` est un PLAFOND de personnes mobilisées, pas « les N
    premiers qui ont de la place » (décision utilisateur 02/09/2026).

    La nuance : un employé DÉJÀ engagé ce jour-là occupe une place même s'il
    est plein. Sans elle, il était sauté et le suivant de la liste le
    remplaçait — quatre techniciens sortis pour un réglage à trois.
    """

    JOUR = datetime(2026, 11, 2).date()          # un lundi

    def _pool(self, config, liste, taches):
        return PL._pool_du_jour(config, liste, taches, self.JOUR)

    def _occupe(self, plein=False):
        """Une journée entamée — pleine ou non."""
        return {"matin": [], "apres_midi": [],
                "secteurs": {"matin": set(), "apres_midi": set()},
                "jour_entier": plein}

    def test_sans_plafond_toute_la_liste_est_mobilisable(self):
        liste = ["A", "B", "C", "D"]
        self.assertEqual(len(self._pool({}, liste, {})), 4)

    def test_le_plafond_limite_le_nombre_de_personnes(self):
        liste = ["A", "B", "C", "D"]
        self.assertEqual(self._pool({"nb_employes": 3}, liste, {}), ["A", "B", "C"])

    def test_un_mobilise_PLEIN_garde_sa_place(self):
        """Le cœur de la demande : B est sorti et saturé — il ne prend plus de
        rendez-vous, mais il ne libère pas sa place pour autant."""
        taches = {("A", self.JOUR): self._occupe(),
                  ("B", self.JOUR): self._occupe(plein=True),
                  ("C", self.JOUR): self._occupe()}
        pool = self._pool({"nb_employes": 3}, ["A", "B", "C", "D"], taches)
        self.assertNotIn("D", pool)          # D n'est PAS appelé en renfort
        self.assertNotIn("B", pool)          # B est plein : il ne prend rien
        self.assertEqual(pool, ["A", "C"])

    def test_tous_les_mobilises_pleins_ferment_la_journee(self):
        """Conséquence assumée : plus aucun créneau ce jour-là, et le client se
        voit proposer le lendemain."""
        taches = {e: self._occupe(plein=True)
                  for e in [("A", self.JOUR), ("B", self.JOUR), ("C", self.JOUR)]}
        self.assertEqual(self._pool({"nb_employes": 3}, ["A", "B", "C", "D"], taches), [])

    def test_tant_que_le_plafond_n_est_pas_atteint_on_complete(self):
        """Deux mobilisés seulement : le troisième peut encore être appelé."""
        taches = {("A", self.JOUR): self._occupe(),
                  ("B", self.JOUR): self._occupe()}
        self.assertEqual(self._pool({"nb_employes": 3}, ["A", "B", "C", "D"], taches),
                         ["A", "B", "C"])

    def test_un_conge_ne_consomme_pas_une_place(self):
        """Il n'est pas sorti : sa place revient à quelqu'un d'autre."""
        pool = PL._pool_du_jour({"nb_employes": 3}, ["A", "B", "C", "D"], {},
                                self.JOUR, conges={("A", self.JOUR)})
        self.assertEqual(pool, ["B", "C", "D"])


class TestZonePartenaire(unittest.TestCase):
    """Zone partenaire (demandes 03/10/2026) : délai en jours OUVRÉS, un gouvernorat par journée."""

    def test_jours_ouvres(self):
        import datetime
        P = PL
        self.assertEqual(P.ajouter_jours_ouvres(datetime.date(2026, 10, 1), 3), datetime.date(2026, 10, 6))   # jeudi → mardi
        self.assertEqual(P.ajouter_jours_ouvres(datetime.date(2026, 10, 2), 3), datetime.date(2026, 10, 7))   # vendredi → mercredi
        self.assertEqual(P.ajouter_jours_ouvres(datetime.date(2026, 10, 3), 3), datetime.date(2026, 10, 7))   # samedi → mercredi
        self.assertEqual(P.ajouter_jours_ouvres(datetime.date(2026, 10, 5), 1), datetime.date(2026, 10, 6))   # lundi → mardi
        self.assertEqual(P.ajouter_jours_ouvres(datetime.date(2026, 10, 5), 0), datetime.date(2026, 10, 5))

    def test_un_gouvernorat_par_journee(self):
        import datetime
        P = PL
        jour = datetime.date(2026, 10, 6)
        vide = None
        sousse = {"matin": [], "apres_midi": [], "secteurs": {"matin": set(), "apres_midi": set()},
                  "gouvernorats": {"Sousse"}, "jour_entier": False}
        self.assertIsNotNone(P._demi_faisable(vide, jour, "matin", "Hors Secteur", 30, {}, "Monastir"))      # journée libre
        self.assertIsNone(P._demi_faisable(sousse, jour, "apres_midi", "Hors Secteur", 30, {}, "Monastir"))  # déjà Sousse
        self.assertIsNotNone(P._demi_faisable(sousse, jour, "apres_midi", "Hors Secteur", 30, {}, "Sousse"))
        self.assertIsNotNone(P._demi_faisable(sousse, jour, "apres_midi", "Hors Secteur", 30, {}, "sousse "))  # tolérant
        self.assertIsNotNone(P._demi_faisable(sousse, jour, "apres_midi", "Secteur 1", 30, {}))               # hors zone : règle inactive


class TestSecteursVoisins(unittest.TestCase):
    """Demande 08/10/2026 : demain et après-demain, une demi-journée qui a encore de la place accepte un secteur VOISIN
    de ceux où le technicien va déjà ; plus loin, la règle « un secteur par demi-journée » reste."""

    VOISINS = {"Secteur 1": {"Secteur 2"}, "Secteur 2": {"Secteur 1", "Secteur 5"}, "Secteur 5": {"Secteur 2"}}

    def setUp(self):
        import frappe
        frappe.local.rdv_voisins = {k: set(v) for k, v in self.VOISINS.items()}    # le cache, sans OSRM

    def tearDown(self):
        import frappe
        frappe.local.rdv_voisins = None

    def test_voisins_depuis_les_temps_de_route(self):
        noms = ["Secteur 1", "Secteur 2", "Secteur 3", "Secteur 8"]
        minutes = [[0, 21, 31, 68],
                   [20, 0, 19, 58],
                   [31, 26, 0, 63],          # 3 → 2 : 26 min, au-dessus du seuil dans CE sens
                   [68, 58, 62, 0]]
        v = PL.voisins_depuis_matrice(noms, minutes, 25)
        self.assertEqual(v, {"Secteur 1": {"Secteur 2"}, "Secteur 2": {"Secteur 1"}, "Secteur 3": set()})   # 8 : jamais
        self.assertEqual(PL.libeller_voisins(v).splitlines()[0], "Secteur 1 : Secteur 2")

    def _faisable(self, secteurs_deja, secteur, decalage, config):
        import datetime
        from frappe.utils import add_days, getdate
        jour = add_days(getdate(), decalage)
        debut = datetime.datetime.combine(jour, datetime.time(9, 30))
        entree = {"matin": [(debut, debut + datetime.timedelta(minutes=30))], "apres_midi": [],
                  "secteurs": {"matin": set(secteurs_deja), "apres_midi": set()}, "gouvernorats": set(), "jour_entier": False}
        return PL._demi_faisable(entree, jour, "matin", secteur, 30, config)

    def test_jours_proches_seulement(self):
        actif = {"voisins_actif": 1}
        self.assertIsNotNone(self._faisable({"Secteur 2"}, "Secteur 1", 1, actif))     # demain : voisin accepté
        self.assertIsNotNone(self._faisable({"Secteur 2"}, "Secteur 1", 2, actif))     # après-demain aussi
        self.assertIsNone(self._faisable({"Secteur 2"}, "Secteur 1", 3, actif))        # J+3 : règle stricte
        self.assertIsNotNone(self._faisable({"Secteur 2"}, "Secteur 1", 3, dict(actif, voisins_jours=3)))   # réglable
        self.assertIsNone(self._faisable({"Secteur 2"}, "Secteur 1", 1, {}))           # case décochée : rien ne change

    def test_chaque_secteur_present_doit_etre_voisin(self):
        actif = {"voisins_actif": 1}
        self.assertIsNone(self._faisable({"Secteur 2", "Secteur 5"}, "Secteur 1", 1, actif))   # 5 n'est pas voisin de 1
        self.assertIsNotNone(self._faisable({"Secteur 1", "Secteur 5"}, "Secteur 2", 1, actif))
        self.assertIsNone(self._faisable({"Secteur 2"}, "Secteur 9", 1, actif))         # lointain : jamais en voisin

    def test_ajustements_a_la_main(self):
        """08/10/2026 : 6 touche 1, 2 et 5 sur la carte (35 min entre centres : le calcul ne les voit pas) ; 7 touche 3 et 4."""
        auto = {"Secteur 1": {"Secteur 2"}, "Secteur 2": {"Secteur 1"}, "Secteur 5": {"Secteur 6"}, "Secteur 6": {"Secteur 5"}}
        ajouts = PL.paires_secteurs("Secteur 6, Secteur 2\n6, 1\nsecteur 7 ; Secteur 3\n7, 4\nSecteur 8, Secteur 9\n")
        v = PL.ajuster_voisins(auto, ajouts, PL.paires_secteurs("Secteur 2, Secteur 1"))
        self.assertEqual(v["Secteur 6"], {"Secteur 1", "Secteur 2", "Secteur 5"})
        self.assertEqual(v["Secteur 7"], {"Secteur 3", "Secteur 4"})
        self.assertNotIn("Secteur 2", v["Secteur 1"])                               # retrait : l'emporte
        self.assertNotIn("Secteur 8", v)                                           # journée entière : jamais ajouté
        self.assertEqual(PL._cle_voisins({"voisins_ajouts": "7, 3"}), PL._cle_voisins({"voisins_ajouts": "Secteur 3, Secteur 7"}))

    def test_secteur_7_voisin_de_3_sur_les_jours_proches(self):
        import frappe
        frappe.local.rdv_voisins = {"Secteur 3": {"Secteur 7"}, "Secteur 7": {"Secteur 3"}}
        actif = {"voisins_actif": 1}
        self.assertIsNotNone(self._faisable({"Secteur 3"}, "Secteur 7", 1, actif))     # demain : 7 rejoint une demi-journée en 3
        self.assertIsNotNone(self._faisable({"Secteur 7"}, "Secteur 3", 2, actif))     # et inversement
        self.assertIsNone(self._faisable({"Secteur 3"}, "Secteur 7", 3, actif))        # J+3 : règle stricte
