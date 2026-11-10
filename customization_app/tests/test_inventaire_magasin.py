"""Inventaire du Magasin (bouton de la page « Stock par entrepôt ») : feuille par emplacement, enregistrement
partiel, clôture = rapprochement ou mouvement de stock négatif. Vrais articles dans un savepoint : tout est annulé."""
from __future__ import annotations

import json
import unittest

from customization_app import inventaire_magasin as I
from customization_app import stock_entrepots as S
from customization_app import zones_magasin as Z
from customization_app.tests.test_stock_entrepots import TRAJETS_DEFAUT, entrepot_essai, fin_de_test, regler_trajets

NEJIB = "aquaworld.servicing@gmail.com"
EMPLOYE_SANS_ROLE = "morchediakram0@gmail.com"     # Akram : Stock User, pas le rôle Inventaire Magasin


class TestCalculPur(unittest.TestCase):
    def test_repartition_premiere_zone_et_total_depot(self):
        import copy
        c = {"total_compte": -4, "zones": [
            {"zone": "F2", "libelle": "F2", "qte": 3},
            {"zone": "Osmoseurs", "libelle": "Osmoseurs", "qte": -7}]}
        original = copy.deepcopy(c)
        zones = [{"zone": z["zone"], "libelle": z["libelle"]} for z in c["zones"]]
        for total, premiere in ((-5, 2), (-1, 6), (0, 7), (-9, -2)):
            with self.subTest(stock=total):
                r = S.repartir_solde_zones(c, total, zones)
                self.assertEqual([z["qte"] for z in r["zones"]], [premiere, -7])
                self.assertEqual(S.flt(sum(z["qte"] for z in r["zones"]), 6), total)
                self.assertEqual(r["zone_mouvements"], "F2")
        self.assertEqual(c, original)                  # le comptage reste une photographie
        # L'ordre de l'article prime : pas de choix alphabétique ni de préférence pour une quantité positive.
        r = S.repartir_solde_zones(c, -5, list(reversed(zones)))
        self.assertEqual([z["qte"] for z in r["zones"]], [-8, 3])
        self.assertEqual(r["zone_mouvements"], "Osmoseurs")
        # Une zone retirée disparaît de la répartition, son solde revient à la première zone actuelle.
        r = S.repartir_solde_zones(c, 13, [{"zone": "Nouvelle", "libelle": "Nouvelle"}])
        self.assertEqual(r["zones"][0]["qte"], 13)
        self.assertIsNone(r["zones"][0]["qte_comptee"])
        # Cases inconnues : zéro conventionnel hors première zone, explicitement distinct d'un comptage.
        r = S.repartir_solde_zones(c, 0.123456, zones + [{"zone": "Inconnue", "libelle": "Inconnue"}])
        self.assertEqual([z["qte"] for z in r["zones"]], [7.123456, -7, 0])
        self.assertIsNone(r["zones"][2]["qte_comptee"])
        self.assertEqual(S.flt(sum(z["qte"] for z in r["zones"]), 6), 0.123456)

    def test_cases_valides_et_somme(self):
        cases = I.cases_valides({"Magasin - A1": "3", "Hall - C2": 2.5, "Magasin - Z9": 1, I.SANS_ZONE: "", "": 4},
                                {"Magasin - A1", "Hall - C2"})
        self.assertEqual(cases, {"Magasin - A1": 3.0, "Hall - C2": 2.5})      # zone inconnue et vide ignorées
        self.assertEqual(I.sommer_cases(cases), 5.5)
        self.assertEqual(I.cases_valides({"Magasin - A1": -2}), {"Magasin - A1": -2})
        self.assertEqual(I.sommer_cases(I.cases_valides({"F2": 3, "Osmoseurs": -7})), -4)
        self.assertEqual(I.cases_valides({I.SANS_ZONE: 0}, set()), {I.SANS_ZONE: 0.0})   # compté 0 = compté

    def test_perimetres(self):
        self.assertEqual(I.libelle_perimetre([]), "Tout le Magasin")
        self.assertEqual(I.libelle_perimetre(["Magasin - A1", I.SANS_ZONE], {"Magasin - A1": "A1"}), "A1, Non rangé")
        self.assertTrue(I.perimetres_se_recoupent([], ["Magasin - A1"]))              # un complet exclut tout
        self.assertTrue(I.perimetres_se_recoupent(["Magasin - A1"], []))
        self.assertTrue(I.perimetres_se_recoupent(["Magasin - A1", "Magasin - A2"], ["Magasin - A2"]))
        self.assertFalse(I.perimetres_se_recoupent(["Magasin - A1"], ["Magasin - B1", I.SANS_ZONE]))

    def test_quantite_rapprochee(self):
        self.assertEqual(I.quantite_rapprochee(12, 10, 12), 10)       # rien n'a bougé : le compté
        self.assertEqual(I.quantite_rapprochee(12, 10, 9), 7)         # 3 vendus depuis : l'écart −2 reste
        self.assertEqual(I.quantite_rapprochee(5, 0, 2), -3)
        self.assertEqual(I.quantite_rapprochee(3, -4, 2), -5)        # une sortie depuis le comptage


class TestInventaire(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("inventaire_magasin")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        self.magasin = S.magasin()
        regler_trajets(TRAJETS_DEFAUT + [("Magasin", "Autre entrepôt", 0), ("Autre entrepôt", "Magasin", 0)])
        self.essai = entrepot_essai("ESSAI INV")
        self.items = frappe.db.sql_list("""select b.item_code from tabBin b join tabItem i on i.name = b.item_code
                                           where b.warehouse = %s and b.actual_qty > 10 and b.valuation_rate > 0
                                             and i.disabled = 0 and i.is_stock_item = 1 and i.has_variants = 0
                                           order by b.item_code limit 3""", self.magasin)
        if len(self.items) < 3:
            self.skipTest("pas assez d'articles en stock au Magasin")
        for n in frappe.get_all(I.INV, filters={"statut": I.EN_COURS}, pluck="name"):   # un seul ouvert à la fois
            frappe.db.set_value(I.INV, n, "statut", I.ANNULE)
        self.espace = I.espace()
        self.zone = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace}, "name")
        if not self.zone:
            self.skipTest("aucune zone dans l'espace de l'inventaire")

    def tearDown(self):
        fin_de_test("inventaire_magasin")

    def _stock(self, item):
        import frappe
        return S.flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": self.magasin}, "actual_qty"), 6)

    def _ligne(self, d, item):
        return next(l for l in d["lignes"] if l["item_code"] == item)

    def test_manquants_negatifs_et_osmoseurs_separes_par_employe(self):
        """3 disponibles, -7 manquants = -4, même à la clôture. Les appareils restent à 3."""
        import frappe
        porte_filtre, osmoseur, autre = self.items
        stock_autre = self._stock(autre)
        autre_zone = frappe.db.get_value(Z.DOCTYPE_ZONE,
            {"espace": self.espace, "est_espace": 0, "name": ["!=", self.zone]}, "name")
        Z.definir_zones(porte_filtre, [self.zone, autre_zone])
        Z.definir_zones(osmoseur, [autre_zone])
        frappe.set_user("hedi.ibidhi@gmail.com")
        self.assertIn(I.ROLE, frappe.get_roles())
        with self.subTest(utilisateur=frappe.session.user):
            name = I.ouvrir_inventaire([self.zone, autre_zone])
            I.enregistrer_inventaire(name, {
                porte_filtre: {"cases": {self.zone: 3, autre_zone: -7}},
                osmoseur: {"cases": {autre_zone: 3}}})
            d = I.detail_inventaire(name)
            self.assertEqual(self._ligne(d, porte_filtre)["cases"], {self.zone: 3, autre_zone: -7})
            self.assertEqual(self._ligne(d, porte_filtre)["qte_comptee"], -4)
            b = I.bilan_cloture(name)
            self.assertEqual(b["nb_ajustements_negatifs"], 1)
            r = I.cloturer_inventaire(name)
            self.assertEqual(self._stock(porte_filtre), -4)
            self.assertEqual(self._stock(osmoseur), 3)
            self.assertEqual(self._stock(autre), stock_autre)
            self.assertEqual(len(r["ecritures"]), 2)
            v = frappe.get_doc(I.INV, name)
            self.assertTrue(v.sortie_stock)
            self.assertEqual(v.termine_par, "hedi.ibidhi@gmail.com")
            self.assertEqual(frappe.db.get_value("Stock Entry", v.sortie_stock, "docstatus"), 1)
            sortie = frappe.get_doc("Stock Entry", v.sortie_stock)
            self.assertEqual(sortie.purpose, "Material Issue")
            self.assertEqual(sortie.items[0].item_code, porte_filtre)
            self.assertEqual(sortie.items[0].expense_account,
                             frappe.db.get_value("Company", S._societe(), "stock_adjustment_account"))
            self.assertEqual(self._ligne(I.detail_inventaire(name), porte_filtre)["qte_rapprochee"], -4)
            with self.assertRaises(frappe.ValidationError):
                I.cloturer_inventaire(name)                        # aucune écriture en double
            # Résorber une partie du manque laisse le stock négatif et passe une entrée.
            suivant = I.ouvrir_inventaire([self.zone])
            I.enregistrer_inventaire(suivant, {porte_filtre: {"cases": {self.zone: 3, autre_zone: -5}}})
            r = I.cloturer_inventaire(suivant)
            self.assertEqual(self._stock(porte_filtre), -2)
            self.assertEqual(len(r["ecritures"]), 1)
            entree = frappe.get_doc("Stock Entry", r["ecritures"][0]["name"])
            self.assertEqual((entree.purpose, entree.items[0].qty), ("Material Receipt", 2))
            # Le manque est inchangé : aucune écriture ; puis le retour à zéro reste possible.
            suivant = I.ouvrir_inventaire([self.zone])
            I.enregistrer_inventaire(suivant, {porte_filtre: {"cases": {self.zone: 3, autre_zone: -5}}})
            self.assertEqual(I.cloturer_inventaire(suivant)["ecritures"], [])
            suivant = I.ouvrir_inventaire([self.zone])
            I.enregistrer_inventaire(suivant, {porte_filtre: {"cases": {self.zone: 3, autre_zone: -3}}})
            self.assertTrue(I.cloturer_inventaire(suivant)["rapprochement"])
            self.assertEqual(self._stock(porte_filtre), 0)

    def test_ajouter_article_dans_perimetre_sans_perdre_comptages(self):
        import frappe
        from unittest.mock import patch
        existant, nouveau, modele = self.items
        Z.definir_zones(existant, [self.zone])
        for code in (nouveau, modele):
            Z.definir_zones(code, [])
        frappe.db.set_value("Item", modele, "has_variants", 1)
        frappe.clear_document_cache("Item", modele)
        self.addCleanup(frappe.clear_document_cache, "Item", modele)
        name = I.ouvrir_inventaire([self.zone])
        I.enregistrer_inventaire(name, {existant: {"cases": {self.zone: 3}}})
        avant = self._ligne(I.detail_inventaire(name), existant)
        frappe.set_user("hedi.ibidhi@gmail.com")
        with patch.object(frappe, "get_roles", return_value=[I.ROLE]):
            for code in (nouveau, modele):
                resultats = I.rechercher_articles("Item", code, "name", 0, 20, {"inventaire": name})
                self.assertIn(code, [r[0] for r in resultats])
                I.ajouter_article(name, code, self.zone)
            d = I.detail_inventaire(name)
            for champ in ("cases", "qte_systeme", "qte_comptee", "compte_par", "compte_le", "compte"):
                self.assertEqual(self._ligne(d, existant)[champ], avant[champ], champ)
            self.assertEqual(self._ligne(d, nouveau)["compte"], 0)
            self.assertEqual(self._ligne(d, nouveau)["zones_ajoutees"], [self.zone])
            self.assertIn(self.zone, Z.zones_de(nouveau))
            I.enregistrer_inventaire(name, {nouveau: {"cases": {self.zone: -7}}})
            I.ajouter_article(name, nouveau, self.zone)
            apres = I.detail_inventaire(name)
            self.assertEqual(len(d["lignes"]), len(apres["lignes"]))
            self.assertEqual(self._ligne(apres, nouveau)["qte_comptee"], -7)
            with self.assertRaises(frappe.ValidationError):
                I.ajouter_article(name, nouveau, self.espace)       # hors périmètre
            I.annuler_inventaire(name)
            with self.assertRaises(frappe.ValidationError):
                I.ajouter_article(name, nouveau, self.zone)

    def test_ajout_et_recherche_refusent_non_stockes_et_sans_role(self):
        import frappe
        code = self.items[0]
        name = I.ouvrir_inventaire([self.zone])
        for champ in ("is_stock_item", "disabled"):
            frappe.db.set_value("Item", code, champ, 0 if champ == "is_stock_item" else 1)
            with self.assertRaises(frappe.ValidationError):
                I.ajouter_article(name, code, self.zone)
            self.assertNotIn(code, [r[0] for r in I.rechercher_articles("Item", code, "name", 0, 20, {"inventaire": name})])
            frappe.db.set_value("Item", code, champ, 1 if champ == "is_stock_item" else 0)
        frappe.set_user(EMPLOYE_SANS_ROLE)
        with self.assertRaises(frappe.PermissionError):
            I.ajouter_article(name, code, self.zone)
        with self.assertRaises(frappe.PermissionError):
            I.rechercher_articles("Item", code, "name", 0, 20, {"inventaire": name})

    def test_saisie_invalide_et_negatif_non_autorise(self):
        import frappe
        code = self.items[0]
        name = I.ouvrir_inventaire()
        for q in ("NaN", "inf", "abc"):
            with self.assertRaises(frappe.ValidationError):
                I.enregistrer_inventaire(name, {code: {"cases": {I.SANS_ZONE: q}}})
        I.enregistrer_inventaire(name, {code: {"cases": {I.SANS_ZONE: -4}}})
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)
        frappe.db.set_value("Item", code, "allow_negative_stock", 0)
        frappe.clear_document_cache("Stock Settings", "Stock Settings")
        frappe.clear_document_cache("Item", code)
        self.addCleanup(frappe.clear_document_cache, "Item", code)
        with self.assertRaises(frappe.ValidationError):
            I.cloturer_inventaire(name)
        self.assertEqual(frappe.db.get_value(I.INV, name, "statut"), I.EN_COURS)

    def test_manquant_et_sortie_apres_comptage(self):
        import frappe
        code = self.items[0]
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, {code: {"cases": {I.SANS_ZONE: -4}}})
        # Une sortie réelle entre le comptage et la clôture reste prise en compte.
        S.ecriture_transfert(self.magasin, self.essai, [(code, 1)], "test inventaire négatif")
        b = I.bilan_cloture(name)
        cible = next(l for l in b["a_rapprocher"] if l["item_code"] == code)
        self.assertEqual(cible["qte_rapprochee"], -5)
        self.assertEqual(b["bouges"][0]["item_code"], code)
        I.cloturer_inventaire(name)
        self.assertEqual(self._stock(code), -5)

    def test_manquant_refuse_pour_article_serialise(self):
        import frappe
        code = self.items[0]
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, {code: {"cases": {I.SANS_ZONE: -4}}})
        for champ in ("has_serial_no", "has_batch_no"):
            frappe.db.set_value("Item", code, champ, 1)
            with self.assertRaisesRegex(frappe.ValidationError, "numéro de série ou lot"):
                I.bilan_cloture(name)
            frappe.db.set_value("Item", code, champ, 0)
        self.assertEqual(frappe.db.get_value(I.INV, name, "statut"), I.EN_COURS)

    def test_solde_mouvements_affectes_premiere_zone_et_comptage_conserve(self):
        import frappe
        code = self.items[0]
        autre_zone = frappe.db.get_value(Z.DOCTYPE_ZONE,
            {"espace": self.espace, "est_espace": 0, "name": ["!=", self.zone]}, "name")
        Z.definir_zones(code, [self.zone, autre_zone])
        name = I.ouvrir_inventaire([self.zone])
        I.enregistrer_inventaire(name, {code: {"cases": {self.zone: 3, autre_zone: -7}}})
        I.cloturer_inventaire(name)

        def solde():
            return next(a for a in S.get_solde(self.magasin, recherche=code)["articles"] if a["item_code"] == code)

        a = solde()
        c = a["entrepots"][0]["comptage_zones"]
        self.assertEqual(a["qte"], -4)
        self.assertEqual(c["inventaire"], name)
        self.assertEqual({z["zone"]: z["qte"] for z in c["zones"]}, {self.zone: 3, autre_zone: -7})
        self.assertTrue(c["compte_le"])
        self.assertEqual(c["variation_stock"], 0)
        self.assertEqual(c["zone_mouvements"], self.zone)
        cases_origine = self._ligne(I.detail_inventaire(name), code)["cases"]
        # Le comptage suivant n'est pas encore clôturé : il ne remplace pas la photographie validée.
        ouvert = I.ouvrir_inventaire([self.zone])
        I.enregistrer_inventaire(ouvert, {code: {"cases": {self.zone: 9}}})
        self.assertEqual(solde()["entrepots"][0]["comptage_zones"], c)
        I.annuler_inventaire(ouvert)
        self.assertEqual(solde()["entrepots"][0]["comptage_zones"], c)
        # Une sortie de 1 va sur la première zone, jamais sur les manquants de la seconde.
        S.ecriture_transfert(self.magasin, self.essai, [(code, 1)], "test détail par zone")
        a = solde()
        self.assertEqual(a["qte"], -5)
        apres = a["entrepots"][0]["comptage_zones"]
        self.assertEqual({z["zone"]: z["qte"] for z in apres["zones"]}, {self.zone: 2, autre_zone: -7})
        self.assertEqual(apres["variation_stock"], -1)
        self.assertEqual(sum(z["qte"] for z in apres["zones"]), a["qte"])
        self.assertEqual(self._ligne(I.detail_inventaire(name), code)["cases"], cases_origine)
        # Le même article à l'autre dépôt n'hérite jamais du comptage du Magasin.
        ailleurs = S.get_solde(self.essai, recherche=code)["articles"][0]
        self.assertNotIn("comptage_zones", ailleurs["entrepots"][0])
        tous = next(a for a in S.get_solde(recherche=code)["articles"] if a["item_code"] == code)
        depots = {e["entrepot"]: e for e in tous["entrepots"]}
        self.assertEqual(depots[self.magasin]["comptage_zones"], apres)
        self.assertNotIn("comptage_zones", depots[self.essai])
        # Les entrées suivent la même règle et ne modifient pas le compte clôturé.
        S.ecriture_transfert(self.essai, self.magasin, [(code, 2)], "test entrée première zone")
        a = solde()
        self.assertEqual(a["qte"], -3)
        self.assertEqual([z["qte"] for z in a["entrepots"][0]["comptage_zones"]["zones"]], [4, -7])
        self.assertEqual(self._ligne(I.detail_inventaire(name), code)["cases"], cases_origine)
        # L'ordre actuel de l'article fait foi, même si la zone du Hall arrive avant celles du Magasin.
        hall = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": ["!=", self.espace]}, "name")
        Z.definir_zones(code, ([hall] if hall else []) + [autre_zone, self.zone])
        a = solde()
        repartition = a["entrepots"][0]["comptage_zones"]
        self.assertEqual(repartition["zone_mouvements"], autre_zone)
        self.assertEqual([z["qte"] for z in repartition["zones"]], [-6, 3])
        self.assertEqual(sum(z["qte"] for z in repartition["zones"]), a["qte"])
        self.assertNotIn(hall, [z["zone"] for z in repartition["zones"]])

    def test_comptages_zones_date_par_article_depot_et_cases_inconnues(self):
        import frappe
        from frappe.utils import add_to_date, now_datetime
        code, jamais_compte = self.items[:2]
        # Isoler les photographies des articles de test ; le savepoint restaure tout l'historique.
        frappe.db.delete("Inventaire Magasin Ligne", {"item_code": ["in", [code, jamais_compte]]})
        instant = now_datetime()

        def photographie(depot, age, cases, statut=I.TERMINE, compte=1, cloture=0):
            v = frappe.new_doc(I.INV)
            v.update({"entrepot": depot, "date": frappe.utils.nowdate(), "statut": statut,
                      "termine_le": add_to_date(instant, seconds=cloture)})
            v.append("lignes", {"item_code": code, "item_name": code, "compte": compte,
                                "compte_le": add_to_date(instant, seconds=age),
                                "comptes": json.dumps(cases), "zones": Z.SEPARATEUR.join([self.zone, self.espace])})
            v.insert(ignore_permissions=True)
            return v.name

        photographie(self.magasin, -60, {self.zone: 99}, cloture=60)  # clôturé après, mais compté avant
        dernier = photographie(self.magasin, -30, {self.zone: 0})
        photographie(self.magasin, 0, {self.zone: 100}, statut=I.EN_COURS)
        photographie(self.magasin, 0, {self.zone: 100}, statut=I.ANNULE)
        photographie(self.magasin, 0, {self.zone: 100}, compte=0)
        autre = photographie(self.essai, -30, {I.SANS_ZONE: 7})
        d = I.derniers_comptages_par_zone([code, jamais_compte], [self.magasin, self.essai])
        self.assertEqual(d[(code, self.magasin)]["inventaire"], dernier)
        self.assertEqual({z["zone"]: z["qte"] for z in d[(code, self.magasin)]["zones"]},
                         {self.zone: 0, self.espace: None})  # une case vide ne devient pas zéro
        self.assertEqual(d[(code, self.essai)]["inventaire"], autre)
        self.assertEqual(d[(code, self.essai)]["total_compte"], 7)
        self.assertNotIn((jamais_compte, self.magasin), d)
        self.assertNotIn((jamais_compte, self.essai), d)

    def test_filtre_jamais_comptes_par_depot_et_cumul_avec_negatifs(self):
        import frappe
        compte, en_cours, non_compte = self.items
        frappe.db.delete("Inventaire Magasin Ligne", {"item_code": ["in", self.items]})

        def inventaire(statut, code, coche=1):
            v = frappe.new_doc(I.INV)
            v.update({"entrepot": self.magasin, "date": frappe.utils.nowdate(), "statut": statut})
            v.append("lignes", {"item_code": code, "item_name": code, "compte": coche,
                                "comptes": json.dumps({I.SANS_ZONE: 0}), "qte_comptee": 0,
                                "compte_le": frappe.utils.now_datetime()})
            v.insert(ignore_permissions=True)

        inventaire(I.TERMINE, compte)                   # un comptage à zéro est bien un comptage
        inventaire(I.EN_COURS, en_cours)
        inventaire(I.ANNULE, en_cours)                  # un inventaire annulé ne valide pas le comptage
        inventaire(I.TERMINE, non_compte, coche=0)      # présent sur la feuille, mais jamais compté
        visibles = {a["item_code"] for a in S.get_solde(self.magasin, jamais_comptes="1")["articles"]}
        self.assertEqual(visibles & set(self.items), {en_cours, non_compte})
        sans_filtre = {a["item_code"] for a in S.get_solde(self.magasin)["articles"]}
        self.assertTrue(set(self.items) <= sans_filtre)

        # Compté au Magasin, jamais dans le second dépôt : ne pas exclure globalement l'article.
        S.ecriture_transfert(self.magasin, self.essai, [(compte, 1)], "test filtre jamais comptés")
        r = S.get_solde(jamais_comptes=1, recherche=compte)
        article = next(a for a in r["articles"] if a["item_code"] == compte)
        self.assertEqual([e["entrepot"] for e in article["entrepots"]], [self.essai])
        self.assertEqual(article["qte"], 1)
        valeur = frappe.db.get_value("Bin", {"item_code": compte, "warehouse": self.essai}, "stock_value")
        self.assertEqual(article["valeur"], S.flt(valeur, 3))
        self.assertEqual(r["valeur"], S.flt(sum(a["valeur"] for a in r["articles"]), 3))

        # La combinaison avec Négatifs et la recherche ne doit garder que leur intersection.
        S.ecriture_transfert(self.magasin, self.essai, [(en_cours, self._stock(en_cours) + 1)], "test négatif jamais compté")
        r = S.get_solde(self.magasin, jamais_comptes=1, negatifs=1, recherche=en_cours)
        self.assertIn(en_cours, {a["item_code"] for a in r["articles"]})
        self.assertTrue(all(a["qte"] < 0 for a in r["articles"]))
        self.assertEqual(r["negatifs"], len(r["articles"]))
        self.assertEqual(r["positifs"], 0)
        self.assertTrue(all(not e.get("comptage_zones") for a in r["articles"] for e in a["entrepots"]))
        # Une fois un comptage clôturé, l'article quitte le filtre.
        inventaire(I.TERMINE, en_cours)
        visibles = {a["item_code"] for a in S.get_solde(self.magasin, jamais_comptes=1, negatifs=1)["articles"]}
        self.assertNotIn(en_cours, visibles)

    def test_modeles_stockes_et_reprise_sans_perdre_le_comptage(self):
        """Les modèles stockés sont comptables, y compris à zéro et lors de la reprise d'une feuille.
        Un ensemble non stocké reste exclu ; l'ajout d'une ligne ne modifie pas un comptage déjà saisi."""
        import frappe
        modele, modele_zero, ensemble = self.items
        for code in self.items:
            self.addCleanup(frappe.clear_document_cache, "Item", code)
        for code in (modele, modele_zero):
            frappe.db.set_value("Item", code, "has_variants", 1)
            frappe.clear_document_cache("Item", code)
        frappe.db.set_value("Bin", {"item_code": modele_zero, "warehouse": self.magasin}, "actual_qty", 0)
        Z.definir_zones(modele, [self.zone])
        Z.definir_zones(modele_zero, [])
        Z.definir_zones(ensemble, [self.zone])
        frappe.db.set_value("Item", ensemble, "is_stock_item", 0)  # ancienne affectation devenue non stockée

        name = I.ouvrir_inventaire([self.zone])
        lignes = {l.item_code for l in frappe.get_doc(I.INV, name).lignes}
        self.assertIn(modele, lignes)
        self.assertNotIn(modele_zero, lignes)
        self.assertNotIn(ensemble, lignes)
        I.enregistrer_inventaire(name, {modele: {"cases": {self.zone: self._stock(modele) + 1}}})
        avant = self._ligne(I.detail_inventaire(name), modele)

        Z.ajouter_a_zone(self.zone, [modele_zero])
        self.assertEqual(I.ouvrir_inventaire([self.zone]), name)
        detail = I.detail_inventaire(name)
        self.assertEqual(self._ligne(detail, modele), avant)
        nouvelle = self._ligne(detail, modele_zero)
        self.assertEqual(nouvelle["qte_systeme"], 0)
        self.assertEqual(nouvelle["compte"], 0)
        self.assertIsNone(nouvelle["qte_comptee"])
        self.assertNotIn(ensemble, {l["item_code"] for l in detail["lignes"]})
        resultat = I.cloturer_inventaire(name)
        self.assertEqual(resultat["nb_rapproches"], 1)
        self.assertEqual(self._stock(modele), avant["qte_systeme"] + 1)
        self.assertEqual(self._stock(modele_zero), 0)

    def test_acces(self):
        import frappe
        frappe.set_user(EMPLOYE_SANS_ROLE)
        self.assertNotIn(I.ROLE, frappe.get_roles())
        with self.assertRaises(frappe.PermissionError):
            I.inventaires_etat()
        self.assertFalse(S.get_context()["inventaire"])
        frappe.set_user(NEJIB)
        if I.ROLE in frappe.get_roles():                  # rôle posé par le patch attribuer_role_inventaire_magasin
            self.assertTrue(S.get_context()["inventaire"])
            self.assertIn("en_cours", I.inventaires_etat())

    def test_feuille_par_emplacement_puis_cloture(self):
        import frappe
        i1, i2, i3 = self.items
        s1, s2, s3 = self._stock(i1), self._stock(i2), self._stock(i3)
        name = I.ouvrir_inventaire()
        self.assertEqual(I.ouvrir_inventaire(), name)                    # un seul inventaire ouvert à la fois
        d = I.detail_inventaire(name)
        self.assertEqual(d["fiche"]["statut"], I.EN_COURS)
        l1 = self._ligne(d, i1)
        self.assertEqual(l1["qte_systeme"], s1)
        self.assertIsNone(l1["qte_comptee"])
        self.assertEqual(l1["zones"], [z for z in Z.zones_de(i1) if z == self.espace or z.startswith(self.espace + " - ")])
        self.assertTrue(all(z["name"] == self.espace or z["espace"] == self.espace for z in d["zones"]))   # jamais le Hall
        self.assertIn("image", l1)
        self.assertTrue(d["zones"])
        # i1 : compté en deux emplacements (dont un nouveau), somme = stock − 2 ; i2 : compté juste ; i3 : pas compté.
        nouvelle = self.zone if self.zone not in Z.zones_de(i1) else frappe.db.get_value(
            Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace, "name": ["not in", Z.zones_de(i1)]}, "name")
        hall = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": ["!=", self.espace]}, "name")
        # « ＋ » : l'emplacement est AUSSITÔT sur la fiche article (demande du 27/10) ; une zone du Hall est refusée.
        with self.assertRaises(frappe.ValidationError):
            I.ajouter_emplacement(name, i1, hall)
        r = I.ajouter_emplacement(name, i1, nouvelle)
        self.assertIn(nouvelle, Z.zones_de(i1))
        self.assertEqual(r["zones_ajoutees"], [nouvelle])
        self.assertIn(nouvelle, self._ligne(I.detail_inventaire(name), i1)["zones"])
        r = I.enregistrer_inventaire(name, json.dumps({
            i1: {"cases": {I.SANS_ZONE: s1 - 3, nouvelle: 1, hall: 7}},      # une case du Hall est ignorée
            i2: {"cases": {I.SANS_ZONE: s2}, "commentaire": "ras"}}))
        self.assertEqual((r["nb_comptes"], r["nb_ecarts"]), (2, 1))
        d = I.detail_inventaire(name)
        l1, l2, l3 = self._ligne(d, i1), self._ligne(d, i2), self._ligne(d, i3)
        self.assertEqual((l1["qte_comptee"], l1["ecart"]), (s1 - 2, -2))
        self.assertEqual(l1["cases"], {I.SANS_ZONE: s1 - 3, nouvelle: 1})
        self.assertEqual((l2["qte_comptee"], l2["ecart"], l2["commentaire"]), (s2, 0, "ras"))
        self.assertIsNone(l3["qte_comptee"])
        # Une ligne renvoyée sans case redevient « non comptée » ; les lignes absentes de l'envoi ne bougent pas.
        I.enregistrer_inventaire(name, json.dumps({i2: {"cases": {I.SANS_ZONE: None}}}))
        d = I.detail_inventaire(name)
        self.assertIsNone(self._ligne(d, i2)["qte_comptee"])
        self.assertEqual(self._ligne(d, i1)["qte_comptee"], s1 - 2)
        b = I.bilan_cloture(name)
        self.assertEqual((b["nb_comptes"], b["nb_non_comptes"], b["nb_ecarts"]), (1, len(d["lignes"]) - 1, 1))
        self.assertEqual([x["item_code"] for x in b["a_rapprocher"]], [i1])
        self.assertEqual(b["zones_nouvelles"], [{"item_code": i1, "item_name": l1["item_name"], "zones": [nouvelle]}])
        r = I.cloturer_inventaire(name, note="essai")
        self.assertTrue(r["rapprochement"])
        self.assertEqual((r["nb_rapproches"], r["nb_zones_ajoutees"]), (1, 1))
        self.assertEqual(frappe.db.get_value("Stock Reconciliation", r["rapprochement"], "docstatus"), 1)
        self.assertEqual(self._stock(i1), s1 - 2)                          # le Magasin est aligné sur le compté
        self.assertEqual((self._stock(i2), self._stock(i3)), (s2, s3))     # les non comptés ne bougent pas
        self.assertIn(nouvelle, Z.zones_de(i1))                            # toujours sur l'article
        v = frappe.get_doc(I.INV, name)
        self.assertEqual((v.statut, v.note, v.rapprochement), (I.TERMINE, "essai", r["rapprochement"]))
        self.assertIsNone(I.en_cours())
        with self.assertRaises(frappe.ValidationError):
            I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: 1}}}))

    def test_un_article_qui_bouge_apres_son_comptage(self):
        """Compté 10 quand le système disait 12, puis 3 sortis : le rapproché est 9 − 2 = 7, pas 10."""
        import frappe
        i1 = self.items[0]
        s1 = self._stock(i1)
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: s1 - 2}}}))
        r = S.creer_transfert(self.magasin, self.essai, json.dumps([{"item_code": i1, "qte": 3}]))
        self.assertFalse(r.get("en_attente"))
        self.assertEqual(self._stock(i1), s1 - 3)
        d = I.detail_inventaire(name)
        l1 = self._ligne(d, i1)
        self.assertEqual((l1["qte_systeme"], l1["qte_actuelle"], l1["bouge"]), (s1, s1 - 3, True))
        b = I.bilan_cloture(name)
        self.assertEqual([(x["item_code"], x["qte_rapprochee"]) for x in b["bouges"]], [(i1, s1 - 5)])
        r = I.cloturer_inventaire(name)
        self.assertEqual((r["nb_bouges"], self._stock(i1)), (1, s1 - 5))

    def test_inventaire_partiel_par_zone(self):
        """Un périmètre = des zones : feuille limitée, inventaires parallèles sans recoupement, un complet exclu
        tant qu'un partiel est ouvert, historique par zone après clôture."""
        import frappe
        i1, i2 = self.items[0], self.items[1]
        s1 = self._stock(i1)
        z1 = self.zone
        z2 = frappe.db.get_value(Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace, "name": ["!=", z1]}, "name")
        if Z._ajouter(i1, z1):
            Z._resynchroniser(i1)
        for z in Z.zones_de(i2):                                            # i2 hors de z1 pour le test
            if z == z1:
                self.skipTest("l'article témoin est rangé dans la zone d'essai")
        a = I.ouvrir_inventaire(json.dumps([z1]))
        self.assertEqual(I.ouvrir_inventaire([z1]), a)                      # même périmètre = repris
        d = I.detail_inventaire(a)
        self.assertEqual((d["fiche"]["perimetre"], d["fiche"]["statut"]), ([z1], I.EN_COURS))
        codes = {l["item_code"] for l in d["lignes"]}
        self.assertIn(i1, codes)
        self.assertNotIn(i2, codes)
        self.assertTrue(all(z1 in l["zones"] for l in d["lignes"]))
        with self.assertRaises(frappe.ValidationError):                     # recoupement : un complet est refusé
            I.ouvrir_inventaire()
        with self.assertRaises(frappe.ValidationError):
            I.ouvrir_inventaire([z1, I.SANS_ZONE])
        with self.assertRaises(frappe.ValidationError):                     # zone inconnue
            I.ouvrir_inventaire(["Hall - Nulle part"])
        b = I.ouvrir_inventaire([I.SANS_ZONE] + ([z2] if z2 else []))      # disjoint : ouvert en parallèle
        self.assertNotEqual(a, b)
        etat = I.inventaires_etat()
        self.assertEqual([v["name"] for v in etat["ouverts"]], [a, b])
        hz = {z["name"]: z for z in etat["zones_historique"]}
        self.assertEqual((hz[z1]["en_cours"], hz[I.SANS_ZONE]["en_cours"]), (a, b))
        self.assertTrue(all(i2 != l["item_code"] or not l["zones"] for l in I.detail_inventaire(b)["lignes"]))
        I.enregistrer_inventaire(a, json.dumps({i1: {"cases": {z1: s1 - 1}}}))
        r = I.cloturer_inventaire(a)
        self.assertEqual((r["nb_rapproches"], self._stock(i1)), (1, s1 - 1))
        etat = I.inventaires_etat()
        hz = {z["name"]: z for z in etat["zones_historique"]}
        self.assertEqual((hz[z1]["en_cours"], hz[z1]["dernier_inventaire"]), (None, a))
        self.assertEqual([v["name"] for v in etat["ouverts"]], [b])
        with self.assertRaises(frappe.ValidationError):                     # b toujours ouvert : pas de complet
            I.ouvrir_inventaire()
        I.annuler_inventaire(b)
        c = I.ouvrir_inventaire()
        self.assertEqual(I.detail_inventaire(c)["fiche"]["perimetre"], [])
        self.assertIn(i2, {l["item_code"] for l in I.detail_inventaire(c)["lignes"]})

    def test_taux_saisi_a_la_cloture(self):
        """Un article sans aucun taux est exclu ; un taux saisi par un responsable l'inclut et prime sur
        l'automatique ; l'écriture porte ce taux ; vide = retour à l'automatique."""
        import frappe
        i1, i2 = self.items[0], self.items[1]
        s1, s2 = self._stock(i1), self._stock(i2)
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: s1 - 2}}, i2: {"cases": {I.SANS_ZONE: s2 + 1}}}))
        v = frappe.get_doc(I.INV, name)
        frappe.db.set_value("Inventaire Magasin Ligne", I._ligne(v, i1).name, "taux", 0)     # plus aucun taux nulle part
        frappe.db.set_value("Item", i1, {"valuation_rate": 0, "last_purchase_rate": 0})
        b = I.bilan_cloture(name)
        self.assertEqual([x["item_code"] for x in b["sans_taux"]], [i1])
        self.assertEqual([x["item_code"] for x in b["a_rapprocher"]], [i2])
        taux_auto_i2 = b["a_rapprocher"][0]["taux"]
        self.assertGreater(taux_auto_i2, 0)
        with self.assertRaises(frappe.ValidationError):
            I.definir_taux(name, i1, -1)
        r = I.definir_taux(name, i1, 12.5)
        self.assertEqual((r["taux"], r["taux_saisi"], r["valeur_ecart"]), (12.5, 12.5, -25))
        I.definir_taux(name, i2, 7)                                        # prime sur le taux photographié
        b = I.bilan_cloture(name)
        self.assertFalse(b["sans_taux"])
        self.assertEqual({x["item_code"]: x["taux"] for x in b["a_rapprocher"]}, {i1: 12.5, i2: 7})
        self.assertEqual(self._ligne(I.detail_inventaire(name), i2)["taux_saisi"], 7)
        I.definir_taux(name, i2, None)                                     # vide = automatique
        b = I.bilan_cloture(name)
        self.assertEqual({x["item_code"]: x["taux"] for x in b["a_rapprocher"]}, {i1: 12.5, i2: taux_auto_i2})
        r = I.cloturer_inventaire(name)
        self.assertEqual((r["nb_rapproches"], r["nb_sans_taux"]), (2, 0))
        taux = {x.item_code: S.flt(x.valuation_rate, 3) for x in frappe.get_doc("Stock Reconciliation", r["rapprochement"]).items}
        self.assertEqual(taux, {i1: 12.5, i2: taux_auto_i2})
        self.assertEqual((self._stock(i1), self._stock(i2)), (s1 - 2, s2 + 1))

    def test_taux_change_sans_ecart_de_quantite(self):
        """Compté juste, mais taux saisi différent du taux de l'entrepôt : la ligne entre dans l'écriture pour son
        taux seul, et le stock prend ce taux (demande du 02/11/2026)."""
        import frappe
        i1 = self.items[0]
        s1 = self._stock(i1)
        taux_avant = S.flt(frappe.db.get_value("Bin", {"item_code": i1, "warehouse": self.magasin}, "valuation_rate"), 6)
        plafond = (I._plafonds([i1]).get(i1) or {}).get("plafond")
        nouveau = round(max(min(taux_avant * 0.5, (plafond or 1e9) * 0.9), 0.01), 3)   # différent du taux, sous le plafond
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: s1}}}))
        self.assertFalse(I.bilan_cloture(name)["a_rapprocher"])                 # juste, taux inchangé : rien
        I.definir_taux(name, i1, nouveau)
        b = I.bilan_cloture(name)
        self.assertEqual([(x["item_code"], x["taux_seul"], x["taux"]) for x in b["a_rapprocher"]], [(i1, True, nouveau)])
        self.assertEqual(b["nb_taux_seuls"], 1)
        r = I.cloturer_inventaire(name)
        self.assertEqual(r["nb_rapproches"], 1)
        sr = frappe.get_doc("Stock Reconciliation", r["rapprochement"])
        self.assertEqual([(x.item_code, S.flt(x.qty, 6), S.flt(x.valuation_rate, 3)) for x in sr.items], [(i1, s1, nouveau)])
        self.assertEqual(self._stock(i1), s1)
        self.assertEqual(S.flt(frappe.db.get_value("Bin", {"item_code": i1, "warehouse": self.magasin}, "valuation_rate"), 3), nouveau)

    def test_retirer_un_emplacement_ajoute_par_erreur(self):
        import frappe
        i1 = self.items[0]
        name = I.ouvrir_inventaire()
        zone = self.zone if self.zone not in Z.zones_de(i1) else frappe.db.get_value(
            Z.DOCTYPE_ZONE, {"est_espace": 0, "espace": self.espace, "name": ["not in", Z.zones_de(i1)]}, "name")
        avant = Z.zones_de(i1)
        I.ajouter_emplacement(name, i1, zone)
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {zone: 4}}}))
        r = I.retirer_emplacement(name, i1, zone)
        self.assertEqual((Z.zones_de(i1), r["zones_ajoutees"]), (avant, []))
        self.assertIsNone(self._ligne(I.detail_inventaire(name), i1)["qte_comptee"])   # sa case est effacée
        if avant:                                                          # un emplacement d'origine ne se retire pas ici
            with self.assertRaises(frappe.ValidationError):
                I.retirer_emplacement(name, i1, avant[0])

    def test_annuler_ne_bouge_rien(self):
        import frappe
        i1 = self.items[0]
        s1 = self._stock(i1)
        name = I.ouvrir_inventaire()
        I.enregistrer_inventaire(name, json.dumps({i1: {"cases": {I.SANS_ZONE: 0}}}))
        r = I.annuler_inventaire(name, motif="test")
        self.assertEqual(r["statut"], I.ANNULE)
        self.assertEqual(self._stock(i1), s1)
        self.assertFalse(frappe.db.get_value(I.INV, name, "rapprochement"))
        self.assertIsNone(I.en_cours())

    def test_ecritures_en_post_seulement(self):
        import frappe
        for fn in (I.ouvrir_inventaire, I.enregistrer_inventaire, I.cloturer_inventaire, I.annuler_inventaire,
                   I.ajouter_article, I.ajouter_emplacement, I.retirer_emplacement):
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func.get(fn), ["POST"], fn.__name__)
