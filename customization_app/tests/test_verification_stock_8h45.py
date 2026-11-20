"""Vérification de stock : une heure à partir de 8 h 45 (20/11/2026) — le patch de rattrapage."""
import datetime
import unittest

from customization_app.patches import verification_stock_8h45_une_heure as P


class TestCreneauPur(unittest.TestCase):
    def test_meme_jour_8h45_une_heure(self):
        debut, fin = P.creneau(datetime.datetime(2026, 11, 23, 9, 0))
        self.assertEqual((debut, fin), (datetime.datetime(2026, 11, 23, 8, 45), datetime.datetime(2026, 11, 23, 9, 45)))
        debut, fin = P.creneau("2026-05-08 08:15:00")                       # ancienne tâche « nizar », plus tôt
        self.assertEqual((debut, fin), (datetime.datetime(2026, 5, 8, 8, 45), datetime.datetime(2026, 5, 8, 9, 45)))
        debut, fin = P.creneau(datetime.datetime(2026, 11, 23, 23, 30))     # le jour ne bouge jamais
        self.assertEqual(debut.date(), datetime.date(2026, 11, 23))


class TestPatchSurLeSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import frappe
        if not getattr(frappe.local, "site", None):
            raise unittest.SkipTest("sans site")
        cls.frappe = frappe

    def setUp(self):
        self.frappe.set_user("Administrator")
        self.frappe.db.savepoint("verif_8h45_test")

    def tearDown(self):
        self.frappe.db.rollback(save_point="verif_8h45_test")

    def _tache(self, titre, statut="Open"):
        f = self.frappe
        employe = f.db.get_value("Employee", {"status": "Active"}, "name")
        doc = f.get_doc({"doctype": "Tache de travail", "custom_type_dintervention": "Autre", "custom_choix_du_staff": employe,
                         "starts_on": "2031-03-03 09:00:00", "ends_on": "2031-03-03 10:30:00", "temps": "1 heure, 30 min",
                         "status": "Open", "titre": titre, "subject": "essai"})
        doc.flags.ignore_permissions = True
        doc.flags.duree_fixee = True
        doc.insert()
        if statut != "Open":
            f.db.set_value("Tache de travail", doc.name, "status", statut, update_modified=False)
        return doc.name

    def test_taches_ouvertes_recalees_les_autres_intactes(self):
        f = self.frappe
        from customization_app import stock_entrepots as S
        ouverte = self._tache("🧾 Vérification stock\nStock d’essai\nEssai")
        ancienne = self._tache("Vérification Stock nizar")
        terminee = self._tache("🧾 Vérification stock\nStock d’essai\nEssai", "Completed")
        depart = self._tache("📦 Départ de Essai\nCompter, fermer et transférer\nStock d’essai")
        autre = self._tache("Installation chez un client")
        P.execute()
        lire = lambda n: f.db.get_value("Tache de travail", n, ["starts_on", "ends_on", "temps"])
        attendu = (datetime.datetime(2031, 3, 3, 8, 45), datetime.datetime(2031, 3, 3, 9, 45), "1 heure")
        intact = (datetime.datetime(2031, 3, 3, 9, 0), datetime.datetime(2031, 3, 3, 10, 30), "1 heure, 30 min")
        self.assertEqual(lire(ouverte), attendu)
        self.assertEqual(lire(ancienne), attendu)
        for n in (terminee, depart, autre):
            self.assertEqual(lire(n), intact, n)
        # plus aucune tâche de vérification ouverte ailleurs qu'à 8 h 45 pour une heure
        for n in P.taches_ouvertes():
            d, fin, temps = lire(n)
            self.assertEqual((d.strftime("%H:%M"), (fin - d).total_seconds(), temps), ("08:45", 3600, "1 heure"), n)
        # le réglage : 08:45 pour chaque stock, une heure
        cfg = S.config_verification()
        self.assertEqual(cfg["duree"], "1 heure")
        self.assertTrue(all(l["heure"] == "08:45" for l in cfg["lignes"]), cfg["lignes"])
        # rejoué : rien ne change
        P.execute()
        self.assertEqual(lire(ouverte), attendu)


if __name__ == "__main__":
    unittest.main()
