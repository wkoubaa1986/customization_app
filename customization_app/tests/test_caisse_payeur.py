"""« Payée de la caisse de… » et confirmation par le payeur (24/11/2026). PUR (pas de base).

Lancer dans le site : `unittest customization_app.tests.test_caisse_payeur` depuis frappe-bench/sites."""
import unittest

from customization_app import caisse_cloture as CL
from customization_app import caisse_collecte as CC
from customization_app import caisse_depenses as D


class TestPayeur(unittest.TestCase):
    def test_lu_dans_la_remarque(self):
        r = "Gasoil\nType : Dépense non facturée\nPayée de la caisse de : Jamel Aloui\nSaisie caisse par x@y"
        self.assertEqual(D.payeur_de_remarque(r), "Jamel Aloui")
        self.assertIsNone(D.payeur_de_remarque("Gasoil\nSaisie caisse par x@y"))
        self.assertIsNone(D.payeur_de_remarque(None))

    def test_point_de_controle_chez_le_payeur(self):
        data = {"depenses": {"lignes": [
            {"name": "JV-1", "doctype": "Journal Entry", "saisi_par": "Sadok", "caisse": "Jamel",
             "payee_par_collegue": True, "description": "Gasoil", "mode": "Espèces", "montant": 20},
            {"name": "JV-2", "doctype": "Journal Entry", "saisi_par": "Jamel", "caisse": "Jamel",
             "payee_par_collegue": False, "description": "Café", "mode": "Espèces", "montant": 3},
            {"name": "PE-1", "doctype": "Payment Entry", "saisi_par": "Najib", "caisse": "Jamel",
             "payee_par_collegue": True, "description": "Facture", "mode": "Espèces", "montant": 87.537},
        ]}}
        pts = CL._depenses_collegues(data)
        self.assertEqual([p["cle"] for p in pts], ["depense_collegue:JV-1", "depense_collegue:PE-1"])
        self.assertEqual((pts[0].get("ecriture"), pts[1].get("paiement")), ("JV-1", "PE-1"))
        self.assertIn("saisie par Sadok", pts[0]["libelle"])

    def test_confirmer_sans_commentaire_mais_contester_avec(self):
        p = {"cle": "depense_collegue:JV-1", "type": "depense_collegue", "bloquant": 0, "libelle": "Dépense…"}
        p["motifs"] = CL.motifs_du_point(p)
        ok = CL.verifier_justifications([p], {p["cle"]: {"motif": "Je confirme l'avoir payée de ma caisse", "commentaire": ""}})
        self.assertEqual(ok[1], [])
        ko = CL.verifier_justifications([p], {p["cle"]: {"motif": "Je ne l'ai pas payée : à corriger", "commentaire": "non"}})
        self.assertTrue(ko[1])

    def test_la_collecte_compte_la_depense_chez_le_payeur(self):
        act = CC.activite_par_caisse({"depenses": {"lignes": [
            {"saisi_par": "Sadok", "caisse": "Jamel", "montant": 20}, {"saisi_par": "Sadok", "montant": 5}]}})
        self.assertEqual((act["Jamel"]["depenses"], act["Sadok"]["depenses"]), (20.0, 5.0))
