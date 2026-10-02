"""Stock par entrepôt — réapprovisionnement (seuils) et vérification des stocks d'employés (01/10/2026).
Règles pures d'abord ; puis le circuit complet dans un savepoint (le cron commite : on le contourne)."""
from __future__ import annotations

import json
import unittest

from customization_app import stock_entrepots as S


class TestRegles(unittest.TestCase):
    def test_prochaine_date(self):
        self.assertEqual(S.prochaine_date("Jeudi", "2026-10-01"), "2026-10-01")     # jeudi → aujourd'hui
        self.assertEqual(S.prochaine_date("Vendredi", "2026-10-01"), "2026-10-02")
        self.assertEqual(S.prochaine_date("Mercredi", "2026-10-01"), "2026-10-07")  # la semaine suivante
        self.assertEqual(S.prochaine_date("Lundi", "2026-10-04"), "2026-10-05")

    def test_heure(self):
        import datetime
        self.assertEqual(S._heure(datetime.timedelta(hours=9)), "09:00")           # Time lu en base
        self.assertEqual(S._heure(datetime.timedelta(hours=10, minutes=30)), "10:30")
        self.assertEqual(S._heure("9:00:00"), "09:00")
        self.assertEqual(S._heure(None), "09:00")

    def test_ecarts(self):
        lignes = [{"qte_systeme": 10, "qte_comptee": 8, "taux": 2.5},      # −2 × 2,5 = −5 (manquant)
                  {"qte_systeme": -1, "qte_comptee": 0, "taux": 10},       # +1 × 10 = +10
                  {"qte_systeme": 3, "qte_comptee": 3, "taux": 1},         # juste
                  {"qte_systeme": 3, "qte_comptee": None, "taux": 1},      # pas compté : ignoré
                  {"qte_systeme": 3, "qte_comptee": "", "taux": 1}]
        b = S.calculer_ecarts(lignes)
        self.assertEqual((b["nb_comptes"], b["nb_ecarts"], b["valeur_ecarts"], b["valeur_manquants"]), (3, 2, 5.0, 5.0))
        self.assertEqual([l["ecart"] for l in lignes], [-2, 1, 0, None, None])


class TestModeleCible(unittest.TestCase):
    def test_fusion(self):
        existant = {"A": 2.0, "B": 5.0}
        modele = {"B": 3.0, "C": 4.0}
        self.assertEqual(S.fusionner_cible(existant, modele), {"A": 2.0, "B": 5.0, "C": 4.0})            # B garde sa quantité
        self.assertEqual(S.fusionner_cible(existant, modele, remplacer=True), {"A": 2.0, "B": 3.0, "C": 4.0})
        self.assertEqual(S.fusionner_cible({}, modele), modele)
        self.assertEqual(S.fusionner_cible(existant, {}), existant)


class TestCircuit(unittest.TestCase):
    ENTREPOT = "Stock Sadok Bouziri - A&S"

    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        if not frappe.db.exists("Employee", {"custom_warehouse": self.ENTREPOT, "status": "Active"}):
            self.skipTest("pas d'employé pour cet entrepôt")
        frappe.db.savepoint("verif")
        # Pas de fiche « En cours » parasite : le circuit en ouvre une.
        for n in frappe.get_all(S.VERIF, filters={"entrepot": self.ENTREPOT, "statut": "En cours"}, pluck="name"):
            frappe.db.set_value(S.VERIF, n, "statut", "Terminée", update_modified=False)

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="verif")

    def test_ouverture_comptage_cloture_sans_mouvement(self):
        import frappe
        nom = S.ouvrir_verification(self.ENTREPOT, "2026-10-01", avec_taches=True)
        v = frappe.get_doc(S.VERIF, nom)
        self.assertEqual(v.statut, "En cours")
        self.assertFalse(v.lignes)                                              # pas de photo à la création (rendez-vous d'avance)
        self.assertTrue(v.tache_employe)                                        # l'employé du stock
        self.assertEqual(S.ouvrir_verification(self.ENTREPOT, "2026-10-01"), nom)   # une seule fiche par date
        self.assertEqual(S.commencer_verification(self.ENTREPOT), nom)          # « Compter maintenant » reprend la fiche ouverte
        S.detail_verification(nom)                                              # première ouverture : photographie
        v = frappe.get_doc(S.VERIF, nom)
        self.assertTrue(v.lignes)
        l0, l1 = v.lignes[0], v.lignes[1]
        stock_avant = frappe.db.get_value("Bin", {"item_code": l1.item_code, "warehouse": self.ENTREPOT}, "actual_qty")
        comptes = {l0.item_code: {"qte_comptee": l0.qte_systeme}, l1.item_code: {"qte_comptee": l1.qte_systeme + 2, "commentaire": "en plus"}}
        r = S.enregistrer_verification(nom, json.dumps(comptes))
        self.assertEqual((r["nb_comptes"], r["nb_ecarts"]), (2, 1))
        r = S.terminer_verification(nom, json.dumps(comptes), note="test")
        self.assertEqual(r["statut"], "À valider")                              # 1re validation : le comptage
        self.assertAlmostEqual(r["valeur_ecarts"], round(2 * l1.taux, 3), places=3)
        self.assertEqual(frappe.db.get_value("Tache de travail", v.tache_employe, "status"), "Completed")
        # le stock ne bouge PAS avant la validation du responsable
        self.assertEqual(frappe.db.get_value("Bin", {"item_code": l1.item_code, "warehouse": self.ENTREPOT}, "actual_qty"), stock_avant)
        r = S.valider_verification(nom)                                         # validation du responsable
        self.assertEqual(r["statut"], "À confirmer")
        emp = frappe.db.get_value("Employee", {"custom_warehouse": self.ENTREPOT, "status": "Active"}, "user_id")
        frappe.set_user(emp)
        r = S.confirmer_verification(nom)                                       # accord de l'employé : rapprochement
        frappe.set_user("Administrator")
        self.assertEqual(r["statut"], "Terminée")
        self.assertTrue(r["rapprochement"])
        self.assertEqual(frappe.db.get_value("Bin", {"item_code": l1.item_code, "warehouse": self.ENTREPOT}, "actual_qty"), stock_avant + 2)
        self.assertEqual([h["name"] for h in S.historique_verifications(self.ENTREPOT)][0], nom)
        with self.assertRaises(frappe.ValidationError):
            S.terminer_verification(nom)                                        # déjà terminée

    def test_le_cron_cree_la_prochaine_de_chaque_stock_dans_la_semaine(self):
        """Les rendez-vous de la semaine à venir existent d'avance : la prochaine occurrence du jour réglé,
        aujourd'hui compris, avec ses tâches ; rejouer le cron ne crée rien de plus."""
        import frappe
        from frappe.utils import nowdate
        cfg = S.config_verification()
        actives = [l for l in cfg["lignes"] if l["actif"]]
        if not actives:
            self.skipTest("aucune vérification planifiée dans le réglage")
        vrai_commit = frappe.db.commit
        frappe.db.commit = lambda *a, **k: None                                 # le cron commite : pas dans un test
        try:
            manquantes = [l for l in actives if not frappe.db.exists(S.VERIF, {"entrepot": l["entrepot"], "date": S.prochaine_date(l["jour"], nowdate())})]
            crees = S.planifier_verifications()
            self.assertEqual(len(crees), len(manquantes))
            for n in crees:
                v = frappe.get_doc(S.VERIF, n)
                self.assertTrue(v.tache_employe and v.tache_responsable)       # les deux tâches, créées d'avance
                tache = frappe.get_doc("Tache de travail", v.tache_employe)
                self.assertIn("Vérification stock", tache.titre or "")         # pas de « null » au calendrier
                self.assertEqual((tache.ends_on - tache.starts_on).total_seconds(), 3600)   # 1 heure par défaut
                self.assertFalse(v.lignes)                                     # photo du stock au premier comptage
            for l in actives:
                self.assertTrue(frappe.db.exists(S.VERIF, {"entrepot": l["entrepot"], "date": S.prochaine_date(l["jour"], nowdate())}))
            self.assertEqual(S.planifier_verifications(), [])
        finally:
            frappe.db.commit = vrai_commit

    def test_un_employe_ne_voit_que_son_stock(self):
        import frappe
        emp = frappe.db.get_value("Employee", {"custom_warehouse": ["is", "set"], "status": "Active",
                                               "name": ["!=", frappe.db.get_value("Employee", {"custom_warehouse": self.ENTREPOT}, "name")]},
                                  ["user_id", "custom_warehouse"], as_dict=True)
        if not emp or not emp.user_id or S.est_responsable(emp.user_id):
            self.skipTest("pas d'employé non responsable avec un stock")
        frappe.set_user(emp.user_id)
        self.assertEqual([s["entrepot"] for s in S.verifications_etat()["stocks"]], [emp.custom_warehouse])
        with self.assertRaises(frappe.PermissionError):
            S.commencer_verification(self.ENTREPOT)
        with self.assertRaises(frappe.PermissionError):
            S.planifier_maintenant()

    def test_solde_a_reapprovisionner(self):
        import frappe
        seuil = S.seuils().get(self.ENTREPOT)
        if not seuil:
            self.skipTest("pas de seuil réglé pour cet entrepôt")
        res = S.get_solde(self.ENTREPOT, a_reappro=1)
        self.assertTrue(res["articles"])
        cible = S.stock_cible(self.ENTREPOT)
        for a in res["articles"]:
            self.assertTrue(a["a_reappro"])
            if a["item_code"] in cible:                                          # la cible de l'article prime sur le seuil
                self.assertEqual((a["cible"], a["a_transferer"]), (cible[a["item_code"]], max(cible[a["item_code"]] - a["qte"], 0)))
            else:
                self.assertTrue(a["qte"] < seuil["seuil"])
                self.assertEqual(a["a_transferer"], max(seuil["cible"] - a["qte"], 0))
        self.assertEqual(res["a_reappro"], len(res["articles"]))
        sans = S.get_solde(self.ENTREPOT)
        self.assertTrue(all(abs(a["qte"]) > 0 for a in sans["articles"]))     # sans filtre : stock non nul seulement


class TestListeArticles(unittest.TestCase):
    """« Coller une liste » dans un stock cible : lecture pure des lignes, puis résolution contre les articles."""

    def test_parser(self):
        p = S.parser_liste_articles
        self.assertEqual(p("C-10'-CTO 10\nCartouche, UDF 10'\t5\nPF-10' x 3\n- A-C\n\n  "), [
            ("C-10'-CTO", 10.0), ("Cartouche, UDF 10'", 5.0), ("PF-10'", 3.0), ("A-C", 1.0)])
        self.assertEqual(p("C-10'-CTO;2,5\nM-80-V × 4", qte_defaut=7), [("C-10'-CTO", 2.5), ("M-80-V", 4.0)])
        self.assertEqual(p("Pack Filtre 10\" (PP-UDF-CTO)"), [("Pack Filtre 10\" (PP-UDF-CTO)", 1.0)])  # un nom qui finit par « ) »
        self.assertEqual(p(""), [])

    def test_resolution(self):
        import frappe
        frappe.set_user("Administrator")
        suivi = frappe.db.get_value("Item", {"is_stock_item": 1, "disabled": 0, "has_variants": 0}, ["name", "item_name"], as_dict=True)
        service = frappe.db.get_value("Item", {"is_stock_item": 0, "disabled": 0}, "name")
        r = S.resoudre_liste_articles(f"{suivi.name.lower()} 3\n{suivi.item_name}\t4\n{service or ''}\nZZZ-INEXISTANT 2")
        self.assertEqual([(l["item_code"], l["qte"]) for l in r["lignes"]], [(suivi.name, 3.0)])     # dédoublonné, casse ignorée
        self.assertEqual(r["inconnus"], ["ZZZ-INEXISTANT"])
        self.assertEqual(len(r["non_suivis"]), 1 if service else 0)
        self.assertEqual(S.articles_non_suivis([suivi.name] + ([service] if service else [])), [service] if service else [])


class TestDoubleValidationVerif(unittest.TestCase):
    """Double validation (02/10/2026) : l'employé termine son comptage, un responsable AUTRE valide (ou ajuste,
    et l'employé confirme) ; le rapprochement de stock n'est passé qu'alors, sur le véhicule."""
    AKRAM = "morchediakram0@gmail.com"

    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("dv_verif")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        self.akram = frappe.db.get_value("Employee", {"user_id": self.AKRAM, "status": "Active"}, "name")
        if not self.akram or S.est_responsable(self.AKRAM):
            self.skipTest("pas d'employé simple avec compte")
        societe = S._societe()
        parent = frappe.db.get_value("Warehouse", {"is_group": 1, "company": societe}, "name")
        self.essai = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "ESSAI DVV", "company": societe,
                                     "parent_warehouse": parent}).insert().name
        frappe.db.set_value("Employee", self.akram, "custom_warehouse", self.essai)
        self.items = frappe.db.sql_list("""select b.item_code from tabBin b join tabItem i on i.name = b.item_code
                                           where b.warehouse = %s and b.actual_qty > 10 and b.valuation_rate > 0
                                             and i.disabled = 0 and i.is_stock_item = 1 and i.has_variants = 0
                                           order by b.item_code limit 2""", S.magasin())
        if len(self.items) < 2:
            self.skipTest("pas assez d'articles au Magasin")
        S.ecriture_transfert(S.magasin(), self.essai, [(self.items[0], 3), (self.items[1], 5)], "essai")

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="dv_verif")

    def _qte(self, code):
        import frappe
        return frappe.db.get_value("Bin", {"item_code": code, "warehouse": self.essai}, "actual_qty")

    def _comptage(self, q1, q2):
        """Akram compte et termine → « À valider »."""
        import frappe
        i1, i2 = self.items
        nom = S.ouvrir_verification(self.essai, frappe.utils.nowdate(), avec_taches=False)
        frappe.set_user(self.AKRAM)
        S.detail_verification(nom)
        r = S.terminer_verification(nom, {i1: {"qte_comptee": q1}, i2: {"qte_comptee": q2}})
        self.assertEqual((r["statut"], r["valide_employe_par"], r["actions"]), ("À valider", "Akram", []))
        with self.assertRaises(frappe.PermissionError):
            S.valider_verification(nom)                                         # pas responsable
        frappe.set_user("Administrator")
        return nom

    def test_validation_sans_ajustement_rapproche_le_vehicule(self):
        import frappe
        i1, i2 = self.items
        nom = self._comptage(3, 4)
        self.assertEqual(self._qte(i2), 5)                                      # rien n'a bougé
        self.assertEqual(S.detail_verification(nom)["fiche"]["actions"], ["valider", "renvoyer"])
        # une sortie ENTRE le comptage et la validation : à la validation les quantités système sont relues,
        # l'article qui a bougé PERD son comptage et la fiche repart au comptage (décision utilisateur 02/10/2026)
        S.ecriture_transfert(self.essai, S.magasin(), [(i1, 1)], "sortie entre comptage et validation")
        r = S.valider_verification(nom)
        self.assertEqual((r["statut"], r["renvoye"], r["a_recompter"], r["renvois"]), ("En cours", True, [i1], 1))
        l1 = next(l for l in frappe.get_doc(S.VERIF, nom).lignes if l.item_code == i1)
        self.assertEqual((l1.compte, l1.a_recompter, l1.qte_systeme), (0, 1, 2))
        self.assertIn("3.0 → 2.0", l1.commentaire)
        frappe.set_user(self.AKRAM)
        with self.assertRaises(frappe.ValidationError):                               # pas sans recompter l'article
            S.terminer_verification(nom, {i2: {"qte_comptee": 4}})
        r = S.terminer_verification(nom, {i1: {"qte_comptee": 2}, i2: {"qte_comptee": 4}})
        self.assertEqual((r["statut"], r["nb_a_recompter"]), ("À valider", 0))
        frappe.set_user("Administrator")
        r = S.valider_verification(nom)
        self.assertEqual((r["statut"], r["ajustes"]), ("À confirmer", []))      # toujours vers l'employé
        self.assertEqual(self._qte(i2), 5)
        frappe.set_user(self.AKRAM)
        r = S.confirmer_verification(nom)                                       # d'accord : validation mutuelle
        self.assertEqual((r["statut"], r["changes"]), ("Terminée", []))
        rec = frappe.get_doc("Stock Reconciliation", r["rapprochement"])
        self.assertEqual((rec.docstatus, [(x.item_code, x.qty) for x in rec.items]), (1, [(i2, 4)]))   # seul le vrai écart
        self.assertEqual((self._qte(i1), self._qte(i2)), (2, 4))
        self.assertEqual(frappe.db.get_value(S.VERIF, nom, "valide_responsable_par"), "Administrator")

    def test_ajustement_puis_confirmation_de_l_employe(self):
        import frappe
        i1, i2 = self.items
        nom = self._comptage(3, 4)
        r = S.valider_verification(nom, {i2: {"qte_comptee": 5}})               # le responsable corrige : 4 → 5
        self.assertEqual((r["statut"], len(r["ajustes"])), ("À confirmer", 1))
        l = next(x for x in frappe.get_doc(S.VERIF, nom).lignes if x.item_code == i2)
        self.assertEqual((l.ajuste, l.qte_employe, l.qte_comptee), (1, 4, 5))
        self.assertEqual(self._qte(i2), 5)                                      # toujours rien
        with self.assertRaises(frappe.PermissionError):
            S.confirmer_verification(nom)                                       # pas l'employé du stock
        frappe.set_user(self.AKRAM)
        self.assertEqual(S.detail_verification(nom)["fiche"]["actions"], ["confirmer", "recompter"])
        r = S.confirmer_verification(nom, {i2: {"qte_comptee": 4}})            # l'employé maintient 4 → retour au responsable
        self.assertEqual((r["statut"], len(r["changes"]), r["valide_responsable_par"]), ("À valider", 1, None))
        l = next(x for x in frappe.get_doc(S.VERIF, nom).lignes if x.item_code == i2)
        self.assertEqual((l.qte_comptee, l.ajuste), (4, 0))
        self.assertIn("Modifié par l’employé", l.commentaire)
        frappe.set_user("Administrator")
        r = S.valider_verification(nom)                                         # le responsable tranche : 4, tel quel
        self.assertEqual((r["statut"], r["ajustes"]), ("À confirmer", []))
        frappe.set_user(self.AKRAM)
        r = S.confirmer_verification(nom)
        self.assertEqual((r["statut"], r["valide_employe_par"]), ("Terminée", "Akram"))
        self.assertEqual(self._qte(i2), 4)                                      # rapproché sur le mot final : 4

    def test_renvoi_au_comptage(self):
        import frappe
        i1, i2 = self.items
        nom = self._comptage(3, 4)
        r = S.renvoyer_verification(nom, motif="recompte le carton")
        self.assertEqual((r["statut"], r["renvois"], r["valide_employe_par"]), ("En cours", 1, None))
        self.assertIn("recompte", frappe.db.get_value(S.VERIF, nom, "note"))
        frappe.set_user(self.AKRAM)
        r = S.terminer_verification(nom, {i1: {"qte_comptee": 3}, i2: {"qte_comptee": 5}})
        self.assertEqual((r["statut"], r["nb_ecarts"]), ("À valider", 0))
        frappe.set_user("Administrator")
        r = S.valider_verification(nom)
        self.assertEqual(r["statut"], "À confirmer")
        frappe.set_user(self.AKRAM)
        r = S.confirmer_verification(nom)
        self.assertEqual((r["statut"], r["rapprochement"]), ("Terminée", None))
