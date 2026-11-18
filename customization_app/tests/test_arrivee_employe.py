"""Arrivée d'un technicien (10/11/2026) : logique pure et câblage. Tourne sans site."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import arrivee_employe as A


class TestPur(unittest.TestCase):

    def test_decouper_nom(self):
        self.assertEqual(A.decouper_nom("  Mohamed   Hedi Chouchane "), ("Mohamed", "Hedi Chouchane"))
        self.assertEqual(A.decouper_nom("Akram"), ("Akram", None))

    def test_ligne_existe_et_cles(self):
        lignes = [{"employe": "HR-EMP-00001", "nom_employe": "Sadok"}, {"user": "x@y.z"}]
        self.assertTrue(A.ligne_existe(lignes, {"employe": "HR-EMP-00001", "nom_employe": "autre"}, ("employe",)))
        self.assertFalse(A.ligne_existe(lignes, {"employe": "HR-EMP-00002"}, ("employe",)))
        self.assertEqual(A.cles_identite({"employee": "x", "mode": "Ventes"}), ("employee",))
        self.assertEqual(A.cles_identite({"caisse": "Sadok", "date_depart": "2026-11-10"}), ("caisse",))
        self.assertEqual(A.cles_identite({"user": "x@y.z", "weight": 1}), ("user",))

    def test_conges_payes_au_prorata(self):
        self.assertEqual(A.jours_conges_payes("2026-10-05"), 4.5)     # Hedi Ibidhi, entré en octobre
        self.assertEqual(A.jours_conges_payes("2026-01-15"), 18.0)
        self.assertEqual(A.jours_conges_payes("2026-12-01"), 1.5)

    def test_profil_technicien(self):
        """Les rôles de Mohamed Hedi Chouchane (relevés le 10/11/2026) + Technicien, pas de rôle d'administration."""
        for r in ("Employee", "Maintenance Manager", "POS Manager", "Raven User", "Sales Manager", "Sales User", "Stock User",
                  "Technicien"):
            self.assertIn(r, A.ROLES_TECHNICIEN)
        for r in ("System Manager", "Responsable magasin", "Sous-traitant", "Congés RH"):
            self.assertNotIn(r, A.ROLES_TECHNICIEN)

    def test_reglages_vehicule_facultatif(self):
        emp = type("E", (), {"name": "HR-EMP-00099", "employee_name": "Test Tech"})()
        fabriques = {enfant: f for _s, enfant, f in A.REGLAGES}
        self.assertIsNone(fabriques["Config Flotte GPS Vehicule"](emp, "t@x.y", {"vehicule": "", "cbox": ""}))
        self.assertEqual(fabriques["Config Flotte GPS Vehicule"](emp, "t@x.y", {"vehicule": "123 TU 1", "cbox": "999"})["actif"], 1)
        self.assertIsNone(fabriques["Assignment Rule User"](emp, None, {}))
        self.assertEqual(fabriques["Config Caisse Depart"](emp, "t@x.y", {"date_entree": "2026-11-10"}),
                         {"caisse": "Test Tech", "date_depart": "2026-11-10"})


class TestCablage(unittest.TestCase):

    def setUp(self):
        self.base = os.path.dirname(inspect.getfile(customization_app))

    def _lire(self, *chemin):
        with open(os.path.join(self.base, *chemin), encoding="utf-8") as fh:
            return fh.read()

    def test_hooks_et_patch(self):
        self.assertIn('"Employee": "public/js/employee_list_arrivee.js"', self._lire("hooks.py"))
        self.assertIn("customization_app.patches.arrivee_technicien", self._lire("patches.txt"))
        self.assertIn('"role": "Technicien"', self._lire("customize_erpnext", "page", "agenda_sous_traitant", "agenda_sous_traitant.json"))

    def test_la_page_agenda_est_reimportee_avec_le_role_technicien(self):
        """18/11/2026 : le premier technicien créé (Radhouan) recevait « Pas permis » sur « Mon agenda ». Le rôle
        avait été ajouté au fichier sans changer sa date `modified` : Frappe ne réimporte une page que si cette
        date diffère de celle en base, la prod gardait donc l'ancienne liste de rôles."""
        import json
        page = json.loads(self._lire("customize_erpnext", "page", "agenda_sous_traitant", "agenda_sous_traitant.json"))
        self.assertIn("Technicien", [r["role"] for r in page["roles"]])
        self.assertGreater(page["modified"], "2026-11-10")      # date de l'ajout du rôle : la page doit être plus récente

    def test_accueil_du_technicien_ses_quatre_liens_seulement(self):
        """18/11/2026 : « une page d'accueil pour lui (mode simple technicien), dans laquelle il peut voir les
        listes — client, article, facture — seulement », puis « ajoute aussi commandes client »."""
        import json
        import re
        page = json.loads(self._lire("customize_erpnext", "page", "accueil_technicien", "accueil_technicien.json"))
        self.assertEqual((page["name"], page["title"]), ("accueil-technicien", "Accueil"))
        # le technicien, pas le sous-traitant (qui ne doit voir que SES choses)
        self.assertEqual(sorted(r["role"] for r in page["roles"]), ["System Manager", "Technicien"])
        js = self._lire("customize_erpnext", "page", "accueil_technicien", "accueil_technicien.js")
        self.assertIn('frappe.pages["accueil-technicien"].on_page_load', js)
        self.assertEqual(re.findall(r"route: (\[[^\]]+\])", js), ['["List", "Customer"]', '["articles-technicien"]',
                                                                 '["List", "Sales Order"]', '["List", "Sales Invoice"]'])
        self.assertIn("frappe.set_route(...l.route)", js)
        self.assertNotIn("frappe.call", js)                      # aucun droit accordé ici : des liens, rien d'autre

    def test_la_barre_du_technicien_mene_a_son_accueil(self):
        nav = self._lire("public", "js", "sous_traitant.js")
        self.assertIn('const TECHNICIEN = st.sous_traitant === false;', nav)
        self.assertIn('const FOND = TECHNICIEN ? ["accueil-technicien"] : AGENDA;', nav)        # sous-traitant : l'agenda, comme avant
        self.assertIn('ECRANS.unshift({ cle: "accueil"', nav)
        self.assertIn('new Set(["Customer", "Sales Order", "Sales Invoice"])', nav)             # « Accueil » reste allumé dans ses listes
        self.assertIn('r[0] === "accueil-technicien" || r[0] === "articles-technicien"', nav)
        self.assertIn('libelle: __("Mon agenda"),  aller: () => frappe.set_route(...AGENDA)', nav)
        self.assertIn('if (r[0] === "tache-de-travail" && r[1] !== "Form") { frappe.set_route(...AGENDA); return; }', nav)

    def test_le_logo_noir_ramene_toujours_a_la_page_de_fond(self):
        """18/11/2026 : « lorsqu'il appuie sur le truc noir, il revient à l'accueil ». Avant, seule la première
        arrivée était redirigée : le logo menait ensuite à un espace de travail du Desk."""
        nav = self._lire("public", "js", "sous_traitant.js")
        self.assertNotIn("if (deja) return;", nav)
        self.assertIn("const premiere = !deja;", nav)
        self.assertIn("if (premiere) setTimeout(() => { if (window.maJournee_ouvrir) window.maJournee_ouvrir(); }, 900);", nav)

    def test_une_fiche_ouverte_dans_un_nouvel_onglet_n_est_pas_prise_pour_l_accueil(self):
        """18/11/2026 : « lorsqu'il ouvre la tâche, il y a Ma journée qui s'ouvre ». Au chargement d'un lien direct
        la route est encore vide ; elle était prise pour l'accueil. Tant qu'elle est vide, l'adresse fait foi."""
        nav = self._lire("public", "js", "sous_traitant.js")
        self.assertIn("function sur_l_accueil(r) {", nav)
        self.assertIn("if (!sur_l_accueil(r)) return;", nav)
        bloc = nav.split("function sur_l_accueil(r) {", 1)[1].split("function accueillir() {", 1)[0]
        self.assertIn('if (tete !== "") return true;', bloc)
        self.assertIn("window.location.pathname", bloc)
        self.assertIn('chemin === "/app"', bloc)

    def test_un_clic_dans_l_agenda_ouvre_ma_journee_sur_la_tache(self):
        """18/11/2026 : « lorsque je clique sur un entretien de mon agenda en simplifié, elle m'ouvre Ma journée
        centrée sur la tâche sur laquelle j'ai cliqué, avec la tâche en gris »."""
        agenda = self._lire("customize_erpnext", "page", "agenda_sous_traitant", "agenda_sous_traitant.js")
        self.assertIn('window.maJournee_ouvrir($(e.currentTarget).data("journee"), $(e.currentTarget).attr("data-tache") || null)', agenda)
        self.assertIn('class="ag-tache ${this._classe(t)}" data-journee="${esc(this.jour)}" data-tache=', agenda)   # vue jour
        self.assertIn('class="ag-sem-ligne ${this._classe(t)}" data-journee="${j}" data-tache=', agenda)            # vue semaine
        journee = self._lire("public", "js", "ma_journee.js")
        self.assertIn("window.maJournee_ouvrir = (jour, tache) => new Journee(jour, tache);", journee)
        self.assertIn('this.cible = (typeof tache === "string" && tache) ? tache : null;', journee)   # un clic de bouton passe un événement
        self.assertIn('this.cible === l.tache ? "mj-cible" : ""', journee)
        self.assertIn('scrollIntoView({ block: "center"', journee)
        self.assertIn("if (!this.cible || this.cible_montree) return;", journee)                      # une seule fois
        self.assertIn(".mj-carte.mj-cible, .mj-carte.faite.mj-cible { background:#e9ecf0;", journee)   # en gris
        # « Dès que je scroll, ça s'enlève » : le gris part au premier défilement de sa main, et ne revient pas.
        self.assertIn('["wheel", "touchmove", "scroll"].forEach((ev) => racine.addEventListener(ev, oter, { capture: true, passive: true }));', journee)
        self.assertIn('this.$corps.find(".mj-carte.mj-cible").removeClass("mj-cible");', journee)
        self.assertIn("this.cible = null;", journee)

    def test_la_liste_d_articles_du_technicien(self):
        """18/11/2026 : « barre de recherche (nom, code) ; l'image avec le code, le nom, la description et le prix
        en Vente standard »."""
        import json
        from customization_app import accueil_technicien as A
        self.assertEqual(A.LISTE_PRIX, "Vente standard")
        self.assertEqual(A.mots("  pompe   QB60 "), ["pompe", "QB60"])
        self.assertEqual(A.mots(None), [])
        # la description : texte d'une ligne, sans balises, coupée sur un mot
        self.assertEqual(A.texte_brut("<div><p>Luxe&nbsp;Inox</p><p>Ø 63&nbsp;mm</p></div>"), "Luxe Inox Ø 63 mm")
        long = A.texte_brut("mot " * 100, 40)
        self.assertTrue(long.endswith("…") and len(long) <= 41 and not long.endswith(" …"))
        self.assertEqual(A.texte_brut(None), "")
        # le prix : celui en vigueur (l'ancien, périmé, est écarté), jamais un prix réservé à un client
        prix = [{"price_list_rate": 425, "uom": "Pièce", "valid_from": "2025-09-01", "valid_upto": "2026-08-29"},
                {"price_list_rate": 455, "uom": "Pièce", "valid_from": "2026-08-30", "valid_upto": None}]
        self.assertEqual(A.prix_du_jour(prix, "Pièce", "2026-11-18"), 455.0)
        self.assertEqual(A.prix_du_jour(prix, "Pièce", "2026-08-01"), 425.0)
        self.assertEqual(A.prix_du_jour([{"price_list_rate": 10, "customer": "X"}], "Pièce", "2026-11-18"), None)
        self.assertEqual(A.prix_du_jour([{"price_list_rate": 90, "uom": "Carton"}, {"price_list_rate": 10, "uom": "Pièce"}],
                                        "Pièce", "2026-11-18"), 10.0)                                   # l'unité de stock d'abord
        self.assertEqual(A.prix_du_jour([], "Pièce", "2026-11-18"), None)
        import inspect
        source = inspect.getsource(A.articles)
        self.assertIn('frappe.has_permission("Item", "read", throw=True)', source)
        for filtre in ("i.disabled = 0", "i.has_variants = 0", "i.is_sales_item = 1"):
            self.assertIn(filtre, source)
        page = json.loads(self._lire("customize_erpnext", "page", "articles_technicien", "articles_technicien.json"))
        self.assertEqual((page["name"], sorted(r["role"] for r in page["roles"])), ("articles-technicien", ["System Manager", "Technicien"]))
        js = self._lire("customize_erpnext", "page", "articles_technicien", "articles_technicien.js")
        for attendu in ('"customization_app.accueil_technicien.articles"', 'type="search"', "art-code", "art-desc", "art-prix",
                        'frappe.pages["articles-technicien"].on_page_load'):
            self.assertIn(attendu, js)
        self.assertNotIn("set_route", js)                         # lecture seule : aucune fiche à ouvrir

    def test_la_caisse_du_technicien_sans_les_suivis_ni_les_boutons_du_bureau(self):
        """18/11/2026 : « pour les employés avec la fonction technicien, ne montre pas [les suivis des dépenses
        facturées et des achats fournisseurs] dans la caisse, et ne montre pas ces boutons »."""
        js = self._lire("customize_erpnext", "page", "caisse_journaliere", "caisse_journaliere.js")
        self.assertIn("else if (simple && simple.sous_traitant === false) this._ecran_technicien();", js)
        bloc = js.split("_ecran_technicien() {", 1)[1].split("}", 1)[0]
        for retire in ("#rcj-suivi-depenses", "#rcj-suivi-achats", "#rcj-btn-impayes", "#rcj-btn-dettes", "#rcj-btn-aramex",
                       "#rcj-btn-bordereaux", "#rcj-btn-etat"):
            self.assertIn(retire, bloc)
        for garde in ("#rcj-btn-depense", "#rcj-btn-cloture"):    # ce qui est à lui : sa dépense, compter et remettre
            self.assertNotIn(garde, bloc)

    def test_bouton(self):
        js = self._lire("public", "js", "employee_list_arrivee.js")
        for morceau in ("arrivee_employe.creer_technicien", "options_arrivee", "ecran_simplifie", "premier_jour_recup",
                        "domicile_lien", "leave_approver", "calendrier_visible_par", "voit_calendrier_de", "MultiCheck"):
            self.assertIn(morceau, js, morceau)

    def test_visibilite_par_permissions_utilisateur(self):
        """Qui voit le calendrier de qui = User Permission sur Employé (mécanisme standard déjà en usage) :
        pas de table maison. Le départ retire le partant de ces listes."""
        from customization_app import depart_employe as D
        src = inspect.getsource(A.creer_technicien)
        self.assertIn("_permission_employe", src)
        self.assertIn("holiday_list", src)
        self.assertIn('"doctype": "User Permission"', inspect.getsource(A._permission_employe))
        self.assertIn("_retirer_permissions_utilisateur", inspect.getsource(D.executer))
        self.assertIn("Request Leave,Demander un congé", self._lire("translations", "fr.csv"))
        self.assertIn("Leave Allocation", src)

    def test_couleur_du_calendrier(self):
        """La couleur se choisit à l'arrivée et vaut pour tout employé : la fiche passe avant le barème."""
        from customization_app import api, sous_traitant as ST
        self.assertIn("custom_couleur", inspect.getsource(A.creer_technicien))
        self.assertIn("couleur_libre", inspect.getsource(A.options_arrivee))
        src = inspect.getsource(api.compute_tache_color)
        self.assertLess(src.index("couleur_calendrier(employe)"), src.index("STAFF_COLORS.get(employe"))
        self.assertNotIn("if employe not in STAFF_COLORS", src)
        self.assertIn("couleur_par_defaut(employe) if cint(r.get(CHAMP_FLAG)) else None", inspect.getsource(ST.couleur_calendrier))
        js = self._lire("public", "js", "employee_list_arrivee.js")
        self.assertIn('fieldtype: "Color", fieldname: "couleur"', js)
        self.assertIn('"new-password"', js)
        self.assertIn("customization_app.patches.couleur_calendrier_employe", self._lire("patches.txt"))

    def test_ecran_rh(self):
        """« Congés / Paie » : notre page, dans la barre du téléphone — on en revient d'un pouce."""
        js = self._lire("public", "js", "sous_traitant.js")
        self.assertIn('frappe.set_route("rh-technicien")', js)
        self.assertNotIn("/hrms", js)
        page = self._lire("customize_erpnext", "page", "rh_technicien", "rh_technicien.js")
        self.assertIn('const API = "customization_app.rh_technicien"', page)
        for m in ('".etat"', '".demander_conge"', '".annuler_demande"', "Mes fiches de paie", "Demander un congé"):
            self.assertIn(m, page, m)
        self.assertIn('"role": "Employee"', self._lire("customize_erpnext", "page", "rh_technicien", "rh_technicien.json"))

    def test_ecran_simplifie_decouple_de_l_argent(self):
        """Les droits et la caisse ne regardent que le sous-traitant ; l'écran regarde les deux."""
        from customization_app import sous_traitant as ST
        for f in (ST.permission_query_conditions_tache, ST.has_permission_tache, ST.permission_query_conditions_verification,
                  ST.has_permission_verification, ST.permission_query_conditions_cloture_caisse, ST.has_permission_cloture_caisse):
            self.assertIn("sous_traitant_courant(user)", inspect.getsource(f), f.__name__)
        self.assertIn("CHAMP_ECRAN", inspect.getsource(ST.employe_courant))
        self.assertNotIn("CHAMP_ECRAN", inspect.getsource(ST.sous_traitant_courant))
        self.assertIn("sous_traitant_courant()", self._lire("caisse_cloture.py"))
        self.assertIn("frappe.boot.ecran_simplifie || frappe.boot.sous_traitant", self._lire("public", "js", "sous_traitant.js"))
