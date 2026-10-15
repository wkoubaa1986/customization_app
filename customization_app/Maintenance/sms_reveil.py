"""SMS de réveil annuel (15/10/2026) — pour les clients « À requalifier » qui n'ont plus rien reçu depuis un an.

Après le dernier appel humain (liste Requalification, puis 2e appel), un client « À requalifier » ne recevait plus
jamais rien : 239 clients dans cette impasse au 15/10/2026. Une fois par an, un seul SMS avec le lien de prise de
rendez-vous : presque gratuit, et un client qui change d'avis revient seul. Jamais pour un client « Perdu », « Non »
aux SMS, géré par le partenaire, avec un rendez-vous, ou dont la relance est reportée. Plafond propre par jour,
pour ne pas manger le plafond des relances du matin. Dev : simulé comme toutes les relances."""
from __future__ import annotations

import frappe
from frappe.utils import add_days, getdate, nowdate

from customization_app import modeles_sms, partenaire_clients, relances_config as RC
from customization_app.utils.run_safely import run_safely

JOURS_SILENCE = 365
REVEIL_PAR_JOUR = 20


def log(msg):
    RC.journal("maintenance_sms").info(msg)


def eligible(statut, envoi_sms, dernier_contact, dernier_reveil, today, jours=JOURS_SILENCE) -> bool:
    """« À requalifier », SMS acceptés, dernier contact (appel ou réveil) il y a au moins `jours`. PURE."""
    if statut != "À requalifier" or (envoi_sms or "").strip() == "Non":
        return False
    limite = add_days(getdate(today), -jours)
    for d in (dernier_contact, dernier_reveil):
        if d and getdate(d) > limite:
            return False
    return bool(dernier_contact or dernier_reveil)


def candidats(today, cfg=None, plafond=REVEIL_PAR_JOUR) -> list:
    """[{customer, customer_name, telephones, secteurs, familles}] à réveiller aujourd'hui, les plus anciens d'abord."""
    cfg = cfg or RC.config()
    if not frappe.db.has_column("Customer", "custom_sms_reveil_le"):
        return []
    rows = frappe.db.sql("""
        select c.name, c.customer_name, c.custom_liste_telephone telephones, c.custom_statut_relance statut, c.custom_envoi_sms envoi_sms,
               c.custom_sms_reveil_le dernier_reveil, c.custom_relance_reportee_au reportee,
               (select max(a.creation) from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                 where a.client = c.name and l.docstatus = 1 and a.a_été_appelé = 1) dernier_appel,
               (select group_concat(distinct ad.custom_secteur separator ', ') from `tabDynamic Link` dl join tabAddress ad on ad.name = dl.parent
                 where dl.parenttype = 'Address' and dl.link_doctype = 'Customer' and dl.link_name = c.name) secteurs
        from tabCustomer c where c.custom_statut_relance = 'À requalifier'""", as_dict=True)
    exclus = set(partenaire_clients.clients_geres()) | RC.clients_avec_rdv(cfg)
    out = []
    for r in rows:
        if r.name in exclus or (r.reportee and getdate(r.reportee) > getdate(today)):
            continue
        if not eligible(r.statut, r.envoi_sms, r.dernier_appel, r.dernier_reveil, today):
            continue
        out.append(r)
    out.sort(key=lambda r: str(r.dernier_reveil or r.dernier_appel or ""))
    return out[:plafond]


def appareil_du_client(customer, fams=None) -> str:
    """Le libellé SMS de la famille prioritaire des machines du client (« votre osmoseur domestique »)."""
    from customization_app.Maintenance.update_schedule import get_customer_machine_families
    fams = fams or RC.familles()
    codes = get_customer_machine_families(customer, fams)
    fam = RC.famille_prioritaire(codes, fams) if codes else None
    return RC.libelle_famille(fam, fams) if fam else RC.LIBELLE_FAMILLE_DEFAUT


def message_reveil(modeles, nom_client, appareil, lien_rdv, telephones) -> str:
    """Le texte, assemblé depuis le réglage « Config Modeles SMS ». PURE."""
    suite = modeles_sms.rendre(modeles["relance_rdv_en_ligne" if lien_rdv else "relance_sans_lien"],
                               {"lien_rdv": lien_rdv, "telephones": telephones})
    return modeles_sms.rendre(modeles["sms_reveil"], {"nom_client": nom_client, "appareil": appareil, "suite": suite,
                                                      "signature": modeles["signature"]})


def envoyer_reveils(dry_run: bool = False) -> dict:
    from customization_app.Maintenance.relance_maintenance_sms import (
        _format_phones_for_message, envoyer_sms_winsms, get_relance_config, lien_rdv_pour_client, simulation_envois, traiter_numero_tel)

    today, cfg, fams = nowdate(), RC.config(), RC.familles()
    modeles = modeles_sms.textes()
    _site, phones = get_relance_config()
    telephones = _format_phones_for_message(phones or [])
    simulation = simulation_envois()
    resume = {"candidats": 0, "envoyes": 0, "sans_numero": 0, "echecs": 0, "simules": simulation}
    for c in candidats(today, cfg):
        resume["candidats"] += 1
        numeros = traiter_numero_tel(c.telephones)
        if not numeros:
            resume["sans_numero"] += 1
            continue
        texte = message_reveil(modeles, c.customer_name or c.name, appareil_du_client(c.name, fams),
                               lien_rdv_pour_client(c.name, c.secteurs), telephones)
        if dry_run:
            log(f"[REVEIL dry] {c.name} -> {numeros[0]} : {texte[:90]!r}")
            continue
        ok = False
        if simulation:
            ok = True
            log(f"[SIMULÉ dev] SMS réveil -> {numeros[0]} ({c.name})")
        else:
            try:
                ok = (envoyer_sms_winsms(numeros[0], texte).json() or {}).get("message") == "Successfully Send"
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"SMS réveil {c.name}")
        if not ok:
            resume["echecs"] += 1
            continue
        frappe.db.set_value("Customer", c.name, "custom_sms_reveil_le", today)
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer", "reference_name": c.name,
                        "content": "📨 SMS de réveil envoyé le %s (un an sans contact) : %s" % (getdate(today).strftime("%d/%m/%Y"), texte)}).insert(ignore_permissions=True)
        resume["envoyes"] += 1
    log("[REVEIL SUMMARY] %s" % resume)
    return resume


def run_cron():
    return run_safely("Cron - SMS de réveil", envoyer_reveils)
