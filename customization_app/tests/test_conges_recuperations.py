"""Congés & récupérations : règles pures + vrais documents HRMS dans un savepoint (tout est annulé)."""
from __future__ import annotations

import unittest

from customization_app import conges_recuperations as CR


class TestRegles(unittest.TestCase):
    def test_dates_planifiees(self):
        d = CR.dates_planifiees("2026-01-05", 2, "2026-02-20")
        self.assertEqual([str(x) for x in d], ["2026-01-05", "2026-01-19", "2026-02-02", "2026-02-16"])
        d = CR.dates_planifiees("2026-01-05", 2, "2026-02-20", apres="2026-01-19")
        self.assertEqual([str(x) for x in d], ["2026-02-02", "2026-02-16"])
        self.assertEqual(CR.dates_planifiees("2026-03-01", 2, "2026-02-20"), [])
        self.assertEqual(CR.type_tache_pour(CR.TYPE_RECUP), "Jour de récupération")
        self.assertEqual(CR.type_tache_pour("Congé (Vacances)"), "Congé")

    def test_lettres_et_mois(self):
        self.assertEqual(CR.lettre_type("Congé (Vacances)"), "V")
        self.assertEqual(CR.lettre_type("Récupération (quinzaine)"), "R")
        self.assertEqual(CR.lettre_type("Congé Maladie"), "M")
        self.assertEqual(CR.lettre_type("Congé Sans Solde"), "S")
        self.assertEqual(CR.lettre_type("Report Congé (Vacances) N-1 Valable jusqu'au 31 Mars"), "V′")
        j = CR.jours_du_mois(2026, 2)
        self.assertEqual((len(j), j[0]["semaine"], j[-1]["jour"]), (28, "Di", 28))


class TestHRMS(unittest.TestCase):
    """Planification, affectation à la main, tâches calendrier, attribution — via HRMS, tout annulé."""

    def setUp(self):
        import frappe
        frappe.db.savepoint("conges")
        frappe.set_user("Administrator")
        self.emp = frappe.db.get_value("Employee", {"status": "Active", "holiday_list": ["is", "set"]}, "name")
        if not self.emp:
            self.skipTest("aucun employé actif avec calendrier")

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="conges")

    def _taches(self, date=None, type_=None):
        import frappe
        f = {"custom_choix_du_staff": self.emp, "subject": ["like", f"%{CR.MARQUE_TACHE}%"]}
        # Les tests travaillent en 2030-2033 : on ne compte pas les vraies tâches de l'employé (ex. Akram en 2026).
        f["starts_on"] = ["between", [f"{date} 00:00:00", f"{date} 23:59:59"]] if date else ["between", ["2030-01-01", "2033-12-31 23:59:59"]]
        if type_:
            f["custom_type_dintervention"] = type_
        return frappe.get_all("Tache de travail", filters=f, fields=["name", "toute_la_journée", "custom_type_dintervention"])

    def test_regle_planifie_absences_et_taches(self):
        import frappe
        # Tâche posée à la main avant la page, le 21/01 : elle doit être REPRISE, pas doublée.
        frappe.get_doc({"doctype": "Tache de travail", "custom_type_dintervention": "Autre", "custom_choix_du_staff": self.emp,
                        "starts_on": "2030-01-21 11:00:00", "ends_on": "2030-01-21 13:00:00",
                        "subject": "Jour de récuperation bi-hebdomadaire", "titre": "Jour de récuperation bi-hebdomadaire"}).insert()
        # Un lundi sur deux à partir du 07/01/2030 : enregistrer la règle planifie AUSSITÔT (sans clic de plus)
        r0 = CR.enregistrer_regle(self.emp, "2030-01-07", 2, 8, 1, planifier=0)   # inactif ici : on pilote l'horizon
        self.assertEqual(r0["poses"], [])
        res = CR.planifier_recuperations("2030-03-01", self.emp)
        r = next(x for x in res if x["employee"] == self.emp)
        self.assertEqual(r["poses"], ["2030-01-07", "2030-01-21", "2030-02-04", "2030-02-18"], r["sautes"])
        s = CR._soldes(2030)[self.emp][CR.TYPE_RECUP]
        self.assertEqual((s["acquis"], s["pris"], s["reste"]), (4.0, 4.0, 0.0))   # crédité et pris le jour même
        t = self._taches("2030-01-21", "Jour de récupération")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["toute_la_journée"], 1)
        self.assertEqual(len(self._taches(type_="Jour de récupération")), 4)
        # la tâche manuelle du 21/01 a été reprise : une seule tâche ce jour-là pour l'employé
        self.assertEqual(frappe.db.count("Tache de travail", {"custom_choix_du_staff": self.emp,
                         "starts_on": ["between", ["2030-01-21 00:00:00", "2030-01-21 23:59:59"]]}), 1)
        # (borné à 2030 : l'employé de test peut avoir de vraies récupérations planifiées en 2026)
        self.assertEqual(frappe.db.count("Recuperation Acquise", {"employee": self.emp, "origine": "Planifié",
                                                                   "date_debut": ["between", ["2030-01-01", "2030-12-31"]]}), 4)
        # Rejouer : rien ; avancer l'horizon : un de plus
        self.assertEqual(CR.planifier_recuperations("2030-03-01", self.emp), [])
        r2 = CR.planifier_recuperations("2030-03-05", self.emp)[0]
        self.assertEqual(r2["poses"], ["2030-03-04"])
        # Planning
        ctx = CR.get_context(2030, 1, self.emp)
        ligne = next(p for p in ctx["planning"] if p["employee"] == self.emp)
        self.assertEqual(ligne["cases"]["2030-01-07"]["lettre"], "R")
        # Annuler un jour : absence annulée, tâche supprimée, trace supprimée, solde rendu
        name = ligne["cases"]["2030-01-07"]["name"]
        CR.annuler_absence(name)
        self.assertEqual(self._taches("2030-01-07"), [])
        self.assertEqual(frappe.db.count("Recuperation Acquise", {"leave_application": name}), 0)
        self.assertEqual(CR._soldes(2030)[self.emp][CR.TYPE_RECUP]["reste"], 1.0)

    def test_enregistrer_regle_planifie_tout_de_suite(self):
        import frappe
        from frappe.utils import add_days, nowdate
        # L'horizon se compte depuis AUJOURD'HUI : premier jour dans la semaine qui vient, hors férié.
        premier = add_days(nowdate(), 2)
        while str(premier) in CR._feries(self.emp, premier, premier):
            premier = add_days(premier, 1)
        r = CR.enregistrer_regle(self.emp, premier, 2, 1, 1)          # horizon 1 semaine → le premier jour seulement
        self.assertEqual(r["poses"], [str(premier)], r["sautes"])
        self.assertEqual(frappe.db.get_value("Regle Recuperation", self.emp, "derniere_planifiee"), frappe.utils.getdate(premier))

    def test_recuperation_a_la_main_credite_le_solde(self):
        import frappe
        r = CR.affecter_absence(self.emp, CR.TYPE_RECUP, "2032-03-04", motif="accordée")   # aucune allocation 2032
        self.assertEqual(r["jours"], 1.0)
        s = CR._soldes(2032)[self.emp][CR.TYPE_RECUP]
        self.assertEqual((s["acquis"], s["pris"], s["reste"]), (1.0, 1.0, 0.0))
        self.assertEqual(len(self._taches("2032-03-04", "Jour de récupération")), 1)
        self.assertEqual(frappe.db.get_value("Recuperation Acquise", {"leave_application": r["name"]}, "origine"), "Affecté à la main")

    def test_conge_cree_une_tache_conge(self):
        t = "Congé (Vacances)"
        CR.attribuer([self.emp], t, 5, "2033-01-01", "2033-12-31")
        r = CR.affecter_absence(self.emp, t, "2033-02-02", "2033-02-03")
        self.assertEqual(r["jours"], 2.0)
        self.assertEqual(len(self._taches(type_="Congé")), 2)
        CR.annuler_absence(r["name"])
        self.assertEqual(len(self._taches(type_="Congé")), 0)

    def test_attribuer_puis_completer_puis_supprimer(self):
        import frappe
        t = "Congé (Vacances)"
        res = CR.attribuer([self.emp], t, 18, "2031-01-01", "2031-12-31")
        self.assertEqual(res[0]["action"], "créée")
        res2 = CR.attribuer([self.emp], t, 2, "2031-01-01", "2031-12-31", completer=1)
        self.assertEqual((res2[0]["action"], res2[0]["name"]), ("augmentée", res[0]["name"]))
        self.assertEqual(frappe.db.get_value("Leave Allocation", res[0]["name"], "total_leaves_allocated"), 20.0)
        with self.assertRaises(Exception):
            CR.attribuer([self.emp], t, 1, "2031-01-01", "2031-12-31", completer=0)
        CR.supprimer_allocation(res[0]["name"])
        self.assertEqual(frappe.db.get_value("Leave Allocation", res[0]["name"], "docstatus"), 2)


class TestAcces(unittest.TestCase):
    """Page réservée au rôle « Congés RH » : un autre compte est refusé côté serveur."""

    def test_refus_sans_role(self):
        import frappe
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                CR.get_context(2026, 1)
        finally:
            frappe.set_user("Administrator")
        self.assertEqual(CR.ROLES, ("Congés RH",))
