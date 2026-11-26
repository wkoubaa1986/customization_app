"""La liste déroulante « Caisse » du rapport de caisse (26/11/2026) : ni comptes techniques, ni employés partis avant la
période affichée. Règle pure, sans base."""
import unittest

from customization_app.rapport_caisse_journaliere import noms_du_selecteur, selection_caisses

EMPLOYES = [
    {"employee_name": "Akram", "user_email": "akram@x", "status": "Left", "relieving_date": "2026-11-10"},
    {"employee_name": "Hedi ibidhii", "user_email": "hedi@x", "status": "Active", "relieving_date": None},
    {"employee_name": "Economic Aqua Solution", "user_email": "eco@x", "status": "Active", "relieving_date": None},
    {"employee_name": "Ancien sans date", "user_email": None, "status": "Left", "relieving_date": None},
    {"employee_name": "", "user_email": "vide@x", "status": "Active"},
]
USERS = [{"name": "hedi@x", "full_name": "Hedi ibidhii"}, {"name": "eco@x", "full_name": "Economiq Aqua Solutions"},
         {"name": "wassim@x", "full_name": "Wassim Koubaa"}, {"name": "assistant@x", "full_name": "Assistant ChatGPT"},
         {"name": "articles@x", "full_name": "Assistant Articles"}]
ROLES = {"hedi@x", "wassim@x"}


class TestSelecteur(unittest.TestCase):
    def test_aujourd_hui(self):
        self.assertEqual(noms_du_selecteur(EMPLOYES, USERS, ROLES, "2026-11-26"),
                         ["Ancien sans date", "Economic Aqua Solution", "Hedi ibidhii", "Wassim Koubaa"])

    def test_un_employe_parti_reste_consultable_avant_son_depart(self):
        self.assertIn("Akram", noms_du_selecteur(EMPLOYES, USERS, ROLES, "2026-11-10"))
        self.assertIn("Akram", noms_du_selecteur(EMPLOYES, USERS, ROLES, "2026-10-27"))
        self.assertNotIn("Akram", noms_du_selecteur(EMPLOYES, USERS, ROLES, "2026-11-11"))

    def test_sans_periode_tous_les_employes(self):
        # Réglages, argent liquide : la caisse d'un employé parti existe toujours dans l'historique.
        self.assertIn("Akram", noms_du_selecteur(EMPLOYES, USERS, ROLES))

    def test_les_comptes_techniques_n_ont_pas_de_caisse(self):
        for debut in (None, "2026-11-26"):
            noms = noms_du_selecteur(EMPLOYES, USERS, ROLES, debut)
            self.assertNotIn("Assistant ChatGPT", noms)
            self.assertNotIn("Assistant Articles", noms)
            self.assertIn("Wassim Koubaa", noms)

    def test_un_compte_porte_par_une_fiche_employe_n_entre_pas_deux_fois(self):
        noms = noms_du_selecteur(EMPLOYES, USERS, ROLES | {"eco@x"}, "2026-11-26")
        self.assertNotIn("Economiq Aqua Solutions", noms)
        self.assertEqual(noms.count("Hedi ibidhii"), 1)

    def test_un_compte_technique_n_a_pas_de_caisse_meme_avec_tous_les_roles(self):
        agent = {"name": "agent-ia@aquaworld.local", "full_name": "Agent IA"}
        self.assertNotIn("Agent IA", noms_du_selecteur(EMPLOYES, USERS + [agent], ROLES | {agent["name"]}, "2026-11-26"))

    def test_rien(self):
        self.assertEqual(noms_du_selecteur([], [], set()), [])
        self.assertEqual(noms_du_selecteur(None, None, None, "2026-11-26"), [])


class TestSelectionMultiple(unittest.TestCase):
    def test_plusieurs_une_ou_toutes(self):
        self.assertEqual(selection_caisses(None, '["Hedi ibidhii", "Jamel Aloui", " "]'), {"Hedi ibidhii", "Jamel Aloui"})
        self.assertEqual(selection_caisses("Sadok Bouziri", None), {"Sadok Bouziri"})
        self.assertEqual(selection_caisses("Sadok Bouziri", "[]"), {"Sadok Bouziri"})
        self.assertEqual(selection_caisses("", ""), set())
        self.assertEqual(selection_caisses(None, ["Jamel Aloui"]), {"Jamel Aloui"})


if __name__ == "__main__":
    unittest.main()
