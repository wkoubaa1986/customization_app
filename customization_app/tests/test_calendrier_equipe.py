"""Calendrier d'équipe : les fonctions pures de calendrier_equipe.py (forme des événements, colonnes, décalage)."""
import datetime
import unittest

from customization_app.calendrier_equipe import SANS_EMPLOYE, bornes, colonnes, decaler_jours, vers_calendrier


class TestBornes(unittest.TestCase):
    def test_une_tache_horaire_garde_ses_heures(self):
        self.assertEqual(bornes("2026-11-20 09:30:00", "2026-11-20 10:00:00", False),
                         ("2026-11-20 09:30:00", "2026-11-20 10:00:00"))

    def test_les_dates_de_la_base_sont_acceptees(self):
        d = datetime.datetime(2026, 11, 20, 9, 30)
        self.assertEqual(bornes(d, d + datetime.timedelta(minutes=45), False),
                         ("2026-11-20 09:30:00", "2026-11-20 10:15:00"))
        self.assertEqual(bornes("2026-11-20 09:30:00.000000", None, False)[0], "2026-11-20 09:30:00")

    def test_sans_fin_ou_fin_avant_le_debut_elle_dure_une_heure(self):
        self.assertEqual(bornes("2026-11-20 09:00:00", None, False)[1], "2026-11-20 10:00:00")
        self.assertEqual(bornes("2026-11-20 09:00:00", "2026-11-20 08:00:00", False)[1], "2026-11-20 10:00:00")

    def test_toute_la_journee_occupe_des_jours_entiers_fin_exclue(self):
        # 08:00 -> 17:00 le même jour : ce jour-là, jusqu'au lendemain exclu
        self.assertEqual(bornes("2026-11-16 08:00:00", "2026-11-16 17:00:00", True),
                         ("2026-11-16 00:00:00", "2026-11-17 00:00:00"))
        # du 16 au 18 inclus
        self.assertEqual(bornes("2026-11-16 08:00:00", "2026-11-18 17:00:00", True),
                         ("2026-11-16 00:00:00", "2026-11-19 00:00:00"))

    def test_un_jour_ferie_dont_la_fin_egale_le_debut_tient_sur_un_jour(self):
        self.assertEqual(bornes("2026-12-17 00:00:00", "2026-12-17 00:00:00", True),
                         ("2026-12-17 00:00:00", "2026-12-18 00:00:00"))

    def test_une_fin_a_minuit_du_lendemain_est_deja_exclue(self):
        self.assertEqual(bornes("2026-11-16 00:00:00", "2026-11-17 00:00:00", True),
                         ("2026-11-16 00:00:00", "2026-11-17 00:00:00"))

    def test_sans_debut_pas_d_evenement(self):
        self.assertEqual(bornes(None, "2026-11-16 10:00:00", False), (None, None))


class TestVersCalendrier(unittest.TestCase):
    TACHE = {"name": "Tache-1", "starts_on": "2026-11-20 09:30:00", "ends_on": "2026-11-20 10:00:00", "all_day": 0,
             "titre": "Secteur 6\nEntretien", "color": "#ED6396", "status": "Open",
             "custom_choix_du_staff": "HR-EMP-00013", "custom_employé": "Radhouan Sifi", "nom_client": "Carlo",
             "custom_reservation_app": 1, "custom_tournee_fixe": 1, "custom_employe_fixe": 0, "tel": ""}

    def test_une_tache_va_dans_la_colonne_de_son_employe(self):
        ev = vers_calendrier(self.TACHE)
        self.assertEqual(ev["id"], "Tache de travail|Tache-1")
        self.assertEqual((ev["genre"], ev["employes"], ev["journee"]), ("tache", ["HR-EMP-00013"], False))
        self.assertEqual((ev["debut"], ev["fin"]), ("2026-11-20 09:30:00", "2026-11-20 10:00:00"))
        self.assertEqual((ev["reservation_app"], ev["heure_fixe"], ev["employe_fixe"]), (1, 1, 0))
        self.assertEqual(ev["nom_client"], "Carlo")
        self.assertNotIn("tel", ev)                      # rubrique vide : pas dans l'info-bulle

    def test_le_client_s_ecrit_sur_la_tuile_nom_sinon_fiche_et_manque_sans_client(self):
        self.assertEqual(vers_calendrier(self.TACHE)["client"], "Carlo")
        self.assertEqual(vers_calendrier(dict(self.TACHE, nom_client="", custom_client="CUST-1"))["client"], "CUST-1")
        self.assertNotIn("client", vers_calendrier(dict(self.TACHE, nom_client=" ", custom_client=None)))

    def test_une_livraison_aramex_est_marquee(self):
        titre = "Secteur 2\n📦 Aramex 55087934495 · Imed\n🚐 Livraison: Client: Imed\nHedi"
        self.assertEqual(vers_calendrier(dict(self.TACHE, titre=titre))["aramex"], 1)
        self.assertNotIn("aramex", vers_calendrier(self.TACHE))

    def test_une_tache_sans_employe_va_dans_non_affecte(self):
        ev = vers_calendrier(dict(self.TACHE, custom_choix_du_staff=None))
        self.assertEqual(ev["employes"], [SANS_EMPLOYE])

    def test_une_reunion_apparait_chez_chacun_de_ses_participants(self):
        ev = vers_calendrier({"doctype": "Reunion Activite", "name": "REU-1", "starts_on": "2026-11-18 11:30:00",
                              "ends_on": "2026-11-18 12:00:00", "all_day": 0, "titre": "Point", "status": "Open"},
                             ["HR-EMP-00011", "HR-EMP-00002", None])
        self.assertEqual((ev["genre"], ev["employes"]), ("reunion", ["HR-EMP-00011", "HR-EMP-00002"]))
        self.assertEqual(ev["id"], "Reunion Activite|REU-1")

    def test_un_jour_ferie_donne_une_pastille_et_un_fond(self):
        pastille = vers_calendrier({"doctype": "Holiday", "name": "FERIE-2026-12-17", "starts_on": "2026-12-17 00:00:00",
                                    "ends_on": "2026-12-17 00:00:00", "all_day": 1, "titre": "Fête", "ferie": 1})
        fond = vers_calendrier({"doctype": "Holiday", "name": "FERIE-FOND-2026-12-17", "starts_on": "2026-12-17 00:00:00",
                                "ends_on": "2026-12-18 00:00:00", "all_day": 0, "titre": "", "ferie": 1, "fond": 1})
        self.assertEqual((pastille["genre"], pastille["journee"], pastille["fin"]), ("ferie", True, "2026-12-18 00:00:00"))
        self.assertEqual((fond["genre"], fond["journee"], fond["fin"]), ("fond", False, "2026-12-18 00:00:00"))
        self.assertEqual(pastille["employes"], [])        # la page l'étend à toutes les colonnes

    def test_un_evenement_sans_date_est_ecarte(self):
        self.assertIsNone(vers_calendrier({"name": "Tache-2", "starts_on": None}))


class TestColonnes(unittest.TestCase):
    ACTIFS = [{"id": "E1", "nom": "Hedi", "couleur": "#111111"}, {"id": "E2", "nom": "Sadok", "couleur": "#222222"}]

    def test_tous_les_actifs_meme_sans_tache(self):
        cols = colonnes(self.ACTIFS, [])
        self.assertEqual([(c["id"], c["actif"]) for c in cols], [("E1", 1), ("E2", 1)])

    def test_un_employe_parti_qui_garde_une_tache_a_sa_colonne(self):
        evs = [{"genre": "tache", "employes": ["E9"]}, {"genre": "tache", "employes": ["E1"]}]
        cols = colonnes(self.ACTIFS, evs, {"E9": "Mohamed Hedi"})
        self.assertEqual([(c["id"], c["nom"], c["actif"]) for c in cols][-1], ("E9", "Mohamed Hedi", 0))

    def test_non_affecte_seulement_s_il_y_en_a(self):
        self.assertNotIn(SANS_EMPLOYE, [c["id"] for c in colonnes(self.ACTIFS, [{"genre": "tache", "employes": ["E1"]}])])
        cols = colonnes(self.ACTIFS, [{"genre": "tache", "employes": [SANS_EMPLOYE]}])
        self.assertEqual(cols[-1]["id"], SANS_EMPLOYE)

    def test_les_participants_d_une_reunion_ne_creent_pas_de_colonne(self):
        cols = colonnes(self.ACTIFS, [{"genre": "reunion", "employes": ["E7"]}])
        self.assertEqual([c["id"] for c in cols], ["E1", "E2"])


class TestDecalerJours(unittest.TestCase):
    def test_memes_heures_autres_jours(self):
        d, f = decaler_jours("2026-11-16 08:00:00", "2026-11-16 17:00:00", 2)
        self.assertEqual((str(d), str(f)), ("2026-11-18 08:00:00", "2026-11-18 17:00:00"))
        d, f = decaler_jours("2026-11-16 08:00:00", "2026-11-17 17:00:00", -1)
        self.assertEqual((str(d), str(f)), ("2026-11-15 08:00:00", "2026-11-16 17:00:00"))


if __name__ == "__main__":
    unittest.main()
