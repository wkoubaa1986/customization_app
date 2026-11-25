"""Accord préalable « lisible » (25/11/2026, point D) : seuil et demandes dans la fenêtre de dépense, retrait, expiration,
autres responsables prévenus, accord cité sur la dépense et à la collecte.

Lancer dans le site (jamais `bench run-tests`) avec frappe.db.commit neutralisé (essai sans trace) :
`unittest customization_app.tests.test_caisse_accord_lisible` depuis frappe-bench/sites."""
import unittest

import frappe
from frappe.utils import add_days, now_datetime

from customization_app import caisse_accord as A
from customization_app import caisse_revue as R


def _l(**autres):
    d = {"name": "JV-1", "montant": 250.0, "especes": 250.0, "cle": "x", "date": "2026-11-25", "saisi_par": "Sadok",
         "fournisseur": "F", "numero_facture": "1", "photos": [{"url": "/x.jpg"}], "type": "Dépense avec facture"}
    d.update(autres)
    return d


class TestSignaux(unittest.TestCase):
    def test_accordee(self):
        s = R.signaux_depense(_l(accord={"name": "ACCORD-1", "par": "Nejib Koubaa", "montant": 250}), [], 200)
        self.assertEqual([x["code"] for x in s], ["accord"])
        self.assertIn("Accordée par Nejib Koubaa", s[0]["texte"])

    def test_especes_sans_accord(self):
        s = R.signaux_depense(_l(), [], 200)
        self.assertEqual(([x["code"] for x in s], s[0]["niveau"]), (["sans_accord"], "rouge"))
        # Un responsable n'a pas besoin d'accord : seulement « montant élevé ».
        self.assertEqual([x["code"] for x in R.signaux_depense(_l(par_approbateur=True), [], 200)], ["eleve"])
        # Payée par chèque : pas d'espèces au-delà du seuil, « montant élevé » comme avant.
        self.assertEqual([x["code"] for x in R.signaux_depense(_l(especes=0), [], 200)], ["eleve"])
        self.assertEqual(R.signaux_depense(_l(montant=150, especes=150), [], 200), [])
        self.assertEqual(R.signaux_depense(_l(), [], 0), [])          # seuil 0 = accord désactivé


class TestCircuitSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if getattr(frappe.local, "db", None) is None:
            raise unittest.SkipTest("besoin d'un site")
        if getattr(frappe.db.commit, "__name__", "") != "<lambda>":
            raise unittest.SkipTest("à lancer avec frappe.db.commit neutralisé (essai sans trace)")
        cls.employe = frappe.db.get_value("Employee", {"employee_name": "Salma Ben Saïd", "status": "Active"}, "user_id")
        cls.resp = A.approbateurs()
        if not cls.employe or len(cls.resp) < 2:
            raise unittest.SkipTest("il faut l'employée et deux responsables")

    def tearDown(self):
        frappe.set_user("Administrator")

    def _notifs(self, user, nom):
        return frappe.get_all("Notification Log", filters={"for_user": user, "document_name": nom}, pluck="subject")

    def test_circuit(self):
        frappe.set_user(self.employe)
        e = A.etat_accords()
        self.assertFalse(e["dispense"])
        self.assertEqual(e["seuil"], 200)
        d = A.demander(350, "ESSAI pompe doseuse", "Fournisseur essai")
        self.assertIn(d["name"], [x.name for x in A.etat_accords()["demandes"]])

        # Un responsable décide : le demandeur ET l'autre responsable sont prévenus.
        frappe.set_user(self.resp[0])
        A.decider(d["name"], 1)
        self.assertTrue(self._notifs(self.resp[1], d["name"]), "l'autre responsable doit être prévenu")
        frappe.set_user(self.employe)
        accorde = next(x for x in A.etat_accords()["demandes"] if x.name == d["name"])
        self.assertEqual((accorde.statut, bool(accorde.valable_jusqu_au)), ("Accordé", True))
        # L'employé lit SA demande (le lien de la notification ne mène plus à « Pas permis »).
        self.assertTrue(frappe.has_permission("Accord Depense Caisse", "read", doc=d["name"], user=self.employe))

        # Retirer une demande en attente.
        d2 = A.demander(400, "ESSAI autre achat")
        A.retirer(d2["name"])
        self.assertEqual(frappe.db.get_value(A.DOCTYPE, d2["name"], "statut"), "Annulé")
        with self.assertRaises(frappe.ValidationError):
            A.retirer(d2["name"])

        # Une demande sans réponse depuis plus de 7 jours expire.
        d3 = A.demander(450, "ESSAI vieille demande")
        frappe.db.set_value(A.DOCTYPE, d3["name"], "demande_le", add_days(now_datetime(), -8))
        frappe.set_user(self.resp[0])
        self.assertNotIn(d3["name"], [x.name for x in A.a_decider()])
        self.assertEqual(frappe.db.get_value(A.DOCTYPE, d3["name"], "statut"), "Expiré")

        # Utilisé : l'écriture de la dépense cite l'accord.
        jv = frappe.get_all("Journal Entry", filters={"docstatus": 1, "cheque_no": ["like", "Dépense caisse%"]},
                            pluck="name", limit=1)
        if jv:
            A.utiliser(d["name"], jv[0])
            self.assertIn(d["name"], frappe.db.get_value("Journal Entry", jv[0], "user_remark"))
