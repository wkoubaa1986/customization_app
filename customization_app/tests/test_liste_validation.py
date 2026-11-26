"""Liste d'appels reprise dans l'app le 25/11/2026 (liste_validation) — règles pures : contrôle de saisie identique au
Server Script « Valider Liste appelle », validation finale aux nouvelles règles, compteur « Appel journalier », patch."""
from __future__ import annotations

import datetime
import os
import unittest
from unittest import mock

import frappe

from customization_app import liste_validation as V
from customization_app import relances_config as RC
from customization_app.Maintenance import creation_liste_appelle as L
from customization_app.patches import listes_appels_dans_l_app as P

PS = V.PAS_DE_SYSTEME


def ligne(client, appele=0, resume="", ent="Oui", sms="Oui", ech="MS1", reponse=""):
    return {"client": client, "a_été_appelé": appele, "resume_appel": resume, "intéressé_par_le_service_dentretien": ent,
            "intéressé_par_le_service_de_relance": sms, "échéancier_dentretien": ech, "reponse_client": reponse}


class TestControleSaisie(unittest.TestCase):
    """Comportement du Server Script « Valider Liste appelle » (Before Save = événement validate)."""

    def test_resume_obligatoire_pour_une_ligne_appelee(self):
        lignes = [ligne("C1", 1, "Fait lui-même"), ligne("C2", 0, ""), ligne("C3", 1, ""), ligne("C4", 1, None)]
        self.assertEqual(V.premiere_ligne_sans_resume(lignes), (3, "C3"))
        self.assertEqual(V.premiere_ligne_sans_resume(lignes[3:]), (1, "C4"))           # NULL = vide (le script le laissait passer)
        self.assertIsNone(V.premiere_ligne_sans_resume(lignes[:2]))

    def test_rendez_vous_et_livraisons_au_calendrier_memes_phrases(self):
        appels = []

        def compter(client, date, type_tache):
            appels.append((client, date, type_tache))
            return {"C1": 1, "C2": 0, "C3": 2, "C4": 0}[client]

        lignes = [ligne("C1", 1, V.RDV_PRIS), ligne("C2", 1, V.RDV_PRIS), ligne("C3", 0, V.LIVRAISON_PREVUE),
                  ligne("C4", 1, V.LIVRAISON_PREVUE), ligne("C5", 1, "Fait lui-même")]
        total, msg = V.controles_rendez_vous(lignes, "2026-11-25", compter)
        self.assertEqual(total, 3)
        self.assertEqual(msg, "Ligne :2 Client : C2 Pas d'entretien prevu dans le calendrier<br>"
                              "Ligne :3 Client : C3 A plusieurs livraisons prevus dans le calendrier<br>"
                              "Ligne :4 Client : C4 Pas de livraison prevu dans le calendrier<br>")
        self.assertEqual([a[2] for a in appels], ["Entretien", "Entretien", "Livraison", "Livraison"])
        self.assertEqual(V.controles_rendez_vous([ligne("C1", 1, V.RDV_PRIS)], "2026-11-25", lambda *a: 2)[1],
                         "Ligne :1 Client : C1 A plusieurs entretiens prevus dans le calendrier<br>")

    def test_hook_validate(self):
        doc = frappe._dict(clients=[ligne("C1", 1, V.RDV_PRIS)], date="2026-11-25", nb_r_c=None)
        with mock.patch.object(V, "_rendez_vous", return_value=1), mock.patch.object(frappe, "msgprint") as mp:
            V.controle_saisie(doc)
        self.assertEqual(doc.nb_r_c, 1) ; mp.assert_not_called()
        doc = frappe._dict(clients=[ligne("C1", 1, V.RDV_PRIS), ligne("C2", 1, "")], date="2026-11-25")
        with mock.patch.object(V, "_rendez_vous", return_value=0):
            with self.assertRaises(frappe.ValidationError) as e:
                V.controle_saisie(doc)
        self.assertIn("Merci d'indiquer le Résumé d'appel pour le client: C2 dans la ligne: 2", str(e.exception))


class TestValidationFinale(unittest.TestCase):
    def test_lignes_non_appelees(self):
        sans, avec = V.lignes_non_appelees([ligne("C1", 1, "Fait lui-même"), ligne("C2"), ligne("C3", 0, "Ne répond pas 1er appel")])
        self.assertEqual(sans, [(2, "C2")]) ; self.assertEqual([a[:2] for a in avec], [(3, "C3")])

    def test_message_bloquant_identique_au_script(self):
        self.assertEqual(V.message_bloquant([(2, "C2"), (5, "C5")]),
                         "Ligne :2 Client : C2 N'est pas encore appele<br>Ligne :5 Client : C5 N'est pas encore appele<br>")

    def test_retour_annonce(self):
        self.assertIn("« Hors secteur »", V.message_non_appelees([(1, "C<1>")], "Hors secteur"))
        self.assertIn("C&lt;1&gt;", V.message_non_appelees([(1, "C<1>")], "Hors secteur"))
        self.assertIn("« Normal »", V.retour_prevu(None))

    def test_retour_annonce_urgence(self):
        """26/11/2026 : l'ancien message promettait l'Urgence « si un rendez-vous est encore prévu », alors que la liste
        validée couvrait toujours ce rendez-vous. Il annonce maintenant ce que fait creation_liste_appelle."""
        m = V.retour_prevu("Urgence")
        self.assertIn("nouvelle liste « Urgence »", m)
        self.assertIn("rendez-vous du technicien", m)
        self.assertIn("sinon la prochaine liste « Normal »", m)
        self.assertNotIn("Normal (ou Urgence", m)

    def test_pas_de_systeme_annule_aussi_les_autres_echeanciers_du_client(self):
        """26/11/2026 : la réponse vaut pour le client ; ses autres échéanciers (articles de la ligne) sont annulés aussi.
        Non / Non n'en touche pas d'autre ; une ligne non appelée non plus."""
        lignes = [ligne("C1", 1, PS, "Non", "Non", "MS1"), ligne("C2", 1, "Autres reponses", "Non", "Non", "MS2"),
                  ligne("C3", 0, PS, "Non", "Non", "MS3")]
        a = V.actions_echeanciers(lignes, {1: ["MS1", "MS1b", "MS1c"], 2: ["MS2b"], 3: ["MS3b"]})
        par = {x["echeancier"]: x for x in a}
        self.assertEqual(set(par), {"MS1", "MS1b", "MS1c", "MS2"})
        self.assertEqual({k: v["action"] for k, v in par.items()},
                         {"MS1": V.ANNULER, "MS1b": V.ANNULER, "MS1c": V.ANNULER, "MS2": V.CONSERVER})
        self.assertEqual({v["principal"] for k, v in par.items() if k.startswith("MS1")}, {"MS1"})
        # Un autre échéancier déjà « conservé » par une autre ligne : annuler l'emporte.
        a = V.actions_echeanciers([ligne("C1", 1, "Autres reponses", "Non", "Non", "MS9"), ligne("C1", 1, PS, "Non", "Non", "MS1")],
                                  {2: ["MS9"]})
        self.assertEqual({x["echeancier"]: x["action"] for x in a}, {"MS9": V.ANNULER, "MS1": V.ANNULER})
        t = V.texte_commentaire(V.ANNULER, "LIST-1", "Normal", "2026-11-24", "2026-11-26", PS, principal="MS1", echeancier="MS1b")
        self.assertIn("autre échéancier du client ; la ligne portait MS1", t)
        self.assertNotIn("autre échéancier", V.texte_commentaire(V.ANNULER, "LIST-1", "Normal", "2026-11-24", "2026-11-26", PS,
                                                                 principal="MS1", echeancier="MS1"))

    def test_non_non_ne_promet_pas_ce_que_le_sms_ne_tient_pas(self):
        """26/11/2026 : la relance SMS ne lit pas « Non aux SMS » ; avec « relancer_non_interesses » coché, un Non / Non
        conservé recevrait de nouveau les SMS. Le commentaire le dit au lieu de promettre la sortie des relances."""
        normal = V.texte_commentaire(V.CONSERVER, "LIST-2", "Normal", "2026-11-24", "2026-11-26", "Autres reponses")
        self.assertIn("écarté des relances par sa fiche (« Non » à l’entretien)", normal)
        self.assertNotIn("ATTENTION", normal)
        coche = V.texte_commentaire(V.CONSERVER, "LIST-2", "Normal", "2026-11-24", "2026-11-26", "Autres reponses", relancer_non=True)
        self.assertIn("ATTENTION", coche) ; self.assertIn("SMS de relance", coche)
        self.assertNotIn("écarté des relances", coche)

    def test_actions_sur_les_echeanciers(self):
        lignes = [ligne("C1", 1, PS, "Non", "Non", "MS1"),                  # pas de système → annuler (l'écran a mis Non / Non)
                  ligne("C2", 1, "Autres reponses", "Non", "Non", "MS2"),   # Non / Non → conserver
                  ligne("C3", 1, "Fait lui-même", "Non", "Oui", "MS3"),     # un seul Non → rien
                  ligne("C4", 0, PS, "Non", "Non", "MS4"),                  # pas appelé → rien
                  ligne("C5", 1, "Autres reponses", "Non", "Non", "MS5"),
                  ligne("C5", 1, PS, "Non", "Non", "MS5")]                  # même échéancier : annuler l'emporte
        a = {x["echeancier"]: x["action"] for x in V.actions_echeanciers(lignes)}
        self.assertEqual(a, {"MS1": V.ANNULER, "MS2": V.CONSERVER, "MS5": V.ANNULER})
        self.assertEqual(V.actions_echeanciers([ligne("C1", 1, PS, ech="")]), [])

    def test_commentaire(self):
        t = V.texte_commentaire(V.ANNULER, "LIST-1", "Normal", "2026-11-24", "2026-11-25", PS)
        for m in ("LIST-1", "Normal du 24/11/2026", "validée le 25/11/2026", "Échéancier annulé"):
            self.assertIn(m, t)
        t = V.texte_commentaire(V.CONSERVER, "LIST-2", None, "2026-11-24", "2026-11-25", "Autres reponses", "  vendu <la> maison ")
        for m in ("LIST-2 (Normal du 24/11/2026)", "« Autres reponses » : « vendu &lt;la&gt; maison »", "Échéancier conservé", "Non / Non"):
            self.assertIn(m, t)

    def _valider(self, lignes, cfg, appliquer=None):
        doc = frappe._dict(name="LIST-T", type_liste="Hors secteur", date="2026-11-24", clients=lignes, date_fin=None)
        faits = []

        def msgprint(msg, *a, **k):            # frappe.throw passe par msgprint(raise_exception=…) : on garde la levée
            if k.get("raise_exception"):
                raise k["raise_exception"](msg)

        with mock.patch.object(RC, "config", return_value=cfg), mock.patch.object(frappe, "msgprint", side_effect=msgprint) as mp, \
                mock.patch.object(V, "_appliquer", side_effect=lambda a, d, j, *r: faits.append(a["echeancier"]) or (appliquer or "annule")), \
                mock.patch.object(V, "_autres_echeanciers", side_effect=lambda l: [l["échéancier_dentretien"] + "-autre"]), \
                mock.patch.object(V, "log"):
            V.validation_finale(doc)
        return doc, faits, mp

    def test_lignes_non_appelees_ne_bloquent_plus(self):
        doc, faits, mp = self._valider([ligne("C1", 1, PS, "Non", "Non", "MS1"), ligne("C2")], {"valider_avec_lignes_non_appelees": 1})
        self.assertEqual(doc.date_fin, frappe.utils.nowdate())
        self.assertEqual(faits, ["MS1", "MS1-autre"])          # 26/11/2026 : les autres échéanciers du client aussi
        self.assertIn("Ligne 2 : C2", mp.call_args[0][0])

    def test_reglage_a_zero_bloque_comme_avant(self):
        with self.assertRaises(frappe.ValidationError) as e:
            self._valider([ligne("C1", 1, "Fait lui-même"), ligne("C2"), ligne("C3", 0, "Rendez-vous pris")], {"valider_avec_lignes_non_appelees": 0})
        self.assertIn("Ligne :2 Client : C2 N'est pas encore appele<br>Ligne :3 Client : C3 N'est pas encore appele<br>", str(e.exception))

    def test_cle_absente_du_reglage_vaut_1(self):
        doc, _f, _m = self._valider([ligne("C2")], {})
        self.assertTrue(doc.date_fin)

    def test_resume_sans_case_cochee_bloque(self):
        with self.assertRaises(frappe.ValidationError) as e:
            self._valider([ligne("C1", 0, "Ne répond pas 1er appel")], {"valider_avec_lignes_non_appelees": 1})
        self.assertIn("Ligne 1 : C1", str(e.exception))


class TestAppelJournalier(unittest.TestCase):
    def test_matin_et_soir(self):
        self.assertEqual(V.stats_appel_journalier(None, None, 60, 8), (60, None, 60))
        self.assertEqual(V.stats_appel_journalier(60, None, 55, 19), (60, 55, 5))
        self.assertEqual(V.stats_appel_journalier(84, 56, 70, 8), (70, 56, 14))       # nouveau passage du matin : seul le matin bouge

    def test_lignes_laissees_ne_sont_pas_des_appels(self):
        """26/11/2026 : matin 60 (55 + 1 + 1 + 3 sur la copie) ; LIST-06-11-2026-00004 validée à 10:00 sans appel, ses
        55 lignes quittent les brouillons : soir 5. Le compteur disait 55 appels, il dit 0."""
        self.assertEqual(V.stats_appel_journalier(60, None, 5, 19), (60, 5, 55))                  # avant la relecture
        self.assertEqual(V.stats_appel_journalier(60, None, 5, 19, laissees=55), (60, 5, 0))
        self.assertEqual(V.stats_appel_journalier(60, None, 2, 19, laissees=45), (60, 2, 13))   # 10 appels sur elle puis 45 laissées, 3 sur les autres

    def test_sans_releve_du_matin_ni_negatif(self):
        # Relecture finale du 26/11/2026 : pas de relevé de 08:00 → pas de nombre d'appels (il donnait −58).
        self.assertEqual(V.stats_appel_journalier(None, None, 57, 19, laissees=1), (None, 57, None))
        self.assertEqual(V.stats_appel_journalier(10, None, 20, 19), (10, 20, 0))              # lignes ajoutées : jamais < 0

    def test_entier(self):
        self.assertEqual([V._entier(v) for v in ("55", 0, "", None, " 3 ", "2.0", "abc", "2.5")], [55, 0, 0, 0, 3, 2, None, None])


class TestPatch(unittest.TestCase):
    def test_empreinte_ignore_espaces_et_fins_de_ligne(self):
        self.assertEqual(P.empreinte("a = 1\r\nb=2 \n\n"), P.empreinte("a = 1\nb=2"))
        self.assertNotEqual(P.empreinte("a = 1\nb=2"), P.empreinte("a = 1\nb=3"))

    def test_etat(self):
        code = "x = 1\r\n"
        self.assertEqual(P.etat_script(code, 0, P.empreinte("x = 1")), "a_eteindre")
        self.assertEqual(P.etat_script(code + "y = 2", 0, P.empreinte("x = 1")), "modifie")
        self.assertEqual(P.etat_script(code + "y = 2", 1, P.empreinte("x = 1")), "deja_eteint")

    def test_empreintes_du_code_porte(self):
        """Les empreintes du patch sont celles du code relevé en base le 25/11/2026 : deux scripts recopiés plus bas
        dans ce fichier, le troisième aussi (il était lu dans la fixture, retirée à la relecture finale du 26/11/2026 : le
        script n'est plus une fixture). Il ne se saute jamais."""
        codes = {"Valider Liste appelle": SCRIPT_VALIDER_LISTE_APPELLE,
                 "validation finale liste appelle": SCRIPT_VALIDATION_FINALE_LISTE_APPELLE,
                 "ajuster rendez vous pris par partenaire": SCRIPT_AJUSTER_RENDEZ_VOUS_PARTENAIRE}
        self.assertEqual(set(codes), set(P.SCRIPTS))
        for nom, code in codes.items():
            self.assertEqual(P.empreinte(code), P.SCRIPTS[nom][0], nom)
            self.assertIn(P.normaliser(P.SCRIPTS[nom][1]), P.normaliser(code), nom)
        # Le comportement porté à l'identique : mêmes phrases que le script d'origine.
        for morceau in ("Merci d'indiquer le Résumé d'appel pour le client: ", " Pas d'entretien prevu dans le calendrier",
                        " A plusieurs livraisons prevus dans le calendrier"):
            self.assertIn(morceau, SCRIPT_VALIDER_LISTE_APPELLE)
        self.assertIn(" N'est pas encore appele", SCRIPT_VALIDATION_FINALE_LISTE_APPELLE)

    def test_valeurs_initiales_des_reglages(self):
        """26/11/2026 : un champ ajouté à un Single déjà enregistré vaut 0 au premier enregistrement ; le patch écrit
        1 / 30 / 3 avant, seulement pour une clé absente ou vide, et jamais sur un réglage jamais enregistré."""
        self.assertEqual(P.valeurs_a_ecrire({"cycles_sans_reponse": "3", "plafond_liste": "100"}),
                         {"valider_avec_lignes_non_appelees": 1, "plafond_hors_secteur": 30, "jours_brouillon_alerte": 3})
        self.assertEqual(P.valeurs_a_ecrire({"plafond_liste": "100", "plafond_hors_secteur": "0", "jours_brouillon_alerte": "",
                                             "valider_avec_lignes_non_appelees": "0"}),
                         {"jours_brouillon_alerte": 3})                 # un 0 posé reste (choix possible) ; le vide est rempli
        self.assertEqual(P.valeurs_a_ecrire({}), {})
        # Les mêmes défauts que le code (RC.config().get(clé, défaut)).
        self.assertEqual(P.VALEURS_INITIALES["plafond_hors_secteur"], L.PLAFOND_HORS_SECTEUR)
        self.assertEqual(P.VALEURS_INITIALES["jours_brouillon_alerte"], L.JOURS_BROUILLON_ALERTE)


# ── Texte des deux Server Scripts tel qu'en base sur la copie de prod du 25/11/2026 12:26 (sans l'en-tête d'extraction).
# Versionné ici (relecture du 26/11/2026) : la preuve des empreintes du patch ne dépend plus d'un fichier de session.
# « ajuster rendez vous pris par partenaire » n'y est pas : il est versionné dans fixtures/server_script.json.
# Espaces de fin et fins de ligne (CRLF en base) retirés : l'empreinte les ignore.
SCRIPT_VALIDER_LISTE_APPELLE = r'''def find_rendez_vous(client,Date,Type_intervention):
    rendez_vous = frappe.db.sql("""
        SELECT DISTINCT
            TT.custom_client,
            TT.custom_employé,
            TT.custom_type_dintervention,
            TT.starts_on,
            TT.ends_on,
            C.custom_envoi_sms,
            C.custom_liste_telephone
        FROM
            `tabTache de travail` TT
        JOIN
            `tabCustomer` C ON C.name = TT.custom_client
        WHERE
            TT.status IN ('Open','Completed')
            AND DATE(TT.starts_on) >= %s
            AND TT.custom_client = %s
            AND TT.custom_type_dintervention = %s
    """, (Date,client,Type_intervention), as_dict=True)
    return rendez_vous
ic=0
liste_de_rv_m_c=''
Nb_rendv=0
for iclient in doc.clients:
    ic=ic+1
    log(iclient.resume_appel)
    if iclient.resume_appel=="Rendez-vous pris":
        rv=find_rendez_vous(iclient.client,doc.date,'Entretien')
        Nb_rendv=Nb_rendv+len(rv)
        if len(rv)==0:
            liste_de_rv_m_c=liste_de_rv_m_c+'Ligne :' +str(ic) +' Client : '+iclient.client +" Pas d'entretien prevu dans le calendrier"+ "<br>"
        elif len(rv)>1:
            liste_de_rv_m_c=liste_de_rv_m_c+'Ligne :' +str(ic) +' Client : '+iclient.client +" A plusieurs entretiens prevus dans le calendrier"+ "<br>"

    elif iclient.resume_appel=="Livraison prévue":
        rv=find_rendez_vous(iclient.client,doc.date,'Livraison')
        Nb_rendv=Nb_rendv+len(rv)
        if len(rv)==0:
            liste_de_rv_m_c=liste_de_rv_m_c+'Ligne :' +str(ic) +' Client : '+iclient.client +" Pas de livraison prevu dans le calendrier"+ "<br>"
        elif len(rv)>1:
            liste_de_rv_m_c=liste_de_rv_m_c+'Ligne :' +str(ic) +' Client : '+iclient.client +" A plusieurs livraisons prevus dans le calendrier"+ "<br>"
    if iclient.a_été_appelé==1 and iclient.resume_appel=='':
        frappe.throw("Merci d'indiquer le Résumé d'appel pour le client: "+iclient.client+ " dans la ligne: "+ str(ic) )
    #if iclient.a_été_appelé==1 and iclient.resume_appel=='Ne répond pas 1er appel':
    #    iclient.a_été_appelé=0

doc.nb_r_c=Nb_rendv
if liste_de_rv_m_c:
    frappe.msgprint(liste_de_rv_m_c)'''

SCRIPT_VALIDATION_FINALE_LISTE_APPELLE = r'''ic=0
liste_de_rv_m_c=''
for iclient in doc.clients:
    ic=ic+1
    if iclient.a_été_appelé==0:
        liste_de_rv_m_c=liste_de_rv_m_c+'Ligne :' +str(ic) +' Client : '+iclient.client +" N'est pas encore appele"+ "<br>"
    #elif iclient.resume_appel=="Ne répond pas 1er appel":
    #    liste_de_rv_m_c=liste_de_rv_m_c+'Ligne :' +str(ic) +' Client : '+iclient.client +" Doit etre rappeler une deuxieme fois"+ "<br>"
    #    iclient.a_été_appelé=0
if liste_de_rv_m_c:
    frappe.throw(liste_de_rv_m_c)
else:
    doc.date_fin=frappe.utils.nowdate()
    list_maint_to_delete=[]
    for iclient in doc.clients:
        if iclient.resume_appel=="Il n'y a pas de système de traitement installé":
            if not(iclient.échéancier_dentretien in list_maint_to_delete):
                list_maint_to_delete.append(iclient.échéancier_dentretien)
        if iclient.intéressé_par_le_service_dentretien=="Non" and iclient.intéressé_par_le_service_de_relance=="Non":
            if not(iclient.échéancier_dentretien in list_maint_to_delete):
                list_maint_to_delete.append(iclient.échéancier_dentretien)
    #frappe.throw(str(list_maint_to_delete))
    for imaint in list_maint_to_delete:
        try:
            frappe.msgprint("L'Échéancier d'Entretien : " +imaint+ " est supprimé")

            maintenance=frappe.get_doc("Maintenance Schedule",imaint)
            # Cancel invoices:
            maintenance.flags.ignore_links=True
            maintenance.cancel()
            frappe.delete_doc("Maintenance Schedule", maintenance.name, ignore_permissions=True,force=True)
        except:
            frappe.msgprint("L'Échéancier d'Entretien : " +imaint+ " déja supprimé")'''


# Le code de « ajuster rendez vous pris par partenaire » tel qu'exporté dans fixtures/server_script.json jusqu'au
# 26/11/2026 (= la base de prod du 25/11/2026), recopié ici quand le script a cessé d'être une fixture.
SCRIPT_AJUSTER_RENDEZ_VOUS_PARTENAIRE = 'user = "economiqaquasolutions23@gmail.com"\n\nlast_list = frappe.get_all(\n    "Liste Appelle Entretien",\n    fields=["name", "creation"],\n    order_by="creation desc",\n    limit=10\n)\n\nif not last_list:\n    frappe.throw("Aucune Liste Appelle Entretien trouvée")\n\nclients_T=[]\nliste_appel = last_list[0].name\nfor ilist in last_list:\n    liste_appel=ilist[\'name\']\n    clients = frappe.get_all(\n        "Appelle Client",\n        filters={"parent": liste_appel, "parenttype": "Liste Appelle Entretien"},\n        pluck="client"\n    )\n    clients_T.extend(clients)\n\nprint(clients_T)\nprint(len(clients_T))\n# Couleur : recalcul délégué à customization_app.api.recolorer_taches_partenaire\n# (compute_tache_color) — source unique du barème, plus de copie locale ici.\n# L\'ancienne copie avait dérivé (HR-EMP-00010 absent -> gris #888c89 chaque nuit).\nrecolorees = frappe.call("customization_app.api.recolorer_taches_partenaire")\nprint(recolorees)\n\ndraft_lists = frappe.get_all(\n    "Liste Appelle Entretien",\n    filters={"docstatus": 0},\n    pluck="name"\n)\n\ntotal = 0\nfor name in draft_lists:\n    doc = frappe.get_doc("Liste Appelle Entretien", name)\n    if int(doc.nb_appels_restant or 0) == 0:\n        doc.submit()\n        \n    total = total + int(doc.nb_appels_restant or 0)\nprint(total)\ndoc=frappe.get_doc(\'Appel journalier\',\'onaropc6oc\')\nnow = frappe.utils.now_datetime()\nhour = now.hour\ntoday = frappe.utils.getdate(now)\nCHILD_TABLE_FIELD = "stats_journaliere"\nrow_today = None\nfor r in doc.get(CHILD_TABLE_FIELD) or []:\n    if frappe.utils.getdate(r.date) == today:\n        row_today = r\n        break\nif not row_today:\n    row_today = doc.append(CHILD_TABLE_FIELD, {})\n    row_today.date = today\n\n# 4) Matin / Soir\nif hour >= 19:\n    row_today.soir = total\nelse:\n    row_today.matin = total\n\n# 5) Calcul nombre appels\nmatin = row_today.matin or 0\nsoir = row_today.soir or 0\nrow_today.nombre_appels = matin - soir\n\ndoc.save(ignore_permissions=True)\n\nfrappe.db.commit()'


if __name__ == "__main__":
    unittest.main()
