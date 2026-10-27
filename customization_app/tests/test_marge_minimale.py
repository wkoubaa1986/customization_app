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
        with self.assertRaises(ValueError):
            M.plancher_ht(10, 100)


class TestCommande(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("marge_minimale")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        frappe.db.set_single_value(M.CONFIG, {"actif": 1, "taux": 15, "base": M.BASE_PRIX, "source_cout": M.SOURCE_VALO,
                                              "ignorer_web": 1, "ignorer_prix_zero": 1})
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

    def _commande(self, lignes, **champs):
        import frappe
        today = frappe.utils.nowdate()
        doc = frappe.new_doc("Sales Order")          # new_doc : document « neuf », ERPNext y pose la TVA par défaut
        doc.update({"customer": self.customer, "transaction_date": today,
                              "delivery_date": today, "selling_price_list": "Vente standard",
                              "custom_type_de_transaction": "Autre", "company": frappe.defaults.get_global_default("company"),
                              "items": [{"item_code": self.item, "qty": 1, "delivery_date": today,
                                         "warehouse": self.magasin, **l} for l in lignes], **champs})
        # Comme à l'écran : l'échéancier est posé AVANT l'enregistrement, sur le total d'avant correction
        # (mode de paiement obligatoire sur chaque échéance).
        doc.run_method("set_missing_values")
        if not doc.taxes:                              # comme le formulaire : le modèle de taxes par défaut (TVA incluse)
            doc.taxes_and_charges = frappe.db.get_value("Sales Taxes and Charges Template", {"is_default": 1}, "name")
            doc.append_taxes_from_master()
        doc.calculate_taxes_and_totals()
        doc.set_payment_schedule()
        for e in doc.payment_schedule:
            e.mode_of_payment = "Espèces"
        self.total_avant = doc.grand_total
        self.assertTrue(doc.taxes, "la commande de test doit porter la TVA par défaut (prix TTC)")
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
        self.assertEqual(l1.rate, M.arrondir_sup(M.plancher_ht(self.cout, 15) * self._tva(l1), 3))
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
