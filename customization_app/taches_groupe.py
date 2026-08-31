"""Actions groupées sur les tâches de travail — décaler ou annuler, en prévenant.

Le besoin (31/08/2026) : un technicien absent, un jour férié tombé tard, une
tournée qui saute — il faut décaler ou annuler d'un coup une série de tâches,
surtout des entretiens, ET prévenir chaque client.

L'ORDRE compte, et il n'est pas le même dans les deux cas :
  - DÉCALER : on déplace D'ABORD, puis on écrit. Le message porte les balises
    {date} et {heure} — envoyé avant, il annoncerait l'ancien créneau.
  - ANNULER : on annule D'ABORD, puis on n'écrit qu'aux clients dont la tâche a
    réellement été annulée. Annoncer une annulation qui a échoué serait un
    mensonge que personne ne rattrape.

Les envois repassent par `sms_taches` : mêmes modèles, mêmes balises, mêmes
traces en commentaire sur la tâche, même garde-fou de développement.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime

DOCTYPE = "Tache de travail"


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


@frappe.whitelist()
def get_filtres() -> dict:
    """Les valeurs proposées à l'écran — lues du réel, pas écrites en dur."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    types = frappe.db.sql_list(
        "SELECT DISTINCT custom_type_dintervention FROM `tab%s` "
        "WHERE IFNULL(custom_type_dintervention,'') != '' "
        "ORDER BY custom_type_dintervention" % DOCTYPE)
    employes = frappe.db.sql(
        """SELECT DISTINCT t.custom_choix_du_staff, e.employee_name
           FROM `tab%s` t JOIN tabEmployee e ON e.name = t.custom_choix_du_staff
           WHERE IFNULL(t.custom_choix_du_staff,'') != ''
           ORDER BY e.employee_name""" % DOCTYPE, as_dict=True)
    return {"types": types,
            "employes": [{"valeur": e.custom_choix_du_staff,
                          "libelle": e.employee_name or e.custom_choix_du_staff}
                         for e in employes]}


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
    if employe:
        filtres["custom_choix_du_staff"] = employe
    if type_intervention:
        filtres["custom_type_dintervention"] = type_intervention
    if statut:
        filtres["status"] = statut
    if client:
        filtres["custom_client"] = ["like", "%" + client + "%"]

    lignes = frappe.get_all(
        DOCTYPE, filters=filtres,
        fields=["name", "custom_client", "custom_type_dintervention", "status",
                "starts_on", "custom_choix_du_staff", "secteur"],
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
        "employe": noms_rh.get(l.custom_choix_du_staff) or l.custom_choix_du_staff or "",
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
            doc.db_set("starts_on", nouveau, update_modified=True)
            if duree is not None:
                doc.db_set("ends_on", nouveau + duree, update_modified=True)
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
            doc.db_set("status", "Cancelled", update_modified=True)
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
