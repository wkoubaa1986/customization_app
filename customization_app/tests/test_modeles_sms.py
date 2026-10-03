"""Modèles des SMS automatiques (03/10/2026) : le rendu, les textes d'origine reproduits à l'identique quand le réglage
est vide, le coût GSM-7, et l'aperçu. Règles pures d'abord (sans base), puis le réglage dans un savepoint."""
from __future__ import annotations

import unittest
from datetime import datetime

import frappe

from customization_app import modeles_sms as MS
from customization_app import rappel_rdv as R
from customization_app.Maintenance import relance_maintenance_sms as REL


def _tache(**kw):
    base = {"name": "Tache-1", "status": "Open", "custom_client": "CLI-1", "nom_client": "Ahmed Farhat",
            "custom_type_dintervention": "Entretien", "starts_on": datetime(2026, 9, 3, 9, 30),
            "custom_choix_du_staff": "HR-EMP-00002", "commande_client": None}
    base.update(kw)
    return frappe._dict(base)


class TestRendu(unittest.TestCase):
    def test_balises_et_lignes_vides(self):
        m = "Bonjour {nom},\nTechnicien : {tech}.\nCout {cout} DT\n{inconnue} reste\n{suite}"
        self.assertEqual(MS.rendre(m, {"nom": "Ali", "tech": "", "cout": 0, "suite": "A\nB"}),
                         "Bonjour Ali,\nCout 0 DT\n{inconnue} reste\nA\nB")               # tech vide → ligne retirée ; 0 = valeur
        self.assertEqual(MS.rendre("x {a}{b} y", {"a": "", "b": None}), "")                # toutes vides → ligne retirée
        self.assertEqual(MS.rendre("x {a}{b} y", {"a": "", "b": "1"}), "x 1 y")

    def test_sans_base_les_defauts(self):
        """Sans connexion (tests purs, scripts hors site), les textes d'origine ; avec la base, les mêmes clés."""
        db = getattr(frappe.local, "db", None)
        frappe.local.db = None
        try:
            self.assertEqual(MS.textes(), MS.DEFAUTS)
        finally:
            frappe.local.db = db
        self.assertEqual(set(MS.textes()), set(MS.DEFAUTS))


class TestTextesDorigine(unittest.TestCase):
    """Réglage vide = exactement ce que le code envoyait avant (chaîne pour chaîne)."""

    def setUp(self):
        self._vrai = R._technicien
        R._technicien = lambda t: ("Jamel Aloui", "51511918")
        self.T = dict(MS.DEFAUTS)

    def tearDown(self):
        R._technicien = self._vrai

    def test_rappel_rendez_vous(self):
        self.assertEqual(R.message_rendez_vous(_tache(), self.T),
                         "Bonsoir Ahmed Farhat,\nRappel : Entretien demain 03/09 vers 09:30. Horaire indicatif, il peut varier dans la journee.\n"
                         "Technicien : Jamel Aloui - 51511918.\nAqua World - 98511119")
        R._technicien = lambda t: ("Jamel Aloui", "")
        self.assertIn("\nTechnicien : Jamel Aloui.\n", R.message_rendez_vous(_tache(), self.T))
        R._technicien = lambda t: ("", "")
        self.assertEqual(R.message_rendez_vous(_tache(), self.T),
                         "Bonsoir Ahmed Farhat,\nRappel : Entretien demain 03/09 vers 09:30. Horaire indicatif, il peut varier dans la journee.\n"
                         "Aqua World - 98511119")

    def test_rappel_livraison_et_avis_aramex(self):
        self.assertEqual(R.message_livraison(_tache(custom_type_dintervention="Livraison"), self.T),
                         "Bonsoir Ahmed Farhat,\nVotre commande sera livree demain 03/09 vers 09:30. Horaire indicatif, il peut varier dans la journee.\n"
                         "Livreur : Jamel Aloui - 51511918.\nAqua World - 98511119")
        self.assertEqual(R.message_aramex(_tache(), "4123456", self.T),
                         "Bonsoir Ahmed Farhat,\nVotre commande a ete remise aujourd'hui a ARAMEX pour livraison.\nN de suivi : 4123456.\nAqua World - 98511119")
        self.assertEqual(R.message_aramex(_tache(), None, self.T),
                         "Bonsoir Ahmed Farhat,\nVotre commande a ete remise aujourd'hui a ARAMEX pour livraison.\n"
                         "Le numero de suivi vous sera communique.\nAqua World - 98511119")

    def test_relance_entretien_trois_cas(self):
        kw = dict(nom_client="Ali Ben Ali", appareil="votre osmoseur", cout=40.0, telephones="98 511 119", lien_boutique="https://aquaworld.tn")
        self.assertEqual(REL.message_relance(self.T, secteur="Secteur 1", lien_rdv="https://x/rdv", **kw),
                         "Bonjour Ali Ben Ali,\nRappel: La maintenance de votre osmoseur est arrivée à échéance.\n"
                         "Cout main-d'oeuvre: 40.0 DT. Ce tarif exclut les filtres de remplacement, facturés séparément selon entretien.\n"
                         "Prenez RDV en ligne: https://x/rdv\nOu appelez le 98 511 119.")
        self.assertEqual(REL.message_relance(self.T, secteur="Secteur 1", lien_rdv="", **dict(kw, cout=None)),
                         "Bonjour Ali Ben Ali,\nRappel: La maintenance de votre osmoseur est arrivée à échéance.\n"
                         "Pour planifier votre entretien, contactez-nous au 98 511 119.")      # coût inconnu : pas de ligne
        self.assertEqual(REL.message_relance(self.T, secteur="Hors Secteur", lien_rdv="", **kw),
                         "Bonjour Ali Ben Ali,\nRappel: La maintenance de votre osmoseur est arrivée à échéance.\n"
                         "Commandez vos filtres directement sur notre site :\nhttps://aquaworld.tn\n"
                         "Ou contactez-nous au 98 511 119 pour passer votre commande")         # hors secteur : jamais de coût
        m = REL.message_relance(self.T, secteur="Hors Secteur", lien_rdv="https://x/rdv", voeux="\nBonne annee.", **kw)
        self.assertTrue(m.startswith("Bonjour Ali Ben Ali,\nBonne annee.\n"))
        self.assertIn("Prenez RDV en ligne: https://x/rdv", m)                             # partenaire : le portail d'abord

    def test_un_modele_personnalise_prime(self):
        T = dict(self.T, rappel_rdv="RDV {date} {heure} - {technicien}\n{signature}", signature="AW")
        self.assertEqual(R.message_rendez_vous(_tache(), T), "RDV 03/09 09:30 - Jamel Aloui - 51511918\nAW")


class TestGSM(unittest.TestCase):
    def test_segments(self):
        a = MS.analyser("a" * 160)
        self.assertEqual((a["unicode"], a["segments"]), (False, 1))
        self.assertEqual(MS.analyser("a" * 161)["segments"], 2)
        self.assertEqual(MS.analyser("a" * 307)["segments"], 3)
        self.assertEqual(MS.analyser("")["segments"], 0)

    def test_unicode_et_translitteration(self):
        self.assertFalse(MS.analyser("Rappel : entretien demain — à 9h, ça marche ?")["unicode"])   # — et ç translittérés
        self.assertFalse(MS.analyser("Entretien prévu, échéance, où ?")["unicode"])                 # é è ù à : alphabet GSM
        a = MS.analyser("Rendez-vous ✅ confirmé")
        self.assertTrue(a["unicode"]) ; self.assertEqual(a["hors_gsm"], ["✅"])
        self.assertEqual(MS.analyser("a" * 71)["segments"], 1) ; self.assertEqual(MS.analyser("✅" + "a" * 70)["segments"], 2)
        self.assertEqual(MS.analyser("{a}")["longueur"], 5)                                           # { } comptent double

    def test_defauts_restent_en_gsm(self):
        for champ, texte in MS.DEFAUTS.items():
            self.assertFalse(MS.analyser(texte)["unicode"], champ)


class TestReglage(unittest.TestCase):
    """Le réglage enregistré prime, un champ vide retombe sur le texte d'origine ; l'aperçu rend tout."""

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("modeles_sms")
        if not frappe.db.exists("DocType", MS.DOCTYPE):
            self.skipTest("DocType absent (migrate non joué)")

    def tearDown(self):
        frappe.db.rollback(save_point="modeles_sms")

    def test_reglage_puis_apercu(self):
        frappe.db.set_single_value(MS.DOCTYPE, "signature", "AW TEST")
        frappe.db.set_single_value(MS.DOCTYPE, "rappel_rdv", "   ")
        T = MS.textes()
        self.assertEqual((T["signature"], T["rappel_rdv"]), ("AW TEST", MS.DEFAUTS["rappel_rdv"]))
        a = MS.apercu({"rappel_rdv": "Test {nom_client} {heure}\n{signature}"})
        self.assertEqual([m["champ"] for m in a], ["rappel_rdv", "rappel_livraison", "avis_aramex",
                                                   "relance_rdv_en_ligne", "relance_sans_lien", "relance_hors_secteur"])
        self.assertTrue(a[0]["texte"].startswith("Test ") and a[0]["texte"].endswith("\nAW TEST"), a[0]["texte"])
        self.assertTrue(all(m["analyse"]["segments"] >= 1 for m in a))
        self.assertIn("Prenez RDV en ligne:", a[3]["texte"]) ; self.assertIn("Commandez vos filtres", a[5]["texte"])
