"""
Envoi SMS + e-mail depuis une Tache de travail — avec modèles prédéfinis.

Le bouton « 📨 SMS / E-mail » de la fiche tâche ouvre le même dialogue que
l'envoi groupé des commandes (sms_commandes.py), avec en plus un CHOIX DE
MODÈLE : un clic remplit le message, qui reste modifiable. Le rendu des
balises, les numéros et l'envoi vivent CÔTÉ SERVEUR — mêmes règles de
numéros (compagne_sms.traiter_numero_tel), même chemin d'envoi
(_send_sms_with_fallback), même garde-fou dev (SIMULATION en developer_mode).

BALISES (rendues par tâche) :
  {nom_client} {date} {heure} {type}
  {technicien}       le nom de l'employé affecté à la tâche
  {tel_technicien}   son numéro de téléphone (Employee.cell_number)
  {commande} {total_ttc} {devise}   la commande client liée (vides sinon)
  {lien_rdv}         le lien du portail de prise de rendez-vous
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

DOCTYPE_TACHE = "Tache de travail"
CHAMP_TEL_CLIENT = "custom_liste_telephone"
SEUIL_DIRECT = 10

# Les modèles proposés dans le dialogue. Le texte reste MODIFIABLE après
# sélection : le modèle est un point de départ, pas une camisole.
# Tous se terminent par la SIGNATURE (demande 29/08) — via {signature}, pour
# la changer un jour à UN seul endroit.
SIGNATURE = "Aqua World & Servicing"

MODELES = [
    {
        "cle": "injoignable",
        "libelle": "Client injoignable — rappeler le technicien",
        "texte": (
            "Bonjour {nom_client},\n\n"
            "Notre technicien a essayé de vous joindre concernant l'installation "
            "de votre commande {commande}, d'un montant total de {total_ttc} {devise}.\n\n"
            "Merci de rappeler {technicien} au {tel_technicien} dans les plus "
            "brefs délais.\n\n{signature}"
        ),
    },
    {
        "cle": "rappel_rdv",
        "libelle": "Rappel du rendez-vous",
        "texte": (
            "Bonjour {nom_client},\n\n"
            "Notre technicien {technicien} passera chez vous le {date} à {heure} "
            "pour votre intervention ({type}).\n\n"
            "Pour toute question, appelez le {tel_technicien}.\n\n{signature}"
        ),
    },
    {
        "cle": "modification",
        "libelle": "Modification du rendez-vous",
        "texte": (
            "Bonjour {nom_client},\n\n"
            "Votre intervention ({type}) a été reprogrammée au {date} à {heure}.\n"
            "Elle sera assurée par notre technicien {technicien} "
            "(tél. {tel_technicien}).\n"
            "Commande concernée : {commande} — {total_ttc} {devise}.\n\n"
            "{signature}"
        ),
    },
    {
        "cle": "annulation",
        "libelle": "Annulation — reprendre un rendez-vous",
        "texte": (
            "Bonjour {nom_client},\n\n"
            "Nous sommes au regret de vous informer que votre intervention "
            "({type}) prévue le {date} a été annulée.\n\n"
            "Vous pouvez reprendre un rendez-vous en ligne ici : {lien_rdv}\n\n"
            "Veuillez nous excuser pour ce désagrément.\n\n{signature}"
        ),
    },
    {
        "cle": "termine",
        "libelle": "Intervention terminée",
        "texte": (
            "Bonjour {nom_client},\n\n"
            "Votre intervention ({type}) a été réalisée ce jour par {technicien}.\n\n"
            "Merci de votre confiance.\n\n{signature}"
        ),
    },
]


def _destinataires(noms):
    """Par tâche : le client (numéros + e-mails), l'employé affecté (nom +
    téléphone) et la commande liée si elle existe."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        traiter_numero_tel,
    )
    from customization_app.retenue_source import _coordonnees_des_contacts

    taches = frappe.get_all(
        DOCTYPE_TACHE, filters={"name": ["in", noms]},
        fields=["name", "custom_client", "custom_choix_du_staff", "commande_client",
                "starts_on", "custom_type_dintervention"],
        limit_page_length=0)

    clients = list({t.custom_client for t in taches if t.custom_client})
    infos_client = {r.name: r for r in frappe.get_all(
        "Customer", filters={"name": ["in", clients]},
        fields=["name", "customer_name", CHAMP_TEL_CLIENT],
        limit_page_length=0)} if clients else {}
    coord = _coordonnees_des_contacts(clients) if clients else {}

    employes = list({t.custom_choix_du_staff for t in taches if t.custom_choix_du_staff})
    infos_emp = {r.name: r for r in frappe.get_all(
        "Employee", filters={"name": ["in", employes]},
        fields=["name", "employee_name", "cell_number"],
        limit_page_length=0)} if employes else {}

    commandes = list({t.commande_client for t in taches if t.commande_client})
    infos_cmd = {r.name: r for r in frappe.get_all(
        "Sales Order", filters={"name": ["in", commandes]},
        fields=["name", "grand_total", "currency"],
        limit_page_length=0)} if commandes else {}

    out = []
    for t in taches:
        client = infos_client.get(t.custom_client) or {}
        contact = coord.get(t.custom_client) or {}
        emp = infos_emp.get(t.custom_choix_du_staff) or {}
        cmd = infos_cmd.get(t.commande_client) or {}
        numeros = traiter_numero_tel(" , ".join(filter(None, [
            client.get(CHAMP_TEL_CLIENT) or "",
            " , ".join(contact.get("telephones") or [])])))
        quand = t.starts_on
        out.append({
            "tache": t.name,
            "client": t.custom_client or "",
            "nom_client": client.get("customer_name") or t.custom_client or "",
            "type": t.custom_type_dintervention or "",
            "date": frappe.utils.formatdate(quand) if quand else "",
            "heure": str(quand)[11:16] if quand else "",
            "technicien": emp.get("employee_name") or "",
            "tel_technicien": emp.get("cell_number") or "",
            "commande": t.commande_client or "",
            # fmt_money sans symbole : « 651,000 » plutôt que « 651.0 » dans le SMS.
            "total_ttc": frappe.utils.fmt_money(flt(cmd.get("grand_total"), 3)) if cmd else "",
            "devise": (cmd.get("currency") or "TND") if cmd else "",
            "numeros": numeros,
            "emails": list(contact.get("emails") or []),
        })
    return out


def rendre(modele, ligne):
    """Remplace les balises ; une balise inconnue reste telle quelle."""
    class _Tolerant(dict):
        def __missing__(self, cle):
            return "{%s}" % cle

    return (modele or "").format_map(_Tolerant({
        "lien_rdv": frappe.utils.get_url("/rdv"),
        "signature": SIGNATURE,
        **{cle: ligne.get(cle, "") for cle in (
            "nom_client", "date", "heure", "type", "technicien",
            "tel_technicien", "commande", "total_ttc", "devise")},
    }))


@frappe.whitelist()
def apercu(taches, modele=None):
    """Qui recevra quoi — AVANT d'envoyer. Livre aussi les modèles prédéfinis."""
    frappe.has_permission(DOCTYPE_TACHE, "read", throw=True)
    taches = frappe.parse_json(taches) if isinstance(taches, str) else (taches or [])
    taches = [t for t in taches if t]
    lignes = _destinataires(taches) if taches else []
    for l in lignes:
        l["message"] = rendre(modele, l) if modele else ""
    return {
        "modeles": MODELES,
        "lignes": lignes,
        "totaux": {
            "taches": len(lignes),
            "numeros": sum(len(l["numeros"]) for l in lignes),
            "emails": sum(len(l["emails"]) for l in lignes),
            "sans_numero": len([l for l in lignes if not l["numeros"]]),
            "sans_email": len([l for l in lignes if not l["emails"]]),
        },
    }


@frappe.whitelist()
def envoyer(taches, modele, sujet=None, sms=1, email=1):
    """Lance l'envoi — direct pour un petit lot, en file d'attente au-delà."""
    frappe.has_permission(DOCTYPE_TACHE, "write", throw=True)

    taches = frappe.parse_json(taches) if isinstance(taches, str) else (taches or [])
    taches = [t for t in taches if t]
    if not taches:
        frappe.throw(_("Aucune tâche sélectionnée."))
    if not (modele or "").strip():
        frappe.throw(_("Écrivez le message à envoyer."))
    sms, email = frappe.utils.cint(sms), frappe.utils.cint(email)
    if not (sms or email):
        frappe.throw(_("Choisissez au moins un canal : SMS ou e-mail."))

    if len(taches) <= SEUIL_DIRECT:
        return _executer(taches, modele, sujet, sms, email, frappe.session.user)

    frappe.enqueue(
        "customization_app.sms_taches._executer",
        queue="long", timeout=3600,
        taches=taches, modele=modele, sujet=sujet, sms=sms, email=email,
        utilisateur=frappe.session.user, differe=True,
        job_name="envoi_taches_%s" % frappe.generate_hash(length=8))
    return {"differe": True, "taches": len(taches)}


def _executer(taches, modele, sujet, sms, email, utilisateur, differe=False):
    """La tournée. Un échec n'interrompt pas les suivants ; chaque tâche touchée
    garde une trace au fil du document."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        _send_sms_with_fallback,
    )

    # ⛔ GARDE-FOU DEV — même règle que sms_commandes._executer : la base dev
    # porte les VRAIS numéros et la VRAIE passerelle, donc on SIMULE en
    # developer_mode, sauf `sms_groupe_reel_en_dev` posé dans site_config.json.
    simulation = bool(frappe.utils.cint(frappe.conf.get("developer_mode"))) \
        and not frappe.utils.cint(frappe.conf.get("sms_groupe_reel_en_dev"))

    resultat = {"sms_envoyes": 0, "emails_envoyes": 0, "echecs": 0,
                "simulation": simulation, "detail": []}
    lignes = _destinataires(taches)
    total = len(lignes)
    for index, ligne in enumerate(lignes, start=1):
        texte = rendre(modele, ligne)
        verdict = {"tache": ligne["tache"], "client": ligne["nom_client"],
                   "sms": None, "email": None}

        if sms and ligne["numeros"] and simulation:
            verdict["sms"] = "SIMULÉ (dev) → %s" % ", ".join(ligne["numeros"])
        elif sms and ligne["numeros"]:
            try:
                _send_sms_with_fallback(ligne["numeros"], texte)
                verdict["sms"] = "envoyé → %s" % ", ".join(ligne["numeros"])
                resultat["sms_envoyes"] += len(ligne["numeros"])
            except Exception as e:
                verdict["sms"] = "échec : %s" % str(e)[:120]
                resultat["echecs"] += 1
        elif sms:
            verdict["sms"] = "aucun numéro"

        if email and ligne["emails"] and simulation:
            verdict["email"] = "SIMULÉ (dev) → %s" % ", ".join(ligne["emails"])
        elif email and ligne["emails"]:
            try:
                frappe.sendmail(
                    recipients=ligne["emails"],
                    subject=(sujet or "").strip()
                            or _("Aqua World & Servicing — votre intervention"),
                    message=frappe.utils.md_to_html(texte),
                    reference_doctype=DOCTYPE_TACHE,
                    reference_name=ligne["tache"],
                    now=True)
                verdict["email"] = "envoyé → %s" % ", ".join(ligne["emails"])
                resultat["emails_envoyes"] += len(ligne["emails"])
            except Exception as e:
                verdict["email"] = "échec : %s" % str(e)[:120]
                resultat["echecs"] += 1
        elif email:
            verdict["email"] = "aucun e-mail"

        if verdict["sms"] or verdict["email"]:
            frappe.get_doc({
                "doctype": "Comment", "comment_type": "Info",
                "reference_doctype": DOCTYPE_TACHE,
                "reference_name": ligne["tache"],
                "content": _("📨 Message client par {0} — SMS : {1} · E-mail : {2}<br>{3}")
                           .format(frappe.session.user, verdict["sms"] or "—",
                                   verdict["email"] or "—",
                                   frappe.utils.escape_html(texte)[:500]),
            }).insert(ignore_permissions=True)
        resultat["detail"].append(verdict)

        frappe.db.commit()
        frappe.publish_realtime(
            "envoi_taches_progres",
            {"fait": index, "total": total, "tache": ligne["tache"],
             "client": ligne["nom_client"]},
            user=utilisateur)

    if differe:
        frappe.publish_realtime("envoi_taches_termine", resultat, user=utilisateur)
    return resultat
