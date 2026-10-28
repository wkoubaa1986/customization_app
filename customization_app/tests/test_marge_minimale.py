"""Marge minimale de vente (customization_app.marge_minimale) : une commande client ne vend pas sous la marge
plancher ; la ligne relevée garde la trace. Tout dans un savepoint, rien n'est gardé."""
from __future__ import annotations

import unittest

from customization_app import marge_minimale as M


class TestCalculs(unittest.TestCase):
    def test_plancher_et_marge(self):
        self.assertAlmostEqual(M.plancher_ht(85, 15), 100.0, places=6)                  # 15 % du prix de vente
        self.assertAlmostEqual(M.marge(100, 85), 15.0, places=6)
        self.assertAlmostEqual(M.plancher_ht(100, 15, M.BASE_COUT), 115.0, places=6)    # 15 % sur le coût
        self.assertAlmostEqual(M.marge(115, 100, M.BASE_COUT), 15.0, places=6)
        self.assertEqual(M.plancher_ht(0, 15), 0.0)
        self.assertIsNone(M.marge(10, 0))
        self.assertEqual(M.arrondir_sup(12.3451), 12.346)
        self.assertEqual(M.arrondir_sup(12.345), 12.345)
        self.assertEqual(M.arrondir_sup(25.99, 0.5), 26.0)                                # au demi-dinar supérieur
        self.assertEqual(M.arrondir_sup(26.0, 0.5), 26.0)
        self.assertEqual(M.arrondir_sup(26.001, 0.5), 26.5)
        self.assertEqual(M.arrondir_sup(0.756, 0.5), 1.0)
        taxes = [{"account_head": "TVA 19% - A&S", "rate": 0, "charge_type": "On Net Total", "included_in_print_rate": 1},
                 {"account_head": "TVA 7% - A&S", "rate": 0, "charge_type": "On Net Total", "included_in_print_rate": 1},
                 {"account_head": "Timbre Fiscal - A&S", "rate": 0, "charge_type": "Actual", "included_in_print_rate": 0}]
        self.assertAlmostEqual(M.facteur_tva('{"TVA 19% - A&S": 19}', taxes), 1.19)
        self.assertAlmostEqual(M.facteur_tva('{"TVA 7% - A&S": 7}', taxes), 1.07)
        self.assertAlmostEqual(M.facteur_tva(None, taxes), 1.0)
        self.assertAlmostEqual(M.facteur_tva("{}", []), 1.0)
        r = {"taux": 15, "base": M.BASE_PRIX, "arrondi": 0.5}
        self.assertEqual(M.plancher_ttc(18.565, 1.19, r), 26.0)                           # 18,565 / 0,85 × 1,19 = 25,99 → 26
        self.assertEqual(M.plancher_ttc(18.565, 1.19, dict(r, arrondi=0)), 25.991)
        self.assertEqual(M.plancher_exonere(3.141, 19, r), 3.782)                         # exonéré : 3,695 × 1,19 → 4,5 → HT 3,782
        self.assertEqual(M.plancher_exonere(18.565, 19, r), round(26.0 / 1.19, 3))
        self.assertEqual(M.plancher_exonere(18.565, None, r), 22.0)                      # TVA inconnue : arrondi sur le HT
        with self.assertRaises(ValueError):
            M.plancher_ht(10, 100)


class TestCommande(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("marge_minimale")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        frappe.db.set_single_value(M.CONFIG, {"actif": 1, "taux": 15, "base": M.BASE_PRIX, "source_cout": M.SOURCE_VALO,
                                              "ignorer_web": 1, "ignorer_prix_zero": 1, "arrondi": 0})   # au millime : marge exacte
        frappe.db.delete("Config Marge Exclusion", {"parent": M.CONFIG})
        frappe.clear_cache(doctype=M.CONFIG)
        self.magasin = frappe.db.get_single_value("Config Stock Entrepot", "entrepot_magasin")
        # Un article suivi en stock, au coût connu, qu'aucune règle de prix ne touche (article ni groupe).
        self.item = frappe.db.sql_list("""select b.item_code from tabBin b join tabItem i on i.name = b.item_code
            where b.warehouse = %s and b.actual_qty > 5 and b.valuation_rate > 0 and i.disabled = 0
              and i.is_stock_item = 1 and i.has_variants = 0 and i.is_sales_item = 1
              and i.name not in (select item_code from `tabPricing Rule Item Code` where item_code is not null)
              and i.item_group not in (select item_group from `tabPricing Rule Item Group` where item_group is not null)
              and exists (select 1 from `tabItem Price` p where p.item_code = i.name and p.price_list = 'Vente standard')
            order by b.item_code limit 1""", self.magasin)
        if not self.item:
            self.skipTest("aucun article de test")
        self.item = self.item[0]
        self.cout = frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": self.magasin}, "valuation_rate")
        self.customer = frappe.db.get_value("Customer", {"disabled": 0, "customer_group": ["!=", ""]}, "name")

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="marge_minimale")
        frappe.clear_cache(doctype=M.CONFIG)

    def _commande(self, lignes, doctype="Sales Order", taxes_and_charges=None, **champs):
        import frappe
        today = frappe.utils.nowdate()
        doc = frappe.new_doc(doctype)                 # new_doc : document « neuf », ERPNext y pose la TVA par défaut
        if doctype == "Quotation":
            champs.update({"quotation_to": "Customer", "party_name": self.customer, "valid_till": today})
        else:
            champs.update({"customer": self.customer, "delivery_date": today, "custom_type_de_transaction": "Autre"})
        doc.update({"transaction_date": today, "selling_price_list": "Vente standard",
                    "company": frappe.defaults.get_global_default("company"),
                    "items": [{"item_code": self.item, "qty": 1, "delivery_date": today,
                               "warehouse": self.magasin, **l} for l in lignes], **champs})
        if taxes_and_charges:                          # modèle imposé (ex. exonération), posé avant les prix
            doc.taxes_and_charges = taxes_and_charges
            doc.append_taxes_from_master()
        # Comme à l'écran : l'échéancier est posé AVANT l'enregistrement, sur le total d'avant correction
        # (mode de paiement obligatoire sur chaque échéance).
        doc.run_method("set_missing_values")
        if not doc.taxes:                              # comme le formulaire : le modèle de taxes par défaut (TVA incluse)
            doc.taxes_and_charges = frappe.db.get_value("Sales Taxes and Charges Template", {"is_default": 1}, "name")
            doc.append_taxes_from_master()
        doc.calculate_taxes_and_totals()
        doc.set_payment_schedule()                     # commande ET devis : mode de paiement obligatoire par échéance
        for e in doc.payment_schedule:
            e.mode_of_payment = "Espèces"
        self.total_avant = doc.grand_total
        self.assertTrue(doc.taxes, "le document de test doit porter un modèle de taxes")
        doc.insert()
        return doc

    def _marge_plancher(self, m, net_rate):
        """Jamais sous 15 % ; au plus un millime (TTC, arrondi vers le haut) au-dessus."""
        self.assertGreaterEqual(m, 15.0 - 1e-6)
        self.assertLessEqual(m - 15.0, 0.0012 / net_rate * 100 + 0.01)

    def _tva(self, l):
        return l.rate / l.net_rate if l.net_rate else 1.0

    def test_ligne_sous_la_marge_relevee_et_tracee(self):
        import frappe
        bas = round(self.cout * 1.19 * 1.05, 3)         # ~5 % de marge TTC : trop peu
        haut = round(self.cout * 1.19 * 2, 3)           # ~50 % : rien à faire
        doc = self._commande([{"rate": bas}, {"rate": haut}, {"rate": 0, "discount_percentage": 100}])   # 0 DT = remise 100 % (sinon ERPNext remet le prix de liste)
        l1, l2, l3 = doc.items
        self.assertEqual(l1.get(M.CHAMP_FLAG), 1)
        self.assertAlmostEqual(l1.get(M.CHAMP_AVANT), bas, places=3)
        self.assertGreater(l1.rate, bas)
        self._marge_plancher(M.marge(l1.net_rate, self.cout), l1.net_rate)              # la marge plancher (HT), au millime près
        self.assertEqual(l1.rate, M.arrondir_sup(M.plancher_ht(self.cout, 15) * self._tva(l1), 0, 3))
        self.assertAlmostEqual(l1.amount, l1.rate * l1.qty, places=3)                     # totaux refaits
        self.assertEqual(doc.total, round(sum(l.amount for l in doc.items), 3))
        self.assertGreater(doc.grand_total, self.total_avant)
        self.assertAlmostEqual(sum(e.payment_amount for e in doc.payment_schedule), doc.grand_total, places=3)   # échéancier réaligné
        self.assertFalse(l2.get(M.CHAMP_FLAG)); self.assertEqual(l2.rate, haut)         # au-dessus : prix de base gardé
        self.assertFalse(l3.get(M.CHAMP_FLAG)); self.assertEqual(l3.rate, 0)            # 0 DT : jamais relevé
        # Ré-enregistrement : la trace reste, le prix d'origine n'est pas écrasé par le prix relevé.
        releve = l1.rate
        doc.save()
        self.assertEqual(doc.items[0].get(M.CHAMP_FLAG), 1)
        self.assertAlmostEqual(doc.items[0].get(M.CHAMP_AVANT), bas, places=3)
        self.assertEqual(doc.items[0].rate, releve)
        # L'utilisateur remonte lui-même le prix au-dessus du plancher : plus une ligne « relevée ».
        doc.items[0].rate = haut
        doc.save()
        self.assertFalse(doc.items[0].get(M.CHAMP_FLAG)); self.assertEqual(doc.items[0].rate, haut)
        self.assertEqual(M.etat_commande(doc.name)["lignes"], [])
        frappe.db.get_value("Sales Order", doc.name, "name")  # existe bien

    def test_base_cout_et_dernier_prix_d_achat(self):
        import frappe
        frappe.db.set_single_value(M.CONFIG, {"base": M.BASE_COUT, "source_cout": M.SOURCE_ACHAT})
        frappe.clear_cache(doctype=M.CONFIG)
        achat = self.cout * 2
        frappe.db.set_value("Item", self.item, "last_purchase_rate", achat)
        doc = self._commande([{"rate": round(self.cout * 1.19 * 1.5, 3)}])      # 50 % sur la valo, mais 25 % SOUS l'achat
        l = doc.items[0]
        self.assertEqual(l.get(M.CHAMP_FLAG), 1)
        self._marge_plancher(M.marge(l.net_rate, achat, M.BASE_COUT), l.net_rate)

    def test_jamais_relevees(self):
        import frappe
        bas = round(self.cout * 1.19 * 1.05, 3)
        doc = self._commande([{"rate": bas}], woocommerce_id="999999999")           # commande du site : prix payé en ligne
        self.assertFalse(doc.items[0].get(M.CHAMP_FLAG)); self.assertEqual(doc.items[0].rate, bas)
        groupe = frappe.db.get_value("Item", self.item, "item_group")
        c = frappe.get_doc(M.CONFIG); c.append("groupes_exclus", {"item_group": groupe}); c.save()
        doc = self._commande([{"rate": bas}])                                        # groupe exclu
        self.assertFalse(doc.items[0].get(M.CHAMP_FLAG)); self.assertEqual(doc.items[0].rate, bas)
        frappe.db.delete("Config Marge Exclusion", {"parent": M.CONFIG})
        frappe.db.set_single_value(M.CONFIG, "actif", 0); frappe.clear_cache(doctype=M.CONFIG)
        doc = self._commande([{"rate": bas}])                                        # réglage désactivé
        self.assertFalse(doc.items[0].get(M.CHAMP_FLAG)); self.assertEqual(doc.items[0].rate, bas)

    def test_prix_releve_au_dessus_du_prix_de_liste(self):
        """Si même le prix de liste est sous la marge, le prix relevé le dépasse : porté par la marge « Amount »
        d'ERPNext, pas par une remise négative."""
        import frappe
        frappe.db.set_value("Item Price", {"item_code": self.item, "price_list": "Vente standard"},
                            "price_list_rate", round(self.cout * 1.19 * 1.02, 3))
        doc = self._commande([{}])                                                   # prix de liste seul
        l = doc.items[0]
        self.assertEqual(l.get(M.CHAMP_FLAG), 1)
        self.assertGreater(l.rate, l.price_list_rate)
        self.assertEqual(l.margin_type, "Amount"); self.assertEqual(l.discount_percentage, 0)
        self._marge_plancher(M.marge(l.net_rate, self.cout), l.net_rate)

    def test_arrondi_au_demi_dinar_et_temps_reel(self):
        import frappe
        frappe.db.set_single_value(M.CONFIG, "arrondi", 0.5); frappe.clear_cache(doctype=M.CONFIG)
        bas = round(self.cout * 1.19 * 1.05, 3)
        doc = self._commande([{"rate": bas}])
        l = doc.items[0]
        self.assertEqual(l.get(M.CHAMP_FLAG), 1)
        self.assertAlmostEqual(l.rate * 2, round(l.rate * 2), places=6)                   # multiple de 0,5
        self.assertGreaterEqual(M.marge(l.net_rate, self.cout), 15.0 - 1e-6)
        self.assertLess(l.rate - M.plancher_ht(self.cout, 15) * self._tva(l), 0.5 + 1e-6)
        # Même réponse pour le formulaire (temps réel), à partir de la ligne telle qu'elle est à l'écran.
        rep = M.plancher_ligne({"item_code": self.item, "warehouse": self.magasin, "qty": 1, "conversion_factor": 1,
                                "item_tax_rate": l.item_tax_rate, "rate": bas},
                               [t.as_dict() for t in doc.taxes])
        self.assertEqual(rep["plancher"], l.rate)
        self.assertIsNone(M.plancher_ligne({"item_code": self.item, "rate": 0}, [])["plancher"])          # 0 DT
        self.assertIsNone(M.plancher_ligne({"item_code": self.item, "rate": bas}, [], woocommerce_id="1")["plancher"])

    def test_document_exonere_prix_de_liste_en_ht(self):
        """Modèle sans TVA incluse : le prix de liste TTC devient HT (÷ 1,19), prix de ligne compris ; un prix tapé à
        la main est gardé ; le plancher de marge est alors HT ; même réponse pour le formulaire."""
        import frappe
        exo = frappe.db.get_value("Sales Taxes and Charges Template", {"name": ["like", "%xon%"]}, "name")
        if not exo:
            self.skipTest("pas de modèle d'exonération")
        ttc = frappe.db.get_value("Item Price", {"item_code": self.item, "price_list": "Vente standard"}, "price_list_rate")
        tva = M.taux_tva_article(self.item)
        self.assertIn(tva, (7.0, 19.0))
        ht = round(ttc / (1 + tva / 100), 3)
        frappe.db.set_single_value(M.CONFIG, "arrondi", 0); frappe.clear_cache(doctype=M.CONFIG)
        doc = self._commande([{}, {"rate": round(ttc * 0.9, 3)}], taxes_and_charges=exo)
        self.assertTrue(M.est_exonere(doc.taxes))
        l1, l2 = doc.items
        self.assertEqual(l1.price_list_rate, ht)
        plancher = M.plancher_exonere(self.cout, tva, {"taux": 15, "base": M.BASE_PRIX, "arrondi": 0})
        self.assertAlmostEqual(plancher, M.plancher_ht(self.cout, 15), places=2)
        self.assertEqual(l1.rate, max(ht, plancher))                                         # HT, relevé si la liste est sous la marge
        self.assertEqual(l1.net_rate, l1.rate)                                               # aucune TVA dans le prix
        self.assertEqual(l2.price_list_rate, ht)
        self.assertEqual(l2.rate, max(round(ttc * 0.9, 3), plancher))                        # prix tapé gardé
        self.assertEqual(M.prix_liste_ht(self.item, "Vente standard", l1.uom, l1.item_tax_rate), ht)
        rep = M.plancher_ligne({"item_code": self.item, "price_list": "Vente standard", "uom": l1.uom, "warehouse": self.magasin,
                                "qty": 1, "conversion_factor": 1, "item_tax_rate": l1.item_tax_rate, "rate": ht},
                               [t.as_dict() for t in doc.taxes])
        self.assertTrue(rep["exonere"]); self.assertEqual(rep["prix_liste_ht"], ht)
        self.assertEqual(rep["plancher"], plancher)
        # Arrondi au demi-dinar : sur le TTC fictif (HT × (1 + TVA)), puis retour en HT.
        frappe.db.set_single_value(M.CONFIG, "arrondi", 0.5); frappe.clear_cache(doctype=M.CONFIG)
        rep = M.plancher_ligne({"item_code": self.item, "price_list": "Vente standard", "uom": l1.uom, "warehouse": self.magasin,
                                "qty": 1, "conversion_factor": 1, "item_tax_rate": l1.item_tax_rate, "rate": ht},
                               [t.as_dict() for t in doc.taxes])
        f = 1 + tva / 100
        self.assertEqual(rep["plancher"], round(M.arrondir_sup(M.plancher_ht(self.cout, 15) * f, 0.5) / f, 3))
        doc = self._commande([{"rate": 0.01}], taxes_and_charges=exo)
        self.assertEqual(doc.items[0].rate, rep["plancher"])
        self.assertAlmostEqual((doc.items[0].rate * f) * 2, round(doc.items[0].rate * f * 2), places=2)   # TTC fictif multiple de 0,5
        # Document standard : pas de conversion.
        doc = self._commande([{}])
        self.assertEqual(doc.items[0].price_list_rate, ttc)
        rep = M.plancher_ligne({"item_code": self.item, "price_list": "Vente standard", "rate": ttc}, [t.as_dict() for t in doc.taxes])
        self.assertFalse(rep["exonere"]); self.assertIsNone(rep["prix_liste_ht"])

    def test_devis_memes_regles(self):
        import frappe
        frappe.db.set_single_value(M.CONFIG, "arrondi", 0.5); frappe.clear_cache(doctype=M.CONFIG)
        bas = round(self.cout * 1.19 * 1.05, 3)
        doc = self._commande([{"rate": bas}], doctype="Quotation")
        l = doc.items[0]
        self.assertEqual(l.get(M.CHAMP_FLAG), 1)
        self.assertAlmostEqual(l.get(M.CHAMP_AVANT), bas, places=3)
        self.assertGreaterEqual(M.marge(l.net_rate, self.cout), 15.0 - 1e-6)
        self.assertEqual(M.etat_commande(doc.name, "Quotation")["lignes"][0]["item_code"], self.item)

    def test_bascule_standard_vers_exonere_et_retour(self):
        """Une ligne relevée en standard, puis le document passe en exonération : le prix relevé n'est pas un prix
        manuel, il est recalculé sur la base HT ; retour en standard : le prix de liste TTC revient."""
        import frappe
        exo = frappe.db.get_value("Sales Taxes and Charges Template", {"name": ["like", "%xon%"]}, "name")
        if not exo:
            self.skipTest("pas de modèle d'exonération")
        frappe.db.set_single_value(M.CONFIG, "arrondi", 0.5); frappe.clear_cache(doctype=M.CONFIG)
        ttc = frappe.db.get_value("Item Price", {"item_code": self.item, "price_list": "Vente standard"}, "price_list_rate")
        tva = M.taux_tva_article(self.item); f = 1 + tva / 100
        doc = self._commande([{"rate": round(self.cout * f * 1.05, 3)}])               # relevée en standard
        l = doc.items[0]
        self.assertEqual(l.get(M.CHAMP_FLAG), 1); releve_ttc = l.rate
        # Bascule en exonération, comme le formulaire : modèle + lignes de taxes rechargées.
        doc.taxes_and_charges = exo; doc.set("taxes", []); doc.append_taxes_from_master(); doc.save()
        l = doc.items[0]
        self.assertEqual(l.price_list_rate, round(ttc / f, 3))
        attendu = M.plancher_exonere(self.cout, tva, {"taux": 15, "base": M.BASE_PRIX, "arrondi": 0.5})
        self.assertEqual(l.rate, max(l.price_list_rate, attendu))                           # recalculé en HT, pas gardé
        self.assertNotEqual(l.rate, releve_ttc)
        self.assertEqual(l.get(M.CHAMP_FLAG), 1 if attendu > l.price_list_rate else 0)
        # Retour en standard : le prix de liste TTC revient, la marge se rejoue sur le TTC.
        doc.taxes_and_charges = frappe.db.get_value("Sales Taxes and Charges Template", {"is_default": 1}, "name")
        doc.set("taxes", []); doc.append_taxes_from_master(); doc.save()
        l = doc.items[0]
        self.assertEqual(l.price_list_rate, ttc)
        self.assertEqual(l.rate, max(ttc, M.plancher_ttc(self.cout, f, {"taux": 15, "base": M.BASE_PRIX, "arrondi": 0.5})))
