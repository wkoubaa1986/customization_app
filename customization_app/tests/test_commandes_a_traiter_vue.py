"""Commandes à traiter (22/10/2026) : une seule définition de « à traiter », la vue « reste à faire »
partagée avec l'onglet Appels, et les bugs corrigés (Annuler, ruptures fantômes, lignes en double,
double SMS, brouillons jamais prévenus)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from customization_app import commandes_a_traiter as CAT


class TestDefinition(unittest.TestCase):

    def test_est_a_traiter(self):
        self.assertTrue(CAT.est_a_traiter(0, "Draft", ""))                        # web à confirmer
        self.assertTrue(CAT.est_a_traiter(1, "To Deliver and Bill", ""))
        self.assertTrue(CAT.est_a_traiter(1, "To Bill", "Tâche ouverte en retard"))
        self.assertFalse(CAT.est_a_traiter(1, "To Bill", ""))                     # livrée, rien à faire
        self.assertFalse(CAT.est_a_traiter(1, "Closed", "Tâche ouverte en retard"))
        self.assertFalse(CAT.est_a_traiter(2, "Cancelled", ""))

    def test_commentaire_traite(self):
        self.assertTrue(CAT.commentaire_traite("Comment", "le client ne répond pas"))
        self.assertTrue(CAT.commentaire_traite("Info", "📨 Envoi groupé par x — SMS : envoyé"))
        self.assertTrue(CAT.commentaire_traite("Info", "🚚 Livraison par notre équipe autorisée par x"))
        self.assertFalse(CAT.commentaire_traite("Comment", "SMS d'annulation envoyé à 1 numéro(s) : 21144306."))
        self.assertFalse(CAT.commentaire_traite("Info", "BL validé"))


class TestFiltreAnomalie(unittest.TestCase):

    def test_par_type_et_par_prefixe(self):
        lignes = [{"name": "A", "anomalie": "Tâche ouverte en retard"},
                  {"name": "B", "anomalie": "Livraison sans tâche"},
                  {"name": "C", "anomalie": "Commande annulée avec tâche Tache-08055"},
                  {"name": "D", "anomalie": ""}]
        garde = lambda a: [c["name"] for c in CAT._filtrer(lignes, None, None, None, None, a, None)]
        self.assertEqual(garde("Livraison sans tâche"), ["B"])
        self.assertEqual(garde("Commande annulée avec tâche"), ["C"])
        self.assertEqual(garde("avec"), ["A", "B", "C"])
        self.assertEqual(garde("sans"), ["D"])

    def test_le_menu_propose_les_types_de_commande_alertes(self):
        from customization_app.commande_alertes import MOTIFS
        frappe.set_user("Administrator")
        self.assertEqual(CAT.get_filtres()["anomalies"][:len(MOTIFS)], list(MOTIFS))


class TestCoherence(unittest.TestCase):
    """Base réelle : le compteur de l'onglet Appels et l'écran disent le même nombre."""

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("cat_vue")

    def tearDown(self):
        frappe.db.rollback(save_point="cat_vue")

    def test_sql_et_python_designent_les_memes_commandes(self):
        sql = set(frappe.db.sql_list(
            f"SELECT so.name FROM `tabSales Order` so WHERE so.transaction_date >= %s AND {CAT.SQL_A_TRAITER}",
            (CAT.DEPUIS_DEFAUT,)))
        py = {r.name for r in frappe.get_all(
            "Sales Order", filters={"transaction_date": [">=", CAT.DEPUIS_DEFAUT]},
            fields=["name", "docstatus", "status", "custom_anomalie"], limit_page_length=0)
            if CAT.est_a_traiter(r.docstatus, r.status, r.custom_anomalie)}
        self.assertEqual(sql, py)

    def test_le_compteur_appels_egale_l_ecran(self):
        r = CAT.reste_a_faire()
        ecran = CAT.get_commandes(page_length=0)
        self.assertEqual(ecran["total"], r["reste"])
        self.assertEqual(ecran["kpis"]["a_traiter"], r["a_traiter"])

    def test_un_commentaire_du_jour_sort_la_commande_du_reste(self):
        avant = CAT.reste_a_faire()
        cible = next((c["name"] for c in CAT.get_commandes(page_length=0)["lignes"]), None)
        if not cible:
            self.skipTest("rien à traiter dans cette base")
        frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Sales Order",
                        "reference_name": cible, "content": "client rappelé, livraison jeudi"}).insert()
        frappe.db.set_value("Comment", {"reference_name": cible, "content": "client rappelé, livraison jeudi"},
                            "owner", "salmaaquaworldservicing@gmail.com", update_modified=False)
        apres = CAT.reste_a_faire()
        self.assertEqual(apres["reste"], avant["reste"] - 1)
        self.assertIn(cible, CAT.traitees_aujourdhui([cible]))


class TestCorrections(unittest.TestCase):

    def test_annuler_est_appelable_et_sauvegarder_ne_l_est_plus(self):
        frappe.is_whitelisted(CAT.annuler)                                # ne lève pas
        with self.assertRaises(frappe.PermissionError):
            frappe.is_whitelisted(CAT._sauvegarder)

    def test_meme_article_sur_deux_lignes(self):
        lignes = [{"code": "C-10'-PP-5m", "qte": 2, "idx": 1}, {"code": "C-10'-PP-5m", "qte": 3, "idx": 2}]
        contexte = ({"C-10'-PP-5m": 100}, {}, {"C-10'-PP-5m"}, {"SO": {1: 2, 2: 0}})
        out, *_ = CAT._enrichir_articles("SO", lignes, contexte)
        self.assertEqual([a["reste"] for a in out], [2, 0])

    def test_une_commande_morte_ne_manque_de_rien(self):
        lignes = [{"code": "PO-200", "qte": 10, "idx": 1}]
        contexte = ({"PO-200": 0}, {}, {"PO-200"}, {"SO": {1: 10}})
        _, manquants, reels, _ = CAT._enrichir_articles("SO", lignes, contexte, vivante=False)
        self.assertEqual((manquants, reels), (0, 0))
        _, manquants, reels, _ = CAT._enrichir_articles("SO", lignes, contexte)
        self.assertEqual((manquants, reels), (1, 1))

    def test_un_brouillon_ne_se_retire_pas_de_l_engage(self):
        lignes = [{"code": "A", "qte": 3, "idx": 1}]
        contexte = ({"A": 5}, {"A": 4}, {"A"}, {"SO": {1: 3}})             # 4 engagés par D'AUTRES commandes
        out, *_ = CAT._enrichir_articles("SO", lignes, contexte, brouillon=True)
        self.assertEqual(out[0]["dispo"], 1)                              # 5 − 4, pas 5 − (4 − 3)

    def test_pas_de_sms_automatique_quand_le_message_est_personnalise(self):
        from customization_app import sms_annulation
        doc = frappe._dict(woocommerce_id="123", name="WEB1-X", flags=frappe._dict())
        with patch.object(sms_annulation.frappe, "enqueue") as enqueue:
            frappe.flags.sans_sms_annulation_auto = True
            try:
                sms_annulation.on_sales_order_cancel(doc)
            finally:
                frappe.flags.sans_sms_annulation_auto = False
            enqueue.assert_not_called()
            sms_annulation.on_sales_order_cancel(doc)
            enqueue.assert_called_once()

    def test_annuler_et_informer_previent_aussi_les_brouillons(self):
        from customization_app import sms_commandes
        dest = {"WEB1-B": {"commande": "WEB1-B", "client": "C1"}, "SO-V": {"commande": "SO-V", "client": "C2"}}
        etats = {"WEB1-B": 0, "SO-V": 1}
        vu = {}

        def envoyer(noms, modele, **kw):
            vu.update(noms=noms, lignes=kw.get("lignes"), flag=frappe.flags.get("sans_sms_annulation_auto"))
            return {"sms_envoyes": 2}

        with patch.object(CAT.frappe.db, "get_value", side_effect=lambda dt, n, f: etats[n]), \
             patch.object(sms_commandes, "_destinataires", side_effect=lambda noms: [dest[n] for n in noms]), \
             patch.object(sms_commandes, "envoyer", side_effect=envoyer), \
             patch.object(CAT, "annuler", side_effect=lambda noms, motif: (
                 vu.setdefault("flag_pendant", frappe.flags.get("sans_sms_annulation_auto")),
                 [{"commande": "WEB1-B", "etat": "brouillon validé, annulé puis supprimé — sauvegarde x"},
                  {"commande": "SO-V", "etat": "annulée"}])[1]):
            r = CAT.annuler_et_informer(["WEB1-B", "SO-V"], "rupture", "Bonjour")
        self.assertTrue(vu["flag_pendant"])                               # SMS automatique coupé pendant l'annulation
        self.assertFalse(vu["flag"])                                      # … et rétabli après
        self.assertEqual(sorted(vu["noms"]), ["SO-V", "WEB1-B"])
        self.assertEqual(sorted(r["informes"]), ["SO-V", "WEB1-B"])


class TestSuivi(unittest.TestCase):
    """Suivi en un clic : la commande sort du reste à faire jusqu'à sa date de reprise (base réelle, rollback)."""

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("cat_suivi")
        if not frappe.db.has_column("Sales Order", "custom_suivi"):
            self.skipTest("champs de suivi absents (patch suivi_commande_fields non joué)")

    def tearDown(self):
        frappe.db.rollback(save_point="cat_suivi")

    def _une(self, web=None):
        for c in CAT.get_commandes(page_length=0)["lignes"]:
            if web is None or c["web"] == web:
                return c["name"]
        self.skipTest("aucune commande adaptée dans cette base")

    def test_les_traces_de_suivi_comptent_comme_traitement(self):
        self.assertTrue(CAT.commentaire_traite("Comment", "⏰ À rappeler le 24/10/2026 09:00 — client en voyage"))
        self.assertTrue(CAT.commentaire_traite("Comment", "📵 Ne répond pas — rappel le 23/10/2026 09:00"))
        self.assertTrue(CAT.commentaire_traite("Comment", "⌛ En instance jusqu'au 30/10/2026 09:00"))

    def test_programmee_plus_tard_sort_du_reste_puis_revient_en_tete(self):
        nom = self._une()
        avant = CAT.reste_a_faire()
        demain = frappe.utils.add_days(frappe.utils.now_datetime(), 1)
        frappe.db.set_value("Sales Order", nom, {"custom_suivi": CAT.SUIVI_A_RAPPELER, "custom_suivi_le": demain})
        apres = CAT.reste_a_faire()
        self.assertEqual(apres["reste"], avant["reste"] - 1)
        self.assertEqual(apres["programmees"], avant["programmees"] + 1)
        # la date est atteinte : retour dans le reste à faire, en TÊTE, marqué « à reprendre »
        frappe.db.set_value("Sales Order", nom, "custom_suivi_le", frappe.utils.add_days(frappe.utils.now_datetime(), -1))
        ecran = CAT.get_commandes(page_length=0)
        self.assertEqual(ecran["lignes"][0]["name"], nom)
        self.assertTrue(ecran["lignes"][0]["suivi_echu"])
        self.assertEqual(ecran["total"], CAT.reste_a_faire()["reste"])

    def test_a_rappeler_exige_une_date(self):
        with self.assertRaises(frappe.ValidationError):
            CAT.poser_suivi([self._une()], CAT.SUIVI_A_RAPPELER)

    def test_ne_repond_pas_horodate_les_appels_web(self):
        nom = self._une(web=True)
        frappe.db.set_value("Sales Order", nom, {"custom_appel_1_sans_reponse": None, "custom_appel_2_sans_reponse": None})
        r1 = CAT.poser_suivi([nom], CAT.SUIVI_NE_REPOND_PAS)[0]
        self.assertFalse(r1["deuxieme_appel"])
        s = frappe.db.get_value("Sales Order", nom, ["custom_suivi", "custom_suivi_le", "custom_appel_1_sans_reponse"], as_dict=True)
        self.assertEqual(s.custom_suivi, CAT.SUIVI_NE_REPOND_PAS)
        self.assertEqual(str(s.custom_suivi_le)[11:16], "09:00")                  # demain 9 h
        self.assertTrue(s.custom_appel_1_sans_reponse)
        self.assertTrue(CAT.poser_suivi([nom], CAT.SUIVI_NE_REPOND_PAS)[0]["deuxieme_appel"])

    def test_deux_appels_sans_reponse_proposent_l_annulation_toutes_commandes(self):
        nom = self._une(web=False)
        r1 = CAT.poser_suivi([nom], CAT.SUIVI_NE_REPOND_PAS)[0]
        r2 = CAT.poser_suivi([nom], CAT.SUIVI_NE_REPOND_PAS)[0]
        self.assertEqual(r2["appels"], r1["appels"] + 1)
        self.assertTrue(r2["deuxieme_appel"])
        self.assertEqual(r1["deuxieme_appel"], r1["appels"] >= 2)
        self.assertIn("appel sans réponse", r2["etat"])
        self.assertFalse(r2["web"])                                       # commande saisie : vérifier avec son auteur
        self.assertTrue(r2["createur"])

    def test_retirer_le_suivi(self):
        nom = self._une()
        CAT.poser_suivi([nom], CAT.SUIVI_EN_INSTANCE, le=frappe.utils.add_days(frappe.utils.now_datetime(), 5), note="attente stock")
        CAT.retirer_suivi([nom])
        self.assertFalse(frappe.db.get_value("Sales Order", nom, "custom_suivi"))


if __name__ == "__main__":
    unittest.main()
