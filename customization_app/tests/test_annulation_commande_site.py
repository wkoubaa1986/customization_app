"""Annulation d'une commande — essai RÉEL sur la copie de prod (26/11/2026), sans trace : commit neutralisé, envois
interdits, file d'attente et index de recherche (MyISAM) neutralisés, tout annulé.

- SAL-ORD-2026-04284 (Adnane Gargouri) : un paiement, un BL, son échéancier F-UV-6w, deux visites d'AUTRES échéanciers
  soldées par elle, une tâche brouillon ;
- SAL-ORD-2025-03439 : un paiement, une écriture (Journal Entry), un BL, son échéancier (dont une visite qu'elle a soldée).
Ce qui est vérifié : paiements, écritures et BL disparaissent comme avant ; l'échéancier est ANNULÉ (plus supprimé) avec
son commentaire ; le calendrier des autres repasse « En Attente » ; la commande annulée se SUPPRIME encore (sans force,
comme depuis la liste) alors qu'un échéancier annulé la référençait — et sans le déliement, elle ne se supprimerait plus ;
le patch éteint le Server Script, une fois ; script encore actif ou non, le résultat est le même.
Relecture du 26/11/2026 : la fiche client nomme l'échéancier annulé ; une liste d'appels brouillon qui le cite
s'enregistre encore (ligne rattachée à un autre échéancier du client, ou retirée) ; aucune pièce jointe ne quitte le
disque pendant l'essai (remove_all neutralisé).
Sauté quand la copie ne contient plus le cas."""
import json
import unittest
from unittest import mock

import frappe

from customization_app import annulation_commande as A
from customization_app import relances_config as RC
from customization_app.Maintenance import relance_maintenance_sms as R
from customization_app.patches import script_annulation_commande_dans_l_app as P


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

SO_A, MS_A, AUTRES_A = "SAL-ORD-2026-04284", "MAT-MSH-2026-01332", ("MAT-MSH-2026-01126", "MAT-MSH-2026-01205")
SO_B, MS_B, JE_B = "SAL-ORD-2025-03439", "MAT-MSH-2025-00802", "ACC-JV-2025-00805"


def _interdit(*a, **k):
    raise AssertionError("envoi réel tenté")


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("annulation_site")
        self.journal, self.file = [], []
        self.mocks = [
            mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
            mock.patch.object(frappe, "log_error", lambda *a, **k: self.journal.append(k.get("title") or "")),
            mock.patch.object(R, "envoyer_sms_winsms", _interdit),
            mock.patch.object(frappe, "sendmail", _interdit),
            mock.patch.object(RC, "alerter", lambda *a, **k: None),
            mock.patch.object(frappe, "enqueue", lambda *a, **k: self.file.append(a[0] if a else k.get("method"))),
            # __global_search est en MyISAM : ni le rollback ni la file ne doivent y laisser de trace.
            mock.patch("frappe.utils.global_search.sync_value_in_queue", lambda *a, **k: None),
            mock.patch("frappe.model.delete_doc.delete_for_document", lambda *a, **k: None),
            # Une pièce supprimée emporte ses fichiers joints du DISQUE tout de suite (File.on_trash), hors transaction :
            # le rollback ne les rendrait pas (relecture du 26/11/2026).
            mock.patch("frappe.model.delete_doc.remove_all", lambda *a, **k: None),
            mock.patch.object(frappe, "publish_realtime", lambda *a, **k: None),
        ]
        for m in self.mocks:
            m.start()
        frappe.cache.delete_value("server_script_map")

    def tearDown(self):
        for m in self.mocks:
            m.stop()
        frappe.db.rollback(save_point="annulation_site")
        frappe.cache.delete_value("server_script_map")   # la carte reconstruite pendant l'essai ne doit pas rester

    def exiger(self, so, ms):
        if frappe.db.get_value("Sales Order", so, "docstatus") != 1 \
                or frappe.db.get_value("Maintenance Schedule", ms, "docstatus") != 1:
            self.skipTest("%s / %s ne sont plus dans l'état de la copie du 25/11/2026" % (so, ms))

    def annuler(self, so):
        doc = frappe.get_doc("Sales Order", so)
        doc.flags.sans_sms_annulation_auto = True
        doc.cancel()

    def liens(self, so):
        pe = frappe.db.sql_list("""select pe.name from `tabPayment Entry` pe join `tabPayment Entry Reference` per
                                   on per.parent = pe.name where per.reference_doctype = 'Sales Order'
                                   and per.reference_name = %s""", so)
        je = frappe.db.sql_list("select parent from `tabJournal Entry Account` where reference_type = 'Sales Order' "
                                "and reference_name = %s", so)
        dn = frappe.db.sql_list("select parent from `tabDelivery Note Item` where against_sales_order = %s", so)
        return pe, je, dn


class TestDerniersPointsSite(_SansTrace):
    """Relecture finale du 26/11/2026."""

    def test_reprise_d_un_echange_d_une_autre_commande_le_dit(self):
        so = "SAL-ORD-2026-02255"
        if frappe.db.get_value("Sales Order", so, "docstatus") != 1:
            self.skipTest("%s n'est plus validée" % so)
        bls = [b.name for b in A._bls_de(so)]
        reprises = A._reprises_d_autres_commandes(bls)
        if not reprises:
            self.skipTest("plus de reprise d'échange sur cette copie")
        self.assertIn("MAT-DN-2026-05844", [r["reprise"] for r in reprises])
        doc = frappe.get_doc("Sales Order", so)
        with self.assertRaises(frappe.ValidationError) as e:
            A._annuler_et_supprimer_bls(doc)
        self.assertIn("Annulez d'abord cet échange", str(e.exception))

    def test_le_client_se_supprime_malgre_un_echeancier_annule(self):
        appels = []
        with mock.patch.object(frappe, "get_all", lambda *a, **k: ["MS-ANNULE"] if k.get("filters", {}).get("docstatus") == 2 else []), \
                mock.patch.object(frappe, "delete_doc", lambda *a, **k: appels.append((a, k))):
            A.on_trash_customer(frappe._dict(name="Client X"))
        self.assertEqual(appels, [(("Maintenance Schedule", "MS-ANNULE"), {"force": True, "ignore_permissions": True})])
        self.assertIn("customization_app.annulation_commande.on_trash_customer",
                      [frappe.get_hooks("doc_events").get("Customer", {}).get("on_trash")] +
                      list(frappe.get_hooks("doc_events").get("Customer", {}).get("on_trash") or []))


class TestAnnulationSite(_SansTrace):
    def test_annulation_puis_suppression_de_SAL_ORD_2026_04284(self):
        self.exiger(SO_A, MS_A)
        # L'état d'AVANT le déploiement (sur un dev déjà migré le script est éteint) : le patch l'éteint et le journalise.
        frappe.db.set_value("Server Script", P.NOM, "disabled", 0, update_modified=False)
        P.execute()
        self.assertTrue(frappe.db.get_value("Server Script", P.NOM, "disabled"))
        pe, je, dn = self.liens(SO_A)
        self.assertTrue(pe and dn)
        self.annuler(SO_A)
        # paiements et BL partis, comme avant
        self.assertEqual(self.liens(SO_A), ([], [], []))
        for nom in pe:
            self.assertFalse(frappe.db.exists("Payment Entry", nom))
        for nom in dn:
            self.assertFalse(frappe.db.exists("Delivery Note", nom))
        # l'échéancier est ANNULÉ, avec son commentaire ; ses visites sont toujours là
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_A, ["docstatus", "status"]), (2, "Cancelled"))
        self.assertTrue(frappe.db.count("Maintenance Schedule Detail", {"parent": MS_A}))
        self.assertTrue(frappe.db.sql("""select 1 from tabComment where reference_doctype = 'Maintenance Schedule'
                                         and reference_name = %s and content like %s""", (MS_A, "%❌ Échéancier annulé%"
                                                                                           + SO_A + "%")))
        # les visites des AUTRES échéanciers soldées par elle repassent « En Attente »
        for autre in AUTRES_A:
            self.assertEqual(frappe.db.get_value("Maintenance Schedule", autre, "docstatus"), 1)
        self.assertFalse(frappe.db.count("Maintenance Schedule Detail", {"custom_sales_order": SO_A}))
        # la commande annulée se supprime encore, sans force
        frappe.delete_doc("Sales Order", SO_A)
        self.assertFalse(frappe.db.exists("Sales Order", SO_A))
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_A, "docstatus"), 2)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule Item", {"parent": MS_A}, "sales_order"), None)
        self.assertTrue(frappe.db.sql("""select 1 from tabComment where reference_doctype = 'Maintenance Schedule'
                                         and reference_name = %s and content like %s""", (MS_A, "%🗑️ Commande " + SO_A
                                                                                           + " supprimée%")))
        self.assertEqual(self.journal, ["Reprise du Server Script « %s »" % P.NOM])

    def test_sans_le_deliement_la_suppression_serait_refusee(self):
        self.exiger(SO_A, MS_A)
        self.annuler(SO_A)
        with mock.patch.object(A, "_delier_echeanciers_annules", lambda doc: {}):
            with self.assertRaises(frappe.LinkExistsError):
                frappe.delete_doc("Sales Order", SO_A)

    def test_ecriture_et_visite_de_son_propre_echeancier(self):
        self.exiger(SO_B, MS_B)
        P.execute()
        self.assertIn(JE_B, self.liens(SO_B)[1])
        visite = frappe.db.get_value("Maintenance Schedule Detail", {"parent": MS_B, "custom_sales_order": SO_B},
                                     ["name", "completion_status", "actual_date"], as_dict=True)
        self.annuler(SO_B)
        self.assertFalse(frappe.db.exists("Journal Entry", JE_B))
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_B, "docstatus"), 2)
        if visite:
            # l'historique de l'échéancier annulé n'est pas réécrit
            self.assertEqual(frappe.db.get_value("Maintenance Schedule Detail", visite.name,
                                                 ["custom_sales_order", "completion_status", "actual_date"]),
                             (SO_B, visite.completion_status, visite.actual_date))
        frappe.delete_doc("Sales Order", SO_B)
        self.assertFalse(frappe.db.exists("Sales Order", SO_B))
        if visite:
            self.assertEqual(frappe.db.get_value("Maintenance Schedule Detail", visite.name,
                                                 ["custom_sales_order", "completion_status"]),
                             (None, visite.completion_status))

    def test_script_encore_actif_meme_resultat(self):
        # Entre le déploiement et le patch, les deux tournent : le script passe après l'app et ne trouve plus rien.
        self.exiger(SO_B, MS_B)
        if frappe.db.get_value("Server Script", P.NOM, "disabled"):
            self.skipTest("le Server Script est déjà éteint sur cette copie")
        self.annuler(SO_B)
        self.assertEqual(self.liens(SO_B), ([], [], []))
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", MS_B, "docstatus"), 2)
        self.assertIsNone(A.annulation_commande_en_cours())

    def test_fiche_client_nomme_l_echeancier_annule(self):
        # Relecture du 26/11/2026 : la page Historique ne lit que les échéanciers validés — rien ne disait l'annulation.
        self.exiger(SO_A, MS_A)
        client = frappe.db.get_value("Sales Order", SO_A, "customer")
        self.annuler(SO_A)
        self.assertTrue(frappe.db.sql("""select 1 from tabComment where reference_doctype = 'Customer'
                                         and reference_name = %s and comment_type = 'Comment' and content like %s""",
                                      (client, "❌ Commande " + SO_A + " annulée le %" + MS_A + " annulé(s)%")))

    def test_liste_brouillon_qui_cite_l_echeancier(self):
        # Une ligne qui porte aussi l'osmoseur du client (MAT-MSH-2026-01126) y est rattachée ; une ligne du seul UV,
        # pas encore appelée, est retirée. La liste s'enregistre ensuite (CancelledLinkError avant).
        self.exiger(SO_A, MS_A)
        liste = frappe.db.get_value("Liste Appelle Entretien", {"docstatus": 0}, "name", order_by="creation desc")
        if not liste or frappe.db.get_value("Maintenance Schedule", AUTRES_A[0], "docstatus") != 1:
            self.skipTest("pas de liste brouillon, ou l'autre échéancier du client n'est plus validé")
        client = frappe.db.get_value("Sales Order", SO_A, "customer")
        n = frappe.db.count("Appelle Client", {"parent": liste})
        lignes = {}
        for cle, detail in (("deux", {"F-UV-6w": "2026-11-01", "AP-M-AJ-5-SM": "2026-05-01"}),
                            ("uv", {"F-UV-6w": "2026-11-01"})):
            n += 1
            d = frappe.get_doc({"doctype": "Appelle Client", "parent": liste, "parenttype": "Liste Appelle Entretien",
                                "parentfield": "clients", "idx": n, "client": client, "échéancier_dentretien": MS_A,
                                "detail_articles": json.dumps(detail)})
            d.db_insert()
            lignes[cle] = d.name
        # la liste qu'on valide (liste_validation) peut être tenue à l'écart
        self.assertEqual(len(A.lignes_listes_brouillon(MS_A)), 2)
        self.assertEqual(A.lignes_listes_brouillon(MS_A, sauf_listes=[liste]), [])
        self.annuler(SO_A)
        self.assertEqual(frappe.db.get_value("Appelle Client", lignes["deux"], "échéancier_dentretien"), AUTRES_A[0])
        self.assertFalse(frappe.db.exists("Appelle Client", lignes["uv"]))
        frappe.get_doc("Liste Appelle Entretien", liste).save(ignore_permissions=True)   # avant : CancelledLinkError

    def test_le_patch_est_rejouable(self):
        if not frappe.db.exists("Server Script", P.NOM):
            self.skipTest("Server Script absent")
        premier = P.execute()["decision"]
        self.assertIn(premier, ("eteindre", "deja_eteint"))
        self.assertEqual(P.execute()["decision"], "deja_eteint")

    def test_code_modifie_en_base_laisse_actif(self):
        if not frappe.db.exists("Server Script", P.NOM) or frappe.db.get_value("Server Script", P.NOM, "disabled"):
            self.skipTest("Server Script absent ou déjà éteint")
        frappe.db.set_value("Server Script", P.NOM, "script", P.REPRIS + "\nfrappe.msgprint('ajout')",
                            update_modified=False)
        self.assertEqual(P.execute()["decision"], "modifie")
        self.assertFalse(frappe.db.get_value("Server Script", P.NOM, "disabled"))


if __name__ == "__main__":
    unittest.main()
