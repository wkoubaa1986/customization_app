"""Liste d'appels (« Liste Appelle Entretien ») : contrôle à l'enregistrement, validation finale et passage de 08:00 /
19:00 — repris dans l'app le 25/11/2026 des trois Server Scripts qui n'existaient qu'en base, sans version ni test :
« Valider Liste appelle » (Before Save), « validation finale liste appelle » (Before Submit) et « ajuster rendez vous
pris par partenaire » (planifié « 0 8,19 * * * »). Le patch listes_appels_dans_l_app les éteint s'ils n'ont pas changé.

Hooks (hooks.py) :
- controle_saisie → doc_events « validate ». Un Server Script « Before Save » est l'événement `validate` de Frappe
  (server_script_utils.EVENT_MAP) : il tourne à l'enregistrement ET à la validation finale. L'accrocher à
  `before_save` l'aurait sauté à la validation ;
- validation_finale → doc_events « before_submit » ;
- passage_8h_19h → scheduler_events cron « 0 8,19 * * * ».

Ce qui change à la validation finale (bilan du 25/11/2026, décisions du propriétaire) :
- une ligne NON APPELÉE ne bloque plus (réglage « valider_avec_lignes_non_appelees », 1 par défaut). La liste Hors
  secteur LIST-06-11-2026-00004 (100 lignes, 45 appelées) n'avait plus bougé depuis le 11/11 : en brouillon, elle
  bloquait la création de toute liste Hors secteur et 183 clients relancés par SMS attendaient. Les lignes non appelées
  sont listées à l'écran, ne sont PAS marquées appelées (liste_appels ne traite que les lignes cochées) et reviennent
  dans la prochaine liste de leur type (creation_liste_appelle : la visite reste sans `custom_appelle` pour Normal,
  Zone partenaire, Hors secteur et Urgence ; reprise explicite pour Relance et Requalification) ;
- « Il n'y a pas de système de traitement installé » ANNULE l'échéancier au lieu de le supprimer, et un client
  Non / Non GARDE le sien (il sort des relances par sa fiche) : le script supprimait l'échéancier, 42 suppressions
  depuis le 01/09 et l'historique des visites perdu avec. Un commentaire sur l'échéancier dit la réponse, la date et
  la liste. date_fin = aujourd'hui, comme avant (elle ouvre le 2e appel).

Relecture du 26/11/2026 :
- « pas de système » vaut pour le CLIENT : les autres échéanciers soumis du client qui portent un article de la ligne
  (ceux que liste_appels marque « appelés ») sont annulés aussi, chacun isolé et commenté. Le script n'annulait que
  l'échéancier principal (3 lignes « pas de système » sur 362 ont d'autres échéanciers) ;
- Non / Non : l'échéancier reste, et le client est écarté des relances par « Non » à l'entretien
  (relances_config.clients_exclus_relance) TANT QUE « relancer_non_interesses » est décoché. La relance SMS ne lit
  pas `custom_envoi_sms` (hors de ce module, signalé) : le commentaire le dit quand le réglage est coché ;
- LIVRAISON : la conservation Non / Non ne tient qu'avec le lot 4 du passage de nuit (update_schedule.verify_data_base
  sans la suppression des clients « Non + Non », arbre de travail du 26/11/2026). La version commitée 98e2a3c annule et
  supprime ces échéanciers la nuit même : les deux lots partent dans le MÊME déploiement (test de site qui enchaîne) ;
- compteur « Appel journalier » : les lignes laissées non appelées par une validation du jour ne comptent plus comme
  des appels (stats_appel_journalier, `laissees`).
"""
from __future__ import annotations

import html

import frappe
from frappe.utils import cint, cstr, getdate, now_datetime, nowdate

from customization_app import relances_config as RC
from customization_app.liste_appels import PAS_DE_SYSTEME
from customization_app.utils.run_safely import run_safely

DOCTYPE = "Liste Appelle Entretien"
RDV_PRIS, LIVRAISON_PREVUE = "Rendez-vous pris", "Livraison prévue"
# Le seul document « Appel journalier » (compteur matin / soir des appels restants), nommé en dur par le Server Script.
APPEL_JOURNALIER = "onaropc6oc"
ANNULER, CONSERVER = "annuler", "conserver"


def log(msg):
    RC.journal("creation_liste_appelle").info(msg)


def _appelee(ligne) -> bool:
    return bool(cint(ligne.get("a_été_appelé")))


def _jj(d) -> str:
    return getdate(d).strftime("%d/%m/%Y") if d else "?"


# =============================================================================
# 1) À L'ENREGISTREMENT (validate) — comportement identique à « Valider Liste appelle »
# =============================================================================

def premiere_ligne_sans_resume(lignes):
    """(n° de ligne, client) de la première ligne cochée « a été appelé » sans résumé d'appel, sinon None. Le script
    testait `== ''` : un résumé NULL passait. Sur les 9 099 lignes appelées de la copie du 25/11/2026, aucune n'a de
    résumé NULL — traiter NULL comme vide ne change rien de mesurable et ferme la porte. PURE."""
    for i, l in enumerate(lignes or [], start=1):
        if _appelee(l) and not l.get("resume_appel"):
            return i, l.get("client")
    return None


def controles_rendez_vous(lignes, date_liste, compter):
    """« Rendez-vous pris » → tâches « Entretien », « Livraison prévue » → tâches « Livraison », à partir de la date de la
    liste : 0 ou plusieurs = un avertissement (mêmes phrases que le script) ; le total trouvé devient nb_r_c.
    `compter(client, date, type)` → nombre de rendez-vous. PURE avec `compter`. → (total, message HTML ou '')."""
    total, messages = 0, ""
    for i, l in enumerate(lignes or [], start=1):
        resume = l.get("resume_appel")
        if resume == RDV_PRIS:
            type_tache, quoi, quoi_pl = "Entretien", "d'entretien prevu", "entretiens prevus"
        elif resume == LIVRAISON_PREVUE:
            type_tache, quoi, quoi_pl = "Livraison", "de livraison prevu", "livraisons prevus"
        else:
            continue
        n = compter(l.get("client"), date_liste, type_tache)
        total += n
        if n == 0:
            messages += "Ligne :" + str(i) + " Client : " + cstr(l.get("client")) + " Pas " + quoi + " dans le calendrier" + "<br>"
        elif n > 1:
            messages += "Ligne :" + str(i) + " Client : " + cstr(l.get("client")) + " A plusieurs " + quoi_pl + " dans le calendrier" + "<br>"
    return total, messages


def _rendez_vous(client, date_liste, type_intervention) -> int:
    """Même requête que le script (DISTINCT sur client, employé, type, début, fin, SMS, téléphone)."""
    return len(frappe.db.sql("""
        SELECT DISTINCT TT.custom_client, TT.custom_employé, TT.custom_type_dintervention, TT.starts_on, TT.ends_on,
                        C.custom_envoi_sms, C.custom_liste_telephone
        FROM `tabTache de travail` TT
        JOIN `tabCustomer` C ON C.name = TT.custom_client
        WHERE TT.status IN ('Open', 'Completed') AND DATE(TT.starts_on) >= %s
          AND TT.custom_client = %s AND TT.custom_type_dintervention = %s""", (date_liste, client, type_intervention)))


def controle_saisie(doc, method=None):
    """Hook `validate` de Liste Appelle Entretien (ex-Server Script « Valider Liste appelle », Before Save) : un appel
    coché exige son résumé ; les rendez-vous et livraisons annoncés sont recherchés au calendrier (nb_r_c)."""
    lignes = doc.get("clients") or []
    manque = premiere_ligne_sans_resume(lignes)
    if manque:
        frappe.throw("Merci d'indiquer le Résumé d'appel pour le client: " + cstr(manque[1]) + " dans la ligne: " + str(manque[0]))
    total, messages = controles_rendez_vous(lignes, doc.get("date"), _rendez_vous)
    doc.nb_r_c = total
    if messages:
        frappe.msgprint(messages)


# =============================================================================
# 2) À LA VALIDATION FINALE (before_submit)
# =============================================================================

def lignes_non_appelees(lignes):
    """→ (sans_resume, avec_resume) : lignes non cochées « a été appelé », [(n° de ligne, client)] et
    [(n° de ligne, client, résumé)]. Une ligne non cochée qui porte un résumé est une saisie incomplète. PURE."""
    sans, avec = [], []
    for i, l in enumerate(lignes or [], start=1):
        if _appelee(l):
            continue
        if l.get("resume_appel"):
            avec.append((i, l.get("client"), l.get("resume_appel")))
        else:
            sans.append((i, l.get("client")))
    return sans, avec


def message_bloquant(lignes_na) -> str:
    """Le message du script, à l'identique (réglage valider_avec_lignes_non_appelees = 0). PURE."""
    return "".join("Ligne :" + str(i) + " Client : " + cstr(c) + " N'est pas encore appele" + "<br>" for i, c, *_ in lignes_na)


def retour_prevu(type_liste) -> str:
    """Où reviennent les lignes non appelées (creation_liste_appelle, 25/11/2026). PURE.
    Urgence (relecture du 26/11/2026) : une Urgence validée avec des lignes non appelées ne couvre plus son rendez-vous
    (creation_liste_appelle.listes_qui_couvrent), une nouvelle Urgence est donc créée au passage de 07:00 suivant tant
    que le rendez-vous est à venir. L'ancien message promettait ce retour alors qu'il n'avait pas lieu."""
    t = type_liste or "Normal"
    if t == "Urgence":
        return ("une nouvelle liste « Urgence » dès le passage de 07:00 suivant, tant que le rendez-vous du technicien "
                "dans leur secteur est à venir (sinon la prochaine liste « Normal »)")
    return "la prochaine liste « %s »" % t


def message_non_appelees(sans, type_liste) -> str:
    """L'avertissement de validation : les lignes laissées, et ce qu'elles deviennent. PURE."""
    tete = "%d ligne(s) non appelée(s) : elles ne sont pas marquées appelées et reviendront dans %s." % (len(sans), retour_prevu(type_liste))
    return tete + "<br>" + "<br>".join("Ligne %d : %s" % (i, html.escape(cstr(c))) for i, c in sans)


def message_incoherentes(avec) -> str:
    """Bloquant : un résumé saisi sans la case « a été appelé » se perdrait (ni effet, ni marque). PURE."""
    return ("Ligne(s) avec un résumé d'appel mais sans la case « a été appelé » : cochez la case (l'appel compte) "
            "ou videz le résumé (la ligne reviendra dans la prochaine liste).<br>"
            + "<br>".join("Ligne %d : %s — « %s »" % (i, html.escape(cstr(c)), html.escape(cstr(r))) for i, c, r in avec))


def actions_echeanciers(lignes, autres=None) -> list:
    """Les échéanciers que la validation touche, depuis les lignes APPELÉES : « pas de système » → annuler ;
    Non à l'entretien ET Non aux SMS → conserver (commentaire seul). Un échéancier une fois, « annuler » l'emporte
    (l'écran met Non / Non dès qu'on choisit « pas de système »). Une ligne non appelée ne touche rien : sa réponse
    n'a pas été donnée au téléphone.
    `autres` = {n° de ligne: [autres échéanciers soumis du client pour les articles de la ligne]} (26/11/2026) : une
    ligne regroupe tous les échéanciers du client, et « pas de système » vaut pour le client, pas pour une machine ;
    ils sont annulés aussi. PURE. → [{echeancier, action, ligne, client, resume, reponse, principal}]."""
    par_ech = {}

    def poser(ech, action, i, l, principal):
        prec = par_ech.get(ech)
        if prec is None or (prec["action"] == CONSERVER and action == ANNULER):
            par_ech[ech] = {"echeancier": ech, "action": action, "ligne": i, "client": l.get("client"),
                            "resume": l.get("resume_appel") or "", "reponse": l.get("reponse_client") or "", "principal": principal}

    for i, l in enumerate(lignes or [], start=1):
        ech = l.get("échéancier_dentretien")
        if not ech or not _appelee(l):
            continue
        if l.get("resume_appel") == PAS_DE_SYSTEME:
            action = ANNULER
        elif l.get("intéressé_par_le_service_dentretien") == "Non" and l.get("intéressé_par_le_service_de_relance") == "Non":
            action = CONSERVER
        else:
            continue
        poser(ech, action, i, l, ech)
        if action == ANNULER:
            for autre in (autres or {}).get(i) or ():
                if autre and autre != ech:
                    poser(autre, ANNULER, i, l, ech)
    return list(par_ech.values())


def _autres_echeanciers(ligne) -> list:
    """Les autres échéanciers SOUMIS du client qui portent un article de la ligne (detail_articles) : le périmètre que
    liste_appels marque « appelé » pour cette ligne."""
    from customization_app.liste_appels import articles_appeles
    articles = tuple(articles_appeles(ligne.get("detail_articles")))
    if not articles or not ligne.get("client"):
        return []
    return frappe.db.sql_list("""select distinct ms.name from `tabMaintenance Schedule` ms
                                 join `tabMaintenance Schedule Detail` d on d.parent = ms.name
                                 where ms.customer = %s and ms.docstatus = 1 and ms.name <> %s and d.item_code in %s
                                 order by ms.name""", (ligne.get("client"), ligne.get("échéancier_dentretien") or "", articles))


def texte_commentaire(action, liste, type_liste, date_liste, jour, resume, reponse="", principal=None, echeancier=None,
                      relancer_non=False) -> str:
    """Le commentaire laissé sur l'échéancier : la liste, la date de validation, la réponse. PURE.
    26/11/2026 : un échéancier annulé qui n'est pas celui de la ligne le dit ; Non / Non ne promet plus « il sort des
    relances » quand `relancer_non_interesses` est coché (la relance SMS ne lit pas « Non aux SMS »)."""
    source = "Liste d’appels %s (%s du %s), validée le %s" % (liste, type_liste or "Normal", _jj(date_liste), _jj(jour))
    rep = "« %s »" % html.escape(resume) if resume else "sans résumé"
    if (reponse or "").strip():
        rep += " : « %s »" % html.escape(reponse.strip()[:200])
    if action == ANNULER:
        autre = ""
        if principal and echeancier and principal != echeancier:
            autre = " (autre échéancier du client ; la ligne portait %s)" % html.escape(principal)
        return "📵 %s — réponse %s%s. Échéancier annulé : plus de relance, son historique reste consultable." % (source, rep, autre)
    if relancer_non:
        sortie = ("ATTENTION : « Relancer les non intéressés » est coché dans Config Relances, il peut encore recevoir les "
                  "SMS de relance (la relance SMS ne lit pas « Non aux SMS »).")
    else:
        sortie = "le client est écarté des relances par sa fiche (« Non » à l’entretien)."
    return ("📵 %s — réponse %s ; le client ne veut ni l’entretien ni les SMS (Non / Non). Échéancier conservé : %s"
            % (source, rep, sortie))


def _appliquer(a, doc, jour, relancer_non=False) -> str:
    """Annule (ou laisse) l'échéancier et y écrit le commentaire. ISOLÉ (savepoint) : un échéancier en échec ne bloque
    ni la validation de la liste ni les autres lignes. → annule | conserve | deja_annule | brouillon | absent | erreur."""
    nom = a["echeancier"]
    texte = texte_commentaire(a["action"], doc.name, doc.get("type_liste"), doc.get("date"), jour, a["resume"], a["reponse"],
                              principal=a.get("principal"), echeancier=nom, relancer_non=relancer_non)
    if not frappe.db.exists("Maintenance Schedule", nom):
        frappe.msgprint("L'Échéancier d'Entretien : %s n'existe plus" % nom)
        return "absent"
    frappe.db.savepoint("liste_echeancier")
    try:
        ms = frappe.get_doc("Maintenance Schedule", nom)
        if a["action"] == ANNULER and ms.docstatus == 1:
            ms.flags.ignore_links = True            # la ligne de la liste le cite : sans cela, « lié à … », annulation refusée
            ms.flags.ignore_permissions = True      # la validation de la liste vaut décision (le script échouait sans le dire)
            ms.cancel()
            # Les AUTRES listes brouillon qui le citent ne s'enregistreraient plus (CancelledLinkError) ; pas celle-ci, dont
            # les lignes sont en mémoire (relecture du 26/11/2026).
            from customization_app.annulation_commande import degager_des_listes_brouillon
            degager_des_listes_brouillon(nom, "📵 Liste %s : pas de système" % doc.name, sauf_listes=[doc.name])
            etat = "annule"
            autre = (" (autre échéancier du client %s)" % a.get("client")) if a.get("principal") not in (None, nom) else ""
            frappe.msgprint("L'Échéancier d'Entretien : %s%s est annulé (pas de système de traitement) ; son historique reste consultable." % (nom, autre))
        elif a["action"] == ANNULER:
            etat = "deja_annule" if ms.docstatus == 2 else "brouillon"
            frappe.msgprint("L'Échéancier d'Entretien : %s %s" % (nom, "était déjà annulé" if etat == "deja_annule" else "est un brouillon : laissé tel quel"))
        else:
            etat = "conserve"
            frappe.msgprint("L'Échéancier d'Entretien : %s est conservé (client Non / Non : %s)." % (
                nom, "ATTENTION, « Relancer les non intéressés » est coché, il peut encore recevoir les SMS de relance" if relancer_non
                else "il est écarté des relances par « Non » à l'entretien"))
        ms.add_comment("Info", texte)
        return etat
    except Exception:
        frappe.db.rollback(save_point="liste_echeancier")
        frappe.log_error(frappe.get_traceback(), "Validation liste d'appels %s : échéancier %s" % (doc.name, nom))
        frappe.msgprint("L'Échéancier d'Entretien : %s n'a pas pu être traité (voir le journal des erreurs)." % nom)
        return "erreur"


def validation_finale(doc, method=None):
    """Hook `before_submit` (ex-Server Script « validation finale liste appelle », avec les règles du 25/11/2026)."""
    cfg = RC.config()
    lignes = doc.get("clients") or []
    sans, avec = lignes_non_appelees(lignes)
    if sans or avec:
        if not cint(cfg.get("valider_avec_lignes_non_appelees", 1)):
            frappe.throw(message_bloquant(sorted(sans + [(i, c) for i, c, _r in avec])))
        if avec:
            frappe.throw(message_incoherentes(avec))
        frappe.msgprint(message_non_appelees(sans, doc.get("type_liste")), title="Lignes non appelées", indicator="orange")
    jour = nowdate()
    doc.date_fin = jour
    bilan = {}
    autres = {}
    for i, l in enumerate(lignes, start=1):
        if _appelee(l) and l.get("resume_appel") == PAS_DE_SYSTEME and l.get("échéancier_dentretien"):
            try:
                autres[i] = _autres_echeanciers(l)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Validation liste d'appels %s : autres échéanciers de %s" % (doc.name, l.get("client")))
    relancer_non = bool(cint(cfg.get("relancer_non_interesses")))
    for a in actions_echeanciers(lignes, autres):
        etat = _appliquer(a, doc, jour, relancer_non)
        bilan[etat] = bilan.get(etat, 0) + 1
    if sans or bilan:
        log("[INFO] Validation %s (%s) : %d ligne(s) non appelée(s) renvoyée(s) à la liste suivante, échéanciers %s"
            % (doc.name, doc.get("type_liste") or "Normal", len(sans), bilan or "{}"))


# =============================================================================
# 3) PASSAGE DE 08:00 ET 19:00 — ex-« ajuster rendez vous pris par partenaire »
# =============================================================================

def stats_appel_journalier(matin, soir, total, heure, laissees=0):
    """Le compteur du jour : à 08:00 les appels restants du matin, à 19:00 ceux du soir ; nombre d'appels = matin − soir
    (calcul du script) − `laissees`. PURE. → (matin, soir, nombre_appels).
    26/11/2026 (relecture) : `laissees` = lignes laissées NON APPELÉES par une liste validée dans la journée (elle était
    un brouillon à 08:00). Elles quittent les brouillons sans appel : sans cette correction, valider à 10:00
    LIST-06-11-2026-00004 (55 lignes non appelées) aurait compté 55 appels ce jour-là (0 à 28 les jours de la semaine
    du 18/11). Jusqu'au 25/11 une liste ne se validait qu'à 0 restant : `laissees` valait toujours 0."""
    if heure >= 19:
        soir = total
    else:
        matin = total
    if heure >= 19 and matin in (None, ""):
        # Relecture finale du 26/11/2026 : sans relevé de 08:00 (planificateur arrêté, migrate, redémarrage), le calcul
        # donnait 0 − soir − laissées (−58 à l'essai). Pas de relevé du matin : pas de nombre d'appels.
        return matin, soir, None
    return matin, soir, max(0, cint(matin) - cint(soir) - cint(laissees))


def _lignes_laissees_du_jour(jour) -> int:
    """Lignes non appelées des listes validées ce jour-là après le passage de 08:00 (date_fin = jour, dernière
    modification après 08:00 = la validation) et qui étaient des brouillons à 08:00 (créées avant) : celles que le
    compteur du matin a comptées et qui ne sont plus dans celui du soir sans avoir été appelées. Une liste validée
    à 08:00 pile par ce passage lui-même n'a aucune ligne non appelée (_passage_8h_19h l'exige)."""
    jour = getdate(jour)
    huit = "%s 08:00:00" % jour
    return cint(frappe.db.sql("""select count(*) from `tabAppelle Client` a
                                 join `tabListe Appelle Entretien` l on l.name = a.parent and a.parenttype = %s
                                 where l.docstatus = 1 and l.date_fin = %s and l.creation < %s and l.modified >= %s
                                   and ifnull(a.a_été_appelé, 0) = 0""", (DOCTYPE, jour, huit, huit))[0][0])


def _entier(v):
    """nb_appels_restant est un champ texte : un entier propre, 0 si vide (le script : int(v or 0)), None si illisible."""
    s = cstr(v).strip()
    if not s:
        return 0
    try:
        return int(float(s)) if float(s).is_integer() else None
    except ValueError:
        return None


def _noter_appel_journalier(total, maintenant):
    nom = APPEL_JOURNALIER if frappe.db.exists("Appel journalier", APPEL_JOURNALIER) else \
        (frappe.get_all("Appel journalier", pluck="name", order_by="creation asc", limit=1) or [None])[0]
    if not nom:
        log("[INFO] Validation auto : aucun document « Appel journalier », compteur non mis à jour")
        return None
    doc = frappe.get_doc("Appel journalier", nom)
    jour = getdate(maintenant)
    ligne = next((r for r in doc.get("stats_journaliere") or [] if r.date and getdate(r.date) == jour), None)
    if not ligne:
        ligne = doc.append("stats_journaliere", {})
        ligne.date = jour
    laissees = _lignes_laissees_du_jour(jour) if maintenant.hour >= 19 else 0
    ligne.matin, ligne.soir, ligne.nombre_appels = stats_appel_journalier(ligne.matin, ligne.soir, total, maintenant.hour, laissees)
    doc.save(ignore_permissions=True)
    if laissees:
        log("[INFO] Appel journalier du %s : %d ligne(s) laissée(s) non appelée(s) par une validation du jour, retirée(s) "
            "du nombre d'appels" % (_jj(jour), laissees))
    return {"matin": ligne.matin, "soir": ligne.soir, "nombre_appels": ligne.nombre_appels, "laissees": laissees}


ANCIENS_SCRIPTS = ("Valider Liste appelle", "validation finale liste appelle", "ajuster rendez vous pris par partenaire")


def anciens_scripts_actifs() -> list:
    """Les Server Scripts repris ici qui sont encore actifs en base (26/11/2026) : le patch listes_appels_dans_l_app les
    laisse actifs s'il les trouve modifiés, et on peut les recocher à la main (ils ne sont plus des fixtures depuis la
    relecture finale du 26/11/2026). L'ancien et le nouveau passage tourneraient alors à la même heure, et l'ancien
    validerait une liste au compteur à 0 qui garde des lignes non appelées (celui-ci la laisse). On ne les éteint pas
    d'ici : on le dit dans le journal."""
    try:
        return frappe.get_all("Server Script", filters={"name": ["in", list(ANCIENS_SCRIPTS)], "disabled": 0}, pluck="name")
    except Exception:
        return []


def _passage_8h_19h(maintenant=None) -> dict:
    """Couleurs des tâches du partenaire, validation des brouillons sans appel restant, compteur « Appel journalier ».
    Chaque étape et chaque liste est ISOLÉE : une liste qui refuse sa validation n'empêche ni les autres ni le
    compteur, et l'absence de liste n'est plus une erreur (le script levait « Aucune Liste Appelle Entretien »)."""
    maintenant = maintenant or now_datetime()
    bilan = {"recolorees": 0, "validees": [], "ecartees": [], "erreurs": [], "restant": 0, "stats": None}
    bilan["anciens_scripts_actifs"] = anciens_scripts_actifs()
    if bilan["anciens_scripts_actifs"]:
        log("[ATTENTION] Server Script(s) encore actif(s) alors que l'app les remplace : %s — ils tournent en plus de "
            "liste_validation (code modifié laissé actif par le patch listes_appels_dans_l_app, ou recoché à la main)"
            % ", ".join(bilan["anciens_scripts_actifs"]))
    try:
        from customization_app.api import recolorer_taches_partenaire
        bilan["recolorees"] = len(recolorer_taches_partenaire() or [])
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Validation auto listes d'appels : couleurs des tâches partenaire")
    total = 0
    for nom in frappe.get_all(DOCTYPE, filters={"docstatus": 0}, pluck="name", order_by="creation asc"):
        frappe.db.savepoint("liste_auto")
        try:
            doc = frappe.get_doc(DOCTYPE, nom)
            restant = _entier(doc.get("nb_appels_restant"))
            if restant is None:
                bilan["ecartees"].append(nom)
                log("[INFO] Validation auto : %s ignorée, nb_appels_restant illisible (%r)" % (nom, doc.get("nb_appels_restant")))
                continue
            if restant == 0:
                # Le compteur est tenu par l'écran : avant le 25/11/2026 une liste au compteur faux échouait à la
                # validation finale ; désormais elle passerait avec des lignes non appelées → on la laisse.
                if any(not _appelee(l) for l in doc.get("clients") or []):
                    bilan["ecartees"].append(nom)
                    log("[INFO] Validation auto : %s laissée en brouillon, compteur à 0 mais des lignes non appelées" % nom)
                    continue
                doc.submit()
                bilan["validees"].append(nom)
                frappe.db.commit()
            total += restant
        except Exception:
            frappe.db.rollback(save_point="liste_auto")
            bilan["erreurs"].append(nom)
            frappe.log_error(frappe.get_traceback(), "Validation auto liste d'appels %s" % nom)
    bilan["restant"] = total
    try:
        bilan["stats"] = _noter_appel_journalier(total, maintenant)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Validation auto listes d'appels : compteur Appel journalier")
    frappe.db.commit()
    log("[INFO] Validation auto %s : %d liste(s) validée(s) %s, %d appel(s) restant(s) sur les brouillons, %d écartée(s), "
        "%d erreur(s), %d tâche(s) partenaire recolorée(s)" % (maintenant.strftime("%H:%M"), len(bilan["validees"]), bilan["validees"] or "",
                                                              total, len(bilan["ecartees"]), len(bilan["erreurs"]), bilan["recolorees"]))
    return bilan


def passage_8h_19h():
    """Cron « 0 8,19 * * * » (ex-Server Script planifié « ajuster rendez vous pris par partenaire »)."""
    return run_safely("Cron - Validation auto listes d'appels", _passage_8h_19h)
