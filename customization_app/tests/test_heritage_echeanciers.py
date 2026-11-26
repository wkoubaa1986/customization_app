"""Données héritées des échéanciers (lot 4 / proposition 9, 26/11/2026) — règles PURES, dates fixes, sans base :
- le recalage sur la date réelle (echeancier_hooks, reprise du Server Script « Update Schedule based on Real Date ») ;
- le patch qui désactive ce Server Script seulement si son code est inchangé et le hook branché ;
- les 76 visites faites restées « Pending » ;
- les 73 vieux échéanciers « sel / porte-membrane » (annuler, convertir en devinette, laisser).
Relecture du 26/11/2026 : date réelle dans le futur (ni soldée ni recalée), lignes d'appel vivantes renvoyées vers
l'échéancier gardé et articles renommés, trace sur le gardé, « à surveiller »."""
from __future__ import annotations

import copy
import datetime
import json
import types
import unittest
from unittest import mock

import frappe

from customization_app import liste_appels as LA
from customization_app.Maintenance import echeancier_hooks as H
from customization_app.patches import echeanciers_sel_porte_membrane as SP
from customization_app.patches import script_date_reelle_dans_l_app as SS
from customization_app.patches import visites_faites_restees_en_attente as VF

D = datetime.date
JOUR = "2026-11-26"          # les tests ne dépendent pas de la date du jour


def _v(date, actual=None, statut="Pending", idx=0, item="A", sms1=None, sms2=None, appel=None, nom=None):
    return frappe._dict(name=nom or "%s-%s-%s" % (item, date, idx), item_code=item, scheduled_date=D.fromisoformat(date),
                        actual_date=D.fromisoformat(actual) if actual else None, completion_status=statut, idx=idx,
                        custom_sms_1=D.fromisoformat(sms1) if sms1 else None, custom_sms_2=D.fromisoformat(sms2) if sms2 else None,
                        custom_sms1_status="Success" if sms1 else None, custom_sms2_status="Success" if sms2 else None,
                        custom_appelle=D.fromisoformat(appel) if appel else None, custom_1er_appel=None, custom_sales_order=None)


def _ancien_script(lignes):
    """Le Server Script tel qu'il est en base (code du patch), exécuté sur les lignes."""
    exec(SS.CODE_ATTENDU, {"doc": frappe._dict(schedules=lignes), "frappe": frappe})


def _recaler(lignes):
    return H.appliquer_recalage(H.plan_recalage(lignes, JOUR))


RIEN = {"dates": [], "statuts": [], "futures": []}


def _dates(lignes):
    return [str(r.scheduled_date) for r in lignes]


class TestRecalage(unittest.TestCase):
    def test_aligne_rien_a_faire(self):
        # Ce qu'écrivent le passage de nuit et « Fait lui-même » : date prévue = date réelle → aucun changement.
        lignes = [_v("2026-01-10", "2026-01-10", "Fully Completed", 1), _v("2026-07-10", idx=2), _v("2027-01-10", idx=3)]
        plan = H.plan_recalage(lignes, JOUR)
        self.assertEqual(plan, RIEN)

    def test_saisie_a_la_main_meme_regle_que_le_script(self):
        # Visite du 01/03 faite le 31/03 : elle prend sa date réelle, les suivantes glissent de 30 j jusqu'à la visite
        # réalisée suivante (qui remet le décalage à son propre écart, ici 0).
        def faire():
            return [_v("2026-03-01", "2026-03-31", idx=1), _v("2026-09-01", idx=2), _v("2027-03-01", "2027-03-01", "Fully Completed", 3),
                    _v("2027-09-01", idx=4)]
        a, b = faire(), faire()
        _ancien_script(a)
        _recaler(b)
        self.assertEqual(_dates(b), ["2026-03-31", "2026-10-01", "2027-03-01", "2027-09-01"])
        self.assertEqual(_dates(a), _dates(b))
        self.assertEqual(b[0].completion_status, "Fully Completed")          # une date réelle = visite faite

    def test_robuste_a_l_ordre_des_lignes(self):
        # Lignes hors de l'ordre des dates (cas de MAT-MSH-2023-00840) et deux articles entremêlés : le script suivait le
        # tableau et laissait des visites en place ; la reprise travaille par article dans l'ordre des dates.
        def faire():
            return [_v("2026-09-30", idx=1), _v("2026-12-30", idx=2, item="B"), _v("2026-06-30", "2026-07-30", idx=3),
                    _v("2026-06-30", idx=4, item="B"), _v("2026-12-30", idx=5)]
        a, b = faire(), faire()
        _ancien_script(a)
        _recaler(b)
        self.assertEqual({r.name: str(r.scheduled_date) for r in b if r.item_code == "A"},
                         {"A-2026-06-30-3": "2026-07-30", "A-2026-09-30-1": "2026-10-30", "A-2026-12-30-5": "2027-01-29"})
        self.assertEqual([str(r.scheduled_date) for r in b if r.item_code == "B"], ["2026-12-30", "2026-06-30"])   # B intact
        self.assertNotEqual(_dates(a), _dates(b))                              # le script, lui, n'a pas décalé la 1re ligne
        self.assertEqual(a[0].scheduled_date, D(2026, 9, 30))

    def test_visite_repoussee_apres_l_entretien_redevient_neuve(self):
        # Prévue le 01/04, déjà relancée, repoussée au 01/05 alors que l'entretien date du 15/04 : SMS et appel effacés,
        # comme au passage de nuit. Celle qui reste après l'entretien garde les siens.
        lignes = [_v("2026-03-01", "2026-04-15", idx=1), _v("2026-04-01", idx=2, sms1="2026-03-20", sms2="2026-03-27", appel="2026-03-30"),
                  _v("2026-09-01", idx=3, sms1="2026-08-25")]
        plan = H.plan_recalage(lignes, JOUR)
        self.assertEqual(len(plan["dates"]), 1)
        self.assertEqual([(str(a), str(b), sorted(e)) for _r, a, b, e in plan["dates"][0]["deplacees"]],
                         [("2026-04-01", "2026-05-16", ["custom_appelle", "custom_sms1_status", "custom_sms2_status", "custom_sms_1", "custom_sms_2"]),
                          ("2026-09-01", "2026-10-16", [])])
        H.appliquer_recalage(plan)
        self.assertEqual((lignes[1].custom_sms_1, lignes[1].custom_sms_2, lignes[1].custom_appelle), (None, None, None))
        self.assertEqual(lignes[2].custom_sms_1, D(2026, 8, 25))
        texte = H.texte_recalage(plan["dates"][0])
        self.assertIn("📅 Visite A du 01/03/2026 réalisée le 15/04/2026", texte)
        self.assertIn("01/04/2026 → 16/05/2026 (effacés : SMS1 20/03/2026 ✓, SMS2 27/03/2026 ✓, appelé 30/03/2026)", texte)

    def test_faite_sans_date_ne_bouge_jamais(self):
        lignes = [_v("2026-01-01", "2026-02-01", idx=1), _v("2026-07-01", statut="Fully Completed", idx=2), _v("2027-01-01", idx=3)]
        _recaler(lignes)
        self.assertEqual(_dates(lignes), ["2026-02-01", "2026-07-01", "2027-02-01"])

    def test_rejouable_et_compatible_avec_le_script(self):
        # La reprise puis le script (Document.run_method : hooks Python d'abord) : aucun double décalage.
        lignes = [_v("2026-03-01", "2026-03-31", idx=1), _v("2026-09-01", idx=2), _v("2027-03-01", idx=3)]
        _recaler(lignes)
        apres = _dates(lignes)
        self.assertEqual(H.plan_recalage(lignes, JOUR), RIEN)
        _ancien_script(lignes)
        self.assertEqual(_dates(lignes), apres)

    def test_rien_apres_le_passage_de_nuit_ni_fait_lui_meme(self):
        # « Fait lui-même » (vague A) solde à la date de l'appel et décale lui-même : le recalage n'a rien à refaire.
        lignes = [_v("2026-05-01", idx=1, sms1="2026-05-02", sms2="2026-05-09"), _v("2026-11-01", idx=2), _v("2027-05-01", idx=3)]
        self.assertIsNotNone(LA.solder_fait_lui_meme(lignes, "2026-11-25"))
        self.assertEqual(H.plan_recalage(lignes, JOUR), RIEN)
        # Le passage de nuit (update_schedule.shift_schedule_for_delivery) pose actual = scheduled = livraison, statut fait.
        nuit = [_v("2026-11-20", "2026-11-20", "Fully Completed", 1), _v("2027-05-20", idx=2)]
        self.assertEqual(H.plan_recalage(nuit, JOUR), RIEN)
        # Une livraison PRÉVUE dans les jours qui viennent (la nuit lit jusqu'à fenetre_mois_apres) : date réelle à
        # venir mais alignée et soldée — rien à faire, rien à signaler.
        bientot = [_v("2026-12-03", "2026-12-03", "Fully Completed", 1), _v("2027-06-03", idx=2)]
        self.assertEqual(H.plan_recalage(bientot, JOUR), RIEN)

    def test_statut_d_une_visite_faite_a_la_main(self):
        lignes = [_v("2025-03-01", "2025-03-01", "Pending", 1), _v("2025-09-01", idx=2)]
        plan = H.plan_recalage(lignes, JOUR)
        self.assertEqual((plan["dates"], plan["statuts"], plan["futures"]), ([], [lignes[0]], []))
        H.appliquer_recalage(plan)
        self.assertEqual(lignes[0].completion_status, "Fully Completed")

    def test_date_reelle_dans_le_futur(self):
        # Relecture du 26/11/2026 : 2062 tapé pour 2026 sur une visite prévue le 01/11/2026. Avant : visite soldée,
        # les suivantes repoussées de ~13 160 j, celles d'avant l'entretien vidées de leurs relances. Maintenant : rien
        # ne bouge, la ligne est signalée.
        lignes = [_v("2026-05-01", "2026-05-31", idx=1), _v("2026-11-01", "2062-11-26", idx=2, sms1="2026-10-25"),
                  _v("2027-05-01", idx=3, sms1="2027-04-25"), _v("2027-11-01", idx=4)]
        plan = H.plan_recalage(lignes, JOUR)
        self.assertEqual(plan["futures"], [lignes[1]])
        self.assertEqual(plan["statuts"], [lignes[0]])
        self.assertEqual([c["ligne"] for c in plan["dates"]], [lignes[0]])
        self.assertEqual(plan["dates"][0]["deplacees"], [])        # le décalage de mai s'arrête à la date douteuse
        H.appliquer_recalage(plan)
        self.assertEqual(_dates(lignes), ["2026-05-31", "2026-11-01", "2027-05-01", "2027-11-01"])
        self.assertEqual((lignes[1].completion_status, lignes[2].custom_sms_1), ("Pending", D(2027, 4, 25)))
        # Le même jour vu plus tard (date passée) : traitée normalement.
        self.assertEqual(H.plan_recalage(lignes, "2062-12-01")["futures"], [])
        self.assertIn("date réelle 26/11/2062", H.texte_futures("MS-1", plan["futures"]))

    def test_le_hook_signale_une_date_future_sans_bloquer(self):
        doc = types.SimpleNamespace(name="MS-TEST", flags=frappe._dict(),
                                    schedules=[_v("2026-11-01", "2062-11-26", idx=1), _v("2027-05-01", idx=2)],
                                    get=lambda champ: doc.schedules if champ == "schedules" else None,
                                    add_comment=lambda *a, **k: None)
        journal, messages = [], []
        with mock.patch.object(H, "nowdate", lambda: JOUR), \
             mock.patch.object(frappe, "log_error", lambda *a, **k: journal.append(k.get("title"))), \
             mock.patch.object(frappe, "msgprint", lambda *a, **k: messages.append(a[0])):
            H.before_update_after_submit(doc)
        self.assertEqual(_dates(doc.schedules), ["2026-11-01", "2027-05-01"])
        self.assertEqual(doc.schedules[0].completion_status, "Pending")
        self.assertEqual(journal, ["Recalage date réelle : date dans le futur — MS-TEST"])
        self.assertEqual(len(messages), 1)

    def test_le_hook_ne_bloque_jamais(self):
        doc = types.SimpleNamespace(name="MS-TEST", flags=frappe._dict(), schedules=[_v("2026-03-01", "2026-03-31", idx=1), _v("2026-09-01", idx=2)],
                                    get=lambda champ: doc.schedules if champ == "schedules" else None)
        traces = []
        doc.add_comment = lambda typ, texte: traces.append((typ, texte))
        with mock.patch.object(H, "nowdate", lambda: JOUR):
            H.before_update_after_submit(doc)
        self.assertEqual(_dates(doc.schedules), ["2026-03-31", "2026-10-01"])
        self.assertEqual(doc.flags.recalage_dates, 1)
        self.assertEqual(traces[0][0], "Info")
        # Une erreur pendant le calcul : journalisée, rien de modifié, l'enregistrement continue.
        doc.schedules = [_v("2026-03-01", "2026-03-31", idx=1), _v("2026-09-01", idx=2)]
        journal = []
        with mock.patch.object(H, "plan_recalage", side_effect=ValueError("boum")), \
             mock.patch.object(frappe, "log_error", lambda *a, **k: journal.append(a)):
            H.before_update_after_submit(doc)
        self.assertEqual(_dates(doc.schedules), ["2026-03-01", "2026-09-01"])
        self.assertEqual(len(journal), 1)
        # Correction volontaire d'une date prévue : on peut couper le recalage.
        doc.flags = frappe._dict(sans_recalage=True)
        H.before_update_after_submit(doc)
        self.assertEqual(_dates(doc.schedules), ["2026-03-01", "2026-09-01"])


class TestPatchScript(unittest.TestCase):
    BON = {"script": SS.CODE_ATTENDU, "reference_doctype": "Maintenance Schedule",
           "doctype_event": "Before Save (Submitted Document)", "disabled": 0}

    def test_desactive_seulement_si_inchange_et_remplace(self):
        self.assertEqual(SS.motif_de_garder(dict(self.BON)), "")
        self.assertEqual(SS.motif_de_garder(dict(self.BON, script=SS.CODE_ATTENDU.replace("\n", "\r\n") + "\n\n")), "")
        self.assertIn("code modifié", SS.motif_de_garder(dict(self.BON, script=SS.CODE_ATTENDU + "\nfrappe.msgprint('x')")))
        self.assertEqual(SS.motif_de_garder(dict(self.BON, disabled=1)), "déjà désactivé")
        self.assertIn("événement", SS.motif_de_garder(dict(self.BON, doctype_event="After Save")))
        self.assertEqual(SS.motif_de_garder(None), "absent de la base")
        self.assertIn("pas branché", SS.motif_de_garder(dict(self.BON), branche=False))

    def test_hook_branche(self):
        self.assertTrue(SS.hook_branche({"Maintenance Schedule": {"before_update_after_submit": [SS.HOOK]}}))
        self.assertTrue(SS.hook_branche({"Maintenance Schedule": {"before_update_after_submit": SS.HOOK}}))
        self.assertFalse(SS.hook_branche({"Maintenance Schedule": {"on_update": [SS.HOOK]}}))
        self.assertFalse(SS.hook_branche({}))


class TestVisitesFaitesEnAttente(unittest.TestCase):
    def test_critere(self):
        lignes = [{"name": "1", "parent": "M1", "item_code": "A", "scheduled_date": D(2024, 4, 26), "actual_date": D(2024, 4, 26),
                   "completion_status": "Pending"},
                  {"name": "2", "parent": "M1", "item_code": "A", "scheduled_date": D(2025, 4, 26), "actual_date": D(2025, 4, 26),
                   "completion_status": "Fully Completed"},
                  {"name": "3", "parent": "M2", "item_code": "B", "scheduled_date": D(2027, 1, 1), "actual_date": D(2027, 1, 1),
                   "completion_status": ""},
                  {"name": "4", "parent": "M3", "item_code": "C", "scheduled_date": D(2024, 6, 24), "actual_date": None,
                   "completion_status": "Fully Completed"},
                  {"name": "5", "parent": "M3", "item_code": "C", "scheduled_date": D(2024, 12, 24), "actual_date": None,
                   "completion_status": "Pending"}]
        c = VF.classer(lignes, "2026-11-26")
        self.assertEqual([r["name"] for r in c["corriger"]], ["1"])
        self.assertEqual([r["name"] for r in c["futur"]], ["3"])         # date réelle à venir : faute de frappe, laissée
        self.assertEqual([r["name"] for r in c["sans_date"]], ["4"])     # faite sans date : aucune date à poser
        texte = VF.compte_rendu(c)
        self.assertTrue(texte.startswith("1 visite(s) faite(s) passée(s) « Fully Completed » (1 échéancier(s))."))
        self.assertIn("M1 A : faite le 26/04/2024", texte)


class TestSelPorteMembrane(unittest.TestCase):
    TYPES = {"ADOUCISSEUR": "Ad-MB-30L", "RO_DOM": "AP-M-AJ-5-SM", "RO_COM": "AP-SM-5-200GPD"}

    def _h(self, ms, code, creation="2025-01-01", realisees=(), recente=False):
        return {"ms": ms, "item_code": code, "famille": SP.famille_visee([code]), "creation": creation, "realisees": list(realisees),
                "recente": recente}

    def _actions(self, plan):
        return {d["ms"]: (d["action"], d["vers"]) for d in plan}

    def test_familles_visees(self):
        self.assertEqual(SP.famille_visee(["S-S25"]), "ADOUCISSEUR")
        self.assertEqual(SP.famille_visee(["P-M-1812"]), "RO_DOM")
        self.assertEqual(SP.famille_visee(["P-M-3013"]), "RO_COM")
        self.assertIsNone(SP.famille_visee(["R-L"]))                        # résine : doute
        self.assertIsNone(SP.famille_visee(["S-S25", "P-M-1812"]))          # deux lignes : doute

    def test_annule_quand_la_famille_est_deja_suivie(self):
        # Wissem barkaoui : trois échéanciers « sel » et une devinette Ad-MB-30L née d'un achat de sel.
        plan = SP.plan_client([self._h("S1", "S-S25"), self._h("S2", "S-S25"), self._h("S3", "S-S25")],
                              [{"ms": "DEV", "familles": {"ADOUCISSEUR"}}], self.TYPES)
        self.assertEqual(self._actions(plan), {"S1": ("annuler", None), "S2": ("annuler", None), "S3": ("annuler", None)})
        self.assertTrue(all(d["garde"] == "DEV" and "DEV" in d["motif"] for d in plan))

    def test_convertit_un_seul_et_annule_ses_doublons(self):
        # Belhssan Chikh : deux « sel », pas d'adoucisseur (un porte-filtre seulement) → le plus récent devient la devinette.
        plan = SP.plan_client([self._h("S1", "S-S25", "2025-06-10"), self._h("S2", "S-S25", "2025-07-22")],
                              [{"ms": "PF", "familles": {"PF"}}], self.TYPES)
        self.assertEqual(self._actions(plan), {"S2": ("remplacer", "Ad-MB-30L"), "S1": ("annuler", None)})
        # À histoire égale le plus récent ; une visite réalisée l'emporte sur la date.
        plan = SP.plan_client([self._h("S1", "S-S25", "2025-06-10", ["SO-1"]), self._h("S2", "S-S25", "2025-07-22")], [], self.TYPES)
        self.assertEqual(self._actions(plan)["S1"], ("remplacer", "Ad-MB-30L"))

    def test_porte_membrane(self):
        plan = SP.plan_client([self._h("P1", "P-M-1812")], [{"ms": "RO", "familles": {"RO_DOM"}}], self.TYPES)
        self.assertEqual(self._actions(plan), {"P1": ("annuler", None)})
        plan = SP.plan_client([self._h("P1", "P-M-1812")], [], self.TYPES)
        self.assertEqual(self._actions(plan), {"P1": ("remplacer", "AP-M-AJ-5-SM")})
        # Un 1812 chez un client qui n'a qu'un osmoseur COMMERCIAL : lequel le porte ? Doute, laissé.
        plan = SP.plan_client([self._h("P1", "P-M-1812")], [{"ms": "RC", "familles": {"RO_COM"}}], self.TYPES)
        self.assertEqual(self._actions(plan), {"P1": ("laisser", None)})
        self.assertIn("RO_COM", plan[0]["motif"])

    def test_doutes(self):
        plan = SP.plan_client([self._h("R", "R-L"), self._h("S", "S-S25")], [], {"RO_DOM": "AP-M-AJ-5-SM"})
        self.assertEqual(self._actions(plan), {"R": ("laisser", None), "S": ("laisser", None)})
        self.assertIn("sans famille sûre", plan[0]["motif"])
        self.assertIn("pas de machine type", plan[1]["motif"])
        # Une commande encore dans la fenêtre de nuit : annuler la rendrait « non couverte », la nuit la relirait.
        plan = SP.plan_client([self._h("S", "S-S25", recente=True)], [{"ms": "AD", "familles": {"ADOUCISSEUR"}}], self.TYPES)
        self.assertEqual(self._actions(plan), {"S": ("laisser", None)})

    def test_devines_en_double(self):
        self.assertEqual(SP.devines_a_annuler([{"ms": "DEV", "devine": True, "realisees": ["SO-SEL"]}]), [])       # seul : gardé
        lot = [{"ms": "VRAI", "devine": False, "realisees": ["SO-1", "SO-2"]},
               {"ms": "DEV1", "devine": True, "realisees": []},                  # rien de propre : annulé
               {"ms": "DEV2", "devine": True, "realisees": ["SO-2"]},            # même achat que le vrai : annulé
               {"ms": "DEV3", "devine": True, "realisees": ["SO-9"]},            # un achat à lui : gardé
               {"ms": "DEV4", "devine": True, "realisees": [""]}]                # visite à la main : gardé
        self.assertEqual(SP.devines_a_annuler(lot), ["DEV1", "DEV2"])

    def test_textes(self):
        t = SP.texte_annulation("MS-1", {"item_code": "S-S25", "item_name": "Sac de sel -25kg"}, "le client a déjà l'échéancier MS-9 (adoucisseur)",
                                [(D(2025, 8, 23), "SO-7"), (D(2025, 1, 2), "")], "2026-11-26")
        self.assertTrue(t.startswith("🧹 Échéancier hérité MS-1 annulé le 26/11/2026 : il suivait S-S25 (Sac de sel -25kg)"))
        self.assertIn("Le client a déjà l'échéancier MS-9", t)
        self.assertIn("23/08/2025 (SO-7), 02/01/2025 (à la main)", t)
        self.assertTrue(SP.texte_annulation("MS-2", {"item_code": "Ad-MB-30L"}, "doublon", [], "2026-11-26", devine=True)
                        .startswith("🧹 Échéancier deviné MS-2 (Ad-MB-30L) annulé le 26/11/2026. Doublon."))
        t = SP.texte_remplacement("MS-1", {"item_code": "S-S25", "item_name": "Sac de sel -25kg"}, {"item_code": "Ad-MB-30L", "item_name": "Adoucisseur"},
                                  10, "2026-11-26")
        self.assertIn("S-S25 (Sac de sel -25kg) est remplacé par Ad-MB-30L (Adoucisseur)", t)
        self.assertIn("ses 10 visites — dates, SMS et appels inchangés", t)

    def test_compte_rendu(self):
        b = {"annules": ["a"], "remplaces": ["r1", "r2"], "laisses": [], "devines_annules": [], "devines_gardes": ["g"],
             "a_surveiller": ["s"], "lignes_appel": ["LIST-1 ligne 1"], "erreurs": [], "mixtes": ["M"]}
        t = SP.compte_rendu(b)
        self.assertTrue(t.startswith("Échéanciers hérités « sel / porte-membrane » : 1 annulé(s), 2 converti(s) en devinette, 0 laissé(s)"))
        self.assertIn("1 à surveiller", t)
        self.assertIn("1 ligne(s) de listes d'appels encore vivantes réécrites", t)
        self.assertIn("À surveiller à la vente d'une machine :\n  s", t)
        self.assertIn("Hors périmètre", t)

    # ── Relecture du 26/11/2026 ──────────────────────────────────────────────

    def test_lignes_d_appel_vivantes(self):
        # Le brouillon (enregistré, validé à 08:00 / 19:00) et ce que le 2e appel relira ; jamais l'historique.
        self.assertEqual(SP.ligne_vivante({"docstatus": 0}), "brouillon")
        source = {"docstatus": 1, "r2": 0, "type_liste": "Normal", "resume_appel": "Ne répond pas 1er appel", "appele": 1}
        self.assertEqual(SP.ligne_vivante(source), "a_relire")
        self.assertIsNone(SP.ligne_vivante(dict(source, r2=1)))                     # déjà reprise
        self.assertIsNone(SP.ligne_vivante(dict(source, resume_appel="Rendez-vous pris")))
        self.assertIsNone(SP.ligne_vivante(dict(source, type_liste="Hors secteur")))  # pas de 2e appel
        self.assertEqual(SP.ligne_vivante(dict(source, type_liste="Relance", appele=0)), "a_relire")  # reprise d'une Relance
        self.assertIsNone(SP.ligne_vivante(dict(source, type_liste="Relance")))     # appelée en Relance : fin
        self.assertIsNone(SP.ligne_vivante({"docstatus": 2}))

    def test_articles_renommes(self):
        # Sté MBG : {"S-S25": …} recopié de LIST-20-11-2026-00002 dans la Relance LIST-23-11-2026-00001 ; converti en
        # Ad-MB-30L, la ligne doit citer Ad-MB-30L, sinon ni la marque « appelé » ni « Fait lui-même » ne trouvent la visite.
        self.assertEqual(json.loads(SP.articles_renommes('{"S-S25": "2026-11-10"}', {"S-S25": "Ad-MB-30L"})),
                         {"Ad-MB-30L": "2026-11-10"})
        # Ligne regroupée : les autres articles restent, l'ordre aussi.
        self.assertEqual(list(json.loads(SP.articles_renommes('{"AP-5": "2026-10-01", "S-S25": "2026-11-10"}', {"S-S25": "Ad-MB-25L"}))),
                         ["AP-5", "Ad-MB-25L"])
        # Le nouvel article est déjà cité : sa date reste, l'ancien part.
        self.assertEqual(json.loads(SP.articles_renommes('{"S-S25": "2026-11-10", "Ad-MB-25L": "2026-09-01"}', {"S-S25": "Ad-MB-25L"})),
                         {"Ad-MB-25L": "2026-09-01"})
        # Un autre échéancier soumis porte encore S-S25 (adoucisseur + ligne sel) : l'ancien reste à côté.
        self.assertEqual(json.loads(SP.articles_renommes('{"S-S25": "2026-11-10"}', {"S-S25": "Ad-MB-25L"}, {"S-S25"})),
                         {"S-S25": "2026-11-10", "Ad-MB-25L": "2026-11-10"})
        self.assertIsNone(SP.articles_renommes('{"AP-5": "2026-10-01"}', {"S-S25": "Ad-MB-25L"}))   # rien à changer
        self.assertIsNone(SP.articles_renommes("illisible", {"S-S25": "Ad-MB-25L"}))
        self.assertIsNone(SP.articles_renommes('{"_meta": 1}', {"_meta": "x"}))

    def test_resoudre_les_chaines(self):
        # Un hérité renvoyé vers un deviné annulé ensuite : il suit jusqu'au vrai échéancier.
        self.assertEqual(SP.resoudre({"S1": "DEV", "DEV": "VRAI"}), {"S1": "VRAI", "DEV": "VRAI"})
        self.assertEqual(SP.resoudre({"S-S25": "Ad-MB-30L", "Ad-MB-30L": "Ad-MB-25L"})["S-S25"], "Ad-MB-25L")
        self.assertEqual(SP.resoudre({"A": "B", "B": "A"}), {"A": "A", "B": "B"})        # une boucle ne tourne pas

    def test_trace_sur_le_garde_et_a_surveiller(self):
        t = SP.texte_pour_le_garde("MS-1", {"item_code": "S-S25", "item_name": "Sac de sel -25kg"},
                                   [(D(2025, 8, 23), "SO-7")], JOUR)
        self.assertTrue(t.startswith("🧹 L'échéancier hérité MS-1 (S-S25, Sac de sel -25kg) de ce client a été annulé le 26/11/2026"))
        self.assertIn("Visites réalisées qu'il portait : 23/08/2025 (SO-7)", t)
        # « Historique client » ne reprend que ⏸️ / ✅ de la fiche client, mais tous les commentaires Info des
        # échéanciers soumis : la trace du gardé commence bien par 🧹 et cite l'annulé.
        self.assertIn("MS-1", t)
        s = SP.a_surveiller("MS-2", "Daboussi Ines", "converti en AP-M-AJ-5-SM", [(D(2025, 10, 25), "SAL-ORD-2025-03107")])
        self.assertIn("1 visite(s) réalisée(s) (25/10/2025 SAL-ORD-2025-03107)", s)
        self.assertIn("à annuler à la main", s)


if __name__ == "__main__":
    unittest.main()
