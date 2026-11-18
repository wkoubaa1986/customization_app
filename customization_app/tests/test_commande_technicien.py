"""La commande simplifiée du technicien en écran simplifié (customization_app.commande_technicien) — demande du
18/11/2026 : client et date déjà posés, ses articles (son stock, ou hors stock « à mentionner »), images, prix,
quantité, et les termes de paiement."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import commande_technicien as K

BASE = os.path.dirname(inspect.getfile(customization_app))
STOCK = {"C-10-PP": 3.0, "T33": 1.0}
SERVICES = {"M-E-OD"}


class TestArticles(unittest.TestCase):
    def test_ses_articles_et_la_main_d_oeuvre(self):
        lignes, erreurs = K.lignes_demandees([{"item_code": "C-10-PP", "qty": 2}, {"item_code": "M-E-OD", "qty": 1}], STOCK, SERVICES)
        self.assertEqual(erreurs, [])
        self.assertEqual([(l["item_code"], l["qty"], l["hors_stock"], l["service"]) for l in lignes],
                         [("C-10-PP", 2.0, False, False), ("M-E-OD", 1.0, False, True)])

    def test_pas_plus_que_son_stock(self):
        lignes, erreurs = K.lignes_demandees([{"item_code": "C-10-PP", "qty": 4}], STOCK, SERVICES)
        self.assertEqual(lignes, [])
        self.assertIn("4 demandé, 3 dans votre stock", erreurs[0])
        self.assertIn("hors de mon stock", erreurs[0])

    def test_un_article_qu_il_n_a_pas_doit_etre_marque_hors_stock(self):
        """« Il peut ajouter un article hors de son stock, mais il faut qu'il le mentionne. »"""
        _, erreurs = K.lignes_demandees([{"item_code": "QB60", "qty": 1}], STOCK, SERVICES)
        self.assertIn("QB60 : 1 demandé, 0 dans votre stock", erreurs[0])
        lignes, erreurs = K.lignes_demandees([{"item_code": "QB60", "qty": 1, "hors_stock": 1}], STOCK, SERVICES)
        self.assertEqual((erreurs, lignes[0]["hors_stock"]), ([], True))

    def test_le_meme_article_de_son_stock_et_hors_stock_fait_deux_lignes(self):
        lignes, erreurs = K.lignes_demandees([{"item_code": "T33", "qty": 1}, {"item_code": "T33", "qty": 2, "hors_stock": 1}], STOCK, SERVICES)
        self.assertEqual(erreurs, [])
        self.assertEqual([(l["qty"], l["hors_stock"]) for l in lignes], [(1.0, False), (2.0, True)])

    def test_les_quantites_d_une_meme_ligne_s_additionnent(self):
        _, erreurs = K.lignes_demandees([{"item_code": "T33", "qty": 1}, {"item_code": "T33", "qty": 1}], STOCK, SERVICES)
        self.assertIn("2 demandé, 1 dans votre stock", erreurs[0])

    def test_la_main_d_oeuvre_n_est_jamais_hors_stock(self):
        lignes, _ = K.lignes_demandees([{"item_code": "M-E-OD", "qty": 1, "hors_stock": 1}], STOCK, SERVICES)
        self.assertFalse(lignes[0]["hors_stock"])

    def test_panier_vide_ou_quantite_nulle(self):
        self.assertEqual(K.lignes_demandees([], STOCK, SERVICES)[1], ["Ajoutez au moins un article."])
        self.assertIn("quantité invalide", K.lignes_demandees([{"item_code": "T33", "qty": 0}], STOCK, SERVICES)[1][0])


class TestReglement(unittest.TestCase):
    def test_especes_et_carte(self):
        """La carte porte la part de la commande : la majoration s'ajoute à la validation."""
        lignes, erreurs = K.echeances_demandees([{"mode": "Espèces", "montant": 72}, {"mode": "Carte de crédit", "montant": 100, "numero": "445566"}], 172)
        self.assertEqual(erreurs, [])
        self.assertEqual(lignes, [{"mode_of_payment": "Espèces", "payment_amount": 72.0},
                                  {"mode_of_payment": "Carte de crédit", "payment_amount": 100.0, K.CHAMP_PIECE: "445566"}])

    def test_la_somme_doit_faire_le_total(self):
        self.assertIn("Les règlements font 171, la commande 172", K.echeances_demandees([{"mode": "Espèces", "montant": 171}], 172)[1][0])

    def test_la_carte_exige_le_numero_de_transaction(self):
        self.assertIn("est obligatoire", K.echeances_demandees([{"mode": "Carte de crédit", "montant": 172}], 172)[1][0])

    def test_le_cheque_a_sept_chiffres_et_sa_banque(self):
        self.assertIn("7 chiffres", K.echeances_demandees([{"mode": "Chèque", "montant": 172, "numero": "123"}], 172)[1][0])
        lignes, erreurs = K.echeances_demandees([{"mode": "Chèque", "montant": 172, "numero": "1234567", "banque": "BIAT"}], 172)
        self.assertEqual((erreurs, lignes[0][K.CHAMP_BANQUE]), ([], "BIAT"))

    def test_un_moyen_inconnu_ou_aucun_reglement(self):
        self.assertIn("non permis", K.echeances_demandees([{"mode": "Avoir client", "montant": 10}], 10)[1][0])
        self.assertEqual(K.echeances_demandees([], 10)[1], ["Indiquez comment le client règle."])

    def test_les_moyens_sont_ceux_du_formulaire(self):
        self.assertEqual(K.MODES, ("Espèces", "Chèque", "Carte de crédit", "Virement", "Traite bancaire LC", "Dette non payée"))


class TestService(unittest.TestCase):
    def test_reserve_au_technicien_sur_ses_interventions(self):
        source = inspect.getsource(K._contexte)
        self.assertIn("ST.employe_courant()", source)
        self.assertIn('doc.get("custom_choix_du_staff") != emp.name', source)
        for fn in (K.preparer, K.articles, K.apercu, K.creer):
            self.assertIn("_contexte(tache)", inspect.getsource(fn))

    def test_le_client_et_la_date_sont_poses_d_office(self):
        source = inspect.getsource(K._commande)
        self.assertIn('"customer": doc.custom_client, "transaction_date": jour, "delivery_date": jour', source)
        self.assertIn("magasin if l[\"hors_stock\"] else (emp.custom_warehouse or magasin)", source)   # hors stock : le magasin
        self.assertIn('so.run_method("set_missing_values")', source)                                 # les prix : ceux d'ERPNext

    def test_l_apercu_ne_laisse_rien(self):
        source = inspect.getsource(K.apercu)
        self.assertIn('frappe.db.savepoint("apercu_commande_technicien")', source)
        self.assertIn('frappe.db.rollback(save_point="apercu_commande_technicien")', source)
        self.assertLess(source.index("so.insert()"), source.index("frappe.db.rollback"))

    def test_la_commande_est_validee_liee_et_le_hors_stock_trace(self):
        source = inspect.getsource(K.creer)
        self.assertIn("so.submit()", source)
        self.assertIn('"commande_client", so.name', source)
        self.assertIn("HORS du stock", source)
        self.assertNotIn("ignore_permissions = True", source)      # sous son identité, comme le formulaire


class TestEcran(unittest.TestCase):
    def _lire(self, *chemin):
        with open(os.path.join(BASE, *chemin), encoding="utf-8") as f:
            return f.read()

    def test_le_bouton_de_la_cloture_ouvre_l_ecran_simple_pour_le_technicien(self):
        js = self._lire("public", "js", "tache_photos_cloture.js")
        self.assertIn("simple.sous_traitant === false && typeof window.commande_technicien === \"function\"", js)
        self.assertIn("window.commande_technicien(tache, {", js)
        self.assertIn("formulaire: () => formulaire_complet(),", js)          # le formulaire complet reste accessible
        self.assertIn('d.set_value("commande", nom);', js)
        self.assertIn('_js("commande_technicien.js")', self._lire("hooks.py"))

    def test_l_ecran_montre_images_prix_quantite_et_reglement(self):
        js = self._lire("public", "js", "commande_technicien.js")
        for attendu in ('API + "preparer"', 'API + "articles"', 'API + "apercu"', 'API + "creer"', "ct-img", "ct-qte",
                        'data-hors="1"', "hors de mon stock", "ct-reglement", "de majoration", "Ouvrir le formulaire complet"):
            self.assertIn(attendu, js)


if __name__ == "__main__":
    unittest.main()
