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
