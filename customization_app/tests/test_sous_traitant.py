"""Sous-traitants (décisions utilisateur 30/10/2026) : règles pures, câblage des fichiers."""
from __future__ import annotations

import inspect
import json
import os
import unittest

from customization_app import sous_traitant as ST
from customization_app import stock_entrepots as SE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestArticles(unittest.TestCase):
    STOCK = {"F-T33-M": 5, "Acc001-B": 20}
    PRIX = {"F-T33-M": 25.0, "Acc001-B": 1.5, "X": 0}
    STOCK_X = {"F-T33-M": 5, "Acc001-B": 20, "X": 1}

    def test_lignes_valides_et_cumul_des_doublons(self):
        lignes, err = ST.lignes_articles([{"item_code": "F-T33-M", "qty": 2}, {"item_code": "F-T33-M", "qty": 3},
                                          {"item_code": "Acc001-B", "qty": 1}], self.STOCK, self.PRIX)
        self.assertEqual(err, [])
        self.assertEqual(lignes, [{"item_code": "F-T33-M", "qty": 2.0, "rate": 25.0},
                                  {"item_code": "F-T33-M", "qty": 3.0, "rate": 25.0},
                                  {"item_code": "Acc001-B", "qty": 1.0, "rate": 1.5}])

    def test_refus_stock_insuffisant_prix_absent_quantite_nulle(self):
        lignes, err = ST.lignes_articles([{"item_code": "F-T33-M", "qty": 6}, {"item_code": "X", "qty": 1},
                                          {"item_code": "Acc001-B", "qty": 0}], self.STOCK_X, self.PRIX)
        self.assertEqual(lignes, [])
        self.assertEqual(len(err), 3)
        self.assertIn("6.0 demandé, 5.0 dans votre stock", err[0])
        self.assertIn("pas de prix de vente", err[1])
        self.assertEqual(ST.lignes_articles([], {}, {})[1], ["Aucun article."])

    def test_main_d_oeuvre_sans_stock(self):
        """Une prestation (main d'œuvre) n'a pas de stock : acceptée si elle est déclarée `sans_stock`, refusée sinon."""
        prix = dict(self.PRIX, **{"M-I-OD": 50.0})
        lignes, err = ST.lignes_articles([{"item_code": "M-I-OD", "qty": 1}, {"item_code": "F-T33-M", "qty": 1}],
                                         self.STOCK, prix, sans_stock={"M-I-OD"})
        self.assertEqual(err, [])
        self.assertEqual(lignes[0], {"item_code": "M-I-OD", "qty": 1.0, "rate": 50.0})
        _, err = ST.lignes_articles([{"item_code": "M-I-OD", "qty": 1}], self.STOCK, prix)
        self.assertIn("1.0 demandé, 0.0 dans votre stock", err[0])
        self.assertEqual(ST.MAIN_OEUVRE, {"Installation": "M-I-OD", "Entretien": "M-E-OD", "Réparation": "M-R-OD"})


class TestReglement(unittest.TestCase):
    def test_fractionne_qui_tombe_juste(self):
        lignes, err = ST.lignes_paiement([{"mode": "Espèces", "montant": 40},
                                          {"mode": "Chèque", "montant": 60.5, "numero": "123", "banque": "BIAT"}],
                                         100.5, "2026-10-30")
        self.assertEqual(err, [])
        self.assertEqual(lignes[1], {"mode_of_payment": "Chèque", "payment_amount": 60.5, "due_date": "2026-10-31",
                                     "custom__n_chèque__transaction": "123", "custom_banque": "BIAT"})
        self.assertNotIn("custom__n_chèque__transaction", lignes[0])
        self.assertEqual(lignes[0]["due_date"], "2026-10-30")      # fractionné : J, J+1… (ERPNext refuse deux échéances le même jour)

    def test_refus_virement_carte_cheque_sans_numero_et_ecart(self):
        _, err = ST.lignes_paiement([{"mode": "Virement", "montant": 10}, {"mode": "Carte de crédit", "montant": 10},
                                     {"mode": "Chèque", "montant": 10}], 30)
        self.assertEqual(len(err), 4)                                    # 3 refus + aucun règlement
        self.assertIn("non permis : Virement", err[0])
        self.assertIn("numéro est obligatoire", err[2])
        _, err = ST.lignes_paiement([{"mode": "Espèces", "montant": 10},
                                     {"mode": "Dette non payée", "montant": 15, "motif": "Litige avec le client", "commentaire": "machine en panne, attend la réparation"}], 30)
        self.assertEqual(err, ["Les règlements font 25.0, la commande 30.0."])
        self.assertEqual(ST.MODES_PERMIS, ("Espèces", "Chèque", "Traite bancaire LC", "Dette non payée"))

    def test_dette_justifiee(self):
        """Une dette exige un motif de la liste et un commentaire d'au moins 10 caractères, gardés
        « motif — commentaire » sur la ligne (31/10/2026)."""
        _, err = ST.lignes_paiement([{"mode": "Dette non payée", "montant": 30}], 30)
        self.assertEqual(err[0], "Dette : choisissez pourquoi c'est une dette.")      # + « Aucun règlement saisi. »
        _, err = ST.lignes_paiement([{"mode": "Dette non payée", "montant": 30, "motif": "Autre", "commentaire": "rs"}], 30)
        self.assertEqual(err[0], "Dette : expliquez en commentaire (10 caractères minimum).")
        _, err = ST.lignes_paiement([{"mode": "Dette non payée", "montant": 30, "motif": "Pas dans la liste", "commentaire": "un commentaire assez long"}], 30)
        self.assertEqual(err[0], "Dette : choisissez pourquoi c'est une dette.")
        lignes, err = ST.lignes_paiement([{"mode": "Dette non payée", "montant": 30, "motif": "Dette accordée par la direction (qui ?)",
                                           "commentaire": "  accord de   Wassim par téléphone "}], 30, "2026-10-31")
        self.assertEqual(err, [])
        self.assertEqual(lignes[0]["custom_justification_dette"], "Dette accordée par la direction (qui ?) — accord de Wassim par téléphone")
        self.assertEqual(ST._justification(lignes[0]["custom_justification_dette"]),
                         {"motif": "Dette accordée par la direction (qui ?)", "commentaire": "accord de Wassim par téléphone"})
        self.assertEqual(ST._justification(None), {})
        self.assertIn("Autre", ST.MOTIFS_DETTE)


class TestRemise(unittest.TestCase):
    """Une remise sur le total se justifie comme une dette (31/10/2026)."""
    def test_sans_remise(self):
        self.assertEqual(ST.remise_valide(None, 100), (0.0, "", []))
        self.assertEqual(ST.remise_valide({"montant": 0, "motif": "Autre"}, 100), (0.0, "", []))

    def test_remise_justifiee_ou_refusee(self):
        m, j, err = ST.remise_valide({"montant": 10, "motif": "Promotion en cours", "commentaire": " remise  fidélité octobre "}, 100)
        self.assertEqual((m, j, err), (10.0, "Promotion en cours — remise fidélité octobre", []))
        self.assertEqual(ST.remise_valide({"montant": 10}, 100)[2], ["Remise : choisissez pourquoi vous faites une remise."])
        self.assertEqual(ST.remise_valide({"montant": 10, "motif": "Autre", "commentaire": "court"}, 100)[2],
                         ["Remise : expliquez en commentaire (10 caractères minimum)."])
        self.assertEqual(ST.remise_valide({"montant": 120, "motif": "Autre", "commentaire": "un commentaire assez long"}, 100)[2],
                         ["Remise : 120.0 dépasse le total 100.0."])
        self.assertIn("Autre", ST.MOTIFS_REMISE)


class TestNomsEtCouleurs(unittest.TestCase):
    def test_noms(self):
        self.assertEqual(ST.nom_entrepot("  Ali   Ben Salah "), "Stock Ali Ben Salah")
        self.assertEqual(ST.nom_compte(" Ali  Ben Salah"), "Ali Ben Salah")

    def test_couleur_stable_et_dans_la_palette(self):
        self.assertEqual(ST.couleur_par_defaut("HR-EMP-00020"), ST.couleur_par_defaut("HR-EMP-00020"))
        self.assertIn(ST.couleur_par_defaut("HR-EMP-00020"), ST.PALETTE)


class TestVerificationQuinzaine(unittest.TestCase):
    def test_saute_si_une_verification_dans_les_13_jours(self):
        self.assertTrue(SE.quinzaine_a_sauter("2026-11-09", ["2026-11-02"]))
        self.assertFalse(SE.quinzaine_a_sauter("2026-11-09", ["2026-10-26"]))
        self.assertFalse(SE.quinzaine_a_sauter("2026-11-09", ["2026-11-09"]))      # la même date : déjà filtrée avant
        self.assertFalse(SE.quinzaine_a_sauter("2026-11-09", []))
        self.assertEqual((SE.HEBDO, SE.QUINZAINE), ("Chaque semaine", "Toutes les deux semaines"))


class TestCouleurs(unittest.TestCase):
    def test_reunion_prevue_rouge_tenue_verte(self):
        from customization_app import api
        self.assertEqual(api.COULEUR_REUNION, "#dc2626")
        self.assertEqual(api.COULEUR_REUNION_TENUE, "#22c55e")


class TestEcheancier(unittest.TestCase):
    """31/10/2026 : moyen de paiement obligatoire sur l'échéancier des VENTES seulement, plus sur les achats."""

    def test_lignes_sans_mode(self):
        from customization_app import echeancier as E
        lignes = [{"idx": 1, "mode_of_payment": "Espèces"}, {"idx": 2, "mode_of_payment": None}, {"idx": 3, "mode_of_payment": ""}]
        self.assertEqual(E.lignes_sans_mode("Sales Order", lignes), [2, 3])
        self.assertEqual(E.lignes_sans_mode("Sales Invoice", lignes), [2, 3])
        self.assertEqual(E.lignes_sans_mode("Purchase Invoice", lignes), [])
        self.assertEqual(E.lignes_sans_mode("Sales Order", []), [])

    def test_cablage(self):
        with open(os.path.join(BASE, "hooks.py"), encoding="utf-8") as f:
            self.assertEqual(f.read().count('"customization_app.echeancier.exiger_mode_de_paiement"'), 2)
        with open(os.path.join(BASE, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("customization_app.patches.echeancier_mode_achats", f.read())
        from customization_app.patches import echeancier_mode_achats as P
        src = inspect.getsource(P.execute)
        self.assertIn('"reqd", 0', src)
        self.assertIn('includes(parent.doctype)', src)


class TestPiecesJointes(unittest.TestCase):
    def test_copie_de_scan_sous_identite_systeme(self):
        """31/10/2026 : un scan déjà joint à une écriture de caisse ne doit pas bloquer l'employé qui saisit la facture."""
        from customization_app import caisse_depenses as CD
        for f in (CD._copier_justificatifs, CD._joindre):
            self.assertIn("_inserer_piece_jointe(", inspect.getsource(f))
            self.assertNotIn(".insert(ignore_permissions=True)", inspect.getsource(f))
        src = inspect.getsource(CD._inserer_piece_jointe)
        self.assertIn('frappe.set_user("Administrator")', src)
        self.assertIn("finally:", src)
        self.assertIn('"owner", utilisateur', src)


class TestCollecteCaisse(unittest.TestCase):
    """31/10/2026 : à la collecte validée, ses paiements quittent son compte et entrent dans la caisse du collecteur."""

    def test_comptes_cibles(self):
        from customization_app import sous_traitant_caisse as SC
        self.assertEqual(SC.compte_cible("Espèces"), "Espèces - A&S")
        self.assertEqual(SC.compte_cible("Chèque"), "Chèques - A&S")
        self.assertEqual(SC.compte_cible("Traite bancaire LC"), "Traite Bancaire - A&S")
        self.assertIsNone(SC.compte_cible("Dette non payée"))   # une dette naît déjà sur Dettes - A&S
        self.assertIsNone(SC.compte_cible(None))
        self.assertEqual(set(SC.COMPTES_CIBLES), set(ST.MODES_PERMIS) - {ST.MODE_DETTE})

    def test_responsable_sous_traitant_collecte_leurs_caisses(self):
        from customization_app import caisse_collecte as CC
        # Fonction pure : Config Caisse d'abord, sinon « titulaire » sur la caisse d'un sous-traitant seulement.
        self.assertEqual(CC._role("x@y", ["x@y"], [], False), "titulaire")
        for f in (CC.collecter, CC.contester_justifications):
            src = inspect.getsource(f)
            self.assertLess(src.index('frappe.get_doc("Cloture Caisse", name)'), src.index("_exiger_collecteur(cl.caisse)"))
        self.assertIn("perimetre_sous_traitants()", inspect.getsource(CC.a_collecter))
        self.assertIn("perimetre_sous_traitants(user)", inspect.getsource(CC.resume))
        self.assertIn("perimetre_sous_traitants(user)", inspect.getsource(CC.peut_agir_sur))
        from customization_app import caisse_cloture as CL
        self.assertIn("role_collecte_pour(caisse)", inspect.getsource(CL._controler_droits))

    def test_branche_sur_la_collecte_apres_le_pdf(self):
        from customization_app import caisse_collecte as CC
        src = inspect.getsource(CC.finaliser)
        self.assertLess(src.index("save_file("), src.index("sous_traitant_caisse.transferer(doc)"))
        self.assertIn("_est_caisse_sous_traitant(doc.caisse)", src)


class TestCablage(unittest.TestCase):
    def test_hooks_droits_scripts_et_pages(self):
        with open(os.path.join(BASE, "hooks.py"), encoding="utf-8") as f:
            hooks = f.read()
        for attendu in ('"Tache de travail": "customization_app.sous_traitant.permission_query_conditions_tache"',
                        # 31/10/2026 : SES vérifications de stock et SES clôtures de caisse ; commandes et BL
                        # restent lisibles en entier (historique des clients).
                        '"Verification Stock": "customization_app.sous_traitant.has_permission_verification"',
                        '"Cloture Caisse": "customization_app.sous_traitant.has_permission_cloture_caisse"',
                        'boot_session = "customization_app.sous_traitant.boot_session"',
                        '_js("cloture_sous_traitant.js")', '_js("sous_traitant_admin.js")', '_js("sous_traitant.js")'):
            self.assertIn(attendu, hooks)
        for interdit in ("has_permission_commande", "permission_query_conditions_bl"):
            self.assertNotIn(interdit, hooks)
        with open(os.path.join(BASE, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("customization_app.patches.sous_traitant_role", f.read())
        # 31/10/2026 : l'onglet « Sous-traitant » (System Manager) est le poste d'administration, rejoué à chaque migrate ;
        # son bloc HTML délègue au panneau de sous_traitant_admin.js (liste, création, activer/désactiver, marche à suivre).
        self.assertIn('"customization_app.patches.ensure_onglet_sous_traitant.execute"', hooks)
        from customization_app.patches import ensure_onglet_sous_traitant as OST
        self.assertEqual(OST.ROLES, ("System Manager", "Responsable sous-traitant"))
        self.assertEqual(ST.SUPERVISEURS, ("System Manager", ST.ROLE_RESPONSABLE))
        self.assertEqual(OST.RESPONSABLES_USERS, ("koubaawassim@gmail.com", "aquaworld.servicing@gmail.com", "jimmyks007@gmail.com"))
        for f in (ST.liste, ST.creer, ST.activer, ST.changer_couleur):
            self.assertIn("frappe.only_for(SUPERVISEURS)", inspect.getsource(f))
        self.assertIn("window.sousTraitant_admin", OST.BLOC_SCRIPT)
        self.assertEqual([r[0] for r in OST.RACCOURCIS][:3], ["Sous-traitants (employés)", "Calendrier de travail", "Stock et transferts"])
        with open(os.path.join(BASE, "public", "js", "sous_traitant_admin.js"), encoding="utf-8") as f:
            admin = f.read()
        for attendu in ("window.sousTraitant_admin = function", "window.sousTraitant_nouveau = dialogue",
                        "customization_app.sous_traitant.liste", "customization_app.sous_traitant.activer", "Marche à suivre",
                        'frappe.user.has_role("Responsable sous-traitant")'):
            self.assertIn(attendu, admin)
        self.assertEqual(list(inspect.signature(ST.activer).parameters), ["employe", "actif"])
        # 31/10/2026 : la pastille du panneau change la couleur et recolore toutes ses tâches ouvertes.
        self.assertEqual(list(inspect.signature(ST.changer_couleur).parameters), ["employe", "couleur"])
        self.assertIn("status = 'Open' AND docstatus < 2", inspect.getsource(ST.changer_couleur))
        self.assertNotIn("frappe.local.__dict__", inspect.getsource(ST))   # werkzeug Local : pas de __dict__
        for attendu in ("customization_app.sous_traitant.changer_couleur", "[data-couleur]", 'fieldtype: "Color"'):
            self.assertIn(attendu, admin)
        with open(os.path.join(BASE, "fixtures", "server_script.json"), encoding="utf-8") as f:
            scripts = {s["name"]: s["script"] for s in json.load(f)}
        # le routage (1 lecture + son commentaire) dans chaque script, et la règle de verrou dans le second
        self.assertEqual(scripts["Generation payement"].count("custom_compte_caisse"), 2)
        self.assertEqual(scripts["re-generate payment after sales order"].count("custom_compte_caisse"), 3)
        with open(os.path.join(BASE, "customize_erpnext", "page", "stock_entrepots", "stock_entrepots.json"),
                  encoding="utf-8") as f:
            self.assertIn({"role": "Sous-traitant"}, json.load(f)["roles"])
        with open(os.path.join(BASE, "customize_erpnext", "doctype", "config_stock_entrepot_verification",
                               "config_stock_entrepot_verification.json"), encoding="utf-8") as f:
            self.assertIn("periodicite", json.load(f)["field_order"])
        from customization_app import caisse_cloture
        self.assertIn("Sous-traitant", caisse_cloture.ROLES)
        self.assertIn("Sous-traitant", SE.LECTEURS)

    def test_page_stock_et_navigation_du_sous_traitant(self):
        """Page Stock : l'onglet BL retiré et « Tous » absent quand un seul entrepôt ; la barre de navigation
        (Ma journée, Calendrier, Mon stock, Ma caisse) et la barre latérale masquée (31/10/2026)."""
        with open(os.path.join(BASE, "customize_erpnext", "page", "stock_entrepots", "stock_entrepots.js"),
                  encoding="utf-8") as f:
            js = f.read()
        self.assertIn("if (ctx.sous_traitant) r.find('[data-onglet=\"bl\"]').remove();", js)
        self.assertIn("liste.length > 1 ? `<span class=\"se-chip", js)
        self.assertIn("if (this.ctx.sous_traitant) return;", js)
        with open(os.path.join(BASE, "public", "js", "sous_traitant.js"), encoding="utf-8") as f:
            nav = f.read()
        for attendu in (".layout-side-section", "st-nav", '"stock-entrepots"', '"caisse-journaliere"',
                        "maJournee_ouvrir", '["agenda-sous-traitant"]', 'r[0] === "tache-de-travail" && r[1] !== "Form"'):
            self.assertIn(attendu, nav)
        # Le serveur impose l'entrepôt du sous-traitant sur le solde et les mouvements.
        for fn in (SE.get_solde, SE.get_mouvements, SE.get_mouvements_article):
            self.assertIn("_entrepot_impose() or entrepot", inspect.getsource(fn))

    def test_commande_liee_et_annulation(self):
        """31/10/2026 : la clôture mobile MONTRE la commande préparée par le bureau (articles, règlement prévu,
        lien) ; la carte de Ma journée remplace « Ouvrir la fiche » par « Annuler » pour lui, qui passe par
        taches_groupe.annuler (même mécanisme que le bureau), sans message au client."""
        # 31/10/2026 : la PAGE « Mon agenda » (jour / semaine / mois) est son fond ; ni liste ni calendrier des tâches.
        with open(os.path.join(BASE, "public", "js", "sous_traitant.js"), encoding="utf-8") as f:
            self.assertNotIn('"Calendrier de travail"', f.read())
        dossier = os.path.join(BASE, "customize_erpnext", "page", "agenda_sous_traitant")
        with open(os.path.join(dossier, "agenda_sous_traitant.json"), encoding="utf-8") as f:
            page = json.load(f)
        self.assertEqual(page["page_name"], "agenda-sous-traitant")
        self.assertIn({"role": "Sous-traitant"}, page["roles"])
        with open(os.path.join(dossier, "agenda_sous_traitant.js"), encoding="utf-8") as f:
            agenda = f.read()
        for attendu in ('frappe.pages["agenda-sous-traitant"].on_page_load', "customization_app.sous_traitant.agenda",
                        'data-vue="${v}"', "startOf(\"isoWeek\")", "window.maJournee_ouvrir($(e.currentTarget).data(\"journee\"))",
                        "rafraichir()"):
            self.assertIn(attendu, agenda)
        self.assertNotIn("frappe.ui.Dialog", agenda)
        self.assertEqual(list(inspect.signature(ST.agenda).parameters), ["debut", "fin"])
        with open(os.path.join(BASE, "public", "js", "cloture_sous_traitant.js"), encoding="utf-8") as f:
            js = f.read()
        for attendu in ("customization_app.sous_traitant.commande_liee", "Articles à poser", "Règlement prévu", "Ouvrir la commande",
                        # commande en brouillon modifiable, main d'œuvre proposée d'office
                        "this.modifiable = !!(c.commande && c.modifiable)", "c.main_oeuvre", "préparée par le bureau, modifiable",
                        # dette justifiée, photo du chèque / de la traite lue par OpenAI (31/10/2026)
                        "Pourquoi c'est une dette ?", "customization_app.caisse_pieces.lire_piece", "data-photo-piece", 'capture="environment"'):
            self.assertIn(attendu, js)
        for fn in (ST._nouvelle_commande, ST._modifier_commande):
            src = inspect.getsource(fn)
            self.assertIn("_tracer_dettes(so.name, echeances)", src)
            self.assertIn("_joindre_photos_pieces(so.name, paiements)", src)
            self.assertIn("_appliquer_remise(so, remise)", src)          # la remise AVANT l'échéancier
            self.assertLess(src.index("_appliquer_remise(so, remise)"), src.index("lignes_paiement(paiements, so.grand_total"))
        with open(os.path.join(BASE, "patches", "sous_traitant_role.py"), encoding="utf-8") as f:
            patch = f.read()
        self.assertIn('"custom_justification_dette"', patch)
        self.assertIn('"custom_justification_remise"', patch)
        for attendu in ("Pourquoi cette remise ?", "data-remise-montant", "net à régler", "this.net()"):
            self.assertIn(attendu, js)
        with open(os.path.join(BASE, "public", "js", "ma_journee.js"), encoding="utf-8") as f:
            mj = f.read()
        for attendu in ("data-annuler", "customization_app.sous_traitant.annuler_intervention", "raison_annulation"):
            self.assertIn(attendu, mj)
        src = inspect.getsource(ST.cloturer_intervention)
        self.assertIn("_modifier_commande(doc, emp, articles, paiements, remise)", src)
        src = inspect.getsource(ST.annuler_intervention)
        self.assertIn("G.annuler([doc.name], motif=motif, sms=0, email=0)", src)
        self.assertIn('"raison_annulation"', src)

    def test_caisse_du_sous_traitant(self):
        """Page Caisse (31/10/2026) : son écran allégé (ni suivis, ni chèques impayés / dettes / Aramex /
        dépense / état des caisses ; le détail de ses ventes reste), ouverte sur SA période depuis sa dernière remise ;
        la clôture porte sur cette période (serveur : debut_periode, rejouée dans etat et valider)."""
        with open(os.path.join(BASE, "customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.js"),
                  encoding="utf-8") as f:
            js = f.read()
        self.assertIn("if (frappe.boot.sous_traitant) this._ecran_sous_traitant();", js)
        bloc = js.split("_ecran_sous_traitant() {", 1)[1].split("_periode_sous_traitant =", 1)[0]
        for cache in ("#rcj-suivi-depenses", "#rcj-suivi-achats", "#rcj-btn-impayes", "#rcj-btn-dettes", "#rcj-btn-aramex",
                      "#rcj-btn-depense", "#rcj-btn-etat"):
            self.assertIn(cache, bloc)
        # Le détail de SES ventes et règlements reste (il doit voir à quelle vente va chaque encaissement).
        self.assertNotIn("#rcj-employees", bloc)
        self.assertIn('"Mes ventes et encaissements"', bloc)
        self.assertIn("customization_app.caisse_cloture.periode_sous_traitant", js)
        self.assertIn("const d1 = sous_traitant ? $(\"#rcj-d2\").val() : $(\"#rcj-d1\").val()", js)
        from customization_app import caisse_cloture as CC
        self.assertIn("debut_periode(caisse, date)", inspect.getsource(CC._mesures))
        for fn in (CC.etat, CC.valider):
            self.assertIn('points_controle(caisse, date, m["data"], m["debut"])', inspect.getsource(fn))
