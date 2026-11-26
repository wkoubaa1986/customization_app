"""Machine rendue — essai RÉEL sur la copie de prod (26/11/2026), sans trace : de vrais BL de retour sont créés et validés
puis tout est annulé (commit neutralisé, envois interdits, file d'attente et index de recherche neutralisés).

- retour complet d'un osmoseur (SAL-ORD-2026-04731, KARRAY SARRAH, livré le 18/11) : échéancier annulé, commentaires ;
  le garde-fou de nuit : gardé quand la nuit le recréerait (commande remise à 100 % et échéanciers annulés non comptés) ;
- retour d'1 TD-1.5 sur 2 (SAL-ORD-2026-04584) : gardé, avec le compte ;
- échéancier à deux machines (MAT-MSH-2026-00440 : un UV, trois porte-filtres) : gardé tant qu'une machine est chez le
  client, annulé quand tout est revenu ;
- reprise d'échange : gardé ;
- le hook ne bloque jamais la validation ; un BL normal sans commande n'est pas lu ;
- relecture du 26/11/2026 : la machine qui REVIENT (remplacement livré sur la même commande, retour annulé) retrouve
  son échéancier par un amendement à l'identique ; une commande dont la machine a été rendue s'annule encore (retours
  d'abord), sans rien rétablir ; une liste d'appels brouillon qui cite l'échéancier annulé s'enregistre encore ; un
  retour saisi avec seulement return_against compte la machine rendue ;
- le rattrapage 2026 : 5 échéanciers annulés, 1 BL sans échéancier, rejouable sans effet.
Dates fixes (nowdate du module mocké au 26/11/2026). Sauté quand la copie ne contient plus le cas."""
import json
import unittest
from unittest import mock

import frappe

from customization_app import annulation_commande as A
from customization_app import api as API
from customization_app import relances_config as RC
from customization_app import retours_machines as RM
from customization_app.Maintenance import relance_maintenance_sms as R
from customization_app.Maintenance import update_schedule as U
from customization_app.patches import echeanciers_machines_rendues as P


# 26/11/2026 : les documents créés par ces essais n'entrent pas dans la file de recherche globale (Redis) — le rollback ne
# l'annule pas, et le dev en gardait des centaines d'entrées vers des documents qui n'existent plus.
_RECHERCHE = {}


def setUpModule():
    try:
        _RECHERCHE["avant"] = frappe.local.conf.get("disable_global_search")
        frappe.local.conf["disable_global_search"] = 1
    except Exception:
        pass


def tearDownModule():
    try:
        if _RECHERCHE.get("avant") is None:
            frappe.local.conf.pop("disable_global_search", None)
        else:
            frappe.local.conf["disable_global_search"] = _RECHERCHE["avant"]
    except Exception:
        pass

JOUR = "2026-11-26"
HOOK = "customization_app.retours_machines.on_submit_delivery_note"
HOOK_ANNULATION = "customization_app.retours_machines.on_cancel_delivery_note"
SO_K, BL_K, ART_K, MS_K = "SAL-ORD-2026-04731", "MAT-DN-2026-06199", "AP-C+-EP-7-MA-SM", "MAT-MSH-2026-01448"
LISTE_BROUILLON = "LIST-25-11-2026-00001"
RENDUS_2026 = {"MAT-MSH-2026-00889", "MAT-MSH-2026-00928", "MAT-MSH-2026-01042", "MAT-MSH-2026-01059", "MAT-MSH-2026-01259"}


def _interdit(*a, **k):
    raise AssertionError("envoi réel tenté")


def couvertes_validees_seulement():
    """commandes_couvertes telle qu'avant le lot 4 (échéanciers validés seulement)."""
    out = set()
    for (so,) in frappe.db.sql("""select msi.sales_order from `tabMaintenance Schedule Item` msi join `tabMaintenance Schedule` ms
                                  on ms.name = msi.parent where ms.docstatus = 1 and ifnull(msi.sales_order, '') <> ''
                                  union select msd.custom_sales_order from `tabMaintenance Schedule Detail` msd
                                  join `tabMaintenance Schedule` ms on ms.name = msd.parent
                                  where ms.docstatus = 1 and ifnull(msd.custom_sales_order, '') <> ''"""):
        out.update(s.strip() for s in (so or "").split(",") if s.strip())
    return out


def couvertes_avec_annules():
    out = set()
    for (so,) in frappe.db.sql("""select msi.sales_order from `tabMaintenance Schedule Item` msi join `tabMaintenance Schedule` ms
                                  on ms.name = msi.parent where ms.docstatus in (1, 2) and ifnull(msi.sales_order, '') <> ''"""):
        out.update(s.strip() for s in (so or "").split(",") if s.strip())
    return out | couvertes_validees_seulement()


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("retours_site")
        self.journal, self.file = [], []
        self.mocks = [
            mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
            mock.patch.object(frappe, "log_error", lambda *a, **k: self.journal.append(k.get("title") or "")),
            mock.patch.object(R, "envoyer_sms_winsms", _interdit),
            mock.patch.object(frappe, "sendmail", _interdit),
            mock.patch.object(RC, "alerter", lambda *a, **k: None),
            mock.patch.object(frappe, "enqueue", lambda *a, **k: self.file.append(a[0] if a else k.get("method"))),
            mock.patch("frappe.utils.global_search.sync_value_in_queue", lambda *a, **k: None),
            mock.patch("frappe.model.delete_doc.delete_for_document", lambda *a, **k: None),
            # une pièce supprimée emporte ses fichiers joints du disque, hors transaction (relecture du 26/11/2026)
            mock.patch("frappe.model.delete_doc.remove_all", lambda *a, **k: None),
            mock.patch.object(frappe, "publish_realtime", lambda *a, **k: None),
            mock.patch.object(RM, "nowdate", lambda: JOUR),
        ]
        for m in self.mocks:
            m.start()

    def tearDown(self):
        for m in self.mocks:
            m.stop()
        frappe.db.rollback(save_point="retours_site")

    def exiger(self, **etats):
        for ms, docstatus in etats.items():
            if frappe.db.get_value("Maintenance Schedule", ms.replace("_", "-"), "docstatus") != docstatus:
                self.skipTest("%s n'est plus dans l'état de la copie du 25/11/2026" % ms)

    def retour(self, bl, quantites, echange=None):
        """Crée et valide un BL de retour de `bl` pour {article: quantité rendue} ; passe le hook s'il n'est pas branché."""
        from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return
        if frappe.db.get_value("Delivery Note", bl, "docstatus") != 1:
            self.skipTest("%s n'est plus validé" % bl)
        ret = make_sales_return(bl)
        ret.items = [it for it in ret.items if it.item_code in quantites]
        for it in ret.items:
            it.qty = -quantites[it.item_code]
        if echange:
            ret.set(RM.CHAMP_ECHANGE, echange)
        ret.flags.ignore_permissions = True
        try:
            ret.insert()
            ret.submit()
        except Exception as e:
            self.skipTest("retour de %s impossible sur cette copie : %s" % (bl, str(e)[:120]))
        if HOOK not in (frappe.get_hooks("doc_events").get("Delivery Note", {}).get("on_submit") or []):
            RM.on_submit_delivery_note(ret)
        return ret

    def branche(self, evenement, hook):
        return hook in (frappe.get_hooks("doc_events").get("Delivery Note", {}).get(evenement) or [])

    def livrer(self, so, quantites):
        """Crée et valide un BL NORMAL de la commande pour {article: quantité} ; passe le hook s'il n'est pas branché."""
        from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
        dn = make_delivery_note(so)
        dn.items = [it for it in dn.items if it.item_code in quantites]
        for it in dn.items:
            it.qty = quantites[it.item_code]
        dn.flags.ignore_permissions = True
        try:
            dn.insert()
            dn.submit()
        except Exception as e:
            self.skipTest("livraison de %s impossible sur cette copie : %s" % (so, str(e)[:120]))
        if not self.branche("on_submit", HOOK):
            RM.on_submit_delivery_note(dn)
        return dn

    def annuler(self, dn):
        dn.reload()
        dn.flags.ignore_permissions = True
        dn.cancel()
        if not self.branche("on_cancel", HOOK_ANNULATION):
            RM.on_cancel_delivery_note(dn)

    def amendement(self, ms):
        return frappe.db.get_value("Maintenance Schedule", {"amended_from": ms}, ["name", "docstatus"], as_dict=True)

    def commentaires(self, doctype, nom):
        return frappe.db.sql_list("""select content from tabComment where reference_doctype = %s and reference_name = %s
                                     and content like %s""", (doctype, nom, "%" + RM.TRACE + "%"))


class TestRetourSite(_SansTrace):
    def test_retour_complet_annule(self):
        self.exiger(MAT_MSH_2026_01448=1)
        with mock.patch.object(U, "commandes_couvertes", couvertes_avec_annules):
            ret = self.retour("MAT-DN-2026-06199", {"AP-C+-EP-7-MA-SM": 1})
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-01448", "docstatus"), 2)
            self.assertFalse(RM.la_nuit_le_recreerait("SAL-ORD-2026-04731"))
        self.assertTrue(any("Échéancier annulé" in c and ret.name in c
                            for c in self.commentaires("Maintenance Schedule", "MAT-MSH-2026-01448")))
        self.assertTrue(any("MAT-MSH-2026-01448 est annulé" in c for c in self.commentaires("Customer", "KARRAY SARRAH")))
        self.assertTrue(frappe.db.count("Maintenance Schedule Detail", {"parent": "MAT-MSH-2026-01448"}))

    def test_retour_annule_retablit(self):
        # Relecture du 26/11/2026 : retour saisi par erreur puis annulé → la machine est chez le client, l'échéancier
        # revient (amendement à l'identique) ; avant, il restait annulé et la nuit (lot 4) ne le recréait pas.
        self.exiger(MAT_MSH_2026_01448=1)
        visites = frappe.db.sql("""select idx, scheduled_date, completion_status, custom_sms_1 from
                                   `tabMaintenance Schedule Detail` where parent = %s order by idx""", MS_K)
        ret = self.retour(BL_K, {ART_K: 1})
        self.assertEqual(RM.echeanciers_annules_par(ret.name), [MS_K])
        self.annuler(ret)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_K, "docstatus"), 2)
        nouveau = self.amendement(MS_K)
        self.assertTrue(nouveau and nouveau.docstatus == 1, nouveau)
        self.assertEqual(RM.echeanciers_de(SO_K, ART_K), [nouveau.name])
        self.assertIn(SO_K, U.commandes_couvertes())
        # mêmes visites (dates, état, SMS), rattachées à la ligne machine du nouvel échéancier
        self.assertEqual(frappe.db.sql("""select idx, scheduled_date, completion_status, custom_sms_1 from
                                          `tabMaintenance Schedule Detail` where parent = %s order by idx""",
                                       nouveau.name), visites)
        self.assertFalse(frappe.db.sql("""select 1 from `tabMaintenance Schedule Detail` d where d.parent = %s
                                          and d.item_reference not in (select name from `tabMaintenance Schedule Item`
                                          where parent = %s)""", (nouveau.name, nouveau.name)))
        for doctype, nom in (("Maintenance Schedule", MS_K), ("Maintenance Schedule", nouveau.name),
                             ("Customer", "KARRAY SARRAH")):
            self.assertTrue(any(c.startswith(RM.TRACE_REVENUE) and ret.name in c and nouveau.name in c
                                for c in frappe.db.sql_list("""select content from tabComment where reference_doctype = %s
                                                               and reference_name = %s""", (doctype, nom))), (doctype, nom))
        self.assertEqual(self.journal, [])

    def test_remplacement_sur_la_meme_commande_retablit(self):
        # Mesuré par la relecture : retour complet, puis le remplacement livré depuis la MÊME commande (ce que propose
        # ERPNext) — la commande repasse « Livrée », couverte par l'échéancier annulé, sans suivi.
        self.exiger(MAT_MSH_2026_01448=1)
        self.retour(BL_K, {ART_K: 1})
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_K, "docstatus"), 2)
        dn = self.livrer(SO_K, {ART_K: 1})
        nouveau = self.amendement(MS_K)
        self.assertTrue(nouveau and nouveau.docstatus == 1, nouveau)
        self.assertEqual(RM.echeanciers_de(SO_K, ART_K), [nouveau.name])
        self.assertTrue(any(dn.name in c for c in self.commentaires("Maintenance Schedule", nouveau.name)
                            if c.startswith(RM.TRACE_REVENUE)) or any(
            dn.name in c for c in frappe.db.sql_list("""select content from tabComment where reference_doctype =
                'Maintenance Schedule' and reference_name = %s""", nouveau.name)))
        # rejoué (2e BL, hook rejoué) : pas de 2e amendement
        RM.on_submit_delivery_note(frappe.get_doc("Delivery Note", dn.name))
        self.assertEqual(frappe.db.count("Maintenance Schedule", {"amended_from": MS_K}), 1)
        self.assertFalse(frappe.db.exists("Maintenance Schedule", {"amended_from": nouveau.name}))

    def test_remplacement_annule_defait_le_retablissement(self):
        # Relecture finale du 26/11/2026 : le BL de remplacement qui avait rétabli l'échéancier est annulé (mauvais BL) —
        # la machine n'est plus chez le client, l'échéancier rétabli est annulé de nouveau.
        self.exiger(MAT_MSH_2026_01448=1)
        self.retour(BL_K, {ART_K: 1})
        dn = self.livrer(SO_K, {ART_K: 1})
        nouveau = self.amendement(MS_K)
        if not (nouveau and nouveau.docstatus == 1):
            self.skipTest("pas de rétablissement sur cette copie")
        self.annuler(dn)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", nouveau.name, "docstatus"), 2)
        self.assertTrue(any("est annulé : la machine n'est plus chez le client" in c
                            for c in self.commentaires("Maintenance Schedule", nouveau.name)))
        self.assertEqual(RM.echeanciers_de(SO_K, ART_K), [])

    def test_bl_normal_annule_sans_retablissement_ne_fait_rien(self):
        with mock.patch.object(RM, "decision", side_effect=AssertionError("lu")):
            RM._defaire_retablissements(frappe._dict(name="MAT-DN-INEXISTANT"))
        self.assertEqual(self.journal, [])

    def test_retour_annule_sans_retablir_le_dit(self):
        # La machine est déjà suivie ailleurs (simulé) : pas d'amendement, le motif est écrit sur l'échéancier et le client.
        self.exiger(MAT_MSH_2026_01448=1)
        ret = self.retour(BL_K, {ART_K: 1})
        vrai = RM.echeanciers_de
        with mock.patch.object(RM, "echeanciers_de",
                               lambda c, a, docstatus=1: ["MAT-MSH-AUTRE"] if docstatus == 1 else vrai(c, a, docstatus)):
            self.annuler(ret)
        self.assertIsNone(self.amendement(MS_K))
        for doctype, nom in (("Maintenance Schedule", MS_K), ("Customer", "KARRAY SARRAH")):
            self.assertTrue(any("n'est pas rétabli : la machine est déjà suivie par MAT-MSH-AUTRE" in c
                                for c in self.commentaires(doctype, nom)), (doctype, nom))

    def test_rien_n_est_retabli_quand_la_machine_reste_rendue(self):
        # Un BL normal d'un AUTRE article sur la commande : la machine reste rendue, rien ne bouge.
        self.exiger(MAT_MSH_2026_01448=1)
        self.retour(BL_K, {ART_K: 1})
        cr = RM.retablir({(SO_K, ART_K)}, "essai")
        self.assertEqual(cr["retablis"], [])
        self.assertEqual(cr["laisses"], [(MS_K, "toutes ses machines restent rendues")])
        self.assertIsNone(self.amendement(MS_K))

    def test_commande_avec_retour_s_annule(self):
        # Relecture du 26/11/2026 : la cascade annulait le BL d'origine avant son retour → « LinkExistsError », toute
        # l'annulation refusée. Retours d'abord ; et le retour annulé PAR la cascade ne rétablit rien.
        self.exiger(MAT_MSH_2026_01448=1)
        ret = self.retour(BL_K, {ART_K: 1})
        self.assertEqual([b.name for b in A._bls_de(SO_K)][0], ret.name)
        appels = []
        hook = API.on_delivery_note_cancel

        def on_cancel(doc, method=None):
            hook(doc, method)
            if method == "on_cancel" and not self.branche("on_cancel", HOOK_ANNULATION):
                appels.append(doc.name)
                RM.on_cancel_delivery_note(doc, method)
        so = frappe.get_doc("Sales Order", SO_K)
        so.flags.sans_sms_annulation_auto = True
        with mock.patch.object(API, "on_delivery_note_cancel", on_cancel):
            so.cancel()
        self.assertEqual(frappe.db.get_value("Sales Order", SO_K, "docstatus"), 2)
        self.assertFalse(frappe.db.exists("Delivery Note", ret.name))
        self.assertFalse(frappe.db.exists("Delivery Note", BL_K))
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_K, "docstatus"), 2)
        self.assertIsNone(self.amendement(MS_K))
        self.assertFalse([c for c in self.commentaires("Maintenance Schedule", MS_K) if "pas rétabli" in c
                          or c.startswith(RM.TRACE_REVENUE)])
        self.assertIsNone(A.annulation_commande_en_cours())
        if not self.branche("on_cancel", HOOK_ANNULATION):
            self.assertIn(ret.name, appels)     # le hook est bien passé, et n'a rien fait

    def test_liste_brouillon_qui_cite_l_echeancier_s_enregistre_encore(self):
        # Relecture du 26/11/2026 : « Cannot link cancelled document » — toute la liste bloquée pour l'opérateur.
        self.exiger(MAT_MSH_2026_01448=1)
        if frappe.db.get_value("Liste Appelle Entretien", LISTE_BROUILLON, "docstatus") != 0:
            self.skipTest("%s n'est plus un brouillon" % LISTE_BROUILLON)
        liste = frappe.get_doc("Liste Appelle Entretien", LISTE_BROUILLON)
        avant = len(liste.clients)
        detail = json.dumps({ART_K: "2026-11-18"})
        a_retirer = frappe.get_doc({"doctype": "Appelle Client", "parent": liste.name, "parenttype": liste.doctype,
                                    "parentfield": "clients", "idx": avant + 1, "client": "KARRAY SARRAH",
                                    "échéancier_dentretien": MS_K, "detail_articles": detail})
        a_retirer.db_insert()
        appelee = frappe.get_doc({"doctype": "Appelle Client", "parent": liste.name, "parenttype": liste.doctype,
                                  "parentfield": "clients", "idx": avant + 2, "client": "KARRAY SARRAH",
                                  "échéancier_dentretien": MS_K, "detail_articles": detail, "a_été_appelé": 1,
                                  "resume_appel": "Ne répond pas 1er appel"})
        appelee.db_insert()
        frappe.db.set_value("Liste Appelle Entretien", liste.name, "nb_appels_restant", "2", update_modified=False)
        ret = self.retour(BL_K, {ART_K: 1})
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_K, "docstatus"), 2)
        self.assertFalse(frappe.db.exists("Appelle Client", a_retirer.name))
        self.assertFalse(frappe.db.exists("Appelle Client", appelee.name))
        # seule la ligne non appelée comptait dans le compteur de l'écran
        self.assertEqual(frappe.db.get_value("Liste Appelle Entretien", liste.name, "nb_appels_restant"), "1")
        self.assertEqual(frappe.db.sql_list("select idx from `tabAppelle Client` where parent = %s order by idx",
                                            liste.name), list(range(1, avant + 1)))
        self.assertTrue(frappe.db.sql("""select 1 from tabComment where reference_doctype = 'Liste Appelle Entretien'
                                         and reference_name = %s and content like %s""",
                                      (liste.name, "%" + ret.name + "%" + MS_K + " est annulé%retirée (pas encore%"
                                       + "recopié sur la fiche client%")))
        self.assertTrue(frappe.db.sql("""select 1 from tabComment where reference_doctype = 'Customer'
                                         and reference_name = 'KARRAY SARRAH' and content like %s""",
                                      ("📞 Appel noté dans la liste d'appels " + liste.name
                                       + " : « Ne répond pas 1er appel »%",)))
        liste = frappe.get_doc("Liste Appelle Entretien", LISTE_BROUILLON)
        liste.save(ignore_permissions=True)     # refusé avant : CancelledLinkError

    def test_retour_saisi_avec_seulement_le_bl_rendu(self):
        # Relecture du 26/11/2026 : la ligne sans commande ni ligne d'origine était rattachée par return_against, mais
        # quantites() ne la comptait pas → « 0 rendue(s) sur 1 », échéancier gardé à tort.
        self.exiger(MAT_MSH_2026_01448=1)
        from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return
        ret = make_sales_return(BL_K)
        ret.items = [it for it in ret.items if it.item_code == ART_K]
        for it in ret.items:
            it.qty, it.against_sales_order, it.so_detail, it.dn_detail = -1, None, None, None
        ret.flags.ignore_permissions = True
        try:
            ret.insert()
            ret.submit()
        except Exception as e:
            self.skipTest("retour sans ligne d'origine impossible : %s" % str(e)[:120])
        if frappe.db.get_value("Delivery Note Item", {"parent": ret.name}, "against_sales_order"):
            self.skipTest("ERPNext a recollé la commande sur la ligne de retour")
        self.assertEqual(RM.quantites(SO_K, ART_K), (1.0, 1.0))
        # Le hook on_submit (branché le 26/11/2026) l'a annulé dès la validation du retour ; rejouer ne refait rien.
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_K, "docstatus"), 2)
        self.assertEqual(RM.echeanciers_annules_par(ret.name), [MS_K])
        cr = RM.traiter_retour(frappe.get_doc("Delivery Note", ret.name))
        self.assertEqual((cr["annules"], cr["deja_annules"]), ([], [MS_K]), cr)

    def test_garde_si_la_nuit_le_recreerait(self):
        # Le garde-fou : la nuit recréerait l'échéancier → l'annulation est défaite, l'échéancier gardé et commenté.
        self.exiger(MAT_MSH_2026_01448=1)
        with mock.patch.object(RM, "la_nuit_le_recreerait", lambda commande, cfg=None: True):
            self.retour("MAT-DN-2026-06199", {"AP-C+-EP-7-MA-SM": 1})
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-01448", "docstatus"), 1)
        self.assertTrue(any("recréerait" in c for c in self.commentaires("Maintenance Schedule", "MAT-MSH-2026-01448")))
        self.assertFalse(self.commentaires("Customer", "KARRAY SARRAH"))

    def test_quand_la_nuit_le_recreerait(self):
        # Après un retour complet, la commande retombe « Livrée en partie » : la nuit ne la relit pas. Remise à 100 %
        # (à la main), elle redevient candidate : recréée si la nuit ne compte que les échéanciers validés.
        self.exiger(MAT_MSH_2026_01448=1)
        with mock.patch.object(U, "commandes_couvertes", couvertes_avec_annules):
            self.retour("MAT-DN-2026-06199", {"AP-C+-EP-7-MA-SM": 1})
        self.assertEqual(frappe.db.get_value("Sales Order", "SAL-ORD-2026-04731", "delivery_status"), "Partly Delivered")
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-01448", "docstatus"), 2)
        self.assertFalse(RM.la_nuit_le_recreerait("SAL-ORD-2026-04731"))
        frappe.db.set_value("Sales Order", "SAL-ORD-2026-04731", {"delivery_status": "Fully Delivered", "per_delivered": 100},
                            update_modified=False)
        with mock.patch.object(U, "commandes_couvertes", couvertes_validees_seulement):
            self.assertTrue(RM.la_nuit_le_recreerait("SAL-ORD-2026-04731"))
        with mock.patch.object(U, "commandes_couvertes", couvertes_avec_annules):
            self.assertFalse(RM.la_nuit_le_recreerait("SAL-ORD-2026-04731"))

    def test_retour_partiel_garde(self):
        self.exiger(MAT_MSH_2026_01401=1)
        ret = self.retour("MAT-DN-2026-06002", {"TD-1.5": 1})
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-01401", "docstatus"), 1)
        c = self.commentaires("Maintenance Schedule", "MAT-MSH-2026-01401")
        self.assertEqual(len(c), 1)
        self.assertIn("TD-1.5 : 1 rendue(s) sur 2 livrée(s)", c[0])
        # rejoué (rattrapage) : pas de second commentaire
        RM.traiter_retour(frappe.get_doc("Delivery Note", ret.name), rattrapage=True)
        self.assertEqual(len(self.commentaires("Maintenance Schedule", "MAT-MSH-2026-01401")), 1)

    def test_deux_machines_gardé_puis_annule(self):
        self.exiger(MAT_MSH_2026_00440=1)
        bl = frappe.db.get_value("Delivery Note Item", {"against_sales_order": "SAL-ORD-2026-01866", "item_code": "F-UV-25w",
                                                        "docstatus": 1}, "parent")
        if not bl:
            self.skipTest("BL de SAL-ORD-2026-01866 introuvable")
        with mock.patch.object(U, "commandes_couvertes", couvertes_avec_annules):
            self.retour(bl, {"F-UV-25w": 1})
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-00440", "docstatus"), 1)
            self.assertTrue(any("P-F-10'-B-3/4 : 0 rendue(s) sur 3" in c
                                for c in self.commentaires("Maintenance Schedule", "MAT-MSH-2026-00440")))
            # 1 porte-filtre sur les 3 livrés : toujours gardé ; les 2 autres : annulé
            self.retour(bl, {"P-F-10'-B-3/4": 1})
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-00440", "docstatus"), 1)
            self.assertTrue(any("P-F-10'-B-3/4 : 1 rendue(s) sur 3" in c
                                for c in self.commentaires("Maintenance Schedule", "MAT-MSH-2026-00440")))
            self.retour(bl, {"P-F-10'-B-3/4": 2})
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-00440", "docstatus"), 2)

    def test_reprise_d_echange_gardee(self):
        self.exiger(MAT_MSH_2026_01448=1)
        if not frappe.db.has_column("Delivery Note", RM.CHAMP_ECHANGE):
            self.skipTest("champ d'échange absent")
        with mock.patch.object(U, "commandes_couvertes", couvertes_avec_annules):
            self.retour("MAT-DN-2026-06199", {"AP-C+-EP-7-MA-SM": 1}, echange="MAT-DN-2026-06199")
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", "MAT-MSH-2026-01448", "docstatus"), 1)
        self.assertTrue(any("un échange n'est pas un rendu" in c
                            for c in self.commentaires("Maintenance Schedule", "MAT-MSH-2026-01448")))

    def test_ne_bloque_jamais(self):
        doc = frappe._dict(name="MAT-DN-TEST", is_return=1)
        with mock.patch.object(RM, "traiter_retour", side_effect=RuntimeError("panne")):
            self.assertIsNone(RM.on_submit_delivery_note(doc))
        self.assertEqual(self.journal, ["Machine rendue — BL MAT-DN-TEST"])

    def test_bl_normal_pas_lu(self):
        with mock.patch.object(RM, "traiter_retour", side_effect=AssertionError("lu")), \
                mock.patch.object(RM, "retablir", side_effect=AssertionError("lu")):
            RM.on_submit_delivery_note(frappe._dict(name="MAT-DN-TEST", is_return=0))
            RM.on_submit_delivery_note(frappe._dict(name="MAT-DN-TEST", is_return=0,
                                                    items=[{"item_code": "X", "qty": 1, "against_sales_order": None}]))

    def test_bl_normal_ne_bloque_jamais(self):
        doc = frappe._dict(name="MAT-DN-TEST", is_return=0, posting_date=JOUR, customer=None,
                           items=[{"item_code": ART_K, "qty": 1, "against_sales_order": SO_K}])
        with mock.patch.object(RM, "retablir", side_effect=RuntimeError("panne")):
            self.assertIsNone(RM.on_submit_delivery_note(doc))
        self.assertEqual(self.journal, ["Machine rendue — BL MAT-DN-TEST"])

    def test_vente_ordinaire_une_seule_requete(self):
        # Le cas de tous les BL : la commande n'a aucun échéancier annulé → rien de plus qu'une lecture.
        doc = frappe._dict(name="MAT-DN-TEST", is_return=0, posting_date=JOUR, customer=None,
                           items=[{"item_code": ART_K, "qty": 1, "against_sales_order": SO_K}])
        with mock.patch.object(RM, "_amender", side_effect=AssertionError("amendé")):
            RM.on_submit_delivery_note(doc)
        self.assertEqual(self.journal, [])


class TestRattrapageSite(_SansTrace):
    def test_rattrapage_2026(self):
        if {n for n in RENDUS_2026 if frappe.db.get_value("Maintenance Schedule", n, "docstatus") == 1} != RENDUS_2026:
            self.skipTest("les 5 échéanciers des machines rendues ne sont plus tous actifs sur cette copie")
        total = P.execute()
        self.assertEqual(set(total["annules"]), RENDUS_2026)
        self.assertEqual(total["gardes"], [])
        self.assertEqual(total["erreurs"], 0)
        self.assertEqual({a for a, c in total["sans_echeancier"]}, {"P-F-S-10'-O-SC", "P-F-D-10'-O-SC"})
        for n in RENDUS_2026:
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", n, "docstatus"), 2)
            self.assertEqual(len(self.commentaires("Maintenance Schedule", n)), 1)
        # rejouable : rien de plus
        encore = P.execute()
        self.assertEqual(encore["annules"], [])
        self.assertEqual(set(encore["deja_annules"]), RENDUS_2026)
        for n in RENDUS_2026:
            self.assertEqual(len(self.commentaires("Maintenance Schedule", n)), 1)


if __name__ == "__main__":
    unittest.main()
