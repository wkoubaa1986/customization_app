"""Plan de paiement d'une Liste Commande Import (14/10/2026) — calcul pur, sans base."""
import unittest

from customization_app import lci_paiement as P


class TestCalcul(unittest.TestCase):
    def test_pourcentage_de_la_commande_ou_du_conteneur(self):
        e = [{"libelle": "Deposit", "conteneur": 0, "pourcentage": 30, "montant": 0},
             {"libelle": "Balance C1", "conteneur": 1, "pourcentage": 70, "montant": 0}]
        r = P.calculer(e, 181483.25, {1: 62770.95, 2: 55388.0})
        self.assertEqual(e[0]["montant"], 54444.98)        # 30 % de la commande
        self.assertEqual(e[1]["montant"], 43939.67)        # 70 % du conteneur C1 (seul % de C1 : pas 100 %)
        self.assertEqual(r["total"], round(54444.98 + 43939.67, 2))
        self.assertEqual(r["reste"], round(181483.25 - 54444.98 - 43939.67, 2))

    def test_trente_soixante_dix_tombe_juste_au_centime(self):
        e = [{"conteneur": 1, "pourcentage": 30}, {"conteneur": 1, "pourcentage": 70}]
        r = P.calculer(e, 0, {1: 62770.95})
        self.assertEqual((e[0]["montant"], e[1]["montant"]), (18831.29, 43939.66))
        self.assertEqual(r["total"], 62770.95)

    def test_le_reste_suit_l_acompte(self):
        """Capture du 14/10 : acompte 20 000 tapé → le solde « = reste » vaut 42 770,95, pas 62 770,95."""
        e = [{"conteneur": 1, "montant": 20000}, {"conteneur": 1, "solde": 1, "montant": 62770.95}]
        r = P.calculer(e, 0, {1: 62770.95})
        self.assertEqual(e[1]["montant"], 42770.95)
        self.assertEqual(r["total"], 62770.95)

    def test_le_reste_apres_un_pourcentage_et_jamais_negatif(self):
        e = [{"conteneur": 2, "solde": 1}, {"conteneur": 2, "pourcentage": 30}]
        P.calculer(e, 0, {2: 1000})
        self.assertEqual((e[1]["montant"], e[0]["montant"]), (300, 700))   # le solde se calcule APRÈS, où qu'il soit
        e = [{"conteneur": 2, "montant": 1500}, {"conteneur": 2, "solde": 1}]
        P.calculer(e, 0, {2: 1000})
        self.assertEqual(e[1]["montant"], 0.0)

    def test_deux_soldes_le_premier_prend_tout(self):
        e = [{"conteneur": 1, "montant": 100}, {"conteneur": 1, "solde": 1}, {"conteneur": 1, "solde": 1}]
        P.calculer(e, 0, {1: 1000})
        self.assertEqual((e[1]["montant"], e[2]["montant"]), (900, 0.0))

    def test_le_reste_de_la_commande_sans_conteneur(self):
        e = [{"conteneur": 0, "pourcentage": 30}, {"conteneur": 0, "solde": 1}]
        P.calculer(e, 10000, {})
        self.assertEqual((e[0]["montant"], e[1]["montant"]), (3000, 7000))

    def test_montant_saisi_sans_pourcentage_reste_tel_quel(self):
        e = [{"conteneur": 2, "pourcentage": 0, "montant": 20000}]
        self.assertEqual(P.calculer(e, 100000, {2: 55388})["total"], 20000)
        self.assertEqual(e[0]["montant"], 20000)

    def test_conteneur_inconnu_signale_et_montant_intact(self):
        e = [{"conteneur": 7, "pourcentage": 50, "montant": 1234}]
        r = P.calculer(e, 100000, {1: 5000})
        self.assertEqual((r["inconnus"], e[0]["montant"]), ([7], 1234))

    def test_base_a_payer(self):
        self.assertEqual(P.base_a_payer(181483.25, {1: 62770.95, 2: 55388.0}), 118158.95)
        self.assertEqual(P.base_a_payer(181483.25, {}), 181483.25)

    def test_sans_echeance(self):
        self.assertEqual(P.calculer([], 1000, {}), {"total": 0.0, "reste": 1000.0, "inconnus": []})

    def test_tri_par_date(self):
        e = [{"date_execution": "2026-12-01", "idx": 1}, {"date_execution": "2026-10-20", "idx": 2},
             {"date_execution": None, "idx": 3}]
        self.assertEqual([x["idx"] for x in P.tri(e)], [2, 1, 3])


class TestDialogue(unittest.TestCase):
    def test_document_enregistre_accepte_le_montant(self):
        """Bug du 14/10 : une ligne de table (Document) ne prend pas e["montant"] = …"""
        import frappe
        e = frappe._dict(conteneur=1, pourcentage=30, montant=0)
        e.__class__ = type("Ligne", (), {"get": lambda self, k, d=None: getattr(self, k, d)})
        e.conteneur, e.pourcentage, e.montant = 1, 30, 0
        P.calculer([e], 0, {1: 1000})
        self.assertEqual(e.montant, 300)

    def test_par_conteneur(self):
        e = [{"conteneur": 1, "libelle": "Deposit", "type_paiement": "TT", "date_execution": "2026-10-20", "pourcentage": 30, "montant": 9},
             {"conteneur": 0, "libelle": "Order deposit"}, {"conteneur": 2, "libelle": "LC"}]
        r = P.par_conteneur(e)
        self.assertEqual(sorted(r), [1, 2])
        self.assertEqual(r[1][0]["libelle"], "Deposit")

    def test_lignes_depuis_plan_prend_le_numero_final_et_ignore_les_vides(self):
        conteneurs = [{"no": 1, "paiements": [{"libelle": "Deposit", "type_paiement": "TT", "pourcentage": 30},
                                              {"libelle": "", "type_paiement": "TT"}]},
                      {"no": 2, "paiements": [{"libelle": "At sight", "type_paiement": "XX", "montant": 500,
                                               "date_execution": "2026-12-10"}]}]
        conteneurs[0]["paiements"].append({"libelle": "Balance", "type_paiement": "TT", "solde": True})
        l = P.lignes_depuis_plan(conteneurs)
        self.assertEqual([(x["conteneur"], x["libelle"], x["type_paiement"]) for x in l],
                         [(1, "Deposit", "TT"), (1, "Balance", "TT"), (2, "At sight", "TT")])
        self.assertEqual(l[1]["solde"], 1)   # un « = reste » sans montant n'est pas une ligne vide

    def test_remplacer_garde_les_echeances_de_toute_la_commande(self):
        import frappe

        class Doc:
            def __init__(self, lignes):
                self.lignes = lignes

            def get(self, k):
                return self.lignes

            def set(self, k, v):
                self.lignes = list(v)

            def append(self, k, v):
                self.lignes.append(frappe._dict(v))

        doc = Doc([frappe._dict(conteneur=0, libelle="Order deposit", type_paiement="TT", montant=1000),
                   frappe._dict(conteneur=1, libelle="Old C1", type_paiement="TT", montant=5)])
        P.remplacer_par_conteneur(doc, [{"no": 1, "paiements": [{"libelle": "New C1", "type_paiement": "LC", "montant": 7}]}])
        self.assertEqual([(l.conteneur, l.libelle) for l in doc.lignes], [(0, "Order deposit"), (1, "New C1")])


class TestPdf(unittest.TestCase):
    def test_tableau_et_reste(self):
        from customization_app import lci_pdf_conteneurs as PDF
        L = PDF.labels("English")
        h = PDF.bloc_paiements([{"libelle": "Deposit", "conteneur": 0, "type_paiement": "TT", "date_execution": None,
                                 "pourcentage": 30, "montant": 300}], L, "USD", L["paiements"], base=1000)
        self.assertIn("Whole order", h)
        self.assertIn("Remaining to schedule", h)
        self.assertIn("700.00", h)
        self.assertEqual(PDF.bloc_paiements([], L, "USD", "x"), "")

    def test_pourcentage_deduit_d_un_montant_saisi(self):
        from customization_app import lci_pdf_conteneurs as PDF
        L = PDF.labels("English")
        h = PDF.bloc_paiements([{"libelle": "Deposit", "conteneur": 1, "type_paiement": "TT", "pourcentage": 0,
                                 "montant": 20000}], L, "USD", "x", bases={1: 62770.95})
        self.assertIn("31.9 %", h)


if __name__ == "__main__":
    unittest.main()
