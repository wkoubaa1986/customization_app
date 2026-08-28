"""
Envoi groupé SMS + e-mail depuis la LISTE des commandes client.

L'utilisateur coche des commandes, écrit un message avec des balises, et le
même message part — personnalisé par commande — en SMS vers la « Liste
Telephone » du client, et par e-mail vers TOUTES les adresses de ses contacts.

BALISES (rendues par commande) :
  {nom_client} {commande} {date} {total_ttc} {devise}
  {articles}   les noms d'articles, séparés par des virgules
  {codes}      les codes articles
  {article}    le nom du PREMIER article (le cas courant : une commande, un appareil)
  {code}       le code du premier article
  {statut}     statut de la commande

Les numéros passent par les mêmes règles que les campagnes SMS
(compagne_sms.traiter_numero_tel : mobiles tunisiens 8 chiffres, dédoublonnés)
et l'envoi par _send_sms_with_fallback — un seul chemin d'envoi dans l'app.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

CHAMP_TEL_CLIENT = "custom_liste_telephone"
# Pas de plafond au nombre de commandes (demande 28/08) : c'est la FILE
# D'ATTENTE qui encaisse les gros envois, pas la requête HTTP — chaque SMS est
# un appel réseau, et une requête web ne tient pas la distance
# (« La requête a expiré » sur 151 commandes).
SEUIL_DIRECT = 10


def _lecture():
    frappe.has_permission("Sales Order", "read", throw=True)


def _articles_par_commande(noms):
    out = {}
    for l in frappe.get_all(
            "Sales Order Item",
            filters={"parent": ["in", noms], "parenttype": "Sales Order"},
            fields=["parent", "item_code", "item_name", "idx"],
            order_by="parent, idx", limit_page_length=0):
        out.setdefault(l.parent, []).append(
            {"code": l.item_code, "nom": l.item_name or l.item_code})
    return out


def _destinataires(noms):
    """Par commande : le client, ses numéros mobiles et les e-mails de ses contacts."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        traiter_numero_tel,
    )
    from customization_app.retenue_source import _coordonnees_des_contacts

    commandes = frappe.get_all(
        "Sales Order", filters={"name": ["in", noms]},
        fields=["name", "customer", "customer_name", "grand_total", "currency",
                "transaction_date", "status", "contact_mobile", "contact_email"],
        limit_page_length=0)
    clients = list({c.customer for c in commandes if c.customer})
    tel_client = {r.name: r.get(CHAMP_TEL_CLIENT) for r in frappe.get_all(
        "Customer", filters={"name": ["in", clients]},
        fields=["name", CHAMP_TEL_CLIENT], limit_page_length=0)} if clients else {}
    coord = _coordonnees_des_contacts(clients) if clients else {}
    articles = _articles_par_commande([c.name for c in commandes])

    out = []
    for c in commandes:
        contact = coord.get(c.customer) or {}
        # Téléphones : la « Liste Telephone » de la fiche client d'abord (c'est
        # elle que l'utilisateur tient à jour), puis les contacts, puis le
        # numéro porté par la commande elle-même (commande web).
        brut = " , ".join(filter(None, [
            tel_client.get(c.customer) or "",
            " , ".join(contact.get("telephones") or []),
            c.contact_mobile or ""]))
        numeros = traiter_numero_tel(brut)
        emails = list(contact.get("emails") or [])
        if c.contact_email and c.contact_email not in emails:
            emails.append(c.contact_email)
        lignes = articles.get(c.name) or []
        out.append({
            "commande": c.name,
            "client": c.customer,
            "nom_client": c.customer_name or c.customer,
            "total_ttc": flt(c.grand_total, 3),
            "devise": c.currency or "TND",
            "date": str(c.transaction_date or ""),
            "statut": c.status or "",
            "articles": ", ".join(l["nom"] for l in lignes),
            "codes": ", ".join(l["code"] for l in lignes),
            "article": lignes[0]["nom"] if lignes else "",
            "code": lignes[0]["code"] if lignes else "",
            "numeros": numeros,
            "emails": emails,
        })
    return out


def rendre(modele, ligne):
    """Remplace les balises. Une balise inconnue est laissée telle quelle plutôt
    que de faire échouer tout l'envoi (message tapé à la main)."""
    class _Tolerant(dict):
        def __missing__(self, cle):
            return "{%s}" % cle

    return (modele or "").format_map(_Tolerant({
        "nom_client": ligne["nom_client"],
        "commande": ligne["commande"],
        "date": ligne["date"],
        "total_ttc": ligne["total_ttc"],
        "devise": ligne["devise"],
        "statut": ligne["statut"],
        "articles": ligne["articles"],
        "codes": ligne["codes"],
        "article": ligne["article"],
        "code": ligne["code"],
    }))


@frappe.whitelist()
def apercu(noms, modele=None):
    """Qui recevra quoi — AVANT d'envoyer. Rendu du message pour chaque commande."""
    _lecture()
    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    noms = [n for n in noms if n]
    if not noms:
        return {"lignes": [], "totaux": {}}

    lignes = _destinataires(noms)
    for l in lignes:
        l["message"] = rendre(modele, l) if modele else ""
    return {
        "lignes": lignes,
        "totaux": {
            "commandes": len(lignes),
            "clients": len({l["client"] for l in lignes}),
            "numeros": sum(len(l["numeros"]) for l in lignes),
            "emails": sum(len(l["emails"]) for l in lignes),
            "sans_numero": len([l for l in lignes if not l["numeros"]]),
            "sans_email": len([l for l in lignes if not l["emails"]]),
        },
    }


@frappe.whitelist()
def envoyer(noms, modele, sujet=None, sms=1, email=1):
    """Lance l'envoi. Petit lot : tout de suite. Gros lot : EN TÂCHE DE FOND.

    ⚠️ Chaque SMS est un appel réseau (~1 s) : 151 commandes en direct dans la
    requête HTTP, c'est « La requête a expiré » et un envoi à moitié fait sans
    que personne ne sache où il s'est arrêté. Au-delà de SEUIL_DIRECT, on
    confie donc la tournée à la file d'attente, et l'écran suit la progression.
    """
    frappe.has_permission("Sales Order", "write", throw=True)

    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    noms = [n for n in noms if n]
    if not noms:
        frappe.throw(_("Sélectionnez au moins une commande."))
    if not (modele or "").strip():
        frappe.throw(_("Écrivez le message à envoyer."))
    sms, email = frappe.utils.cint(sms), frappe.utils.cint(email)
    if not (sms or email):
        frappe.throw(_("Choisissez au moins un canal : SMS ou e-mail."))

    if len(noms) <= SEUIL_DIRECT:
        return _executer(noms, modele, sujet, sms, email, frappe.session.user)

    frappe.enqueue(
        "customization_app.sms_commandes._executer",
        queue="long", timeout=3600,
        noms=noms, modele=modele, sujet=sujet, sms=sms, email=email,
        utilisateur=frappe.session.user, differe=True,
        job_name="envoi_groupe_%s" % frappe.generate_hash(length=8))
    return {"differe": True, "commandes": len(noms)}


def _executer(noms, modele, sujet, sms, email, utilisateur, differe=False):
    """La tournée elle-même. Un échec n'interrompt pas les suivants — chaque
    commande a son verdict, et une trace est posée sur CHAQUE commande touchée :
    six mois plus tard, on doit pouvoir dire ce qui a été envoyé, à qui."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        _send_sms_with_fallback,
    )

    # ⛔ GARDE-FOU DEV. Le site de développement porte les VRAIS numéros des
    # clients (base restaurée de la prod) et partage la VRAIE passerelle SMS :
    # un envoi groupé lancé en dev part donc pour de bon. Le 28/08/2026, un
    # test de 25 commandes a expédié 27 SMS « Test … » à de vrais clients.
    # En developer_mode, on SIMULE — sauf `sms_groupe_reel_en_dev` posé
    # explicitement dans site_config.json par quelqu'un qui sait ce qu'il fait.
    simulation = bool(frappe.utils.cint(frappe.conf.get("developer_mode"))) \
        and not frappe.utils.cint(frappe.conf.get("sms_groupe_reel_en_dev"))

    resultat = {"sms_envoyes": 0, "emails_envoyes": 0, "echecs": 0,
                "simulation": simulation, "detail": []}
    lignes = _destinataires(noms)
    total = len(lignes)
    for index, ligne in enumerate(lignes, start=1):
        texte = rendre(modele, ligne)
        verdict = {"commande": ligne["commande"], "client": ligne["nom_client"],
                   "sms": None, "email": None}

        if sms and ligne["numeros"] and simulation:
            verdict["sms"] = "SIMULÉ (dev) → %s" % ", ".join(ligne["numeros"])
            resultat["sms_envoyes"] += 0
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
                            or _("Aqua World & Servicing — {0}").format(ligne["commande"]),
                    message=frappe.utils.md_to_html(texte),
                    reference_doctype="Sales Order",
                    reference_name=ligne["commande"],
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
                "reference_doctype": "Sales Order",
                "reference_name": ligne["commande"],
                "content": _("📨 Envoi groupé par {0} — SMS : {1} · E-mail : {2}<br>{3}")
                           .format(frappe.session.user, verdict["sms"] or "—",
                                   verdict["email"] or "—",
                                   frappe.utils.escape_html(texte)[:500]),
            }).insert(ignore_permissions=True)
        resultat["detail"].append(verdict)

        # La progression se voit à l'écran, et le travail déjà fait est ACQUIS :
        # on commit au fil de l'eau plutôt qu'en bloc à la fin.
        frappe.db.commit()
        frappe.publish_realtime(
            "envoi_groupe_progres",
            {"fait": index, "total": total, "commande": ligne["commande"],
             "client": ligne["nom_client"]},
            user=utilisateur)

    if differe:
        frappe.publish_realtime("envoi_groupe_termine", resultat, user=utilisateur)
        try:
            frappe.get_doc({
                "doctype": "Notification Log",
                "for_user": utilisateur,
                "type": "Alert",
                "subject": _("Envoi groupé terminé — {0} SMS, {1} e-mail(s), {2} échec(s)")
                           .format(resultat["sms_envoyes"], resultat["emails_envoyes"],
                                   resultat["echecs"]),
                "email_content": _("{0} commande(s) traitée(s).").format(total),
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "sms_commandes notification")
    return resultat
