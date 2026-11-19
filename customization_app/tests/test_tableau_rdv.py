"""Rendez-vous pris par canal (19/11/2026) — règles et agrégation, sans la base."""
import unittest

from customization_app import tableau_rdv as T


def rdv(date, auteur="salma", app=0, sujet="", type_i="Entretien", annule=False):
    return {"date": date, "auteur": auteur, "app": app, "sujet": sujet, "type": type_i, "annule": annule}


class TestCanal(unittest.TestCase):
    def test_sans_la_case_de_l_app_c_est_le_calendrier(self):
        self.assertEqual(T.canal(0, "salma", ""), "manuel")
        self.assertEqual(T.canal(None, "Guest", None), "manuel")

    def test_app_par_un_utilisateur(self):
        self.assertEqual(T.canal(1, "salma", "RDV magasin (Salma) — Entretien (matin)"), "app_magasin")

    def test_app_par_le_client(self):
        self.assertEqual(T.canal(1, "Guest", "RDV portail — Entretien (matin)"), "app_client")

    def test_un_utilisateur_connecte_qui_reserve_par_le_portail_public_compte_comme_client(self):
        self.assertEqual(T.canal(1, "wassim", "RDV portail — Entretien (matin)"), "app_client")

    def test_un_sujet_reecrit_par_le_magasin_ne_change_pas_le_canal(self):
        self.assertEqual(T.canal(1, "jamel", "Entretien FIAT"), "app_magasin")
        self.assertEqual(T.canal("1", "Guest", "Entretien FIAT"), "app_client")


class TestPeriodes(unittest.TestCase):
    def test_pas_automatique(self):
        self.assertEqual(T.pas_auto("2026-11-01", "2026-11-30"), "jour")
        self.assertEqual(T.pas_auto("2026-09-01", "2026-11-30"), "semaine")
        self.assertEqual(T.pas_auto("2026-01-01", "2026-11-30"), "mois")

    def test_les_jours_vides_sont_presents(self):
        p = T.periodes("2026-11-17", "2026-11-19", "jour")
        self.assertEqual([x["cle"] for x in p], ["2026-11-17", "2026-11-18", "2026-11-19"])
        self.assertEqual(p[0]["libelle"], "17/11")

    def test_la_semaine_commence_le_lundi_et_ne_deborde_pas(self):
        # 19/11/2026 est un jeudi ; sa semaine commence le lundi 16.
        p = T.periodes("2026-11-12", "2026-11-19", "semaine")
        self.assertEqual([x["cle"] for x in p], ["2026-11-09", "2026-11-16"])
        self.assertEqual((p[0]["du"], p[0]["au"]), ("2026-11-12", "2026-11-15"))
        self.assertEqual((p[1]["du"], p[1]["au"]), ("2026-11-16", "2026-11-19"))

    def test_les_mois_passent_l_annee(self):
        p = T.periodes("2026-12-15", "2027-02-03", "mois")
        self.assertEqual([x["cle"] for x in p], ["2026-12-01", "2027-01-01", "2027-02-01"])
        self.assertEqual([x["libelle"] for x in p], ["12/2026", "01/2027", "02/2027"])
        self.assertEqual((p[0]["du"], p[2]["au"]), ("2026-12-15", "2027-02-03"))


class TestAgreger(unittest.TestCase):
    def setUp(self):
        self.lignes = [
            rdv("2026-11-17", "jamel"),
            rdv("2026-11-17", "salma", 1, "RDV magasin (Salma) — Entretien (matin)"),
            rdv("2026-11-18", "salma", 1, "RDV magasin (Salma) — Installation (matin)", "Installation", annule=True),
            rdv("2026-11-18", "Guest", 1, "RDV portail — Entretien (matin)"),
            rdv("2026-11-19", "salma"),
        ]
        self.r = T.agreger(self.lignes, "2026-11-17", "2026-11-19", "jour")

    def test_chaque_rendez_vous_dans_un_seul_canal(self):
        t = self.r["totaux"]
        self.assertEqual((t["manuel"], t["app_magasin"], t["app_client"], t["total"]), (2, 2, 1, 5))
        self.assertEqual(t["total"], sum(t[c] for c in T.CLES))

    def test_par_periode(self):
        self.assertEqual([(p["manuel"], p["app_magasin"], p["app_client"]) for p in self.r["periodes"]],
                         [(1, 1, 0), (0, 1, 1), (1, 0, 0)])

    def test_le_client_n_est_pas_un_utilisateur(self):
        self.assertNotIn("Guest", self.r["utilisateurs"])
        salma = self.r["utilisateurs"]["salma"]
        self.assertEqual((salma["manuel"], salma["app_magasin"], salma["total"]), (1, 2, 3))
        self.assertEqual(self.r["utilisateurs"]["jamel"]["total"], 1)

    def test_par_type(self):
        self.assertEqual(self.r["types"]["Entretien"]["total"], 4)
        self.assertEqual(self.r["types"]["Installation"]["app_magasin"], 1)

    def test_les_annulations_sont_comptees_a_part(self):
        self.assertEqual(self.r["annules"], {"manuel": 0, "app_magasin": 1, "app_client": 0})
        self.assertEqual(self.r["totaux"]["annules"], 1)
        self.assertEqual(self.r["totaux"]["total"], 5)      # un annulé reste un rendez-vous PRIS

    def test_une_date_hors_periode_est_ignoree(self):
        r = T.agreger(self.lignes + [rdv("2026-11-25")], "2026-11-17", "2026-11-19", "jour")
        self.assertEqual(r["totaux"]["total"], 5)

    def test_regroupement_par_semaine(self):
        r = T.agreger(self.lignes + [rdv("2026-11-12")], "2026-11-12", "2026-11-19", "semaine")
        self.assertEqual([p["total"] for p in r["periodes"]], [1, 5])


if __name__ == "__main__":
    unittest.main()
