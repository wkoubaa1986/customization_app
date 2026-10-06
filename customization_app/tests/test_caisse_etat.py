"""État des caisses et justifications recopiées (06/10/2026).

Règle utilisateur : une caisse est À COMPTER et À COLLECTER dès la moindre action dans le rapport du
jour — encaissement quel que soit le mode, dette comprise, commande, dépense — et le collecteur voit,
caisse par caisse et jour par jour depuis la date de départ, ce qui n'a été ni compté ni collecté.
Les justifications des points de contrôle deviennent des commentaires sur la commande / la tâche.

Logique pure : tourne sans site. `TestPublication` (site) écrit dans un savepoint annulé à la fin.
"""
from __future__ import annotations

import unittest

import frappe

from customization_app import caisse_cloture as CL
from customization_app import caisse_collecte as CC


def _rapport(employees=(), recap=(), anciens=(), depenses=()):
    return {"employees": list(employees), "recap": {"par_employe": list(recap)},
            "anciens": {"paiements": list(anciens)}, "depenses": {"lignes": list(depenses)}}


def _cmd(nom, tache_status="Completed", paiements=()):
    return {"sales_order": nom, "tache_status": tache_status, "payments": list(paiements)}


class TestActivite(unittest.TestCase):

    def test_dette_seule_rend_la_caisse_active(self):
        act = CC.activite_par_caisse(_rapport(recap=[
            {"employe": "Salma", "total": 441, "par_mode": {"Dette non payée": 441.0, "Espèces": 0.0}}]))
        self.assertEqual(act["Salma"]["par_mode"], {"Dette non payée": 441.0})
        self.assertTrue(CC.caisse_active(act["Salma"]))

    def test_commande_sans_paiement_et_depense_seule_comptent(self):
        act = CC.activite_par_caisse(_rapport(
            employees=[{"employe": "Akram", "orders": [_cmd("SO1", tache_status="Open")]}],
            depenses=[{"saisi_par": "Nejib", "montant": 55.0}, {"saisi_par": "Nejib", "montant": 2446.245}]))
        self.assertEqual(act["Akram"]["commandes"], 1)
        self.assertEqual((act["Nejib"]["nb_depenses"], act["Nejib"]["depenses"]), (2, 2501.245))
        self.assertTrue(CC.caisse_active(act["Akram"]) and CC.caisse_active(act["Nejib"]))

    def test_tache_annulee_sans_paiement_du_jour_n_est_pas_une_action(self):
        annulee = _cmd("SO2", tache_status="Cancelled", paiements=[{"mode": "Espèces", "hors_periode": True}])
        avec_dette = _cmd("SO3", tache_status="Cancelled", paiements=[{"mode": "Dette non payée", "hors_periode": False}])
        payee = _cmd("SO4", tache_status="Cancelled", paiements=[{"mode": "Espèces", "hors_periode": False}])
        self.assertFalse(CC.commande_active(annulee))
        self.assertTrue(CC.commande_active(avec_dette))
        self.assertTrue(CC.commande_active(payee))
        act = CC.activite_par_caisse(_rapport(employees=[{"employe": "Hedi", "orders": [annulee]}]))
        self.assertNotIn("Hedi", act)

    def test_ancien_paiement_meme_exclu_compte_et_saisi_par_inconnu_ignore(self):
        act = CC.activite_par_caisse(_rapport(anciens=[{"saisi_par": "Jamel", "exclu": 1}, {"saisi_par": None}]))
        self.assertEqual(list(act), ["Jamel"])
        self.assertEqual(act["Jamel"]["anciens"], 1)

    def test_resume_lisible(self):
        txt = CC.resume_activite({"commandes": 2, "par_mode": {"Dette non payée": 441.0, "Espèces": 15.0},
                                  "anciens": 0, "depenses": 55.0, "nb_depenses": 1})
        self.assertEqual(txt, "15,000 DT Espèces · 441,000 DT Dette non payée · 2 commande(s) · 1 dépense(s) 55,000 DT")
        self.assertEqual(CC.resume_activite(None), "aucune action")


class TestAvance(unittest.TestCase):
    """L'avance, c'est tout mode sauf la dette et la perte de non paiement (06/10/2026)."""
    JOUR = "2026-10-06"

    def _pe(self, mode, quand="2026-10-01"):
        return {"mode_of_payment": mode, "posting_date": quand, "creation_date": quand}

    def test_paiement_anterieur_est_une_avance(self):
        from customization_app.rapport_caisse_journaliere import _hors_periode
        for mode in ("Espèces", "Chèque", "Virement", "Traite bancaire LC", "Carte de crédit"):
            self.assertTrue(_hors_periode(self._pe(mode), self.JOUR, self.JOUR), mode)

    def test_dette_et_perte_ne_sont_jamais_des_avances(self):
        from customization_app.rapport_caisse_journaliere import _hors_periode
        self.assertFalse(_hors_periode(self._pe("Dette non payée"), self.JOUR, self.JOUR))
        self.assertFalse(_hors_periode(self._pe("Perte de paiement"), self.JOUR, self.JOUR))

    def test_paiement_du_jour_n_est_pas_une_avance(self):
        from customization_app.rapport_caisse_journaliere import _hors_periode
        self.assertFalse(_hors_periode(self._pe("Espèces", self.JOUR), self.JOUR, self.JOUR))


class TestEtatCellule(unittest.TestCase):
    ACT = {"commandes": 1, "par_mode": {}, "anciens": 0, "depenses": 0, "nb_depenses": 0}

    def test_cloture_prime(self):
        def etat(cl):
            return CC.etat_de_cellule("2026-10-05", "2026-10-06", None, cl, None)
        self.assertEqual(etat({"docstatus": 1, "statut": "Validée"}), "validee")
        self.assertEqual(etat({"docstatus": 0, "statut": CC.STATUT_ECART}), "ecart")
        self.assertEqual(etat({"docstatus": 0, "statut": CC.STATUT_JUSTIF}), "justif")
        self.assertEqual(etat({"docstatus": 0, "statut": CC.STATUT_A_COLLECTER}), "a_collecter")

    def test_action_sans_comptage(self):
        self.assertEqual(CC.etat_de_cellule("2026-10-05", "2026-10-06", "2026-10-05", None, self.ACT), "non_comptee")
        self.assertEqual(CC.etat_de_cellule("2026-10-06", "2026-10-06", "2026-10-05", None, self.ACT), "a_compter")
        self.assertEqual(CC.etat_de_cellule("2026-10-05", "2026-10-06", None, None, None), "vide")

    def test_avant_la_date_de_depart_de_la_caisse(self):
        self.assertEqual(CC.etat_de_cellule("2026-10-04", "2026-10-06", "2026-10-05", None, self.ACT), "avant")

    def test_rien_ne_compte_avant_la_date_de_depart_meme_un_comptage(self):
        cl = {"docstatus": 0, "statut": CC.STATUT_A_COLLECTER}
        self.assertEqual(CC.etat_de_cellule("2026-10-04", "2026-10-06", "2026-10-05", cl, self.ACT), "avant")
        self.assertEqual(CC.etat_de_cellule("2026-10-05", "2026-10-06", "2026-10-05", cl, self.ACT), "a_collecter")


class TestBornes(unittest.TestCase):

    def test_la_plus_ancienne_date_de_depart(self):
        debut, tronque = CC.bornes_etat("2026-10-06", ["2026-10-05", "2026-10-03", None])
        self.assertEqual((str(debut), tronque), ("2026-10-03", False))

    def test_sans_date_de_depart_la_derniere_semaine(self):
        self.assertEqual(str(CC.bornes_etat("2026-10-06", [None])[0]), "2026-09-30")

    def test_trop_ancien_tronque(self):
        debut, tronque = CC.bornes_etat("2026-10-06", ["2026-01-01"], jours_max=10)
        self.assertEqual((str(debut), tronque), ("2026-09-27", True))

    def test_date_de_depart_future(self):
        self.assertEqual(str(CC.bornes_etat("2026-10-06", ["2026-10-10"])[0]), "2026-10-06")


class TestCommentaire(unittest.TestCase):

    def test_documents_du_point(self):
        self.assertEqual(CL.documents_du_point({"commande": "SO1", "tache": "Tache-1", "paiement": None}),
                         [("Sales Order", "SO1"), ("Tache de travail", "Tache-1")])
        self.assertEqual(CL.documents_du_point({"type": "caisse_non_collectee", "caisse": "X"}), [])

    def test_texte_justification_d_abord_marque_et_echappement(self):
        txt = CL.texte_commentaire({"type": "dette_hors_aramex", "montant": 441},
                                   {"motif": "Litige avec le client", "commentaire": "  il dit <b>non</b>   au prix "},
                                   "CLO-0901", "Salma", "2026-10-05", "Salma Ben Saïd")
        self.assertTrue(txt.startswith(CL.MARQUE_COMMENTAIRE + "05/10/2026 — Dette hors Aramex (441,000 DT) : "))
        self.assertIn("<b>Litige avec le client</b> — il dit &lt;b&gt;non&lt;/b&gt; au prix", txt)
        self.assertTrue(txt.endswith("caisse Salma (CLO-0901)</small>"))

    def test_points_portent_la_tache(self):
        o = {"sales_order": "SO1", "customer": "C", "task_open": True, "tache_status": "Open", "is_validated": 1,
             "is_aramex": False, "tache_reference": "Tache-9", "payments": [], "delivery_notes": []}
        p = CL._controles({"employees": [{"orders": [o]}], "anciens": {}})
        self.assertEqual(CL.documents_du_point(p[0]), [("Sales Order", "SO1"), ("Tache de travail", "Tache-9")])


class TestCablage(unittest.TestCase):

    def test_page_branche_l_etat(self):
        import inspect
        import os

        import customization_app
        base = os.path.join(os.path.dirname(inspect.getfile(customization_app)),
                            "customize_erpnext", "page", "caisse_journaliere")
        with open(os.path.join(base, "caisse_journaliere.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(base, "caisse_journaliere.html"), encoding="utf-8") as fh:
            html = fh.read()
        for morceau in ('".etat_caisses"', "caisse_collecte.etat_caisses", "rcj_etat_caisses(this)", "rcj_lier_documents"):
            self.assertIn(morceau, js, morceau)
        self.assertIn('id="rcj-btn-etat"', html)
        # Le gabarit d'une Page Desk devient une chaîne JS entre apostrophes : jamais d'apostrophe droite.
        self.assertNotIn("'", html)


class TestPublication(unittest.TestCase):
    """Sur le site : les justifications deviennent des commentaires, retirés à la réouverture."""

    @classmethod
    def setUpClass(cls):
        if getattr(frappe.local, "db", None) is None:
            raise unittest.SkipTest("besoin d'un site : bench --site … run-tests --module … --skip-before-tests")

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("test_caisse_etat")
        self.so = frappe.db.get_value("Sales Order", {"docstatus": 1}, "name")
        self.tache = frappe.db.get_value("Tache de travail", {}, "name")

    def tearDown(self):
        frappe.db.rollback(save_point="test_caisse_etat")

    def _commentaires(self, nom):
        return frappe.get_all("Comment", filters={"content": ["like", "%%(%s)%%" % nom], "comment_type": "Comment"},
                              fields=["reference_doctype", "reference_name", "content"])

    def test_publier_puis_retirer(self):
        doc = frappe._dict(name="CLO-TEST-ETAT", caisse="Akram", date_cloture="2026-10-05")
        points = [{"cle": "dette_hors_aramex:" + self.so, "type": "dette_hors_aramex", "bloquant": 0,
                   "commande": self.so, "tache": self.tache, "montant": 80},
                  {"cle": "bl_non_valide:X", "type": "bl_non_valide", "bloquant": 1, "commande": self.so},
                  {"cle": "tache_sans_commande:Tache-INEXISTANTE", "type": "tache_sans_commande", "bloquant": 0,
                   "tache": "Tache-INEXISTANTE"}]
        justifs = {"dette_hors_aramex:" + self.so: {"motif": "Litige avec le client", "commentaire": "il revient vendredi"},
                   "tache_sans_commande:Tache-INEXISTANTE": {"motif": "Autre", "commentaire": "document disparu"}}
        self.assertEqual(CL.publier_justifications(doc, points, justifs), 2)
        poses = self._commentaires(doc.name)
        self.assertEqual(sorted(c.reference_doctype for c in poses), ["Sales Order", "Tache de travail"])
        # La pastille 💬 de la commande montre la justification.
        if frappe.db.has_column("Sales Order", "custom_dernier_commentaire"):
            self.assertIn("Litige avec le client",
                          frappe.db.get_value("Sales Order", self.so, "custom_dernier_commentaire"))
        CL.retirer_justifications(doc.name)
        self.assertEqual(self._commentaires(doc.name), [])

    def test_caisse_globale_ne_publie_pas(self):
        doc = frappe._dict(name="CLO-TEST-GLOB", caisse=CL.CAISSE_GLOBALE, date_cloture="2026-10-05")
        p = [{"cle": "k", "type": "tache_ouverte", "bloquant": 0, "commande": self.so}]
        self.assertEqual(CL.publier_justifications(doc, p, {"k": {"motif": "Autre", "commentaire": "xxxxxxxxxxx"}}), 0)


if __name__ == "__main__":
    unittest.main()
