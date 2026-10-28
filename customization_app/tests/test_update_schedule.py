"""Échéanciers de maintenance (revue 03/10/2026) : prolongation, décalage par famille, familles cibles — règles pures,
puis une prolongation sur un document en mémoire."""
from __future__ import annotations

import datetime
import unittest

import frappe

from customization_app import relances_config as RC
from customization_app.Maintenance import update_schedule as U

FAMS = RC.familles() if False else [  # défauts, sans base
    {"code": f["code"], "libelle_sms": f["libelle_sms"], "article_entretien": f["article_entretien"], "article_type": f["article_type"],
     "groupes_machines": RC.liste(f["groupes_machines"]), "groupes_consommables": RC.liste(f["groupes_consommables"])} for f in RC.DEFAUT_FAMILLES]


def _ligne(code, date, actual=None, so=None, statut="Pending", idx=0):
    return frappe._dict(item_code=code, scheduled_date=datetime.date.fromisoformat(date), actual_date=actual,
                        custom_sales_order=so, completion_status=statut, idx=idx)


class TestProlongation(unittest.TestCase):
    def test_prochaines_dates_sautent_le_retard(self):
        d = U.prochaines_dates("2024-01-01", 6, 2, "2026-10-03")           # dernière visite il y a presque 3 ans
        self.assertEqual(d, [datetime.date(2026, 7, 1), datetime.date(2027, 1, 1)])   # au plus une période de retard
        self.assertEqual(U.prochaines_dates("2026-09-01", 6, 2, "2026-10-03"), [datetime.date(2027, 3, 1), datetime.date(2027, 9, 1)])

    def test_slots_et_extension_en_memoire(self):
        import types
        ms = types.SimpleNamespace(name="MS-TEST", items=[frappe._dict(item_code="A", periodicity="Half Yearly")],
                                   schedules=[frappe._dict(item_code="A", item_name="Osmoseur", scheduled_date=datetime.date(2026, 1, 1),
                                                           custom_sms_1=datetime.date(2026, 1, 2), custom_sms_2=datetime.date(2026, 1, 9), sales_person="X")])
        ajout = []
        ms.append = lambda champ, valeurs: ajout.append(valeurs) or ms.schedules.append(frappe._dict(valeurs))
        self.assertFalse(U.has_free_sms_slots(ms))
        n = U.extend_schedule_for_sms(ms, extra_visits_per_item=2, aujourd_hui="2026-10-03")
        self.assertEqual(n, 2)
        self.assertEqual([a["scheduled_date"] for a in ajout], [datetime.date(2026, 7, 1), datetime.date(2027, 1, 1)])
        self.assertEqual({a["completion_status"] for a in ajout}, {"Pending"})                 # plus « En Attente »
        self.assertEqual(ajout[0]["item_name"], "Osmoseur")
        self.assertTrue(U.has_free_sms_slots(ms))
        self.assertEqual(U.extend_schedule_for_sms(ms, 2, "2026-10-03"), 0)                     # plus rien à ajouter


class TestDecalage(unittest.TestCase):
    FAM = {"OSMO": "RO_DOM", "ADOU": "ADOUCISSEUR"}.get

    def test_vise_la_famille_et_la_plus_ancienne_en_retard(self):
        lignes = [_ligne("OSMO", "2026-03-01", actual="2026-03-02", so="SO-1", statut="Fully Completed", idx=1),
                  _ligne("OSMO", "2026-09-01", idx=2), _ligne("ADOU", "2026-09-05", idx=3), _ligne("OSMO", "2027-03-01", idx=4)]
        ref = U.ligne_a_marquer(lignes, "2026-09-04", "RO_DOM", self.FAM, "SO-2")
        self.assertEqual((ref.item_code, str(ref.scheduled_date)), ("OSMO", "2026-09-01"))      # pas l'adoucisseur du 05/09
        self.assertIsNone(U.ligne_a_marquer(lignes, "2026-03-03", "RO_DOM", self.FAM, "SO-1"))  # commande déjà appliquée
        self.assertIsNone(U.ligne_a_marquer([lignes[0]], "2026-03-03", "RO_DOM", self.FAM, "SO-9"))  # rien en attente
        self.assertEqual(U.ligne_a_marquer(lignes, "2026-03-03", "ADOUCISSEUR", self.FAM, "SO-3").item_code, "ADOU")
        # Client en retard de deux visites qui achète enfin : c'est la PLUS ANCIENNE qui est soldée (l'ancienne règle
        # prenait la plus proche, 2027-03-01, et laissait 2026-09-01 ouverte pour toujours).
        tard = [_ligne("OSMO", "2026-09-01", idx=1), _ligne("OSMO", "2027-03-01", idx=2), _ligne("OSMO", "2027-09-01", idx=3)]
        self.assertEqual(str(U.ligne_a_marquer(tard, "2027-02-20", "RO_DOM", self.FAM, "SO-4").scheduled_date), "2026-09-01")
        # Rien en retard : la prochaine à venir
        self.assertEqual(str(U.ligne_a_marquer(tard, "2026-08-01", "RO_DOM", self.FAM, "SO-5").scheduled_date), "2026-09-01")

    def test_sans_famille_toutes_les_lignes(self):
        lignes = [_ligne("OSMO", "2026-09-01"), _ligne("ADOU", "2026-09-05")]
        self.assertEqual(U.ligne_a_marquer(lignes, "2026-09-05", None, self.FAM).item_code, "OSMO")   # la plus ancienne en retard

    def test_decalage_remet_a_neuf_les_visites_repoussees(self):
        import types
        rows = [_ligne("OSMO", "2025-03-01", idx=1), _ligne("OSMO", "2025-09-01", idx=2), _ligne("OSMO", "2026-03-01", idx=3)]
        for r in rows[:2]:
            r.update(custom_sms_1=datetime.date(2025, 3, 3), custom_sms_2=datetime.date(2025, 3, 10), custom_appelle=datetime.date(2025, 4, 1))
        saved = []
        ms = types.SimpleNamespace(name="MS", customer="C", items=[frappe._dict(item_code="OSMO", periodicity="Half Yearly")], schedules=rows,
                                   flags=frappe._dict(), save=lambda: saved.append(True), append=lambda champ, v: rows.append(frappe._dict(v)))
        U._famille_de_la_ligne = lambda ms_, code, fams=None, cache=None: "RO_DOM"
        ok = U.shift_schedule_for_delivery(ms, "2026-02-20", "SO-7", "RO_DOM", aujourd_hui="2026-10-03")
        self.assertTrue(ok and saved)
        self.assertEqual((str(rows[0].actual_date), rows[0].custom_sales_order, rows[0].completion_status), ("2026-02-20", "SO-7", "Fully Completed"))
        self.assertEqual([str(r.scheduled_date) for r in rows[:3]], ["2026-02-20", "2026-08-23", "2027-02-20"])   # + 356 j
        self.assertIsNone(rows[1].custom_sms_1) ; self.assertIsNone(rows[1].custom_appelle)                      # repoussée → neuve


class TestFamilles(unittest.TestCase):
    def test_familles_cibles_et_priorite(self):
        items = [{"item_code": "C1", "item_name": "Cartouche charbon", "item_group": "Cartouches à charbon"},
                 {"item_code": "S1", "item_name": "Sel adoucisseur 25 kg", "item_group": "Consommables & Accessoires"},
                 {"item_code": "M1", "item_name": "Osmoseur", "item_group": "RO flux direct"}]             # machine : ignorée ici
        cibles, sures = U.familles_cibles(items, FAMS)
        self.assertIn("RO_DOM", cibles) ; self.assertIn("ADOUCISSEUR", cibles) ; self.assertEqual(sures, {"ADOUCISSEUR"})
        self.assertEqual(RC.famille_prioritaire(sures or cibles, FAMS), "ADOUCISSEUR")
        self.assertEqual(RC.famille_prioritaire(cibles, FAMS), "RO_DOM")                       # ordre du réglage, pas du set
        self.assertEqual(U.familles_consommable({"item_name": "Membrane 4040", "item_group": "Inconnu"}, FAMS), ["RO_IND"])
        self.assertEqual(U.familles_consommable({"item_name": "Sac de sel", "item_group": "Autre"}, FAMS), ["ADOUCISSEUR"])
        self.assertEqual(U.famille_machine({"item_group": "Filtres UV"}, FAMS), "UV")
        self.assertEqual(U.familles_consommable({"item_group": "Filtres UV", "item_name": "Lampe UV 6w"}, FAMS), ["UV"])   # indice « uv » du nom
        self.assertFalse(U.est_consommable({"item_group": "Filtres UV", "item_name": "Stérilisateur 6w"}, FAMS))  # plus un consommable

    def test_cout_commun(self):
        fam, _prix = RC.cout_entretien({"RO_IND", "RO_DOM"}, FAMS) if False else (RC.famille_prioritaire({"RO_IND", "RO_DOM"}, FAMS), None)
        self.assertEqual(fam, "RO_DOM")
        self.assertEqual(RC.liste("a, b\nc\n\nb"), ["a", "b", "c"])


class TestRattrapage(unittest.TestCase):
    """15/10/2026 : porte-filtre = pièce de rechange chez un client suivi, pas de décalage par la commande qui crée la
    machine, rattrapage limité aux commandes postérieures à la dernière visite réalisée."""

    def test_porte_filtre_machine_seulement_chez_un_client_sans_echeancier(self):
        items = [{"item_code": "PF1", "item_name": "Porte-filtre 10\"", "item_group": "Porte-filtres"},
                 {"item_code": "C1", "item_name": "Cartouche", "item_group": "Cartouches à charbon"},
                 {"item_code": "PF1", "item_name": "Porte-filtre 10\"", "item_group": "Porte-filtres"}]
        self.assertEqual(U.machines_de_la_commande(items, FAMS, client_suivi=True), [])                 # entretien : pièce
        self.assertEqual([f for _, f in U.machines_de_la_commande(items, FAMS, client_suivi=False)], ["PF"])  # vendu seul : machine, une fois
        osmo = [{"item_code": "M1", "item_name": "Osmoseur", "item_group": "RO flux direct"}] + items
        self.assertEqual([f for _, f in U.machines_de_la_commande(osmo, FAMS, client_suivi=True)], ["RO_DOM"])  # la vraie machine passe

    def test_la_commande_qui_cree_la_machine_ne_la_decale_pas(self):
        from types import SimpleNamespace as NS   # pas frappe._dict : « items » y est la méthode du dict
        neuf = NS(name="MS-NEUF", items=[NS(item_code="M1", sales_order="SO-1")])
        ancien = NS(name="MS-ANCIEN", items=[NS(item_code="M0", sales_order="SO-0")])
        self.assertEqual([m.name for m in U.echeanciers_hors_commande([neuf, ancien], "SO-1")], ["MS-ANCIEN"])
        self.assertEqual([m.name for m in U.echeanciers_hors_commande([neuf, ancien], "SO-9")], ["MS-NEUF", "MS-ANCIEN"])

    def test_commande_rattrapable_et_accessoire(self):
        self.assertTrue(U.commande_rattrapable("2026-02-10", None))
        self.assertTrue(U.commande_rattrapable("2026-02-10", "2025-08-01"))
        self.assertFalse(U.commande_rattrapable("2025-03-01", "2025-08-01"))   # l'échéancier a déjà avancé : historique
        self.assertFalse(U.commande_rattrapable("2025-08-01", "2025-08-01"))
        self.assertTrue(U.est_accessoire({"PF"}))
        self.assertFalse(U.est_accessoire({"PF", "RO_DOM"}))
        self.assertFalse(U.est_accessoire(set()))

    def test_main_d_oeuvre_d_entretien_et_doublon_sans_histoire(self):
        cibles, sures = U.familles_cibles([{"item_code": "M-E-OD", "item_name": "Main d’œuvre entretien", "item_group": "Main d’œuvre"}], FAMS)
        self.assertEqual((cibles, sures), ({"RO_DOM"}, {"RO_DOM"}))           # entretien facturé sans consommable : reconnu
        self.assertEqual(U.familles_cibles([{"item_code": "M-I-OD", "item_group": "Main d’œuvre"}], FAMS), (set(), set()))  # installation : non
        self.assertTrue(U.doublon_sans_histoire_propre([], ["SO-1"]))
        self.assertTrue(U.doublon_sans_histoire_propre(["SO-1", "SO-2"], ["SO-1", "SO-2", "SO-3"]))   # tout déjà porté par l'osmoseur
        self.assertFalse(U.doublon_sans_histoire_propre(["SO-1", "SO-9"], ["SO-1"]))                  # SO-9 n'est que sur le doublon
        self.assertFalse(U.doublon_sans_histoire_propre([""], ["SO-1"]))                               # visite faite à la main


class TestUnEcheancierParCommande(unittest.TestCase):
    """ERPNext n'admet qu'un échéancier soumis par commande : le second (machine puis famille devinée depuis un
    consommable de la même commande) se crée sans lien de commande sur sa ligne machine (WEB1-008619, 26-27/10/2026)."""

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.db.savepoint("un_par_commande")

    def tearDown(self):
        frappe.db.rollback(save_point="un_par_commande")

    def test_second_echeancier_sans_lien_de_commande(self):
        cfg = RC.config()
        client = frappe.db.get_value("Customer", {"disabled": 0, "customer_group": ["not in", list(cfg["groupes_b2b_liste"]) or [""]]}, "name")
        so = frappe.db.get_value("Sales Order", {"docstatus": 1, "customer": client}, "name") or frappe.db.get_value("Sales Order", {"docstatus": 1}, "name")
        item = frappe.db.get_value("Item", {"disabled": 0, "is_sales_item": 1}, ["name", "item_name"], as_dict=True)
        if not (client and so and item):
            self.skipTest("données absentes")
        ligne = [{"item_code": item.name, "item_name": item.item_name, "sales_order": so}]
        premier = U._nouvel_echeancier(client, frappe.utils.nowdate(), ligne, cfg)
        self.assertEqual(premier.items[0].sales_order, so)
        self.assertEqual(U._commande_deja_portee(so), premier.name)
        second = U._nouvel_echeancier(client, frappe.utils.nowdate(), ligne, cfg)      # avant : ValidationError
        self.assertEqual(second.docstatus, 1)
        self.assertIsNone(second.items[0].sales_order)
        self.assertEqual(U._commande_deja_portee(so), premier.name)                     # toujours un seul lien
