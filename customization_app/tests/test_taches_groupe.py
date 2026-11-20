"""« Gérer les interventions » (customization_app.taches_groupe) — demande du 18/11/2026 : choix multiple sur le
type et l'employé, seuls les employés actifs dans la liste, et une action « Transférer » d'un employé à un autre.
Refonte du 20/11/2026 : l'action en haut, les tâches par jour, deux agendas côte à côte pour le transfert, et une
tâche « Employé fixe » ne se transfère pas."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import taches_groupe as T

BASE = os.path.dirname(inspect.getfile(customization_app))


class TestChoixMultiple(unittest.TestCase):
    def test_une_liste_un_json_ou_une_seule_valeur(self):
        self.assertEqual(T.plusieurs('["Entretien","Installation"]'), ["Entretien", "Installation"])
        self.assertEqual(T.plusieurs(["HR-EMP-00001", "HR-EMP-00013"]), ["HR-EMP-00001", "HR-EMP-00013"])
        self.assertEqual(T.plusieurs("Entretien"), ["Entretien"])                 # ancien appel : une seule valeur

    def test_rien_de_choisi_ne_filtre_pas(self):
        for vide in (None, "", "[]", [], [""]):
            self.assertEqual(T.plusieurs(vide), [])

    def test_sans_doublon(self):
        self.assertEqual(T.plusieurs(["A", "B", "A"]), ["A", "B"])

    def test_la_recherche_filtre_sur_plusieurs_valeurs(self):
        source = inspect.getsource(T.rechercher)
        self.assertIn('filtres["custom_choix_du_staff"] = ["in", employes]', source)
        self.assertIn('filtres["custom_type_dintervention"] = ["in", types]', source)


class TestEmployesActifs(unittest.TestCase):
    def test_la_liste_ne_propose_que_les_employes_actifs(self):
        source = inspect.getsource(T.get_filtres)
        self.assertIn('frappe.get_all("Employee", filters={"status": "Active"}', source)
        self.assertNotIn("SELECT DISTINCT t.custom_choix_du_staff", source)       # avant : quiconque avait eu une tâche

    def test_on_ne_transfere_qu_a_un_employe_actif(self):
        source = inspect.getsource(T.transferer)
        self.assertIn('if cible.status != "Active":', source)


class TestTransfert(unittest.TestCase):
    def test_une_tache_ouverte_d_un_autre_employe_se_transfere(self):
        self.assertIsNone(T.refus_transfert("Open", "Entretien", "HR-EMP-00001", "HR-EMP-00013"))
        self.assertIsNone(T.refus_transfert("Open", "Installation", None, "HR-EMP-00013"))   # tâche sans employé

    def test_ce_qui_ne_se_transfere_pas(self):
        self.assertEqual(T.refus_transfert("Completed", "Entretien", "A", "B"), "déjà terminée")
        self.assertEqual(T.refus_transfert("Cancelled", "Entretien", "A", "B"), "déjà annulée")
        self.assertEqual(T.refus_transfert("Open", "Entretien", "B", "B"), "déjà confiée à cet employé")
        for absence in ("Congé", "Jour de récupération"):
            self.assertIn("ne se transfère pas", T.refus_transfert("Open", absence, "A", "B"))

    def test_une_tache_a_employe_fixe_reste_a_son_employe(self):
        """20/11/2026 : « lorsque la tâche a employé fixe, garde-la fixe, non transférable »."""
        self.assertEqual(T.refus_transfert("Open", "Entretien", "A", "B", fixe=1), "employé fixe : ne se transfère pas")
        self.assertIsNone(T.refus_transfert("Open", "Entretien", "A", "B", fixe=0))
        self.assertEqual(T.refus_transfert("Completed", "Entretien", "A", "B", fixe=1), "déjà terminée")
        self.assertIn('fixe=cint(doc.get("custom_employe_fixe"))', inspect.getsource(T.transferer))

    def test_le_transfert_refuse_vraiment_une_tache_a_employe_fixe(self):
        import frappe
        if not getattr(frappe.local, "site", None) or not frappe.db:
            self.skipTest("sans site")
        actifs = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=2)
        if len(actifs) < 2:
            self.skipTest("moins de deux employés actifs")
        frappe.db.savepoint("gt_fixe")
        try:
            def tache(fixe):
                t = frappe.get_doc({"doctype": T.DOCTYPE, "custom_type_dintervention": "Autre", "custom_choix_du_staff": actifs[0],
                                    "starts_on": "2031-03-03 09:00:00", "subject": "essai transfert", "status": "Open",
                                    "custom_employe_fixe": fixe})
                t.flags.ignore_permissions = True
                t.insert()
                return t.name
            fixe, libre = tache(1), tache(0)
            lignes = {l["tache"]: l for l in T.rechercher("2031-03-03", "2031-03-03", employe=[actifs[0]], statut="")["lignes"]}
            self.assertEqual((lignes[fixe]["fixe"], lignes[libre]["fixe"]), (1, 0))
            self.assertTrue(lignes[libre]["fin"].startswith("2031-03-03 "))
            commit = frappe.db.commit
            frappe.db.commit = lambda *a, **k: None
            try:
                r = T.transferer([fixe, libre], vers=actifs[1])
            finally:
                frappe.db.commit = commit
            etats = {x["tache"]: x["etat"] for x in r["resultats"]}
            self.assertEqual(etats[fixe], "employé fixe : ne se transfère pas")
            self.assertEqual(r["transferees"], [libre])
            self.assertEqual(frappe.db.get_value(T.DOCTYPE, fixe, "custom_choix_du_staff"), actifs[0])
            self.assertEqual(frappe.db.get_value(T.DOCTYPE, libre, "custom_choix_du_staff"), actifs[1])
        finally:
            frappe.db.rollback(save_point="gt_fixe")

    def test_un_employe_parti_reste_une_origine_de_transfert(self):
        import frappe
        if not getattr(frappe.local, "site", None) or not frappe.db:
            self.skipTest("sans site")
        f = T.get_filtres()
        actifs = {x["valeur"] for x in f["employes"]}
        for a in f["anciens"]:
            self.assertNotIn(a["valeur"], actifs)                                # jamais proposé comme destinataire
            self.assertGreater(a["ouvertes"], 0)
            self.assertLessEqual(a["du"], a["au"])

    def test_le_transfert_passe_par_la_sauvegarde_et_laisse_une_trace(self):
        source = inspect.getsource(T.transferer)
        self.assertIn("_ecriture()", source)
        self.assertIn("doc.save()", source)                                       # hooks de la commande liée, nom affiché
        self.assertIn("🔁 Transférée par {0} : {1} → {2}", source)
        self.assertIn("_prevenir(faites, modele, sujet, sms, email)", source)     # message vide = aucun envoi
        self.assertNotIn("starts_on =", source)                                   # le créneau ne bouge pas


class TestEcran(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(BASE, "public", "js", "taches_gestion_groupe.js"), encoding="utf-8") as f:
            self.js = f.read()

    def test_les_trois_actions(self):
        self.assertIn('const ACTIONS = { "Décaler": "decaler", "Transférer": "transferer", "Annuler": "annuler" };', self.js)
        self.assertIn('method: API + "." + action', self.js)
        self.assertIn('data-champ="vers"', self.js)
        self.assertIn('action === "transferer" && !vers', self.js)                # pas de transfert sans destinataire
        # l'action et ses réglages sont en haut, avant la liste ; le message est replié en bas
        self.assertLess(self.js.index('fieldname: "tete"'), self.js.index('fieldname: "resultats"'))
        self.assertLess(self.js.index('fieldname: "resultats"'), self.js.index('fieldname: "sec_message", collapsible: 1'))

    def test_type_et_employe_a_choix_multiple(self):
        self.assertIn("employe: JSON.stringify(Array.from(employes))", self.js)
        self.assertIn("type_intervention: JSON.stringify(Array.from(types))", self.js)
        self.assertIn('if (v === "") ens.clear();', self.js)                      # « Tous »
        self.assertNotIn('fieldname: "employe"', self.js)                         # plus de liste à choix unique
        self.assertNotIn('fieldname: "type_intervention"', self.js)

    def test_les_taches_sont_rangees_par_jour(self):
        self.assertIn("function _par_jour(liste) {", self.js)
        self.assertIn('class="gt-cocher-jour"', self.js)
        for raccourci in ('data-rac="jour"', 'data-rac="demain"', 'data-rac="semaine"', 'data-rac="suivante"'):
            self.assertIn(raccourci, self.js)

    def test_le_transfert_montre_les_deux_agendas_et_verrouille(self):
        self.assertIn("employe: JSON.stringify([e.de, e.vers].filter(Boolean))", self.js)   # les deux agendas entiers
        self.assertIn("function _html_transfert() {", self.js)
        self.assertIn("se_chevauchent", self.js)                                  # chevauchement chez celui qui reçoit
        self.assertIn('if (l.fixe) return __("employé fixe");', self.js)          # reflet de refus_transfert
        self.assertIn('l.matricule === e.de && l.statut === "Open" && !verrou(l)', self.js)   # seules cochables

    def test_le_calendrier_se_recharge_apres_une_action(self):
        self.assertIn("applique = true;", self.js)
        self.assertIn('cur_list.doctype === "Tache de travail"', self.js)

    def test_l_ouverture_garde_sa_signature(self):
        """tache_liste_groupe.js appelle ouvrir(coches, periode)."""
        self.assertIn("function ouvrir(preselection, periode) {", self.js)
        with open(os.path.join(BASE, "public", "js", "tache_liste_groupe.js"), encoding="utf-8") as f:
            self.assertIn("customization_app.gestion_taches.ouvrir(coches, periode);", f.read())


if __name__ == "__main__":
    unittest.main()
