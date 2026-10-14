"""PDF du plan de chargement par conteneur (14/10/2026) — fonctions pures, sans base ni PDF."""
import json
import unittest

import frappe

from customization_app import lci_pdf_conteneurs as P

L = P.labels("English")


class TestLigne(unittest.TestCase):
    def test_sa_ligne(self):
        self.assertEqual(P.ref_ligne("L11 · Reverse Osmosis System"), 11)
        self.assertIsNone(P.ref_ligne("Reverse Osmosis System"))
        self.assertIsNone(P.ref_ligne(None))

    def test_parts(self):
        self.assertEqual(P.parts_de(json.dumps([{"no": 1, "qty": 50, "pos": 3}, {"no": 2, "qty": 100}])),
                         [{"no": 1, "qty": 50}, {"no": 2, "qty": 100}])
        self.assertEqual(P.parts_de("pas du json"), [])

    def test_scindee_dit_ou_est_le_reste(self):
        parts = [{"no": 1, "qty": 50}, {"no": 2, "qty": 100}]
        self.assertEqual(P.texte_scinde(parts, 2, 150, L), "100 of 150 — rest in C1 (50)")
        self.assertEqual(P.texte_scinde(parts, 1, 150, L), "50 of 150 — rest in C2 (100)")
        self.assertIsNone(P.texte_scinde([{"no": 1, "qty": 150}], 1, 150, L))

    def test_libelles_dans_les_cinq_langues(self):
        cles = set(P.LABELS["English"])
        for langue, lab in P.LABELS.items():
            self.assertEqual(set(lab), cles, langue)
        self.assertIs(P.labels("Klingon"), P.LABELS["English"])


class TestDescription(unittest.TestCase):
    def test_ses_puces_d_abord_puis_son_texte_puis_la_notre(self):
        r = frappe._dict(description_fournisseur_puces="• RO system\n• 5 stages", description_fournisseur="L11 · RO system 5 stages",
                         item_name_traduit="Our RO", description_traduite="• Tank")
        self.assertEqual(P.description_pour_lui(r), "• RO system\n• 5 stages")
        r.description_fournisseur_puces = ""
        self.assertEqual(P.description_pour_lui(r), "RO system 5 stages")
        r.description_fournisseur = ""
        self.assertEqual(P.description_pour_lui(r), "Our RO\n• Tank")

    def test_en_puces(self):
        self.assertEqual(P.description_html("• RO system\n• 5 stages\n• Metal base"),
                         '<div class="pu-t">RO system</div><ul class="pu"><li>5 stages</li><li>Metal base</li></ul>')
        self.assertEqual(P.description_html("Pack of 3 <filters>"), "Pack of 3 &lt;filters&gt;")
        self.assertEqual(P.description_html(""), "")

    def test_une_puce_par_ligne_meme_longue_liste(self):
        h = P.description_html("\n".join("• p%d" % i for i in range(20)))
        self.assertEqual(h.count("<li>"), 19)
        self.assertNotIn("<table", h)

    def test_remplissage(self):
        self.assertAlmostEqual(P.remplissage({"volume": 78.781, "capacite": 77.4}), 101.78, places=2)
        self.assertEqual(P.remplissage({"volume": 3, "capacite": 0}), 0.0)


if __name__ == "__main__":
    unittest.main()
