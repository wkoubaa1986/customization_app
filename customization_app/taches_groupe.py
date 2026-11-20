"""Actions groupées sur les tâches de travail — décaler, transférer ou annuler, en prévenant.

Le besoin (31/08/2026) : un technicien absent, un jour férié tombé tard, une
tournée qui saute — il faut décaler ou annuler d'un coup une série de tâches,
surtout des entretiens, ET prévenir chaque client.

L'ORDRE compte, et il n'est pas le même dans les deux cas :
  - DÉCALER : on déplace D'ABORD, puis on écrit. Le message porte les balises
    {date} et {heure} — envoyé avant, il annoncerait l'ancien créneau.
  - ANNULER : on annule D'ABORD, puis on n'écrit qu'aux clients dont la tâche a
    réellement été annulée. Annoncer une annulation qui a échoué serait un
    mensonge que personne ne rattrape.

TRANSFÉRER (18/11/2026) : les tâches cochées passent à un autre employé ACTIF — une absence, un nouveau
technicien qui reprend un secteur. Le créneau ne bouge pas ; le client n'est prévenu que si un message est écrit.
Les filtres de l'écran acceptent plusieurs types et plusieurs employés, et ne proposent que les employés actifs.
Depuis le 20/11/2026 l'écran montre les deux agendas côte à côte, jour par jour ; une tâche « Employé fixe »
(`custom_employe_fixe`, la même case que respecte l'optimisation des tournées) ne se transfère pas ; un employé
parti qui garde des tâches ouvertes est proposé comme ORIGINE d'un transfert (jamais comme destinataire).

Les envois repassent par `sms_taches` : mêmes modèles, mêmes balises, mêmes
traces en commentaire sur la tâche, même garde-fou de développement.

⚠️ On passe par `doc.save()`, jamais par `db_set` : le doctype porte des hooks
`on_update` qui réalignent le pourcentage livré de la commande et recalculent
son anomalie. Écrire directement en base laisserait la commande avec une
anomalie périmée — une commande dont la seule tâche vient d'être annulée doit
redevenir « sans tâche ».
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime

DOCTYPE = "Tache de travail"
#: une absence n'est pas une intervention : elle ne se confie pas à quelqu'un d'autre
TYPES_ABSENCE = ("Congé", "Jour de récupération")


def _ecriture():
    frappe.has_permission(DOCTYPE, "write", throw=True)


def _liste(taches):
    taches = frappe.parse_json(taches) if isinstance(taches, str) else (taches or [])
    return [t for t in taches if t]


def _modifiable(doc):
    """Une tâche déjà terminée ou annulée ne se décale pas : la déplacer
    réécrirait l'histoire d'une intervention faite."""
    if doc.status == "Completed":
        return "déjà terminée"
    if doc.status == "Cancelled":
        return "déjà annulée"
    return None


def plusieurs(valeur) -> list:
    """Un filtre à choix multiple tel qu'il arrive de l'écran : une liste, le JSON d'une liste, ou une seule
    valeur (ancien appel). -> la liste des valeurs non vides, sans doublon, dans l'ordre. PURE."""
    if isinstance(valeur, str):
        v = valeur.strip()
        if v.startswith("["):
            try:
                valeur = frappe.parse_json(v)
            except Exception:
                valeur = [v]
        else:
            valeur = [v]
    return list(dict.fromkeys(str(x) for x in (valeur or []) if x))


def refus_transfert(statut, type_intervention, actuel, vers, fixe=False):
    """Pourquoi une tâche ne se transfère pas — ou None si elle se transfère. PURE.
    `fixe` : la case « Employé fixe » de la tâche — ce client, cette intervention, c'est CET employé."""
    if statut == "Completed":
        return "déjà terminée"
    if statut == "Cancelled":
        return "déjà annulée"
    if type_intervention in TYPES_ABSENCE:
        return "congé ou récupération : ne se transfère pas"
    if fixe:
        return "employé fixe : ne se transfère pas"
    if actuel and actuel == vers:
        return "déjà confiée à cet employé"
    return None


@frappe.whitelist()
def get_filtres() -> dict:
    """Les valeurs proposées à l'écran — lues du réel, pas écrites en dur."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    types = frappe.db.sql_list(
        "SELECT DISTINCT custom_type_dintervention FROM `tab%s` "
        "WHERE IFNULL(custom_type_dintervention,'') != '' "
        "ORDER BY custom_type_dintervention" % DOCTYPE)
    # Les employés ACTIFS seulement (18/11/2026) : un employé parti ne se choisit plus, ni comme filtre ni comme
    # destinataire d'un transfert. Tous les actifs, même sans tâche encore : un nouveau technicien doit pouvoir
    # en recevoir. (Sans filtre d'employé, les tâches restées à un employé parti s'affichent toujours.)
    employes = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name"],
                              order_by="employee_name")
    # Un employé parti qui garde des tâches ouvertes : on peut les lui REPRENDRE (origine d'un transfert), avec la
    # période qui les couvre — elles sont souvent anciennes, hors de la semaine affichée.
    anciens = frappe.db.sql(
        """SELECT e.name AS valeur, e.employee_name AS libelle, COUNT(*) AS ouvertes,
                  DATE(MIN(t.starts_on)) AS du, DATE(MAX(t.starts_on)) AS au
           FROM `tab%s` t JOIN `tabEmployee` e ON e.name = t.custom_choix_du_staff
           WHERE e.status != 'Active' AND t.status = 'Open' AND t.starts_on IS NOT NULL
             AND IFNULL(t.custom_type_dintervention, '') NOT IN %%(absences)s
           GROUP BY e.name, e.employee_name ORDER BY e.employee_name""" % DOCTYPE,
        {"absences": TYPES_ABSENCE}, as_dict=True)
    return {"types": types,
            "employes": [{"valeur": e.name, "libelle": e.employee_name or e.name} for e in employes],
            "anciens": [{"valeur": a.valeur, "libelle": a.libelle or a.valeur, "ouvertes": cint(a.ouvertes),
                         "du": str(a.du), "au": str(a.au)} for a in anciens]}


@frappe.whitelist()
def rechercher(date_from=None, date_to=None, employe=None, type_intervention=None,
               statut="Open", client=None, limite=200) -> dict:
    """Les tâches d'une période, filtrées — c'est la matière des actions groupées.

    Par défaut on ne montre QUE les tâches ouvertes : décaler ou annuler une
    intervention déjà faite n'a pas de sens, et les noyer dans la liste ferait
    cocher des lignes intraitables.
    """
    frappe.has_permission(DOCTYPE, "read", throw=True)
    filtres = {}
    if date_from and date_to:
        filtres["starts_on"] = ["between", [date_from + " 00:00:00",
                                            date_to + " 23:59:59"]]
    elif date_from:
        filtres["starts_on"] = [">=", date_from + " 00:00:00"]
    elif date_to:
        filtres["starts_on"] = ["<=", date_to + " 23:59:59"]
    employes, types = plusieurs(employe), plusieurs(type_intervention)      # un ou plusieurs (choix multiple)
    if employes:
        filtres["custom_choix_du_staff"] = ["in", employes]
    if types:
        filtres["custom_type_dintervention"] = ["in", types]
    if statut:
        filtres["status"] = statut
    if client:
        filtres["custom_client"] = ["like", "%" + client + "%"]

    lignes = frappe.get_all(
        DOCTYPE, filters=filtres,
        fields=["name", "custom_client", "custom_type_dintervention", "status",
                "starts_on", "ends_on", "custom_choix_du_staff", "secteur"]
               + (["custom_employe_fixe"] if frappe.db.has_column(DOCTYPE, "custom_employe_fixe") else []),
        order_by="starts_on asc", limit_page_length=cint(limite) or 200)
    matricules = {l.custom_choix_du_staff for l in lignes if l.custom_choix_du_staff}
    noms_rh = dict(frappe.get_all(
        "Employee", filters={"name": ["in", list(matricules)]},
        fields=["name", "employee_name"], as_list=True)) if matricules else {}
    return {"lignes": [{
        "tache": l.name,
        "client": l.custom_client or "",
        "type": l.custom_type_dintervention or "",
        "statut": l.status,
        "quand": str(l.starts_on)[:16] if l.starts_on else "",
        "fin": str(l.ends_on)[:16] if l.ends_on else "",
        "fixe": cint(l.get("custom_employe_fixe")),
        "employe": noms_rh.get(l.custom_choix_du_staff) or l.custom_choix_du_staff or "",
        "matricule": l.custom_choix_du_staff or "",
        "secteur": l.secteur or "",
    } for l in lignes], "total": len(lignes)}


@frappe.whitelist()
def apercu(taches, modele=None):
    """Qui recevra quoi, et l'état de chaque tâche — AVANT d'agir."""
    from customization_app import sms_taches

    noms = _liste(taches)
    vue = sms_taches.apercu(noms, modele)
    etats = {d.name: d for d in frappe.get_all(
        DOCTYPE, filters={"name": ["in", noms]},
        fields=["name", "status", "starts_on", "custom_type_dintervention"],
        limit_page_length=0)}
    for ligne in vue.get("lignes") or []:
        etat = etats.get(ligne["tache"]) or {}
        ligne["statut"] = etat.get("status")
        ligne["quand"] = str(etat.get("starts_on") or "")[:16]
        ligne["type"] = etat.get("custom_type_dintervention")
    vue["par_type"] = {}
    for e in etats.values():
        cle = e.custom_type_dintervention or "?"
        vue["par_type"][cle] = vue["par_type"].get(cle, 0) + 1
    return vue


@frappe.whitelist()
def decaler(taches, jours=None, nouvelle_date=None, modele=None, sujet=None,
            sms=1, email=1):
    """Décale les tâches de N jours, ou vers une date précise — l'heure est
    conservée, puis les clients sont prévenus.

    On ne repasse PAS par le moteur de planification : ici c'est le magasin qui
    décide, pas le client. Le moteur refuserait des créneaux que le magasin
    s'autorise (dimanche, secteur, capacité) — d'où un décalage sec, assumé.
    """
    _ecriture()
    noms = _liste(taches)
    jours = cint(jours)
    if not jours and not nouvelle_date:
        frappe.throw(_("Indiquez un nombre de jours ou une nouvelle date."))

    faites, resultats = [], []
    for nom in noms:
        doc = frappe.get_doc(DOCTYPE, nom)
        refus = _modifiable(doc)
        if refus:
            resultats.append({"tache": nom, "etat": refus})
            continue
        if not doc.starts_on:
            resultats.append({"tache": nom, "etat": "sans date de début"})
            continue
        try:
            ancien = get_datetime(doc.starts_on)
            if nouvelle_date:
                # Nouvelle date, MÊME heure : le créneau du matin reste le matin.
                nouveau = get_datetime("%s %s" % (nouvelle_date, ancien.strftime("%H:%M:%S")))
            else:
                nouveau = add_days(ancien, jours)
            duree = (get_datetime(doc.ends_on) - ancien) if doc.ends_on else None
            doc.starts_on = nouveau
            if duree is not None:
                doc.ends_on = nouveau + duree
            # save() et NON db_set : le doctype porte des hooks `on_update`
            # (alignement du % livré, recalcul d'anomalie de la commande liée).
            # db_set écrit en base sans les déclencher — la commande resterait
            # avec une anomalie périmée.
            doc.flags.ignore_permissions = True
            doc.save()
            frappe.get_doc({
                "doctype": "Comment", "comment_type": "Info",
                "reference_doctype": DOCTYPE, "reference_name": nom,
                "content": _("🗓️ Décalée par {0} : {1} → {2}").format(
                    frappe.session.user, str(ancien)[:16], str(nouveau)[:16]),
            }).insert(ignore_permissions=True)
            faites.append(nom)
            resultats.append({"tache": nom,
                              "etat": "décalée au %s" % str(nouveau)[:16]})
        except Exception as e:
            resultats.append({"tache": nom, "etat": "échec : %s" % str(e)[:120]})
    frappe.db.commit()

    envoi = _prevenir(faites, modele, sujet, sms, email)
    return {"resultats": resultats, "prevenus": faites, "envoi": envoi}


@frappe.whitelist()
def transferer(taches, vers=None, modele=None, sujet=None, sms=1, email=1):
    """Confie les tâches à un autre employé ACTIF. Le créneau ne change pas.

    Refusées, une par une et sans bloquer les autres : une tâche terminée ou annulée, un congé / une
    récupération, une tâche « Employé fixe », une tâche déjà à cet employé. Le nom affiché sur la tâche suit le choix du staff (hook
    before_save) ; une trace est laissée en commentaire. Le client n'est prévenu que si un message est écrit.
    """
    _ecriture()
    noms = _liste(taches)
    cible = frappe.db.get_value("Employee", vers, ["name", "employee_name", "status"], as_dict=True) if vers else None
    if not cible:
        frappe.throw(_("Choisissez l'employé à qui transférer les tâches."))
    if cible.status != "Active":
        frappe.throw(_("{0} n'est plus un employé actif : on ne lui transfère pas de tâche.").format(cible.employee_name))
    nom_vers = cible.employee_name or cible.name

    faites, resultats = [], []
    for nom in noms:
        doc = frappe.get_doc(DOCTYPE, nom)
        refus = refus_transfert(doc.status, doc.get("custom_type_dintervention"), doc.get("custom_choix_du_staff"), cible.name,
                                fixe=cint(doc.get("custom_employe_fixe")))
        if refus:
            resultats.append({"tache": nom, "etat": refus})
            continue
        try:
            ancien = doc.get("custom_choix_du_staff")
            nom_de = (frappe.db.get_value("Employee", ancien, "employee_name") if ancien else None) or ancien or "—"
            doc.custom_choix_du_staff = cible.name
            doc.flags.ignore_permissions = True
            try:
                # save() et NON db_set : mêmes raisons que pour décaler (hooks de la commande liée, nom affiché).
                doc.save()
            except frappe.ValidationError:
                # Tâche ancienne qui porte une valeur de liste périmée : la sauvegarde complète la refuse, on
                # écrit le strict nécessaire (comme depart_employe._transferer_taches).
                frappe.db.set_value(DOCTYPE, nom, {"custom_choix_du_staff": cible.name, "custom_employé": nom_vers},
                                    update_modified=True)
                from customization_app import flotte_gps
                flotte_gps.oublier_suivi(doc.get("starts_on"))          # écriture directe : pas de hook on_update
            frappe.get_doc({
                "doctype": "Comment", "comment_type": "Info",
                "reference_doctype": DOCTYPE, "reference_name": nom,
                "content": _("🔁 Transférée par {0} : {1} → {2}").format(
                    frappe.session.user, frappe.utils.escape_html(nom_de), frappe.utils.escape_html(nom_vers)),
            }).insert(ignore_permissions=True)
            faites.append(nom)
            resultats.append({"tache": nom, "etat": "transférée de %s à %s" % (nom_de, nom_vers)})
        except Exception as e:
            resultats.append({"tache": nom, "etat": "échec : %s" % str(e)[:120]})
    frappe.db.commit()

    envoi = _prevenir(faites, modele, sujet, sms, email)
    return {"resultats": resultats, "prevenus": faites if envoi else [], "transferees": faites, "envoi": envoi}


@frappe.whitelist()
def annuler(taches, motif=None, modele=None, sujet=None, sms=1, email=1):
    """Annule les tâches (statut Cancelled) puis prévient les clients.

    On ne SUPPRIME pas : au Desk, l'historique d'une intervention annulée a de
    la valeur (qui, quand, pourquoi). Le portail client, lui, supprime — c'est
    le seul moyen d'y libérer le créneau.
    """
    _ecriture()
    noms = _liste(taches)
    faites, resultats = [], []
    for nom in noms:
        doc = frappe.get_doc(DOCTYPE, nom)
        refus = _modifiable(doc)
        if refus:
            resultats.append({"tache": nom, "etat": refus})
            continue
        try:
            doc.status = "Cancelled"
            # save() : l'annulation doit REDESCENDRE sur la commande liée —
            # une commande dont la seule tâche est annulée redevient « sans
            # tâche », et son anomalie doit le dire.
            doc.flags.ignore_permissions = True
            doc.save()
            frappe.get_doc({
                "doctype": "Comment", "comment_type": "Info",
                "reference_doctype": DOCTYPE, "reference_name": nom,
                "content": _("❌ Annulée par {0}{1}").format(
                    frappe.session.user,
                    (" — " + frappe.utils.escape_html(motif)) if motif else ""),
            }).insert(ignore_permissions=True)
            faites.append(nom)
            resultats.append({"tache": nom, "etat": "annulée"})
        except Exception as e:
            resultats.append({"tache": nom, "etat": "échec : %s" % str(e)[:120]})
    frappe.db.commit()

    envoi = _prevenir(faites, modele, sujet, sms, email)
    return {"resultats": resultats, "prevenus": faites, "envoi": envoi}


def _prevenir(taches, modele, sujet, sms, email):
    """L'envoi, seulement aux tâches réellement traitées, et seulement si un
    message a été écrit — décaler sans prévenir reste un choix valable."""
    if not taches or not (modele or "").strip():
        return None
    if not (cint(sms) or cint(email)):
        return None
    from customization_app import sms_taches

    return sms_taches.envoyer(taches, modele, sujet=sujet, sms=cint(sms),
                              email=cint(email))



# ── Réunion : une tâche « Autre » par participant (demande utilisateur 04/10/2026) ──────────

def plan_reunion(titre, jour, heure, duree_minutes, noms):
    """Le plan d'une réunion : un dict par participant, même créneau, titre choisi.
    `noms` = [(employee_id, employee_name)]. La dernière ligne du titre porte le nom de l'employé
    (gabarit des autres tâches, repris par reaffecter_titre). Fonction pure."""
    titre = (titre or "").strip()
    if not titre:
        raise ValueError("Donnez un titre à la réunion.")
    if not noms:
        raise ValueError("Choisissez au moins un participant.")
    duree = int(duree_minutes or 0)
    if not 5 <= duree <= 720:
        raise ValueError("La durée doit être comprise entre 5 minutes et 12 heures.")
    heure = (heure or "").strip()[:5]
    if len(heure) != 5 or heure[2] != ":":
        raise ValueError("Heure de début invalide (HH:MM).")
    debut = "%s %s:00" % (jour, heure)
    tous = ", ".join(n for _e, n in noms)
    return [{"employe": e, "nom": n, "starts_on": debut, "duree": duree,
             "titre": "👥 %s\n%s" % (titre, n),
             "subject": "%s\nParticipants : %s" % (titre, tous)}
            for e, n in noms]


@frappe.whitelist()
def employes_reunion() -> list:
    """Les employés actifs proposables comme participants."""
    frappe.has_permission(DOCTYPE, "create", throw=True)
    return frappe.get_all("Employee", filters={"status": "Active"},
                          fields=["name as valeur", "employee_name as libelle"], order_by="employee_name")


@frappe.whitelist(methods=["POST"])
def creer_reunion(titre, jour, heure, duree_minutes, employes, notes=None):
    """Crée UNE tâche « Autre » par participant, même créneau, avec le titre choisi. Un commentaire
    commun trace la réunion sur chaque tâche. Renvoie les tâches créées."""
    frappe.has_permission(DOCTYPE, "create", throw=True)
    ids = frappe.parse_json(employes) if isinstance(employes, str) else (employes or [])
    ids = list(dict.fromkeys(i for i in ids if i))
    noms = [(i, frappe.db.get_value("Employee", i, "employee_name") or i) for i in ids]
    try:
        plan = plan_reunion(titre, jour, heure, duree_minutes, noms)
    except ValueError as exc:
        frappe.throw(str(exc))
    creees = []
    for p in plan:
        fin = frappe.utils.add_to_date(frappe.utils.get_datetime(p["starts_on"]), minutes=p["duree"])
        doc = frappe.get_doc({
            "doctype": DOCTYPE, "custom_type_dintervention": "Autre",
            "custom_choix_du_staff": p["employe"], "custom_employé": p["nom"],
            "titre": p["titre"], "subject": p["subject"] + (("\n" + notes.strip()) if notes and notes.strip() else ""),
            "starts_on": p["starts_on"], "ends_on": fin, "status": "Open",
        })
        doc.flags.duree_fixee = True          # la durée saisie, pas le standard du type (Autre = 2 h)
        doc.insert()
        doc.add_comment("Comment", _("👥 Réunion « {0} » créée par {1} avec {2}.").format(
            titre.strip(), frappe.session.user, ", ".join(x["nom"] for x in plan)))
        creees.append({"tache": doc.name, "employe": p["nom"]})
    frappe.db.commit()
    return {"creees": creees, "debut": plan[0]["starts_on"]}
