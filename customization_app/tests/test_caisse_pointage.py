"""Le pointage des chèques et des traites au comptage (27/11/2026) — fonctions pures de caisse_pointage."""
import unittest

from customization_app import caisse_pointage as PP
from customization_app import caisse_tiroir as CT

#: Ce que le collecteur principal a en main le 27/11/2026 d'après la page « Où est l'argent liquide ».
CHEQUES = [798.0, 3000.0, 3000.0, 562.0, 654.0]
TRAITES = [934.9, 1136.531, 1319.483]


class TestComparer(unittest.TestCase):
    def test_tout_y_est_quel_que_soit_l_ordre(self):
        self.assertEqual(PP.comparer(CHEQUES, [654, 3000, 562, 3000, 798]), {"manquantes": [], "en_trop": []})

    def test_un_meme_montant_deux_fois_se_declare_deux_fois(self):
        """Deux chèques BIOAQUA de 3 000 DT : en déclarer un seul en laisse un manquant."""
        self.assertEqual(PP.comparer(CHEQUES, [798, 3000, 562, 654]), {"manquantes": [3000.0], "en_trop": []})

    def test_une_piece_manquante_et_une_inconnue(self):
        self.assertEqual(PP.comparer(TRAITES, [934.9, 1136.531, 500]), {"manquantes": [1319.483], "en_trop": [500.0]})

    def test_au_millime(self):
        self.assertEqual(PP.comparer([1136.531], [1136.53]), {"manquantes": [1136.531], "en_trop": [1136.53]})
        self.assertEqual(PP.comparer([], []), {"manquantes": [], "en_trop": []})


class TestPointer(unittest.TestCase):
    def test_le_collecteur_pointe_tout(self):
        p = PP.pointer({"Chèque": CHEQUES, "Traite": TRAITES}, {"Chèque": list(CHEQUES), "Traite": list(TRAITES)})
        self.assertEqual(p["ecarts"], 0)
        self.assertEqual(PP.resume(p), "")
        self.assertEqual((p["Chèque"]["nb_declare"], p["Traite"]["nb_declare"]), (5, 3))

    def test_il_ne_declare_rien(self):
        """Cases laissées vides : tout ce qui est attendu manque, et ça se voit."""
        p = PP.pointer({"Chèque": CHEQUES, "Traite": TRAITES}, {"Chèque": [], "Traite": []})
        self.assertEqual(p["ecarts"], 8)
        self.assertIn("il manque 5 chèques", PP.resume(p))
        self.assertIn("il manque 3 traites", PP.resume(p))

    def test_resume_lisible(self):
        p = PP.pointer({"Chèque": CHEQUES, "Traite": TRAITES}, {"Chèque": list(CHEQUES), "Traite": [934.9, 1136.531, 500]})
        self.assertEqual(p["ecarts"], 2)
        self.assertEqual(PP.resume(p), "il manque 1 traite (1 319,483) ; 1 traite en trop (500,000)")

    def test_un_employe_declare_un_cheque_qu_il_n_a_pas_encaisse(self):
        p = PP.pointer({"Chèque": [], "Traite": []}, {"Chèque": [455.0], "Traite": []})
        self.assertEqual(PP.resume(p), "1 chèque en trop (455,000)")

    def test_ancienne_saisie_un_nombre_sans_montants(self):
        p = PP.pointer({"Chèque": [654.0, 562.0], "Traite": []}, {"Chèque": None, "Traite": []}, {"Chèque": 1, "Traite": 0})
        self.assertEqual(p["ecarts"], 1)
        self.assertEqual(PP.resume(p), "2 chèques attendu(s), 1 déclaré(s)")
        juste = PP.pointer({"Chèque": [654.0, 562.0], "Traite": []}, {"Chèque": None, "Traite": []}, {"Chèque": 2})
        self.assertEqual((juste["ecarts"], PP.resume(juste)), (0, ""))

    def test_totaux_et_aller_retour(self):
        self.assertEqual(PP.totaux({"Chèque": CHEQUES, "Traite": TRAITES}),
                         {"Chèque": {"nb": 5, "total": 8014.0}, "Traite": {"nb": 3, "total": 3390.914}})
        p = PP.pointer({"Chèque": CHEQUES, "Traite": TRAITES}, {"Chèque": [798.0], "Traite": []})
        self.assertEqual(PP.lire(PP.en_texte(p)), p)
        self.assertIsNone(PP.lire(""))
        self.assertIsNone(PP.lire("pas du json"))


class TestVerdict(unittest.TestCase):
    BASE = dict(jour="2026-11-27", nom="Nejib", tiroir={"attendu": 500.0},
                comptage={"quand": "2026-11-27 08:04:46", "compte": 500, "attendu": 500, "ecart": 0, "par_nom": "Nejib"},
                verification={"quand": "2026-11-20 10:00:00"}, coffre={"solde": 0.0, "inventaire": None},
                pieces={"anciennes": 0}, employes=[], a_regulariser=0)

    def test_jamais_pointe(self):
        v = CT.verdict(**self.BASE, pointage=None)
        self.assertEqual(v["etat"], "a_faire")
        self.assertIn("jamais été pointés", v["points"][0]["texte"])

    def test_pointage_juste(self):
        self.assertEqual(CT.verdict(**self.BASE, pointage={"quand": "2026-11-27 08:04:46", "ecarts": 0, "resume": ""})["etat"], "ok")

    def test_pointage_en_ecart(self):
        v = CT.verdict(**self.BASE, pointage={"quand": "2026-11-27 08:04:46", "ecarts": 1, "resume": "il manque 1 traite (1 319,483)"})
        self.assertEqual(v["etat"], "alerte")
        self.assertIn("il manque 1 traite (1 319,483)", v["points"][0]["texte"])

    def test_site_sans_pointage(self):
        """Avant la migration : pas de point sur un pointage qui n'existe pas encore."""
        self.assertEqual(CT.verdict(**self.BASE)["etat"], "ok")


if __name__ == "__main__":
    unittest.main()
