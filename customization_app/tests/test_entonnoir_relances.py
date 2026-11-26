"""Entonnoir des relances (lot 6.1 du 25-26/11/2026) — règles PURES, sans lecture en base.

Pourquoi ce module : la baisse des listes d'appel d'octobre 2026 ne s'est vue qu'en novembre ; LIST-06-11-2026-00004
(Hors secteur) est restée en brouillon du 11/11 au 25/11 sans alerte ; 41 échéanciers n'avaient plus de visite à venir.
Ces essais fixent : le contrat noter() (fusion, ne lève jamais), le calcul des jours ouvrés (le même que l'alerte de
07:00), l'état des passages des crons, et chaque signal rouge / orange de la page."""
from __future__ import annotations

import datetime
import json
import unittest
from decimal import Decimal
from unittest import mock

import frappe

from customization_app import entonnoir_relances as ER

D = datetime.date
DT = datetime.datetime


def _reg(**k):
    return ER.reglages(dict(k))


def _flux_jour(**k):
    f = ER._flux_vide()
    f.update(k)
    return f


def _passage(cle, etats, titre=None, erreurs=0):
    return {"cle": cle, "titre": titre or cle, "installe": True, "erreurs_elements": erreurs, "exemples_erreurs": ["X"] if erreurs else [],
            "prevus": [{"heure": h, "prevu": "2026-11-25 " + h, "etat": e} for h, e in etats]}


class TestPropreEtFusion(unittest.TestCase):
    def test_propre_rend_du_json_sur(self):
        v = ER.propre({"d": D(2026, 11, 25), "t": DT(2026, 11, 25, 7, 0), "s": {"b", "a"}, "x": Decimal("1.5"),
                       "nan": float("nan"), "o": object(), 3: (1, 2)})
        json.dumps(v)                                     # ne lève pas
        self.assertEqual(v["d"], "2026-11-25")
        self.assertEqual(v["s"], ["a", "b"])
        self.assertEqual(v["x"], 1.5)
        self.assertIsNone(v["nan"])
        self.assertEqual(v["3"], [1, 2])

    def test_propre_profondeur_bornee(self):
        a = {}
        x = a
        for _ in range(20):
            x["n"] = {}
            x = x["n"]
        json.dumps(ER.propre(a))

    def test_fusion_additionne_les_nombres_et_remplace_le_reste(self):
        avant = {"envoyes": 30, "erreurs": 0, "cleanup": {"supprimes": 2}, "date": "2026-11-25", "simule": False, "noms": ["A"]}
        apres = {"envoyes": 0, "erreurs": 1, "cleanup": {"supprimes": 1, "corriges": 4}, "date": "2026-11-25", "simule": True,
                 "noms": ["A", "B"]}
        r = ER.fusionner(avant, apres)
        self.assertEqual(r["envoyes"], 30)              # une relance à la main n'efface pas les 30 SMS du cron
        self.assertEqual(r["erreurs"], 1)
        self.assertEqual(r["cleanup"], {"supprimes": 3, "corriges": 4})
        self.assertIs(r["simule"], True)                 # un vrai / faux n'est pas additionné
        self.assertEqual(r["noms"], ["A", "B"])

    def test_fusion_liste_bornee(self):
        r = ER.fusionner(list(range(40)), list(range(30, 80)))
        self.assertEqual(len(r), ER.LISTE_MAX)
        self.assertEqual(r[-1], 79)

    def test_maj_source_compte_les_passages(self):
        s = ER.maj_source({}, "sms", {"envoyes": 12}, "10:00:05")
        s = ER.maj_source(s, "sms", {"envoyes": 3}, "15:12:00")
        s = ER.maj_source(s, "listes", {"lignes": 4}, "07:00:10")
        self.assertEqual(s["sms"]["passages"], 2)
        self.assertEqual((s["sms"]["premier"], s["sms"]["dernier"]), ("10:00:05", "15:12:00"))
        self.assertEqual(s["sms"]["compteurs"], {"envoyes": 15})
        self.assertEqual(s["sms"]["dernier_passage"], {"envoyes": 3})
        self.assertEqual(s["listes"]["passages"], 1)

    def test_maj_source_resiste_a_un_ancien_contenu_abime(self):
        s = ER.maj_source({"sms": "texte"}, "sms", {"envoyes": 1}, "10:00:00")
        self.assertEqual(s["sms"]["passages"], 1)


class TestNoter(unittest.TestCase):
    """noter() : contrat partagé avec les crons — un enregistrement par jour, compteurs fusionnés, ne lève JAMAIS."""

    def setUp(self):
        self.ecrit = {}
        self.db = mock.MagicMock()
        self.db.set_value.side_effect = lambda dt, nom, champ, valeur, update_modified=True: self.ecrit.__setitem__(nom, json.loads(valeur))
        self.lignes = {}
        self.patches = [mock.patch.object(frappe, "db", self.db),
                        mock.patch.object(ER, "_table_prete", return_value=True),
                        mock.patch.object(ER, "now_datetime", return_value=DT(2026, 11, 25, 10, 0, 7)),
                        mock.patch.object(ER, "_ligne_du_jour", side_effect=lambda nom: self.lignes.get(nom) or {"name": nom, "sources": "{}"}),
                        mock.patch.object(frappe, "log_error")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_un_enregistrement_par_jour_et_fusion(self):
        self.assertTrue(ER.noter("sms", {"envoyes": 12, "exclus_perdu": 3}))
        self.assertEqual(self.ecrit["2026-11-25"]["sms"]["compteurs"], {"envoyes": 12, "exclus_perdu": 3})
        self.lignes["2026-11-25"] = {"name": "2026-11-25", "sources": json.dumps(self.ecrit["2026-11-25"])}
        self.assertTrue(ER.noter("sms", {"envoyes": 2}))
        self.assertTrue(ER.noter("nuit", {"new_ms_created": 4, "cleanup": {"erreurs": 0}}, jour="2026-11-24"))
        self.assertEqual(self.ecrit["2026-11-25"]["sms"]["compteurs"]["envoyes"], 14)
        self.assertEqual(self.ecrit["2026-11-25"]["sms"]["passages"], 2)
        self.assertEqual(self.ecrit["2026-11-24"]["nuit"]["compteurs"]["new_ms_created"], 4)   # le jour donné, pas aujourd'hui
        self.assertEqual(self.ecrit["2026-11-25"]["sms"]["premier"], "10:00:07")

    def test_valeurs_bizarres(self):
        self.assertTrue(ER.noter("listes", {"jour": D(2026, 11, 25), "clients": {"b", "a"}, "x": object()}))
        c = self.ecrit["2026-11-25"]["listes"]["compteurs"]
        self.assertEqual(c["jour"], "2026-11-25")
        self.assertEqual(c["clients"], ["a", "b"])
        self.assertTrue(ER.noter("reveil", 5))
        self.assertEqual(self.ecrit["2026-11-25"]["reveil"]["compteurs"], {"valeur": 5})
        self.assertTrue(ER.noter("", {}))
        self.assertIn("inconnu", self.ecrit["2026-11-25"])

    def test_ne_leve_jamais(self):
        self.db.set_value.side_effect = RuntimeError("verrou")
        self.assertFalse(ER.noter("sms", {"envoyes": 1}))
        self.db.rollback.assert_called_with(save_point="entonnoir_noter")    # rien de partiel
        frappe.log_error.assert_called()
        self.db.savepoint.side_effect = RuntimeError("pas de transaction")
        self.assertFalse(ER.noter("sms", {"envoyes": 1}))
        self.assertFalse(ER.noter("sms", {"envoyes": 1}, jour="pas une date"))

    def test_table_absente_rien_ecrit(self):
        with mock.patch.object(ER, "_table_prete", return_value=False):
            self.assertFalse(ER.noter("sms", {"envoyes": 1}))
        self.db.set_value.assert_not_called()


class TestJoursOuvres(unittest.TestCase):
    def test_vendredi(self):
        self.assertEqual(ER.jours_ouvres_entre("2026-11-20 15:00", D(2026, 11, 24)), 2)   # samedi, lundi
        self.assertEqual(ER.jours_ouvres_entre("2026-11-20", D(2026, 11, 25)), 3)
        self.assertEqual(ER.jours_ouvres_entre("2026-11-25 07:00", D(2026, 11, 25)), 0)
        self.assertEqual(ER.jours_ouvres_entre(None, D(2026, 11, 25)), 0)

    def test_liste_hors_secteur_du_11_11(self):
        # LIST-06-11-2026-00004 : créée le 06/11, plus touchée depuis le 11/11 — 11 j.o. pleins au 25/11, 15 depuis la création.
        self.assertEqual(ER.jours_ouvres_entre("2026-11-11 15:37", D(2026, 11, 25)), 11)
        self.assertEqual(ER.jours_ouvres_entre("2026-11-06 07:00", D(2026, 11, 25)), 15)

    def test_meme_regle_que_l_alerte_de_7h(self):
        from customization_app.Maintenance import creation_liste_appelle as CL
        if not hasattr(CL, "jours_ouvres_ecoules"):
            self.skipTest("alerte des brouillons immobiles absente")
        for i in range(0, 20):
            for j in range(i, i + 20):
                a, b = D(2026, 11, 1) + datetime.timedelta(days=i), D(2026, 11, 1) + datetime.timedelta(days=j)
                self.assertEqual(ER.jours_ouvres_entre(a, b), CL.jours_ouvres_ecoules(a, b), (a, b))


class TestStatutEtReference(unittest.TestCase):
    def test_statut_relance(self):
        j = D(2026, 11, 25)
        self.assertEqual(ER.statut_relance({"gere": 1, "statut": "Perdu"}, j), "partenaire")
        self.assertEqual(ER.statut_relance({"statut": "À requalifier", "interesse": "Non"}, j), "À requalifier")
        self.assertEqual(ER.statut_relance({"statut": "Perdu"}, j), "Perdu")
        self.assertEqual(ER.statut_relance({"interesse": "Non"}, j), "Non")
        self.assertEqual(ER.statut_relance({"interesse": "Non"}, j, relancer_non=True), "relançable")
        self.assertEqual(ER.statut_relance({"reporte_au": "2026-12-01"}, j), "reporté")
        self.assertEqual(ER.statut_relance({"reporte_au": "2026-11-25"}, j), "relançable")   # échu : à rappeler
        self.assertEqual(ER.statut_relance(None, j), "relançable")

    def test_reference_meme_jour(self):
        par_jour = {"2026-11-18": 17, "2026-11-11": 7, "2026-11-04": 14, "2026-10-28": 11, "2026-11-24": 0, "2026-10-21": 99}
        ref, vals, plafond = ER.reference_meme_jour(par_jour, D(2026, 11, 25), 4)
        self.assertEqual(vals, [17, 7, 14, 11])            # les 4 mercredis d'avant, pas le mardi ni le 5e
        self.assertAlmostEqual(ref, 12.25)
        self.assertIsNone(plafond)
        self.assertEqual(ER.reference_meme_jour({}, D(2026, 11, 25)), (None, [], None))
        # écrêtage à 2 × la médiane (12,5) : rien ne dépasse 25, la moyenne décidée reste la même
        self.assertEqual(ER.reference_meme_jour(par_jour, D(2026, 11, 25), 4, 2), (12.25, [17, 7, 14, 11], None))

    def test_reference_ecretee_liste_de_rattrapage(self):
        # Relecture du 26/11/2026, vraies valeurs de la copie : la liste de 100 lignes du vendredi 25/09/2026 portait la
        # référence des vendredis à 25,5, et le vendredi 09/10 (9 lignes) sortait rouge. Écrêtée à 2 × la médiane (1 → 2) :
        # moyenne 1,0, sous le minimum de 3, pas de signal. Rejoué du 20/09 au 25/11 : 22 jours rouges sur 57 → 4.
        par_jour = {"2026-10-02": 0, "2026-09-25": 100, "2026-09-18": 2, "2026-09-11": 0, "2026-10-09": 9}
        ref, vals, plafond = ER.reference_meme_jour(par_jour, D(2026, 10, 9), 4, 2)
        self.assertEqual(vals, [0, 100, 2, 0])             # valeurs brutes, affichées telles quelles
        self.assertEqual(plafond, 2)
        self.assertAlmostEqual(ref, 1.0)
        self.assertAlmostEqual(ER.reference_meme_jour(par_jour, D(2026, 10, 9), 4, 0)[0], 25.5)     # 0 = pas d'écrêtage
        # une moyenne ordinaire écrêtée : [5, 8, 4, 39] → médiane 6,5, plafond 13 → 7,5
        ref, vals, plafond = ER.reference_meme_jour({"2026-10-22": 5, "2026-10-15": 8, "2026-10-08": 4, "2026-10-01": 39}, D(2026, 10, 29), 4, 2)
        self.assertEqual((ref, plafond), (7.5, 13))

    def test_nombre(self):
        self.assertEqual(ER.nombre(12.25), "12,2")
        self.assertEqual(ER.nombre(7.35), "7,3")
        self.assertEqual(ER.nombre(None), "0,0")


class TestAppelsDesVersions(unittest.TestCase):
    def test_lignes_passees_a_appele(self):
        v1 = json.dumps({"row_changed": [["clients", 0, "ROW1", [["a_été_appelé", 0, 1], ["resume_appel", "", "Fait lui-même"]]],
                                         ["clients", 1, "ROW2", [["a_été_appelé", 1, 0]]],          # décochée : pas un appel
                                         ["autre_table", 0, "ROW3", [["a_été_appelé", 0, 1]]]]})
        v2 = json.dumps({"row_changed": [["clients", 0, "ROW1", [["a_été_appelé", 0, 1]]]],        # doublon du jour
                         "added": [["clients", {"name": "ROW4", "a_été_appelé": 1}], ["clients", {"name": "ROW5", "a_été_appelé": 0}]]})
        r = ER.appels_des_versions([("2026-11-23 09:00:00", v1), ("2026-11-23 11:00:00", v2), ("2026-11-24 08:00:00", "pas du json"),
                                    ("2026-11-24 08:00:00", "[]")])
        self.assertEqual(r, {"2026-11-23": {"ROW1", "ROW4"}})


class TestPassages(unittest.TestCase):
    def test_passages_prevus(self):
        self.assertEqual(ER.passages_prevus("0 7 * * 1-6", D(2026, 11, 22)), [])                     # dimanche
        self.assertEqual(ER.passages_prevus("0 7 * * 1-6", D(2026, 11, 23)), [DT(2026, 11, 23, 7, 0)])
        self.assertEqual(ER.passages_prevus("30 7,10 * * *", D(2026, 11, 22)), [DT(2026, 11, 22, 7, 30), DT(2026, 11, 22, 10, 30)])
        self.assertEqual(ER.passages_prevus("", D(2026, 11, 23), "Daily Long"), [DT(2026, 11, 23, 0, 0)])
        self.assertEqual(ER.passages_prevus("", D(2026, 11, 23), "Inconnue"), [])

    def test_etats(self):
        t = [DT(2026, 11, 25, 7, 0)]
        m = DT(2026, 11, 25, 12, 0)
        etat = lambda journaux, plantages=(), maintenant=m: ER.etat_passages(t, journaux, plantages, maintenant)[0]["etat"]
        self.assertEqual(etat([{"status": "Complete", "creation": DT(2026, 11, 25, 7, 0, 3)}]), "ok")
        self.assertEqual(etat([{"status": "Failed", "creation": DT(2026, 11, 25, 7, 0, 3)}]), "echec")
        self.assertEqual(etat([{"status": "Start", "creation": DT(2026, 11, 25, 7, 0, 3)}]), "interrompu")
        self.assertEqual(etat([{"status": "Start", "creation": DT(2026, 11, 25, 7, 0, 3)}], maintenant=DT(2026, 11, 25, 7, 20)), "en_cours")
        # run_safely avale l'exception : le journal dit « Complete », l'erreur « Cron - … » dit le contraire.
        self.assertEqual(etat([{"status": "Complete", "creation": DT(2026, 11, 25, 7, 0, 3)}], [DT(2026, 11, 25, 7, 4)]), "plantage")
        self.assertEqual(etat([{"status": "Complete", "creation": DT(2026, 11, 25, 7, 0, 3)}], [DT(2026, 11, 24, 7, 4)]), "ok")
        self.assertEqual(etat([]), "manquant")
        self.assertEqual(etat([], maintenant=DT(2026, 11, 25, 7, 30)), "a_venir")
        self.assertEqual(etat([{"status": "Complete", "creation": DT(2026, 11, 24, 7, 0)}]), "manquant")   # celui de la veille

    def test_jour_sms_a_controler(self):
        ok = {"passages": [_passage(ER.CLE_SMS, [("10:00", "ok")])]}
        venir = {"passages": [_passage(ER.CLE_SMS, [("10:00", "a_venir")])]}
        dim = {"passages": [dict(_passage(ER.CLE_SMS, []), prevus=[])]}
        fl = {"2026-11-23": ok, "2026-11-22": dim, "2026-11-21": ok}
        self.assertEqual(ER.jour_sms_a_controler(dict(fl, **{"2026-11-24": venir}), D(2026, 11, 24), DT(2026, 11, 24, 8, 0)), "2026-11-23")
        self.assertEqual(ER.jour_sms_a_controler(dict(fl, **{"2026-11-24": ok}), D(2026, 11, 24), DT(2026, 11, 24, 11, 0)), "2026-11-24")
        # lundi matin : dimanche n'a pas de passage, on contrôle samedi
        self.assertEqual(ER.jour_sms_a_controler({"2026-11-23": venir, "2026-11-22": dim, "2026-11-21": ok}, D(2026, 11, 23),
                                                 DT(2026, 11, 23, 8, 0)), "2026-11-21")
        # cron absent du planificateur : lundi–samedi après 10:30
        self.assertEqual(ER.jour_sms_a_controler({"2026-11-24": {"passages": []}}, D(2026, 11, 24), DT(2026, 11, 24, 10, 31)), "2026-11-24")
        self.assertIsNone(ER.jour_sms_a_controler({}, D(2026, 11, 24), DT(2026, 11, 24, 11, 0)))


class TestSignaux(unittest.TestCase):
    J = D(2026, 11, 25)                     # un mercredi
    M = DT(2026, 11, 25, 10, 35)

    def flux(self, lignes_jour=12, sms=(11, 6), passages=None):
        """28 jours ordinaires (12 lignes, 15 SMS, passage de 10:00 fait sauf le dimanche) puis le jour essayé."""
        fl = {}
        for k in range(1, 29):
            d = self.J - datetime.timedelta(days=k)
            fl[str(d)] = _flux_jour(lignes_flux=12, sms1=10, sms2=5,
                                    passages=[_passage(ER.CLE_SMS, [] if d.weekday() == 6 else [("10:00", "ok")])])
        fl[str(self.J)] = _flux_jour(lignes_flux=lignes_jour, sms1=sms[0], sms2=sms[1],
                                     passages=passages if passages is not None else [_passage(ER.CLE_LISTES, [("07:00", "ok")]),
                                                                                     _passage(ER.CLE_SMS, [("10:00", "ok")])])
        return fl

    def photo(self, **k):
        p = {"jour": str(self.J), "brouillons": [], "sans_visite_a_venir": {"echeanciers": 0, "a_prolonger": 0}, "appel": None,
             "flux": {"veille": _flux_jour(), "jour": _flux_jour()}}
        p.update(k)
        return p

    def cles(self, sig, niveau=None):
        return [s["cle"] for s in sig if niveau is None or s["niveau"] == niveau]

    def test_tout_va_bien(self):
        self.assertEqual(ER.signaux(self.photo(), self.flux(), _reg(), self.M), [])

    def test_brouillon_immobile_rouge_et_travaille_orange(self):
        bs = [{"name": "LIST-06-11-2026-00004", "type": "Hors secteur", "cree_le": "2026-11-06 07:00", "age_jo": 15, "inactif_jo": 11,
               "lignes": 100, "appelees": 45},
              {"name": "LIST-B", "type": "Normal", "cree_le": "2026-11-20 07:00", "age_jo": 3, "inactif_jo": 0, "lignes": 10, "appelees": 2},
              {"name": "LIST-C", "type": "Urgence", "cree_le": "2026-11-24 07:00", "age_jo": 0, "inactif_jo": 0, "lignes": 1, "appelees": 0}]
        appel = {"a_appeler": [{"type": "Hors secteur", "clients": 183, "en_attente": 129}]}
        sig = ER.signaux(self.photo(brouillons=bs, appel=appel), self.flux(), _reg(), self.M)
        rouge = [s for s in sig if s["cle"] == "brouillon:LIST-06-11-2026-00004"][0]
        self.assertEqual(rouge["niveau"], "rouge")
        self.assertIn("immobile depuis 11 jour(s) ouvré(s)", rouge["texte"])
        self.assertIn("129 client(s) attendent", rouge["texte"])
        self.assertIn("aucune nouvelle liste « Hors secteur »", rouge["texte"])
        self.assertEqual([s["niveau"] for s in sig if s["cle"] == "brouillon:LIST-B"], ["orange"])
        self.assertNotIn("brouillon:LIST-C", self.cles(sig))
        self.assertEqual(sig[0]["niveau"], "rouge")                      # rouges d'abord
        # seuil à 0 : signal coupé
        self.assertEqual(self.cles(ER.signaux(self.photo(brouillons=bs), self.flux(), _reg(jours_brouillon_alerte=0), self.M)), [])

    def test_brouillon_urgence_orange(self):
        # Relecture du 26/11/2026 : l'Urgence ne bloque aucune liste (alerte de 07:00 : TYPES_BLOQUES_PAR_BROUILLON) ;
        # immobile, elle reste orange, sans e-mail. Le même brouillon en « Normal » est rouge.
        b = {"name": "LIST-U", "type": "Urgence", "cree_le": "2026-11-18 07:00", "age_jo": 5, "inactif_jo": 4, "lignes": 2, "appelees": 0}
        sig = ER.signaux(self.photo(brouillons=[b]), self.flux(), _reg(), self.M)
        self.assertEqual([(s["cle"], s["niveau"]) for s in sig], [("brouillon:LIST-U", "orange")])
        self.assertIn("ne bloque aucune liste", sig[0]["texte"])
        sig = ER.signaux(self.photo(brouillons=[dict(b, type="Normal")]), self.flux(), _reg(), self.M)
        self.assertEqual([s["niveau"] for s in sig], ["rouge"])

    def test_seuil_des_brouillons_celui_de_l_alerte_de_7h(self):
        # Un seul réglage pour la même règle : jours_brouillon_alerte (creation_liste_appelle).
        b = {"name": "LIST-N", "type": "Normal", "cree_le": "2026-11-18 07:00", "age_jo": 4, "inactif_jo": 4, "lignes": 2, "appelees": 0}
        self.assertEqual(self.cles(ER.signaux(self.photo(brouillons=[b]), self.flux(), _reg(jours_brouillon_alerte=5), self.M)), [])
        self.assertEqual(self.cles(ER.signaux(self.photo(brouillons=[b]), self.flux(), _reg(jours_brouillon_alerte=4), self.M), "rouge"),
                         ["brouillon:LIST-N"])
        from customization_app.Maintenance import creation_liste_appelle as CL
        self.assertEqual(_reg()["seuil_brouillon_jo"], CL.JOURS_BROUILLON_ALERTE)
        self.assertEqual(set(_reg()["types_bloquants"]), set(CL.TYPES_BLOQUES_PAR_BROUILLON))
        self.assertNotIn("Urgence", _reg()["types_bloquants"])

    def test_brouillon_d_un_jour_passe_sans_immobilite(self):
        bs = [{"name": "LIST-X", "type": "Normal", "cree_le": "2026-11-18 07:00", "age_jo": 5, "inactif_jo": None, "lignes": 3, "appelees": 0}]
        sig = ER.signaux(self.photo(brouillons=bs), self.flux(), _reg(), self.M)
        self.assertEqual([s["niveau"] for s in sig], ["rouge"])
        self.assertIn("ouvert depuis 5 jour(s) ouvré(s) ce jour-là", sig[0]["texte"])   # pas « immobile » : inconnu

    def test_echeanciers_sans_visite_hors_geles(self):
        sv = {"echeanciers": 41, "a_prolonger": 36, "geles": 5, "relancables": 29}
        sig = ER.signaux(self.photo(sans_visite_a_venir=sv), self.flux(), _reg(), self.M)
        self.assertEqual(self.cles(sig, "rouge"), ["sans_visite"])
        self.assertIn("36 échéancier(s)", sig[0]["texte"])
        self.assertIn("5 autre(s) laissé(s) gelé(s)", sig[0]["texte"])
        # après la nuit : il ne reste que les gelés voulus → aucun signal
        sv = {"echeanciers": 5, "a_prolonger": 0, "geles": 5}
        self.assertEqual(ER.signaux(self.photo(sans_visite_a_venir=sv), self.flux(), _reg(), self.M), [])
        # règle des gelés inconnue (ancienne nuit) : tous comptent
        self.assertEqual(self.cles(ER.signaux(self.photo(sans_visite_a_venir={"echeanciers": 2}), self.flux(), _reg(), self.M)), ["sans_visite"])

    def test_passage_ko_et_planificateur(self):
        p = [_passage(ER.CLE_LISTES, [("07:00", "plantage")], "Listes d’appels"), _passage(ER.CLE_SMS, [("10:00", "ok")]),
             _passage("update_schedule.run_cron", [("00:00", "manquant")], "Échéanciers", erreurs=2)]
        sig = ER.signaux(self.photo(), self.flux(passages=p), _reg(), self.M)
        rouges = self.cles(sig, "rouge")
        self.assertIn("passage:%s:2026-11-25 07:00" % ER.CLE_LISTES, rouges)
        self.assertIn("passage:update_schedule.run_cron:2026-11-25 00:00", rouges)
        self.assertIn("erreurs:update_schedule.run_cron:2026-11-25", self.cles(sig, "orange"))
        # planificateur à l'arrêt : un seul signal, pas un « manquant » par cron
        sig = ER.signaux(self.photo(), self.flux(passages=p), _reg(), self.M, ordonnanceur_inactif=True)
        rouges = self.cles(sig, "rouge")
        self.assertIn("ordonnanceur", rouges)
        self.assertNotIn("passage:update_schedule.run_cron:2026-11-25 00:00", rouges)
        self.assertIn("passage:%s:2026-11-25 07:00" % ER.CLE_LISTES, rouges)        # un plantage reste un plantage

    def test_aucun_sms(self):
        sig = ER.signaux(self.photo(), self.flux(sms=(0, 0)), _reg(), self.M)
        self.assertEqual(self.cles(sig, "rouge"), ["sms:2026-11-25"])
        # 08:00 : le passage de 10:00 n'a pas eu lieu, on contrôle la veille (qui a eu ses SMS)
        p = [_passage(ER.CLE_LISTES, [("07:00", "ok")]), _passage(ER.CLE_SMS, [("10:00", "a_venir")])]
        self.assertEqual(ER.signaux(self.photo(), self.flux(sms=(0, 0), passages=p), _reg(), DT(2026, 11, 25, 8, 0)), [])

    def test_aucun_sms_le_dimanche_n_est_pas_un_signal(self):
        dim = D(2026, 11, 22)
        fl = {str(dim - datetime.timedelta(days=k)): _flux_jour(sms1=5, passages=[_passage(ER.CLE_SMS, [("10:00", "ok")])]) for k in range(1, 8)}
        fl[str(dim)] = _flux_jour(passages=[dict(_passage(ER.CLE_SMS, []), prevus=[])])
        p = dict(self.photo(), jour=str(dim))
        self.assertEqual(ER.signaux(p, fl, _reg(), DT(2026, 11, 22, 11, 0)), [])

    def test_lignes_en_baisse(self):
        sig = ER.signaux(self.photo(), self.flux(lignes_jour=4), _reg(), self.M)
        self.assertEqual(self.cles(sig, "rouge"), ["lignes:2026-11-25"])
        self.assertIn("4, contre 12,0 en moyenne les 4 derniers mercredis", sig[0]["texte"])
        self.assertEqual(ER.signaux(self.photo(), self.flux(lignes_jour=8), _reg(), self.M), [])           # 8 ≥ 7,2
        # le mardi est vide par construction : moyenne sous le minimum → pas de signal
        fl = self.flux(lignes_jour=0)
        for k in range(1, 5):
            fl[str(self.J - datetime.timedelta(days=7 * k))]["lignes_flux"] = 1
        self.assertEqual(ER.signaux(self.photo(), fl, _reg(), self.M), [])
        # avant le passage de 07:00 : rien à comparer
        p = [_passage(ER.CLE_LISTES, [("07:00", "a_venir")]), _passage(ER.CLE_SMS, [("10:00", "a_venir")])]
        self.assertEqual(self.cles(ER.signaux(self.photo(), self.flux(lignes_jour=0, passages=p), _reg(), DT(2026, 11, 25, 6, 50)), "rouge"), [])
        self.assertEqual(ER.signaux(self.photo(), self.flux(lignes_jour=0), _reg(entonnoir_seuil_lignes_pct=0), self.M), [])

    def test_lignes_liste_de_rattrapage_ecretee(self):
        fl = self.flux(lignes_jour=12)
        fl[str(self.J - datetime.timedelta(days=7))]["lignes_flux"] = 100          # liste au plafond il y a une semaine
        self.assertEqual(ER.signaux(self.photo(), fl, _reg(), self.M), [])          # (24 + 12 × 3) / 4 = 15 → seuil 9
        sig = ER.signaux(self.photo(), fl, _reg(entonnoir_ecretage_reference=0), self.M)
        self.assertEqual(self.cles(sig, "rouge"), ["lignes:2026-11-25"])              # sans écrêtage : 34 → seuil 20,4
        fl[str(self.J)]["lignes_flux"] = 4
        sig = ER.signaux(self.photo(), fl, _reg(), self.M)
        self.assertEqual(self.cles(sig, "rouge"), ["lignes:2026-11-25"])
        self.assertIn("valeurs ramenées à 24,0 (2 × la médiane)", sig[0]["texte"])

    def test_sms_tous_en_echec_panne_winsms(self):
        # Relecture du 26/11/2026 : de décembre 2025 à avril 2026, tous les envois de la copie sont « Failed » ; les visites
        # portaient quand même leur date de SMS, et l'ancien compte voyait une journée normale.
        fl = self.flux(sms=(0, 0))
        fl[str(self.J)].update(sms1_echec=12, sms2_echec=3, sms1_invalide=1)
        sig = ER.signaux(self.photo(), fl, _reg(), self.M)
        self.assertEqual(self.cles(sig, "rouge"), ["sms:2026-11-25"])
        self.assertIn("Aucun SMS de relance RÉUSSI", sig[0]["texte"])
        self.assertIn("15 envoi(s) en échec", sig[0]["texte"])
        self.assertIn("crédit WinSMS", sig[0]["texte"])

    def test_sms_en_partie_en_echec(self):
        fl = self.flux(sms=(3, 0))
        fl[str(self.J)].update(sms1_echec=12)                         # 80 % d'échecs ≥ 50 % : rouge
        sig = ER.signaux(self.photo(), fl, _reg(), self.M)
        self.assertEqual([(s["cle"], s["niveau"]) for s in sig], [("sms_echecs:2026-11-25", "rouge")])
        self.assertIn("12 en échec (« Failed ») sur 15 envoi(s), soit 80 %", sig[0]["texte"])
        fl = self.flux(sms=(11, 0))
        fl[str(self.J)].update(sms1_echec=2, sms1_invalide=5)         # 2 / 13 = 15 % : orange ; les sans-numéro ne comptent pas
        sig = ER.signaux(self.photo(), fl, _reg(), self.M)
        self.assertEqual([(s["cle"], s["niveau"]) for s in sig], [("sms_echecs:2026-11-25", "orange")])
        self.assertEqual(ER.signaux(self.photo(), fl, _reg(entonnoir_seuil_echecs_sms_pct=0), self.M), [])
        fl[str(self.J)].update(sms1_echec=0)
        self.assertEqual(ER.signaux(self.photo(), fl, _reg(), self.M), [])          # numéros invalides seuls : pas de signal

    def _arrete(self, cle, **k):
        return dict({"cle": cle, "titre": "Relance SMS / e-mail", "installe": False, "arrete": True, "absent": False,
                     "prevus": [], "erreurs_elements": 0}, **k)

    def test_cron_sms_arrete_un_seul_signal_stable(self):
        # Relecture du 26/11/2026 : « Stopped » coché, le repli lundi–samedi envoyait chaque jour un rouge « aucun SMS »
        # neuf (un e-mail par jour) et rien ne disait « arrêté ».
        fl = self.flux(sms=(0, 0), passages=[_passage(ER.CLE_LISTES, [("07:00", "ok")]), self._arrete(ER.CLE_SMS)])
        for k in range(1, 29):
            fl[str(self.J - datetime.timedelta(days=k))].update(sms1=0, sms2=0, passages=[self._arrete(ER.CLE_SMS)])
        sig = ER.signaux(self.photo(), fl, _reg(), self.M)
        self.assertEqual([(s["cle"], s["niveau"]) for s in sig], [("arret:%s" % ER.CLE_SMS, "rouge")])
        self.assertIn("arrêté (« Stopped »)", sig[0]["texte"])
        t = ER.tendance(self.J, 3, fl, {}, self.M)
        self.assertEqual([r["sms_ko"] for r in t], [False, False, False])         # un cron arrêté n'attend pas de SMS

    def test_cron_absent_repli_garde(self):
        absent = self._arrete(ER.CLE_SMS, arrete=False, absent=True)
        fl = self.flux(sms=(0, 0), passages=[_passage(ER.CLE_LISTES, [("07:00", "ok")]), absent])
        sig = ER.signaux(self.photo(), fl, _reg(), self.M)
        self.assertEqual(self.cles(sig, "rouge"), ["arret:%s" % ER.CLE_SMS, "sms:2026-11-25"])   # repli lundi–samedi
        self.assertIn("absent du planificateur", sig[0]["texte"])
        self.assertIsNone(ER._resolu({"passages": [absent]}, ER.CLE_SMS))
        self.assertFalse(ER._resolu({"passages": [self._arrete(ER.CLE_SMS)]}, ER.CLE_SMS))

    def test_machine_sans_echeancier(self):
        veille = _flux_jour()
        veille["machines"] = dict(veille["machines"], sans=["MAT-DN-2026-01234"])
        sig = ER.signaux(self.photo(flux={"veille": veille, "jour": _flux_jour()}), self.flux(), _reg(), self.M)
        self.assertEqual([(s["cle"], s["niveau"]) for s in sig], [("machines:2026-11-24", "orange")])


class TestScalairesTendanceReglages(unittest.TestCase):
    def test_scalaires(self):
        photo = {"visites_echues": {"lignes": 4574, "par_statut": {"relançable": {"lignes": 2561}}},
                 "appel": {"candidats": 412, "a_appeler": [{"clients": 4, "en_attente": 0}, {"clients": 183, "en_attente": 129}]},
                 "sans_visite_a_venir": {"echeanciers": 41, "a_prolonger": 36},
                 "brouillons": [{"age_jo": 15}, {"age_jo": 0}], "flux": {"jour": {"lignes": 5, "sms1": 11, "sms2": 6}}}
        s = ER.scalaires(photo, [{"niveau": "rouge"}, {"niveau": "orange"}], _reg())
        self.assertEqual(s, {"visites_echues": 4574, "visites_relancables": 2561, "candidats": 412, "a_appeler": 187, "en_attente": 129,
                             "echeanciers_sans_visite": 36, "brouillons_anciens": 0, "lignes_creees": 5, "sms_envoyes": 17, "sms_echecs": 0,
                             "nb_signaux": 1})
        # brouillons bloqués = signaux rouges « brouillon » ; SMS réussis et en échec séparés
        photo["flux"]["jour"].update(sms1_echec=4, sms2_invalide=2)
        s = ER.scalaires(photo, [{"niveau": "rouge", "cle": "brouillon:A"}, {"niveau": "orange", "cle": "brouillon:B"}], _reg())
        self.assertEqual((s["brouillons_anciens"], s["sms_envoyes"], s["sms_echecs"]), (1, 17, 4))
        self.assertEqual(set(ER.scalaires({}, [], {})), set(s))         # une photo vide ne plante pas

    def test_tendance(self):
        j = D(2026, 11, 25)
        fl = {"2026-11-25": _flux_jour(lignes=5, sms1=11, passages=[_passage(ER.CLE_SMS, [("10:00", "ok")])]),
              "2026-11-24": _flux_jour(lignes=5, passages=[_passage(ER.CLE_SMS, [("10:00", "ok")])]),
              "2026-11-22": _flux_jour(passages=[dict(_passage(ER.CLE_SMS, []), prevus=[])])}
        fl["2026-11-24"]["machines"] = dict(fl["2026-11-24"]["machines"], lignes=3)
        stockes = {"2026-11-25": {"name": "2026-11-25", "jour": "2026-11-25", "photo_le": "2026-11-25 07:30:02", "visites_echues": 4574,
                                  "sources": {"x": 1}},
                   "2026-11-24": {"name": "2026-11-24", "photo_le": None, "visites_echues": 0, "sources": {"sms": {}}}}
        t = ER.tendance(j, 4, fl, stockes, DT(2026, 11, 25, 12, 0))
        self.assertEqual([r["jour"] for r in t], ["2026-11-25", "2026-11-24", "2026-11-23", "2026-11-22"])
        self.assertEqual(t[0]["machines_veille"], 3)                     # BL de la veille, échéanciers de la nuit
        self.assertEqual(t[0]["stock"], {"photo_le": "2026-11-25 07:30:02", "visites_echues": 4574})   # sans les sources
        self.assertIsNone(t[1]["stock"])                                 # ligne née d'un noter() sans photo : pas de stock
        self.assertIsNone(t[2]["stock"])
        self.assertFalse(t[0]["sms_ko"])
        self.assertTrue(t[1]["sms_ko"])                                  # passage fait, aucun SMS
        self.assertTrue(t[2]["sms_ko"])                                  # lundi sans trace du planificateur
        self.assertFalse(t[3]["sms_ko"])                                 # dimanche
        self.assertTrue(t[3]["dimanche"])

    def test_reglages(self):
        r = ER.reglages({"jours_brouillon_alerte": "5", "entonnoir_types_flux": "", "entonnoir_seuil_lignes_pct": None})
        self.assertEqual(r["seuil_brouillon_jo"], 5)
        self.assertEqual(r["entonnoir_seuil_lignes_pct"], 60)
        self.assertEqual(r["types_flux"], ["Normal", "Urgence"])
        self.assertEqual((r["entonnoir_ecretage_reference"], r["entonnoir_seuil_echecs_sms_pct"]), (2, 50))
        self.assertEqual(ER.reglages({"jours_brouillon_alerte": 0})["seuil_brouillon_jo"], 0)   # 0 coupe le signal
        self.assertNotIn("entonnoir_seuil_brouillon_jo", r)                                     # plus de doublon


class TestResumeSansVisite(unittest.TestCase):
    ROWS = [{"parent": "MS1", "item_code": "A", "customer": "C1"}, {"parent": "MS1", "item_code": "B", "customer": "C1"},
            {"parent": "MS2", "item_code": "A", "customer": "C2"}, {"parent": "MS3", "item_code": "A", "customer": "C3"}]

    def test_geles_comptes_a_part(self):
        r = ER.resume_sans_visite(self.ROWS, {"MS1", "MS9"}, {"C1": {}, "C2": {"statut": "Perdu"}}, D(2026, 11, 25))
        self.assertEqual((r["echeanciers"], r["articles"], r["clients"]), (3, 4, 3))
        self.assertEqual((r["a_prolonger"], r["geles"], r["relancables"]), (1, 2, 1))
        self.assertEqual(r["exemples"], ["MS1"])
        self.assertEqual(r["exemples_geles"], ["MS2", "MS3"])

    def test_sans_regle_des_geles(self):
        r = ER.resume_sans_visite(self.ROWS, None, {"C2": {"statut": "Perdu"}}, D(2026, 11, 25))
        self.assertEqual((r["a_prolonger"], r["geles"], r["relancables"], r["regle_gel"]), (3, 0, 2, False))
        self.assertEqual(ER.resume_sans_visite([], set(), {}, D(2026, 11, 25))["a_prolonger"], 0)


class TestAlerte(unittest.TestCase):
    def test_corps_echappe_le_html(self):
        c = ER.corps_alerte([{"texte": "Brouillon <b>X</b>"}], {"jour": "2026-11-25", "calcule_le": "2026-11-25 07:30:02"})
        self.assertIn("&lt;b&gt;X&lt;/b&gt;", c)
        self.assertIn("mer. 25/11 à 07:30", c)
        self.assertIn("/app/journaux-relances", c)



class TestSmsParStatut(unittest.TestCase):
    def test_classe_statut(self):
        self.assertEqual(ER.classe_statut_sms("Success"), "ok")
        self.assertEqual(ER.classe_statut_sms(""), "ok")             # lignes de 2023, d'avant le statut
        self.assertEqual(ER.classe_statut_sms(None), "ok")
        self.assertEqual(ER.classe_statut_sms("Invalid Number"), "invalide")
        self.assertEqual(ER.classe_statut_sms("Failed"), "echec")
        self.assertEqual(ER.classe_statut_sms("Inconnu"), "echec")    # un statut inconnu ne cache pas une panne

    def test_sms_du_jour_et_tendance(self):
        f = _flux_jour(sms1=3, sms2=1, sms1_echec=12, sms2_echec=2, sms1_invalide=1)
        self.assertEqual(ER.sms_du_jour(f), {"ok": 4, "echec": 14, "invalide": 1, "envois": 18})
        j = D(2026, 11, 25)
        fl = {"2026-11-25": _flux_jour(sms1_echec=15, passages=[_passage(ER.CLE_SMS, [("10:00", "ok")])])}
        t = ER.tendance(j, 1, fl, {}, DT(2026, 11, 25, 12, 0))[0]
        self.assertTrue(t["sms_ko"])                                  # 15 visites marquées, aucune réussie : journée KO
        self.assertEqual((t["sms1"], t["sms_echecs"]), (0, 15))


class TestPlantages(unittest.TestCase):
    """Relecture du 26/11/2026 : seul le titre EXACT du filet run_safely dit « planté »."""

    CRON = {"cle": ER.CLE_LISTES, "titre": "Listes d’appels", "filet": ER.TITRES_FILET[ER.CLE_LISTES],
            "motifs": ("2e appel%", "Cron - Génération listes%")}

    def test_est_plantage(self):
        self.assertTrue(ER.est_plantage("Cron - Génération listes d'appels", self.CRON))
        for titre in ("Cron - Génération listes : alerte du brouillon LIST-06-11-2026-00004", "Cron - Génération listes : alertes des brouillons",
                      "Cron - Génération listes : entonnoir non mémorisé", "2e appel : LIST-X"):
            self.assertFalse(ER.est_plantage(titre, self.CRON), titre)
        # sans filet connu : un « Cron - … » sans « : » après le nom
        sans = {"motifs": ("Cron - Validation auto%",)}
        self.assertTrue(ER.est_plantage("Cron - Validation auto listes d'appels", sans))
        self.assertFalse(ER.est_plantage("Cron - Validation auto : liste LIST-1", sans))

    def test_titres_filet_ceux_des_crons(self):
        # Les titres recopiés sont bien ceux que les modules passent à run_safely.
        import inspect
        import re

        from customization_app import qualification_appels, rappel_rdv
        from customization_app.Maintenance import creation_liste_appelle, relance_maintenance_sms, sms_reveil, update_schedule
        mods = {"update_schedule.run_cron": update_schedule, "creation_liste_appelle.run_cron": creation_liste_appelle,
                "relance_maintenance_sms.run_cron": relance_maintenance_sms, "rappel_rdv.cron_du_soir": rappel_rdv,
                "qualification_appels.run_cron": qualification_appels, "sms_reveil.run_cron": sms_reveil, "entonnoir_relances.cron_photo": ER}
        for cle, mod in mods.items():
            titres = re.findall(r"run_safely\(\s*\"([^\"]+)\"", inspect.getsource(mod))
            self.assertIn(ER.TITRES_FILET[cle], titres, cle)
        self.assertEqual({c["cle"]: c["filet"] for c in ER.crons_suivis()}, ER.TITRES_FILET)

    def _charge(self, erreurs, stopped=0, present=True):
        t = {"name": ER.CLE_LISTES, "cron_format": "0 7 * * 1-6", "frequency": "Cron", "stopped": stopped}
        return {"crons": [self.CRON], "types": {ER.CLE_LISTES: t} if present else {},
                "journaux": {ER.CLE_LISTES: [{"status": "Complete", "creation": DT(2026, 11, 25, 7, 0, 3)}]},
                "erreurs": [{"method": m, "creation": c} for m, c in erreurs]}

    def test_erreur_d_element_pas_un_plantage(self):
        ch = self._charge([("Cron - Génération listes : alerte du brouillon LIST-06-11-2026-00004", DT(2026, 11, 25, 7, 1))])
        p = ER._passages_du_jour(D(2026, 11, 25), ch, DT(2026, 11, 25, 12, 0))[0]
        self.assertEqual([h["etat"] for h in p["prevus"]], ["ok"])
        self.assertEqual(p["erreurs_elements"], 1)
        ch = self._charge([("Cron - Génération listes d'appels", DT(2026, 11, 25, 7, 4))])
        p = ER._passages_du_jour(D(2026, 11, 25), ch, DT(2026, 11, 25, 12, 0))[0]
        self.assertEqual([h["etat"] for h in p["prevus"]], ["plantage"])
        self.assertEqual(p["erreurs_elements"], 0)

    def test_pas_de_passage_attendu_avant_la_naissance_de_la_tache(self):
        # Relecture finale du 26/11/2026 : la tâche planifiée née le 25/11 à 07:30 (déploiement) n'attendait pas de passage
        # le 24/11 ni à 07:00 le 25/11 — avant, la 1re photo criait « pas de passage » pour elle-même.
        ch = self._charge([])
        ch["types"][ER.CLE_LISTES]["creation"] = DT(2026, 11, 25, 7, 30)
        self.assertEqual(ER._passages_du_jour(D(2026, 11, 24), ch, DT(2026, 11, 25, 12, 0))[0]["prevus"], [])
        self.assertEqual(ER._passages_du_jour(D(2026, 11, 25), ch, DT(2026, 11, 25, 12, 0))[0]["prevus"], [])
        self.assertEqual(len(ER._passages_du_jour(D(2026, 11, 26), ch, DT(2026, 11, 26, 12, 0))[0]["prevus"]), 1)

    def test_arrete_et_absent(self):
        p = ER._passages_du_jour(D(2026, 11, 25), self._charge([], stopped=1), DT(2026, 11, 25, 12, 0))[0]
        self.assertEqual((p["installe"], p["arrete"], p["absent"], p["prevus"]), (False, True, False, []))
        p = ER._passages_du_jour(D(2026, 11, 25), self._charge([], present=False), DT(2026, 11, 25, 12, 0))[0]
        self.assertEqual((p["installe"], p["arrete"], p["absent"]), (False, False, True))


class TestRdvSansVersion(unittest.TestCase):
    def test_ajout_au_jour_de_la_tache(self):
        # LIST-24-11-2026-00001 : la ligne 4396jao4f8 prend son RDV (Tache-09562) le 24/11 à 10:03 par db.set_value,
        # aucune Version ne la montre ; la ligne ROW1 est déjà comptée le 23/11 par sa Version.
        appels = {"2026-11-23": {"ROW1"}}
        ajout = ER.ajouter_rdv_sans_version(appels, [("4396jao4f8", DT(2026, 11, 24, 10, 3, 46)), ("ROW1", DT(2026, 11, 24, 9, 0)),
                                                     ("ROW2", DT(2026, 11, 23, 15, 0)), ("ROW2", DT(2026, 11, 23, 16, 0)), (None, None)],
                                            marques={"ROW1", "ROW9"})
        self.assertEqual(ajout, {"2026-11-24": 1, "2026-11-23": 1})
        self.assertEqual(appels, {"2026-11-23": {"ROW1", "ROW2"}, "2026-11-24": {"4396jao4f8"}})


class TestAlerteRougeNouveau(unittest.TestCase):
    """Relecture du 26/11/2026 : un orange qui devient rouge envoie son e-mail ; rouge → orange → rouge en envoie deux."""

    def setUp(self):
        self.ligne = {"name": "2026-11-16", "sources": "{}", "signaux": None}
        self.alertes, self.suite = [], []
        db = mock.MagicMock()

        def set_value(dt, nom, valeurs, update_modified=True):
            self.ligne.update(valeurs)
        db.set_value.side_effect = set_value
        db.sql.return_value = []
        self.patches = [mock.patch.object(frappe, "db", db),
                        mock.patch.object(ER.RC, "config", return_value={}),
                        mock.patch.object(ER.RC, "alerter", side_effect=lambda sujet, corps, cfg=None: self.alertes.append(sujet) or True),
                        mock.patch.object(ER, "now_datetime", return_value=DT(2026, 11, 16, 7, 30)),
                        mock.patch.object(ER, "flux", return_value={}),
                        mock.patch.object(ER, "indicateurs", return_value={"jour": "2026-11-16", "calcule_le": "2026-11-16 07:30:00"}),
                        mock.patch.object(ER, "signaux", side_effect=lambda *a, **k: self.suite.pop(0)),
                        mock.patch.object(ER, "_table_prete", return_value=True),
                        mock.patch.object(ER, "_ligne_du_jour", side_effect=lambda nom: dict(self.ligne))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def sig(self, niveau):
        return [{"cle": "brouillon:LIST-06-11-2026-00004", "niveau": niveau, "texte": "Brouillon LIST-06-11-2026-00004"}]

    def test_orange_puis_rouge_un_email(self):
        self.suite = [self.sig("orange"), self.sig("rouge"), self.sig("rouge")]
        self.assertEqual(ER.enregistrer_photo()["nouveaux"], 0)
        self.assertEqual(ER.enregistrer_photo()["nouveaux"], 1)
        self.assertEqual(ER.enregistrer_photo()["nouveaux"], 0)           # rouge qui dure : pas de deuxième e-mail
        self.assertEqual(len(self.alertes), 1)

    def test_rouge_orange_rouge_deux_emails(self):
        self.suite = [self.sig("rouge"), self.sig("orange"), self.sig("rouge")]
        for _ in range(3):
            ER.enregistrer_photo()
        self.assertEqual(len(self.alertes), 2)

    def test_cles_rouges(self):
        self.assertEqual(ER.cles_rouges([{"cle": "a", "niveau": "rouge"}, {"cle": "b", "niveau": "orange"}, "x", {"niveau": "rouge"}]), {"a"})
        self.assertEqual(ER.cles_rouges(None), set())

    def test_veille_orange_aujourd_hui_rouge(self):
        # première photo du jour (ligne née d'un noter) : la veille fait foi, et son orange ne compte pas
        frappe.db.sql.return_value = [(json.dumps(self.sig("orange")),)]
        self.suite = [self.sig("rouge")]
        self.assertEqual(ER.enregistrer_photo()["nouveaux"], 1)


if __name__ == "__main__":
    unittest.main()
