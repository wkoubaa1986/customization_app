"""Page « Stock par entrepôt » : solde, sorties, transfert, remise à zéro par le Magasin.
Un entrepôt d'essai et de vrais articles dans un savepoint : tout est annulé."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from customization_app import stock_entrepots as S

EMPLOYE_AVEC_ENTREPOT = "morchediakram0@gmail.com"     # Akram : « Stock Akram », pas responsable magasin
# Les trajets demandés le 05/10/2026 (= patch ensure_transfert_double_validation).
TRAJETS_DEFAUT = [("Magasin", "Hall", 0), ("Hall", "Magasin", 0), ("Magasin", "Stock véhicule", 1),
                  ("Stock véhicule", "Magasin", 1), ("Stock véhicule", "Articles défectueux", 1),
                  ("Articles défectueux", "Stock véhicule", 1), ("Magasin", "Articles défectueux", 0)]


def regler_trajets(trajets, **entrepots):
    """Réglage des trajets (et du Hall / des Articles défectueux) pour le test — dans son savepoint."""
    import frappe
    frappe.db.delete(S.CONFIG_TRAJET, {"parent": S.CONFIG, "parenttype": S.CONFIG})
    for idx, (de, vers, double) in enumerate(trajets, 1):
        frappe.get_doc({"doctype": S.CONFIG_TRAJET, "parent": S.CONFIG, "parenttype": S.CONFIG, "parentfield": "trajets",
                        "idx": idx, "depuis": de, "vers": vers, "double_validation": double}).db_insert()
    for champ, wh in entrepots.items():
        frappe.db.set_single_value(S.CONFIG, champ, wh)


def entrepot_essai(nom):
    import frappe
    societe = S._societe()
    parent = frappe.db.get_value("Warehouse", {"is_group": 1, "company": societe}, "name")
    return frappe.get_doc({"doctype": "Warehouse", "warehouse_name": nom, "company": societe,
                           "parent_warehouse": parent}).insert().name


def fin_de_test(save_point):
    import frappe
    frappe.set_user("Administrator")
    frappe.db.rollback(save_point=save_point)
    frappe.db.value_cache.pop(S.CONFIG, None)        # le rollback ne vide pas le cache des valeurs du réglage


class TestGabarit(unittest.TestCase):
    def test_pas_d_apostrophe_droite(self):
        """Le .html d'une page Desk finit dans une chaîne JS entre apostrophes : une seule ' casse la page."""
        html = (Path(__file__).parents[1] / "customize_erpnext/page/stock_entrepots/stock_entrepots.html").read_text()
        self.assertNotIn("'", re.sub(r"<!--.*?-->", "", html, flags=re.S))


class TestStockEntrepots(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("stock_entrepots")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        self.magasin = S.magasin()
        societe = S._societe()
        parent = frappe.db.get_value("Warehouse", {"is_group": 1, "company": societe}, "name")
        self.essai = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "ESSAI SE", "company": societe,
                                     "parent_warehouse": parent}).insert().name
        regler_trajets(TRAJETS_DEFAUT + [("Magasin", "Autre entrepôt", 0), ("Autre entrepôt", "Magasin", 0)])
        self.items = frappe.db.sql_list("""select b.item_code from tabBin b join tabItem i on i.name = b.item_code
                                           where b.warehouse = %s and b.actual_qty > 10 and b.valuation_rate > 0
                                             and i.disabled = 0 and i.is_stock_item = 1 and i.has_variants = 0
                                           order by b.item_code limit 2""", self.magasin)
        if len(self.items) < 2:
            self.skipTest("pas assez d'articles en stock au Magasin")

    def tearDown(self):
        fin_de_test("stock_entrepots")

    def _solde(self, entrepot):
        return {a["item_code"]: a["qte"] for a in S.get_solde(entrepot)["articles"]}

    def test_transfert_puis_remise_a_zero(self):
        import frappe
        i1, i2 = self.items
        r = S.creer_transfert(self.magasin, self.essai, json.dumps([{"item_code": i1, "qte": 2}, {"item_code": i1, "qte": 1}]))
        self.assertEqual(r["lignes"], 1)                                           # même article : lignes cumulées
        S.creer_transfert(self.essai, self.magasin, [{"item_code": i2, "qte": 4}])  # l'entrepôt passe en négatif
        self.assertEqual(self._solde(self.essai), {i1: 3, i2: -4})
        self.assertEqual(S.get_solde(self.essai, negatifs=1)["negatifs"], 1)

        today = frappe.utils.nowdate()
        sorties = S.get_sorties(self.essai, today, today)
        self.assertEqual([(a["item_code"], a["qte"]) for a in sorties["articles"]], [(i2, 4)])
        ligne = S.get_sorties_article(i2, self.essai, today, today)["lignes"][0]
        self.assertEqual((ligne["libelle"], ligne["vers"], ligne["qte"]), ("Transfert", self.magasin, 4))

        etat = next(w for w in S.entrepots_a_zero()["entrepots"] if w["name"] == self.essai)
        self.assertEqual((etat["negatifs"], etat["positifs"]), (1, 1))
        apercu = {l["item_code"]: l for l in S.apercu_zero(self.essai)["lignes"]}
        self.assertEqual((apercu[i1]["sens"], apercu[i2]["sens"]), ("retour", "apport"))

        frappe.clear_messages()
        r = S.remettre_a_zero(self.essai, json.dumps([i1, i2]))
        # l'avertissement ERPNext « stock négatif » (un par article) ne doit pas noyer le résultat
        self.assertFalse([m for m in frappe.local.message_log if m.get("title") == frappe._("Warning on Negative Stock")])
        self.assertEqual((r["apports"], r["retours"], r["restant"]), (1, 1, []))
        self.assertEqual(self._solde(self.essai), {})
        sens = {(d.from_warehouse, d.to_warehouse) for d in (frappe.get_doc("Stock Entry", n) for n in r["ecritures"])}
        self.assertEqual(sens, {(self.magasin, self.essai), (self.essai, self.magasin)})

    def test_remise_a_zero_partielle(self):
        i1, i2 = self.items
        S.creer_transfert(self.magasin, self.essai, [{"item_code": i1, "qte": 2}, {"item_code": i2, "qte": 5}])
        S.remettre_a_zero(self.essai, [i1])                                       # seulement l'article coché
        self.assertEqual(self._solde(self.essai), {i2: 5})

    def test_refus(self):
        import frappe
        i1 = self.items[0]
        with self.assertRaises(frappe.ValidationError):
            S.remettre_a_zero(self.magasin)                                       # le Magasin porte les écarts
        with self.assertRaises(frappe.ValidationError):
            S.creer_transfert(self.essai, self.essai, [{"item_code": i1, "qte": 1}])
        with self.assertRaises(frappe.ValidationError):
            S.creer_transfert(self.magasin, self.essai, [{"item_code": i1, "qte": 0}])
        reglage = frappe.get_single(S.CONFIG)
        reglage.append("entrepots_exclus", {"entrepot": self.essai})
        reglage.save()
        self.assertNotIn(self.essai, [w["name"] for w in S.entrepots_a_zero()["entrepots"]])
        with self.assertRaises(frappe.ValidationError):
            S.remettre_a_zero(self.essai)
        frappe.db.set_value("Item", i1, "disabled", 1)
        with self.assertRaises(frappe.ValidationError):
            S.creer_transfert(self.magasin, self.essai, [{"item_code": i1, "qte": 1}])

    def test_trajet_absent_du_reglage_interdit(self):
        import frappe
        i1 = self.items[0]
        regler_trajets([("Magasin", "Autre entrepôt", 0)])
        S.creer_transfert(self.magasin, self.essai, [{"item_code": i1, "qte": 1}])
        with self.assertRaises(frappe.ValidationError) as cm:
            S.creer_transfert(self.essai, self.magasin, [{"item_code": i1, "qte": 1}])
        self.assertIn("interdit", str(cm.exception))
        S.remettre_a_zero(self.essai)                     # la remise à zéro, elle, n'est pas un trajet du réglage
        self.assertEqual(self._solde(self.essai), {})
        regler_trajets([])                                # tableau vide = ancien fonctionnement, tout est permis
        S.creer_transfert(self.essai, self.magasin, [{"item_code": i1, "qte": 1}])

    def test_employe_voit_son_entrepot_et_rien_de_plus(self):
        import frappe
        entrepot = frappe.db.get_value("Employee", {"user_id": EMPLOYE_AVEC_ENTREPOT, "status": "Active"}, "custom_warehouse")
        if not entrepot or S.est_responsable(EMPLOYE_AVEC_ENTREPOT):
            self.skipTest("employé d'essai absent ou responsable magasin")
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        ctx = S.get_context()
        self.assertEqual((ctx["mien"], ctx["defaut"], ctx["responsable"]), (entrepot, entrepot, False))
        self.assertNotIn("valeur", S.get_solde(entrepot))                         # quantités seulement
        for appel in (lambda: S.entrepots_a_zero(), lambda: S.remettre_a_zero(entrepot),
                      lambda: S.rechercher_articles("membrane"),
                      lambda: S.creer_transfert(self.magasin, entrepot, [{"item_code": self.items[0], "qte": 1}])):
            with self.assertRaises(frappe.PermissionError):
                appel()

    def test_ecritures_en_post_seulement(self):
        import frappe
        for fn in (S.creer_transfert, S.annuler_transfert, S.remettre_a_zero):
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func.get(fn), ["POST"], fn.__name__)


class TestSortiesEnrichies(unittest.TestCase):
    def test_bl_donne_commande_et_tache(self):
        """Une vraie sortie par BL d'une commande qui a une tâche : la page doit montrer les deux."""
        import frappe
        row = frappe.db.sql("""select sle.posting_date, sle.posting_time, sle.warehouse, sle.voucher_type, sle.voucher_no,
                                      sle.voucher_detail_no, -sle.actual_qty as qte, dni.against_sales_order as commande
                               from `tabStock Ledger Entry` sle
                               join `tabDelivery Note Item` dni on dni.name = sle.voucher_detail_no
                               where sle.voucher_type = 'Delivery Note' and sle.is_cancelled = 0 and sle.actual_qty < 0
                                 and exists (select 1 from `tabTache de travail` t where t.commande_client = dni.against_sales_order)
                               order by sle.posting_date desc limit 1""", as_dict=True)
        if not row:
            self.skipTest("aucun BL de commande avec tâche")
        ligne = S.enrichir_sorties(row)[0]
        self.assertEqual((ligne["libelle"], ligne["commande"]), ("BL", row[0].commande))
        self.assertTrue(ligne["taches"])
        self.assertTrue(all(t["name"] and "employe" in t and "type" in t for t in ligne["taches"]))


class TestDoubleValidation(unittest.TestCase):
    """Trajets qui touchent un véhicule : l'employé du véhicule et un responsable magasin valident les mêmes
    quantités, en va-et-vient jusqu'à l'accord (02/10/2026, va-et-vient et trajets le 05/10/2026)."""

    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("double_validation")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        if not S._champs_validation():
            self.skipTest("champs de validation absents (patch non joué)")
        self.magasin = S.magasin()
        self.essai = entrepot_essai("ESSAI DV")
        self.hall = entrepot_essai("ESSAI HALL")
        self.defectueux = entrepot_essai("ESSAI DEF")
        regler_trajets(TRAJETS_DEFAUT, entrepot_hall=self.hall, entrepot_defectueux=self.defectueux)
        self.akram = frappe.db.get_value("Employee", {"user_id": EMPLOYE_AVEC_ENTREPOT, "status": "Active"}, "name")
        if not self.akram or S.est_responsable(EMPLOYE_AVEC_ENTREPOT):
            self.skipTest("pas d'employé simple avec compte")
        frappe.db.set_value("Employee", self.akram, "custom_warehouse", self.essai)   # son véhicule = l'entrepôt d'essai
        self.items = frappe.db.sql_list("""select b.item_code from tabBin b join tabItem i on i.name = b.item_code
                                           where b.warehouse = %s and b.actual_qty > 10 and b.valuation_rate > 0
                                             and i.disabled = 0 and i.is_stock_item = 1 and i.has_variants = 0
                                           order by b.item_code limit 2""", self.magasin)
        if len(self.items) < 2:
            self.skipTest("pas assez d'articles en stock au Magasin")

    def tearDown(self):
        fin_de_test("double_validation")

    def _solde(self, entrepot):
        return {a["item_code"]: a["qte"] for a in S.get_solde(entrepot)["articles"]}

    def _demande(self, source=None, cible=None):
        i1, i2 = self.items
        r = S.creer_transfert(source or self.magasin, cible or self.essai,
                              [{"item_code": i1, "qte": 2}, {"item_code": i2, "qte": 5}])
        self.assertTrue(r["en_attente"])
        self.assertEqual(r["employe"], self.akram)
        return r["name"]

    def _formulaire(self, source, cible, item):
        """Un transfert saisi dans le formulaire Stock Entry standard, hors de la page."""
        import frappe
        se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": S._type_transfert(), "purpose": S.PURPOSE,
                             "company": S._societe(), "items": [{"item_code": item, "qty": 1, "s_warehouse": source,
                                                                 "t_warehouse": cible}]})
        se.flags.ignore_permissions = True
        se.insert()
        return se

    def test_categories(self):
        self.assertEqual([S.categorie(w) for w in (self.magasin, self.hall, self.defectueux, self.essai)],
                         [S.CAT_MAGASIN, S.HALL, S.DEFECTUEUX, S.VEHICULE])

    def test_accord_du_premier_coup(self):
        import frappe
        i1, i2 = self.items
        name = self._demande()
        self.assertEqual(self._solde(self.essai), {})                              # rien n'a bougé
        self.assertEqual(frappe.db.get_value("Stock Entry", name, ["docstatus", S.CHAMP_TOUR]), (0, S.COTE_EMPLOYE))
        t = next(t for t in S.transferts_a_valider() if t["name"] == name)         # le responsable les voit toutes
        self.assertFalse(t["a_moi"])                                               # … mais c'est au tour d'Akram
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        t = next(t for t in S.transferts_a_valider() if t["name"] == name)
        self.assertTrue(t["a_moi"])
        self.assertEqual({l["item_code"]: l["qte"] for l in t["lignes"]}, {i1: 2, i2: 5})
        r = S.valider_transfert(name)
        self.assertEqual((r["statut"], r["ecarts"]), ("valide", []))
        frappe.set_user("Administrator")
        self.assertEqual(self._solde(self.essai), {i1: 2, i2: 5})
        se = frappe.get_doc("Stock Entry", name)
        self.assertEqual((se.docstatus, se.get(S.CHAMP_VALIDE_PAR)), (1, EMPLOYE_AVEC_ENTREPOT))
        self.assertIsNone(next(t for t in S.transferts_recents() if t["name"] == name)["attente"])

    def test_va_et_vient_jusqu_a_l_accord(self):
        """Akram change une quantité → au Magasin ; le responsable corrige à son tour → à Akram ; Akram accepte."""
        import frappe
        i1, i2 = self.items
        name = self._demande()
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        r = S.valider_transfert(name, [{"item_code": i2, "qte": 3}])
        self.assertEqual((r["statut"], r["tour"]), ("renvoye", S.COTE_MAGASIN))
        with self.assertRaises(frappe.ValidationError):                         # plus son tour
            S.valider_transfert(name)
        self.assertNotIn(name, [t["name"] for t in S.transferts_a_valider() if t["a_moi"]])

        frappe.set_user("Administrator")
        self.assertEqual(self._solde(self.essai), {})                               # toujours rien en stock
        t = next(t for t in S.transferts_a_valider() if t["name"] == name)
        self.assertTrue(t["a_moi"])
        l2 = next(l for l in t["lignes"] if l["item_code"] == i2)
        self.assertEqual((l2["qte"], l2["qte_precedente"], l2["qte_initiale"]), (3, 5, 5))
        recent = next(t for t in S.transferts_recents() if t["name"] == name)
        self.assertEqual((recent["attente"]["tour"], recent["attente"]["a_moi"]), (S.COTE_MAGASIN, True))
        self.assertEqual({l["item_code"]: l["qte"] for l in recent["lignes"]}, {i1: 2, i2: 3})   # proposition en cours
        r = S.valider_transfert(name, [{"item_code": i2, "qte": 4}])             # le Magasin corrige à son tour
        self.assertEqual(r["tour"], S.COTE_EMPLOYE)

        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        r = S.valider_transfert(name, [{"item_code": i1, "qte": 2}, {"item_code": i2, "qte": 4}])   # d'accord
        self.assertEqual(r["statut"], "valide")
        frappe.set_user("Administrator")
        self.assertEqual(self._solde(self.essai), {i1: 2, i2: 4})
        se = frappe.get_doc("Stock Entry", name)
        self.assertIn("demandé 5, validé 4", se.get(S.CHAMP_ECART))
        self.assertEqual(len(json.loads(se.get(S.CHAMP_HISTO))), 4)              # demande, 2 contre-propositions, accord
        self.assertEqual(next(t for t in S.transferts_recents() if t["name"] == name)["validation"]["propositions"], 3)

    def test_retour_au_magasin_meme_mecanisme(self):
        import frappe
        i1, i2 = self.items
        S.creer_transfert(self.magasin, self.essai, [{"item_code": i1, "qte": 3}])
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        S.valider_transfert(next(t["name"] for t in S.transferts_a_valider() if t["a_moi"] and t["de"] == self.magasin))
        frappe.set_user("Administrator")
        r = S.creer_transfert(self.essai, self.magasin, [{"item_code": i1, "qte": 3}])
        self.assertEqual((r["en_attente"], r["tour"]), (True, S.COTE_EMPLOYE))
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        self.assertEqual(S.valider_transfert(r["name"], [{"item_code": i1, "qte": 2}])["statut"], "renvoye")
        frappe.set_user("Administrator")
        self.assertEqual(S.valider_transfert(r["name"])["statut"], "valide")    # le Magasin accepte 2
        self.assertEqual(self._solde(self.essai), {i1: 1})

    def test_vehicule_et_articles_defectueux(self):
        import frappe
        i1 = self.items[0]
        r = S.creer_transfert(self.essai, self.defectueux, [{"item_code": i1, "qte": 1}])
        self.assertEqual((r["en_attente"], r["tour"]), (True, S.COTE_EMPLOYE))
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        self.assertEqual(S.valider_transfert(r["name"])["statut"], "valide")
        frappe.set_user("Administrator")
        self.assertEqual(self._solde(self.defectueux), {i1: 1})

    def test_trajets_interdits_et_formulaire(self):
        import frappe
        i1 = self.items[0]
        for source, cible in ((self.essai, self.hall), (self.hall, self.essai)):    # Hall : avec le Magasin seulement
            with self.assertRaises(frappe.ValidationError) as cm:
                S.creer_transfert(source, cible, [{"item_code": i1, "qte": 1}])
            self.assertIn("interdit", str(cm.exception))
        r = S.creer_transfert(self.magasin, self.hall, [{"item_code": i1, "qte": 1}])  # Hall ↔ Magasin : immédiat
        self.assertNotIn("en_attente", r)
        self.assertEqual(self._solde(self.hall), {i1: 1})

        se = self._formulaire(self.magasin, self.essai, i1)          # le formulaire ne contourne pas la double validation
        with self.assertRaises(frappe.ValidationError) as cm:
            se.submit()
        self.assertIn("double validation", str(cm.exception))
        se = self._formulaire(self.essai, self.hall, i1)
        with self.assertRaises(frappe.ValidationError):
            se.submit()
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)                          # Hall ↔ Magasin : responsable magasin seulement
        se = self._formulaire(self.hall, self.magasin, i1)
        se.flags.ignore_permissions = True
        with self.assertRaises(frappe.PermissionError):
            se.submit()
        frappe.set_user("Administrator")
        self.assertEqual(self._solde(self.hall), {i1: 1})

    def test_rien_d_accord_a_zero_retire_la_demande(self):
        import frappe
        name = self._demande()
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        r = S.valider_transfert(name, [{"item_code": c, "qte": 0} for c in self.items])
        self.assertEqual(r["statut"], "renvoye")                                    # contre-proposition « rien »
        frappe.set_user("Administrator")
        r = S.valider_transfert(name)                                               # le Magasin l'accepte
        self.assertTrue(r["supprime"])
        self.assertFalse(frappe.db.exists("Stock Entry", name))
        self.assertEqual(self._solde(self.essai), {})

    def test_un_autre_employe_ne_valide_pas(self):
        import frappe
        autre = frappe.db.get_value("Employee", {"status": "Active", "user_id": ["not in", ["", EMPLOYE_AVEC_ENTREPOT]],
                                                 "name": ["!=", self.akram]}, "user_id")
        if not autre or S.est_responsable(autre):
            self.skipTest("pas d'autre employé simple")
        name = self._demande()
        frappe.set_user(autre)
        with self.assertRaises(frappe.PermissionError):
            S.valider_transfert(name)

    def test_son_propre_vehicule_valide_par_un_autre_responsable(self):
        """Un responsable qui charge SON véhicule (Sadok) ne se valide pas lui-même : un autre responsable le fait."""
        import frappe
        autres = [u for u in frappe.get_all("Has Role", filters={"role": "Responsable magasin", "parenttype": "User"},
                                            pluck="parent")
                  if u not in ("Administrator", EMPLOYE_AVEC_ENTREPOT) and frappe.db.get_value("User", u, "enabled")]
        if not autres:
            self.skipTest("pas d'autre responsable magasin")
        frappe.db.set_value("Employee", self.akram, "user_id", "Administrator")      # c'est MON véhicule
        i1 = self.items[0]
        r = S.creer_transfert(self.magasin, self.essai, [{"item_code": i1, "qte": 1}])
        self.assertEqual((r["en_attente"], r["tour"]), (True, S.COTE_MAGASIN))
        with self.assertRaises(frappe.ValidationError):
            S.valider_transfert(r["name"])
        frappe.set_user(autres[0])
        self.assertEqual(S.valider_transfert(r["name"])["statut"], "valide")

    def test_soumission_directe_refusee(self):
        """Le formulaire Stock Entry (ou un script) ne passe pas outre l'attente : hook before_submit.
        MAT-STE-2026-00104 avait été soumis ainsi par le demandeur, sans l'employé (02/10/2026)."""
        import frappe
        name = self._demande()
        se = frappe.get_doc("Stock Entry", name)
        se.flags.ignore_permissions = True
        with self.assertRaises(frappe.ValidationError) as cm:
            se.submit()
        self.assertIn("attend la double validation", str(cm.exception))
        self.assertEqual(frappe.db.get_value("Stock Entry", name, "docstatus"), 0)
        self.assertEqual(self._solde(self.essai), {})                                 # le stock n'a pas bougé
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)                                        # la vraie validation passe
        S.valider_transfert(name)
        self.assertEqual(frappe.db.get_value("Stock Entry", name, "docstatus"), 1)

    def test_ancienne_demande_sans_historique(self):
        """Un brouillon d'avant le va-et-vient (ni tour ni historique) : au tour de l'employé, demande = 1er tour."""
        import frappe
        i1, i2 = self.items
        name = self._demande()
        frappe.db.set_value("Stock Entry", name, {S.CHAMP_TOUR: None, S.CHAMP_HISTO: None})
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        t = next(t for t in S.transferts_a_valider() if t["name"] == name)
        self.assertEqual((t["tour"], t["a_moi"], len(t["tours"])), (S.COTE_EMPLOYE, True, 1))
        self.assertEqual(S.valider_transfert(name, [{"item_code": i2, "qte": 1}])["tour"], S.COTE_MAGASIN)

    def test_annuler_une_demande_en_attente(self):
        import frappe
        name = self._demande()
        self.assertTrue(S.annuler_transfert(name))
        self.assertFalse(frappe.db.exists("Stock Entry", name))
        self.assertNotIn(name, [t["name"] for t in S.transferts_a_valider()])   # d’autres vraies demandes peuvent exister

    def test_ma_journee_seulement_a_son_tour(self):
        import frappe
        from customization_app.planning_employe import _transferts_a_valider
        name = self._demande()
        frappe.set_user(EMPLOYE_AVEC_ENTREPOT)
        self.assertIn(name, [t["name"] for t in _transferts_a_valider(self.akram)])
        S.valider_transfert(name, [{"item_code": self.items[1], "qte": 1}])     # renvoyé au Magasin
        self.assertNotIn(name, [t["name"] for t in _transferts_a_valider(self.akram)])
