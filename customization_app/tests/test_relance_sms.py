"""Relance SMS d'entretien, revue du 25/11/2026 — règles PURES (sans base) :

- un seul SMS par client et par jour : les « paquets » d'avant (un échéancier × un type) d'un même client fusionnent en
  un envoi ; le texte est, mot pour mot, celui du paquet de la famille prioritaire (un client à UN seul SMS garde donc
  le texte d'avant, « vos équipements » compris — relecture du 25/11/2026) ; TOUTES les visites sont marquées, chacune
  dans son champ, y compris sans validation quand la fiche refuse sa sauvegarde ; le plafond compte un SMS par client ;
  réglage à 0 = exactement l'envoi d'avant ;
- les exclusions comptées par motif (« exclus_partenaire » = les seuls clients du partenaire) ;
- la ligne [SUMMARY] et les compteurs remis à l'entonnoir (noter, jamais bloquant)."""
from __future__ import annotations

import datetime
import sys
import types
import unittest
from unittest import mock

import frappe

from customization_app import relances_config as RC
from customization_app.Maintenance import relance_maintenance_sms as R

FAMS = [{"code": f["code"], "libelle_sms": f["libelle_sms"], "article_entretien": f["article_entretien"],
         "article_type": f["article_type"], "groupes_machines": RC.liste(f["groupes_machines"]),
         "groupes_consommables": RC.liste(f["groupes_consommables"])} for f in RC.DEFAUT_FAMILLES]
ORDRE = [f["code"] for f in FAMS]          # RO_DOM, ADOUCISSEUR, RO_COM, RO_IND, BIO, FONTAINE, UV, PF, POMPE
J = datetime.date


def V(ms, item, typ="SMS1", date="2026-05-20", famille="RO_DOM", client="CLI-1", secteur="Secteur 1"):
    return {"customer": client, "maintenance": ms, "item_code": item, "sms_type": typ, "scheduled_date": date,
            "secteur": secteur, "famille": famille}


class TestPaquets(unittest.TestCase):
    def test_un_paquet_devient_ses_visites(self):
        paquet = {"AP-M-AJ-5-SM*?/*MS-1*?/*SMS1": [J(2026, 5, 20), "Secteur 2"],
                  "P-F-T-10'-T-SC*?/*MS-1*?/*SMS1": [J(2026, 5, 21), "Secteur 2"]}
        familles = {"AP-M-AJ-5-SM": "RO_DOM", "P-F-T-10'-T-SC": "PF"}
        visites = R.visites_du_paquet(paquet, "CLI-1", familles.get)
        self.assertEqual([(v["item_code"], v["maintenance"], v["sms_type"], v["scheduled_date"], v["secteur"], v["famille"])
                          for v in visites],
                         [("AP-M-AJ-5-SM", "MS-1", "SMS1", J(2026, 5, 20), "Secteur 2", "RO_DOM"),
                          ("P-F-T-10'-T-SC", "MS-1", "SMS1", J(2026, 5, 21), "Secteur 2", "PF")])
        self.assertTrue(all(v["customer"] == "CLI-1" for v in visites))
        self.assertEqual(R.visites_du_paquet({}, "CLI-1", familles.get), [])


class TestRegrouperEnvois(unittest.TestCase):
    def test_deux_echeanciers_du_meme_client_un_seul_envoi(self):
        """Cas du 26/11/2026 sur la copie (A R S D) : adoucisseur + fontaine, deux échéanciers, deux SMS1 le même jour."""
        paquets = [[V("MS-A", "Ad-MB-30L", famille="ADOUCISSEUR")], [V("MS-B", "FF-5-Mini-75GPD", famille="FONTAINE")]]
        envois = R.regrouper_envois(paquets, ORDRE, True)
        self.assertEqual(len(envois), 1)
        e = envois[0]
        self.assertEqual((e["customer"], e["nb_paquets"], len(e["visites"]), e["regroupe"]), ("CLI-1", 2, 2, True))
        self.assertEqual(e["principale"]["maintenance"], "MS-A")            # ADOUCISSEUR passe avant FONTAINE (réglage)
        self.assertEqual(e["familles"], ["ADOUCISSEUR", "FONTAINE"])
        self.assertEqual(e["familles_message"], ["ADOUCISSEUR"])               # le SMS d'avant de MS-A
        self.assertEqual(R.texte_appareil(e["familles_message"], FAMS), "votre adoucisseur")

    def test_un_seul_paquet_garde_le_texte_d_avant_meme_reglage_actif(self):
        """Défaut de relecture (25/11/2026) : réglage à 1, un client à UN seul SMS dont l'échéancier doit osmoseur +
        porte-filtre (Dherbi Tahar, RO_DOM + PF ; Sté LMM, adoucisseur + UV le 28/11) — pas de fusion, donc ni
        « regroupé » ni changement de texte : « vos équipements de traitement d'eau », comme avant."""
        paquet = [V("MS-A", "AP-6-DF", famille="RO_DOM"), V("MS-A", "P-F", famille="PF")]
        for reglage in (True, False):
            e = R.regrouper_envois([paquet], ORDRE, reglage)[0]
            self.assertEqual((e["nb_paquets"], e["regroupe"]), (1, False))
            self.assertEqual(e["familles_message"], ["RO_DOM", "PF"])
            self.assertEqual(R.texte_appareil(e["familles_message"], FAMS), "vos équipements de traitement d'eau")

    def test_regroupe_le_message_du_paquet_prioritaire_mot_pour_mot(self):
        """Regroupé : le texte du paquet de la famille prioritaire, tel quel. Ce paquet doit adoucisseur + UV : « vos
        équipements » (le SMS d'avant de MS-A), et non « votre adoucisseur » ; la fontaine de MS-B est couverte."""
        paquets = [[V("MS-B", "FF-5", famille="FONTAINE")],
                   [V("MS-A", "Ad-30L", famille="ADOUCISSEUR"), V("MS-A", "UV-12", famille="UV")]]
        e = R.regrouper_envois(paquets, ORDRE, True)[0]
        self.assertEqual((e["nb_paquets"], e["regroupe"], e["principale"]["maintenance"]), (2, True, "MS-A"))
        self.assertEqual(e["familles"], ["ADOUCISSEUR", "FONTAINE", "UV"])
        self.assertEqual(e["familles_message"], ["ADOUCISSEUR", "UV"])
        self.assertEqual(R.texte_appareil(e["familles_message"], FAMS), "vos équipements de traitement d'eau")
        seul = R.regrouper_envois([paquets[0]], ORDRE, True)[0]
        self.assertEqual(R.texte_appareil(seul["familles_message"], FAMS), RC.libelle_famille("FONTAINE", FAMS))

    def test_sms1_et_sms2_du_meme_echeancier(self):
        paquets = [[V("MS-A", "AP-6-DF", "SMS1", "2026-10-22")], [V("MS-A", "P-F", "SMS2", "2026-10-01", famille="PF")]]
        e = R.regrouper_envois(paquets, ORDRE, True)
        self.assertEqual(len(e), 1)
        self.assertEqual(sorted(v["sms_type"] for v in e[0]["visites"]), ["SMS1", "SMS2"])
        self.assertEqual(e[0]["principale"]["item_code"], "AP-6-DF")         # RO_DOM avant PF, même plus récente

    def test_reglage_a_zero_un_envoi_par_paquet_dans_le_meme_ordre(self):
        paquets = [[V("MS-A", "X")], [V("MS-B", "Y", famille="PF")], [V("MS-C", "Z", client="CLI-2")]]
        envois = R.regrouper_envois(paquets, ORDRE, False)
        self.assertEqual([e["principale"]["maintenance"] for e in envois], ["MS-A", "MS-B", "MS-C"])
        self.assertTrue(all(e["nb_paquets"] == 1 and not e["regroupe"] for e in envois))

    def test_ordre_de_premiere_apparition_et_clients_distincts(self):
        paquets = [[V("MS-A", "X", client="C1")], [V("MS-B", "Y", client="C2")], [V("MS-C", "Z", "SMS2", client="C1")]]
        envois = R.regrouper_envois(paquets, ORDRE, True)
        self.assertEqual([(e["customer"], e["nb_paquets"]) for e in envois], [("C1", 2), ("C2", 1)])

    def test_principale_famille_puis_echeance_la_plus_ancienne(self):
        paquets = [[V("MS-B", "X", date="2026-06-01"), V("MS-B", "Y", date="2026-09-01", famille=None)],
                   [V("MS-A", "Z", date="2026-04-01")]]
        e = R.regrouper_envois(paquets, ORDRE, True)[0]
        self.assertEqual((e["principale"]["maintenance"], e["principale"]["item_code"]), ("MS-A", "Z"))
        self.assertEqual(e["familles"], ["RO_DOM"])                           # sans famille : ni compté ni prioritaire
        sans = R.regrouper_envois([[V("MS-S", "SEL", famille=None)], [V("MS-P", "PF", famille="PF")]], ORDRE, True)[0]
        self.assertEqual(sans["principale"]["maintenance"], "MS-P")           # une famille connue passe avant « aucune »

    def test_secteur_de_l_envoi_celui_de_la_principale(self):
        paquets = [[V("MS-B", "Y", famille="PF", secteur="Secteur 5, Secteur 6")], [V("MS-A", "X", secteur="Secteur 6, Secteur 5")]]
        self.assertEqual(R.regrouper_envois(paquets, ORDRE, True)[0]["secteur"], "Secteur 6, Secteur 5")

    def test_paquets_vides_ignores(self):
        self.assertEqual(R.regrouper_envois([[], None, [V("MS-A", "X")]], ORDRE, True)[0]["nb_paquets"], 1)
        self.assertEqual(R.regrouper_envois([], ORDRE, True), [])


class TestBilanRegroupement(unittest.TestCase):
    def test_chiffres(self):
        paquets = [[V("MS-A", "X"), V("MS-A", "W")], [V("MS-B", "Y")], [V("MS-C", "Z", client="C2")]]
        un = R.bilan_regroupement(paquets, R.regrouper_envois(paquets, ORDRE, True))
        self.assertEqual(un, {"visites_candidates": 4, "clients_candidats": 2, "clients_plusieurs_sms": 1,
                              "envois_candidats": 2, "sms_evites": 1})
        zero = R.bilan_regroupement(paquets, R.regrouper_envois(paquets, ORDRE, False))
        self.assertEqual((zero["envois_candidats"], zero["sms_evites"], zero["clients_plusieurs_sms"]), (3, 0, 1))


class TestTexteAppareil(unittest.TestCase):
    def test_regle_d_avant(self):
        """Le texte d'un paquet, tel que l'écrivait l'ancien code (HEAD) : plusieurs familles → « vos équipements »."""
        self.assertEqual(R.texte_appareil(["RO_DOM", "PF"], FAMS), "vos équipements de traitement d'eau")
        self.assertEqual(R.texte_appareil(["ADOUCISSEUR", "UV"], FAMS), "vos équipements de traitement d'eau")
        self.assertEqual(R.texte_appareil(["PF"], FAMS), "votre porte-filtre")
        self.assertEqual(R.texte_appareil(["ADOUCISSEUR"], FAMS), "votre adoucisseur")
        self.assertEqual(R.texte_appareil(["PF", "PF"], FAMS), "votre porte-filtre")

    def test_sans_famille(self):
        self.assertEqual(R.texte_appareil([], FAMS), RC.LIBELLE_FAMILLE_DEFAUT)
        self.assertEqual(R.texte_appareil([None], FAMS), RC.LIBELLE_FAMILLE_DEFAUT)


def L(item, date, **kw):
    base = {"item_code": item, "scheduled_date": J(*date), "custom_sms_1": None, "custom_sms1_status": None,
            "custom_sms_2": None, "custom_sms2_status": None}
    base.update(kw)
    return frappe._dict(base)


class TestMarquerVisites(unittest.TestCase):
    def test_chaque_visite_dans_son_champ(self):
        lignes = [L("AP", (2026, 5, 20)), L("AP", (2026, 11, 20)), L("PF", (2026, 5, 20), custom_sms_1=J(2026, 11, 10))]
        visites = [V("MS", "AP", "SMS1", "2026-11-20"), V("MS", "PF", "SMS2", J(2026, 5, 20))]
        jour = J(2026, 11, 26)
        self.assertEqual(R.marquer_visites(lignes, visites, jour, "Success"), 2)
        self.assertEqual((lignes[1].custom_sms_1, lignes[1].custom_sms1_status, lignes[1].custom_sms_2), (jour, "Success", None))
        self.assertEqual((lignes[2].custom_sms_1, lignes[2].custom_sms_2, lignes[2].custom_sms2_status),
                         (J(2026, 11, 10), jour, "Success"))
        self.assertIsNone(lignes[0].custom_sms_1)                                   # l'autre visite de l'article : intacte

    def test_premiere_ligne_seulement_et_rien_si_absente(self):
        lignes = [L("AP", (2026, 5, 20)), L("AP", (2026, 5, 20))]
        self.assertEqual(R.marquer_visites(lignes, [V("MS", "AP", "SMS1", "2026-05-20")], J(2026, 11, 26), "Invalid Number"), 1)
        self.assertEqual((lignes[0].custom_sms1_status, lignes[1].custom_sms1_status), ("Invalid Number", None))
        self.assertEqual(R.marquer_visites(lignes, [V("MS", "AP", "SMS1", "2026-06-20")], J(2026, 11, 26), "Success"), 0)
        self.assertEqual(R.marquer_visites(lignes, [V("MS", "AP", "SMS3", "2026-05-20")], J(2026, 11, 26), "Success"), 0)


class TestMarquerEnBase(unittest.TestCase):
    """Repli quand la fiche refuse sa sauvegarde après l'envoi (relecture du 25/11/2026) : sans lui, le même SMS
    repartait chaque jour."""

    def test_seuls_les_champs_sms_de_la_bonne_ligne(self):
        lignes = [frappe._dict(name="r1", item_code="AP", scheduled_date=J(2026, 5, 20), custom_sms_1=None,
                               custom_sms1_status=None, custom_sms_2=None, custom_sms2_status=None),
                  frappe._dict(name="r2", item_code="AP", scheduled_date=J(2026, 11, 20), custom_sms_1=J(2026, 11, 10),
                               custom_sms1_status="Success", custom_sms_2=None, custom_sms2_status=None),
                  frappe._dict(name="r3", item_code="AP", scheduled_date=J(2026, 11, 20), custom_sms_1=None,
                               custom_sms1_status=None, custom_sms_2=None, custom_sms2_status=None)]
        jour = J(2026, 11, 26)
        with mock.patch.object(frappe.db, "sql", return_value=lignes) as sql, \
                mock.patch.object(frappe.db, "set_value") as set_value:
            n = R.marquer_en_base("MS-X", [V("MS-X", "AP", "SMS2", "2026-11-20")], jour, "Failed")
        self.assertEqual(n, 1)
        self.assertEqual(sql.call_args.args[1], ("MS-X",))
        self.assertIn("ORDER BY idx", sql.call_args.args[0])
        set_value.assert_called_once_with("Maintenance Schedule Detail", "r2",
                                          {"custom_sms_2": jour, "custom_sms2_status": "Failed"}, update_modified=False)

    def test_rien_a_ecrire_si_aucune_ligne(self):
        with mock.patch.object(frappe.db, "sql", return_value=[]), \
                mock.patch.object(frappe.db, "set_value") as set_value:
            self.assertEqual(R.marquer_en_base("MS-DISPARU", [V("MS-DISPARU", "AP")], J(2026, 11, 26), "Success"), 0)
        set_value.assert_not_called()


class TestMarquerEnvoiRepli(unittest.TestCase):
    """marquer_envoi sans base : la fiche refuse save() → repli marquer_en_base, Error Log, pas de prolongation."""

    def _envoi(self):
        return R.regrouper_envois([[V("MS-A", "AP", "SMS1")], [V("MS-B", "PF", "SMS2", famille="PF")]], ORDRE, True)[0]

    def _lancer(self, en_base):
        class Fiche:
            def __init__(self, casse):
                self.casse, self.saves = casse, 0
                self.schedules = [frappe._dict(item_code="AP", scheduled_date=J(2026, 5, 20)),
                                  frappe._dict(item_code="PF", scheduled_date=J(2026, 5, 20))]

            def save(self):
                if self.casse:
                    raise frappe.ValidationError("refusé")
                self.saves += 1

        cache = {"MS-A": Fiche(True), "MS-B": Fiche(False)}
        stats = {"visites_marquees": 0, "erreurs": 0, "maintenances_extended": 0}
        prolonges = []
        with mock.patch.object(frappe.db, "savepoint"), mock.patch.object(frappe.db, "rollback"), \
                mock.patch.object(R, "marquer_en_base", en_base), \
                mock.patch.object(R, "extend_schedule_for_sms", lambda ms: prolonges.append(ms) or 0), \
                mock.patch.object(frappe, "log_error") as log_error:
            R.marquer_envoi(self._envoi(), "Success", J(2026, 11, 26), cache, set(), stats)
        return stats, prolonges, log_error, cache

    def test_fiche_refusee_marquee_sans_validation(self):
        en_base = mock.Mock(return_value=1)
        stats, prolonges, log_error, cache = self._lancer(en_base)
        en_base.assert_called_once()
        self.assertEqual(en_base.call_args.args[0], "MS-A")
        self.assertEqual([v["item_code"] for v in en_base.call_args.args[1]], ["AP"])
        self.assertEqual((stats["visites_marquees"], stats["erreurs"], stats["marquages_secours"]), (2, 1, 1))
        self.assertEqual(len(prolonges), 1)                                   # seul MS-B (sauvegardé) est prolongé
        self.assertNotIn("MS-A", cache)
        self.assertEqual(log_error.call_args.kwargs["title"], "SMS Maintenance — marquage MS-A")
        self.assertIn("1 visite(s) marquée(s) sans validation", log_error.call_args.kwargs["message"])

    def test_repli_en_echec_aussi_journalise_sans_arreter(self):
        en_base = mock.Mock(side_effect=RuntimeError("verrou"))
        stats, prolonges, log_error, _cache = self._lancer(en_base)
        self.assertEqual((stats["visites_marquees"], stats["erreurs"], stats.get("marquages_secours", 0)), (1, 1, 0))
        self.assertIn("en échec aussi", log_error.call_args.kwargs["message"])
        self.assertIn("verrou", log_error.call_args.kwargs["message"])


class TestPlafond(unittest.TestCase):
    def test_hors_secteur_toujours_et_secteurs_entiers(self):
        autorises, rep, total = R.secteurs_dans_le_plafond({"Secteur 1": 6, "Hors Secteur": 3, "Secteur 2": 4, "Secteur 3": 1}, 10)
        self.assertEqual(list(rep), ["Hors Secteur", "Secteur 1", "Secteur 2", "Secteur 3"])
        self.assertEqual((autorises, total), (["Hors Secteur", "Secteur 1", "Secteur 3"], 10))     # Secteur 2 ne tient pas
        self.assertEqual(R.secteurs_dans_le_plafond({"Hors Secteur": 12}, 10), (["Hors Secteur"], {"Hors Secteur": 12}, 12))

    def test_le_plafond_compte_un_sms_par_client(self):
        """12 visites de 4 clients du Secteur 1 : 4 SMS, le secteur tient sous un plafond de 5 (12 paquets ne tenaient pas)."""
        paquets = [[V("MS-%d-%d" % (c, i), "X", client="C%d" % c)] for c in range(4) for i in range(3)]
        with mock.patch.object(R, "log", lambda *a, **k: None):
            regroupe = R.calculer_secteurs_autorises(R.regrouper_envois(paquets, ORDRE, True), plafond=5)
            avant = R.calculer_secteurs_autorises(R.regrouper_envois(paquets, ORDRE, False), plafond=5)
            ancien_format = R.calculer_secteurs_autorises([{"X*?/*MS*?/*SMS1": [J(2026, 5, 20), "Secteur 1"]}], plafond=5)
        self.assertEqual(regroupe, (["Secteur 1"], {"Secteur 1": 4}, 4))
        self.assertEqual(avant, ([], {"Secteur 1": 12}, 0))
        self.assertEqual(ancien_format, (["Secteur 1"], {"Secteur 1": 1}, 1))


class TestExclusions(unittest.TestCase):
    MOTIFS = {"partenaire": {"P"}, "perdu": {"L", "PL"}, "a_requalifier": {"Q"}, "non_interesse": {"N", "P", "Q"},
              "reporte": {"Rp"}}

    def test_chaque_client_une_fois_sous_son_premier_motif(self):
        clients = {"P", "L", "PL", "Q", "N", "Rp", "Rdv", "X", "OK"}
        exclus = {"P", "L", "PL", "Q", "N", "Rp", "X"}
        compte = R.compter_exclusions(clients, {"Rdv", "Q"}, exclus, self.MOTIFS)
        self.assertEqual(compte, {"partenaire": 1, "perdu": 2, "a_requalifier": 1, "non_interesse": 1, "reporte": 1,
                                  "rendez_vous": 1, "autre": 1})
        self.assertEqual(sum(compte.values()), 8)                                    # « OK » n'est pas écarté
        self.assertEqual(list(compte), list(R.MOTIFS_EXCLUSION))

    def test_partenaire_seulement_les_siens_et_sans_motifs(self):
        self.assertEqual(R.compter_exclusions({"A", "B"}, set(), {"A", "B"}, self.MOTIFS)["partenaire"], 0)
        compte = R.compter_exclusions({"A", "B", "C"}, {"C"}, {"A"}, {})
        self.assertEqual((compte["autre"], compte["rendez_vous"], sum(compte.values())), (1, 1, 2))
        self.assertEqual(sum(R.compter_exclusions(set(), set(), set(), None).values()), 0)


class TestResumeEtEntonnoir(unittest.TestCase):
    def _summary(self):
        s = {"date": "2026-11-26", "un_sms_par_client": 1, "total_maintenance_schedules": 3261, "clients_avec_rdv": 138,
             "sms1_candidates": 8, "sms2_candidates": 11, "total_sms_candidates": 19, "total_sms_prevus": 18,
             "sms_sent_success": 17, "sms_invalid_number": 1, "sms_failed": 0, "emails_sent": 2, "emails_failed": 0,
             "maintenances_extended": 3, "envois_candidats": 18, "clients_plusieurs_sms": 1, "sms_evites": 1,
             "visites_candidates": 19, "visites_marquees": 19, "sms_hors_plafond": 0, "clients_exclus": 591,
             "exclus_par_motif": {"partenaire": 4, "perdu": 18, "a_requalifier": 290, "non_interesse": 126, "reporte": 39,
                                  "rendez_vous": 114, "autre": 0},
             "exclus_partenaire": 4, "erreurs": 0, "secteurs_autorises": ["Hors Secteur"], "repartition_sms_par_secteur": {"Hors Secteur": 1},
             "dry_run_flag": True}
        return s

    def test_ligne_resume_garde_les_champs_d_avant_en_tete(self):
        ligne = R.ligne_resume(self._summary())
        self.assertTrue(ligne.startswith(
            "[SUMMARY] date=2026-11-26, total_ms=3261, clients_avec_rdv=138, sms1_candidats=8, sms2_candidats=11, "
            "total_sms_candidats=19, total_sms_prevus=18, sms_envoyes=17, sms_invalid=1, sms_failed=0, emails_envoyes=2, "
            "emails_failed=0, ms_extendues=3, "))
        self.assertTrue(ligne.endswith(", marquages_secours=0, erreurs=0"))
        for morceau in ("sms_evites=1", "clients_exclus=591", "exclus_partenaire=4", "exclus_a_requalifier=290",
                        "exclus_rendez_vous=114", "erreurs=0"):
            self.assertIn(morceau, ligne)
        self.assertTrue(R.ligne_resume({}).startswith("[SUMMARY] date=None, total_ms=0"))   # résumé vide : pas d'erreur

    def test_compteurs_entonnoir_entiers_a_plat(self):
        c = R.compteurs_entonnoir(self._summary())
        self.assertTrue(all(isinstance(v, int) and not isinstance(v, bool) for v in c.values()))
        self.assertEqual((c["sms_sent_success"], c["exclus_partenaire"], c["exclus_perdu"], c["exclus_autre"]), (17, 4, 18, 0))
        for absent in ("date", "secteurs_autorises", "repartition_sms_par_secteur", "exclus_par_motif", "dry_run_flag"):
            self.assertNotIn(absent, c)

    def test_noter_entonnoir_contrat(self):
        module = types.ModuleType("customization_app.entonnoir_relances")
        module.noter = mock.Mock()
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": module}):
            self.assertTrue(R.noter_entonnoir(self._summary()))
        args, kwargs = module.noter.call_args
        self.assertEqual(args[0], "sms")
        self.assertEqual(args[1]["visites_marquees"], 19)
        self.assertEqual(kwargs, {"jour": "2026-11-26"})

    def test_noter_entonnoir_jamais_bloquant(self):
        with mock.patch.object(R, "log", lambda *a, **k: None), \
                mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": None}):
            self.assertFalse(R.noter_entonnoir(self._summary()))                     # module pas encore livré
        module = types.ModuleType("customization_app.entonnoir_relances")
        module.noter = mock.Mock(side_effect=RuntimeError("base indisponible"))
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": module}), \
                mock.patch.object(frappe, "log_error") as log_error:
            self.assertFalse(R.noter_entonnoir(self._summary()))
        self.assertEqual(log_error.call_args.kwargs.get("title"), "SMS Maintenance — entonnoir")

    def test_noter_entonnoir_rend_le_resultat_de_noter(self):
        """Table « Entonnoir Relances » pas encore migrée : noter() rend False, sans bruit ; on ne prétend pas avoir noté."""
        module = types.ModuleType("customization_app.entonnoir_relances")
        module.noter = mock.Mock(return_value=False)
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": module}):
            self.assertFalse(R.noter_entonnoir(self._summary()))
        module.noter.return_value = True
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": module}):
            self.assertTrue(R.noter_entonnoir(self._summary()))

    def test_noter_entonnoir_module_casse_au_chargement(self):
        """Une erreur autre qu'ImportError au chargement du module (en cours d'écriture par un autre lot) ne remonte pas
        au cron : elle part en Error Log, après le commit des marques."""
        import builtins
        vrai_import = builtins.__import__

        def faux_import(nom, *a, **k):
            if nom == "customization_app.entonnoir_relances":
                raise RuntimeError("module cassé")
            return vrai_import(nom, *a, **k)

        with mock.patch.object(builtins, "__import__", faux_import), \
                mock.patch.object(frappe, "log_error") as log_error:
            self.assertFalse(R.noter_entonnoir(self._summary()))
        self.assertEqual(log_error.call_args.kwargs.get("title"), "SMS Maintenance — entonnoir")


if __name__ == "__main__":
    unittest.main()
