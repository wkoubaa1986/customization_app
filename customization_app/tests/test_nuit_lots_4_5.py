"""Passage de nuit, lots 4 et 5 du 26/11/2026 — règles pures (dates fixes, aucune base sauf `frappe._dict`).

- lot 4 : annuler au lieu de supprimer (texte du commentaire), clients génériques (PASSAGER) ;
- lot 5 : familles soldées par un achat (main-d'œuvre d'abord, une seule famille par cartouche, la suivante si la
  première n'a rien à solder), porte-filtre soldé avec l'osmoseur qu'il protège, avance limitée sans main-d'œuvre,
  achat antérieur à la machine."""
from __future__ import annotations

import datetime
import types
import unittest
from unittest import mock

import frappe

from customization_app import relances_config as RC
from customization_app.Maintenance import update_schedule as U

FAMS = [{"code": f["code"], "libelle_sms": f["libelle_sms"], "article_entretien": f["article_entretien"], "article_type": f["article_type"],
         "groupes_machines": RC.liste(f["groupes_machines"]), "groupes_consommables": RC.liste(f["groupes_consommables"])}
        for f in RC.DEFAUT_FAMILLES]

CHARBON = {"item_code": "C-10'-CTO", "item_name": "Cartouche charbon 10", "item_group": "Cartouches à charbon"}
SEDIMENT = {"item_code": "C-10'-PP-5m", "item_name": "Cartouche PP 5 microns", "item_group": "Cartouches anti-sédiment"}
LAVABLE = {"item_code": "C-LAV", "item_name": "Cartouche lavable", "item_group": "Cartouches lavables"}
SEL = {"item_code": "S-S25", "item_name": "Sel adoucisseur 25 kg", "item_group": "Consommables & Accessoires"}
M_E_OD = {"item_code": "M-E-OD", "item_name": "Entretien Osmoseur Domestique", "item_group": "Main d’œuvre"}
M_E_AD = {"item_code": "M-E-Ad", "item_name": "Entretien Adoucisseur", "item_group": "Main d’œuvre"}


def _d(s):
    return datetime.date.fromisoformat(s)


def _ligne(code, date, actual=None, so=None, statut="Pending", idx=0):
    return frappe._dict(item_code=code, item_name=code, scheduled_date=_d(date), actual_date=_d(actual) if actual else None,
                        custom_sales_order=so, completion_status=statut, idx=idx, sales_person="X")


class TestAnnulation(unittest.TestCase):
    def test_texte_du_commentaire(self):
        t = U.texte_annulation("MAT-1", "client professionnel", "2026-11-26", remplace_par="MAT-2", commandes=["SO-1", "", None])
        self.assertIn("MAT-1 annulé le 26/11/2026", t)
        self.assertIn("pas supprimé", t)
        self.assertIn("client professionnel", t)
        self.assertIn("Remplacé par l’échéancier MAT-2", t)
        self.assertIn("Commande(s) : SO-1.", t)
        sans = U.texte_annulation("MAT-1", "B2B", "2026-11-26")
        self.assertIn("Aucun échéancier ne le remplace", sans)
        self.assertNotIn("Commande(s)", sans)

    def test_le_commentaire_dit_ou_l_historique_reste_lisible(self):
        # Relecture du 26/11/2026 : le panneau « Historique client » ne lit que les échéanciers soumis.
        self.assertIn("lisibles sur l’échéancier annulé", U.texte_annulation("MAT-1", "B2B", "2026-11-26"))

    def test_le_motif_b2b_porte_la_marque_relue_par_la_couverture(self):
        motif = U.MOTIF_B2B.format("Compte Pro")
        self.assertTrue(motif.startswith(U.MARQUE_B2B))
        self.assertIn(U.MARQUE_B2B, U.texte_annulation("MAT-1", motif, "2026-11-26"))
        self.assertNotIn("%", U.MARQUE_B2B)
        self.assertNotIn("_", U.MARQUE_B2B)                                     # pas de joker LIKE

    def test_devinette_sans_histoire(self):
        # Toute devinette de la nuit solde sa 1re visite à sa naissance, par sa commande d'origine : ce n'est pas une
        # histoire propre (599 devinettes sur la copie, toutes avec cette visite ; le retrait ne s'exécutait jamais).
        naissance = _ligne("AP", "2026-08-10", actual="2026-08-10", so="SO-C", statut="Fully Completed", idx=1)
        a_venir = _ligne("AP", "2027-02-10", idx=2)
        self.assertTrue(U.devinette_sans_histoire([naissance, a_venir], "SO-C"))
        self.assertTrue(U.devinette_sans_histoire([a_venir], "SO-C"))
        self.assertTrue(U.devinette_sans_histoire([], "SO-C"))
        # Une visite faite par une autre commande, ou à la main, la garde.
        autre = _ligne("AP", "2027-02-10", actual="2027-02-01", so="SO-E", statut="Fully Completed", idx=2)
        self.assertFalse(U.devinette_sans_histoire([naissance, autre], "SO-C"))
        main = _ligne("AP", "2027-02-10", actual="2027-02-01", so=None, statut="Fully Completed", idx=2)
        self.assertFalse(U.devinette_sans_histoire([naissance, main], "SO-C"))
        sans_date = _ligne("AP", "2027-02-10", statut="Fully Completed", idx=2)
        self.assertFalse(U.devinette_sans_histoire([naissance, sans_date], "SO-C"))
        # Sans commande d'origine (ligne machine sans lien) : toute visite faite garde.
        self.assertFalse(U.devinette_sans_histoire([naissance], ""))
        self.assertTrue(U.devinette_sans_histoire([a_venir], None))
        self.assertTrue(U.devinette_sans_histoire([naissance], " SO-C "))       # espaces ignorés

    def test_texte_de_reactivation(self):
        oui = U.texte_reactivation(False, False)
        self.assertEqual(oui, "✅ Relance réactivée : achat lié à l’entretien.")
        self.assertEqual(U.texte_reactivation(False, True), oui)
        non = U.texte_reactivation(True, False)
        self.assertIn("reste « Non »", non)
        self.assertIn("toujours ni SMS ni appel", non)
        # « Relancer les non intéressés » coché : il est de nouveau relancé, le commentaire ne doit pas dire le contraire.
        coche = U.texte_reactivation(True, True)
        self.assertIn("reste « Non »", coche)
        self.assertNotIn("ni SMS ni appel", coche)
        self.assertIn("peut de nouveau être relancé", coche)
        for t in (oui, non, coche):
            self.assertTrue(t.startswith("✅"), t)                                # relu par l'historique client

    def test_creation_permise_apres_pas_de_systeme(self):
        # Réponse « pas de système » le 20/11 (échéancier supprimé par l'ancien Server Script) : ce qui a été livré
        # jusque-là ne recrée rien ; une machine livrée ensuite est une nouvelle machine.
        self.assertFalse(U.creation_permise("2026-09-01", "2026-11-20"))
        self.assertFalse(U.creation_permise("2026-11-20", _d("2026-11-20")))      # le jour même : la réponse fait foi
        self.assertTrue(U.creation_permise("2026-11-21", "2026-11-20"))
        self.assertTrue(U.creation_permise("2026-09-01", None))                     # aucune réponse : comme toujours

    def test_clients_generiques(self):
        self.assertEqual(U.clients_generiques({}), ["PASSAGER"])
        self.assertEqual(U.clients_generiques({"clients_generiques": "PASSAGER\nCOMPTOIR"}), ["PASSAGER", "COMPTOIR"])
        self.assertEqual(U.clients_generiques({"clients_generiques": " "}), ["PASSAGER"])     # vide = défaut, comme config()


class TestAvance(unittest.TestCase):
    def test_avance_permise(self):
        self.assertTrue(U.avance_permise("2027-04-20", "2026-11-03", None))       # avec main-d'œuvre : comme avant
        self.assertTrue(U.avance_permise("2026-10-01", "2026-11-03", 60))         # échue
        self.assertTrue(U.avance_permise("2026-11-03", "2026-11-03", 60))         # due le jour même
        self.assertTrue(U.avance_permise("2027-01-02", "2026-11-03", 60))         # dans 60 j
        self.assertFalse(U.avance_permise("2027-01-03", "2026-11-03", 60))        # dans 61 j
        self.assertFalse(U.avance_permise("2027-04-20", "2026-11-03", 60))        # MAT-MSH-2024-00169 : 168 j d'avance
        self.assertFalse(U.avance_permise("2026-11-04", "2026-11-03", 0))         # 0 : seulement une visite échue

    def test_reglage(self):
        self.assertEqual(U.jours_avance_sans_main_oeuvre({}), 60)
        self.assertEqual(U.jours_avance_sans_main_oeuvre({"jours_avance_sans_main_oeuvre": 30}), 30)
        self.assertEqual(U.jours_avance_sans_main_oeuvre({"jours_avance_sans_main_oeuvre": ""}), 60)
        self.assertEqual(U.jours_avance_sans_main_oeuvre({"jours_avance_sans_main_oeuvre": -5}), 0)

    def test_main_oeuvre_d_entretien(self):
        for code in ("M-E-OD", "M-E-Ad", "M-E-OC", "M-E-OI", "M-E-PF"):
            self.assertTrue(U.est_main_oeuvre_entretien(code, FAMS), code)
        for code in ("M-I-OD", "M-R-OC", "C-10'-CTO", "", None):
            self.assertFalse(U.est_main_oeuvre_entretien(code, FAMS), code)
        self.assertTrue(U.commande_avec_main_oeuvre([CHARBON, M_E_OD], FAMS))
        self.assertFalse(U.commande_avec_main_oeuvre([CHARBON, {"item_code": "M-I-OD"}], FAMS))
        self.assertFalse(U.commande_avec_main_oeuvre([], FAMS))

    def test_achat_anterieur_a_la_machine(self):
        ms = types.SimpleNamespace(transaction_date=_d("2026-09-04"), items=[types.SimpleNamespace(start_date=_d("2026-09-04"))])
        self.assertEqual(U.debut_echeancier(ms), _d("2026-09-04"))
        self.assertTrue(U.achat_anterieur("2026-08-27", ms))                      # Laboratoire SAMI ABID, WEB1-008144
        self.assertFalse(U.achat_anterieur("2026-09-04", ms))                     # le même jour que la machine : non
        self.assertFalse(U.achat_anterieur("2026-11-17", ms))
        vide = types.SimpleNamespace(transaction_date=None, items=[])
        self.assertIsNone(U.debut_echeancier(vide))
        self.assertFalse(U.achat_anterieur("2020-01-01", vide))                   # sans date : on ne sait pas, on garde


class TestFamillesASolder(unittest.TestCase):
    def test_porte_filtre_protege_les_osmoseurs(self):
        for f in ("RO_DOM", "RO_COM", "RO_IND", "BIO", "FONTAINE"):
            self.assertTrue(U.pf_protege(f, FAMS), f)
        for f in ("ADOUCISSEUR", "UV", "PF", "POMPE", None, "INCONNUE"):
            self.assertFalse(U.pf_protege(f, FAMS), f)

    def test_la_main_d_oeuvre_designe_la_famille(self):
        # MAT-MSH-2023-00048 : M-E-Ad + cartouches PP sur osmoseur + adoucisseur — avant, l'osmoseur (1er du réglage).
        self.assertEqual(U.familles_a_solder([SEDIMENT, M_E_AD], {"RO_DOM", "ADOUCISSEUR"}, FAMS),
                         [("ADOUCISSEUR", "main-d'œuvre"), ("RO_DOM", "cartouche")])
        self.assertEqual(U.familles_a_solder([M_E_AD], {"RO_DOM", "ADOUCISSEUR"}, FAMS), [("ADOUCISSEUR", "main-d'œuvre")])

    def test_une_cartouche_ne_solde_qu_une_famille(self):
        self.assertEqual(U.familles_a_solder([CHARBON], {"RO_DOM", "RO_COM", "PF"}, FAMS), [("RO_DOM", "cartouche")])
        # L'entretien de l'osmoseur domestique et ses cartouches ne soldent pas aussi le commercial.
        self.assertEqual(U.familles_a_solder([M_E_OD, CHARBON, SEDIMENT], {"RO_DOM", "RO_COM"}, FAMS), [("RO_DOM", "main-d'œuvre")])
        # Deux cartouches différentes de la même commande : la même machine, une seule famille.
        self.assertEqual(U.familles_a_solder([CHARBON, SEDIMENT], {"RO_DOM", "RO_COM"}, FAMS), [("RO_DOM", "cartouche")])

    def test_la_cartouche_va_a_la_famille_suivante_si_la_premiere_n_a_rien(self):
        # MAT-MSH-2023-00469 : l'osmoseur domestique n'a plus de visite ouverte, la cartouche revient au commercial.
        appels = []
        essayer = lambda fam: appels.append(fam) or fam == "RO_COM"
        self.assertEqual(U.familles_a_solder([CHARBON, SEDIMENT], {"RO_DOM", "RO_COM"}, FAMS, essayer), [("RO_COM", "cartouche")])
        self.assertEqual(appels, ["RO_DOM", "RO_COM"])                            # chaque famille essayée une fois

    def test_une_famille_sure_absente_n_explique_pas_la_cartouche(self):
        # M-E-OD chez un client qui n'a qu'un osmoseur commercial : la cartouche solde le commercial.
        self.assertEqual(U.familles_a_solder([M_E_OD, CHARBON], {"RO_COM"}, FAMS), [("RO_COM", "cartouche")])
        # Le sel ne concerne que l'adoucisseur ; la main-d'œuvre explique les cartouches même si elle n'a rien soldé.
        self.assertEqual(U.familles_a_solder([SEL], {"ADOUCISSEUR", "RO_DOM"}, FAMS), [("ADOUCISSEUR", "consommable")])
        self.assertEqual(U.familles_a_solder([M_E_OD, CHARBON], {"RO_DOM", "RO_COM"}, FAMS, lambda fam: False), [])

    def test_porte_filtre_seul_et_cartouche_propre_au_porte_filtre(self):
        self.assertEqual(U.familles_a_solder([CHARBON], {"PF"}, FAMS), [("PF", "cartouche")])
        # Une cartouche lavable (porte-filtre seulement) n'« explique » pas la cartouche charbon : l'osmoseur aussi.
        self.assertEqual(U.familles_a_solder([LAVABLE, CHARBON], {"RO_DOM", "PF"}, FAMS),
                         [("PF", "consommable"), ("RO_DOM", "cartouche")])
        self.assertEqual(U.familles_a_solder([CHARBON], set(), FAMS), [])
        self.assertEqual(U.plan_des_familles([CHARBON, SEDIMENT], {"RO_DOM", "PF"}, FAMS),
                         {"sures": [], "cartouches": [["RO_DOM", "PF"]]})


class TestDecalageLots45(unittest.TestCase):
    """shift_schedule_for_delivery sur un échéancier en mémoire (osmoseur AP + porte-filtre PF, adoucisseur AD)."""

    FAMILLE = {"AP": "RO_DOM", "PF": "PF", "AD": "ADOUCISSEUR"}

    def setUp(self):
        self.mocks = [mock.patch.object(U, "_famille_de_la_ligne", lambda ms, code, fams=None, cache=None: self.FAMILLE.get(code)),
                      mock.patch.object(U, "_reactiver_client", lambda customer: None)]
        for m in self.mocks:
            m.start()

    def tearDown(self):
        for m in self.mocks:
            m.stop()

    def _ms(self, lignes):
        saved, traces = [], []
        ms = types.SimpleNamespace(name="MS-T", customer="C", flags=frappe._dict(), schedules=lignes,
                                   items=[frappe._dict(item_code=c, periodicity="Half Yearly") for c in {l.item_code for l in lignes}],
                                   save=lambda: saved.append(True), append=lambda champ, v: lignes.append(frappe._dict(v)),
                                   add_comment=lambda t, texte: traces.append(texte))
        return ms, saved, traces

    def test_ligne_a_marquer_juge_deja_applique_par_famille(self):
        lignes = [_ligne("AP", "2026-09-01", actual="2026-09-04", so="SO-1", statut="Fully Completed"), _ligne("PF", "2026-09-01")]
        fam = self.FAMILLE.get
        self.assertIsNone(U.ligne_a_marquer(lignes, "2026-09-04", "RO_DOM", fam, "SO-1"))              # déjà appliquée à l'osmoseur
        self.assertEqual(U.ligne_a_marquer(lignes, "2026-09-04", "PF", fam, "SO-1").item_code, "PF")   # pas au porte-filtre
        self.assertIsNone(U.ligne_a_marquer(lignes, "2026-09-04", None, fam, "SO-1"))                  # sans famille : tout l'échéancier

    def test_le_porte_filtre_est_solde_avec_l_osmoseur(self):
        lignes = [_ligne("AP", "2026-09-01", idx=1), _ligne("AP", "2027-03-01", idx=2),
                  _ligne("PF", "2026-09-01", idx=3), _ligne("PF", "2027-03-01", idx=4)]
        ms, saved, traces = self._ms(lignes)
        stats = {}
        self.assertTrue(U.shift_schedule_for_delivery(ms, "2026-11-17", "SO-9", "RO_DOM", FAMS, {}, aujourd_hui="2026-11-26",
                                                      jours_avance=None, stats=stats))
        faites = [(l.item_code, str(l.actual_date), l.custom_sales_order) for l in lignes if l.actual_date]
        self.assertEqual(faites, [("AP", "2026-11-17", "SO-9"), ("PF", "2026-11-17", "SO-9")])          # même date, même commande
        self.assertEqual([str(l.scheduled_date) for l in lignes if not l.actual_date], ["2027-05-17", "2027-05-17"])  # +77 j chacune
        self.assertEqual(stats, {"porte_filtres_avec_machine": 1})
        self.assertEqual(len(saved), 1)                                                                # un seul enregistrement
        self.assertEqual(len(traces), 2)
        self.assertTrue(traces[0].startswith("📅 Visite AP du 01/09/2026 réalisée par SO-9 (livraison du 17/11/2026)"))
        self.assertTrue(traces[1].endswith(U.NOTE_PORTE_FILTRE))
        self.assertFalse(U.shift_schedule_for_delivery(ms, "2026-11-17", "SO-9", "RO_DOM", FAMS, {}, aujourd_hui="2026-11-26"))  # rejoué : rien

    def test_l_adoucisseur_n_emporte_pas_le_porte_filtre(self):
        lignes = [_ligne("AD", "2026-09-01", idx=1), _ligne("AD", "2027-03-01", idx=2), _ligne("PF", "2026-09-01", idx=3),
                  _ligne("PF", "2027-03-01", idx=4)]
        ms, _saved, _traces = self._ms(lignes)
        self.assertTrue(U.shift_schedule_for_delivery(ms, "2026-11-17", "SO-9", "ADOUCISSEUR", FAMS, {}, aujourd_hui="2026-11-26"))
        self.assertEqual([l.item_code for l in lignes if l.actual_date], ["AD"])

    def test_sans_main_d_oeuvre_une_visite_lointaine_n_est_pas_consommee(self):
        # Une commande de cartouches 9 j après l'entretien consommait la visite d'avril 2027 (analyse du 25/11/2026).
        lignes = [_ligne("AP", "2026-11-01", actual="2026-11-01", so="SO-E", statut="Fully Completed", idx=1),
                  _ligne("AP", "2027-04-20", idx=2), _ligne("AP", "2027-10-20", idx=3)]
        ms, saved, traces = self._ms(lignes)
        stats = {}
        self.assertFalse(U.shift_schedule_for_delivery(ms, "2026-11-10", "SO-C", "RO_DOM", FAMS, {}, aujourd_hui="2026-11-26",
                                                       jours_avance=60, stats=stats))
        self.assertEqual(stats, {"avances_evitees": 1})
        self.assertEqual((saved, traces), ([], []))
        self.assertEqual([str(l.scheduled_date) for l in lignes], ["2026-11-01", "2027-04-20", "2027-10-20"])   # rien n'a bougé
        # Due dans 60 j : consommée, comme avant.
        self.assertTrue(U.shift_schedule_for_delivery(ms, "2027-03-01", "SO-D", "RO_DOM", FAMS, {}, aujourd_hui="2026-11-26",
                                                      jours_avance=60, stats=stats))
        self.assertEqual(str(lignes[1].actual_date), "2027-03-01")

    def test_sans_main_d_oeuvre_la_visite_echue_est_consommee_mais_pas_le_porte_filtre_lointain(self):
        lignes = [_ligne("AP", "2026-09-01", idx=1), _ligne("AP", "2027-03-01", idx=2), _ligne("PF", "2027-06-01", idx=3)]
        ms, _saved, _traces = self._ms(lignes)
        stats = {}
        self.assertTrue(U.shift_schedule_for_delivery(ms, "2026-11-17", "SO-C", "RO_DOM", FAMS, {}, aujourd_hui="2026-11-26",
                                                      jours_avance=60, stats=stats))
        self.assertEqual([l.item_code for l in lignes if l.actual_date], ["AP"])
        self.assertEqual(stats, {})

    def test_trace_sans_note_inchangee(self):
        avant = U.texte_decalage("AP", "2026-09-01", "2026-11-17", "SO-9", 77, [])
        self.assertEqual(avant, "📅 Visite AP du 01/09/2026 réalisée par SO-9 (livraison du 17/11/2026).")
        self.assertEqual(U.texte_decalage("AP", "2026-09-01", "2026-11-17", "SO-9", 77, [], "Note."), avant + " Note.")


if __name__ == "__main__":
    unittest.main()
