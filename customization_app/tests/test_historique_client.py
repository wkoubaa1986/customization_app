"""Historique client et pièces à proposer (07/10/2026) — règles pures, sans la base."""
import datetime
import json
import unittest

from customization_app import historique_client as H
from customization_app import pieces_a_changer as P
from customization_app.Maintenance import update_schedule as U

REGLES = {p["cle"]: dict(p) for p in P.DEFAUT_PIECES}
AUJ = "2026-10-07"


def evt(date, achat=(), machine=None, code="X", auto=False):
    return {"date": date, "achat": set(achat), "machine": machine, "code": code, "auto_uv": auto}


def par_cle(pieces):
    return {p["cle"]: p for p in pieces}


class TestArticles(unittest.TestCase):
    def test_cartouches_osmoseur(self):
        self.assertEqual(P.pieces_de_l_article("PF-10'-PP-UDF-CTO", "Cartouches anti-sédiment"), {"pp", "udf", "cto"})
        self.assertEqual(P.pieces_de_l_article("C-10'-PP-5m", "Cartouches anti-sédiment"), {"pp"})
        self.assertEqual(P.pieces_de_l_article("QF-10'-UDF", "Cartouches à charbon"), {"udf"})
        self.assertEqual(P.pieces_de_l_article("C-10'-CTO", "Cartouches à charbon"), {"cto"})
        self.assertEqual(P.pieces_de_l_article("F-T33-C-QF", "Filtres T33"), {"t33"})
        self.assertEqual(P.pieces_de_l_article("F-T33-M", "Filtres T33"), {"mineral"})
        self.assertEqual(P.pieces_de_l_article("F-T33-A", "Filtres T33"), {"alcalin"})
        self.assertEqual(P.pieces_de_l_article("M-75-Pu", "Membranes RO domestiques (≤100 GPD)"), {"membrane"})

    def test_lampes_resine_et_main_d_oeuvre(self):
        self.assertEqual(P.pieces_de_l_article("L-UV-6w", "Accessoires UV"), {"lampe_uv:6"})
        self.assertEqual(P.pieces_de_l_article("L-UV-55w", "Accessoires UV"), {"lampe_uv:55"})
        self.assertEqual(P.pieces_de_l_article("R-L", "Consommables & Accessoires"), {"resine"})
        self.assertEqual(P.pieces_de_l_article("M-E-OD", "Main d’œuvre"), set())     # main d'œuvre ≠ membrane
        self.assertEqual(P.pieces_de_l_article("S-S25", "Consommables & Accessoires"), set())   # le sel n'est pas suivi

    def test_porte_filtre_suivi_a_l_article(self):
        self.assertEqual(P.pieces_de_l_article("C-20'-PP-5m", "Cartouches anti-sédiment"), {"pf:C-20'-PP-5m"})
        self.assertEqual(P.pieces_de_l_article("C-10'-Bob-25m", "Cartouches anti-sédiment"), {"pf:C-10'-Bob-25m"})

    def test_pack_entretien_jamais_une_cartouche(self):
        """Les anciens packs désactivés sont rangés dans « Cartouches anti-sédiment » : leur contenu compte, pas eux."""
        self.assertEqual(P.pieces_de_l_article("P-E-2an-7-V", "Cartouches anti-sédiment"), set())


class TestMachines(unittest.TestCase):
    def test_etapes_du_code(self):
        self.assertEqual(P.etapes_du_code("AP-M-AJ-7-MA-SM"), {"mineral": True, "alcalin": True, "uv": False, "auto": False})
        self.assertEqual(P.etapes_du_code("AP-M-AJ-7-MUA-SM"), {"mineral": True, "alcalin": False, "uv": True, "auto": True})
        self.assertEqual(P.etapes_du_code("AP-P-EP-8-MAUA-SM"), {"mineral": True, "alcalin": True, "uv": True, "auto": True})
        self.assertEqual(P.etapes_du_code("AP-SM-8-DF"), {"mineral": True, "alcalin": True, "uv": True, "auto": False})
        self.assertEqual(P.etapes_du_code("AP-7-P"), {"mineral": True, "alcalin": True, "uv": False, "auto": False})
        self.assertEqual(P.etapes_du_code("AP-M-AJ-5-SM"), {"mineral": False, "alcalin": False, "uv": False, "auto": False})

    def test_osmoseur_lit_son_contenu(self):
        m = P.machine_de_la_ligne("AP-SM-8-DF", "RO domestique avec pompe", ["AP-SM-5", "M-75-DF", "F-T33-M", "F-T33-A", "F-UV-6w"])
        self.assertEqual(m["type"], "osmoseur")
        self.assertEqual(m["pieces"], {"pp", "udf", "cto", "t33", "membrane", "mineral", "alcalin", "lampe_uv:6"})
        self.assertFalse(m["auto"])
        self.assertTrue(P.machine_de_la_ligne("AP-X", "RO domestique avec pompe", ["Rel-24V"])["auto"])

    def test_porte_filtres(self):
        self.assertEqual(P.machine_de_la_ligne("P-F-T-10'-O-SC", "Porte-filtres", [])["pieces"], {"pp", "udf", "cto"})
        self.assertEqual(P.machine_de_la_ligne("P-F-S-10'-T-v2", "Porte-filtres", [])["pieces"], {"pp"})
        self.assertIsNone(P.machine_de_la_ligne("P-F-10'-O", "Porte-filtres", []))      # boîtier de rechange d'osmoseur

    def test_adoucisseur_et_uv(self):
        self.assertEqual(P.machine_de_la_ligne("Ad-25L", "Adoucisseurs Domestiques", [])["pieces"], {"resine"})
        self.assertEqual(P.machine_de_la_ligne("F-UV-12w", "Filtres UV", [])["pieces"], {"lampe_uv:12"})


class TestCalcul(unittest.TestCase):
    RO = {"type": "osmoseur", "pieces": {"pp", "udf", "cto", "t33", "membrane", "lampe_uv:6"}, "auto": False}

    def test_cas_reel_anis_dammak(self):
        """Osmoseur 8 étages du 26/02/2025, cartouches rachetées le 08/01/2026, lampe UV jamais changée."""
        evts = [evt("2025-02-26", machine=self.RO, code="AP-SM-8-DF"),
                evt("2026-01-08", achat={"pp", "udf", "cto", "t33", "membrane"}, code="C-10'-CTO")]
        r = par_cle(P.calculer(evts, REGLES, AUJ))
        self.assertEqual((r["pp"]["statut"], r["pp"]["echeance"]), ("retard", "2026-07-08"))
        self.assertEqual((r["lampe_uv:6"]["statut"], r["lampe_uv:6"]["echeance"], r["lampe_uv:6"]["origine"]),
                         ("retard", "2025-11-26", "pose"))
        self.assertEqual((r["t33"]["statut"], r["t33"]["echeance"]), ("ok", "2027-01-08"))
        self.assertEqual(r["membrane"]["echeance"], "2028-01-08")

    def test_la_quantite_ne_compte_pas(self):
        """Trois minéraux le même jour = un seul changement : le compteur part de cette date."""
        evts = [evt("2026-02-12", achat={"mineral"}), evt("2026-02-12", achat={"mineral"}), evt("2026-02-12", achat={"mineral"})]
        self.assertEqual(par_cle(P.calculer(evts, REGLES, AUJ))["mineral"]["echeance"], "2027-02-12")

    def test_le_dernier_achat_remet_le_compteur(self):
        evts = [evt("2025-01-10", achat={"pp"}), evt("2026-06-01", achat={"pp"})]
        self.assertEqual(par_cle(P.calculer(evts, REGLES, AUJ))["pp"]["echeance"], "2026-12-01")

    def test_activation_automatique_sans_rappel_de_lampe(self):
        evts = [evt("2025-01-01", machine=self.RO), evt("2026-05-07", code="Rel-24V", auto=True)]
        self.assertNotIn("lampe_uv:6", par_cle(P.calculer(evts, REGLES, AUJ)))

    def test_uv_toute_la_maison_garde_ses_9_mois(self):
        uv = {"type": "uv", "pieces": {"lampe_uv:12"}, "auto": False}
        evts = [evt("2026-01-20", machine=uv), evt("2026-02-01", code="Rel-24V", auto=True)]
        self.assertEqual(par_cle(P.calculer(evts, REGLES, AUJ))["lampe_uv:12"]["echeance"], "2026-10-20")

    def test_resine_trois_ans_et_test_de_durete(self):
        ad = {"type": "adoucisseur", "pieces": {"resine"}, "auto": False}
        r = par_cle(P.calculer([evt("2023-09-01", machine=ad)], REGLES, AUJ))["resine"]
        self.assertEqual((r["echeance"], r["statut"]), ("2026-09-01", "retard"))
        self.assertIn("dureté", r["message"])

    def test_porte_filtre_selon_son_rythme(self):
        evts = [evt("2025-01-01", achat={"pf:C-20'-PP"}), evt("2025-04-01", achat={"pf:C-20'-PP"}),
                evt("2025-07-01", achat={"pf:C-20'-PP"})]
        r = par_cle(P.calculer(evts, REGLES, "2025-08-01", {"C-20'-PP": "PP 20 pouces"}))["pf:C-20'-PP"]
        self.assertEqual((r["intervalle_mois"], r["appris"], r["echeance"], r["libelle"]), (3, True, "2025-10-01", "PP 20 pouces"))

    def test_porte_filtre_achat_unique_intervalle_par_defaut(self):
        r = par_cle(P.calculer([evt("2026-01-01", achat={"pf:C-BIG"})], REGLES, AUJ))["pf:C-BIG"]
        self.assertEqual((r["intervalle_mois"], r["appris"]), (6, False))

    def test_cartouches_10_pouces_suivent_l_osmoseur(self):
        """Avec un osmoseur, le PP garde ses 6 mois même si le client a aussi un porte-filtre 10"."""
        pf = {"type": "porte_filtre", "pieces": {"pp"}, "auto": False}
        evts = [evt("2025-01-01", machine=pf), evt("2025-02-01", achat={"pp"}), evt("2025-03-01", achat={"pp"}),
                evt("2025-04-01", achat={"membrane"})]                          # membrane achetée = il a un osmoseur
        r = par_cle(P.calculer(evts, REGLES, AUJ))["pp"]
        self.assertEqual((r["intervalle_mois"], r["appris"]), (6, False))

    def test_ancien_bientot(self):
        r = par_cle(P.calculer([evt("2023-01-01", achat={"t33"}), evt("2026-04-20", achat={"pp"})], REGLES, AUJ))
        self.assertEqual(r["t33"]["statut"], "ancien")                         # > 2 intervalles de retard
        self.assertEqual(r["pp"]["statut"], "bientot")                         # échéance 20/10, dans 13 j

    def test_piece_desactivee(self):
        regles = {**REGLES, "membrane": {**REGLES["membrane"], "actif": 0}}
        self.assertNotIn("membrane", par_cle(P.calculer([evt("2020-01-01", achat={"membrane"})], regles, AUJ)))


class TestAffichage(unittest.TestCase):
    def test_pack_filtre_regroupe(self):
        pieces = P.calculer([evt("2026-01-08", achat={"pp", "udf", "cto"}), evt("2026-05-01", achat={"t33", "mineral"})],
                            REGLES, AUJ)
        g = P.regrouper(pieces)
        self.assertEqual([x["libelle"] for x in g], ["Pack filtre PP + UDF + CTO", "T33 + Minéral"])
        self.assertIn("Pack filtre PP + UDF + CTO (retard 3 mois)", P.texte_court(g))   # dû le 08/07, 91 j
        self.assertNotIn("T33", P.texte_court(g))                               # à jour : pas proposé

    def test_echeances_eloignees_pas_regroupees(self):
        g = P.regrouper(P.calculer([evt("2026-01-08", achat={"pp"}), evt("2026-06-01", achat={"udf"})], REGLES, AUJ))
        self.assertEqual(sorted(x["cle"] for x in g), ["pp", "udf"])


class TestHistorique(unittest.TestCase):
    def test_nature_commentaire(self):
        self.assertEqual(H.nature_commentaire("📵 1er appel sans réponse — appel de Salma"), "appel")
        self.assertEqual(H.nature_commentaire("[RELANCE-PAIEMENT] | SMS | ok"), "relance")
        self.assertEqual(H.nature_commentaire("🗺️ Tournée optimisée"), "auto")
        self.assertEqual(H.nature_commentaire("le client va vérifier avec sa femme"), "note")

    def test_premiers_passages(self):
        """Qui a appelé et quand : la PREMIÈRE modification de la ligne, lue dans le suivi de la liste."""
        v1 = {"owner": "salma@x", "creation": "2026-09-29 16:35",
              "data": json.dumps({"row_changed": [["clients", 57, "r1", [["a_été_appelé", 0, 1], ["resume_appel", "", "Ne répond pas 1er appel"]]]]})}
        v2 = {"owner": "hedi@x", "creation": "2026-10-01 10:00",
              "data": json.dumps({"row_changed": [["clients", 57, "r1", [["resume_appel", "Ne répond pas 1er appel", "Rendez-vous pris"]]],
                                                  ["clients", 3, "r2", [["secteur", "", "S1"]]]]})}
        p = H.premiers_passages([v1, v2], {"r1", "r2"})
        self.assertEqual((p["r1"]["par"], p["r1"]["le"]), ("salma@x", "2026-09-29 16:35"))
        self.assertNotIn("r2", p)                                              # le secteur n'est pas un appel

    def test_texte_sans_html(self):
        self.assertEqual(H.texte("<div class='ql-editor'><p>Rappeler&nbsp;demain</p></div>"), "Rappeler demain")
        # Une note d'appel est échappée à l'écriture : elle doit se relire telle que l'opératrice l'a tapée.
        self.assertEqual(H.texte("Test note d&apos;appel &lt;b&gt;gras&lt;/b&gt;"), "Test note d'appel <b>gras</b>")


class TestAcces(unittest.TestCase):
    def _avec(self, roles, lecture=True):
        from unittest.mock import patch
        with patch.object(H.frappe, "get_roles", return_value=roles), \
                patch.object(H.frappe, "has_permission", return_value=lecture):
            return H._autorise()

    def test_technicien_et_operatrice(self):
        self.assertTrue(self._avec(["Employee", "Sales User", "Stock User"]))

    def test_partenaire_seul_exclu(self):
        """Le partenaire exécute chez nos clients, mais leur historique (achats, échéancier, appels) reste à nous."""
        self.assertFalse(self._avec(["Employee", "Partenaire", "Raven User"]))

    def test_sans_lecture_client(self):
        self.assertFalse(self._avec(["Employee"], lecture=False))


class TestTraceDecalage(unittest.TestCase):
    def test_texte(self):
        t = U.texte_decalage("OSMO", datetime.date(2025, 3, 1), datetime.date(2026, 2, 20), "SO-7", 356,
                             [(datetime.date(2025, 9, 1), datetime.date(2026, 8, 23),
                               {"custom_sms_1": datetime.date(2025, 9, 3), "custom_sms1_status": "Success",
                                "custom_sms_2": datetime.date(2025, 9, 10), "custom_sms2_status": "Failed",
                                "custom_appelle": datetime.date(2025, 10, 1)})])
        self.assertIn("Visite OSMO du 01/03/2025 réalisée par SO-7", t)
        self.assertIn("01/09/2025 → 23/08/2026", t)
        self.assertIn("SMS1 03/09/2025 ✓", t)
        self.assertIn("SMS2 10/09/2025 ✗ Failed", t)
        self.assertIn("appelé 01/10/2025", t)

    def test_sans_visite_suivante(self):
        self.assertTrue(U.texte_decalage("OSMO", "2025-03-01", "2025-03-05", "SO-1", 4, []).endswith("(livraison du 05/03/2025)."))


if __name__ == "__main__":
    unittest.main()
