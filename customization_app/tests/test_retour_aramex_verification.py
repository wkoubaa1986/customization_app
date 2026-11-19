"""Vérification de la photo d'un retour Aramex (19/11/2026) — règles pures, sans la base ni l'IA.

Le cas d'école est réel : le 18/11/2026, le retour de Nabil Inoubli (bordereau 55087538380, retour annoncé sous le
numéro 55087946126) a été constaté avec la photo de la pochette de Warghui Ridha (étiquette 55087851044,
« Shpr Ref » 55087711265).
"""
import unittest

from customization_app import retour_aramex as R

NABIL, RETOUR_NABIL = "55087538380", "55087946126"
ETIQUETTE_WARGHUI = {"lisible": True, "bordereau": "55087851044", "reference_expediteur": "55087711265",
                     "expediteur": "Warghui Ridha", "telephone": "21699290473", "autres_numeros": []}


class TestNumerosDeRetour(unittest.TestCase):
    def test_lu_dans_le_commentaire_d_un_evenement(self):
        suivi = {"evenements": [{"description": "Returned to Shipper", "commentaire": "Rtn HAWB 55087946126"},
                                {"description": "Out for Delivery", "commentaire": ""}]}
        self.assertEqual(R.numeros_de_retour(suivi), {RETOUR_NABIL})

    def test_lu_dans_la_derniere_description(self):
        self.assertEqual(R.numeros_de_retour(None, "Colis renvoyé à l'expéditeur (Rtn HAWB 55087946126)"), {RETOUR_NABIL})

    def test_aucun_retour_annonce(self):
        self.assertEqual(R.numeros_de_retour({"evenements": [{"description": "Delivered"}]}, "Colis livré"), set())


class TestNumerosLus(unittest.TestCase):
    def test_bordereau_et_reference_expediteur(self):
        self.assertEqual(R.numeros_lus(ETIQUETTE_WARGHUI), {"55087851044", "55087711265"})

    def test_un_telephone_n_est_pas_un_bordereau(self):
        lu = dict(ETIQUETTE_WARGHUI, autres_numeros=["21699290473", "21698511119", "2036"])
        self.assertEqual(R.numeros_lus(lu), {"55087851044", "55087711265"})

    def test_rien_de_lu(self):
        self.assertEqual(R.numeros_lus({"lisible": False}), set())
        self.assertEqual(R.numeros_lus(None), set())


class TestVerdict(unittest.TestCase):
    def test_le_cas_du_18_novembre_est_refuse(self):
        self.assertEqual(R.verdict_photo(ETIQUETTE_WARGHUI, NABIL, {RETOUR_NABIL}), "autre")

    def test_la_meme_photo_est_bonne_pour_le_colis_de_warghui(self):
        self.assertEqual(R.verdict_photo(ETIQUETTE_WARGHUI, "55087711265", set()), "ok")

    def test_l_etiquette_de_retour_annoncee_par_aramex_est_reconnue(self):
        lu = {"lisible": True, "bordereau": RETOUR_NABIL, "reference_expediteur": ""}
        self.assertEqual(R.verdict_photo(lu, NABIL, {RETOUR_NABIL}), "ok")

    def test_l_etiquette_d_origine_encore_sur_le_colis_est_reconnue(self):
        self.assertEqual(R.verdict_photo({"lisible": True, "bordereau": NABIL}, NABIL, set()), "ok")

    def test_sans_numero_lu_on_fait_reprendre_la_photo(self):
        self.assertEqual(R.verdict_photo({"lisible": False}, NABIL, {RETOUR_NABIL}), "illisible")

    def test_un_chiffre_d_ecart_est_une_lecture_douteuse_pas_un_autre_colis(self):
        lu = {"lisible": True, "bordereau": "55087946128"}          # 6 lu 8 en dernier chiffre
        self.assertEqual(R.verdict_photo(lu, NABIL, {RETOUR_NABIL}), "illisible")


class TestCandidatsProches(unittest.TestCase):
    """« Shpr Ref » est en petits caractères : l'IA en perd ou en change un chiffre. On en tire une piste."""

    NOS = ["55087711265", "55087538380", "55087538181", "55087924662"]

    def test_distance(self):
        self.assertEqual(R.distance("55087711265", "55087711265"), 0)
        self.assertEqual(R.distance("5507711265", "55087711265"), 1)      # un chiffre perdu (essai du 19/11)
        self.assertEqual(R.distance("55077711265", "55087711265"), 1)     # un chiffre changé (essai du 19/11)

    def test_les_deux_mauvaises_lectures_du_19_novembre_retrouvent_le_bon_bordereau(self):
        self.assertEqual(R.candidats_proches("5507711265", self.NOS), ["55087711265"])
        self.assertEqual(R.candidats_proches("55077711265", self.NOS), ["55087711265"])

    def test_un_numero_sans_rapport_ne_donne_aucune_piste(self):
        self.assertEqual(R.candidats_proches("48812240606", self.NOS), [])
        self.assertEqual(R.candidats_proches("", self.NOS), [])

    def test_le_plus_proche_d_abord(self):
        self.assertEqual(R.candidats_proches("55087538580", self.NOS), ["55087538380", "55087538181"])   # 1 chiffre, puis 2


class TestDecision(unittest.TestCase):
    def test_bonne_photo(self):
        self.assertEqual(R.decision("ok", None), {"autorise": True, "force": False})

    def test_mauvais_colis_refuse_meme_si_aramex_dit_livre(self):
        self.assertFalse(R.decision("autre", True)["autorise"])

    def test_etiquette_illisible_refusee(self):
        self.assertFalse(R.decision("illisible", True)["autorise"])

    def test_ia_en_panne_c_est_aramex_qui_decide(self):
        self.assertTrue(R.decision("indisponible", True)["autorise"])
        self.assertFalse(R.decision("indisponible", False)["autorise"])
        self.assertFalse(R.decision("indisponible", None)["autorise"])

    def test_seul_un_responsable_peut_passer_outre(self):
        self.assertFalse(R.decision("autre", False, peut_forcer=False, forcer=True)["autorise"])
        self.assertEqual(R.decision("autre", False, peut_forcer=True, forcer=True), {"autorise": True, "force": True})
        self.assertFalse(R.decision("autre", False, peut_forcer=True, forcer=False)["autorise"])


if __name__ == "__main__":
    unittest.main()
