"""Listes d'appels (revue 03/10/2026) : regroupement par client, règle des 160 jours, répartition, tri des secteurs — règles pures."""
from __future__ import annotations

import datetime
import unittest

from customization_app import relances_config as RC
from customization_app.Maintenance import creation_liste_appelle as L

CFG = dict(RC.DEFAUTS, **{k + "_liste": RC.liste(v) for k, v in RC.DEFAUTS.items() if isinstance(v, str) and "\n" in v})
D = datetime.date


class TestRegroupement(unittest.TestCase):
    def test_un_client_une_entree_et_regle_160_jours(self):
        lignes = [{"customer": "C1", "schedule": "MS1", "item_code": "A", "scheduled_date": D(2026, 8, 1), "secteurs": "Secteur 2"},
                  {"customer": "C1", "schedule": "MS2", "item_code": "B", "scheduled_date": D(2026, 6, 1), "secteurs": "Secteur 2"},
                  {"customer": "C2", "schedule": "MS3", "item_code": "A", "scheduled_date": D(2026, 7, 1), "secteurs": "Hors Secteur"},
                  {"customer": "C3", "schedule": "MS4", "item_code": "A", "scheduled_date": D(2026, 7, 1), "secteurs": "Secteur 1"}]
        dernieres = {("MS3", "A"): D(2026, 9, 1),      # intervention il y a 1 mois → C2 écarté
                     ("MS4", "A"): D(2026, 1, 1)}      # il y a 9 mois → C3 gardé
        r = L.regrouper_par_client(lignes, dernieres, CFG, D(2026, 10, 3))
        self.assertEqual(set(r), {"C1", "C3"})
        self.assertEqual(set(r["C1"]["echeanciers"]), {"MS1", "MS2"})         # 2 échéanciers, UNE entrée
        self.assertEqual(r["C1"]["premiere"], D(2026, 6, 1))

    def test_tri_des_secteurs_numerique(self):
        self.assertEqual(sorted(["Secteur 10", "Secteur 2", "Zone partenaire - X", "Secteur 1"], key=L.cle_secteur),
                         ["Secteur 1", "Secteur 2", "Secteur 10", "Zone partenaire - X"])


class TestRepartition(unittest.TestCase):
    def _clients(self):
        base = {"echeanciers": {"MS": {"A": D(2026, 6, 1)}}, "premiere": D(2026, 6, 1), "nb_appels": 0, "dernier_appel": None}
        return {"tunis": dict(base, secteurs="Secteur 2"), "deux_adresses": dict(base, secteurs="Secteur 7, Secteur 2"),
                "sousse": dict(base, secteurs="Hors Secteur"), "gafsa": dict(base, secteurs="Hors Secteur"),
                "partenaire": dict(base, secteurs="Secteur 1"), "rdv": dict(base, secteurs="Secteur 1"),
                "recent": dict(base, secteurs="Secteur 1", dernier_appel=D(2026, 9, 20))}

    def test_classes(self):
        zone = lambda c: {"employe": "HR-EMP-00007", "nom": "EAS"} if c == "sousse" else None
        normal, z, urg, ecartes, hors = L.repartir(self._clients(), D(2026, 10, 3), CFG, exclus={"partenaire"}, zones={"x": 1},
                                                   zone_du_client=zone, rdv_clients={"rdv"}, secteur_urgence=["Secteur 7"], urgence_active=True,
                                                   motifs={"partenaire": "partenaire"})
        self.assertEqual(set(normal), {"tunis"})
        self.assertEqual(set(urg), {"deux_adresses"})                          # « Secteur 7, Secteur 2 » → urgence (plus la chaîne entière)
        self.assertEqual(set(z), {"sousse"}) ; self.assertTrue(z["sousse"]["secteurs"].startswith("Zone partenaire - EAS"))
        # 25/11/2026 : un compteur par motif (tous présents, à 0 s'ils ne servent pas).
        self.assertEqual(ecartes, {"partenaire": 1, "perdu": 0, "a_requalifier": 0, "non_interesse": 0, "reporte": 0,
                                   "rdv": 1, "recent": 1, "hors_secteur": 1})
        self.assertEqual(set(hors), {"gafsa"})                                  # 15/10 : plus écarté, liste « Hors secteur » en dernier

    def test_urgence_detectee_mais_non_creee_garde_les_clients(self):
        normal, _z, urg, _e, _h = L.repartir(self._clients(), D(2026, 10, 3), CFG, exclus=set(), zones={}, zone_du_client=lambda c: None,
                                             rdv_clients=set(), secteur_urgence=["Secteur 7"], urgence_active=False)
        self.assertIn("deux_adresses", normal) ; self.assertEqual(urg, {})

    def test_resume(self):
        self.assertIn("Normal : 3 client(s) (+5 en attente)", L.resume_resultats({"normal_urgence": {"normal": {"created": True, "count_clients": 3, "en_attente": 5}}}))
        self.assertIn("Aucune liste", L.resume_resultats({}))


class TestBrouillonAbandonne(unittest.TestCase):
    """15/10/2026 : un brouillon jamais touché depuis plus de 7 jours est remplacé ; un brouillon entamé ou récent reste."""

    def test_regle(self):
        from customization_app.Maintenance.creation_liste_appelle import brouillon_abandonne as b
        self.assertTrue(b("2026-10-05 07:01:07", "2026-10-05 07:01:07", 0, "2026-10-15"))      # la liste Zone du 05/10
        self.assertFalse(b("2026-10-14 07:00:32", "2026-10-14 07:00:32", 0, "2026-10-15"))     # trop récent
        self.assertFalse(b("2026-10-05 07:01:07", "2026-10-05 07:01:07", 3, "2026-10-15"))     # des lignes appelées
        self.assertFalse(b("2026-10-05 07:01:07", "2026-10-06 09:00:00", 0, "2026-10-15"))     # quelqu'un l'a ouverte et enregistrée
        self.assertTrue(b("2026-10-08 07:00:00", "2026-10-08 07:00:00", 0, "2026-10-15"))      # exactement 7 jours
        self.assertFalse(b("2026-10-09 07:00:00", "2026-10-09 07:00:00", 0, "2026-10-15"))


class TestLot25Novembre(unittest.TestCase):
    """25/11/2026 : plafond Hors secteur, motifs d'exclusion réels, jours ouvrés des brouillons, compteurs du passage."""

    def test_plafond_hors_secteur_propre(self):
        self.assertEqual(L.plafond_du_type(L.TYPE_HORS_SECTEUR, CFG), 30)                          # clé absente du réglage : 30
        self.assertEqual(L.plafond_du_type(L.TYPE_HORS_SECTEUR, dict(CFG, plafond_hors_secteur=12)), 12)
        for t in (L.TYPE_NORMAL, L.TYPE_ZONE, L.TYPE_URGENCE, L.TYPE_REQUALIFICATION):
            self.assertEqual(L.plafond_du_type(t, dict(CFG, plafond_liste=100, plafond_hors_secteur=30)), 100)

    def test_plafond_hors_secteur_a_zero_reprend_plafond_liste(self):
        """26/11/2026 : un champ Int ajouté au Single déjà enregistré vaut 0 au premier enregistrement du réglage. Avec
        « 0 = sans plafond », la liste Hors secteur prenait d'un coup les 183 candidats (pire que les 100 d'avant)."""
        for vide in (0, "0", "", None, -5):
            self.assertEqual(L.plafond_du_type(L.TYPE_HORS_SECTEUR, dict(CFG, plafond_liste=100, plafond_hors_secteur=vide)), 100, vide)
        self.assertEqual(L.plafond_du_type(L.TYPE_HORS_SECTEUR, dict(CFG, plafond_liste=0, plafond_hors_secteur=0)), 0)   # tout sans plafond

    def test_urgence_validee_avec_des_lignes_non_appelees_ne_couvre_plus(self):
        """26/11/2026 : une Urgence validée en laissant des lignes ne couvre plus le passage du technicien : une Urgence
        est recréée, au lieu d'envoyer ces clients dans la Normal (après le passage)."""
        listes = [{"name": "U-brouillon", "docstatus": 0}, {"name": "U-toute-appelee", "docstatus": 1},
                  {"name": "U-partielle", "docstatus": 1}, {"name": "U-brouillon-entame", "docstatus": 0}]
        garde = [l["name"] for l in L.listes_qui_couvrent(listes, {"U-partielle": 3, "U-brouillon-entame": 4})]
        self.assertEqual(garde, ["U-brouillon", "U-toute-appelee", "U-brouillon-entame"])
        self.assertEqual(len(L.listes_qui_couvrent(listes, {})), 4)              # l'existant : toute liste validée est appelée

    def test_bloques_derriere_et_dans_le_brouillon(self):
        """26/11/2026 : 183 candidats Hors secteur, dont 54 encore dans LIST-06-11-2026-00004 → 129 derrière."""
        candidats = {"C%d" % i for i in range(183)}
        brouillon = {"C%d" % i for i in range(54)} | {"X1", "X2"}            # X : appelés, plus candidats
        self.assertEqual(L.separer_bloques(candidats, brouillon), (129, 54))
        self.assertEqual(L.separer_bloques(candidats, set()), (183, 0))
        self.assertEqual(L.separer_bloques(set(), brouillon), (0, 0))

    def test_motif_exclusion_dans_l_ordre(self):
        jour = D(2026, 11, 25)
        m = lambda **c: L.motif_exclusion(dict({"name": "C"}, **c), geres={"P"}, today=jour)
        self.assertEqual(L.motif_exclusion({"name": "P", "custom_statut_relance": "Perdu"}, {"P"}, jour), "partenaire")
        self.assertEqual(m(custom_statut_relance="Perdu", custom_intéressé_par_le_service_entretien="Non"), "perdu")
        self.assertEqual(m(custom_statut_relance="À requalifier", custom_intéressé_par_le_service_entretien="Non"), "a_requalifier")
        self.assertEqual(m(custom_intéressé_par_le_service_entretien="Non"), "non_interesse")
        self.assertEqual(m(custom_relance_reportee_au=D(2026, 12, 1)), "reporte")
        self.assertEqual(m(custom_relance_reportee_au=D(2026, 11, 25)), "autre")         # reprise atteinte : plus reporté
        self.assertEqual(L.motif_exclusion({"name": "C", "custom_intéressé_par_le_service_entretien": "Non"}, set(), jour, relancer_non=True), "autre")

    def test_le_journal_ne_range_plus_tout_dans_partenaire(self):
        """Le 25/11 : 99 exclus comptés « partenaire », aucun du partenaire."""
        base = {"echeanciers": {"MS": {"A": D(2026, 6, 1)}}, "premiere": D(2026, 6, 1), "nb_appels": 0, "dernier_appel": None, "secteurs": "Secteur 2"}
        clients = {n: dict(base) for n in ("requal", "non", "reporte", "perdu", "inconnu", "tunis")}
        motifs = {"requal": "a_requalifier", "non": "non_interesse", "reporte": "reporte", "perdu": "perdu"}
        normal, _z, _u, ecartes, _h = L.repartir(clients, D(2026, 11, 25), CFG, exclus=set(clients) - {"tunis"}, zones={}, zone_du_client=lambda c: None,
                                                 rdv_clients=set(), secteur_urgence=[], urgence_active=False, motifs=motifs)
        self.assertEqual(set(normal), {"tunis"})
        self.assertEqual(ecartes["partenaire"], 0)
        self.assertEqual((ecartes["a_requalifier"], ecartes["non_interesse"], ecartes["reporte"], ecartes["perdu"], ecartes["autre"]), (1, 1, 1, 1, 1))

    def test_regroupement_compte_les_clients_ecartes_par_l_intervention(self):
        lignes = [{"customer": "C1", "schedule": "MS1", "item_code": "A", "scheduled_date": D(2026, 8, 1), "secteurs": "Secteur 2"},
                  {"customer": "C2", "schedule": "MS2", "item_code": "A", "scheduled_date": D(2026, 7, 1), "secteurs": "Secteur 2"},
                  {"customer": "C2", "schedule": "MS2", "item_code": "B", "scheduled_date": D(2026, 7, 1), "secteurs": "Secteur 2"},
                  {"customer": "C3", "schedule": "MS3", "item_code": "A", "scheduled_date": D(2026, 7, 1), "secteurs": "Secteur 2"}]
        dernieres = {("MS2", "A"): D(2026, 10, 1), ("MS2", "B"): D(2026, 10, 1),     # C2 entièrement écarté
                     ("MS3", "A"): D(2026, 10, 1)}                                   # C3 aussi
        stats = {}
        r = L.regrouper_par_client(lignes, dernieres, CFG, D(2026, 11, 25), stats)
        self.assertEqual(set(r), {"C1"})
        self.assertEqual(stats, {"visites": 4, "clients": 3, "intervention_recente": 2, "a_repartir": 1})

    def test_jours_ouvres(self):
        j = L.jours_ouvres_ecoules
        self.assertEqual(j("2026-11-11 15:37:37", "2026-11-25"), 11)      # LIST-06-11-2026-00004 : du 12 au 24, sans les 2 dimanches
        self.assertEqual(j("2026-11-20 15:00:00", "2026-11-24"), 2)       # vendredi → mardi : samedi, lundi
        self.assertEqual(j("2026-11-20 15:00:00", "2026-11-25"), 3)       # → mercredi : l'alerte part
        self.assertEqual(j("2026-11-21", "2026-11-23"), 0)                # samedi → lundi : le dimanche ne compte pas
        self.assertEqual(j("2026-11-25 06:00:00", "2026-11-25"), 0)
        self.assertEqual(j(None, "2026-11-25"), 0)

    def test_message_d_alerte(self):
        b = {"name": "LIST-06-11-2026-00004", "type_liste": "Hors secteur", "titre": "Hors secteur 2026-11-06 (+120 en attente)",
             "modified": "2026-11-11 15:37:37", "modified_by": "salma@x", "jours_ouvres": 11, "appeles": 45, "total": 100}
        sujet, corps = L.message_alerte_brouillon(b, "Salma <test>", "https://site/app/liste-appelle-entretien/LIST-06-11-2026-00004", 129)
        self.assertIn("LIST-06-11-2026-00004", sujet) ; self.assertIn("11 jour(s) ouvré(s)", sujet)
        for morceau in ("<b>45</b> ligne(s) appelée(s) sur <b>100</b>", "11/11/2026 à 15:37", "Salma &lt;test&gt;", "129 client(s) attendent",
                        "aucune nouvelle liste « Hors secteur »", 'href="https://site/app/liste-appelle-entretien/LIST-06-11-2026-00004"'):
            self.assertIn(morceau, corps)
        self.assertNotIn("attendent", L.message_alerte_brouillon(b, "S", "u", None)[1])

    def test_compteurs_du_passage(self):
        results = {"brouillons_supprimes": ["LIST-X"],
                   "second_call": {"created": False, "skipped": True, "reason": "draft_relance_exists"},
                   "normal_urgence": {"entonnoir": {"visites": 600, "clients": 412, "intervention_recente": 7, "a_repartir": 405},
                                      "ecartes": {"partenaire": 0, "a_requalifier": 63, "non_interesse": 20, "reporte": 10, "perdu": 6,
                                                  "rdv": 5, "recent": 120, "hors_secteur": 183},
                                      "repartition": {"normal": 4, "zone_partenaire": 1, "urgence": 0, "hors_secteur": 183},
                                      "normal": {"created": True, "count_clients": 4, "en_attente": 0},
                                      "hors_secteur": None, "bloques": {"Hors secteur": 129, "Zone partenaire": 1},
                                      "deja_en_brouillon": {"Hors secteur": 54, "Zone partenaire": 0}, "rappels": 0},
                   "brouillons_bloquants": {"immobiles": 1, "alertes": 1, "plus_vieux_jours": 11}}
        c = L.compteurs_du_passage(results)
        self.assertEqual(c["exclus_a_requalifier"], 63) ; self.assertEqual(c["exclus_partenaire"], 0) ; self.assertEqual(c["exclus_autre"], 0)
        self.assertEqual(c["ecartes_appel_recent"], 120) ; self.assertEqual(c["ecartes_rendez_vous"], 5)
        self.assertEqual(c["liste_normal"], 4) ; self.assertEqual(c["liste_hors_secteur"], 0)
        self.assertEqual(c["attente_hors_secteur"], 129) ; self.assertEqual(c["attente_zone_partenaire"], 1)
        self.assertEqual((c["en_brouillon_hors_secteur"], c["en_brouillon_zone_partenaire"], c["en_brouillon_normal"]), (54, 0, 0))
        self.assertEqual(c["vers_hors_secteur"], 183) ; self.assertEqual(c["relance_bloquee"], 1)
        self.assertEqual((c["brouillons_purges"], c["brouillons_immobiles"], c["brouillons_alertes"], c["brouillon_plus_vieux_jours"]), (1, 1, 1, 11))
        self.assertTrue(all(isinstance(v, int) for v in c.values()))
        self.assertEqual(L.compteurs_du_passage({})["visites_candidates"], 0)
