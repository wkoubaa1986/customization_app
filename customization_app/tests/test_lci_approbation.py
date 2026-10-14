"""Approbation finale d'une Liste Commande Import (14/10/2026) — plan pur, sans base."""
import unittest

from customization_app import lci_approbation as A


def ligne(name, **kw):
    d = {"name": name, "idx": int(name[1:]), "item_code": name, "qty": 0, "qty_cible": 0, "qty_fournisseur": 0,
         "prix_fournisseur": 0, "prix_cible_negocie": 0, "decision": "", "volume_ligne_m3": 0}
    d.update(kw)
    return d


class TestPlan(unittest.TestCase):
    def test_son_prix_remplace_le_prix_negocie(self):
        """PF-10' : négocié 1,30, il cote 1,40 → 1,40 retenu, ligne Accepté."""
        p = A.plan_approbation([ligne("L31", qty=5000, qty_fournisseur=5000, prix_fournisseur=1.4,
                                      prix_cible_negocie=1.3, decision="À négocier")])
        c = p["lignes"][0]
        self.assertEqual((c["prix_de"], c["prix_a"], c["decision_de"]), (1.3, 1.4, "À négocier"))
        self.assertAlmostEqual(p["montant_apres"] - p["montant_avant"], 500)

    def test_sa_quantite_remplace_la_notre_et_le_volume_suit(self):
        """Acc023-H : 99 demandés, il en cote 100."""
        p = A.plan_approbation([ligne("L147", qty=99, qty_fournisseur=100, prix_fournisseur=0.3, volume_ligne_m3=0.99)])
        c = p["lignes"][0]
        self.assertEqual((c["qty_de"], c["qty_a"]), (99, 100))
        self.assertAlmostEqual(p["volume_apres"], 1.0)
        self.assertAlmostEqual(p["montant_apres"], 30.0)

    def test_sans_prix_du_fournisseur_rien_ne_bouge(self):
        p = A.plan_approbation([ligne("L8", qty=4, decision="À négocier", volume_ligne_m3=0.32)])
        self.assertEqual(p["lignes"], [])
        self.assertEqual(p["sans_prix"][0]["code"], "L8")
        self.assertEqual((p["montant_avant"], p["montant_apres"], p["volume_apres"]), (0, 0, 0.32))

    def test_sans_quantite_du_fournisseur_on_garde_la_notre(self):
        p = A.plan_approbation([ligne("L2", qty=50, prix_fournisseur=2, decision="À négocier")])
        c = p["lignes"][0]
        self.assertNotIn("qty_de", c)
        self.assertEqual(c["decision_de"], "À négocier")

    def test_deja_accepte_a_son_prix_rien_a_faire(self):
        p = A.plan_approbation([ligne("L3", qty=10, qty_fournisseur=10, prix_fournisseur=5, decision="Accepté")])
        self.assertEqual((p["lignes"], p["deja_ok"]), ([], 1))

    def test_prix_negocie_egal_au_sien_est_retire_sans_compter_comme_changement_de_prix(self):
        p = A.plan_approbation([ligne("L66", qty=500, qty_fournisseur=500, prix_fournisseur=2.8,
                                      prix_cible_negocie=2.8, decision="Accepté")])
        c = p["lignes"][0]
        self.assertTrue(c["retirer_negocie"])
        self.assertNotIn("prix_de", c)

    def test_abandon_hors_jeu(self):
        p = A.plan_approbation([ligne("L5", qty=10, prix_fournisseur=3, decision="Abandonné", volume_ligne_m3=1)])
        self.assertEqual((p["lignes"], p["abandonnees"], p["montant_avant"], p["volume_avant"]), ([], 1, 0, 0))

    def test_quantite_contre_proposee_remplacee_par_la_sienne(self):
        p = A.plan_approbation([ligne("L9", qty=100, qty_cible=80, qty_fournisseur=120, prix_fournisseur=1)])
        self.assertEqual((p["lignes"][0]["qty_de"], p["lignes"][0]["qty_a"]), (80, 120))


class TestAppliquer(unittest.TestCase):
    def test_pose_prix_quantite_decision(self):
        import frappe
        rows = [frappe._dict(ligne("L31", qty=5000, qty_fournisseur=5000, prix_fournisseur=1.4, prix_cible_negocie=1.3)),
                frappe._dict(ligne("L147", qty=99, qty_fournisseur=100, prix_fournisseur=0.3)),
                frappe._dict(ligne("L8", qty=4, decision="À négocier"))]
        doc = frappe._dict(articles=rows)
        A.appliquer_plan(doc, A.plan_approbation(rows))
        self.assertEqual((rows[0].prix_cible_negocie, rows[0].decision), (0, "Accepté"))
        self.assertEqual((rows[1].qty_cible, rows[1].decision), (100, "Accepté"))
        self.assertEqual(rows[2].decision, "À négocier")  # sans prix : intouchée


class TestParts(unittest.TestCase):
    def test_en_plus_au_dernier_conteneur(self):
        self.assertEqual(A.ajuster_parts([{"no": 1, "qty": 50, "pos": 3}, {"no": 2, "qty": 49}], 99, 100),
                         [{"no": 1, "qty": 50, "pos": 3}, {"no": 2, "qty": 50}])

    def test_en_moins_en_partant_du_dernier(self):
        self.assertEqual(A.ajuster_parts([{"no": 1, "qty": 50}, {"no": 2, "qty": 10}], 60, 45),
                         [{"no": 1, "qty": 45}])

    def test_sans_repartition(self):
        self.assertEqual(A.ajuster_parts([], 99, 100), [])

    def test_appliquer_ajuste_la_repartition(self):
        import json

        import frappe
        r = frappe._dict(ligne("L147", qty=99, qty_fournisseur=100, prix_fournisseur=0.3,
                               repartition_conteneurs=json.dumps([{"no": 4, "qty": 99, "pos": 2}])))
        A.appliquer_plan(frappe._dict(articles=[r]), A.plan_approbation([r]))
        self.assertEqual(json.loads(r.repartition_conteneurs), [{"no": 4, "qty": 100, "pos": 2}])


class TestTrace(unittest.TestCase):
    def test_trace_lisible(self):
        p = A.plan_approbation([ligne("L31", item_code="PF-10", qty=5000, qty_fournisseur=5000, prix_fournisseur=1.4,
                                      prix_cible_negocie=1.3),
                                ligne("L147", item_code="Acc023-H", qty=99, qty_fournisseur=100, prix_fournisseur=0.3),
                                ligne("L8", item_code="AP-C+", qty=4)])
        t = A.texte_trace(p, "USD")
        self.assertIn("#31 PF-10 1.3 → 1.4", t)
        self.assertIn("#147 Acc023-H 99 → 100", t)
        self.assertIn("#8 AP-C+", t)


if __name__ == "__main__":
    unittest.main()
