"""Échéanciers, correctifs du 25/11/2026 — essai SUR LE SITE (copie de prod), sans trace : commit neutralisé, tout annulé.

- la prolongation : la requête de nuit (echeanciers_epuises) et la règle pure (articles_a_prolonger) disent la même chose ;
- la boucle de nuit : SAL-ORD-2026-03459 / 03460 (deux AP-M-AJ-5-SM, client Abdallah chotrana) ne se suppriment plus ;
- les deux échéanciers fantômes de Khadija Moalla partent, et seulement si tout est encore vrai.
Sauté quand la copie ne contient plus le cas."""
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, nowdate

from customization_app import relances_config as RC
from customization_app.Maintenance import update_schedule as U
from customization_app.patches import echeanciers_fantomes_moalla as F

SO_A, SO_B, DEJA = "SAL-ORD-2026-03459", "SAL-ORD-2026-03460", "MAT-MSH-2026-01462"


class _SansTrace(unittest.TestCase):
    """Commit neutralisé, tout annulé — et frappe.log_error capturé : tabError Log est en MyISAM, le rollback ne
    l'annule pas (relecture du 26/11/2026 : 16 faux comptes rendus « supprimé » laissés sur le dev)."""

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("lot1")
        self.journal = []
        self.mocks = [mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
                      mock.patch.object(frappe, "log_error", lambda *a, **k: self.journal.append(k.get("message") or (a[0] if a else ""))),
                      # File Redis de la recherche globale et __global_search (MyISAM) : le rollback ne les annule pas.
                      mock.patch.dict(frappe.local.conf, {"disable_global_search": 1})]
        for m in self.mocks:
            m.start()

    def tearDown(self):
        for m in self.mocks:
            m.stop()
        frappe.db.rollback(save_point="lot1")


class TestProlongationSite(_SansTrace):
    def _attendus(self, jour):
        lignes = {}
        for r in frappe.db.sql("""select d.parent, d.item_code, d.scheduled_date, d.actual_date, d.completion_status
                                  from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                                  where ms.docstatus = 1""", as_dict=True):
            lignes.setdefault(r.parent, []).append(r)
        return {ms for ms, rows in lignes.items() if U.articles_a_prolonger(rows, jour)}

    def test_la_requete_de_nuit_suit_la_regle(self):
        # Dans un an, beaucoup d'articles n'auront plus de visite à venir : la comparaison a toujours de la matière.
        loin = add_days(nowdate(), 365)
        attendus = self._attendus(loin)
        self.assertGreater(len(attendus), 0)
        self.assertEqual(set(U.echeanciers_epuises(loin)), attendus)
        self.assertEqual(set(U.echeanciers_epuises(nowdate())), self._attendus(nowdate()))   # et aujourd'hui (peut être vide)

    def test_apres_une_nuit_il_ne_reste_que_les_flux_en_double(self):
        avant = U.echeanciers_epuises()
        if not avant:
            self.skipTest("aucun échéancier épuisé sur cette copie (la nuit est déjà passée)")
        with mock.patch.object(RC, "alerter", lambda *a, **k: None):
            U.prolonger_echeanciers_epuises()
        self.assertEqual(U.echeanciers_a_prolonger(), [])
        # Ce qui reste épuisé, ce sont des articles gardés gelés exprès (jumeau / porte-filtre), chacun avec son motif.
        for name in U.echeanciers_epuises():
            ms = frappe.get_doc("Maintenance Schedule", name)
            a = U.articles_a_prolonger(ms.schedules, nowdate())
            self.assertEqual(set(a), set(U.articles_geles(ms, a, nowdate())), name)
        # La nuit suivante ne rajoute rien.
        self.assertEqual(U.prolonger_echeanciers_epuises(), 0)

    def test_les_jumeaux_mesures_ne_repartent_pas(self):
        # Ben Salem Néjib : MAT-MSH-2023-00847 double MAT-MSH-2023-01074 (mêmes commandes), qui a encore une visite à venir.
        if not U.echeanciers_epuises() or "MAT-MSH-2023-00847" not in U.echeanciers_epuises():
            self.skipTest("MAT-MSH-2023-00847 n'est plus épuisé sur cette copie")
        ms = frappe.get_doc("Maintenance Schedule", "MAT-MSH-2023-00847")
        n0 = len(ms.schedules)
        self.assertEqual(U.extend_schedule_for_sms(ms, aujourd_hui=nowdate()), 0)
        self.assertEqual(len(ms.schedules), n0)
        self.assertTrue(any("MAT-MSH-2023-01074" in m for m in ms.flags.articles_geles.values()))


class TestBoucleSite(_SansTrace):
    def _commande(self, so):
        cfg = RC.config()
        return next((c for c in U.commandes_a_traiter(cfg) if c["sales_order"] == so), None)

    def test_commande_vend_la_famille(self):
        fams = RC.familles()
        self.assertIs(U.commande_vend_la_famille(SO_A, "RO_DOM", fams, {}), True)
        self.assertIsNone(U.commande_vend_la_famille("SAL-ORD-0000-00000", "RO_DOM", fams, {}))
        self.assertIsNone(U.commande_vend_la_famille(None, "RO_DOM", fams, {}))
        entretien = frappe.db.sql_list("""select so.name from `tabSales Order` so where so.docstatus = 1 and not exists (
                                          select 1 from `tabSales Order Item` i where i.parent = so.name and i.item_code like 'AP-%%')
                                          and exists (select 1 from `tabSales Order Item` i where i.parent = so.name and i.item_code = 'M-E-OD')
                                          limit 1""")
        if entretien:
            self.assertIs(U.commande_vend_la_famille(entretien[0], "RO_DOM", fams, {}), False)

    def test_la_deuxieme_machine_n_efface_plus_la_premiere(self):
        if not frappe.db.exists("Maintenance Schedule", {"name": DEJA, "docstatus": 1}):
            self.skipTest("l'échéancier %s n'est plus sur cette copie" % DEJA)
        i_sal = self._commande(SO_A)
        if not i_sal or SO_A in U.commandes_couvertes():
            self.skipTest("%s n'est plus à traiter sur cette copie" % SO_A)
        cree, _r = U.traiter_commande(i_sal, RC.config(), RC.familles(), {})
        self.assertTrue(cree)
        self.assertTrue(frappe.db.exists("Maintenance Schedule", {"name": DEJA, "docstatus": 1}))     # plus supprimé
        self.assertIn(SO_A, U.commandes_couvertes())
        self.assertIn(SO_B, U.commandes_couvertes())                                                # la nuit suivante : rien
        noms = frappe.db.sql_list("""select distinct msi.parent from `tabMaintenance Schedule Item` msi
                                     join `tabMaintenance Schedule` ms on ms.name = msi.parent
                                     where ms.docstatus = 1 and msi.sales_order in %s""", ((SO_A, SO_B),))
        self.assertEqual(len(noms), 2)                                                              # deux machines suivies

    def test_une_vraie_devinette_part_toujours(self):
        # Une devinette fabriquée pour l'essai : machine type, née d'une commande d'entretien qui a soldé sa 1re visite
        # (comme toute devinette de la nuit : update_single_machine_schedule). Relecture du 26/11/2026 : cette visite de
        # naissance la gardait pour toujours ; l'essai d'avant, SANS elle, passait alors que le vrai chemin ne marchait pas.
        entretien = frappe.db.sql("""select so.name, so.customer from `tabSales Order` so where so.docstatus = 1 and not exists (
                                     select 1 from `tabSales Order Item` i where i.parent = so.name and i.item_code like 'AP-%%')
                                     and exists (select 1 from `tabSales Order Item` i where i.parent = so.name and i.item_code = 'M-E-OD')
                                     and not exists (select 1 from `tabMaintenance Schedule Item` msi where msi.sales_order = so.name)
                                     limit 1""", as_dict=True)
        if not entretien:
            self.skipTest("aucune commande d'entretien libre sur cette copie")
        so, client = entretien[0].name, entretien[0].customer
        fams, cfg = RC.familles(), RC.config()
        type_code = RC.famille("RO_DOM", fams)["article_type"]
        item = frappe.db.get_value("Item", type_code, ["item_code", "item_name"], as_dict=True)
        ms = U._nouvel_echeancier(client, nowdate(), [{"item_code": item.item_code, "item_name": item.item_name, "sales_order": so}], cfg)
        with mock.patch.object(U, "_reactiver_client", lambda *a, **k: None):
            self.assertTrue(U.shift_schedule_for_delivery(ms, nowdate(), so, "RO_DOM", fams, {}))       # visite de naissance
        self.assertTrue(frappe.db.exists("Maintenance Schedule Detail", {"parent": ms.name, "custom_sales_order": so,
                                                                          "actual_date": ["is", "set"]}))
        U._retirer_echeancier_devine(client, "RO_DOM", fams, {})
        # Lot 4 (26/11/2026) : annulée, plus supprimée — elle reste lisible et couvre toujours sa commande d'origine.
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms.name, "docstatus"), 2)
        self.assertIn(so, U.commandes_couvertes())


class TestFantomesSite(_SansTrace):
    def setUp(self):
        super().setUp()
        if not all(frappe.db.exists("Maintenance Schedule", n) for n in F.FANTOMES):
            self.tearDown()
            self.skipTest("les fantômes ne sont plus sur cette copie")

    def test_les_deux_fantomes_partent_la_vraie_machine_reste(self):
        traces = {n: frappe.db.count("Deleted Document", {"deleted_doctype": "Maintenance Schedule", "deleted_name": n}) for n in F.FANTOMES}
        compte_rendu = F.execute()
        for nom in F.FANTOMES:
            self.assertFalse(frappe.db.exists("Maintenance Schedule", nom))
            # Une trace de PLUS : les deux noms en ont déjà une (les originaux supprimés le 20/08/2026).
            self.assertEqual(frappe.db.count("Deleted Document", {"deleted_doctype": "Maintenance Schedule", "deleted_name": nom}),
                             traces[nom] + 1)
        self.assertTrue(all("supprimé" in l for l in compte_rendu), compte_rendu)
        self.assertTrue(frappe.db.exists("Maintenance Schedule", {"name": F.VRAI, "docstatus": 1}))
        self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Customer", "reference_name": F.CLIENTE,
                                                     "content": ["like", "%fantômes%"]}))
        self.assertEqual(len(self.journal), 1)                                                       # le compte rendu, capturé
        self.assertTrue(all("déjà absent" in l for l in F.execute()))                                # rejoué : sans effet

    def test_dans_le_doute_on_garde(self):
        frappe.db.set_value("Maintenance Schedule", F.VRAI, "docstatus", 2, update_modified=False)   # la vraie machine absente
        F.execute()
        for nom in F.FANTOMES:
            self.assertTrue(frappe.db.exists("Maintenance Schedule", nom))
        ms = frappe.get_doc("Maintenance Schedule", "MAT-MSH-2026-00796")
        self.assertIn("vraie machine", F.motif_de_garder(ms, {"name": "SAL-ORD-2026-03004", "existe": False, "lignes_appel": 0}, None))
        self.assertIn("existe de nouveau", F.motif_de_garder(ms, {"name": "SAL-ORD-2026-03004", "existe": True, "lignes_appel": 0}, F.VRAI))
        self.assertIn("lignes machine", F.motif_de_garder(ms, {"name": "SAL-ORD-2026-99999", "existe": False, "lignes_appel": 0}, F.VRAI))
        self.assertIn("ligne(s) d'appel", F.motif_de_garder(ms, {"name": "SAL-ORD-2026-03004", "existe": False, "lignes_appel": 2}, F.VRAI))
        self.assertEqual(F.motif_de_garder(ms, {"name": "SAL-ORD-2026-03004", "existe": False, "lignes_appel": 0}, F.VRAI), "")


if __name__ == "__main__":
    unittest.main()
