"""Réparation osmoseurs : règle d'affectation pure + cycle de vie sur vrais documents (savepoint, tout annulé)."""
from __future__ import annotations

import unittest

from frappe.utils import getdate

from customization_app import reparation_osmoseur as RO

CFG = {"responsables": ["E1", "E2"], "machines_par_jour": 3, "creneaux": ["09:00", "10:15", "11:30"], "horizon_jours": 10, "duree_minutes": 75}


def _taches(par_jour):
    """Fabrique un `taches_du_jour` à partir de {date: [employee, ...]}."""
    def f(jour):
        return [{"custom_choix_du_staff": e} for e in par_jour.get(str(getdate(jour)), [])]
    return f


class TestRegleAffectation(unittest.TestCase):
    def test_premier_responsable_disponible_dans_l_ordre(self):
        c = RO.choisir_creneau("2030-01-07", CFG, est_disponible=lambda e, j: True, taches_du_jour=_taches({}))
        self.assertEqual((str(c["jour"]), c["employee"], c["rang"]), ("2030-01-07", "E1", 0))
        # E1 absent ce jour-là → E2, même jour
        c = RO.choisir_creneau("2030-01-07", CFG, est_disponible=lambda e, j: e != "E1", taches_du_jour=_taches({}))
        self.assertEqual((str(c["jour"]), c["employee"]), ("2030-01-07", "E2"))

    def test_un_seul_operateur_par_jour(self):
        # Le jour a déjà une machine chez E2 : la suivante va à E2, pas à E1, même si E1 est libre.
        c = RO.choisir_creneau("2030-01-07", CFG, est_disponible=lambda e, j: True,
                               taches_du_jour=_taches({"2030-01-07": ["E2"]}))
        self.assertEqual((c["employee"], c["rang"]), ("E2", 1))

    def test_jour_plein_passe_au_lendemain(self):
        c = RO.choisir_creneau("2030-01-07", CFG, est_disponible=lambda e, j: True,
                               taches_du_jour=_taches({"2030-01-07": ["E1", "E1", "E1"]}))
        self.assertEqual((str(c["jour"]), c["employee"], c["rang"]), ("2030-01-08", "E1", 0))

    def test_personne_disponible_passe_au_lendemain(self):
        c = RO.choisir_creneau("2030-01-07", CFG, est_disponible=lambda e, j: str(j) != "2030-01-07",
                               taches_du_jour=_taches({}))
        self.assertEqual(str(c["jour"]), "2030-01-08")

    def test_sans_responsable_ou_horizon_epuise(self):
        self.assertIsNone(RO.choisir_creneau("2030-01-07", dict(CFG, responsables=[])))
        self.assertIsNone(RO.choisir_creneau("2030-01-07", CFG, est_disponible=lambda e, j: False, taches_du_jour=_taches({})))

    def test_jour_occupe_par_un_non_responsable(self):
        # Une tâche atelier posée à la main chez quelqu'un hors liste ne capte pas la journée : lendemain.
        c = RO.choisir_creneau("2030-01-07", CFG, est_disponible=lambda e, j: True,
                               taches_du_jour=_taches({"2030-01-07": ["X"]}))
        self.assertEqual(str(c["jour"]), "2030-01-08")


class TestVerdictControle(unittest.TestCase):
    ATT = {"nom": "Abd Elkader Ayadi", "tel": "94716661 / 94887263", "ref": "OSM-2026-0002"}

    def test_conforme(self):
        lu = {"post_it_visible": True, "post_it_texte": "AYADI Abd elkader\n94 887 263\nosm-2026-0002",
              "reference": "osm 2026 0002", "telephone": "94 887 263", "nom": "Ayadi", "meme_appareil": True}
        v = RO.verdict_controle(lu, self.ATT)
        self.assertTrue(v["ok"], v["problemes"])

    def test_reference_fausse_ou_absente(self):
        lu = {"post_it_visible": True, "reference": "OSM-2026-0003", "telephone": "94716661", "nom": "Ayadi", "meme_appareil": True}
        self.assertEqual(len(RO.verdict_controle(lu, self.ATT)["problemes"]), 1)
        lu["reference"] = None
        self.assertFalse(RO.verdict_controle(lu, self.ATT)["ok"])
        # la référence seulement dans le texte brut suffit
        lu["post_it_texte"] = "Ayadi 94716661 OSM-2026-0002"
        self.assertTrue(RO.verdict_controle(lu, self.ATT)["ok"])

    def test_telephone_nom_et_appareil(self):
        base = {"post_it_visible": True, "reference": "OSM-2026-0002", "telephone": "94716661", "nom": "Ayadi", "meme_appareil": True}
        self.assertFalse(RO.verdict_controle(dict(base, telephone="22000000"), self.ATT)["ok"])
        self.assertFalse(RO.verdict_controle(dict(base, nom="Trabelsi", post_it_texte=""), self.ATT)["ok"])
        self.assertFalse(RO.verdict_controle(dict(base, meme_appareil=False, appareil_commentaire="autre couleur"), self.ATT)["ok"])
        self.assertFalse(RO.verdict_controle(dict(base, post_it_visible=False), self.ATT)["ok"])
        # sans téléphone au dossier, le téléphone n'est pas exigé
        self.assertTrue(RO.verdict_controle(dict(base, telephone=None), dict(self.ATT, tel=""))["ok"])

    def test_tolerance_aux_reponses_bizarres(self):
        for lu in (None, [], "null", 3, {}):
            v = RO.verdict_controle(lu, self.ATT)
            self.assertFalse(v["ok"])


class TestCycleDeVie(unittest.TestCase):
    """Réception → clôture → tâche auto → clôture de la tâche → Réparée → Rendue. Tout dans un savepoint."""

    def setUp(self):
        import frappe
        frappe.db.savepoint("osm")
        frappe.set_user("Administrator")
        self.client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        self.garantie = "Hors garantie"
        self.emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not self.client or not self.emp:
            self.skipTest("pas de client / employé")
        cfg = frappe.get_doc(RO.DOCTYPE_CONFIG)
        cfg.set("responsables", [{"employee": self.emp}])
        cfg.machines_par_jour = 3
        cfg.horizon_jours = 30
        cfg.save(ignore_permissions=True)
        frappe.clear_cache(doctype=RO.DOCTYPE_CONFIG)

        self._code = RO._verifier_code
        RO._verifier_code = lambda code: code == "bon"
        self._lecteur = RO.LECTEUR_IA
        def lecteur_parfait(a, p):
            """Un modèle qui lit exactement ce que le dossier attend."""
            att = RO.texte_post_it(frappe.get_doc(RO.DOCTYPE, self._ref))
            return {"post_it_visible": True, "reference": att["ref"], "telephone": att["tel"].split(" / ")[0],
                    "nom": att["nom"], "meme_appareil": True, "post_it_texte": ""}
        RO.LECTEUR_IA = lecteur_parfait
        self._ref = None

    def tearDown(self):
        import frappe
        RO.LECTEUR_IA = self._lecteur
        RO._verifier_code = self._code
        frappe.db.rollback(save_point="osm")
        frappe.clear_cache(doctype=RO.DOCTYPE_CONFIG)

    def _nouveau(self):
        nom = RO.creer_reception(self.client, note="fuite au robinet", garantie=self.garantie)["name"]
        self._ref = nom          # le lecteur IA simulé lit la bonne référence
        return nom

    def test_controle_ia_refuse_puis_indisponible(self):
        import frappe
        nom = self._nouveau()
        RO.enregistrer_photo(nom, "arrivee", "/files/a.jpg")
        RO.enregistrer_photo(nom, "post_it", "/files/p.jpg")
        RO.LECTEUR_IA = lambda a, p: {"post_it_visible": True, "reference": "OSM-2000-9999", "meme_appareil": False}
        with self.assertRaises(frappe.ValidationError):
            RO.cloturer_reception(nom)
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["statut", "controle_ia_ok", "controle_ia_resultat"], as_dict=True)
        self.assertEqual((m.statut, m.controle_ia_ok), (RO.S_RECEPTION, 0))
        self.assertIn("OSM-2000-9999", m.controle_ia_resultat)
        # service en panne : la clôture passe, avec avertissement tracé
        def panne(a, p):
            raise RuntimeError("openai down")
        RO.LECTEUR_IA = panne
        r = RO.cloturer_reception(nom)
        self.assertTrue(r["avertissement"])
        self.assertNotEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_RECEPTION)

    def test_garantie_obligatoire_et_notee_sur_la_tache(self):
        import frappe
        with self.assertRaises(frappe.ValidationError):
            RO.creer_reception(self.client, note="x")                 # garantie non choisie
        with self.assertRaises(frappe.ValidationError):
            RO.creer_reception(self.client, garantie="Sous garantie", commande_garantie="SAL-ORD-0000-INEXISTANTE")
        self.garantie = "Sous garantie"
        nom = self._nouveau()
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "garantie"), "Sous garantie")
        RO.enregistrer_photo(nom, "arrivee", "/files/a.jpg")
        RO.enregistrer_photo(nom, "post_it", "/files/p.jpg")
        RO.cloturer_reception(nom)
        t = RO.affecter_tache(nom, self.emp, "2031-05-05", "09:00")["tache"]
        tache = frappe.get_doc(RO.DOCTYPE_TACHE, t)
        self.assertTrue(tache.subject.startswith(RO.NOTE_GARANTIE), tache.subject)
        self.assertIn("🆓 GARANTIE", tache.titre)
        # hors garantie : aucune mention
        self.garantie = "Hors garantie"
        nom2 = self._nouveau()
        RO.enregistrer_photo(nom2, "arrivee", "/files/a.jpg"); RO.enregistrer_photo(nom2, "post_it", "/files/p.jpg")
        RO.cloturer_reception(nom2)
        t2 = RO.affecter_tache(nom2, self.emp, "2031-05-06", "09:00")["tache"]
        self.assertNotIn("GARANTIE", frappe.db.get_value(RO.DOCTYPE_TACHE, t2, "subject") + frappe.db.get_value(RO.DOCTYPE_TACHE, t2, "titre"))

    def test_commandes_client_13_mois(self):
        import frappe
        client = frappe.db.sql("""select customer from `tabSales Order` where docstatus=1
                                  and transaction_date >= date_sub(curdate(), interval 13 month) group by customer
                                  order by count(*) desc limit 1""")[0][0]
        r = RO.commandes_client(client)
        self.assertTrue(r["commandes"])
        c = r["commandes"][0]
        self.assertTrue(c["articles"]); self.assertIn("garanti", c); self.assertIn("image", c["articles"][0])
        self.assertTrue(all(str(x["transaction_date"]) >= r["depuis"] for x in r["commandes"]))
        self.assertEqual(RO.commandes_client("CLIENT-INEXISTANT-XYZ")["commandes"], [])

    def test_cloture_exige_les_photos_ou_le_code(self):
        import frappe
        nom = self._nouveau()
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_RECEPTION)
        with self.assertRaises(frappe.ValidationError):
            RO.cloturer_reception(nom)                       # pas de photo d'arrivée
        RO.enregistrer_photo(nom, "arrivee", "/files/arrivee.jpg")
        with self.assertRaises(frappe.ValidationError):
            RO.cloturer_reception(nom)                       # pas de post-it, pas de code
        with self.assertRaises(frappe.ValidationError):
            RO.cloturer_reception(nom, code="certainement-pas-le-bon")
        self.assertFalse(RO.etat_reception(nom)["peut_cloturer"])
        RO.enregistrer_photo(nom, "post_it", "/files/postit.jpg")
        self.assertTrue(RO.etat_reception(nom)["peut_cloturer"])

    def test_cycle_complet_avec_affectation_auto(self):
        import frappe
        nom = self._nouveau()
        RO.enregistrer_photo(nom, "arrivee", "/files/a.jpg")
        RO.enregistrer_photo(nom, "post_it", "/files/p.jpg")
        # Affectation à une date lointaine pour ne pas dépendre du planning réel de l'employé.
        doc = frappe.get_doc(RO.DOCTYPE, nom)
        doc.photo_post_it = "/files/p.jpg"
        r = RO.cloturer_reception(nom)
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_PLANIFIEE if r.get("tache") else RO.S_RECEPTIONNEE)
        if not r.get("tache"):
            # Personne de libre dans l'horizon réel : on force une date lointaine.
            r = RO.planifier(nom, a_partir="2031-03-03")
        self.assertTrue(r["tache"], r)
        t = frappe.get_doc(RO.DOCTYPE_TACHE, r["tache"])
        self.assertEqual((t.custom_type_dintervention, t.get(RO.CHAMP_TACHE), t.custom_client, t.dans_local),
                         ("Réparation", nom, self.client, "Oui"))
        # 1 h 15 par réparation (réglage de l’atelier, même valeur que le type Réparation depuis le 05/10/2026)
        self.assertEqual((t.ends_on - t.starts_on).total_seconds(), 75 * 60)
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["statut", "responsable", "date_prevue"], as_dict=True)
        self.assertEqual((m.statut, m.responsable, str(m.date_prevue)), (RO.S_PLANIFIEE, t.custom_choix_du_staff, str(getdate(t.starts_on))))
        # Idempotent : une 2e planification ne double pas la tâche
        self.assertTrue(RO.planifier(nom)["deja"])
        # Une seconde machine le même jour → même opérateur, créneau suivant
        nom2 = self._nouveau()
        RO.enregistrer_photo(nom2, "arrivee", "/files/a.jpg")
        RO.enregistrer_photo(nom2, "post_it", "/files/p.jpg")
        RO.cloturer_reception(nom2)
        r2 = RO.planifier(nom2, a_partir=str(getdate(t.starts_on))) if not frappe.db.get_value(RO.DOCTYPE_TACHE, {RO.CHAMP_TACHE: nom2}, "name") else {"deja": True}
        t2 = frappe.get_doc(RO.DOCTYPE_TACHE, frappe.db.get_value(RO.DOCTYPE_TACHE, {RO.CHAMP_TACHE: nom2}, "name"))
        self.assertEqual(t2.custom_choix_du_staff, t.custom_choix_du_staff)
        # Rendre AVANT réparation : refusé
        with self.assertRaises(frappe.ValidationError):
            RO.rendre(nom)
        # Clôture de la tâche (dispense de photos : on teste le cycle, pas la règle des photos)
        t.reload()
        t.dispense_photos = 1
        t.rapport_visite = "membrane remplacée"
        t.status = "Completed"
        t.flags.ignore_permissions = True
        t.save()
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["statut", "date_reparee"], as_dict=True)
        self.assertEqual(m.statut, RO.S_REPAREE)
        self.assertTrue(m.date_reparee)
        # Hors garantie et aucune vente liée : ni sans motif, ni sans le code superviseur (20/11/2026)
        with self.assertRaises(frappe.ValidationError):
            RO.rendre(nom)
        with self.assertRaises(frappe.ValidationError):
            RO.rendre(nom, motif="Geste commercial", code="faux")
        self.assertEqual(RO.rendre(nom, motif="Geste commercial", code="bon")["statut"], RO.S_RENDUE)
        # Une machine rendue ne redescend jamais, même si une tâche bouge
        RO.synchroniser(nom)
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_RENDUE)

    def test_rouvrir_supprime_les_taches_ouvertes(self):
        import frappe
        nom = self._nouveau()
        RO.enregistrer_photo(nom, "arrivee", "/files/a.jpg")
        RO.enregistrer_photo(nom, "post_it", "/files/p.jpg")
        RO.cloturer_reception(nom)
        RO.affecter_tache(nom, self.emp, "2031-04-04", "10:00")
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_PLANIFIEE)
        t = frappe.db.get_value(RO.DOCTYPE_TACHE, {RO.CHAMP_TACHE: nom}, "name")
        RO.rouvrir_reception(nom)
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_RECEPTION)
        # la tâche est SUPPRIMÉE, pas annulée
        self.assertFalse(frappe.db.exists(RO.DOCTYPE_TACHE, t))


class TestRestitution(unittest.TestCase):
    """Étape « à rendre » (02/10/2026) : retrait au magasin (SMS, remise) ou livraison (tâche, clôture, annulation)."""

    def setUp(self):
        import frappe
        frappe.db.savepoint("osm_rest")
        frappe.set_user("Administrator")
        self.client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        self.emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not self.client or not self.emp:
            self.skipTest("pas de client / employé")
        nom = RO.creer_reception(self.client, note="essai restitution", garantie="Hors garantie", tel="20123456")["name"]
        frappe.db.set_value(RO.DOCTYPE, nom, {"statut": RO.S_REPAREE, "date_reparee": frappe.utils.now_datetime()})
        self.nom = nom
        self._code = RO._verifier_code
        RO._verifier_code = lambda code: code == "bon"

    def tearDown(self):
        import frappe
        RO._verifier_code = self._code
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="osm_rest")

    def _statut(self):
        import frappe
        return frappe.db.get_value(RO.DOCTYPE, self.nom, "statut")

    def test_retrait_au_magasin_avec_sms_puis_remise(self):
        import frappe
        r = RO.restituer_magasin(self.nom, sms=1)
        self.assertEqual((r["statut"], self._statut()), (RO.S_PRETE, RO.S_PRETE))
        self.assertTrue(r["sms"]["simule"])                                      # dev : jamais un vrai SMS
        self.assertEqual(r["sms"]["numeros"], ["20123456"])
        self.assertIn("hors garantie", r["sms"]["texte"])
        self.assertTrue(frappe.db.get_value(RO.DOCTYPE, self.nom, "sms_pret_le"))
        with self.assertRaises(frappe.ValidationError):
            RO.restituer_magasin(self.nom)                                       # déjà au magasin
        RO.enregistrer_photo(self.nom, "remise", "/files/remise.jpg")
        self.assertEqual(RO.rendre(self.nom, rendu_a="Son frère", remarque="règlement espèces",
                                   motif="Geste commercial", code="bon")["statut"], RO.S_RENDUE)
        m = frappe.db.get_value(RO.DOCTYPE, self.nom, ["rendu_a", "mode_restitution", "photo_remise", "date_rendue"], as_dict=True)
        self.assertEqual((m.rendu_a, m.mode_restitution, m.photo_remise), ("Son frère", "Retrait au magasin", "/files/remise.jpg"))
        self.assertTrue(m.date_rendue)
        RO.synchroniser(self.nom)
        self.assertEqual(self._statut(), RO.S_RENDUE)                            # une machine rendue ne redescend pas

    def test_annuler_le_retrait(self):
        RO.restituer_magasin(self.nom)
        self.assertEqual(RO.annuler_restitution(self.nom)["statut"], RO.S_REPAREE)
        self.assertEqual(self._statut(), RO.S_REPAREE)

    def test_livraison_cloturee_rend_la_machine(self):
        import frappe
        r = RO.planifier_livraison(self.nom, self.emp, "2031-04-04", heure="10:30", note="appeler avant", sms=1)
        self.assertEqual((r["statut"], self._statut()), (RO.S_LIVRAISON, RO.S_LIVRAISON))
        self.assertTrue(r["sms"]["simule"] and "04/04/2031" in r["sms"]["texte"] and "livraison" in r["sms"]["texte"])
        self.assertTrue(frappe.db.get_value(RO.DOCTYPE, self.nom, "sms_pret_le"))
        t = frappe.get_doc(RO.DOCTYPE_TACHE, r["tache"])
        self.assertEqual((t.custom_type_dintervention, t.get(RO.CHAMP_TACHE), t.custom_choix_du_staff, str(t.starts_on)[:16]),
                         ("Livraison", self.nom, self.emp, "2031-04-04 10:30"))
        self.assertEqual((t.ends_on - t.starts_on).total_seconds(), 30 * 60)
        self.assertIn("hors garantie", t.subject)
        self.assertIn("appeler avant", t.subject)
        m = frappe.db.get_value(RO.DOCTYPE, self.nom, ["tache_livraison", "date_livraison_prevue", "mode_restitution", "date_reparee"], as_dict=True)
        self.assertEqual((m.tache_livraison, str(m.date_livraison_prevue), m.mode_restitution), (t.name, "2031-04-04", "Livraison"))
        self.assertTrue(m.date_reparee)                                          # créer la livraison ne l'efface plus
        with self.assertRaises(frappe.ValidationError):
            RO.rendre(self.nom)                                                  # pas de remise à la main pendant une livraison
        # un mouvement sur la tâche de RÉPARATION ne touche pas au statut de restitution
        self.assertEqual(RO.synchroniser(self.nom), RO.S_LIVRAISON)
        t.dispense_photos = 1
        t.rapport_visite = "livrée, client présent"
        t.status = "Completed"
        t.flags.ignore_permissions = True
        t.save()
        m = frappe.db.get_value(RO.DOCTYPE, self.nom, ["statut", "rendu_a", "date_rendue"], as_dict=True)
        self.assertEqual(m.statut, RO.S_RENDUE)
        self.assertTrue(m.date_rendue and m.rendu_a.startswith("livrée par"))

    def test_livraison_supprimee_revient_a_rendre(self):
        import frappe
        r = RO.planifier_livraison(self.nom, self.emp, "2031-04-05")
        frappe.delete_doc(RO.DOCTYPE_TACHE, r["tache"], ignore_permissions=True, force=True)   # le hook after_delete resynchronise
        self.assertEqual(self._statut(), RO.S_REPAREE)
        self.assertFalse(frappe.db.get_value(RO.DOCTYPE, self.nom, "tache_livraison"))
        # et l'annulation depuis l'écran supprime la tâche ouverte
        r = RO.planifier_livraison(self.nom, self.emp, "2031-04-06")
        self.assertEqual(RO.annuler_restitution(self.nom)["statut"], RO.S_REPAREE)
        self.assertFalse(frappe.db.exists(RO.DOCTYPE_TACHE, r["tache"]))

    def test_rendre_directement_depuis_reparee_reste_possible(self):
        self.assertEqual(RO.rendre(self.nom, rendu_a="Le client", motif="Rien à réparer", code="bon")["statut"], RO.S_RENDUE)


class TestReglesPures(unittest.TestCase):
    """Simplification du 20/11/2026 : trois groupes, « rendue » séparée du règlement, ce qui traîne. Sans la base."""

    def test_trois_groupes_pour_huit_statuts(self):
        self.assertEqual([RO.groupe_de(s) for s in RO.STATUTS],
                         ["À réparer", "À réparer", "À réparer", "À rendre", "À rendre", "À rendre", "À réparer", "Rendue"])

    def test_une_dette_non_payee_n_est_pas_un_paiement(self):
        # commande de 95 : un Payment Entry « Dette non payée » la fait paraître payée à ERPNext, rien n'est encaissé
        self.assertEqual(RO.etat_reglement(95, regle=0, dette=95, hors_dette=0), {"total": 95.0, "reste": 95.0, "etat": "du"})
        self.assertEqual(RO.etat_reglement(104, regle=104, dette=0, hors_dette=104)["etat"], "payee")
        self.assertEqual(RO.etat_reglement(200, regle=120, dette=80, hors_dette=120), {"total": 200.0, "reste": 80.0, "etat": "du"})
        # payée par un avoir : aucun encaissement, mais l'échéancier la couvre
        self.assertEqual(RO.etat_reglement(50, regle=0, dette=0, hors_dette=50)["etat"], "payee")
        # ni règlement ni échéancier : tout reste dû ; commande à 0 DT (garantie) : rien à encaisser
        self.assertEqual(RO.etat_reglement(70, 0, 0, 0)["reste"], 70.0)
        self.assertEqual(RO.etat_reglement(0, 0, 0, 0)["etat"], "zero")

    def test_ce_qu_il_faut_pour_fermer(self):
        r = RO.regle_sortie
        # une commande liée : libre, payée ou non
        self.assertEqual(r(True, "Hors garantie", True, False), {"cas": "commande", "motif": False, "code": False})
        self.assertEqual(r(True, "Sous garantie", True, False)["cas"], "commande")        # garantie facturée (OSM-0009)
        # sous garantie : libre avec la commande d'origine, code superviseur sans elle
        self.assertEqual(r(True, "Sous garantie", False, True), {"cas": "garantie", "motif": False, "code": False})
        self.assertEqual(r(True, "Sous garantie", False, False), {"cas": "garantie_sans_preuve", "motif": False, "code": True})
        # hors garantie sans aucune vente : motif + code
        self.assertEqual(r(True, "Hors garantie", False, False), {"cas": "non_facturee", "motif": True, "code": True})
        # machine reprise sans réparation : un motif, pas de code
        self.assertEqual(r(False, "Hors garantie", False, False), {"cas": "sans_reparation", "motif": True, "code": False})

    def test_la_phrase_gardee_sur_le_dossier(self):
        self.assertEqual(RO.libelle_reglement({"etat": "du", "reste": 95.0, "total": 95.0, "commande": "SO-1"}, "Hors garantie"),
                         "reste dû 95.000 DT sur 95.000 (commande SO-1)")
        self.assertEqual(RO.libelle_reglement({"etat": "payee", "reste": 0, "total": 104.0, "commande": "SO-2"}, "Hors garantie"),
                         "payée — 104.000 DT (commande SO-2)")
        self.assertEqual(RO.libelle_reglement(None, "Sous garantie"), "garantie — rien à facturer")
        self.assertEqual(RO.libelle_reglement(None, "Hors garantie"), "non facturée")

    def test_depuis_combien_de_jours_un_dossier_attend(self):
        jour = getdate("2026-11-20")
        a = RO.attente({"statut": RO.S_REPAREE, "date_reparee": "2026-11-16 16:55:00"}, jour)
        self.assertEqual(a, {"motif": "a_rendre", "jours": 4})
        # « toujours au magasin » remet le compteur à zéro
        a = RO.attente({"statut": RO.S_PRETE, "date_reparee": "2026-10-20 11:02:00", "toujours_la_le": "2026-11-19 09:00:00"}, jour)
        self.assertEqual(a["jours"], 1)
        a = RO.attente({"statut": RO.S_RECEPTIONNEE, "date_reception": "2026-10-30 17:00:00", "date_cloture_reception": "2026-10-30 17:02:00"}, jour)
        self.assertEqual(a, {"motif": "sans_technicien", "jours": 21})
        # une livraison n'attend que si sa date est passée
        self.assertIsNone(RO.attente({"statut": RO.S_LIVRAISON, "date_livraison_prevue": "2026-11-20"}, jour))
        self.assertEqual(RO.attente({"statut": RO.S_LIVRAISON, "date_livraison_prevue": "2026-11-18"}, jour), {"motif": "livraison", "jours": 2})
        # planifiée chez un technicien, ou rendue : rien à signaler
        self.assertIsNone(RO.attente({"statut": RO.S_PLANIFIEE, "date_reception": "2026-10-01"}, jour))
        self.assertIsNone(RO.attente({"statut": RO.S_RENDUE, "date_reparee": "2026-10-01"}, jour))

    def test_le_tri_du_matin(self):
        jour = getdate("2026-11-20")
        machines = [
            {"name": "A", "statut": RO.S_REPAREE, "date_reparee": "2026-11-19"},                                # 1 j : trop tôt
            {"name": "B", "statut": RO.S_REPAREE, "date_reparee": "2026-11-16"},                                # 4 j : rappel
            {"name": "C", "statut": RO.S_PRETE, "date_reparee": "2026-10-20", "rappel_depuis": "2026-11-15"},   # sans réponse 5 j
            {"name": "D", "statut": RO.S_RECEPTIONNEE, "date_reception": "2026-10-30", "rappel_depuis": "2026-11-19"},
            {"name": "E", "statut": RO.S_LIVRAISON, "date_livraison_prevue": "2026-11-19"},                     # 1 j de retard
            {"name": "F", "statut": RO.S_PLANIFIEE, "date_reception": "2026-09-01"},
        ]
        tri = RO.a_rappeler(machines, jour, rappel_jours=3, relance_jours=3)
        self.assertEqual([m["name"] for m in tri["rappel"]], ["C", "D", "B", "E"])      # les plus anciens d'abord
        self.assertEqual([m["name"] for m in tri["relance"]], ["C"])
        texte = RO.composer_rappel(tri["rappel"], "https://x/app/reparation-osmoseurs?verifier=1")
        self.assertIn("4 machine(s) attendent une réponse", texte)
        self.assertIn("**B** ·  — réparée, chez nous depuis 4 j", texte)
        self.assertIn("**D** ·  — reçue, sans technicien depuis 21 j", texte)
        self.assertIn("**E** ·  — livraison prévue il y a 1 j, pas clôturée", texte)
        self.assertIn("sans réponse de Salma", RO.composer_rappel(tri["relance"], "u", relance_de="Salma"))

    def test_les_photos_du_dossier_dans_l_ordre_de_sa_vie(self):
        self.assertEqual(RO.photos_de('📁 "/private/files/a.png"\n\n📁 "/files/b.jpg"\nn importe quoi'),
                         ["/private/files/a.png", "/files/b.jpg"])
        m = {"photo_arrivee": "/files/1.jpg", "photo_post_it": "/files/2.jpg", "photo_remise": "/files/5.jpg", "tache_livraison": "T2"}
        taches = [{"name": "T1", "photos_avant": ["/files/3.jpg"], "photos_apres": ["/files/4.jpg"]},
                  {"name": "T2", "photos_avant": [], "photos_apres": ["/files/6.jpg"]}]
        p = RO.photos_du_dossier(m, taches)
        self.assertEqual([x["url"] for x in p], ["/files/1.jpg", "/files/2.jpg", "/files/3.jpg", "/files/4.jpg", "/files/6.jpg", "/files/5.jpg"])
        self.assertEqual([x["titre"] for x in p][2:5], ["Réparation — pièces enlevées", "Réparation — appareil réparé", "Livraison"])
        self.assertEqual(RO.photos_du_dossier({}, []), [])


class TestSortie(unittest.TestCase):
    """La sortie d'une machine (20/11/2026) : commande liée et règlement réel, garantie, sans réparation, rappel."""

    def setUp(self):
        import frappe
        frappe.db.savepoint("osm_sortie")
        frappe.set_user("Administrator")
        self.emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        self._code = RO._verifier_code
        RO._verifier_code = lambda code: code == "bon"

    def tearDown(self):
        import frappe
        RO._verifier_code = self._code
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="osm_sortie")

    def _dossier(self, client, garantie="Hors garantie", statut=None, **champs):
        import frappe
        nom = RO.creer_reception(client, note="essai sortie", garantie=garantie, tel="20123456")["name"]
        frappe.db.set_value(RO.DOCTYPE, nom, dict({"statut": statut or RO.S_REPAREE, "date_reparee": frappe.utils.now_datetime()}, **champs))
        return nom

    def _commande(self, dette: bool):
        """Une vraie commande validée, avec ou sans ligne « Dette non payée » à l'échéancier."""
        import frappe
        r = frappe.db.sql("""select so.name, so.customer from `tabSales Order` so
            where so.docstatus = 1 and so.grand_total > 0 and so.per_billed = 0 and %s exists (
                select 1 from `tabPayment Schedule` ps where ps.parent = so.name and ps.parenttype = 'Sales Order'
                and ps.mode_of_payment = 'Dette non payée')
            and exists (select 1 from `tabPayment Schedule` ps where ps.parent = so.name and ps.parenttype = 'Sales Order')
            order by so.transaction_date desc limit 1""" % ("" if dette else "not"), as_dict=True)
        if not r:
            self.skipTest("pas de commande %s dette" % ("avec" if dette else "sans"))
        return r[0]

    def test_commande_avec_dette_la_machine_sort_et_le_reste_du_est_garde(self):
        import frappe
        so = self._commande(dette=True)
        reg = RO.reglements([so.name])[so.name]
        self.assertEqual(reg["etat"], "du")
        self.assertGreater(reg["reste"], 0)
        nom = self._dossier(so.customer)
        s = RO.situation_sortie(nom)
        self.assertTrue(s["repare"])
        self.assertEqual(s["regle"]["cas"], "non_facturee")                       # rien de lié pour l'instant
        r = RO.rendre(nom, rendu_a="Le client", commande=so.name)                 # ni motif ni code : la commande suffit
        self.assertEqual((r["statut"], r["cas"]), (RO.S_RENDUE, "commande"))
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["commande_client", "reglement_sortie", "rendue_par", "sans_reparation"], as_dict=True)
        self.assertEqual(m.commande_client, so.name)
        self.assertTrue(m.reglement_sortie.startswith("reste dû"), m.reglement_sortie)
        self.assertTrue(m.rendue_par)
        self.assertEqual(m.sans_reparation, 0)
        # l'écran montre le reste dû d'aujourd'hui à côté de la machine rendue
        ligne = [x for x in RO.get_data(vue="Rendue")["machines"] if x.name == nom][0]
        self.assertEqual((ligne["groupe"], ligne["reglement"]["etat"]), ("Rendue", "du"))

    def test_commande_payee_et_commande_d_un_autre_client(self):
        import frappe
        so = self._commande(dette=False)
        if RO.reglements([so.name])[so.name]["etat"] != "payee":
            self.skipTest("commande sans dette mais pas soldée")
        nom = self._dossier(so.customer)
        autre = frappe.db.get_value("Sales Order", {"docstatus": 1, "customer": ["!=", so.customer]}, "name")
        with self.assertRaises(frappe.ValidationError):
            RO.rendre(nom, commande=autre)                                        # pas celle de ce client
        r = RO.rendre(nom, commande=so.name)
        self.assertTrue(r["reglement"].startswith("payée"), r["reglement"])

    def test_garantie_libre_avec_la_commande_d_origine_code_sinon(self):
        import frappe
        so = self._commande(dette=False)
        nom = self._dossier(so.customer, garantie="Sous garantie")
        with self.assertRaises(frappe.ValidationError):
            RO.rendre(nom)                                                        # sous garantie, sans preuve, sans code
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_REPAREE)
        r = RO.rendre(nom, commande_garantie=so.name)                             # la commande d'origine suffit
        self.assertEqual((r["cas"], r["reglement"]), ("garantie", "garantie — rien à facturer"))
        self.assertFalse(frappe.db.get_value(RO.DOCTYPE, nom, "commande_client"))
        nom2 = self._dossier(so.customer, garantie="Sous garantie")
        self.assertEqual(RO.rendre(nom2, code="bon")["cas"], "garantie_sans_preuve")

    def test_sortie_sans_reparation_ferme_le_dossier_et_retire_les_taches(self):
        import frappe
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        nom = self._dossier(client, statut=RO.S_RECEPTIONNEE, date_reparee=None)
        t = RO.affecter_tache(nom, self.emp, "2031-07-07", "09:00")["tache"]
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "statut"), RO.S_PLANIFIEE)
        with self.assertRaises(frappe.ValidationError):
            RO.sortir_sans_reparation(nom, motif="")                              # le motif est obligatoire
        with self.assertRaises(frappe.ValidationError):
            RO.rendre(nom, motif="x", code="bon")                                 # pas réparée : pas une remise normale
        r = RO.sortir_sans_reparation(nom, motif="Le client reprend sa machine", rendu_a="Son fils")
        self.assertEqual(r["statut"], RO.S_RENDUE)
        self.assertFalse(frappe.db.exists(RO.DOCTYPE_TACHE, t))                   # la tâche ouverte n'a jamais eu lieu
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["statut", "sans_reparation", "motif_sortie", "reglement_sortie", "rendu_a"], as_dict=True)
        self.assertEqual((m.statut, m.sans_reparation, m.motif_sortie, m.reglement_sortie, m.rendu_a),
                         (RO.S_RENDUE, 1, "Le client reprend sa machine", "sans réparation", "Son fils"))
        # clic par erreur : le dossier redevient ouvert, sans technicien
        self.assertEqual(RO.annuler_sortie(nom)["statut"], RO.S_RECEPTIONNEE)
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["sans_reparation", "motif_sortie", "date_rendue"], as_dict=True)
        self.assertEqual((m.sans_reparation, m.motif_sortie, m.date_rendue), (0, None, None))

    def test_une_tache_retypee_reste_la_reparation_du_dossier(self):
        import frappe
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        nom = self._dossier(client, statut=RO.S_RECEPTIONNEE, date_reparee=None)
        t = frappe.get_doc(RO.DOCTYPE_TACHE, RO.affecter_tache(nom, self.emp, "2031-07-08", "09:00")["tache"])
        t.custom_type_dintervention = "Livraison"                                # OSM-2026-0012 : retypée par quelqu'un
        t.dispense_photos = 1
        t.rapport_visite = "partie haute pression changée"
        t.status = "Completed"
        t.flags.ignore_permissions = True
        t.save()
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["statut", "date_reparee", "tache_livraison"], as_dict=True)
        self.assertEqual((m.statut, m.tache_livraison), (RO.S_REPAREE, None))     # à rendre, et non « à affecter »
        # le nom affiché est bien celui du technicien de la tâche
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, "nom_responsable"), frappe.db.get_value("Employee", self.emp, "employee_name"))
        self.assertTrue(m.date_reparee)
        # une tâche annulée, elle, laisse le dossier sans technicien — d'où il sort « sans réparation »
        nom2 = self._dossier(client, statut=RO.S_RECEPTIONNEE, date_reparee=None)
        t2 = frappe.get_doc(RO.DOCTYPE_TACHE, RO.affecter_tache(nom2, self.emp, "2031-07-09", "09:00")["tache"])
        t2.status = "Cancelled"
        t2.rapport_visite = "le client reprend sa machine"
        t2.flags.ignore_permissions = True
        t2.save()
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom2, "statut"), RO.S_RECEPTIONNEE)
        self.assertEqual(RO.sortir_sans_reparation(nom2, motif="Le client reprend sa machine")["statut"], RO.S_RENDUE)

    def test_toujours_au_magasin_bandeau_et_tri_du_rappel(self):
        import frappe
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        nom = self._dossier(client, date_reparee="2031-01-01 10:00:00")
        frappe.db.set_value(RO.DOCTYPE, nom, "rappel_depuis", "2031-01-05")
        self.assertIn(nom, [x.name for x in RO.machines_du_client(client)])       # le bandeau de sa commande la signale
        ligne = [x for x in RO.get_data(vue="À rendre")["machines"] if x.name == nom][0]
        self.assertEqual((ligne["groupe"], ligne["attente"]["motif"], ligne["photos"]), ("À rendre", "a_rendre", []))
        RO.toujours_la(nom)
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["toujours_la_le", "rappel_depuis", "statut"], as_dict=True)
        self.assertTrue(m.toujours_la_le)
        self.assertIsNone(m.rappel_depuis)
        self.assertEqual(m.statut, RO.S_REPAREE)                                  # rien d'autre ne bouge
        # le tri du matin, sans rien envoyer
        tri = RO.rappel_quotidien(envoyer=0)
        self.assertEqual(tri["envoyes"], [])
        self.assertTrue(set(tri["relance"]) <= set(tri["rappel"]))
        RO.rendre(nom, motif="Geste commercial", code="bon")
        self.assertNotIn(nom, [x.name for x in RO.machines_du_client(client)])

    def test_commentaire_de_suivi_garde_son_auteur_et_son_histoire(self):
        import frappe
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        nom = self._dossier(client, date_reparee="2031-01-01 10:00:00")
        r = RO.enregistrer_commentaire(nom, "  client appelé, viendra samedi ")
        self.assertTrue(r["change"] and r["par"] and r["le"])
        m = frappe.db.get_value(RO.DOCTYPE, nom, ["commentaire_suivi", "commentaire_suivi_par", "statut", "toujours_la_le"], as_dict=True)
        self.assertEqual((m.commentaire_suivi, m.statut), ("client appelé, viendra samedi", RO.S_REPAREE))
        self.assertIsNone(m.toujours_la_le)                                      # un commentaire ne répond pas au rappel
        self.assertFalse(RO.enregistrer_commentaire(nom, "client appelé, viendra samedi")["change"])   # rien de neuf : rien d'écrit
        ligne = [x for x in RO.get_data(recherche="viendra samedi")["machines"] if x.name == nom][0]   # et il se cherche
        self.assertEqual(ligne["commentaire_suivi"], "client appelé, viendra samedi")
        RO.enregistrer_commentaire(nom, "")
        self.assertIsNone(frappe.db.get_value(RO.DOCTYPE, nom, "commentaire_suivi"))
        traces = [frappe.utils.strip_html(c) for c in frappe.get_all("Comment", filters={"reference_doctype": RO.DOCTYPE, "reference_name": nom}, pluck="content", order_by="creation")]
        self.assertEqual([t for t in traces if t.startswith("💬")],
                         ["💬 Suivi — client appelé, viendra samedi", "💬 Suivi effacé (il disait : client appelé, viendra samedi)"])

    def test_la_file_de_l_atelier_dans_mes_appels_par_priorite(self):
        """20/11/2026 : le rappel sort dans « Mes appels par priorité » de la personne du suivi — et d'elle seule."""
        import frappe
        from customization_app import tableau_appels as T
        suivi = frappe.db.get_value("Has Role", {"role": "Appels", "parenttype": "User", "parent": ["not in", ["Administrator", "Guest"]]}, "parent")
        autre = frappe.db.get_value("Has Role", {"role": "Appels", "parenttype": "User", "parent": ["not in", ["Administrator", "Guest", suivi]]}, "parent")
        if not suivi or not autre:
            self.skipTest("il faut deux utilisateurs du rôle Appels")
        frappe.db.set_single_value(RO.DOCTYPE_CONFIG, {"suivi_restitutions": suivi, "relance_a": None, "rappel_jours": 3})
        frappe.clear_document_cache(RO.DOCTYPE_CONFIG, RO.DOCTYPE_CONFIG)
        client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        nom = self._dossier(client, date_reparee="2031-01-01 10:00:00")           # (2031 : pas encore en attente)
        frappe.db.set_value(RO.DOCTYPE, nom, "date_reparee", frappe.utils.add_days(frappe.utils.now_datetime(), -5))
        try:
            frappe.set_user(suivi)
            r = RO.mon_rappel()
            self.assertEqual(r["role"], "suivi")
            self.assertIn(nom, [m["name"] for m in r["machines"]])
            self.assertEqual([m["texte"] for m in r["machines"] if m["name"] == nom], ["réparée, chez nous depuis 5 j"])
            p = T.get_priorites()
            file = [f for f in p["files"] if f["cle"] == "atelier"][0]
            self.assertEqual((file["rang"], file["reste"], file["route"], file["options"]), (3, r["n"], ["reparation-osmoseurs"], {"verifier": 1}))
            self.assertEqual([f["rang"] for f in p["files"]], list(range(1, len(T.PRIORITES) + 1)))
            fait = file["fait"]
            RO.toujours_la(nom)                                                   # une réponse : comptée, et la file baisse
            p = T.get_priorites()
            file = [f for f in p["files"] if f["cle"] == "atelier"][0]
            self.assertEqual((file["fait"], file["reste"]), (fait + 1, r["n"] - 1))
            self.assertNotIn(nom, [m["name"] for m in RO.mon_rappel()["machines"]])
            # quelqu'un d'autre : ni alerte, ni file — et sa liste reste numérotée sans trou
            frappe.set_user(autre)
            self.assertIsNone(RO.mon_rappel())
            p = T.get_priorites()
            self.assertNotIn("atelier", [f["cle"] for f in p["files"]])
            self.assertEqual([f["rang"] for f in p["files"]], list(range(1, len(T.PRIORITES))))
            # la personne RELANCÉE voit dans sa file tout ce qui attend (et non 0) ; son alerte, elle, ne porte que
            # sur ce qui est resté sans réponse
            frappe.set_user("Administrator")
            frappe.db.set_single_value(RO.DOCTYPE_CONFIG, "relance_a", autre)
            frappe.clear_document_cache(RO.DOCTYPE_CONFIG, RO.DOCTYPE_CONFIG)
            frappe.set_user(suivi)
            total = RO.mon_rappel()["n"]
            frappe.set_user(autre)
            file = [f for f in T.get_priorites()["files"] if f["cle"] == "atelier"][0]
            self.assertEqual(file["reste"], total)
            r = RO.mon_rappel()
            self.assertEqual(r["role"], "relance")
            self.assertLessEqual(r["n"], total)
        finally:
            frappe.set_user("Administrator")
            frappe.clear_document_cache(RO.DOCTYPE_CONFIG, RO.DOCTYPE_CONFIG)


class TestRattachement(unittest.TestCase):
    """Une tâche Réparation déjà au calendrier se rattache au dossier (02/10/2026)."""

    def setUp(self):
        import frappe
        frappe.db.savepoint("osm_ratt")
        frappe.set_user("Administrator")
        self.client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        self.emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not self.client or not self.emp:
            self.skipTest("pas de client / employé")
        self.nom = RO.creer_reception(self.client, note="essai rattachement", garantie="Sous garantie", tel="20123456")["name"]
        frappe.db.set_value(RO.DOCTYPE, self.nom, "statut", RO.S_RECEPTIONNEE)
        self.tache = frappe.get_doc({"doctype": RO.DOCTYPE_TACHE, "custom_type_dintervention": "Réparation", "custom_choix_du_staff": self.emp,
                                     "custom_client": self.client, "starts_on": "2031-06-06 09:00:00", "subject": "panne pompe", "status": "Open"})
        self.tache.flags.ignore_permissions = True
        self.tache.insert()

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="osm_ratt")

    def test_rattacher_puis_detacher(self):
        import frappe
        cands = RO.taches_candidates(self.nom)
        # celles du client en tête (il peut en avoir une autre, réelle, sur la copie de prod)
        self.assertTrue(cands[0]["meme_client"])
        self.assertIn(self.tache.name, [c["name"] for c in cands if c["meme_client"]])
        r = RO.rattacher_tache(self.nom, self.tache.name)
        self.assertEqual((r["statut"], frappe.db.get_value(RO.DOCTYPE, self.nom, "statut")), (RO.S_PLANIFIEE, RO.S_PLANIFIEE))
        t = frappe.get_doc(RO.DOCTYPE_TACHE, self.tache.name)
        self.assertEqual((t.get(RO.CHAMP_TACHE), t.dans_local), (self.nom, "Oui"))
        self.assertTrue(t.subject.startswith(RO.NOTE_GARANTIE) and "Réparation osmoseur %s" % self.nom in t.subject and "panne pompe" in t.subject)
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, self.nom, "responsable"), self.emp)
        self.assertNotIn(self.tache.name, [c["name"] for c in RO.taches_candidates(self.nom)])   # plus candidate
        autre = RO.creer_reception(self.client, note="autre", garantie="Hors garantie")["name"]
        frappe.db.set_value(RO.DOCTYPE, autre, "statut", RO.S_RECEPTIONNEE)
        with self.assertRaises(frappe.ValidationError):
            RO.rattacher_tache(autre, self.tache.name)                                               # déjà prise
        self.assertEqual(RO.detacher_tache(self.nom, self.tache.name)["statut"], RO.S_RECEPTIONNEE)
        self.assertFalse(frappe.db.get_value(RO.DOCTYPE_TACHE, self.tache.name, RO.CHAMP_TACHE))

    def test_retirer_une_affectation_supprime_la_tache_vide(self):
        """20/11/2026 : « j'affecte un technicien, j'enlève l'affectation, la tâche reste sur le calendrier »."""
        import frappe
        t = RO.affecter_tache(self.nom, self.emp, "2031-07-07", "09:00")["tache"]
        ligne = next(x for x in RO.get_data(recherche=self.nom)["machines"][0]["taches"] if x["name"] == t)
        self.assertTrue(ligne["vierge"])                                                         # l'écran l'annonce
        r = RO.detacher_tache(self.nom, t)
        self.assertEqual((r["supprimee"], r["statut"]), (True, RO.S_RECEPTIONNEE))
        self.assertFalse(frappe.db.exists(RO.DOCTYPE_TACHE, t))                                  # plus rien au calendrier
        self.assertFalse(frappe.db.get_value(RO.DOCTYPE, self.nom, "responsable"))
        traces = frappe.get_all("Comment", filters={"reference_doctype": RO.DOCTYPE, "reference_name": self.nom}, pluck="content")
        self.assertTrue(any("Affectation retirée" in c and "supprimée du calendrier" in c for c in traces))

    def test_retirer_une_affectation_garde_la_tache_qui_porte_quelque_chose(self):
        import frappe
        # un rapport déjà écrit : la tâche reste au calendrier, détachée
        t = RO.affecter_tache(self.nom, self.emp, "2031-07-08", "09:00")["tache"]
        frappe.db.set_value(RO.DOCTYPE_TACHE, t, "rapport_visite", "pompe démontée, pièce commandée")
        r = RO.detacher_tache(self.nom, t)
        self.assertEqual((r["supprimee"], r["statut"]), (False, RO.S_RECEPTIONNEE))
        self.assertTrue(frappe.db.exists(RO.DOCTYPE_TACHE, t))
        self.assertFalse(frappe.db.get_value(RO.DOCTYPE_TACHE, t, RO.CHAMP_TACHE))
        # une tâche qui existait au calendrier avant le dossier y retourne
        RO.rattacher_tache(self.nom, self.tache.name)
        ligne = next(x for x in RO.get_data(recherche=self.nom)["machines"][0]["taches"] if x["name"] == self.tache.name)
        self.assertFalse(ligne["vierge"])
        self.assertFalse(RO.detacher_tache(self.nom, self.tache.name)["supprimee"])
        self.assertTrue(frappe.db.exists(RO.DOCTYPE_TACHE, self.tache.name))

    def test_tache_vierge(self):
        self.assertTrue(RO.tache_vierge({"status": "Open", "rapport_visite": " ", "liste_photos_avant": None}))
        self.assertFalse(RO.tache_vierge({"status": "Open", "rapport_visite": "fait"}))
        self.assertFalse(RO.tache_vierge({"status": "Open", "liste_photos_apres": '📁 "/files/a.jpg"'}))
        self.assertFalse(RO.tache_vierge({"status": "Cancelled"}))

    def test_sms_au_texte_choisi_avant_toute_reparation(self):
        """20/11/2026 : « un SMS spécifique au client », aussi quand la machine attend un technicien."""
        import frappe
        p = RO.preparer_sms(self.nom)
        self.assertEqual(p["numeros"], ["20123456"])
        self.assertFalse(p["repare"])
        self.assertEqual([m["libelle"] for m in p["modeles"]], ["Venez reprendre votre appareil", "Merci de nous rappeler", "Texte libre"])
        self.assertFalse(any("repare" in m["texte"] for m in p["modeles"]))                      # jamais « réparé » ici
        self.assertTrue(all(self.nom in m["texte"] for m in p["modeles"]))
        with self.assertRaises(frappe.ValidationError):
            RO.envoyer_sms_client(self.nom, "  ")
        with self.assertRaises(frappe.ValidationError):
            RO.envoyer_sms_client(self.nom, p["modeles"][-1]["texte"])                           # le modèle libre, rien d'écrit
        with self.assertRaises(frappe.ValidationError):
            RO.envoyer_sms_client(self.nom, "x" * (RO.SMS_MAX + 1))
        r = RO.envoyer_sms_client(self.nom, "Bonjour, la pièce   arrive mardi : réparation prévue mercredi. Aquaworld")
        self.assertTrue(r["simule"])                                                             # dev : jamais un vrai SMS
        self.assertEqual(r["texte"], "Bonjour, la piece arrive mardi : reparation prevue mercredi. Aquaworld")
        self.assertEqual(r["statut"], RO.S_RECEPTIONNEE)                                         # l'état ne bouge pas
        self.assertFalse(frappe.db.get_value(RO.DOCTYPE, self.nom, "sms_pret_le"))               # pas « client prévenu »
        traces = frappe.get_all("Comment", filters={"reference_doctype": RO.DOCTYPE, "reference_name": self.nom}, pluck="content")
        self.assertTrue(any(c.startswith("📲 SMS") and "piece arrive mardi" in c for c in traces))

    def test_sms_au_texte_choisi_machine_reparee(self):
        import frappe
        frappe.db.set_value(RO.DOCTYPE, self.nom, {"statut": RO.S_REPAREE, "date_reparee": frappe.utils.now_datetime()})
        p = RO.preparer_sms(self.nom)
        self.assertTrue(p["repare"])
        self.assertEqual(p["modeles"][0]["libelle"], "Votre appareil est prêt")
        r = RO.envoyer_sms_client(self.nom, p["modeles"][0]["texte"] + " Ouvert samedi matin.")
        self.assertEqual(r["statut"], RO.S_PRETE)
        self.assertTrue(frappe.db.get_value(RO.DOCTYPE, self.nom, "sms_pret_le"))

    def test_un_dossier_d_un_employe_parti_reste_accessible(self):
        """Salma (20/11/2026) : « pas permis » sur les dossiers dont le technicien a quitté — ses permissions par
        employé s'appliquaient au champ Responsable. Le champ en est dispensé, et un parti n'est plus proposé."""
        import frappe, json
        from pathlib import Path
        champs = json.loads((Path(RO.__file__).parent / "customize_erpnext/doctype/machine_reparation/machine_reparation.json").read_text())["fields"]
        self.assertEqual(next(f for f in champs if f["fieldname"] == "responsable").get("ignore_user_permissions"), 1)
        self.assertEqual(frappe.get_meta(RO.DOCTYPE).get_field("responsable").ignore_user_permissions, 1)
        parti = frappe.db.get_value("Employee", {"status": "Left"}, "name")
        if not parti:
            self.skipTest("aucun employé parti")
        frappe.db.set_value(RO.DOCTYPE, self.nom, "responsable", parti)
        m = RO.get_data(recherche=self.nom)["machines"][0]
        self.assertEqual((m["responsable"], m["responsable_actif"]), (parti, None))
        limite = frappe.db.sql("""select up.user from `tabUser Permission` up join tabUser u on u.name = up.user and u.enabled = 1
                                  where up.allow = 'Employee' and up.user in (select parent from `tabHas Role` where role = 'Sales User')
                                  limit 1""")
        if not limite:
            self.skipTest("aucun compte limité par employé")
        self.assertTrue(frappe.has_permission(RO.DOCTYPE, "write", doc=self.nom, user=limite[0][0]))

    def test_machine_abandonnee_une_tache_la_fait_demonter(self):
        """20/11/2026 : « si un client ne veut plus reprendre sa machine : machine à démonter, jeter le reste,
        avec une tâche pour faire ça »."""
        import frappe
        reparation = RO.affecter_tache(self.nom, self.emp, "2031-07-07", "09:00")["tache"]         # elle n'aura pas lieu
        p = RO.preparer_demontage(self.nom)
        self.assertEqual((p["ouvertes"], p["repare"], p["numeros"]), (1, False, ["20123456"]))
        with self.assertRaises(frappe.ValidationError):
            RO.planifier_demontage(self.nom, " ", self.emp, "2031-07-09")                            # le motif est obligatoire
        r = RO.planifier_demontage(self.nom, "Le client ne veut plus la reprendre : au téléphone le 20/11",
                                   self.emp, "2031-07-09", heure="10:15", sms=1)
        self.assertEqual(r["statut"], RO.S_DEMONTAGE)
        self.assertTrue(r["sms"]["simule"] and "demonte le 09/07/2031" in r["sms"]["texte"])
        # la réparation prévue est sortie du calendrier (son numéro, libéré, peut resservir à la tâche de démontage)
        self.assertEqual(frappe.get_all(RO.DOCTYPE_TACHE, filters={RO.CHAMP_TACHE: self.nom}, pluck="name"), [r["tache"]])
        self.assertTrue(reparation)
        t = frappe.get_doc(RO.DOCTYPE_TACHE, r["tache"])
        self.assertEqual((t.custom_type_dintervention, t.get(RO.CHAMP_TACHE), t.custom_choix_du_staff, str(t.starts_on)[:16], t.dans_local),
                         (RO.TYPE_DEMONTAGE, self.nom, self.emp, "2031-07-09 10:15", "Oui"))
        self.assertEqual((t.ends_on - t.starts_on).total_seconds(), RO.DUREE_DEMONTAGE * 60)
        self.assertTrue(t.titre.startswith("🧩 Démontage et débarras: %s" % self.nom))
        self.assertTrue(t.subject.startswith("Démontage et débarras de l'osmoseur %s" % self.nom))
        self.assertIn("garder les pièces réutilisables, débarrasser le reste", t.subject)
        self.assertIn("au téléphone le 20/11", t.subject)
        m = RO.get_data(recherche=self.nom)["machines"][0]
        self.assertEqual((m["statut"], m["groupe"], m["tache_demontage"], str(m["date_prevue"]), m["sans_reparation"]),
                         (RO.S_DEMONTAGE, RO.G_REPARER, t.name, "2031-07-09", 1))
        self.assertTrue(m["motif_sortie"].startswith("Abandonnée — Le client ne veut plus"))
        self.assertFalse(frappe.db.get_value(RO.DOCTYPE, self.nom, "sms_pret_le"))                  # pas « machine prête »
        for refuse in (lambda: RO.rendre(self.nom, code="bon"), lambda: RO.detacher_tache(self.nom, t.name),
                       lambda: RO.planifier_demontage(self.nom, "encore", self.emp, "2031-07-10")):
            with self.assertRaises(frappe.ValidationError):
                refuse()
        # la clôture de la tâche ferme le dossier, et on ne revient pas dessus
        t.rapport_visite = "pompe et transformateur gardés, le reste jeté"
        t.status = "Completed"
        t.flags.ignore_permissions = True
        t.save()
        m = frappe.db.get_value(RO.DOCTYPE, self.nom, ["statut", "date_rendue", "reglement_sortie", "sans_reparation", "rendue_par"], as_dict=True)
        self.assertEqual((m.statut, m.reglement_sortie, m.sans_reparation), (RO.S_RENDUE, "machine démontée", 1))
        self.assertTrue(m.date_rendue and m.rendue_par)
        with self.assertRaises(frappe.ValidationError):
            RO.annuler_sortie(self.nom)

    def test_annuler_le_demontage_rend_le_dossier_a_son_etat(self):
        import frappe
        # pas encore réparée : redevient « sans technicien »
        r = RO.planifier_demontage(self.nom, "Client injoignable : 3 appels sans réponse", self.emp, "2031-07-09")
        self.assertEqual(RO.annuler_demontage(self.nom)["statut"], RO.S_RECEPTIONNEE)
        self.assertFalse(frappe.db.exists(RO.DOCTYPE_TACHE, r["tache"]))
        m = frappe.db.get_value(RO.DOCTYPE, self.nom, ["tache_demontage", "motif_sortie", "sans_reparation", "responsable"], as_dict=True)
        self.assertEqual((m.tache_demontage, m.motif_sortie, m.sans_reparation, m.responsable), (None, None, 0, None))
        # déjà réparée : la tâche de démontage supprimée au calendrier la rend « à rendre », date de réparation gardée
        rep = frappe.get_doc(RO.DOCTYPE_TACHE, RO.affecter_tache(self.nom, self.emp, "2031-07-07", "09:00")["tache"])
        rep.update({"dispense_photos": 1, "rapport_visite": "réparée", "status": "Completed"})
        rep.flags.ignore_permissions = True
        rep.save()
        reparee = frappe.db.get_value(RO.DOCTYPE, self.nom, ["statut", "date_reparee"], as_dict=True)
        self.assertEqual(reparee.statut, RO.S_REPAREE)
        r = RO.planifier_demontage(self.nom, "Le client ne veut plus la reprendre : passé au magasin", self.emp, "2031-07-11")
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, self.nom, "sans_reparation"), 0)
        frappe.delete_doc(RO.DOCTYPE_TACHE, r["tache"], ignore_permissions=True, force=True)
        m = frappe.db.get_value(RO.DOCTYPE, self.nom, ["statut", "date_reparee", "tache_demontage"], as_dict=True)
        self.assertEqual((m.statut, m.date_reparee, m.tache_demontage), (RO.S_REPAREE, reparee.date_reparee, None))

    def test_un_demontage_en_retard_est_rappele(self):
        jour = getdate("2026-11-20")
        self.assertIsNone(RO.attente({"statut": RO.S_DEMONTAGE, "date_prevue": "2026-11-20"}, jour))
        a = RO.attente({"statut": RO.S_DEMONTAGE, "date_prevue": "2026-11-18"}, jour)
        self.assertEqual(a, {"motif": "demontage", "jours": 2})
        self.assertTrue(RO.a_verifier({"statut": RO.S_DEMONTAGE, "date_prevue": "2026-11-19"}, jour, 3))
        self.assertEqual(RO.LIBELLES_ATTENTE["demontage"].format(2), "démontage prévu il y a 2 j, pas clôturé")
        self.assertEqual(RO.groupe_de(RO.S_DEMONTAGE), RO.G_REPARER)

    def test_refus_type_et_statut(self):
        import frappe
        livraison = frappe.get_doc({"doctype": RO.DOCTYPE_TACHE, "custom_type_dintervention": "Livraison", "custom_choix_du_staff": self.emp,
                                    "custom_client": self.client, "starts_on": "2031-06-07 09:00:00", "status": "Open"})
        livraison.flags.ignore_permissions = True
        livraison.insert()
        with self.assertRaises(frappe.ValidationError):
            RO.rattacher_tache(self.nom, livraison.name)
        frappe.db.set_value(RO.DOCTYPE, self.nom, "statut", RO.S_REPAREE)
        with self.assertRaises(frappe.ValidationError):
            RO.rattacher_tache(self.nom, self.tache.name)


class TestRepriseReglesPures(unittest.TestCase):
    """Machine reprise chez le client pendant une intervention (23/11/2026) : les règles, sans base."""

    def test_en_route_seulement_si_reprise_et_pas_receptionnee(self):
        self.assertTrue(RO.est_en_route({"statut": RO.S_RECEPTION, "tache_origine": "T1"}))
        self.assertFalse(RO.est_en_route({"statut": RO.S_RECEPTION, "tache_origine": None}))      # déposée par son client
        self.assertFalse(RO.est_en_route({"statut": RO.S_PLANIFIEE, "tache_origine": "T1"}))       # déjà au magasin

    def test_rappel_des_le_lendemain(self):
        m = {"name": "OSM-1", "statut": RO.S_RECEPTION, "tache_origine": "T1", "date_reception": "2026-11-23 10:30:00"}
        self.assertEqual(RO.attente(m, getdate("2026-11-23")), {"motif": "en_route", "jours": 0})
        self.assertFalse(RO.a_verifier(m, getdate("2026-11-23"), 3))        # le jour même : elle est en route
        self.assertTrue(RO.a_verifier(m, getdate("2026-11-24"), 3))         # le lendemain, sans attendre les 3 jours
        # une réception ordinaire en cours n'est pas rappelée
        self.assertIsNone(RO.attente({"statut": RO.S_RECEPTION, "date_reception": "2026-11-20 10:00:00"}, getdate("2026-11-24")))
        tri = RO.a_rappeler([m], getdate("2026-11-25"), 3, 3)
        self.assertEqual([x["attente"]["motif"] for x in tri["rappel"]], ["en_route"])
        self.assertIn("pas encore réceptionnée", RO.composer_rappel(tri["rappel"], "http://x"))

    def test_etat_court(self):
        self.assertEqual(RO.etat_court(RO.S_RECEPTION, True), "en route vers le magasin")
        self.assertEqual(RO.etat_court(RO.S_RECEPTION, False), "réception en cours")
        self.assertEqual(RO.etat_court(RO.S_REPAREE), RO.etat_court(RO.S_PRETE))
        for statut in RO.STATUTS:
            self.assertTrue(RO.etat_court(statut))                           # chaque statut a ses mots

    def test_refus_reprise(self):
        t = {"status": "Open", "custom_type_dintervention": "Entretien", "custom_client": "C1"}
        self.assertIsNone(RO.refus_reprise(t))
        self.assertIsNone(RO.refus_reprise(dict(t, custom_type_dintervention="Visite")))
        self.assertTrue(RO.refus_reprise(dict(t, status="Completed")))
        self.assertTrue(RO.refus_reprise(dict(t, custom_type_dintervention="Livraison")))
        self.assertTrue(RO.refus_reprise(dict(t, custom_client=None)))
        self.assertTrue(RO.refus_reprise(dict(t, machine_reparation="OSM-2026-0001")))    # déjà la tâche d'un dossier
        self.assertTrue(RO.refus_reprise(t, sous_traitant=True))

    def test_duree_de_l_entretien_a_refaire(self):
        self.assertEqual(RO.duree_entretien("2026-11-23 10:00:00", "2026-11-23 10:30:00"), 30)
        self.assertEqual(RO.duree_entretien("2026-11-23 10:00:00", "2026-11-23 10:40:00"), 45)    # arrondi au cran du dessus
        self.assertEqual(RO.duree_entretien("2026-11-23 10:00:00", "2026-11-23 13:00:00"), 90)    # jamais plus de 1 h 30
        self.assertEqual(RO.duree_entretien("2026-11-23 10:00:00", None), RO.DUREE_LIVRAISON)
        self.assertIn(RO.RAISON_REPRISE, "Machine reprise à l'atelier")


class TestReprise(unittest.TestCase):
    """Reprise chez le client → dossier en route → réception au magasin → retour qui refait l'entretien. Savepoint."""

    def setUp(self):
        import frappe
        from customization_app import planning_employe as P
        if not getattr(frappe.local, "db", None):
            self.skipTest("sans site : ce circuit se joue sur de vrais documents")
        frappe.set_user("Administrator")
        frappe.db.savepoint("osm_reprise")
        if RO.RAISON_REPRISE not in (frappe.get_meta(RO.DOCTYPE_TACHE).get_field("raison_annulation").options or ""):
            self.skipTest("raison « Machine reprise à l'atelier » pas encore migrée")
        self.client = frappe.db.get_value("Customer", {"disabled": 0}, "name")
        self.emp = next((e for e in frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
                         if not P._est_sous_traitant(e)), None)
        if not self.client or not self.emp:
            self.skipTest("pas de client / employé")
        self.tache = frappe.get_doc({
            "doctype": RO.DOCTYPE_TACHE, "custom_type_dintervention": "Entretien", "custom_choix_du_staff": self.emp,
            "custom_client": self.client, "starts_on": "2030-01-07 10:00:00", "ends_on": "2030-01-07 10:45:00",
            "status": "Open", "temps": "45 min", "subject": "essai reprise"})
        self.tache.flags.ignore_permissions = True
        self.tache.flags.duree_fixee = True
        self.tache.insert()
        cfg = frappe.get_doc(RO.DOCTYPE_CONFIG)
        cfg.set("responsables", [{"employee": self.emp}])
        cfg.machines_par_jour, cfg.horizon_jours = 3, 30
        cfg.save(ignore_permissions=True)
        frappe.clear_cache(doctype=RO.DOCTYPE_CONFIG)
        self._code = RO._verifier_code
        RO._verifier_code = lambda code: code == "bon"

    def tearDown(self):
        import frappe
        RO._verifier_code = self._code
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="osm_reprise")
        frappe.clear_cache(doctype=RO.DOCTYPE_CONFIG)

    def _reprendre(self):
        return RO.reprendre_machine(self.tache.name, "/files/essai-reprise.jpg", "fuite sur le réservoir")["machine"]

    def test_la_reprise_cree_le_dossier_et_ferme_l_intervention(self):
        import frappe
        with self.assertRaises(frappe.ValidationError):
            RO.reprendre_machine(self.tache.name, "/files/essai-reprise.jpg", " ")           # la phrase est obligatoire
        with self.assertRaises(frappe.ValidationError):
            RO.reprendre_machine(self.tache.name, "", "fuite")                               # la photo aussi
        nom = self._reprendre()
        m = frappe.get_doc(RO.DOCTYPE, nom)
        self.assertEqual((m.statut, m.client, m.tache_origine, m.photo_arrivee), (RO.S_RECEPTION, self.client, self.tache.name, "/files/essai-reprise.jpg"))
        self.assertIn("fuite sur le réservoir", m.note_reception)
        self.assertFalse(m.garantie)                                                         # décidée au magasin
        t = frappe.db.get_value(RO.DOCTYPE_TACHE, self.tache.name, ["status", "raison_annulation"])
        self.assertEqual(t, ("Cancelled", RO.RAISON_REPRISE))
        self.assertEqual(RO.reprises_des_taches([self.tache.name])[self.tache.name],
                         {"dossier": nom, "statut": RO.S_RECEPTION, "etat": "en route vers le magasin"})
        self.assertIn(nom, [x.name for x in RO.dossiers_en_attente()])
        ligne = next(x for x in RO.get_data(recherche=nom)["machines"] if x["name"] == nom)
        self.assertTrue(ligne["en_route"])
        self.assertEqual(ligne["origine"]["tache"], self.tache.name)
        with self.assertRaises(frappe.ValidationError):
            self._reprendre()                                                                # une seule fois

    def test_annuler_la_reprise_rouvre_l_intervention(self):
        import frappe
        nom = self._reprendre()
        RO.annuler_reprise(nom)
        self.assertFalse(frappe.db.exists(RO.DOCTYPE, nom))
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE_TACHE, self.tache.name, ["status", "raison_annulation"]), ("Open", None))
        self.assertEqual(RO.reprises_des_taches([self.tache.name]), {})

    def test_reception_au_magasin_puis_retour_en_entretien(self):
        import frappe
        nom = self._reprendre()
        RO.enregistrer_photo(nom, "post_it", "/files/essai-post-it.jpg")
        with self.assertRaises(frappe.ValidationError):
            RO.cloturer_reception(nom, code="bon")                                           # la garantie d'abord
        etat = RO.decider_garantie(nom, "Hors garantie")
        self.assertEqual((etat["garantie"], etat["origine"]["tache"]), ("Hors garantie", self.tache.name))
        r = RO.cloturer_reception(nom, code="bon")
        self.assertEqual(r["statut"], RO.S_PLANIFIEE)
        with self.assertRaises(frappe.ValidationError):
            RO.annuler_reprise(nom)                                                          # elle est au magasin
        self.assertEqual(RO.reprises_des_taches([self.tache.name])[self.tache.name]["etat"], "réparation planifiée")
        for t in frappe.get_all(RO.DOCTYPE_TACHE, filters={RO.CHAMP_TACHE: nom, "status": "Open"}, pluck="name"):
            frappe.db.set_value(RO.DOCTYPE_TACHE, t, "status", "Completed")
        self.assertEqual(RO.synchroniser(nom), RO.S_REPAREE)
        liv = RO.planifier_livraison(nom, self.emp, "2030-01-10", "10:00")
        self.assertTrue(liv["entretien"])
        retour = frappe.get_doc(RO.DOCTYPE_TACHE, liv["tache"])
        self.assertEqual((retour.custom_type_dintervention, retour.temps), ("Entretien", "45 min"))   # l'entretien non fait, sa durée
        self.assertEqual(str(retour.ends_on)[11:16], "10:45")
        self.assertIn(self.tache.name, retour.subject)
        self.assertEqual(frappe.db.get_value(RO.DOCTYPE, nom, ["statut", "tache_livraison"]), (RO.S_LIVRAISON, retour.name))
        # la clôture de ce retour ferme le dossier, comme une livraison
        frappe.db.set_value(RO.DOCTYPE_TACHE, retour.name, "status", "Completed")
        self.assertEqual(RO.synchroniser(nom), RO.S_RENDUE)
        self.assertEqual(RO.reprises_des_taches([self.tache.name])[self.tache.name]["etat"], "rendue au client")

    def test_une_machine_deposee_par_son_client_se_livre_toujours_en_livraison(self):
        self.assertIsNone(RO.entretien_a_refaire(None))
        self.assertIsNone(RO.entretien_a_refaire(self.tache.name))          # l'entretien est ouvert : rien à refaire


class TestGabarit(unittest.TestCase):
    def test_pas_d_apostrophe_droite(self):
        """Le .html d'une page Desk finit dans une chaîne JS entre apostrophes : une seule ' casse la page (vécu le 02/10/2026)."""
        import re
        from pathlib import Path
        html = (Path(__file__).parents[1] / "customize_erpnext/page/reparation_osmoseurs/reparation_osmoseurs.html").read_text()
        self.assertNotIn("'", re.sub(r"<!--.*?-->", "", html, flags=re.S))
