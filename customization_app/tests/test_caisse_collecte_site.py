"""Double validation de la caisse — circuit complet sur le site (bench run-tests).

Les chiffres du jour (`_mesures`), les contrôles et le PDF sont simulés : on teste la MACHINE À
ÉTATS et les garde-fous, pas le rapport. Caisses « TEST-COLLECTE-* », utilisateurs de test créés
puis supprimés ; Config Caisse restaurée à la fin.
"""
from __future__ import annotations

import unittest
from unittest import mock

import frappe
from frappe.utils import flt

from customization_app import caisse_cloture as CL
from customization_app import caisse_collecte as CC

EMPLOYE = "test.collecte.employe@example.com"
CO_TITULAIRE = "test.collecte.cotitulaire@example.com"
TITULAIRE = "test.collecte.titulaire@example.com"
DELEGUE = "test.collecte.delegue@example.com"
CAISSES = {EMPLOYE: "TEST-COLLECTE-Employe", TITULAIRE: "TEST-COLLECTE-Titulaire", DELEGUE: "TEST-COLLECTE-Delegue",
           CO_TITULAIRE: "TEST-COLLECTE-CoTitulaire"}
DATE = "2026-10-01"


def _mesures_simulees(caisse, date):
    return {"encaissements_especes": 1300.0, "total_cheques": 200.0, "total_autres_modes": 0.0,
            "depenses_especes": 0.0, "data": {"recap": {"par_employe": []}, "employees": [], "anciens": {}}}


class _PatchsContacts:
    """Neutralise les deux crochets User -> Contact (création et mise à jour)."""
    def __enter__(self):
        self._p = [mock.patch("frappe.core.doctype.user.user.create_contact"),
                   mock.patch("frappe.contacts.doctype.contact.contact.update_contact")]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._p:
            p.stop()
        return False


class TestDoubleValidationCaisse(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if getattr(frappe.local, "db", None) is None:
            raise unittest.SkipTest("besoin d'un site : bench --site … run-tests --module … --skip-before-tests")
        frappe.set_user("Administrator")
        # La création d'un User fabrique/met à jour un Contact, et un Server Script maison sur
        # Contact (update_list_tel) suppose un client : on neutralise ces crochets, hors sujet ici.
        with cls._sans_contacts():
            cls._purger_contacts()
            for u in (EMPLOYE, TITULAIRE, DELEGUE, CO_TITULAIRE):
                if not frappe.db.exists("User", u):
                    frappe.get_doc({"doctype": "User", "email": u, "first_name": u.split("@")[0],
                                    "send_welcome_email": 0, "roles": [{"role": "Sales User"}]}).insert(ignore_permissions=True)
        cfg = frappe.get_doc("Config Caisse")
        cls._cfg_avant = {"responsable": cfg.responsable, "delegues": [d.user for d in cfg.delegues],
                          "responsables": [r.user for r in cfg.responsables],
                          "photo": cfg.photo_remise_obligatoire, "date_depart": cfg.date_depart,
                          "departs": [(r.caisse, r.date_depart) for r in cfg.get("departs") or []]}
        cfg.responsable = TITULAIRE
        cfg.set("responsables", [{"user": CO_TITULAIRE}])
        cfg.photo_remise_obligatoire = 0
        cfg.date_depart = None
        cfg.set("delegues", [{"user": DELEGUE}])
        cfg.save(ignore_permissions=True)
        frappe.db.commit()
        cls._nettoyer()
        cls._patches = [
            mock.patch.object(CL, "_mesures", side_effect=_mesures_simulees),
            mock.patch.object(CL, "_controles", return_value=[]),
            mock.patch.object(CL, "_controler_droits", return_value=None),
            mock.patch.object(CL, "_html_instantane", return_value="<p>test</p>"),
            mock.patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF-1.4 test"),
            # save_file valide les PDF avec pypdf : un faux PDF serait rejeté — on compte les appels.
            mock.patch("frappe.utils.file_manager.save_file", return_value=frappe._dict(file_url="/private/files/t.pdf")),
            mock.patch.object(CC, "_caisse_de", side_effect=lambda u: CAISSES.get(u)),
            # Config Caisse refuse une « date par caisse » sur un nom inconnu : nos caisses de test sont connues.
            mock.patch("customization_app.rapport_caisse_journaliere.noms_caisses",
                       return_value=list(CAISSES.values()) + ["TEST-COLLECTE-Jamais"]),
            # En dernier : test_le_titulaire_peut_compter… arrête self._patches[2] par sa position.
            mock.patch.object(CL, "_taches_sans_commande", return_value=[]),
        ]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        frappe.set_user("Administrator")
        cls._nettoyer()
        cfg = frappe.get_doc("Config Caisse")
        cfg.responsable = cls._cfg_avant["responsable"]
        cfg.photo_remise_obligatoire = cls._cfg_avant["photo"]
        cfg.date_depart = cls._cfg_avant["date_depart"]
        cfg.set("departs", [{"caisse": c, "date_depart": d} for c, d in cls._cfg_avant["departs"]])
        cfg.set("delegues", [{"user": u} for u in cls._cfg_avant["delegues"]])
        cfg.set("responsables", [{"user": u} for u in cls._cfg_avant["responsables"]])
        cfg.save(ignore_permissions=True)
        with cls._sans_contacts():
            for u in (EMPLOYE, TITULAIRE, DELEGUE, CO_TITULAIRE):
                if frappe.db.exists("User", u):
                    frappe.delete_doc("User", u, ignore_permissions=True, force=True)
            cls._purger_contacts()
        frappe.db.commit()

    @staticmethod
    def _sans_contacts():
        return _PatchsContacts()

    @staticmethod
    def _purger_contacts():
        for n in frappe.get_all("Contact", filters={"email_id": ["in", list(CAISSES)]}, pluck="name"):
            frappe.delete_doc("Contact", n, ignore_permissions=True, force=True)

    @classmethod
    def _nettoyer(cls):
        for n in frappe.get_all("Cloture Caisse", filters={"caisse": ["like", "TEST-COLLECTE-%"]}, pluck="name"):
            frappe.db.set_value("Cloture Caisse", n, "docstatus", 2, update_modified=False)
            frappe.delete_doc("Cloture Caisse", n, ignore_permissions=True, force=True)
        for n in frappe.get_all("Passation Caisse", filters={"delegue": ["in", list(CAISSES)]}, pluck="name"):
            frappe.db.set_value("Passation Caisse", n, "docstatus", 2, update_modified=False)
            frappe.delete_doc("Passation Caisse", n, ignore_permissions=True, force=True)
        frappe.db.commit()

    def setUp(self):
        self._nettoyer()

    # ── outils ──
    def _compter(self, user, remises=1300.0, comptees=1315.5, nb_cheques=1, nb_traites=0, caisse=None, rouvrir=None):
        frappe.set_user(user)
        r = CL.valider(caisse or CAISSES[user], DATE, especes_comptees=comptees, note="t",
                       justifications="{}", especes_remises=remises,
                       nb_cheques_remis=nb_cheques, nb_traites_remis=nb_traites, rouvrir=rouvrir)
        return frappe.get_doc("Cloture Caisse", r["name"])

    # ── tests ──
    def test_employe_compte_et_remet_brouillon_a_collecter(self):
        cl = self._compter(EMPLOYE)
        self.assertEqual(cl.docstatus, 0)
        self.assertEqual(cl.statut, CC.STATUT_A_COLLECTER)
        self.assertEqual(flt(cl.fond_conserve, 3), 15.5)
        self.assertEqual(cl.valide_par, EMPLOYE)
        self.assertIsNone(cl.collecte_par)
        # Compter deux fois le même jour est refusé.
        with self.assertRaises(frappe.ValidationError):
            self._compter(EMPLOYE)

    def test_remise_superieure_au_comptage_refusee(self):
        frappe.set_user(EMPLOYE)
        with self.assertRaises(frappe.ValidationError):
            CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=100, justifications="{}", especes_remises=101)
        with self.assertRaises(frappe.ValidationError):
            CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=None, justifications="{}", especes_remises=0)

    def test_titulaire_valide_sa_caisse_seul(self):
        cl = self._compter(TITULAIRE, remises=None)
        self.assertEqual(cl.docstatus, 1)
        self.assertEqual(cl.statut, CC.STATUT_VALIDEE)
        self.assertEqual(cl.validation_seule, 1)
        self.assertEqual(flt(cl.especes_remises), 0)
        # Le PDF instantané est attaché (save_file simulé : on vérifie l'appel et sa cible).
        import frappe.utils.file_manager as fm
        appels = [c for c in fm.save_file.call_args_list if c.args[3] == cl.name]
        self.assertEqual(len(appels), 1)
        self.assertEqual(appels[0].args[2], "Cloture Caisse")
        self.assertTrue(appels[0].args[0].endswith(".pdf"))

    def test_collecte_identique_valide_avec_deux_signatures(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        r = CC.collecter(cl.name, especes_recues=1300, nb_cheques_recus=1, nb_traites_recus=0)
        cl.reload()
        self.assertEqual(r["statut"], CC.STATUT_VALIDEE)
        self.assertEqual(cl.docstatus, 1)
        self.assertEqual(cl.collecte_par, TITULAIRE)
        self.assertEqual(cl.valide_par, EMPLOYE)
        self.assertEqual(cl.par_delegation, 0)
        self.assertIsNone(cl.passation)
        self.assertEqual(flt(cl.ecart_remise), 0)
        # Le report du lendemain = compté − remis = le fond conservé.
        self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-02"), 15.5)

    def test_employe_ne_collecte_pas_sa_caisse_et_un_non_collecteur_est_refuse(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(EMPLOYE)
        with self.assertRaises(frappe.ValidationError):
            CC.collecter(cl.name, especes_recues=1300, nb_cheques_recus=1)
        cl2 = self._compter(TITULAIRE, remises=None)   # validation seule : rien à collecter
        frappe.set_user(TITULAIRE)
        with self.assertRaises(frappe.ValidationError):
            CC.collecter(cl2.name, especes_recues=1)

    def test_titulaire_valide_directement_une_caisse_qu_il_a_comptee(self):
        # Le responsable compte la caisse d'un employé absent : la carte de collecte lui offre
        # « Valider directement » — une seule signature, tracée (demande 04/10/2026).
        frappe.set_user(TITULAIRE)
        r = CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=700, justifications="{}", especes_remises=700)
        liste = CC.a_collecter(DATE)
        c = next(x for x in liste["clotures"] if x["name"] == r["name"])
        self.assertTrue(c["validation_directe"]); self.assertTrue(c["collectable"]); self.assertFalse(c["auto_passation"])
        res = CC.collecter(r["name"], especes_recues=700)
        self.assertTrue(res["validation_directe"])
        cl = frappe.get_doc("Cloture Caisse", r["name"])
        self.assertEqual(cl.docstatus, 1)
        self.assertEqual(cl.validation_seule, 1)
        self.assertEqual(cl.collecte_par, TITULAIRE)
        self.assertIsNone(cl.passation)
        self.assertIn("valide directement", cl.echanges)
        self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-02"), 0.0)   # 700 comptées − 700 remises

    def test_boucle_ecart_puis_le_responsable_tranche(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        r = CC.collecter(cl.name, especes_recues=1250, nb_cheques_recus=1, commentaire="il manque 50")
        self.assertEqual(r["statut"], CC.STATUT_ECART)
        cl.reload()
        self.assertEqual(cl.docstatus, 0)
        self.assertEqual(cl.tours_ecart, 1)
        self.assertEqual(flt(cl.ecart_remise), -50)
        self.assertIn("il manque 50", cl.echanges)
        # Impossible de trancher avant la réponse de l'employé : la clôture est en écart, pas « à collecter ».
        # L'employé maintient.
        frappe.set_user(EMPLOYE)
        CC.repondre_ecart(cl.name, accepter=0, commentaire="j'ai bien compté")
        cl.reload()
        self.assertEqual(cl.statut, CC.STATUT_A_COLLECTER)
        # Le responsable recompte : toujours 1250 -> sans forcer, nouvel écart ; avec forcer, il tranche.
        frappe.set_user(TITULAIRE)
        CC.collecter(cl.name, especes_recues=1250, nb_cheques_recus=1, forcer=1, commentaire="recompté deux fois")
        cl.reload()
        self.assertEqual(cl.docstatus, 1)
        self.assertEqual(cl.statut, CC.STATUT_VALIDEE)
        self.assertEqual(flt(cl.ecart_remise), -50)
        self.assertIn("tranche", cl.echanges)

    def test_employe_accepte_le_chiffre_du_responsable(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        CC.collecter(cl.name, especes_recues=1250, nb_cheques_recus=1)
        frappe.set_user(EMPLOYE)
        r = CC.repondre_ecart(cl.name, accepter=1)
        cl.reload()
        self.assertEqual(r["statut"], CC.STATUT_VALIDEE)
        self.assertEqual(cl.docstatus, 1)
        self.assertEqual(flt(cl.ecart_remise), -50)          # l'écart reste tracé
        self.assertEqual(flt(cl.fond_conserve, 3), 65.5)      # 1315,5 − 1250 réellement partis
        self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-02"), 65.5)

    def test_forcer_sans_tour_d_ecart_est_ignore(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        r = CC.collecter(cl.name, especes_recues=1000, nb_cheques_recus=1, forcer=1)
        self.assertEqual(r["statut"], CC.STATUT_ECART)

    def test_delegue_collecte_par_passation_puis_titulaire_recoit(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(DELEGUE)
        r = CC.collecter(cl.name, especes_recues=1300, nb_cheques_recus=1)
        cl.reload()
        self.assertEqual(cl.docstatus, 1)
        self.assertEqual(cl.par_delegation, 1)
        self.assertTrue(cl.passation)
        p = frappe.get_doc("Passation Caisse", cl.passation)
        self.assertEqual(p.statut, CC.PAS_OUVERTE)
        self.assertEqual(p.delegue, DELEGUE)
        self.assertEqual(flt(p.total_especes), 1300)
        self.assertEqual(p.total_cheques, 1)
        # Sa propre caisse, en l'absence du titulaire : versée telle quelle dans la même passation.
        cl2 = self._compter(DELEGUE, remises=500, comptees=500, nb_cheques=0)
        frappe.set_user(DELEGUE)
        CC.collecter(cl2.name, especes_recues=0)
        p.reload()
        self.assertEqual(len(p.clotures), 2)
        self.assertEqual(flt(p.total_especes), 1800)
        # Le délégué ne reçoit pas sa propre passation ; le titulaire la reçoit.
        with self.assertRaises(frappe.ValidationError):
            CC.valider_passation(p.name, especes_recues=1800, nb_cheques_recus=1)
        CC.remettre_passation(p.name, note="dans l'enveloppe bleue")
        frappe.set_user(TITULAIRE)
        r = CC.valider_passation(p.name, especes_recues=1700, nb_cheques_recus=1)
        self.assertEqual(r["statut"], CC.PAS_ECART)
        frappe.set_user(DELEGUE)
        r = CC.repondre_ecart_passation(p.name, accepter=1, commentaire="ok")
        self.assertEqual(r["statut"], CC.PAS_VALIDEE)
        p.reload()
        self.assertEqual(p.docstatus, 1)
        self.assertEqual(flt(p.ecart), -100)
        self.assertEqual(p.responsable, TITULAIRE)

    def test_date_de_depart_remet_les_caisses_a_zero(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        CC.collecter(cl.name, especes_recues=1300, nb_cheques_recus=1)
        self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-02"), 15.5)
        try:
            frappe.db.set_single_value("Config Caisse", "date_depart", "2026-10-02")
            frappe.clear_cache(doctype="Config Caisse")
            self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-02"), 0.0)   # à zéro dès le jour J
            self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-01"), 0.0)   # avant : rien d'antérieur de toute façon
            frappe.db.set_single_value("Config Caisse", "date_depart", "2026-10-01")
            frappe.clear_cache(doctype="Config Caisse")
            self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-02"), 15.5)  # la clôture du 01 est ≥ départ
            # Date PAR CAISSE : celle-ci repart à zéro le 02 alors que la générale est au 01.
            cfg = frappe.get_doc("Config Caisse")
            cfg.set("departs", [{"caisse": CAISSES[EMPLOYE], "date_depart": "2026-10-02"}])
            cfg.save(ignore_permissions=True)
            frappe.clear_cache(doctype="Config Caisse")
            self.assertEqual(CL._ouverture(CAISSES[EMPLOYE], "2026-10-02"), 0.0)
            self.assertEqual(CL._ouverture(CAISSES[TITULAIRE], "2026-10-02"), 0.0)   # pas de clôture : 0 de toute façon
        finally:
            cfg = frappe.get_doc("Config Caisse")
            cfg.date_depart = None
            cfg.set("departs", [])
            cfg.save(ignore_permissions=True)
            frappe.clear_cache(doctype="Config Caisse")

    def test_co_titulaire_collecte_sans_passation_et_valide_sa_caisse_seul(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(CO_TITULAIRE)
        self.assertEqual(CC.role_collecte(), "titulaire")
        CC.collecter(cl.name, especes_recues=1300, nb_cheques_recus=1)
        cl.reload()
        self.assertEqual(cl.docstatus, 1)
        self.assertEqual(cl.par_delegation, 0)
        self.assertIsNone(cl.passation)
        self.assertEqual(cl.collecte_par, CO_TITULAIRE)
        mienne = self._compter(CO_TITULAIRE, remises=None)
        self.assertEqual(mienne.docstatus, 1)
        self.assertEqual(mienne.validation_seule, 1)

    def test_delegue_hors_periode_redevient_simple_employe(self):
        cfg = frappe.get_doc("Config Caisse")
        try:
            cfg.set("delegues", [{"user": DELEGUE, "date_debut": "2026-01-01", "date_fin": "2026-01-31"}])
            cfg.save(ignore_permissions=True)
            frappe.clear_cache(doctype="Config Caisse")
            self.assertIsNone(CC.role_collecte(DELEGUE))
            self.assertFalse(CC.peut_voir_toutes_les_caisses(DELEGUE))
            self.assertIn(DELEGUE, CC.config()["hors_periode"])
            # Même un membre de la direction codée en dur perd ses droits hors période.
            with mock.patch.object(CL, "DIRECTION", set(CL.DIRECTION) | {DELEGUE}):
                self.assertIsNone(CC.role_collecte(DELEGUE))
            cfg.set("delegues", [{"user": DELEGUE, "date_debut": frappe.utils.nowdate(), "date_fin": None}])
            cfg.save(ignore_permissions=True)
            frappe.clear_cache(doctype="Config Caisse")
            self.assertEqual(CC.role_collecte(DELEGUE), "delegue")
            self.assertTrue(CC.peut_voir_toutes_les_caisses(DELEGUE))
        finally:
            cfg.set("delegues", [{"user": DELEGUE}])
            cfg.save(ignore_permissions=True)
            frappe.clear_cache(doctype="Config Caisse")

    def test_un_employe_ne_voit_que_sa_caisse(self):
        from customization_app import rapport_caisse_journaliere as RCJ
        frappe.set_user(EMPLOYE)
        self.assertFalse(CC.peut_voir_toutes_les_caisses())
        with mock.patch.object(RCJ, "_ma_caisse", return_value="TEST-COLLECTE-Employe"):
            data = RCJ.get_data(DATE, DATE, employe="")                 # « Tous » demandé explicitement
            self.assertTrue(data["restreint"])
            self.assertEqual(data["employe"], "TEST-COLLECTE-Employe")
            self.assertEqual(data["employes"], ["TEST-COLLECTE-Employe"])
            data = RCJ.get_data(DATE, DATE, employe="TEST-COLLECTE-Titulaire")   # la caisse d'un collègue
            self.assertEqual(data["employe"], "TEST-COLLECTE-Employe")
        # La bannière d'une autre caisse reste muette pour lui.
        autre = self._compter(TITULAIRE, remises=None)
        frappe.set_user(EMPLOYE)
        self.assertIsNone(CL.cloture_info(CAISSES[TITULAIRE], DATE))
        frappe.set_user(TITULAIRE)
        self.assertEqual(CL.cloture_info(CAISSES[TITULAIRE], DATE)["name"], autre.name)
        frappe.set_user(CO_TITULAIRE)
        self.assertTrue(CC.peut_voir_toutes_les_caisses())
        with mock.patch.object(RCJ, "_ma_caisse", return_value="TEST-COLLECTE-CoTitulaire"):
            self.assertFalse(RCJ.get_data(DATE, DATE, employe="")["restreint"])

    def test_employe_rouvre_son_comptage_tant_que_pas_collecte(self):
        cl = self._compter(EMPLOYE)
        # Rouvrir = même numéro, nouvelles valeurs, trace dans les échanges.
        cl2 = self._compter(EMPLOYE, remises=1200, comptees=1250, nb_cheques=0, rouvrir=cl.name)
        self.assertEqual(cl2.name, cl.name)
        self.assertEqual(flt(cl2.especes_remises), 1200)
        self.assertEqual(flt(cl2.fond_conserve), 50)
        self.assertEqual(cl2.statut, CC.STATUT_A_COLLECTER)
        self.assertIn("rouvre et modifie", cl2.echanges)
        # Un collègue ne rouvre pas le comptage d'un autre.
        frappe.set_user(DELEGUE)
        with self.assertRaises(frappe.ValidationError):
            CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=1, justifications="{}", especes_remises=1, rouvrir=cl.name)
        # Après un écart signalé : réouverture possible, les chiffres du responsable sont effacés.
        frappe.set_user(TITULAIRE)
        CC.collecter(cl.name, especes_recues=1100, nb_cheques_recus=0)
        cl3 = self._compter(EMPLOYE, remises=1100, comptees=1250, nb_cheques=0, rouvrir=cl.name)
        self.assertEqual(cl3.statut, CC.STATUT_A_COLLECTER)
        self.assertIsNone(cl3.collecte_par)
        self.assertEqual(flt(cl3.especes_recues), 0)
        self.assertEqual(cl3.tours_ecart, 1)                     # l'historique du tour reste
        # Le responsable recollecte : conforme -> validée. Ensuite plus de réouverture.
        frappe.set_user(TITULAIRE)
        CC.collecter(cl.name, especes_recues=1100, nb_cheques_recus=0)
        cl3.reload()
        self.assertEqual(cl3.docstatus, 1)
        frappe.set_user(EMPLOYE)
        with self.assertRaises(frappe.ValidationError):
            self._compter(EMPLOYE, rouvrir=cl.name)
        # Et `etat` annonce la réouverture possible pendant l'attente.
        cl4 = self._compter(DELEGUE, remises=100, comptees=100, nb_cheques=0)
        frappe.set_user(DELEGUE)
        e = CL.etat(CAISSES[DELEGUE], DATE)
        self.assertEqual(e["en_attente"]["name"], cl4.name)
        self.assertTrue(e["en_attente"]["rouvrable"])
        self.assertEqual(flt(e["en_attente"]["especes_remises"]), 100)

    def test_le_titulaire_de_la_caisse_rouvre_meme_si_un_autre_a_compte(self):
        # La direction compte la caisse de l'employé (absent) ; l'employé, de retour, peut rouvrir.
        frappe.set_user("Administrator")
        r = CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=500, justifications="{}", especes_remises=500)
        frappe.set_user(EMPLOYE)
        e = CL.etat(CAISSES[EMPLOYE], DATE)
        self.assertTrue(e["en_attente"]["rouvrable"])
        info = CL.cloture_info(CAISSES[EMPLOYE], DATE)
        self.assertTrue(info["mienne"])
        cl = self._compter(EMPLOYE, remises=450, comptees=500, nb_cheques=0, rouvrir=r["name"])
        self.assertEqual(cl.name, r["name"])
        self.assertEqual(flt(cl.especes_remises), 450)
        self.assertEqual(cl.valide_par, EMPLOYE)
        # Un collègue (délégué compris) ne rouvre pas, ne répond pas, n'annule pas.
        frappe.set_user(DELEGUE)
        with self.assertRaises(frappe.ValidationError):
            CC.annuler_comptage(cl.name)

    def test_le_titulaire_peut_compter_la_caisse_d_un_autre_mais_pas_un_employe(self):
        # _controler_droits est neutralisé par la classe : on le rétablit le temps de ce test.
        patch_droits = self._patches[2]
        patch_droits.stop()
        try:
            with mock.patch.object(CL, "_ma_caisse", side_effect=lambda employees, users: CAISSES.get(frappe.session.user)):
                frappe.set_user(TITULAIRE)
                CL._controler_droits(CAISSES[EMPLOYE])                 # titulaire : OK
                with self.assertRaises(frappe.ValidationError):
                    CL._controler_droits(CL.CAISSE_GLOBALE)             # la globale reste à la direction
                frappe.set_user(DELEGUE)
                with self.assertRaises(frappe.ValidationError):
                    CL._controler_droits(CAISSES[EMPLOYE])             # un délégué ne compte pas pour un autre
                frappe.set_user(EMPLOYE)
                CL._controler_droits(CAISSES[EMPLOYE])                 # sa propre caisse
        finally:
            patch_droits.start()

    def test_config_refuse_une_caisse_inconnue_ou_en_double(self):
        frappe.set_user("Administrator")
        cfg = frappe.get_doc("Config Caisse")
        try:
            cfg.set("departs", [{"caisse": "Quelqu'un Qui N'existe Pas", "date_depart": "2026-10-05"}])
            with self.assertRaises(frappe.ValidationError):
                cfg.save(ignore_permissions=True)
            cfg.reload()
            cfg.set("departs", [{"caisse": CAISSES[EMPLOYE], "date_depart": "2026-10-05"},
                                {"caisse": " %s " % CAISSES[EMPLOYE], "date_depart": "2026-10-06"}])
            with self.assertRaises(frappe.ValidationError):
                cfg.save(ignore_permissions=True)
            cfg.reload()
            cfg.set("departs", [{"caisse": " %s " % CAISSES[EMPLOYE], "date_depart": "2026-10-05"}])
            cfg.save(ignore_permissions=True)                       # espaces nettoyés
            self.assertEqual(CC.config()["departs"], {CAISSES[EMPLOYE]: "2026-10-05"})
        finally:
            cfg.reload(); cfg.set("departs", []); cfg.save(ignore_permissions=True)
            frappe.clear_cache(doctype="Config Caisse")

    def test_soumission_directe_refusee(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user("Administrator")
        doc = frappe.get_doc("Cloture Caisse", cl.name)
        with self.assertRaises(frappe.ValidationError):
            doc.submit()
        doc.reload()
        self.assertEqual(doc.docstatus, 0)
        p = frappe.get_doc({"doctype": "Passation Caisse", "delegue": DELEGUE, "statut": CC.PAS_OUVERTE})
        p.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            p.submit()

    def test_annuler_comptage_tant_que_personne_n_a_touche(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        with self.assertRaises(frappe.ValidationError):
            CC.annuler_comptage(cl.name)       # pas sa caisse (et pas direction)
        frappe.set_user(EMPLOYE)
        CC.annuler_comptage(cl.name)
        self.assertFalse(frappe.db.exists("Cloture Caisse", cl.name))
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        CC.collecter(cl.name, especes_recues=1000, nb_cheques_recus=1)
        frappe.set_user(EMPLOYE)
        CC.repondre_ecart(cl.name, accepter=0, commentaire="non")
        with self.assertRaises(frappe.ValidationError):
            CC.annuler_comptage(cl.name)       # déjà traité par le responsable

    def test_resume_et_a_collecter(self):
        cl = self._compter(EMPLOYE)
        frappe.set_user(TITULAIRE)
        res = CC.resume(TITULAIRE, DATE)
        self.assertEqual(res["role"], "titulaire")
        self.assertGreaterEqual(res["a_collecter"], 1)
        with mock.patch.object(CL, "get_data", return_value={"recap": {"par_employe": [
                {"employe": "TEST-COLLECTE-Jamais", "total": 80, "par_mode": {"Espèces": 80}},
                {"employe": CAISSES[EMPLOYE], "total": 1300, "par_mode": {"Espèces": 1300}}]}}):
            liste = CC.a_collecter(DATE)
        noms = [c["name"] for c in liste["clotures"]]
        self.assertIn(cl.name, noms)
        self.assertEqual([n["caisse"] for n in liste["non_comptees"]], ["TEST-COLLECTE-Jamais"])
        self.assertFalse(liste["par_delegation"])
        frappe.set_user(EMPLOYE)
        with self.assertRaises(frappe.ValidationError):
            CC.a_collecter(DATE)
        self.assertIsNone(CC.resume(EMPLOYE, DATE)["role"])

    def test_justifications_lues_contestees_puis_corrigees(self):
        point = {"cle": "tache_ouverte:SO-T", "type": "tache_ouverte", "bloquant": 0, "libelle": "Tâche ouverte — SO-T"}
        ok = '{"tache_ouverte:SO-T": {"motif": "Rendez-vous reporté par le client", "commentaire": "rs"}}'
        with mock.patch.object(CL, "_controles", side_effect=lambda data: [dict(point)]):
            frappe.set_user(EMPLOYE)
            with self.assertRaises(frappe.ValidationError):            # commentaire trop court
                CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=1315.5, justifications=ok, especes_remises=1300)
            r = CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=1315.5, especes_remises=1300,
                           justifications=ok.replace('"rs"', '"le client rappelle jeudi"'))
            # Le collecteur LIT la justification sur sa carte.
            frappe.set_user(TITULAIRE)
            carte = next(c for c in CC.a_collecter(DATE)["clotures"] if c["name"] == r["name"])
            self.assertEqual(carte["controles"], [{"libelle": "Tâche ouverte — SO-T",
                                                   "justification": "Rendez-vous reporté par le client — le client rappelle jeudi"}])
            with self.assertRaises(frappe.ValidationError):            # commentaire du collecteur obligatoire
                CC.contester_justifications(r["name"], "")
            self.assertEqual(CC.contester_justifications(r["name"], "Quel jour ? Mets la date.")["statut"], CC.STATUT_JUSTIF)
            with self.assertRaises(frappe.ValidationError):            # déjà contestée
                CC.contester_justifications(r["name"], "encore une fois")
            self.assertEqual([j["name"] for j in CC.resume(EMPLOYE, DATE)["mes_justifs"]], [r["name"]])
            # Un simple employé ne conteste pas.
            frappe.set_user(EMPLOYE)
            with self.assertRaises(frappe.ValidationError):
                CC.contester_justifications(r["name"], "pas mon rôle")
            # L'employé rouvre et corrige : retour « À collecter », trace des justifications modifiées.
            cl = frappe.get_doc("Cloture Caisse", r["name"])
            e = CL.etat(CAISSES[EMPLOYE], DATE)
            self.assertTrue(e["en_attente"]["rouvrable"])
            self.assertEqual(e["controles"][0]["motifs"], CL.MOTIFS["tache"])
            CL.valider(CAISSES[EMPLOYE], DATE, especes_comptees=1315.5, especes_remises=1300, rouvrir=cl.name,
                       justifications=ok.replace('"rs"', '"le client rappelle jeudi 08/10 pour fixer"'))
            cl.reload()
            self.assertEqual(cl.statut, CC.STATUT_A_COLLECTER)
            self.assertIn("justifications modifiées", cl.echanges)
            self.assertIn("jeudi 08/10", cl.controles)
            # Contestée une seconde fois, le titulaire collecte quand même : acceptées en l'état, validée.
            frappe.set_user(TITULAIRE)
            CC.contester_justifications(cl.name, "toujours pas clair")
            CC.collecter(cl.name, especes_recues=1300, nb_cheques_recus=0)
            cl.reload()
            self.assertEqual((cl.docstatus, cl.statut), (1, CC.STATUT_VALIDEE))
            self.assertIn("justifications acceptées en l'état", cl.echanges)

    def test_caisse_globale_exige_justification_des_caisses_non_collectees(self):
        self._compter(EMPLOYE)          # comptée mais pas collectée
        data = {"recap": {"par_employe": [
            {"employe": CAISSES[EMPLOYE], "total": 1300, "par_mode": {"Espèces": 1300}},
            {"employe": "TEST-COLLECTE-Jamais", "total": 80, "par_mode": {"Espèces": 80}},
            {"employe": "TEST-COLLECTE-Cheques", "total": 80, "par_mode": {"Chèque": 80}}]}}
        points = CC.controles_globale(DATE, data)
        self.assertEqual(sorted(p["caisse"] for p in points), [CAISSES[EMPLOYE], "TEST-COLLECTE-Jamais"])
        self.assertTrue(all(not p["bloquant"] for p in points))
        self.assertIn("pas encore collectée", points[0]["libelle"])
