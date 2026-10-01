"""Rapport Prime : règles pures (sans base) + concordance avec l'ancien Script Report.

Le Script Report « Prime Jamel » n'existe qu'en base : le test de concordance
est sauté là où il manque. Là où il existe, la page doit lister EXACTEMENT les
mêmes commandes que lui pour chaque vendeur (c'est sur ces listes que les primes
ont été versées) ; la somme des primes doit être la même à 1 millime près.
"""
from __future__ import annotations

import unittest

from customization_app import rapport_prime as RP


class TestRegles(unittest.TestCase):
    def test_trimestre_de(self):
        self.assertEqual(RP.trimestre_de("2026-01-01"), "Q1")
        self.assertEqual(RP.trimestre_de("2026-03-31"), "Q1")
        self.assertEqual(RP.trimestre_de("2026-04-01"), "Q2")
        self.assertEqual(RP.trimestre_de("2026-09-30"), "Q3")
        self.assertEqual(RP.trimestre_de("2026-12-31"), "Q4")

    def test_trimestre_courant_et_periode(self):
        self.assertEqual(RP.trimestre_courant(2026, "2026-09-30"), "Q3")
        self.assertEqual(RP.periode(2026, "2026-09-30"), ("2026-01-01", "2026-09-30"))
        self.assertEqual(RP.periode(2026, "2026-10-01"), ("2026-01-01", "2026-12-31"))
        # Année passée : tout ; année future : rien encore (Q1 sans commandes).
        self.assertEqual(RP.periode(2025, "2026-09-30"), ("2025-01-01", "2025-12-31"))
        self.assertEqual(RP.trimestre_courant(2027, "2026-09-30"), "Q1")

    def test_eligibilite(self):
        self.assertTrue(RP.ligne_eligible("RO domestique avec pompe", "AP-M"))
        # Nom RÉEL du groupe : apostrophe typographique + « œ ». L'ancien rapport
        # comparait à « Main d'oeuvre » et ne l'excluait jamais.
        self.assertFalse(RP.ligne_eligible("Main d’œuvre", "M-I-OD"))
        self.assertFalse(RP.ligne_eligible("Livraison", "F-km"))
        self.assertFalse(RP.ligne_eligible("Divers", "Liv"))
        self.assertFalse(RP.ligne_eligible(None, "A-D"))

    def test_groupes_exclus_existent_en_base(self):
        import frappe
        for g in RP.GROUPES_NON_ELIGIBLES:
            self.assertTrue(frappe.db.exists("Item Group", g), f"groupe introuvable : {g!r}")

    def test_prime_et_reste(self):
        self.assertEqual(RP.prime_de(1000), 20.0)
        self.assertEqual(RP.reste(935.0, 1008.0), -73.0)
        self.assertEqual(RP.reste(2066.667, 1790), 276.667)

    def test_appartient(self):
        self.assertTrue(RP.appartient([], "Jamel Aloui"))
        self.assertTrue(RP.appartient(["Jamel Aloui", "X"], "Jamel Aloui"))
        self.assertFalse(RP.appartient(["Nejib Koubaa"], "Jamel Aloui"))

    def test_synthese_cumuls(self):
        lignes = [
            {"trimestre": "Q1", "prime": 100.0}, {"trimestre": "Q2", "prime": 50.0},
            {"trimestre": "Q3", "prime": 10.0}, {"trimestre": None, "prime": 7.0},
        ]
        versements = [{"trimestre": "Q1", "montant": 60}, {"trimestre": "Q1", "montant": 60},
                      {"trimestre": "Q2", "montant": 20}]
        s = RP._synthese(lignes, versements, "Q2")
        q1, q2, q3, q4 = s["trimestres"]
        self.assertEqual((q1["calcule"], q1["verse"], q1["reste"]), (100.0, 120.0, -20.0))
        self.assertEqual((q2["calcule"], q2["verse"], q2["reste"]), (50.0, 20.0, 30.0))
        self.assertEqual(q2["cumul_reste"], 10.0)
        self.assertFalse(q3["ouvert"])
        self.assertEqual(q3["calcule"], 10.0)  # visible, mais hors cumul « à ce jour »
        self.assertFalse(q4["ouvert"])
        self.assertEqual(s["total_calcule"], 150.0)
        self.assertEqual(s["total_verse"], 140.0)
        self.assertEqual(s["reste"], 10.0)
        self.assertEqual(s["hors_annee"], 7.0)


class TestConcordanceAncienRapport(unittest.TestCase):
    """Même liste de commandes que le Script Report « Prime Jamel », vendeur par vendeur.

    La prime, elle, est INFÉRIEURE ou égale : l'ancien rapport comptait la
    main-d'œuvre et la livraison (voir test_eligibilite)."""

    def test_memes_commandes_et_prime_au_plus_egale(self):
        import frappe
        from frappe.utils import flt, getdate, nowdate

        if not frappe.db.exists("Report", "Prime Jamel"):
            self.skipTest("Script Report « Prime Jamel » absent de ce site")
        from frappe.desk.query_report import run

        annee = getdate(nowdate()).year
        debut, fin = RP.periode(annee)
        for emp in RP._employes_vendeurs(annee)[:4]:
            ancien = run("Prime Jamel", {"annee": str(annee), "employee": emp.name},
                         ignore_prepared_report=True)["result"]
            nouveau = RP._lignes_employe(emp, annee, debut, fin)
            with self.subTest(employe=emp.employee_name):
                self.assertEqual({r["commande"] for r in ancien}, {l["commande"] for l in nouveau})
                self.assertLessEqual(sum(l["prime"] for l in nouveau),
                                     sum(flt(r["Prime"]) for r in ancien) + 0.01)


class TestEcriturePartagee(unittest.TestCase):
    """Une écriture de caisse peut porter plusieurs versements, jusqu'à son montant.
    Tout est annulé par rollback, rien n'est conservé."""

    def setUp(self):
        import frappe
        frappe.db.savepoint("prime_partagee")
        # Une écriture « prime » réelle : c'est sur celles-là que porte la proposition de rattachement.
        self.je = frappe.db.get_value("Journal Entry", {"docstatus": 1, "total_debit": [">", 100],
                                                        "user_remark": ["like", "%prime%"]},
                                      ["name", "total_debit", "posting_date"], as_dict=True)
        self.emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not self.je or not self.emp:
            self.skipTest("pas d'écriture validée ou d'employé sur ce site")

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="prime_partagee")

    def _versement(self, montant):
        import frappe
        return frappe.get_doc({"doctype": "Prime Versee", "employee": self.emp, "annee": 2026,
                               "trimestre": "Q1", "date_versement": "2026-03-31", "montant": montant,
                               "journal_entry": self.je.name}).insert()

    def test_deux_parts_puis_plafond(self):
        import frappe
        from customization_app.customize_erpnext.doctype.prime_versee.prime_versee import deja_rattache
        total = float(self.je.total_debit)
        self._versement(total * 0.4)
        self._versement(total * 0.6)              # la même écriture, deux vendeurs : accepté
        self.assertAlmostEqual(deja_rattache(self.je.name), total, places=2)
        with self.assertRaises(frappe.ValidationError):
            self._versement(1)                     # au-delà du montant de l'écriture : refusé
        # Plus rien à proposer pour cette écriture
        candidates = RP.ecritures_prime_non_rattachees(self.je.posting_date.year)
        self.assertFalse(any(c["name"] == self.je.name for c in candidates))

    def test_reste_declare_autre_prime(self):
        """Écriture qui contient d'autres primes : on ne prend qu'une part et on la solde."""
        import frappe
        total = float(self.je.total_debit)
        doc = self._versement(total * 0.3)
        annee = self.je.posting_date.year
        self.assertTrue(any(c["name"] == self.je.name and abs(c["reste"] - total * 0.7) < 0.01
                            for c in RP.ecritures_prime_non_rattachees(annee)))
        doc.ecriture_soldee = 1
        doc.save()
        self.assertFalse(any(c["name"] == self.je.name for c in RP.ecritures_prime_non_rattachees(annee)))


class TestPdfEmploye(unittest.TestCase):
    """Le PDF d'un vendeur ne contient QUE lui : trimestres, versements, commandes."""

    def test_html_ne_parle_que_de_lui(self):
        d = {
            "employee": "HR-EMP-00002", "employee_name": "Jamel Aloui", "annee": 2026,
            "periode": {"debut": "2026-01-01", "fin": "2026-09-30", "libelle": "3e trimestre"},
            "lignes": [{"commande": "SAL-ORD-2026-00001", "date_commande": "2026-02-01", "date_livraison": "2026-02-03",
                        "trimestre": "Q1", "client": "C1", "client_nom": "Client Un", "ttc": 119.0, "ttc_e": 119.0,
                        "ht": 100.0, "ht_e": 100.0, "prime": 2.0, "type": "E/R"}],
            "versements": [{"date_versement": "2026-04-29", "trimestre": "Q1", "montant": 733.0,
                            "journal_entry": "ACC-JV-2026-00298", "remarque": "1ère tranche", "ecriture_soldee": 1}],
        }
        d.update(RP._synthese(d["lignes"], d["versements"], "Q3"))
        html = RP._html_pdf(d, "TND")
        for attendu in ("Jamel Aloui", "SAL-ORD-2026-00001", "Client Un", "ACC-JV-2026-00298", "(part)",
                        "1er trimestre", "Total à ce jour", "E/R"):
            self.assertIn(attendu, html)
        for exclu in ("Nejib", "Synthèse par employé", "non rattachées", "Rattacher"):
            self.assertNotIn(exclu, html)


class TestModesEtMainOeuvre(unittest.TestCase):
    """Taux configurables, mode par employé, main-d'œuvre partagée entre techniciens."""

    def test_regles_pures(self):
        self.assertEqual(RP.prime_de(1000, 0.03), 30.0)
        self.assertEqual(RP.part_main_oeuvre(300, 3), 100.0)
        self.assertEqual(RP.part_main_oeuvre(300, 0), 300.0)   # jamais de division par zéro
        self.assertTrue(RP.compte_ventes("Ventes") and RP.compte_ventes("Ventes + Main d'œuvre"))
        self.assertFalse(RP.compte_ventes("Main d'œuvre") or RP.compte_ventes("Exclu"))
        self.assertTrue(RP.compte_main_oeuvre("Main d'œuvre") and RP.compte_main_oeuvre("Ventes + Main d'œuvre"))
        self.assertFalse(RP.compte_main_oeuvre("Ventes") or RP.compte_main_oeuvre("Exclu"))

    def test_synthese_additionne_les_deux_sources(self):
        lignes = [{"trimestre": "Q1", "prime": 100.0}]
        taches = [{"trimestre": "Q1", "prime": 40.0}, {"trimestre": "Q2", "prime": 5.0}, {"trimestre": None, "prime": 1.0}]
        s = RP._synthese(lignes, [], "Q2", taches)
        q1, q2 = s["trimestres"][:2]
        self.assertEqual((q1["calcule"], q1["calcule_vente"], q1["calcule_mo"], q1["commandes"], q1["taches"]),
                         (140.0, 100.0, 40.0, 1, 1))
        self.assertEqual((q2["calcule"], q2["calcule_mo"]), (5.0, 5.0))
        self.assertEqual((s["total_calcule"], s["total_vente"], s["total_mo"], s["hors_annee"]), (145.0, 100.0, 45.0, 1.0))

    def test_config_appliquee(self):
        """Un technicien en « Main d'œuvre » n'a que des tâches ; un « Exclu » disparaît ; les taux suivent la config."""
        import frappe
        from frappe.utils import getdate, nowdate
        frappe.db.savepoint("config_prime")
        try:
            annee = getdate(nowdate()).year
            tech = frappe.db.get_value("Tache de travail", {"status": "Completed", "custom_type_dintervention": "Installation",
                                                             "custom_choix_du_staff": ["is", "set"]}, "custom_choix_du_staff")
            vendeur = frappe.db.get_value("Sales Person", {"employee": ["is", "set"], "employee": ["!=", tech or ""]}, "employee")
            if not tech or not vendeur:
                self.skipTest("pas de technicien ou de vendeur sur ce site")
            cfg = frappe.get_doc("Config Prime")
            cfg.taux_vente, cfg.taux_main_oeuvre = 3, 5
            cfg.set("employes", [])
            cfg.append("employes", {"employee": tech, "mode": "Main d'œuvre"})
            cfg.append("employes", {"employee": vendeur, "mode": "Exclu"})
            cfg.save()
            frappe.clear_cache(doctype="Config Prime")

            config = RP._config()
            self.assertEqual((config["taux_vente"], config["taux_mo"]), (0.03, 0.05))
            noms = [e.name for e in RP._employes_vendeurs(annee, config)]
            self.assertIn(tech, noms)
            self.assertNotIn(vendeur, noms)

            debut, fin = RP.periode(annee)
            emp = frappe.db.get_value("Employee", tech, ["name", "employee_name", "user_id"], as_dict=True)
            lignes, taches, mode = RP._calcul_employe(emp, annee, debut, fin, config)
            self.assertEqual(mode, "Main d'œuvre")
            self.assertEqual(lignes, [])
            self.assertTrue(taches)
            t = taches[0]
            self.assertAlmostEqual(t["prime"], 0.05 * t["part"], places=3)
            self.assertAlmostEqual(t["part"], t["ht_mo"] / t["nb_techniciens"], places=2)
        finally:
            frappe.db.rollback(save_point="config_prime")
            frappe.clear_cache(doctype="Config Prime")


class TestCoefficient(unittest.TestCase):
    """Coefficient par employé et par trimestre : prime retenue = brut × coef."""

    def test_regle_pure(self):
        self.assertEqual(RP.applique_coefficient(200, None), 200.0)
        self.assertEqual(RP.applique_coefficient(200, 50), 100.0)
        self.assertEqual(RP.applique_coefficient(200, 0), 0.0)

    def test_synthese_avec_coefficient(self):
        lignes = [{"trimestre": "Q1", "prime": 100.0}, {"trimestre": "Q2", "prime": 80.0}]
        s = RP._synthese(lignes, [{"trimestre": "Q1", "montant": 30}], "Q2",
                         coefs={"Q1": {"coefficient": 50, "remarque": "objectif non atteint"}})
        q1, q2 = s["trimestres"][:2]
        self.assertEqual((q1["brut"], q1["coefficient"], q1["calcule"], q1["reste"]), (100.0, 50.0, 50.0, 20.0))
        self.assertEqual((q2["brut"], q2["coefficient"], q2["calcule"]), (80.0, 100.0, 80.0))
        self.assertEqual((s["total_brut"], s["total_calcule"], s["reste"]), (180.0, 130.0, 100.0))

    def test_enregistrer_puis_retour_a_100(self):
        import frappe
        frappe.db.savepoint("coef")
        try:
            emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
            nom = RP.enregistrer_coefficient(emp, 2026, "Q1", 50, "essai")
            self.assertTrue(nom)
            self.assertEqual(RP._coefficients(2026)[(emp, "Q1")]["coefficient"], 50.0)
            nom2 = RP.enregistrer_coefficient(emp, 2026, "Q1", 75)     # remplace, pas de doublon
            self.assertEqual(nom2, nom)
            self.assertIsNone(RP.enregistrer_coefficient(emp, 2026, "Q1", 100))  # 100 % sans motif = fiche supprimée
            self.assertNotIn((emp, "Q1"), RP._coefficients(2026))
            with self.assertRaises(frappe.ValidationError):
                RP.enregistrer_coefficient(emp, 2026, "Q1", 150)
        finally:
            frappe.db.rollback(save_point="coef")


class TestAppreciation(unittest.TestCase):
    """Commentaire du responsable sous le trimestre : stockage, PDF en rouge, prompt IA."""

    def test_prompt_garde_le_fond_et_la_langue(self):
        system, user = RP.prompt_appreciation("bon travail mais retards", "Jamel Aloui", "Q1", 2026,
                                              {"brut": 885.6, "coefficient": 100, "commandes": 196, "taches": 0})
        self.assertIn("N'invente aucun chiffre", system)
        self.assertIn("langue du texte", system)
        self.assertIn("bon travail mais retards", user)
        self.assertIn("1er trimestre 2026", user)
        self.assertIn("Jamel Aloui", user)

    def test_pdf_appreciation_en_rouge(self):
        d = {"employee": "E", "employee_name": "Jamel Aloui", "annee": 2026,
             "periode": {"debut": "2026-01-01", "fin": "2026-09-30", "libelle": "3e trimestre"},
             "lignes": [], "versements": []}
        d.update(RP._synthese([{"trimestre": "Q1", "prime": 10.0}], [], "Q3",
                              coefs={"Q1": {"coefficient": 100, "appreciation": "Très bon trimestre, à confirmer."}}))
        html = RP._html_pdf(d, "TND")
        self.assertIn("Appréciation 1er trimestre : Très bon trimestre, à confirmer.", html)
        self.assertIn("color:#b02a37", html)

    def test_enregistrer_appreciation(self):
        import frappe
        frappe.db.savepoint("appr")
        try:
            emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
            nom = RP.enregistrer_appreciation(emp, 2026, "Q2", "  Bon travail.  ")
            self.assertTrue(nom)
            fiche = RP._coefficients(2026)[(emp, "Q2")]
            self.assertEqual((fiche["coefficient"], fiche["appreciation"]), (100.0, "Bon travail."))
            # Le coefficient à 100 % ne supprime pas la fiche tant qu'il y a une appréciation.
            self.assertEqual(RP.enregistrer_coefficient(emp, 2026, "Q2", 100), nom)
            # Vider l'appréciation, coefficient 100 %, sans motif : fiche supprimée.
            self.assertIsNone(RP.enregistrer_appreciation(emp, 2026, "Q2", ""))
            self.assertNotIn((emp, "Q2"), RP._coefficients(2026))
        finally:
            frappe.db.rollback(save_point="appr")
