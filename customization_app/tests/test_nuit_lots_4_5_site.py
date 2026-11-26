"""Passage de nuit, lots 4 et 5 du 26/11/2026 — essai SUR LE SITE (copie de prod), sans trace.

Commit neutralisé, tout annulé (savepoint), Error Log capturé (MyISAM : le rollback ne l'annule pas), index de recherche
globale coupé (la file Redis « global_search_queue » et __global_search, MyISAM, ne s'annulent pas non plus), SMS /
e-mail interdits (ils lèvent), alertes et entonnoir capturés, date du passage fixée au 26/11/2026. Les échéanciers
d'essai sont fabriqués sur de vrais clients et de vraies commandes (liens valides) qui n'en portent pas ; un test se
saute si la copie n'a pas ce qu'il faut.

Relecture du 26/11/2026 : la devinette passe par le VRAI chemin (née d'un achat de cartouches, 1re visite soldée à sa
naissance, puis la vraie machine), trois nuits de suite AVEC des annulations (devinette remplacée, B2B, machine d'un
« Non » annulée à la main), le B2B redevenu particulier, la réponse « pas de système » de l’ancien Server Script."""
import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, getdate
from frappe.utils import global_search as GS

from customization_app import entonnoir_relances as ER
from customization_app import relances_config as RC
from customization_app.Maintenance import relance_maintenance_sms as R
from customization_app.Maintenance import update_schedule as U

JOUR = "2026-11-26"
OSMOSEUR, PORTE_FILTRE, ADOUCISSEUR = "AP-M-AJ-5-SM", "P-F-T-10'-T-SC", "Ad-MB-30L"
CHARBON = "C-10'-CTO"


def _interdit(*a, **k):
    raise AssertionError("envoi réel tenté pendant un essai")


class _SansTrace(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("lots45")
        frappe.local._config_relances = None
        self.journal, self.notes = [], []
        self.mocks = [mock.patch.object(frappe.db, "commit", lambda *a, **k: None),
                      mock.patch.object(frappe, "log_error", lambda *a, **k: self.journal.append(k.get("message") or (a[0] if a else ""))),
                      mock.patch.object(frappe, "sendmail", _interdit),
                      mock.patch.object(R, "envoyer_sms_winsms", _interdit),
                      mock.patch.object(RC, "alerter", lambda *a, **k: None),
                      mock.patch.object(ER, "noter", lambda source, compteurs, jour=None: self.notes.append((source, compteurs)) or True),
                      mock.patch.object(U, "nowdate", lambda: JOUR),
                      mock.patch.object(RC, "nowdate", lambda: JOUR),
                      # frappe.model.document importe update_global_search par son nom : seul le réglage le coupe partout.
                      mock.patch.dict(frappe.local.conf, {"disable_global_search": 1})]
        for m in self.mocks:
            m.start()
        self.cfg, self.fams = RC.config(), RC.familles()
        for code in (OSMOSEUR, PORTE_FILTRE, ADOUCISSEUR, CHARBON):
            if not frappe.db.exists("Item", code):
                self.tearDown()
                self.skipTest("article %s absent" % code)

    def tearDown(self):
        for m in reversed(self.mocks):
            m.stop()
        self.mocks = []
        frappe.db.rollback(save_point="lots45")
        frappe.local._config_relances = None

    # ── fabrique ────────────────────────────────────────────────────────────────
    def client(self, interesse="Oui", sms=None, b2b=False, sauf=()):
        b2b_l = tuple(self.cfg["groupes_b2b_liste"]) or ("",)
        hors = tuple(self.cfg["clients_proteges_liste"] + U.clients_generiques(self.cfg) + list(sauf)) or ("",)
        cond = "c.customer_group in %(b2b)s" if b2b else "ifnull(c.customer_group, '') not in %(b2b)s"
        noms = frappe.db.sql_list("""select c.name from tabCustomer c where c.disabled = 0 and %s and c.name not in %%(hors)s
                                     and c.custom_intéressé_par_le_service_entretien = %%(i)s
                                     and (%%(sms)s is null or c.custom_envoi_sms = %%(sms)s)
                                     and ifnull(c.custom_statut_relance, '') = ''
                                     and not exists (select 1 from `tabMaintenance Schedule` ms where ms.customer = c.name)
                                     order by c.creation desc limit 1""" % cond, {"b2b": b2b_l, "hors": hors, "i": interesse, "sms": sms})
        if not noms:
            self.skipTest("aucun client %s / %s sans échéancier" % (interesse, "B2B" if b2b else "particulier"))
        return noms[0]

    def commandes(self, n, sans_famille=None):
        """n commandes soumises qu'aucun échéancier ne couvre ; `sans_famille` : qui ne vendent pas cette famille
        (l'origine d'une devinette, sinon commande_vend_la_famille la garde)."""
        if not hasattr(self, "_libres"):
            couvertes = U.commandes_couvertes()
            self._libres = [so for so in frappe.db.sql_list("select name from `tabSales Order` where docstatus = 1 order by creation desc limit 400")
                            if so not in couvertes]
        choisies = [so for so in self._libres
                    if sans_famille is None or U.commande_vend_la_famille(so, sans_famille, self.fams, {}) is False][:n]
        if len(choisies) < n:
            self.skipTest("pas assez de commandes libres")
        self._libres = [so for so in self._libres if so not in choisies]
        return choisies

    def echeancier(self, client, debut, codes, so):
        lignes = [{"item_code": c, "item_name": frappe.db.get_value("Item", c, "item_name"), "sales_order": so} for c in codes]
        return U._nouvel_echeancier(client, debut, lignes, self.cfg)

    @staticmethod
    def premiere(ms_name, code):
        return frappe.db.sql("""select name, scheduled_date, actual_date, custom_sales_order from `tabMaintenance Schedule Detail`
                                where parent = %s and item_code = %s order by scheduled_date limit 1""", (ms_name, code), as_dict=True)[0]

    @staticmethod
    def ms_de(so):
        return frappe.db.get_value("Maintenance Schedule Item", {"sales_order": so}, "parent")

    def commentaires(self, doctype, name, motif):
        return frappe.db.count("Comment", {"reference_doctype": doctype, "reference_name": name, "content": ["like", "%" + motif + "%"]})

    @staticmethod
    def photo(clients):
        """(échéanciers (nom, docstatus), visites faites, commentaires) de ces clients — pour comparer deux nuits."""
        clients = tuple(clients)
        ms = frappe.db.sql("select name, docstatus from `tabMaintenance Schedule` where customer in %s order by name", (clients,))
        noms = tuple(n for n, _d in ms) or ("",)
        faites = frappe.db.sql("""select parent, item_code, actual_date, custom_sales_order from `tabMaintenance Schedule Detail`
                                  where parent in %s and actual_date is not null order by parent, idx""", (noms,))
        comm = frappe.db.sql("""select count(*) from tabComment where (reference_doctype = 'Customer' and reference_name in %s)
                                or (reference_doctype = 'Maintenance Schedule' and reference_name in %s)""", (clients, noms))[0][0]
        return ms, faites, comm


class TestAnnulerAuLieuDeSupprimer(_SansTrace):
    def test_le_nettoyage_b2b_annule_et_ne_relit_pas(self):
        client = self.client("Non", b2b=True)
        so = self.commandes(1)[0]
        ms = self.echeancier(client, "2026-09-01", [OSMOSEUR], so)
        r = U.verify_data_base(self.cfg)
        self.assertGreaterEqual(r["annules"], 1)
        self.assertEqual(r["supprimes"], 0)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms.name, "docstatus"), 2)           # là, annulé
        self.assertEqual(self.commentaires("Maintenance Schedule", ms.name, "annulé le 26/11/2026"), 1)
        self.assertEqual(self.commentaires("Customer", client, ms.name + " annulé le 26/11/2026"), 1)
        self.assertIn(so, U.commandes_couvertes())                                                       # il couvre sa commande
        U.verify_data_base(self.cfg)                                                                      # 2e nuit : pas relu
        self.assertEqual(self.commentaires("Maintenance Schedule", ms.name, "annulé le"), 1)
        self.assertEqual(self.journal, [])

    def test_un_b2b_redevenu_particulier_libere_ses_commandes(self):
        # Relecture du 26/11/2026 : la suppression d'avant le lot 4 libérait la commande ; un client mal classé B2B puis
        # corrigé retrouvait sa machine la nuit suivante. Annulé, l'échéancier ne doit pas la confisquer pour toujours.
        client = self.client("Non", b2b=True)
        so = self.commandes(1)[0]
        ms = self.echeancier(client, "2026-09-01", [OSMOSEUR], so)
        U.verify_data_base(self.cfg)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms.name, "docstatus"), 2)
        self.assertIn(so, U.commandes_couvertes(self.cfg))                     # B2B : couverte (et de toute façon pas lue)
        frappe.db.set_value("Customer", client, "customer_group", "Individuel")
        self.assertNotIn(so, U.commandes_couvertes(self.cfg))                  # particulier : libre
        i_sal = {"sales_order": so, "customer": client, "delivery_date": "2026-09-01", "items": OSMOSEUR}
        self.assertTrue(U.create_maintenance_for_machines(i_sal, self.cfg, self.fams, {}))
        self.assertIn(so, U.commandes_couvertes(self.cfg))                     # couverte par le nouvel échéancier
        frappe.db.set_value("Customer", client, "customer_group", self.cfg["groupes_b2b_liste"][0])
        self.assertIn(so, U.commandes_couvertes(self.cfg))

    def test_une_annulation_pour_un_autre_motif_couvre_toujours_un_particulier(self):
        client = self.client("Non")
        so = self.commandes(1)[0]
        ms = self.echeancier(client, "2026-09-01", [OSMOSEUR], so)
        U.annuler_echeancier(ms.name, "essai : pas de système")
        self.assertIn(so, U.commandes_couvertes(self.cfg))

    def test_le_nettoyage_ne_touche_plus_aux_non(self):
        client = self.client("Non", sms="Non")
        ms = self.echeancier(client, "2026-09-01", [OSMOSEUR], self.commandes(1)[0])
        U.verify_data_base(self.cfg)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms.name, "docstatus"), 1)

    def test_la_vraie_devinette_est_annulee_quand_la_machine_arrive(self):
        # Relecture du 26/11/2026 : le VRAI chemin. La devinette naît d'un achat de cartouches et solde sa 1re visite avec
        # lui ; avant le correctif, cette visite la protégeait et elle n'était jamais annulée (devines_annules = 0).
        client = self.client("Oui")
        so_conso, so_machine = self.commandes(2, sans_famille="RO_DOM")
        stats = {}
        r = U.update_maintenance_schedule({"sales_order": so_conso, "customer": client, "delivery_date": "2026-08-10",
                                           "items": CHARBON}, self.cfg, self.fams, {}, stats=stats)
        self.assertEqual(r, "guess")
        devine = self.ms_de(so_conso)
        self.assertTrue(frappe.db.exists("Maintenance Schedule Detail", {"parent": devine, "custom_sales_order": so_conso,
                                                                          "actual_date": ["is", "set"]}))   # visite de naissance
        cree, _r = U.traiter_commande({"sales_order": so_machine, "customer": client, "delivery_date": "2026-10-10",
                                       "items": OSMOSEUR}, self.cfg, self.fams, {}, stats=stats)
        self.assertTrue(cree)
        vraie = self.ms_de(so_machine)
        self.assertNotEqual(vraie, devine)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", devine, "docstatus"), 2)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", vraie, "docstatus"), 1)
        self.assertEqual(stats.get("devines_annules"), 1)
        for doctype, name in (("Maintenance Schedule", devine), ("Customer", client)):
            self.assertEqual(self.commentaires(doctype, name, "Remplacé par l’échéancier " + vraie), 1, doctype)
        self.assertIn(so_conso, U.commandes_couvertes())                                                 # l'ancien achat ne rejoue pas
        self.assertEqual(self.journal, [])

    def test_une_devinette_qui_a_servi_reste(self):
        # Une visite faite par une AUTRE commande (un entretien payé) : histoire propre, on garde.
        client = self.client("Oui")
        so_conso, so_entretien, so_machine = self.commandes(3, sans_famille="RO_DOM")
        U.update_maintenance_schedule({"sales_order": so_conso, "customer": client, "delivery_date": "2026-01-10",
                                       "items": CHARBON}, self.cfg, self.fams, {})
        devine = self.ms_de(so_conso)
        prochaine = frappe.db.sql("""select scheduled_date from `tabMaintenance Schedule Detail` where parent = %s and actual_date is null
                                     order by scheduled_date limit 1""", devine)[0][0]
        r = U.update_maintenance_schedule({"sales_order": so_entretien, "customer": client, "delivery_date": str(add_days(prochaine, 5)),
                                           "items": "M-E-OD"}, self.cfg, self.fams, {})
        self.assertEqual(r, "update")
        stats = {}
        U.traiter_commande({"sales_order": so_machine, "customer": client, "delivery_date": str(add_days(prochaine, 30)),
                            "items": OSMOSEUR}, self.cfg, self.fams, {}, stats=stats)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", devine, "docstatus"), 1)
        self.assertFalse(stats.get("devines_annules"))

    def test_le_doublon_porte_filtre_est_annule_et_libere_sa_commande(self):
        client = self.client("Oui")
        so_osmo, so_pf = self.commandes(2)
        osmo = self.echeancier(client, "2026-06-01", [OSMOSEUR], so_osmo)
        pf = self.echeancier(client, "2026-07-01", [PORTE_FILTRE], so_pf)
        doublons = [d["name"] for d in U.echeanciers_accessoires_en_double(self.fams)]
        self.assertIn(pf.name, doublons)
        # Le retrait ne vise que le doublon fabriqué (la copie en a d'autres, hors de cet essai).
        seul = [d for d in U.echeanciers_accessoires_en_double(self.fams) if d["name"] == pf.name]
        with mock.patch.object(U, "echeanciers_accessoires_en_double", lambda fams=None: seul):
            r = U.retirer_echeanciers_accessoires_en_double(self.fams)
        self.assertEqual((r["annules"], r["erreurs"]), (1, 0))
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", pf.name, "docstatus"), 2)
        self.assertIsNone(frappe.db.get_value("Maintenance Schedule Item", {"parent": pf.name}, "sales_order"))
        self.assertNotIn(so_pf, U.commandes_couvertes())                                                 # libérée exprès
        self.assertEqual(self.commentaires("Customer", client, "Remplacé par l’échéancier " + osmo.name), 1)
        self.assertNotIn(pf.name, [d["name"] for d in U.echeanciers_accessoires_en_double(self.fams)])   # pas relu
        # Libre, la commande du porte-filtre ne recrée rien : le client est suivi.
        i_sal = {"sales_order": so_pf, "customer": client, "delivery_date": "2026-07-01", "items": PORTE_FILTRE}
        self.assertFalse(U.create_maintenance_for_machines(i_sal, self.cfg, self.fams, {}))

    def test_un_essai_ne_nourrit_pas_l_index_de_recherche(self):
        # Relecture du 26/11/2026 : 9 917 entrées attendaient dans la file Redis, dont des échéanciers d'essai annulés
        # par le rollback ; elles seraient écrites dans __global_search (MyISAM) sous des noms inexistants.
        envoyes = []
        with mock.patch.object(GS, "sync_value_in_queue", lambda value: envoyes.append(value)):
            ms = self.echeancier(self.client("Oui"), "2026-09-01", [OSMOSEUR], self.commandes(1)[0])
            U.annuler_echeancier(ms.name, "essai")
        self.assertEqual(envoyes, [])


class TestClientsNon(_SansTrace):
    def test_la_machine_d_un_non_a_son_echeancier_sans_relance(self):
        client = self.client("Non")
        so_machine, so_entretien, so_cartouche = self.commandes(3)
        stats = {}
        i_sal = {"sales_order": so_machine, "customer": client, "delivery_date": "2026-09-01", "items": OSMOSEUR}
        self.assertTrue(U.create_maintenance_for_machines(i_sal, self.cfg, self.fams, {}, stats=stats))
        self.assertEqual(stats, {"new_ms_non_interesses": 1})
        ms = self.ms_de(so_machine)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms, "docstatus"), 1)
        self.assertIn(so_machine, U.commandes_couvertes())
        if not self.cfg.get("relancer_non_interesses"):
            self.assertIn(client, RC.clients_exclus_relance(self.cfg))                                   # aucune relance
        # Pas de devinette pour un « Non » : une cartouche, sans échéancier de sa famille, ne crée rien.
        autre = self.client("Non", sauf=[client])
        r = U.update_maintenance_schedule({"sales_order": so_cartouche, "customer": autre, "delivery_date": "2026-10-01",
                                           "items": CHARBON}, self.cfg, self.fams, {})
        self.assertEqual(r, "skip")
        self.assertFalse(frappe.db.exists("Maintenance Schedule", {"customer": autre}))
        # Il paie un entretien : la visite s'inscrit, et il reste « Non » (son statut « À requalifier » est levé).
        frappe.db.set_value("Customer", client, "custom_statut_relance", "À requalifier")
        premiere = self.premiere(ms, OSMOSEUR)
        livraison = str(add_days(getdate(premiere.scheduled_date), 10))
        r = U.update_maintenance_schedule({"sales_order": so_entretien, "customer": client, "delivery_date": livraison,
                                           "items": "M-E-OD"}, self.cfg, self.fams, {})
        self.assertEqual(r, "update")
        self.assertEqual(self.premiere(ms, OSMOSEUR).custom_sales_order, so_entretien)
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_intéressé_par_le_service_entretien"), "Non")
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_statut_relance") or "", "")
        self.assertEqual(self.commentaires("Customer", client, "reste « Non »"), 1)

    def test_un_non_qui_paie_un_entretien_recoit_un_echeancier_sans_relance(self):
        # Sa demande du 26/11/2026 : 11 entretiens payés en 45 jours par des « Non » sans échéancier n'étaient inscrits nulle
        # part (Hbib Ghrairi, Nizar Chamli…). Avec la main-d'œuvre d'entretien, la machine type est devinée — sans relance.
        client = self.client("Non")
        so_entretien, so_coupe = self.commandes(2)
        stats = {}
        r = U.update_maintenance_schedule({"sales_order": so_entretien, "customer": client, "delivery_date": "2026-10-13",
                                           "items": "M-E-OD,," + CHARBON}, self.cfg, self.fams, {}, stats=stats)
        self.assertEqual((r, stats.get("devines_non_interesses")), ("guess", 1))
        ms = self.ms_de(so_entretien)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms, ["docstatus", "customer"]), (1, client))
        type_code = RC.famille("RO_DOM", self.fams)["article_type"]
        premiere = self.premiere(ms, type_code)
        self.assertEqual((str(premiere.actual_date), premiere.custom_sales_order), ("2026-10-13", so_entretien))   # l'entretien est inscrit
        self.assertEqual(frappe.db.get_value("Customer", client, "custom_intéressé_par_le_service_entretien"), "Non")  # il reste « Non »
        if not self.cfg.get("relancer_non_interesses"):
            self.assertIn(client, RC.clients_exclus_relance(self.cfg))                                              # aucune relance
        # Réglage décoché : plus de devinette pour un « Non », même avec main-d'œuvre.
        autre = self.client("Non", sauf=[client])
        cfg0 = dict(self.cfg, echeancier_non_avec_entretien=0)
        self.assertEqual(U.update_maintenance_schedule({"sales_order": so_coupe, "customer": autre, "delivery_date": "2026-10-13",
                                                        "items": "M-E-OD"}, cfg0, self.fams, {}), "skip")
        self.assertFalse(frappe.db.exists("Maintenance Schedule", {"customer": autre}))

    def test_regle_pure_de_la_devinette_pour_un_non(self):
        mo, cartouche = [{"item_code": "M-E-OD"}], [{"item_code": CHARBON}]
        self.assertTrue(U.devinette_pour_un_non(mo, {}, self.fams))                       # réglage absent = oui
        self.assertFalse(U.devinette_pour_un_non(cartouche, {}, self.fams))               # cartouche seule : rien
        self.assertFalse(U.devinette_pour_un_non(mo, {"echeancier_non_avec_entretien": 0}, self.fams))
        self.assertTrue(U.devinette_pour_un_non(mo, {"echeancier_non_avec_entretien": ""}, self.fams))

    def test_le_commentaire_de_reactivation_suit_le_reglage(self):
        # « Relancer les non intéressés » coché : le « Non » réactivé est de nouveau relancé, le commentaire le dit.
        client = self.client("Non")
        for relancer, attendu in ((0, "toujours ni SMS ni appel"), (1, "peut de nouveau être relancé")):
            frappe.db.set_value("Customer", client, "custom_statut_relance", "À requalifier")
            U._reactiver_client(client, dict(self.cfg, relancer_non_interesses=relancer))
            self.assertEqual(self.commentaires("Customer", client, attendu), 1, relancer)
            self.assertEqual(frappe.db.get_value("Customer", client, "custom_intéressé_par_le_service_entretien"), "Non")

    def test_ni_passager_ni_b2b(self):
        so = self.commandes(1)[0]
        for client in U.clients_generiques(self.cfg) + [self.client("Non", b2b=True)]:
            i_sal = {"sales_order": so, "customer": client, "delivery_date": "2026-09-01", "items": OSMOSEUR}
            self.assertFalse(U.create_maintenance_for_machines(i_sal, self.cfg, self.fams, {}), client)
            self.assertEqual(U.update_maintenance_schedule(dict(i_sal, items="M-E-OD"), self.cfg, self.fams, {}), "skip")
        generiques = set(U.clients_generiques(self.cfg))
        self.assertFalse(generiques & {c["customer"] for c in U.commandes_a_traiter(self.cfg)})

    def test_un_non_ne_passe_plus_a_requalifier(self):
        non = self.client("Non")
        oui = self.client("Oui")
        with mock.patch.object(U, "cycles_sans_reponse_par_client", lambda cfg=None, statut="": {non: 9, oui: 9}), \
                mock.patch.object(RC, "clients_avec_rdv", lambda *a, **k: set()), \
                mock.patch.object(RC, "clients_reportes", lambda *a, **k: set()):
            cfg = dict(self.cfg, cycles_sans_reponse=3, relancer_non_interesses=0)
            self.assertEqual(U.clients_a_requalifier(cfg), [oui])
            self.assertEqual(sorted(U.clients_a_requalifier(dict(cfg, relancer_non_interesses=1))), sorted([non, oui]))


class TestPasDeSysteme(_SansTrace):
    """L'ancien Server Script « validation finale liste appelle » SUPPRIME l'échéancier d'une réponse « pas de système ».
    La nuit, qui lit désormais les clients « Non », ne doit pas recréer la machine que le client dit ne pas avoir."""

    def _liste_validee(self, client, ms_name, jour):
        """Une liste d'appel validée, écrite directement (db_insert : ni validate ni hooks) — annulée par le rollback.
        Créée MAINTENANT, après l'échéancier qu'elle appelle (comme la liste de 07:00) ; validée le `jour`."""
        from customization_app.liste_appels import PAS_DE_SYSTEME
        quand = frappe.utils.now()
        liste = frappe.get_doc({"doctype": "Liste Appelle Entretien", "name": "ESSAI-LOTS45-" + frappe.generate_hash(length=8),
                                "type_liste": "Normal", "date": jour, "date_fin": jour, "docstatus": 1,
                                "creation": quand, "modified": quand})
        liste.db_insert()
        frappe.get_doc({"doctype": "Appelle Client", "name": frappe.generate_hash(length=10), "parent": liste.name,
                        "parenttype": "Liste Appelle Entretien", "parentfield": "clients", "idx": 1, "docstatus": 1,
                        "client": client, "échéancier_dentretien": ms_name, "a_été_appelé": 1, "resume_appel": PAS_DE_SYSTEME,
                        "creation": quand, "modified": quand}).db_insert()
        return liste.name

    def test_la_nuit_ne_recree_pas_la_machine_d_un_pas_de_systeme_supprime(self):
        # Client resté « Oui » (le cas le plus fort : sans la règle, la machine ET une devinette reviendraient).
        client = self.client("Oui")
        so_machine, so_cartouche, so_nouvelle = self.commandes(3, sans_famille="RO_DOM")
        ms = self.echeancier(client, "2026-09-01", [OSMOSEUR], so_machine)
        self._liste_validee(client, ms.name, JOUR)
        # Ce que fait l'ancien script à la validation : cancel puis delete_doc. Le numéro est repris si c'était le dernier
        # échéancier de la série (revert_series_if_last) : l'échéancier recréé peut porter le MÊME nom.
        ms.reload()
        ms.flags.ignore_links = True
        ms.cancel()
        frappe.delete_doc("Maintenance Schedule", ms.name, ignore_permissions=True, force=True)
        self.assertNotIn(so_machine, U.commandes_couvertes(self.cfg))                     # une suppression ne couvre rien
        self.assertEqual(U.clients_pas_de_systeme("2026-06-26").get(client), getdate(JOUR))
        self.assertNotIn(client, U.clients_pas_de_systeme("2026-12-01"))                  # hors fenêtre : pas lu
        commandes = [{"sales_order": so_machine, "customer": client, "delivery_date": getdate("2026-09-01"), "items": OSMOSEUR},
                     {"sales_order": so_cartouche, "customer": client, "delivery_date": getdate("2026-10-01"), "items": CHARBON},
                     {"sales_order": so_nouvelle, "customer": client, "delivery_date": getdate("2026-11-27"), "items": OSMOSEUR}]
        with mock.patch.object(U, "commandes_a_traiter", lambda cfg, de=None, a=None: [frappe._dict(c) for c in commandes]), \
                mock.patch.object(U, "prolonger_echeanciers_epuises", lambda *a, **k: 0), \
                mock.patch.object(U, "requalifier_clients_silencieux", lambda *a, **k: 0):
            n1 = U.run_maintenance_planning()["summary"]
            n2 = U.run_maintenance_planning()["summary"]
        # La machine et la cartouche d'avant la réponse ne recréent rien ; la machine vendue après, si.
        self.assertEqual((n1["pas_de_systeme"], n1["new_ms_created"], n1["guessed"], n1["errors"]), (2, 1, 0, 0))
        self.assertEqual(frappe.db.sql_list("select msi.sales_order from `tabMaintenance Schedule Item` msi join `tabMaintenance Schedule` ms "
                                            "on ms.name = msi.parent where ms.customer = %s", client), [so_nouvelle])
        self.assertEqual((n2["pas_de_systeme"], n2["new_ms_created"], n2["guessed"], n2["already_covered"]), (2, 0, 0, 1))
        self.assertEqual(U.clients_pas_de_systeme("2026-06-26").get(client), getdate(JOUR))   # même si le nom est repris
        self.assertEqual(self.journal, [])

    def test_une_reponse_dont_l_echeancier_existe_ne_bloque_rien(self):
        # liste_validation ANNULE (l'échéancier existe, docstatus 2) : la couverture suffit, pas de règle en plus.
        client = self.client("Oui")
        so = self.commandes(1)[0]
        ms = self.echeancier(client, "2026-09-01", [OSMOSEUR], so)
        self._liste_validee(client, ms.name, JOUR)
        U.annuler_echeancier(ms.name, "essai : pas de système")
        self.assertNotIn(client, U.clients_pas_de_systeme("2026-06-26"))
        self.assertIn(so, U.commandes_couvertes(self.cfg))

    def test_sur_les_reponses_de_la_copie(self):
        from customization_app.liste_appels import PAS_DE_SYSTEME
        r = frappe.db.sql("""select a.client, date(ifnull(l.date_fin, l.modified)) jour from `tabAppelle Client` a
                             join `tabListe Appelle Entretien` l on l.name = a.parent
                             where a.resume_appel = %s and l.docstatus = 1 and ifnull(a.`échéancier_dentretien`, '') <> ''
                               and ifnull(a.client, '') <> ''
                               and not exists (select 1 from `tabMaintenance Schedule` m where m.name = a.`échéancier_dentretien`)
                             order by jour desc limit 1""", PAS_DE_SYSTEME, as_dict=True)
        if not r:
            self.skipTest("aucune réponse « pas de système » à échéancier supprimé sur cette copie")
        lus = U.clients_pas_de_systeme(add_days(r[0].jour, -1))
        self.assertIn(r[0].client, lus)
        self.assertGreaterEqual(lus[r[0].client], getdate(r[0].jour))

    def test_une_lecture_en_panne_n_arrete_pas_la_nuit(self):
        def panne(*a, **k):
            raise RuntimeError("lecture en panne")

        with mock.patch.object(U, "commandes_a_traiter", lambda cfg, de=None, a=None: []), \
                mock.patch.object(U, "prolonger_echeanciers_epuises", lambda *a, **k: 0), \
                mock.patch.object(U, "requalifier_clients_silencieux", lambda *a, **k: 0), \
                mock.patch.object(U, "clients_pas_de_systeme", panne):
            s = U.run_maintenance_planning()["summary"]
        self.assertEqual((s["errors"], s["pas_de_systeme"]), (1, 0))
        self.assertEqual(len(self.journal), 1)
        self.assertEqual([x for x, _c in self.notes], ["nuit"])


class TestAchatsLots5(_SansTrace):
    def _client_et_echeancier(self, codes, debut="2026-01-10"):
        client = self.client("Oui")
        so_machine = self.commandes(1)[0]
        return client, self.echeancier(client, debut, codes, so_machine)

    def test_la_main_d_oeuvre_solde_l_osmoseur_et_son_porte_filtre(self):
        client, ms = self._client_et_echeancier([OSMOSEUR, PORTE_FILTRE])
        so = self.commandes(1)[0]
        p_osmo, p_pf = self.premiere(ms.name, OSMOSEUR), self.premiere(ms.name, PORTE_FILTRE)
        livraison = str(add_days(getdate(p_osmo.scheduled_date), 20))
        stats = {}
        r = U.update_maintenance_schedule({"sales_order": so, "customer": client, "delivery_date": livraison, "items": "M-E-OD"},
                                          self.cfg, self.fams, {}, stats=stats)
        self.assertEqual(r, "update")
        for code in (OSMOSEUR, PORTE_FILTRE):
            p = frappe.db.get_value("Maintenance Schedule Detail", (p_osmo if code == OSMOSEUR else p_pf).name,
                                    ["actual_date", "custom_sales_order"], as_dict=True)
            self.assertEqual((str(p.actual_date), p.custom_sales_order), (livraison, so), code)       # même date, même commande
        self.assertEqual(stats.get("porte_filtres_avec_machine"), 1)
        self.assertEqual(U.update_maintenance_schedule({"sales_order": so, "customer": client, "delivery_date": livraison,
                                                        "items": "M-E-OD"}, self.cfg, self.fams, {}), "rien")   # rejouée : rien

    def test_sans_main_d_oeuvre_pas_d_avance_lointaine(self):
        client, ms = self._client_et_echeancier([OSMOSEUR, PORTE_FILTRE])
        so_tot, so_proche = self.commandes(2)
        p_osmo = self.premiere(ms.name, OSMOSEUR)
        tot = str(add_days(getdate(p_osmo.scheduled_date), -150))                                        # visite dans 150 j
        stats = {}
        r = U.update_maintenance_schedule({"sales_order": so_tot, "customer": client, "delivery_date": tot, "items": CHARBON},
                                          self.cfg, self.fams, {}, stats=stats)
        self.assertEqual(r, "rien")
        self.assertGreaterEqual(stats.get("avances_evitees", 0), 1)
        self.assertFalse(frappe.db.exists("Maintenance Schedule Detail", {"parent": ms.name, "actual_date": ["is", "set"]}))
        proche = str(add_days(getdate(p_osmo.scheduled_date), -30))                                     # visite dans 30 j
        r = U.update_maintenance_schedule({"sales_order": so_proche, "customer": client, "delivery_date": proche, "items": CHARBON},
                                          self.cfg, self.fams, {})
        self.assertEqual(r, "update")
        self.assertEqual(self.premiere(ms.name, OSMOSEUR).custom_sales_order, so_proche)
        self.assertEqual(self.premiere(ms.name, PORTE_FILTRE).custom_sales_order, so_proche)           # le porte-filtre suit

    def test_un_achat_anterieur_a_la_machine_ne_la_solde_pas(self):
        client, ms = self._client_et_echeancier([OSMOSEUR], debut="2026-09-04")
        so = self.commandes(1)[0]
        stats = {}
        r = U.update_maintenance_schedule({"sales_order": so, "customer": client, "delivery_date": "2026-08-27", "items": "M-E-OD"},
                                          self.cfg, self.fams, {}, stats=stats)
        self.assertEqual(r, "rien")
        self.assertEqual(stats.get("achats_anterieurs"), 1)

    def test_la_main_d_oeuvre_de_l_adoucisseur_n_est_plus_volee_par_l_osmoseur(self):
        client, ms = self._client_et_echeancier([OSMOSEUR, ADOUCISSEUR])
        so = self.commandes(1)[0]
        p = self.premiere(ms.name, ADOUCISSEUR)
        livraison = str(add_days(getdate(p.scheduled_date), 5))
        self.assertEqual(U.update_maintenance_schedule({"sales_order": so, "customer": client, "delivery_date": livraison,
                                                        "items": "M-E-Ad"}, self.cfg, self.fams, {}), "update")
        self.assertEqual(self.premiere(ms.name, ADOUCISSEUR).custom_sales_order, so)
        self.assertIsNone(self.premiere(ms.name, OSMOSEUR).actual_date)                                  # l'osmoseur ne bouge pas


class TestDevinetteApresAnnulation(_SansTrace):
    """Relecture finale du 26/11/2026 : le seul échéancier d'un client est annulé (machine rendue…) ; un vieil achat de
    cartouches resté libre (règle des 60 jours) faisait naître la nuit suivante une machine devinée."""
    MS, SO = "MAT-MSH-2023-00845", "SAL-ORD-2026-03499"

    def test_un_achat_anterieur_ne_devine_plus(self):
        if not frappe.db.exists("Maintenance Schedule", {"name": self.MS, "docstatus": 1}):
            self.skipTest("%s n'est plus validé sur cette copie" % self.MS)
        i_sal = next((c for c in U.commandes_a_traiter(self.cfg) if c["sales_order"] == self.SO), None)
        if not i_sal or self.SO in U.commandes_couvertes(self.cfg):
            self.skipTest("%s n'est plus à lire sur cette copie" % self.SO)
        client = i_sal["customer"]
        if frappe.db.count("Maintenance Schedule", {"customer": client, "docstatus": 1}) != 1:
            self.skipTest("le client a d'autres échéanciers sur cette copie")
        U.annuler_echeancier(self.MS, "machine rendue (essai)")
        annules = U.clients_echeancier_annule("2026-01-01")
        self.assertIn(client, annules)
        avant = frappe.db.count("Maintenance Schedule")
        stats = {}
        cree, r = U.traiter_commande(i_sal, self.cfg, self.fams, {}, stats=stats, annule_le=annules[client])
        self.assertEqual((cree, r, frappe.db.count("Maintenance Schedule")), (False, "skip", avant))
        self.assertEqual(stats.get("achats_avant_annulation"), 1)
        # Contre-épreuve : sans la règle, la devinette naissait.
        self.assertEqual(U.traiter_commande(i_sal, self.cfg, self.fams, {})[1], "guess")

    def test_regle_pure(self):
        self.assertTrue(U.devinette_permise("2026-11-27", "2026-11-26"))      # acheté après : nouvelle machine possible
        self.assertFalse(U.devinette_permise("2026-11-26", "2026-11-26"))
        self.assertFalse(U.devinette_permise("2026-09-03", "2026-11-26"))
        self.assertTrue(U.devinette_permise("2026-09-03", None))


class TestDeuxNuits(_SansTrace):
    def _nuits(self, commandes, n, entre=None):
        out = []
        with mock.patch.object(U, "commandes_a_traiter", lambda cfg, de=None, a=None: [frappe._dict(c) for c in commandes]), \
                mock.patch.object(U, "prolonger_echeanciers_epuises", lambda *a, **k: 0), \
                mock.patch.object(U, "requalifier_clients_silencieux", lambda *a, **k: 0):
            for i in range(n):
                out.append(U.run_maintenance_planning()["summary"])
                if i == 0 and entre:
                    entre()
        return out

    def test_la_deuxieme_nuit_ne_fait_rien_de_plus_et_l_entonnoir_est_note(self):
        client = self.client("Non")
        so_machine, so_entretien, so_cartouche = self.commandes(3)
        commandes = [{"sales_order": so_machine, "customer": client, "delivery_date": getdate("2026-08-01"), "items": OSMOSEUR},
                     {"sales_order": so_entretien, "customer": client, "delivery_date": getdate("2026-07-20"), "items": "M-E-OD"},
                     {"sales_order": so_cartouche, "customer": client, "delivery_date": getdate("2026-11-01"), "items": CHARBON}]
        photos = []
        n1, n2 = self._nuits(commandes, 2, entre=lambda: photos.append(self.photo([client])))
        self.assertEqual((n1["new_ms_created"], n1["new_ms_non_interesses"]), (1, 1))
        self.assertEqual(n1["achats_anterieurs"], 1)                       # l'entretien du 20/07 précède la machine du 01/08
        self.assertEqual(n1["avances_evitees"], 1)                         # la cartouche du 01/11, visite en janvier/février
        self.assertEqual(n2["new_ms_created"] + n2["updated_from_consumables"] + n2["guessed"], 0)
        self.assertEqual(self.photo([client]), photos[0])
        self.assertEqual([s for s, _c in self.notes], ["nuit", "nuit"])
        self.assertIn("avances_evitees", self.notes[0][1])

    def test_trois_nuits_avec_des_annulations_sans_boucle(self):
        # Relecture du 26/11/2026 : la preuve de non-boucle doit contenir des ANNULATIONS — une devinette remplacée par la
        # vraie machine (vrai chemin), un échéancier B2B annulé par le nettoyage, la machine d'un « Non » annulée à la main
        # (comme liste_validation pour « pas de système ») avec sa commande dans la fenêtre.
        oui = self.client("Oui")
        non = self.client("Non", sauf=[oui])
        pro = self.client("Non", b2b=True)
        so_conso, so_machine = self.commandes(2, sans_famille="RO_DOM")
        so_non, so_b2b = self.commandes(2)
        ms_b2b = self.echeancier(pro, "2026-09-01", [OSMOSEUR], so_b2b)
        commandes = [{"sales_order": so_conso, "customer": oui, "delivery_date": getdate("2026-08-10"), "items": CHARBON},
                     {"sales_order": so_non, "customer": non, "delivery_date": getdate("2026-09-10"), "items": OSMOSEUR},
                     {"sales_order": so_machine, "customer": oui, "delivery_date": getdate("2026-10-10"), "items": OSMOSEUR}]
        photos = []

        def a_la_main():
            U.annuler_echeancier(self.ms_de(so_non), "essai : pas de système")
            photos.append(self.photo([oui, non, pro]))

        n1, n2, n3 = self._nuits(commandes, 3, entre=a_la_main)
        self.assertEqual((n1["new_ms_created"], n1["guessed"], n1["devines_annules"], n1["new_ms_non_interesses"]), (2, 1, 1, 1))
        self.assertGreaterEqual(n1["cleanup"]["annules"], 1)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", ms_b2b.name, "docstatus"), 2)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", self.ms_de(so_conso), "docstatus"), 2)   # la devinette
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", self.ms_de(so_machine), "docstatus"), 1)
        self.assertEqual(frappe.db.get_value("Maintenance Schedule", self.ms_de(so_non), "docstatus"), 2)
        for n in (n2, n3):
            self.assertEqual((n["new_ms_created"], n["guessed"], n["updated_from_consumables"], n["devines_annules"],
                              n["cleanup"]["annules"], n["errors"]), (0, 0, 0, 0, 0, 0))
            self.assertEqual(n["already_covered"], 3)
        self.assertEqual(self.photo([oui, non, pro]), photos[0])          # rien créé, annulé, marqué ni commenté de plus
        self.assertLessEqual({so_conso, so_machine, so_non, so_b2b}, U.commandes_couvertes(self.cfg))
        self.assertEqual(self.journal, [])

    def test_une_prolongation_en_panne_n_arrete_ni_les_commandes_ni_le_compte_rendu(self):
        client = self.client("Non")
        so = self.commandes(1)[0]
        commandes = [frappe._dict({"sales_order": so, "customer": client, "delivery_date": getdate("2026-08-01"), "items": OSMOSEUR})]

        def panne(*a, **k):
            raise RuntimeError("requête de prolongation en panne")

        with mock.patch.object(U, "commandes_a_traiter", lambda cfg, de=None, a=None: commandes), \
                mock.patch.object(U, "prolonger_echeanciers_epuises", panne), \
                mock.patch.object(U, "requalifier_clients_silencieux", panne):
            s = U.run_maintenance_planning()["summary"]
        self.assertEqual((s["new_ms_created"], s["prolonges"], s["requalifies"], s["errors"]), (1, 0, 0, 2))
        self.assertEqual(len(self.journal), 2)                                     # les deux pannes, journalisées
        self.assertEqual([x for x, _c in self.notes], ["nuit"])                    # et le compte rendu noté quand même

    def test_un_entonnoir_en_panne_n_arrete_pas_la_nuit(self):
        with mock.patch.object(U, "commandes_a_traiter", lambda cfg, de=None, a=None: []), \
                mock.patch.object(U, "prolonger_echeanciers_epuises", lambda *a, **k: 0), \
                mock.patch.object(U, "requalifier_clients_silencieux", lambda *a, **k: 0), \
                mock.patch.object(U, "verify_data_base", lambda cfg=None: {"corriges": 0, "annules": 0, "supprimes": 0, "erreurs": 0}), \
                mock.patch.object(ER, "noter", side_effect=RuntimeError("panne")):
            r = U.run_maintenance_planning()
        self.assertEqual(r["summary"]["total_sales_orders"], 0)


if __name__ == "__main__":
    unittest.main()
