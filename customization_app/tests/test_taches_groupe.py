"""« Gérer les interventions » (customization_app.taches_groupe) — demande du 18/11/2026 : choix multiple sur le
type et l'employé, seuls les employés actifs dans la liste, et une action « Transférer » d'un employé à un autre."""
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
        self.assertIn('fieldname: "vers", label: __("Transférer à")', self.js)
        self.assertIn('action === "transferer" && !vers', self.js)                # pas de transfert sans destinataire

    def test_type_et_employe_a_choix_multiple(self):
        self.assertIn("employe: JSON.stringify(Array.from(employes))", self.js)
        self.assertIn("type_intervention: JSON.stringify(Array.from(types))", self.js)
        self.assertIn('if (v === "") ens.clear();', self.js)                      # « Tous »
        self.assertNotIn('fieldname: "employe"', self.js)                         # plus de liste à choix unique
        self.assertNotIn('fieldname: "type_intervention"', self.js)

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
