"""Points de contrôle de la clôture de caisse (05/10/2026) : motif + commentaire obligatoires, dettes
avec leur montant quelle que soit la situation de la commande, tâches ouvertes sans commande.

Les classes « pures » tournent sans base ; `TestTachesSansCommande` écrit deux tâches en SQL brut
(aucun crochet, aucun SMS) dans un savepoint annulé à la fin.
"""
from __future__ import annotations

import unittest
from unittest import mock

from customization_app import caisse_cloture as CL
from customization_app import caisse_collecte as CC


def _commande(nom, task_open=False, tache_status=None, valide=True, aramex=False, dette=0.0, bl_brouillon=False):
    paiements = [{"mode": "Espèces", "amount": 100.0}]
    if dette:
        paiements.append({"mode": "Dette non payée", "amount": dette})
    return {"sales_order": nom, "customer": "Client " + nom, "task_open": task_open, "tache_status": tache_status,
            "is_validated": valide, "is_aramex": aramex, "payments": paiements,
            "tache_reference": ("Tache-" + nom) if tache_status else None, "intervention": "Entretien",
            "tache_employee": "Akram",
            "delivery_notes": [{"name": "BL-" + nom, "docstatus": 0, "grand_total": 50}] if bl_brouillon else []}


def _points(*commandes):
    return CL._controles({"employees": [{"orders": list(commandes)}], "anciens": {}})


class TestDettes(unittest.TestCase):

    def test_tache_ouverte_porte_le_montant_de_la_dette_sans_second_point(self):
        p = _points(_commande("SO1", task_open=True, tache_status="Open", dette=450))
        self.assertEqual([x["type"] for x in p], ["tache_ouverte"])
        self.assertIn("450,000 DT en dette", p[0]["libelle"])
        self.assertEqual(p[0]["montant"], 450)

    def test_commande_validee_tache_terminee_garde_son_libelle(self):
        p = _points(_commande("SO2", tache_status="Completed", dette=175.5))
        self.assertEqual([x["type"] for x in p], ["dette_hors_aramex"])
        self.assertTrue(p[0]["libelle"].startswith("Commande validée, tâche terminée, mais 175,500 DT en dette"))

    def test_dette_sans_tache_ou_tache_annulee_est_signalee(self):
        sans = _points(_commande("SO3", dette=80))
        annulee = _points(_commande("SO4", tache_status="Cancelled", dette=60))
        self.assertIn("commande sans tâche", sans[0]["libelle"])
        self.assertIn("tâche annulée", annulee[0]["libelle"])
        self.assertEqual((sans[0]["cle"], annulee[0]["montant"]), ("dette_hors_aramex:SO3", 60))

    def test_aramex_et_commande_sans_dette_ne_sont_pas_signalees(self):
        self.assertEqual(_points(_commande("SO5", tache_status="Completed", aramex=True, dette=300),
                                 _commande("SO6", tache_status="Completed")), [])

    def test_bl_brouillon_reste_bloquant(self):
        p = _points(_commande("SO7", tache_status="Completed", bl_brouillon=True))
        self.assertEqual([(x["type"], x["bloquant"]) for x in p], [("bl_non_valide", 1)])


class TestJustifications(unittest.TestCase):

    def setUp(self):
        self.points = [
            {"cle": "tache_ouverte:SO1", "type": "tache_ouverte", "bloquant": 0, "libelle": "Tâche ouverte — SO1"},
            {"cle": "dette_hors_aramex:SO2", "type": "dette_hors_aramex", "bloquant": 0, "libelle": "Dette SO2"},
            {"cle": "bl_non_valide:BL1", "type": "bl_non_valide", "bloquant": 1, "libelle": "BL1", "bl": "BL1"},
        ]
        for p in self.points:
            if not p["bloquant"]:
                p["motifs"] = CL.motifs_du_point(p)

    def test_motifs_propres_au_type(self):
        self.assertIn("Client absent ou injoignable", CL.motifs_du_point({"type": "tache_sans_commande"}))
        self.assertIn("Dette accordée par la direction (qui ?)", CL.motifs_du_point({"type": "dette_hors_aramex"}))
        self.assertEqual(CL.motifs_du_point({"type": "inconnu"}), ["Autre"])

    def test_motif_et_commentaire_valides_au_format_historique(self):
        texte, erreurs = CL.verifier_justifications(self.points, {
            "tache_ouverte:SO1": {"motif": "Client absent ou injoignable", "commentaire": "  appelé 2 fois,\n pas de réponse "},
            "dette_hors_aramex:SO2": {"motif": "Autre", "commentaire": "accord de Nejib, paiera le 10/10"}})
        self.assertEqual(erreurs, [])
        self.assertEqual(texte, "Tâche ouverte — SO1\n  → Client absent ou injoignable — appelé 2 fois, pas de réponse\n"
                                "Dette SO2\n  → Autre — accord de Nejib, paiera le 10/10")
        # La réouverture relit « motif — commentaire » par libellé.
        self.assertEqual(CC.justifications_depuis_controles(texte)["Dette SO2"], "Autre — accord de Nejib, paiera le 10/10")
        self.assertEqual([x["libelle"] for x in CC.controles_en_liste(texte)], ["Tâche ouverte — SO1", "Dette SO2"])

    def test_refus(self):
        _, erreurs = CL.verifier_justifications(self.points, {
            "tache_ouverte:SO1": "rs",                                                   # ancienne page en cache
            "dette_hors_aramex:SO2": {"motif": "Client absent ou injoignable", "commentaire": "x" * 20}})   # motif d'un autre type
        self.assertEqual(len(erreurs), 2)
        self.assertIn("Ctrl+Maj+R", erreurs[0])
        _, erreurs = CL.verifier_justifications(self.points, {
            "tache_ouverte:SO1": {"motif": "Autre", "commentaire": "rs rs"},              # trop court
            "dette_hors_aramex:SO2": {"motif": "", "commentaire": "un commentaire assez long"}})
        self.assertEqual(len(erreurs), 2)
        self.assertIn("trop court", erreurs[0])
        self.assertIn("choisissez un motif", erreurs[1])

    def test_les_bloquants_ne_se_justifient_pas(self):
        texte, erreurs = CL.verifier_justifications([self.points[2]], {})
        self.assertEqual((texte, erreurs), ("", []))


class TestCablage(unittest.TestCase):

    def test_statut_conteste_est_en_attente_et_dans_le_doctype(self):
        import json
        import os
        self.assertIn(CC.STATUT_JUSTIF, CC.STATUTS_EN_ATTENTE)
        base = os.path.dirname(CL.__file__)
        with open(os.path.join(base, "customize_erpnext", "doctype", "cloture_caisse", "cloture_caisse.json"), encoding="utf-8") as fh:
            statut = next(f for f in json.load(fh)["fields"] if f["fieldname"] == "statut")
        self.assertIn(CC.STATUT_JUSTIF, statut["options"].split("\n"))

    def test_page_js_envoie_motif_et_commentaire(self):
        import os
        base = os.path.dirname(CL.__file__)
        with open(os.path.join(base, "customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.js"), encoding="utf-8") as fh:
            js = fh.read()
        for attendu in ("rcj-just-motif", "{ motif, commentaire }", "contester_justifications", "rcj_html_controles_justifies",
                        "data-rouvrir-justifs"):
            self.assertIn(attendu, js)


class TestTachesSansCommande(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import frappe
        if getattr(frappe.local, "db", None) is None:
            raise unittest.SkipTest("besoin d'un site : bench --site … run-tests --module … --skip-before-tests")

    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("taches_sans_commande")

    def tearDown(self):
        import frappe
        frappe.db.rollback(save_point="taches_sans_commande")

    def _tache(self, nom, employe, type_i, status="Open", commande=None, heure="10:30:00", objet="ESSAI CAISSE"):
        import frappe
        frappe.db.sql("""INSERT INTO `tabTache de travail` (name, owner, modified_by, creation, modified, docstatus,
                             status, custom_choix_du_staff, starts_on, ends_on, custom_type_dintervention, subject, commande_client)
                         VALUES (%s, 'Administrator', 'Administrator', NOW(), NOW(), 0, %s, %s, %s, %s, %s, %s, %s)""",
                      (nom, status, employe, "2031-03-04 " + heure, "2031-03-04 23:00:00", type_i, objet, commande))

    def test_tache_ouverte_sans_commande_signalee_a_sa_caisse_et_a_la_globale(self):
        import frappe
        from customization_app.rapport_caisse_journaliere import COMPANY
        emp = frappe.get_all("Employee", filters={"company": COMPANY, "status": "Active"},
                             fields=["name", "employee_name"], order_by="name", limit=1)
        if not emp:
            self.skipTest("aucun employé actif")
        emp = emp[0]
        self._tache("ESSAI-CAISSE-1", emp.name, "Autre", objet="Préparer la machine du client pour demain matin, livrée par Aramex")
        self._tache("ESSAI-CAISSE-2", emp.name, "Jour de récupération")          # hors journée
        self._tache("ESSAI-CAISSE-3", emp.name, "Visite", status="Completed")     # fermée
        self._tache("ESSAI-CAISSE-4", emp.name, "Entretien", commande="SAL-ORD-ESSAI")   # suivie par le rapport
        with mock.patch.object(CL, "_exclusions", return_value=(set(), set())):
            sa_caisse = [p for p in CL._taches_sans_commande(emp.employee_name, "2031-03-04") if p["tache"].startswith("ESSAI-")]
            globale = [p for p in CL._taches_sans_commande(CL.CAISSE_GLOBALE, "2031-03-04") if p["tache"].startswith("ESSAI-")]
            autre = [p for p in CL._taches_sans_commande("Caisse inexistante", "2031-03-04") if p["tache"].startswith("ESSAI-")]
        self.assertEqual([p["tache"] for p in sa_caisse], ["ESSAI-CAISSE-1"])
        self.assertEqual([p["tache"] for p in globale], ["ESSAI-CAISSE-1"])
        self.assertEqual(autre, [])
        p = sa_caisse[0]
        self.assertEqual((p["cle"], p["type"], p["bloquant"]), ("tache_sans_commande:ESSAI-CAISSE-1", "tache_sans_commande", 0))
        self.assertIn("(Autre, %s, 10:30)" % emp.employee_name, p["libelle"])
        self.assertIn("…", p["libelle"])                                          # objet tronqué à 60 caractères
        # Et il entre dans les points de la clôture, avec ses motifs.
        with mock.patch.object(CL, "_exclusions", return_value=(set(), set())):
            points = CL.points_controle(emp.employee_name, "2031-03-04", {"employees": [], "anciens": {}})
        essai = [x for x in points if x.get("tache") == "ESSAI-CAISSE-1"]
        self.assertEqual(essai[0]["motifs"], CL.MOTIFS["tache"])

