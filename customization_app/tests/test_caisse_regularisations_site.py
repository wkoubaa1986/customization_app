"""Régularisations, inventaire, apports — essai SUR LE SITE, sans trace (commit neutralisé, tout annulé à la fin).
Joue contre la copie de prod : le saut de chaîne CLO-1055 de Nejib Koubaa (20/10/2026). Sauté s'il n'y est plus."""
import unittest
from unittest import mock

import frappe

from customization_app import argent_liquide as A
from customization_app import caisse_cloture as CL
from customization_app import caisse_regularisations as R

CLE = "saut:CLO-1055"


class TestRegularisationsSite(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if not frappe.db.table_exists(R.DOCTYPE):
            self.skipTest("migration pas passée")
        frappe.db.savepoint("regul")
        self.commit = mock.patch.object(frappe.db, "commit", lambda *a, **k: None)
        self.commit.start()
        frappe.db.delete(R.DOCTYPE)                       # dans le point de reprise : la carte sans aucune décision
        self.s = A.situation()
        if CLE not in {a["cle"] for a in self.s["a_regulariser"]}:
            self.tearDown()
            self.skipTest("le saut CLO-1055 n'est plus sur ce site")
        self.titulaire = frappe.db.get_single_value("Config Caisse", "responsable")

    def tearDown(self):
        frappe.local.form_dict.pop("cmd", None)
        frappe.set_user("Administrator")
        self.commit.stop()
        frappe.db.rollback(save_point="regul")

    def _nejib(self, s):
        return next(e for e in s["employes"] if e["caisse"] == s["principal"]["caisse"])

    def test_la_carte_s_equilibre_et_les_versements_en_banque_ne_sont_pas_des_erreurs(self):
        self.assertEqual(self.s["rapprochement"]["reste"], 0.0)
        self.assertTrue(all(st["reste"] == 0.0 for st in self.s["flux"]["stocks"]))
        self.assertFalse([a for a in self.s["a_regulariser"] if a["nature"] == "Compte inattendu"])
        self.assertTrue(all("TAWFIR" in v["compte"] or v["compte"] for v in self.s["rapprochement"]["verses_banque"]))

    def test_regulariser_le_saut_le_retire_de_la_carte(self):
        avant = self._nejib(self.s)["total"]
        r = R.regulariser(CLE, "retirer", "Transfert du coffre du 19/10 : cet argent a payé les billets d'avion.")
        self.assertEqual(r["faites"][0]["effet"], -5293.0)
        s = A.situation()
        self.assertEqual(round(self._nejib(s)["total"] - avant, 3), -5293.0)
        self.assertNotIn(CLE, {a["cle"] for a in s["a_regulariser"]})
        self.assertEqual(s["principal"]["sauts"], [])
        self.assertEqual((s["rapprochement"]["termes"]["regularisations"], s["rapprochement"]["reste"]), (-5293.0, 0.0))
        self.assertEqual(s["regularisations"][0]["cle"], CLE)
        self.assertRaises(frappe.ValidationError, R.regulariser, CLE, "retirer", "une deuxième fois, refusée")
        # Annuler : l'anomalie revient.
        R.annuler(s["regularisations"][0]["name"])
        self.assertIn(CLE, {a["cle"] for a in A.situation()["a_regulariser"]})

    def test_motif_obligatoire_et_reserve_a_la_direction(self):
        self.assertRaises(frappe.ValidationError, R.regulariser, CLE, "retirer", "court")
        self.assertRaises(frappe.ValidationError, R.regulariser, CLE, "inconnue", "un motif assez long pour passer")
        frappe.set_user(self.titulaire)
        self.assertRaises(frappe.PermissionError, R.regulariser, CLE, "retirer", "le collecteur ne décide pas pour lui-même")

    def test_un_lot_de_paiements_antidates(self):
        cles = [a["cle"] for a in self.s["a_regulariser"] if a["nature"] == "Paiement antidaté"]
        if len(cles) < 2:
            self.skipTest("moins de deux paiements antidatés")
        R.regulariser(frappe.as_json(cles), "vu", "Encaissés avant le départ des caisses, vérifiés un par un.")
        restantes = {a["cle"] for a in A.situation()["a_regulariser"]}
        self.assertFalse(restantes & set(cles))

    def test_l_inventaire_remet_la_carte_au_reel_et_absorbe_le_passe(self):
        attendu = self.s["principal"]["especes"]
        frappe.set_user(self.titulaire)                       # le collecteur compte lui-même
        r = R.inventorier("2500,500", "Compté avec Wassim le soir, billets et pièces.")
        self.assertEqual((r["compte"], r["theorique"], r["ecart"]), (2500.5, attendu, round(2500.5 - attendu, 3)))
        frappe.set_user("Administrator")
        s = A.situation()
        self.assertEqual(s["principal"]["especes"], 2500.5)
        self.assertEqual(s["principal"]["inventaire"]["compte"], 2500.5)
        self.assertEqual(s["rapprochement"]["reste"], 0.0)
        # Ses anomalies passées (saut, écritures « Achat divers ») sont absorbées ; les antidatés restent à voir.
        self.assertFalse([a for a in s["a_regulariser"] if a.get("caisse") == s["principal"]["caisse"]])
        self.assertRaises(frappe.ValidationError, R.inventorier, "-5", "négatif")
        # Le coffre : pour mémoire, sans effet sur la carte.
        c = R.inventorier(str(self.s["totaux"]["sabah"] - 1000), "Coffre compté : il manque mille dinars.", coffre=1)
        self.assertEqual(c["ecart"], -1000.0)
        s2 = A.situation()
        self.assertEqual((s2["inventaire_coffre"]["ecart"], s2["totaux"]["sabah"], s2["principal"]["especes"]),
                         (-1000.0, self.s["totaux"]["sabah"], 2500.5))

    def _transfert_du_coffre(self, montant):
        je = frappe.get_doc({"doctype": "Journal Entry", "posting_date": frappe.utils.nowdate(), "company": frappe.defaults.get_global_default("company"),
                             "cheque_no": "Transfert Compte Sabah vers Espèce", "cheque_date": frappe.utils.nowdate(),
                             "accounts": [{"account": A.COMPTE_ESPECES, "debit_in_account_currency": montant},
                                          {"account": A.COMPTE_SABAH, "credit_in_account_currency": montant}]})
        je.flags.ignore_permissions = True
        je.insert()
        je.submit()
        return je.name

    def test_un_apport_du_coffre_entre_dans_la_journee_de_caisse(self):
        frappe.db.sql("UPDATE `tabSingles` SET value = %s WHERE doctype = 'Config Caisse' AND field = 'apports_depuis'",
                      (frappe.utils.nowdate(),))
        caisse = self.s["principal"]["caisse"]
        collecte, nejib = self.s["principal"]["collecte"], self._nejib(self.s)["total"]
        avant = CL._mesures(caisse, frappe.utils.nowdate())
        frappe.set_user(self.titulaire)
        nom = self._transfert_du_coffre(500)
        frappe.set_user("Administrator")
        m = CL._mesures(caisse, frappe.utils.nowdate())
        self.assertEqual((avant["apports_especes"], m["apports_especes"], [a["name"] for a in m["apports"]]), (0.0, 500.0, [nom]))
        s = A.situation()
        self.assertEqual(s["principal"]["collecte"], collecte)                   # plus compté chez le collecteur
        self.assertEqual(round(self._nejib(s)["total"] - nejib, 3), 500.0)       # dans sa caisse, pas encore compté
        self.assertEqual(s["rapprochement"]["reste"], 0.0)
        # Avant la mise en route (historique) : rien ne change, l'apport du 19/10 reste chez le collecteur.
        self.assertFalse([a for a in s["apports"] if a["date"] < frappe.utils.nowdate()])

    def test_refus_de_l_entree_manuelle(self):
        je = frappe.get_doc({"doctype": "Journal Entry", "posting_date": frappe.utils.nowdate(),
                             "accounts": [{"account": A.COMPTE_ESPECES, "debit_in_account_currency": 80},
                                          {"account": "Compte temporaire - compte  d'overture - A&S", "credit_in_account_currency": 80}]})
        R.controler_ecriture_especes(je)                                     # un outil (pas le formulaire) : rien
        frappe.local.form_dict["cmd"] = "frappe.desk.form.save.savedocs"
        R.controler_ecriture_especes(je)                                     # la direction garde la main
        frappe.set_user(self.titulaire)
        frappe.local.form_dict["cmd"] = "frappe.desk.form.save.savedocs"     # set_user vide la requête
        self.assertRaises(frappe.ValidationError, R.controler_ecriture_especes, je)
        je.accounts[1].account = A.COMPTE_SABAH                              # un transfert du coffre reste permis
        R.controler_ecriture_especes(je)


if __name__ == "__main__":
    unittest.main()
