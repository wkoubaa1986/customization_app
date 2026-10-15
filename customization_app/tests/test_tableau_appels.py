"""Tableau des appels (13/10/2026) — lecture de l'historique des listes et agrégation, sans la base."""
import json
import unittest

from customization_app import tableau_appels as T


def version(owner, creation, lignes, liste="LIST-1"):
    """Une Version de Liste Appelle Entretien : `lignes` = [(nom de ligne, [[champ, avant, après], …])]."""
    return {"owner": owner, "creation": creation, "docname": liste,
            "data": json.dumps({"changed": [["nb_appels_restant", "46", 45]],
                                "row_changed": [["clients", i, nom, champs] for i, (nom, champs) in enumerate(lignes)]})}


class TestLignesTraitees(unittest.TestCase):
    def test_une_ligne_appelee_compte(self):
        v = version("salma", "2026-10-13 11:34:58", [("l1", [["a_été_appelé", 0, 1], ["resume_appel", "", "Fait lui-même"]])])
        self.assertEqual(T.lignes_traitees([v]), [("salma", "2026-10-13", "l1", "LIST-1")])

    def test_les_compteurs_de_la_liste_ne_comptent_pas(self):
        """`changed` (nb_appels_restant…) n'est pas un appel ; seules les lignes de la table clients comptent."""
        v = {"owner": "salma", "creation": "2026-10-13 10:00:00",
             "data": json.dumps({"changed": [["nb_appels_restant", "46", 45]], "row_changed": []})}
        self.assertEqual(T.lignes_traitees([v]), [])

    def test_decocher_ou_vider_ne_compte_pas(self):
        v = version("salma", "2026-10-13 10:00:00", [("l1", [["a_été_appelé", 1, 0], ["resume_appel", "Fait lui-même", ""]])])
        self.assertEqual(T.lignes_traitees([v]), [])

    def test_un_champ_hors_appel_ne_compte_pas(self):
        v = version("salma", "2026-10-13 10:00:00", [("l1", [["adresse", "", "Sfax"]])])
        self.assertEqual(T.lignes_traitees([v]), [])

    def test_plusieurs_lignes_dans_une_version(self):
        v = version("eco", "2026-09-28 09:00:00", [("a", [["resume_appel", "", "Ne répond pas 1er appel"]]),
                                                    ("b", [["resume_appel", "", "Ne répond pas 1er appel"]])])
        self.assertEqual(len(T.lignes_traitees([v])), 2)

    def test_json_illisible_ignore(self):
        self.assertEqual(T.lignes_traitees([{"owner": "x", "creation": "2026-10-13", "data": "{pas du json"}]), [])


class TestPriorites(unittest.TestCase):
    def test_ordre_decide_le_13_10_revu_le_14_10(self):
        """14/10 : le Rattrapage passe AVANT le 2e appel, la Requalification AVANT la Zone partenaire, et la relance
        paiements (1×/semaine) ferme la marche."""
        self.assertEqual(T.CLES_PRIORITES, ("urgence", "commandes", "normal", "rattrapage", "relance_liste", "requalif",
                                            "zone", "relances", "ras", "hors_secteur"))    # 15/10 : RAS puis Hors secteur ferment la marche

    def test_type_de_liste(self):
        self.assertEqual(T.priorite_de_liste("Urgence"), "urgence")
        self.assertEqual(T.priorite_de_liste("Relance"), "relance_liste")
        self.assertEqual(T.priorite_de_liste("Zone partenaire"), "zone")
        self.assertEqual(T.priorite_de_liste(None), "normal")
        self.assertEqual(T.priorite_de_liste("Inconnu"), "normal")

    def test_prochaine_priorite_saute_les_files_vides(self):
        self.assertEqual(T.prochaine_priorite({"urgence": 0, "commandes": 3, "normal": 40}), "commandes")
        self.assertEqual(T.prochaine_priorite({"urgence": 2, "commandes": 3}), "urgence")
        self.assertEqual(T.prochaine_priorite({"rattrapage": None, "zone": 100}), "zone")
        self.assertIsNone(T.prochaine_priorite({}))

    def test_chaque_priorite_sait_ou_s_ouvrir(self):
        self.assertEqual(set(T.OUVERTURE), set(T.CLES_PRIORITES))
        self.assertEqual(T.OUVERTURE["urgence"][1], {"docstatus": 0, "type_liste": "Urgence"})
        self.assertEqual(T.OUVERTURE["relances"][0], ["relance-paiements"])


class TestTypeRelance(unittest.TestCase):
    def test_paiement_quel_que_soit_le_canal(self):
        """Téléphone, SMS ou e-mail : la relance de la semaine est faite."""
        self.assertEqual(T.type_relance("[RELANCE-PAIEMENT] | Téléphone | promet de passer vendredi"), "relances")
        self.assertEqual(T.type_relance("[RELANCE-PAIEMENT] | SMS | Envoyé au 55162716"), "relances")
        self.assertEqual(T.type_relance("<p>[RELANCE-PAIEMENT] | Email | Envoyé à a@b.tn</p>"), "relances")

    def test_retenue_hors_files(self):
        self.assertEqual(T.type_relance("[RELANCE-RAS] | Téléphone | ok"), "relance_ras")
        self.assertNotIn("relance_ras", T.CLES_PRIORITES)

    def test_commentaire_ordinaire(self):
        self.assertIsNone(T.type_relance("Client rappelé, il passe demain"))


class TestAgreger(unittest.TestCase):
    def test_meme_client_meme_jour_compte_une_fois(self):
        """Un client sur plusieurs lignes (un échéancier chacune) : le même appel, répété."""
        ev = [("normal", "salma", "2026-10-13", "C1"), ("normal", "salma", "2026-10-13", "C1"),
              ("normal", "salma", "2026-10-13", "C2")]
        self.assertEqual(T.agreger(ev, [])[("salma", "2026-10-13")]["normal"], 2)

    def test_sources_et_total(self):
        ev = [("urgence", "salma", "2026-10-13", "C1"), ("commandes", "salma", "2026-10-13", "SO-1"),
              ("rattrapage", "salma", "2026-10-13", "C1"), ("zone", "salma", "2026-10-13", "C3")]
        case = T.agreger(ev, [])[("salma", "2026-10-13")]
        self.assertEqual((case["urgence"], case["commandes"], case["rattrapage"], case["total"]), (1, 1, 1, 4))
        # « listes » = ce qui vient des listes d'appels seulement (base du taux de rendez-vous)
        self.assertEqual(case["listes"], 2)

    def test_par_jour_et_par_utilisateur(self):
        ev = [("normal", "salma", "2026-10-12", "C1"), ("normal", "salma", "2026-10-13", "C1"),
              ("normal", "eco", "2026-10-13", "C1")]
        self.assertEqual(len(T.agreger(ev, [])), 3)

    def test_rendez_vous(self):
        rdv = [("salma", "2026-10-13", "T1", "normal"), ("salma", "2026-10-13", "T2", None),
               ("salma", "2026-10-13", "T2", None), ("salma", "2026-10-13", "T3", "rattrapage")]
        case = T.agreger([], rdv)[("salma", "2026-10-13")]
        self.assertEqual((case["rdv"], case["rdv_liste"], case["total"]), (3, 1, 0))
        self.assertEqual((case["rdv_normal"], case["rdv_rattrapage"], case["rdv_hors"], case["rdv_urgence"]), (1, 1, 1, 0))


def rdv(tache, client="C1", par="salma", jour="2026-10-13", commande="", depuis_liste=False, type_liste=None):
    return {"par": par, "jour": jour, "tache": tache, "client": client, "commande": commande,
            "depuis_liste": depuis_liste, "type_liste": type_liste}


class TestOriginesRdv(unittest.TestCase):
    def test_cree_depuis_la_ligne_de_liste(self):
        self.assertEqual(T.origines_rdv([rdv("T1", depuis_liste=True, type_liste="Requalification")], []),
                         [("salma", "2026-10-13", "T1", "requalif")])

    def test_client_appele_le_meme_jour_par_la_meme_personne(self):
        """RDV posé depuis le calendrier juste après l'appel (Ikbal Saâdallah, 07/10) : rangé dans la file de l'appel."""
        ev = [("rattrapage", "salma", "2026-10-13", "C1"), ("relance_liste", "salma", "2026-10-13", "C1")]
        self.assertEqual(T.origines_rdv([rdv("T1")], ev)[0][3], "rattrapage")

    def test_un_autre_jour_ou_une_autre_personne_ne_compte_pas(self):
        ev = [("normal", "salma", "2026-10-12", "C1"), ("normal", "eco", "2026-10-13", "C1"),
              ("client", "salma", "2026-10-13", "C1")]
        self.assertIsNone(T.origines_rdv([rdv("T1")], ev)[0][3])

    def test_commande_rattachee(self):
        self.assertEqual(T.origines_rdv([rdv("T1", commande="SAL-ORD-1")], [])[0][3], "commandes")

    def test_la_ligne_de_liste_passe_avant_tout(self):
        ev = [("rattrapage", "salma", "2026-10-13", "C1")]
        out = T.origines_rdv([rdv("T1", commande="SAL-ORD-1", depuis_liste=True, type_liste="Normal")], ev)
        self.assertEqual(out[0][3], "normal")

    def test_rdv_liste_inclut_le_client_appele_dans_une_liste(self):
        ev = [("zone", "salma", "2026-10-13", "C1")]
        case = T.agreger(ev, T.origines_rdv([rdv("T1")], ev))[("salma", "2026-10-13")]
        self.assertEqual((case["rdv"], case["rdv_zone"], case["rdv_liste"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()


class TestJoursDesFiles(unittest.TestCase):
    """15/10/2026 : relance paiements le lundi, retenue à la source le mardi ; les files quotidiennes tous les jours."""

    def test_est_son_jour(self):
        self.assertTrue(T.est_son_jour("relances", "2026-10-19"))     # lundi
        self.assertFalse(T.est_son_jour("relances", "2026-10-20"))    # mardi
        self.assertTrue(T.est_son_jour("ras", "2026-10-20"))
        self.assertFalse(T.est_son_jour("ras", "2026-10-19"))
        self.assertTrue(T.est_son_jour("normal", "2026-10-20"))
        self.assertTrue(T.est_son_jour("hors_secteur", "2026-10-24"))
