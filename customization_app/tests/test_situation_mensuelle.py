"""Situation mensuelle : mêmes chiffres que le rapport « Test », omissions totales et partielles,
détail cohérent avec les totaux, droits. Tout est annulé (savepoint)."""
from __future__ import annotations

import unittest

from customization_app import situation_mensuelle as SM


class TestEquivalenceRapport(unittest.TestCase):
    def test_memes_chiffres_que_le_rapport_test(self):
        """Sans les omissions ajoutées depuis la page, les chiffres sont ceux du rapport « Test » au
        millime. Les omissions des utilisateurs sont mises de côté dans un savepoint, puis rétablies."""
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("equivalence")
        frappe.db.delete(SM.DOCTYPE_EXCLUSION, {"motif": ["not like", "Repris de l%"]})
        try:
            self._comparer()
        finally:
            frappe.db.rollback(save_point="equivalence")

    def _comparer(self):
        import frappe
        rep = frappe.get_doc("Report", "Test")
        cas = [({"mensuel": 1, "mois": m}, ("mois", m, None)) for m in SM.mois_disponibles()] + \
              [({"mensuel": 0, "annee": a}, ("annee", None, a)) for a in ("Total", "2023", "2024", "2025")]
        for f, (mode, mois, annee) in cas:
            attendu = {r["account_name"]: r["total_monthly"] for r in rep.execute_script_report(frappe._dict(f))[1]}
            c = SM.calculer(SM.periode(mode, mois, annee))
            for libelle, valeur in (("Total des ventes délivrées", c["ventes"]), ("Coût de la marchandise délivrée", c["cout"]["net"]),
                                    ("Total des charges", c["charges"]["net"]), ("TVA Achat", c["tva"]["net"]),
                                    ("Bénéfice / Perte", c["benefice"])):
                self.assertAlmostEqual(valeur, attendu[libelle], places=2, msg=f"{mois or annee} — {libelle}")


class TestDetail(unittest.TestCase):
    def test_detail_de_chaque_rubrique_concorde_avec_le_total(self):
        """Toutes les rubriques, plusieurs mois et une année : le détail se charge (le Coût de la
        marchandise plantait sur les Bons de livraison) et la somme des lignes = total compté."""
        import frappe
        frappe.set_user("Administrator")
        periodes = [("mois", m, None) for m in SM.mois_disponibles()[:3]] + [("annee", None, "2025"), ("annee", None, "Total")]
        for mode, mois, annee in periodes:
            for rub in ("Ventes", "Coût de la marchandise", "Charges", "TVA Achat"):
                det = SM.get_detail(rub, mode, mois, annee)
                somme = sum(l["montant"] for c in det["comptes"] for l in c["pieces"]) + sum(c["reste"]["montant"] for c in det["comptes"])
                self.assertAlmostEqual(somme, det["total"], places=2, msg=f"{rub} {mois or annee}")
                self.assertTrue(all(len(c["pieces"]) <= SM.LIGNES_PAR_COMPTE for c in det["comptes"]))
                if rub == "Coût de la marchandise" and det["comptes"]:
                    self.assertTrue(any(l["libelle"] for c in det["comptes"] for l in c["pieces"]), "libellés vides")


class TestMargesEtComparaison(unittest.TestCase):
    def test_ratios(self):
        r = SM.ratios(1000, 380, 340)
        self.assertAlmostEqual(r["marge"], 34.0)
        self.assertAlmostEqual(r["marge_brute"], 62.0)                 # (ventes − coût) / ventes — « c'est le contraire » (01/10 soir)
        self.assertAlmostEqual(r["cout_ventes"], 38.0)                 # coût / ventes, affiché en sous-titre
        self.assertEqual(SM.ratios(0, 10, -10), {"marge": None, "marge_brute": None, "cout_ventes": None})

    def test_comparaison_des_annees(self):
        import frappe
        from frappe.utils import getdate, nowdate
        frappe.set_user("Administrator")
        r = SM.get_comparaison()
        annees = {a["annee"]: a for a in r["annees"]}
        self.assertEqual(list(annees)[0], "2024")                   # 2023 écartée : pas représentative
        self.assertNotIn("2023", annees)
        from frappe.utils import getdate, nowdate
        en_cours = annees[str(getdate(nowdate()).year)]
        self.assertTrue(all(m is None for m in en_cours["mois"][getdate(nowdate()).month:]))   # mois à venir : rien
        self.assertTrue(all(m is not None for m in annees["2024"]["mois"]))                  # année pleine
        auj = getdate(nowdate())
        courante = annees[str(auj.year)]
        self.assertTrue(all(m is None for m in courante["mois"][auj.month:]))   # mois à venir : rien
        self.assertIsNotNone(courante["mois"][auj.month - 1])
        c = SM.calculer(SM.periode("annee", annee="2025"))
        self.assertAlmostEqual(annees["2025"]["total"]["benefice"], c["benefice"], places=2)
        sept = annees["2025"]["mois"][8]
        cs = SM.calculer(SM.periode("mois", "septembre 2025"))
        self.assertAlmostEqual(sept["ventes"], cs["ventes"], places=2)
        self.assertAlmostEqual(sept["marge"], 100 * cs["benefice"] / cs["ventes"], places=6)


class TestMainDOeuvre(unittest.TestCase):
    """Carte « Main d’œuvre » (26/10/2026) : la main d’œuvre des BL validés, TTC, SANS les ventes
    réalisées par le partenaire Economiq (règle de la section « economiq » du bilan)."""

    def test_du_partenaire(self):
        cmd = {"SAL-ORD-1"}
        self.assertTrue(SM._du_partenaire({"against_sales_order": "SAL-ORD-1"}, cmd))
        self.assertFalse(SM._du_partenaire({"against_sales_order": "SAL-ORD-2"}, cmd))
        self.assertFalse(SM._du_partenaire({"against_sales_order": None}, cmd))      # BL sans commande : à nous

    def test_commandes_de(self):
        """La tâche la plus récente décide (lignes déjà triées, la plus récente d'abord) : une
        commande réaffectée deux fois (partenaire → nous → partenaire) est au partenaire, et
        l'inverse à nous. Fonction pure, indépendante de la base."""
        rows = [("X", "Economic"), ("Y", "Nous"), ("X", "Nous"), ("Y", "Economic"), ("X", "Economic"), ("Y", "Nous"), ("Z", None)]
        self.assertEqual(SM._commandes_de(rows, "Economic"), {"X"})
        self.assertEqual(SM._commandes_de([], "Economic"), set())

    def test_sql_rend_la_tache_la_plus_recente_en_premier(self):
        """Trois tâches dont l'employé se répète, insérées directement (savepoint) : la lecture SQL
        de `commandes_du_partenaire` et `_split_shared` du bilan classent pareil. Garde contre un
        `distinct` (MariaDB dédoublonne avant de trier : la ligne gardée n'est plus la plus récente)."""
        import frappe
        from customization_app import bilan_vente
        frappe.set_user("Administrator")
        employe = frappe.db.get_value("Employee", {"user_id": bilan_vente.PARTNER_USER}, "employee_name")
        if not employe:
            self.skipTest("pas d'employé partenaire")
        frappe.db.savepoint("sm_taches")
        try:
            cas = {"SM-TEST-X": [(employe, 1), ("Autre Employé", 2), (employe, 3)],            # → partenaire
                   "SM-TEST-Y": [("Autre Employé", 1), (employe, 2), ("Autre Employé", 3)]}    # → nous
            for so, taches in cas.items():
                frappe.db.sql("""insert into `tabDocShare` (name, creation, modified, share_doctype, share_name, user, `read`)
                                 values (%s, now(), now(), 'Sales Order', %s, %s, 1)""", ("SM-SHARE-" + so, so, bilan_vente.PARTNER_USER))
                for emp, jour in taches:
                    frappe.db.sql("""insert into `tabTache de travail` (name, creation, modified, docstatus, commande_client, custom_employé)
                                     values (%s, %s, %s, 0, %s, %s)""",
                                  ("SM-TACHE-%s-%s" % (so, jour), "2030-01-%02d 10:00:00" % jour, "2030-01-%02d 10:00:00" % jour, so, emp))
            frappe.local.sm_commandes_partenaire = None
            cmd = SM.commandes_du_partenaire()
            self.assertIn("SM-TEST-X", cmd)
            self.assertNotIn("SM-TEST-Y", cmd)
            par_lui, pour_lui = bilan_vente._split_shared([frappe._dict(name="SM-TEST-X"), frappe._dict(name="SM-TEST-Y")])
            self.assertEqual({o.name for o in par_lui}, {"SM-TEST-X"})
            self.assertEqual({o.name for o in pour_lui}, {"SM-TEST-Y"})
        finally:
            frappe.db.rollback(save_point="sm_taches")
            frappe.local.sm_commandes_partenaire = None

    def test_total_detail_et_part_du_partenaire(self):
        import frappe
        frappe.set_user("Administrator")
        groupes = SM.groupes_main_d_oeuvre()
        self.assertIn(SM.GROUPE_MAIN_D_OEUVRE, groupes)
        cmd = SM.commandes_du_partenaire()
        self.assertTrue(cmd)
        for mode, mois, annee in [("mois", m, None) for m in SM.mois_disponibles()[:2]] + [("annee", None, "Total")]:
            p = SM.periode(mode, mois, annee)
            c = SM.calculer(p)["mo"]
            # Témoin indépendant : HT des lignes du groupe + TVA détaillée par article des BL.
            # Le brut doit dépasser le HT (TVA 19 % sur presque tout) : une carte passée en HT
            # — ou un témoin « réaligné » — ferait chuter le chiffre de ~16 %.
            ht, ht_part = frappe.db.sql("""select sum(dni.net_amount),
                                                  sum(if(dni.against_sales_order in %(cmd)s, dni.net_amount, 0))
                                           from `tabDelivery Note Item` dni
                                           join `tabDelivery Note` dn on dn.name = dni.parent
                                           left join `tabItem` i on i.name = dni.item_code
                                           where dn.docstatus = 1 and dn.posting_date between %(d1)s and %(d2)s
                                             and coalesce(i.item_group, dni.item_group) in %(g)s""",
                                        {"cmd": tuple(cmd), "d1": p["debut"], "d2": p["fin"], "g": tuple(groupes)})[0]
            ht, ht_part = float(ht or 0), float(ht_part or 0)
            self.assertGreater(c["brut"], ht * 1.10, msg=str(mois or annee))
            # Jamais plus que 19 % — à l'arrondi au millime de chaque ligne près (octobre 2026 : +0,010 sur ~100 lignes).
            self.assertLessEqual(c["brut"], ht * 1.19 + 0.1, msg=str(mois or annee))
            self.assertAlmostEqual(c["net"], c["brut"] - c["partenaire"], places=3)
            # La part partenaire : entre son HT (anciens BL sans TVA, 2025) et HT × 1,19 (BL HT + TVA).
            self.assertGreaterEqual(c["partenaire"], ht_part - 0.01, msg=str(mois or annee))
            self.assertLessEqual(c["partenaire"], ht_part * 1.19 + 0.01, msg=str(mois or annee))
            det = SM.get_detail(SM.RUBRIQUE_MAIN_D_OEUVRE, mode, mois, annee)
            somme = sum(l["montant"] for cp in det["comptes"] for l in cp["pieces"]) + sum(cp["reste"]["montant"] for cp in det["comptes"])
            self.assertAlmostEqual(somme, det["total"], places=2, msg=str(mois or annee))
            self.assertAlmostEqual(det["total"], c["net"], places=2)                 # le détail = la carte
            self.assertAlmostEqual(det["ecartees"]["total"], c["partenaire"], places=2)
            self.assertEqual(det["ecartees"]["nb"], c["nb_partenaire"])               # même compte de BL
            self.assertFalse(det["peut_omettre"])                                     # informatif : rien à omettre
            self.assertTrue(all(l["commande"] in cmd for l in det["ecartees"]["pieces"]))
            self.assertTrue(all(l["voucher_type"] == "Delivery Note" for cp in det["comptes"] for l in cp["pieces"]))

    def test_ttc_sur_un_bl_du_partenaire_et_un_des_notres(self):
        """Un BL du partenaire (prix HT + TVA) et un BL à prix TTC : la ligne vaut HT × 1,19 dans
        les deux cas, et un BL retrouvé par sa commande dans la recherche."""
        import frappe
        frappe.set_user("Administrator")
        cmd = SM.commandes_du_partenaire()
        bl = frappe.db.sql("""select dn.name, dn.posting_date, dni.against_sales_order, dni.net_amount, dni.amount
                              from `tabDelivery Note Item` dni join `tabDelivery Note` dn on dn.name = dni.parent
                              join `tabItem` i on i.name = dni.item_code
                              where dn.docstatus = 1 and i.item_group = %s and dni.against_sales_order in %s
                                and abs(dni.amount - dni.net_amount) < 0.001 and dni.net_amount > 0
                              order by dn.posting_date desc limit 1""", (SM.GROUPE_MAIN_D_OEUVRE, tuple(cmd)), as_dict=True)
        if not bl:
            self.skipTest("aucun BL partenaire à prix HT")
        b = bl[0]
        lignes = {l.name: l for l in SM._lignes_main_d_oeuvre(b.posting_date, b.posting_date)}
        self.assertAlmostEqual(lignes[b.name].montant, float(b.net_amount) * 1.19, places=2)      # HT + TVA, pas `amount`
        notre = next((l for l in lignes.values() if not SM._du_partenaire(l, cmd)), None)
        if notre:
            ht = frappe.db.get_value("Delivery Note Item", {"parent": notre.name, "item_code": notre.item_code}, "sum(net_amount)")
            self.assertAlmostEqual(notre.montant, float(ht) * 1.19, places=2)
        p = SM.periode("mois", SM.libelle_mois(b.posting_date))
        det = SM._detail_main_d_oeuvre(p, b.against_sales_order.lower())
        self.assertEqual([x["voucher_no"] for x in det["ecartees"]["pieces"]], [b.name])
        self.assertEqual(det["ecartees"]["nb_trouvees"], 1)

    def test_meme_perimetre_que_la_section_economiq_du_bilan(self):
        """Toute commande de la section « economiq » du bilan est « du partenaire » ici, aucune de
        la section Aqua World ne l'est : on ne redéfinit pas « vente Economiq »."""
        import frappe
        from customization_app import bilan_vente
        frappe.set_user("Administrator")
        cmd = SM.commandes_du_partenaire()
        self.assertIs(cmd, SM.commandes_du_partenaire())                              # lue une fois par requête
        vues = 0
        # 2025-07 : SAL-ORD-2025-02074, deux tâches de deux employés, la plus récente au partenaire.
        for mois in ("2026-08", "2026-09", "2025-09", "2025-07"):
            sections = {s["key"]: s for s in bilan_vente.get_data(mois)["sections"]}
            for o in sections["economiq"]["orders"]:
                self.assertIn(o["name"], cmd, mois)
                vues += 1
            for o in sections["aqua_world"]["orders"]:
                self.assertNotIn(o["name"], cmd, mois)
        if not vues:
            self.skipTest("aucune vente Economiq sur les mois témoins")

    def test_serie_et_comparaison_portent_la_main_d_oeuvre(self):
        import frappe
        frappe.set_user("Administrator")
        s = SM.serie(nb=2)
        self.assertTrue(all("mo" in m for m in s))
        c = SM.calculer(SM.periode("mois", s[-1]["mois"]))
        self.assertAlmostEqual(s[-1]["mo"], c["mo"]["net"], places=3)
        self.assertIn("mo", SM.INDICATEURS)
        self.assertAlmostEqual(SM._valeurs(c)["mo"], c["mo"]["net"], places=3)


class TestRecherche(unittest.TestCase):
    def test_recherche_retrouve_une_petite_piece_hors_liste(self):
        import frappe
        frappe.set_user("Administrator")
        det = SM.get_detail("Coût de la marchandise", "annee", None, "Total")
        compte = next((c for c in det["comptes"] if c["reste"]["n"]), None)
        if not compte:
            self.skipTest("aucun compte au-delà de la limite")
        cachees = {l["voucher_no"] for c in det["comptes"] for l in c["pieces"]}
        p = SM.periode("annee", annee="Total")
        petites = [vn for (vt, vn), v in SM._pieces("Coût de la marchandise", p["debut"], p["fin"]).items() if vn not in cachees]
        trouve = SM.get_detail("Coût de la marchandise", "annee", None, "Total", recherche=petites[0])
        self.assertIn(petites[0], [l["voucher_no"] for c in trouve["comptes"] for l in c["pieces"]])
        self.assertAlmostEqual(trouve["total"], det["total"], places=2)                 # le total ne dépend pas du filtre


class TestOmissions(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("situation")
        # le mois complet le plus récent qui a des charges
        self.mois = SM.mois_disponibles()[1]
        self.p = SM.periode("mois", self.mois)
        pieces = SM._pieces("Charges", self.p["debut"], self.p["fin"])
        libres = [(k, v) for k, v in pieces.items() if v > 100 and not SM._exclusions("Charges", {k: v})]
        if not libres:
            self.skipTest("aucune pièce de charge libre ce mois-là")
        (self.vt, self.vn), self.montant = max(libres, key=lambda x: x[1])

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="situation")

    def test_totale_partielle_retablir(self):
        import frappe
        avant = SM.calculer(self.p)
        SM.omettre("Charges", self.vt, self.vn, "Partielle", 100, "test partiel")
        apres = SM.calculer(self.p)
        self.assertAlmostEqual(apres["charges"]["net"], avant["charges"]["net"] - 100, places=3)
        self.assertAlmostEqual(apres["benefice"], avant["benefice"] + 100, places=3)
        with self.assertRaises(frappe.ValidationError):
            SM.omettre("Charges", self.vt, self.vn, "Partielle", self.montant + 1, "trop")
        with self.assertRaises(frappe.ValidationError):
            SM.omettre("Charges", self.vt, self.vn, "Totale", None, "  ")          # motif obligatoire
        SM.omettre("Charges", self.vt, self.vn, "Totale", None, "test total")     # met à jour, pas de doublon
        self.assertEqual(frappe.db.count(SM.DOCTYPE_EXCLUSION, {"voucher_no": self.vn, "rubrique": "Charges"}), 1)
        apres = SM.calculer(self.p)
        self.assertAlmostEqual(apres["charges"]["net"], avant["charges"]["net"] - self.montant, places=3)
        # le détail montre la pièce omise et ses totaux concordent
        det = SM.get_detail("Charges", "mois", self.mois, recherche=self.vn)
        lignes = [l for c in det["comptes"] for l in c["pieces"]]
        self.assertAlmostEqual(det["net"], apres["charges"]["net"], places=3)
        self.assertTrue(any(l["voucher_no"] == self.vn and l["omission"] for l in lignes))
        self.assertIn(self.vn, [o.voucher_no for o in SM.get_omissions("mois", self.mois)["omissions"]])
        nom = frappe.db.get_value(SM.DOCTYPE_EXCLUSION, {"voucher_no": self.vn, "rubrique": "Charges"})
        SM.retablir(nom)
        self.assertAlmostEqual(SM.calculer(self.p)["charges"]["net"], avant["charges"]["net"], places=3)
        with self.assertRaises(frappe.ValidationError):
            SM.omettre("TVA Achat", "Journal Entry", "ACC-JV-INEXISTANTE", "Totale", None, "x")

    def test_omettre_reserve_au_role_banque(self):
        import frappe
        from unittest import mock
        # Un comptable sans le rôle Banque. On simule ses rôles : créer un vrai User réenregistre son
        # Contact, et le Server Script « update_list_tel » plante sur un contact sans client.
        with mock.patch.object(SM.frappe, "get_roles", return_value=["Accounts User", "Employee"]):
            r = SM.get_situation("mois", self.mois)
            self.assertFalse(r["peut_omettre"])
            self.assertIn("ventes", r["calcul"])                                # il lit
            with self.assertRaises(frappe.PermissionError):
                SM.omettre("Charges", self.vt, self.vn, "Totale", None, "interdit")
        with mock.patch.object(SM.frappe, "get_roles", return_value=["Employee"]):
            with self.assertRaises(frappe.PermissionError):
                SM.get_situation("mois", self.mois)                             # hors comptabilité : rien
