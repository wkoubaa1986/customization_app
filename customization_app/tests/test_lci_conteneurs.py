"""Tests de la répartition en conteneurs d'une Liste Commande Import.

Convention de l'app : `unittest.TestCase` pur, données injectées, aucun accès
réseau ni base.
"""
from __future__ import annotations

import json
import unittest

from customization_app import lci_conteneurs as C


def l(row, qty, vu, pu=0.0):
    return {"row": row, "libelle": row, "qty": qty, "volume_unitaire": vu,
            "montant_unitaire": pu}


class TestCapacite(unittest.TestCase):
    def test_la_capacite_retenue_est_le_volume_chargeable_pas_le_cubage(self):
        self.assertAlmostEqual(C.capacite("40' HC", 0.90), 68.4)
        self.assertAlmostEqual(C.capacite("20'", 0.90), 29.7)

    def test_type_inconnu_repli_sur_le_40_hc(self):
        self.assertAlmostEqual(C.capacite("35'", 1.0), 76.0)


class TestRepartition(unittest.TestCase):
    def test_tout_tient_dans_un_seul_conteneur(self):
        p = C.repartir([l("a", 10, 1.0), l("b", 5, 2.0)], 30)
        self.assertEqual(len(p["conteneurs"]), 1)
        self.assertAlmostEqual(p["conteneurs"][0]["volume"], 20.0)
        self.assertEqual(len(p["conteneurs"][0]["lignes"]), 2)

    def test_une_ligne_qui_deborde_est_scindee_a_l_unite_pres(self):
        p = C.repartir([l("a", 100, 0.5)], 30)   # 50 m³ pour 30 de capacité
        self.assertEqual(len(p["conteneurs"]), 2)
        c1, c2 = p["conteneurs"]
        self.assertEqual(c1["lignes"][0]["qty"], 60)
        self.assertEqual(c2["lignes"][0]["qty"], 40)
        self.assertTrue(c1["lignes"][0]["scinde"])

    def test_la_scission_ne_coupe_jamais_une_unite_en_deux(self):
        p = C.repartir([l("a", 10, 7.0)], 30)    # 4 tiennent, pas 4,28
        self.assertEqual(p["conteneurs"][0]["lignes"][0]["qty"], 4)
        self.assertAlmostEqual(p["conteneurs"][0]["volume"], 28.0)

    def test_l_ordre_de_la_liste_est_respecte_donc_les_familles_restent_groupees(self):
        p = C.repartir([l("a", 1, 10.0), l("b", 1, 10.0), l("c", 1, 10.0),
                        l("d", 1, 10.0)], 20)
        self.assertEqual([[x["row"] for x in c["lignes"]] for c in p["conteneurs"]],
                         [["a", "b"], ["c", "d"]])

    def test_les_montants_suivent_la_quantite_scindee(self):
        p = C.repartir([l("a", 100, 0.5, 2.0)], 30)
        self.assertAlmostEqual(p["conteneurs"][0]["montant"], 120.0)
        self.assertAlmostEqual(p["conteneurs"][1]["montant"], 80.0)

    def test_une_ligne_sans_volume_est_signalee_et_ne_fausse_pas_le_calcul(self):
        p = C.repartir([l("a", 10, 0.0), l("b", 10, 1.0)], 30)
        self.assertEqual([x["row"] for x in p["sans_volume"]], ["a"])
        self.assertEqual(len(p["conteneurs"]), 1)
        self.assertAlmostEqual(p["conteneurs"][0]["volume"], 10.0)

    def test_une_unite_plus_grosse_que_le_conteneur_voyage_seule(self):
        p = C.repartir([l("a", 2, 40.0)], 30)
        self.assertEqual(len(p["conteneurs"]), 2)
        self.assertTrue(all(c["lignes"][0]["qty"] == 1 for c in p["conteneurs"]))

    def test_aucune_ligne_chargeable_rend_aucun_conteneur(self):
        p = C.repartir([l("a", 0, 1.0)], 30)
        self.assertEqual(p["conteneurs"], [])

    def test_le_nombre_de_conteneurs_reste_le_minimum_theorique(self):
        """Scinder autorisé : 191,5 m³ à 68,4 de capacité tiennent en 3."""
        lignes = [l(f"r{i}", 50, 0.0766) for i in range(50)]   # 191,5 m³
        p = C.repartir(lignes, 68.4)
        self.assertEqual(len(p["conteneurs"]), 3)
        self.assertAlmostEqual(sum(c["volume"] for c in p["conteneurs"]), 191.5, places=1)


if __name__ == "__main__":
    unittest.main()


class TestGabaritsMelanges(unittest.TestCase):
    """Un 20' derrière deux 40' HC : la commande n'est pas obligée de voyager
    dans un seul format."""

    def test_chaque_conteneur_prend_la_capacite_qu_on_lui_impose(self):
        p = C.repartir([l("a", 100, 1.0)], 68.4, caps=[30.0, 68.4])
        self.assertEqual([c["capacite"] for c in p["conteneurs"]], [30.0, 68.4, 68.4])
        self.assertEqual([c["lignes"][0]["qty"] for c in p["conteneurs"]], [30, 68, 2])

    def test_au_dela_de_la_liste_on_reprend_le_gabarit_par_defaut(self):
        p = C.repartir([l("a", 10, 10.0)], 50.0, caps=[20.0])
        self.assertEqual([c["capacite"] for c in p["conteneurs"]], [20.0, 50.0, 50.0])

    def test_une_capacite_nulle_dans_la_liste_retombe_sur_le_defaut(self):
        p = C.repartir([l("a", 10, 1.0)], 68.4, caps=[0])
        self.assertEqual(p["conteneurs"][0]["capacite"], 68.4)

    def test_le_resume_de_flotte_compte_les_gabarits(self):
        self.assertEqual(C.resume_gabarits(
            [{"type": "40' HC"}, {"type": "40' HC"}, {"type": "20'"}]),
            "2 × 40' HC + 1 × 20'")

    def test_resume_vide_sans_conteneur(self):
        self.assertEqual(C.resume_gabarits([]), "")


class Ligne:
    def __init__(self, name, code, qty, volume_ligne, prix, parts, decision=""):
        self.name = name
        self.item_code = code
        self.item_name = code
        self.item_name_traduit = ""
        self.uom = "Pièce"
        self.qty = qty
        self.qty_cible = 0
        self.volume_ligne_m3 = volume_ligne
        self.prix_fournisseur = prix
        self.prix_cible_negocie = 0
        self.decision = decision
        self.repartition_conteneurs = json.dumps(parts) if parts is not None else ""

    def get(self, k, default=None):
        return self.__dict__.get(k, default)


class Doc:
    def __init__(self, entete, articles):
        self.plan_conteneurs = json.dumps(entete)
        self.articles = articles


class TestPlanRelu(unittest.TestCase):
    """L'onglet du formulaire et la feuille Excel relisent le plan APPLIQUÉ —
    ils ne le recalculent pas, sinon un plan retouché à la main bougerait tout
    seul à l'export."""

    def _doc(self):
        entete = {"type": "40' HC", "taux": 0.9, "capacite": 68.4, "conteneurs": [
            {"no": 1, "type": "20'", "capacite": 29.7},
            {"no": 2, "type": "40' HC", "capacite": 68.4}]}
        return Doc(entete, [
            Ligne("a", "ART-1", 100, 50.0, 2.0, [{"no": 1, "qty": 40}, {"no": 2, "qty": 60}]),
            Ligne("b", "ART-2", 10, 5.0, 3.0, [{"no": 2, "qty": 10}]),
            Ligne("c", "ART-3", 10, 5.0, 1.0, None),          # jamais réparti
            Ligne("d", "ART-4", 10, 5.0, 1.0, None, "Abandonné"),
        ])

    def test_chaque_conteneur_retrouve_ses_parts_et_ses_totaux(self):
        p = C.plan_enregistre(self._doc())
        c1, c2 = p["conteneurs"]
        self.assertEqual([l["item_code"] for l in c1["lignes"]], ["ART-1"])
        self.assertAlmostEqual(c1["volume"], 20.0)     # 40 × 0,5 m³
        self.assertAlmostEqual(c1["montant"], 80.0)    # 40 × 2 USD
        self.assertAlmostEqual(c2["volume"], 35.0)     # 60 × 0,5 + 10 × 0,5
        self.assertAlmostEqual(c2["montant"], 150.0)

    def test_le_gabarit_de_chaque_conteneur_est_conserve(self):
        p = C.plan_enregistre(self._doc())
        self.assertEqual([c["type"] for c in p["conteneurs"]], ["20'", "40' HC"])
        self.assertEqual([c["capacite"] for c in p["conteneurs"]], [29.7, 68.4])

    def test_une_ligne_scindee_est_signalee_des_deux_cotes(self):
        p = C.plan_enregistre(self._doc())
        self.assertTrue(all(l["scinde"] for c in p["conteneurs"]
                            for l in c["lignes"] if l["item_code"] == "ART-1"))

    def test_une_ligne_hors_plan_n_apparait_dans_aucun_conteneur(self):
        p = C.plan_enregistre(self._doc())
        codes = [l["item_code"] for c in p["conteneurs"] for l in c["lignes"]]
        self.assertNotIn("ART-3", codes)
        self.assertNotIn("ART-4", codes)

    def test_une_part_qui_vise_un_conteneur_disparu_est_ignoree(self):
        doc = self._doc()
        doc.articles.append(Ligne("e", "ART-5", 5, 5.0, 1.0, [{"no": 9, "qty": 5}]))
        p = C.plan_enregistre(doc)   # ne doit pas lever
        self.assertEqual(len(p["conteneurs"]), 2)

    def test_sans_plan_enregistre_rien_a_montrer(self):
        self.assertEqual(C.plan_enregistre(Doc({}, []))["conteneurs"], [])


class TestDatesDeDepart(unittest.TestCase):
    """Un import part rarement d'un coup : chaque conteneur porte sa date."""

    def test_la_date_est_conservee_a_la_relecture(self):
        entete = {"type": "40' HC", "taux": 0.9, "capacite": 68.4, "conteneurs": [
            {"no": 1, "type": "40'", "capacite": 67.0, "date": "2026-10-05"},
            {"no": 2, "type": "20'", "capacite": 29.7, "date": ""}]}
        doc = Doc(entete, [Ligne("a", "ART-1", 10, 5.0, 1.0,
                                 [{"no": 1, "qty": 6}, {"no": 2, "qty": 4}])])
        p = C.plan_enregistre(doc)
        self.assertEqual([c["date"] for c in p["conteneurs"]], ["2026-10-05", ""])

    def test_un_conteneur_sans_date_ne_casse_rien(self):
        entete = {"conteneurs": [{"no": 1, "type": "20'", "capacite": 29.7}]}
        p = C.plan_enregistre(Doc(entete, []))
        self.assertEqual(p["conteneurs"][0]["date"], "")


class TestEstimationVolume(unittest.TestCase):
    """Garde-fous sur ce que l'IA propose : un volume inventé qui passe sans
    contrôle fausse le plan de chargement entier."""

    def test_un_volume_plausible_passe_tel_quel(self):
        self.assertEqual(C.normaliser_estimation(0.0004), (0.0004, 0.0, 0.0))

    def test_le_volume_se_deduit_du_carton_quand_il_manque(self):
        vol, pcs, vct = C.normaliser_estimation(0, 500, 0.2)
        self.assertAlmostEqual(vol, 0.0004)
        self.assertEqual((pcs, vct), (500, 0.2))

    def test_un_carton_incoherent_est_jete_mais_le_volume_reste(self):
        vol, pcs, vct = C.normaliser_estimation(0.0004, 500, 2.0)   # 0,004 par pièce
        self.assertAlmostEqual(vol, 0.0004)
        self.assertEqual((pcs, vct), (0.0, 0.0))

    def test_un_ecart_de_10_pourcent_sur_le_carton_reste_acceptable(self):
        vol, pcs, vct = C.normaliser_estimation(0.0004, 500, 0.21)
        self.assertEqual((pcs, vct), (500, 0.21))

    def test_un_volume_absurde_n_est_pas_propose(self):
        self.assertIsNone(C.normaliser_estimation(12))
        self.assertIsNone(C.normaliser_estimation(0))
        self.assertIsNone(C.normaliser_estimation(-1))

    def test_rien_du_tout_ne_propose_rien(self):
        self.assertIsNone(C.normaliser_estimation(None, None, None))
