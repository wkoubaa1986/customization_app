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
        # 1 h 15 par réparation (et non les 2 h du type Réparation)
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
        self.assertEqual(RO.rendre(nom)["statut"], RO.S_RENDUE)
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
