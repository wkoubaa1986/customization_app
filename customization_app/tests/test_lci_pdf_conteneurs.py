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


class TestMarquage(unittest.TestCase):
    """Shipping marks (16/10/2026) : marque principale + accessoires par conteneur."""

    def test_prefixe_sans_reference_ni_conteneur(self):
        from customization_app.lci_pdf_conteneurs import prefixe_marque
        self.assertEqual(prefixe_marque(""), "AQUAWORLD")
        self.assertEqual(prefixe_marque("AQUAWORLD · {ref} · {conteneur}/{total}"), "AQUAWORLD")
        self.assertEqual(prefixe_marque("AQW TUNIS {conteneur} {ref}"), "AQW TUNIS")
        self.assertEqual(prefixe_marque("{ref} - AQUA WORLD - {conteneur}"), "AQUA WORLD")

    def test_marque_principale(self):
        from customization_app.lci_pdf_conteneurs import marque_principale
        self.assertEqual(marque_principale("", "LCI-2026-00295", 1, 3), "AQUAWORLD · LCI-2026-00295 · C1/3")
        self.assertEqual(marque_principale("AQW TUNIS {conteneur} {ref}", "LCI-1", 2, 2), "AQW TUNIS C2 LCI-1")

    def test_accessoires_cumules_par_conteneur(self):
        from customization_app.lci_pdf_conteneurs import accessoires_conteneur
        class R:
            def __init__(self, name, code, adds): self.name, self.item_code, self.item_name, self.adds = name, code, code, adds
        rows = {"r1": R("r1", "AP-C-EP-7", [{"item_code": "Rob-LC", "item_name": "Robinet, Luxe Chromé", "item_name_traduit": "Faucet, Deluxe Chrome", "qty_par_pack": 1, "uom": "Pièce"},
                                            {"item_code": "M-75-DF", "item_name": "Membrane 75", "brand": "Dow Filmtec", "qty_par_pack": 1, "uom": "Pièce"}]),
                "r2": R("r2", "AP-FD-7", [{"item_code": "Rob-LC", "item_name": "Robinet, Luxe Chromé", "qty_par_pack": 2, "uom": "Pièce"}]),
                "r3": R("r3", "SANS", [])}
        c = {"no": 1, "lignes": [{"row": "r1", "qty": 100}, {"row": "r2", "qty": 50}, {"row": "r3", "qty": 10}, {"row": "inconnue", "qty": 5}]}
        acc = accessoires_conteneur(c, rows, lambda r: r.adds)
        self.assertEqual([(a["item_code"], a["qty"], a["pour"]) for a in acc], [("M-75-DF", 100.0, ["AP-C-EP-7"]), ("Rob-LC", 200.0, ["AP-C-EP-7", "AP-FD-7"])])
        self.assertEqual(acc[1]["nom"], "Faucet, Deluxe Chrome")           # le nom traduit du premier vu
        self.assertEqual(acc[0]["brand"], "Dow Filmtec")
        self.assertEqual(accessoires_conteneur({"no": 2, "lignes": []}, rows, lambda r: r.adds), [])

    def test_labels_portent_le_marquage_dans_toutes_les_langues(self):
        from customization_app.lci_pdf_conteneurs import labels, LABELS
        for langue in LABELS:
            L = labels(langue)
            self.assertTrue(L["marquage"] and L["accessoires"] and L["note"], langue)


class TestMarquesArticles(unittest.TestCase):
    def test_proposition_deterministe_par_marque_et_lettre(self):
        from customization_app.lci_marquage import proposition_deterministe, _nettoyer
        lignes = [{"cle": "r1", "genre": "unite", "code": "AP-P-EP-7", "nom": "x", "marque_commerciale": "ecoPurium"},
                  {"cle": "r2", "genre": "unite", "code": "AP-C-EP-7", "nom": "x", "marque_commerciale": "ecoPurium"},
                  {"cle": "r3", "genre": "unite", "code": "AP-M-AJ-7", "nom": "x", "marque_commerciale": "AquaJad"},
                  {"cle": "r4", "genre": "unite", "code": "C-3.2", "nom": "x", "marque_commerciale": ""},
                  {"cle": "acc:Rob-LC", "genre": "accessoire", "code": "Rob-LC", "nom": "Robinet, Luxe Chromé", "nom_traduit": "Faucet, Deluxe Chrome"}]
        p = proposition_deterministe(lignes)
        self.assertEqual(p, {"r1": "ECOPURIUM · MODEL A", "r2": "ECOPURIUM · MODEL B", "r3": "AQUAJAD · MODEL A", "r4": "AQUAWORLD · MODEL A", "acc:Rob-LC": "FAUCET DELUXE CHROME"})
        self.assertEqual(_nettoyer("  faucet,   deluxe  chrome extra long name that goes on  "), "FAUCET,   DELUXE  CHROME EXTRA".replace("   ", " ").replace("  ", " ")[:28].rstrip())

    def test_accessoires_portent_les_marques(self):
        from customization_app.lci_pdf_conteneurs import accessoires_conteneur
        class R:
            def __init__(self, name, code, adds): self.name, self.item_code, self.item_name, self.adds = name, code, code, adds
        rows = {"r1": R("r1", "AP-C-EP-7", [{"item_code": "Rob-LC", "item_name": "Robinet", "qty_par_pack": 1, "uom": "Pièce"}])}
        c = {"no": 1, "lignes": [{"row": "r1", "qty": 10}]}
        acc = accessoires_conteneur(c, rows, lambda r: r.adds, {"r1": "ECOPURIUM · MODEL A", "acc:Rob-LC": "FAUCET DELUXE CHROME"})
        self.assertEqual((acc[0]["pour"], acc[0]["marque"]), (["ECOPURIUM · MODEL A"], "FAUCET DELUXE CHROME"))
