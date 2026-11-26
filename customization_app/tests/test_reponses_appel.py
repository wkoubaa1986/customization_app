"""Réponses à l'appel (lot 3 + lot 5.1, 25/11/2026) — règles pures : « Fait lui-même » vaut un entretien fait le jour de
l'appel et réactive un « À requalifier », « Il va passer au magasin » reporte la relance de 15 j (sans réactiver : relecture
du 26/11/2026), une vieille visite échue est COUVERTE par un appel plus récent (au lieu d'être marquée, ce qui gonflait la
lassitude), chaque effet d'une ligne est isolé, et les réglages entiers survivent à un enregistrement du formulaire."""
from __future__ import annotations

import copy
import datetime
import inspect
import types
import unittest
from unittest import mock

import frappe

from customization_app import liste_appels as LA, relances_config as RC
from customization_app.Maintenance import update_schedule as U
from customization_app.patches import config_relances_reglages_entiers as P

D = datetime.date
JOUR = "2026-11-25"


def _v(date, actual=None, statut="Pending", idx=0, sms1=None, sms2=None, appel=None, item="A", nom=None):
    return frappe._dict(name=nom or "%s-%s" % (item, date), item_code=item, scheduled_date=D.fromisoformat(date), actual_date=actual,
                        completion_status=statut, idx=idx, custom_sms_1=sms1, custom_sms_2=sms2, custom_sms1_status="Success" if sms1 else None,
                        custom_sms2_status="Success" if sms2 else None, custom_appelle=appel, custom_1er_appel=None, custom_sales_order=None)


class TestEffetReponse(unittest.TestCase):
    def test_pas_de_systeme_et_rendez_vous_inchanges(self):
        self.assertEqual(LA.effet_reponse(LA.PAS_DE_SYSTEME, ""), {"custom_statut_relance": "Perdu"})
        self.assertEqual(LA.effet_reponse(LA.PAS_DE_SYSTEME, "À requalifier"), {"custom_statut_relance": "Perdu"})
        self.assertEqual(LA.effet_reponse(LA.PAS_DE_SYSTEME, "Perdu"), {})
        self.assertEqual(LA.effet_reponse(LA.RENDEZ_VOUS, "À requalifier"), {"custom_statut_relance": ""})
        self.assertEqual(LA.effet_reponse(LA.RENDEZ_VOUS, ""), {})

    def test_fait_lui_meme_reactive_s_il_a_solde(self):
        self.assertEqual(LA.effet_reponse(LA.FAIT_LUI_MEME, "À requalifier", visite_soldee=True), {"custom_statut_relance": ""})
        self.assertEqual(LA.effet_reponse(LA.FAIT_LUI_MEME, "", visite_soldee=True), {})          # déjà relancé : rien à écrire
        self.assertEqual(LA.effet_reponse(LA.FAIT_LUI_MEME, "Perdu", visite_soldee=True), {})     # décision humaine : ne bouge plus

    def test_magasin_ne_reactive_jamais(self):
        # Relecture du 26/11/2026 : le report ne protège que jusqu'au jour du rappel, la nuit de ce jour-là requalifiait
        # le client (15 sur 15 sur la copie) — la réactivation n'apportait qu'un aller-retour de commentaires.
        for statut in ("À requalifier", "", "Perdu"):
            self.assertEqual(LA.effet_reponse(LA.MAGASIN, statut), {}, statut)
            self.assertEqual(LA.effet_reponse(LA.MAGASIN, statut, visite_soldee=True), {}, statut)

    def test_sans_solde_pas_de_reactivation(self):
        # Sans visite soldée, ses cycles restent ouverts : la nuit le requalifierait aussitôt (va-et-vient de commentaires).
        # Le défaut vaut « rien soldé » (test_qualification_appels).
        self.assertEqual(LA.effet_reponse(LA.FAIT_LUI_MEME, "À requalifier"), {})
        self.assertEqual(LA.effet_reponse(LA.RENDEZ_VOUS, "À requalifier"), {"custom_statut_relance": ""})   # inchangé
        self.assertEqual(LA.effet_reponse(LA.PAS_DE_SYSTEME, "", visite_soldee=True), {"custom_statut_relance": "Perdu"})

    def test_autres_reponses_sans_effet(self):
        for issue in ("Autres reponses", LA.NE_REPOND_PAS, "Ne répond pas 2ieme appel", "Livraison prévue", LA.MAGASIN, ""):
            self.assertEqual(LA.effet_reponse(issue, "À requalifier", visite_soldee=True), {}, issue)


class TestReglages(unittest.TestCase):
    def test_defauts_tant_que_le_champ_n_existe_pas(self):
        self.assertEqual(LA.reglages({}), {"fait_lui_meme_solde": True, "jours_rappel_magasin": 15})
        self.assertEqual(LA.reglages({"fait_lui_meme_solde": 0, "jours_rappel_magasin": 30}),
                         {"fait_lui_meme_solde": False, "jours_rappel_magasin": 30})


class TestRappelMagasin(unittest.TestCase):
    def test_quinze_jours_apres_l_appel(self):
        self.assertEqual(LA.date_rappel_magasin(JOUR, None), D(2026, 12, 10))
        self.assertEqual(LA.date_rappel_magasin(JOUR, D(2026, 12, 1)), D(2026, 12, 10))      # report plus proche : repoussé
        self.assertEqual(LA.date_rappel_magasin(JOUR, None, 30), D(2026, 12, 25))

    def test_un_report_plus_lointain_reste(self):
        self.assertIsNone(LA.date_rappel_magasin(JOUR, D(2026, 12, 31)))                   # « rappeler fin décembre »
        self.assertIsNone(LA.date_rappel_magasin(JOUR, D(2026, 12, 10)))                   # même date : rien à écrire

    def test_zero_coupe_le_report(self):
        # Un report au jour même ferait rappeler le client dès la liste du lendemain.
        self.assertIsNone(LA.date_rappel_magasin(JOUR, None, 0))
        self.assertIsNone(LA.date_rappel_magasin(JOUR, None, -3))


class TestVisiteASolder(unittest.TestCase):
    def test_la_plus_ancienne_echue_non_faite(self):
        rows = [_v("2025-05-10", actual=D(2025, 5, 12), statut="Fully Completed", idx=1), _v("2025-11-10", idx=2),
                _v("2026-05-10", idx=3), _v("2026-12-10", idx=4)]
        self.assertEqual(LA.visite_a_solder(rows, JOUR).scheduled_date, D(2025, 11, 10))

    def test_jamais_une_visite_faite_ni_a_venir(self):
        fully = _v("2025-11-10", statut="Fully Completed")                    # soldée sans date réelle : faite quand même
        self.assertIsNone(LA.visite_a_solder([fully, _v("2026-12-10")], JOUR))
        self.assertIsNone(LA.visite_a_solder([], JOUR))
        self.assertEqual(LA.visite_a_solder([_v("2026-11-25")], JOUR).scheduled_date, D(2026, 11, 25))   # due ce jour


class TestSolderFaitLuiMeme(unittest.TestCase):
    def _rows(self):
        return [_v("2025-05-10", actual=D(2025, 5, 10), statut="Fully Completed", idx=1),
                _v("2025-11-10", idx=2, sms1=D(2025, 11, 11), sms2=D(2025, 11, 18), appel=D(2026, 1, 15)),
                _v("2026-05-10", idx=3, sms1=D(2026, 5, 11), sms2=D(2026, 5, 18)),
                _v("2026-11-10", idx=4, sms1=D(2026, 11, 11)),
                _v("2027-05-10", idx=5)]

    def test_solde_et_decale_comme_un_achat(self):
        rows = self._rows()
        res = LA.solder_fait_lui_meme(rows, JOUR)
        self.assertEqual((res["ancienne"], res["decalage"]), (D(2025, 11, 10), 380))
        ref = rows[1]
        self.assertIs(res["ref"], ref)
        self.assertEqual((ref.actual_date, ref.completion_status, ref.scheduled_date), (D(2026, 11, 25), "Fully Completed", D(2026, 11, 25)))
        self.assertEqual(ref.custom_appelle, D(2026, 1, 15))                    # date déjà posée conservée
        self.assertEqual(rows[0].scheduled_date, D(2025, 5, 10))                # visite faite : intouchée
        self.assertEqual([r.scheduled_date for r in rows[2:]],
                         [D(2026, 5, 10) + datetime.timedelta(380), D(2026, 11, 10) + datetime.timedelta(380), D(2027, 5, 10) + datetime.timedelta(380)])
        # Passées → à venir : visites neuves (SMS / appel effacés) ; la visite déjà à venir garde ses champs.
        self.assertEqual((rows[2].custom_sms_1, rows[2].custom_sms_2, rows[3].custom_sms_1), (None, None, None))
        self.assertEqual([len(e) for _a, _b, e in res["deplacees"]], [4, 2, 0])  # sms1+statut+sms2+statut, sms1+statut, rien
        self.assertEqual(res["deplacees"][0][2]["custom_sms_1"], D(2026, 5, 11))

    def test_rien_d_echu_rien_ne_bouge(self):
        rows = [_v("2025-05-10", actual=D(2025, 5, 10), statut="Fully Completed"), _v("2027-05-10", sms1=D(2027, 5, 11))]
        avant = copy.deepcopy(rows)
        self.assertIsNone(LA.solder_fait_lui_meme(rows, JOUR))
        self.assertEqual(rows, avant)

    def test_ref_sans_appel_recoit_la_date_de_l_appel(self):
        rows = [_v("2026-11-25")]
        res = LA.solder_fait_lui_meme(rows, JOUR)
        self.assertEqual((res["decalage"], rows[0].custom_appelle, res["deplacees"]), (0, D(2026, 11, 25), []))

    def test_meme_regle_que_shift_schedule_for_delivery(self):
        """Même décalage, mêmes visites remises à neuf qu'un achat livré le jour de l'appel (seule la commande diffère)."""
        base = [_v("2025-03-01", idx=1, sms1=D(2025, 3, 3), sms2=D(2025, 3, 10), appel=D(2025, 4, 1)),
                _v("2025-09-01", idx=2, sms1=D(2025, 9, 3), sms2=D(2025, 9, 10), appel=D(2025, 10, 1)),
                _v("2026-03-01", idx=3), _v("2026-09-01", actual=D(2026, 9, 1), statut="Fully Completed", idx=4), _v("2027-03-01", idx=5)]
        achat, appel = copy.deepcopy(base), copy.deepcopy(base)
        ms = types.SimpleNamespace(name="MS", customer="C", items=[frappe._dict(item_code="A", periodicity="Half Yearly")], schedules=achat,
                                   flags=frappe._dict(), save=lambda: None, append=lambda champ, v: achat.append(frappe._dict(v)))
        with mock.patch.object(U, "_famille_de_la_ligne", return_value="RO_DOM"), mock.patch.object(U, "_reactiver_client"), \
                mock.patch.object(U, "extend_schedule_for_sms", return_value=0), mock.patch.object(U, "log"):
            self.assertTrue(U.shift_schedule_for_delivery(ms, "2026-02-20", "SO-1", "RO_DOM", aujourd_hui="2026-02-20"))
        LA.solder_fait_lui_meme(appel, "2026-02-20")
        champs = ("scheduled_date", "actual_date", "completion_status", "custom_sms_1", "custom_sms_2", "custom_sms1_status",
                  "custom_sms2_status", "custom_appelle", "custom_1er_appel")
        self.assertEqual([[r.get(c) for c in champs] for r in appel], [[r.get(c) for c in champs] for r in achat])
        self.assertEqual(achat[0].custom_sales_order, "SO-1")
        self.assertIsNone(appel[0].custom_sales_order)                          # pas de commande derrière « Fait lui-même »


class TestTextes(unittest.TestCase):
    def test_trace_de_l_echeancier(self):
        t = LA.texte_fait_lui_meme("AP-M", D(2025, 11, 10), JOUR, "LIST-25-11-2026-00001", 380,
                                   [(D(2026, 5, 10), D(2027, 5, 25), {"custom_sms_1": D(2026, 5, 11), "custom_sms1_status": "Success"})])
        self.assertEqual(t, "🔧 Entretien fait par le client lui-même — appel du 25/11/2026, liste LIST-25-11-2026-00001 : visite AP-M "
                            "du 10/11/2025 soldée. 1 visite(s) suivante(s) décalée(s) de 380 j : 10/05/2026 → 25/05/2027 "
                            "(effacés : SMS1 11/05/2026 ✓).")
        self.assertTrue(LA.texte_fait_lui_meme("AP-M", D(2026, 11, 25), JOUR, "L", 0, []).endswith("soldée."))

    def test_temoin_client(self):
        self.assertEqual(LA.texte_client_fait_lui_meme(JOUR, "L-1", []),
                         "🔧 Entretien fait par le client lui-même — appel du 25/11/2026, liste L-1 : aucune visite échue à solder.")
        t = LA.texte_client_fait_lui_meme(JOUR, "L-1", [("MS-1", "A", D(2025, 11, 10)), ("MS-2", "A", D(2026, 1, 3))])
        self.assertIn("liste L-1 :", t)                                         # le témoin que _deja_traite recherche
        self.assertIn("MS-1 A du 10/11/2025 ; MS-2 A du 03/01/2026", t)

    def test_motif_like_echappe(self):
        self.assertEqual(LA._like("a%b_c"), "a\\%b\\_c")


class TestVisiteCouverte(unittest.TestCase):
    def test_couverte_par_un_appel_posterieur_a_son_echeance(self):
        v1, v2, v3 = _v("2025-05-10"), _v("2025-11-10", appel=D(2026, 1, 10)), _v("2026-05-10", sms1=D(2026, 5, 11), sms2=D(2026, 5, 18))
        rows = [v1, v2, v3]
        self.assertTrue(LA.visite_couverte(v1, rows))                           # due avant l'appel du 10/01 : couverte
        self.assertFalse(LA.visite_couverte(v3, rows))                          # due après : un cycle neuf, à appeler
        self.assertFalse(LA.visite_couverte(v2, rows))                          # elle-même appelée : pas « couverte »
        self.assertTrue(LA.visite_couverte(_v("2026-01-10"), [v2]))             # appel le jour même de l'échéance : couverte
        seule = _v("2026-01-11")
        self.assertFalse(LA.visite_couverte(seule, [seule, v1]))                # aucune autre visite appelée

    def test_ni_faite_ni_sans_date(self):
        faite = _v("2025-05-10", actual=D(2025, 5, 12), statut="Fully Completed")
        self.assertFalse(LA.visite_couverte(faite, [faite, _v("2025-11-10", appel=D(2026, 1, 10))]))
        sans = frappe._dict(scheduled_date=None, custom_appelle=None)
        self.assertFalse(LA.visite_couverte(sans, [_v("2025-11-10", appel=D(2026, 1, 10))]))
        self.assertFalse(LA.visite_couverte(_v("2025-05-10"), []))

    def test_clause_sql(self):
        s = " ".join(LA.SQL_VISITE_NON_COUVERTE.split())
        self.assertTrue(s.startswith("AND NOT EXISTS"))
        self.assertIn("w.parent = d.parent AND w.item_code = d.item_code", s)
        self.assertIn("w.custom_appelle >= d.scheduled_date", s)
        self.assertNotIn("%", s)                                                # se colle dans une requête à %(today)s


class TestClauseDansLesListes(unittest.TestCase):
    """Relecture du 26/11/2026 : le lot 5.1 n'a d'effet que si la clause est dans la VRAIE requête des listes. Ce test
    ÉCHOUE tant qu'elle n'y est pas collée (ajouts_ailleurs) : un oubli ne passe plus inaperçu."""

    def test_clause_collee_dans_echeances_a_appeler(self):
        from customization_app.Maintenance import creation_liste_appelle as CL
        self.assertTrue("SQL_VISITE_NON_COUVERTE" in inspect.getsource(CL.echeances_a_appeler),
                        "À intégrer : creation_liste_appelle.echeances_a_appeler doit ajouter liste_appels.SQL_VISITE_NON_COUVERTE "
                        "à la requête des visites candidates (lot 5.1), sinon les clients reviennent au bout de 60 j sur une vieille visite.")


class TestEffetsALaValidation(unittest.TestCase):
    """Relecture finale du 26/11/2026 : « Fait lui-même » et « magasin » ne jouent qu'à la VALIDATION de la liste — au
    brouillon, une réponse corrigée ensuite laissait une visite soldée et les suivantes repoussées à tort."""

    def _jouer(self, issue, docstatus):
        doc = frappe._dict(name="L-1", docstatus=docstatus, clients=[frappe._dict(
            client="C-1", a_été_appelé=1, resume_appel=issue, parent="L-1", detail_articles='{"A": "2026-05-10"}')])
        faux = {n: mock.Mock(return_value=None) for n in ("_cases_client", "_fait_lui_meme", "_appliquer_statut", "_marquer_visites", "_reporter_magasin")}
        patches = [mock.patch.object(LA, n, f) for n, f in faux.items()]
        patches += [mock.patch.object(LA, "reglages", return_value={"fait_lui_meme_solde": True, "jours_rappel_magasin": 15}),
                    mock.patch.object(LA, "nowdate", return_value=JOUR), mock.patch.object(frappe, "log_error")]
        for p in patches:
            p.start()
        try:
            LA.synchroniser_appels(doc)
            return faux
        finally:
            for p in reversed(patches):
                p.stop()

    def test_au_brouillon_rien_n_est_solde_ni_reporte(self):
        for issue, effet in ((LA.FAIT_LUI_MEME, "_fait_lui_meme"), (LA.MAGASIN, "_reporter_magasin")):
            faux = self._jouer(issue, 0)
            faux[effet].assert_not_called()
            faux["_marquer_visites"].assert_called_once()                  # la marque « appelé », elle, est posée
            faux = self._jouer(issue, 1)
            faux[effet].assert_called_once()


class TestEffetsIsoles(unittest.TestCase):
    """Relecture du 26/11/2026 : une erreur d'un effet (marquage, report) n'empêche plus les autres — avant, le statut
    était écrit après le marquage, et un client « pas de système » restait relancé si le marquage levait."""

    def _jouer(self, issue, en_panne):
        ligne = frappe._dict(client="C-1", a_été_appelé=1, resume_appel=issue, parent="L-1", detail_articles='{"A": "2026-05-10"}')
        faux = {n: mock.Mock(return_value=None) for n in ("_cases_client", "_fait_lui_meme", "_appliquer_statut", "_marquer_visites", "_reporter_magasin")}
        for n in en_panne:
            faux[n].side_effect = RuntimeError("panne simulée")
        patches = [mock.patch.object(LA, n, f) for n, f in faux.items()]
        patches += [mock.patch.object(LA, "reglages", return_value={"fait_lui_meme_solde": True, "jours_rappel_magasin": 15}),
                    mock.patch.object(LA, "nowdate", return_value=JOUR),
                    mock.patch.object(frappe, "get_traceback", return_value="trace"),
                    mock.patch.object(frappe, "log_error")]
        for p in patches:
            p.start()
        try:
            LA._synchroniser_ligne(ligne, "L-1")
            journal = frappe.log_error
            return faux, [c.args[1] for c in journal.call_args_list]
        finally:
            for p in reversed(patches):
                p.stop()

    def test_le_statut_passe_meme_si_le_marquage_leve(self):
        faux, erreurs = self._jouer(LA.PAS_DE_SYSTEME, ["_marquer_visites"])
        faux["_appliquer_statut"].assert_called_once_with("C-1", LA.PAS_DE_SYSTEME, False, "L-1", JOUR)
        self.assertEqual(len(erreurs), 1)
        self.assertIn("marquage des visites", erreurs[0])

    def test_ordre_et_isolement_de_chaque_effet(self):
        faux, erreurs = self._jouer(LA.MAGASIN, ["_cases_client", "_appliquer_statut", "_marquer_visites"])
        faux["_reporter_magasin"].assert_called_once_with("C-1", "L-1", JOUR, 15)       # le report passe quand même
        self.assertEqual(len(erreurs), 3)
        faux["_fait_lui_meme"].assert_not_called()

    def test_fait_lui_meme_en_panne_ne_reactive_pas(self):
        faux, erreurs = self._jouer(LA.FAIT_LUI_MEME, ["_fait_lui_meme"])
        # Rien de soldé (tout ou rien) : le statut est jugé « rien soldé », donc pas de réactivation.
        faux["_appliquer_statut"].assert_called_once_with("C-1", LA.FAIT_LUI_MEME, False, "L-1", JOUR)
        faux["_marquer_visites"].assert_called_once()
        self.assertEqual(len(erreurs), 1)

    def test_etape_rend_le_defaut(self):
        with mock.patch.object(frappe, "get_traceback", return_value="trace"), mock.patch.object(frappe, "log_error") as journal:
            self.assertEqual(LA._etape("x", "L", "C", mock.Mock(side_effect=ValueError("boum")), defaut=[]), [])
            self.assertEqual(LA._etape("x", "L", "C", lambda a: a + 1, 1), 2)
        journal.assert_called_once()


class TestPatchReglagesEntiers(unittest.TestCase):
    """Relecture du 26/11/2026 : un réglage entier sans ligne en base passait à 0 (= fonction coupée) au premier
    enregistrement du formulaire. Le patch écrit la valeur que config() rend déjà."""

    def test_defauts_entiers(self):
        d = P.defauts_entiers({"plafond_liste": 100, "groupes_b2b": "Pro", "fait_lui_meme_solde": 0})
        self.assertEqual(d, {"fait_lui_meme_solde": 0, "jours_rappel_magasin": 15, "jours_reveil_requalifies": 180, "plafond_liste": 100})
        reel = P.defauts_entiers()                                               # relances_config.DEFAUTS
        self.assertTrue({"fait_lui_meme_solde", "jours_rappel_magasin", "jours_reveil_requalifies", "cycles_sans_reponse"} <= set(reel))
        self.assertTrue(all(isinstance(v, int) for v in reel.values()))
        self.assertEqual(reel["cycles_sans_reponse"], RC.DEFAUTS["cycles_sans_reponse"])

    def test_memes_defauts_que_le_code(self):
        # Le patch écrit exactement ce que le code lit en l'absence de valeur.
        self.assertEqual(LA.reglages({}), {"fait_lui_meme_solde": bool(P.DEFAUTS_REPONSES["fait_lui_meme_solde"]),
                                           "jours_rappel_magasin": P.DEFAUTS_REPONSES["jours_rappel_magasin"]})
        from customization_app.Maintenance import sms_reveil as S
        self.assertEqual(S.jours_reveil({}), P.DEFAUTS_REPONSES["jours_reveil_requalifies"])

    def test_a_poser(self):
        self.assertEqual(P.a_poser(["plafond_liste", "modified"], {"plafond_liste": 100, "jours_rappel_magasin": 15}),
                         {"jours_rappel_magasin": 15})                           # une valeur en base, même 0, ne bouge pas
        self.assertEqual(P.a_poser([], {"jours_rappel_magasin": 15}), {})       # jamais enregistré : défauts du JSON


if __name__ == "__main__":
    unittest.main()
