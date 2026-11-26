"""SMS de réveil (15/10/2026) — pour les clients « À requalifier » restés sans contact.

Après le dernier appel humain (liste Requalification, puis 2e appel), un client « À requalifier » ne recevait plus
jamais rien : 239 clients dans cette impasse au 15/10/2026. Un seul SMS avec le lien de prise de rendez-vous : presque
gratuit, et un client qui change d'avis revient seul. Jamais pour un client « Perdu », « Non » aux SMS, géré par le
partenaire, avec un rendez-vous, ou dont la relance est reportée. Plafond propre par jour, pour ne pas manger le plafond
des relances du matin. Dev : simulé comme toutes les relances.

25/11/2026 : le PREMIER réveil vient `jours_reveil_requalifies` jours après le dernier appel (180 par défaut au lieu de
365) — les 290 « À requalifier » en retard ont eu leur dernier appel en octobre 2026 : à 365 j, aucun réveil avant
octobre 2027 ; à 180 j, dès avril 2027. Les réveils SUIVANTS restent annuels (JOURS_SILENCE entre deux réveils,
relecture du 26/11/2026) : avec le même délai pour les deux, un client sans réponse aurait reçu un SMS tous les 180 j.
Le texte (« cela fait plus d'un an que … n'a pas été entretenu ») parle de l'entretien, pas du contact : il reste vrai
en règle générale, un client n'est « À requalifier » qu'après 3 mois d'échéance sans entretien depuis sa dernière visite
faite (visites semestrielles), et un achat d'entretien le réactive. À 90 j ou moins, relire ce texte (Config Modeles
SMS)."""
from __future__ import annotations

import frappe
from frappe.utils import add_days, cint, getdate, nowdate

from customization_app import modeles_sms, partenaire_clients, relances_config as RC
from customization_app.utils.run_safely import run_safely

JOURS_SILENCE = 365            # règle d'origine (15/10/2026) : un réveil par an au plus (écart minimal entre deux réveils)
JOURS_REVEIL_DEFAUT = 180      # décision du 25/11/2026 : réglage « jours_reveil_requalifies » (1er réveil après l'appel)
REVEIL_PAR_JOUR = 20


def log(msg):
    RC.journal("maintenance_sms").info(msg)


def jours_reveil(cfg=None) -> int:
    """Le délai sans contact avant un réveil (réglage `jours_reveil_requalifies`, 180 j tant que le champ n'existe pas).
    0 ou moins = aucun réveil : avec 0, le même client recevrait un SMS chaque matin. PURE avec `cfg`."""
    cfg = RC.config() if cfg is None else cfg
    return cint(cfg.get("jours_reveil_requalifies", JOURS_REVEIL_DEFAUT))


def eligible(statut, envoi_sms, dernier_appel, dernier_reveil, today, jours=JOURS_SILENCE) -> bool:
    """« À requalifier », SMS acceptés, dernier APPEL il y a au moins `jours`, dernier RÉVEIL il y a au moins
    max(`jours`, JOURS_SILENCE). Le réglage avance le premier réveil après l'appel ; il ne fait pas répéter le SMS plus
    d'une fois par an (relecture du 26/11/2026 : avec 180 j pour les deux, un client réveillé le 30/04/2027 l'aurait été
    de nouveau le 27/10/2027, puis le 24/04/2028… — deux SMS par an au lieu d'un). PURE."""
    if statut != "À requalifier" or (envoi_sms or "").strip() == "Non":
        return False
    today, jours = getdate(today), cint(jours)
    if dernier_appel and getdate(dernier_appel) > add_days(today, -jours):
        return False
    if dernier_reveil and getdate(dernier_reveil) > add_days(today, -max(jours, JOURS_SILENCE)):
        return False
    return bool(dernier_appel or dernier_reveil)


def candidats(today, cfg=None, plafond=REVEIL_PAR_JOUR) -> list:
    """[{customer, customer_name, telephones, secteurs, familles}] à réveiller aujourd'hui, les plus anciens d'abord :
    dernier appel il y a au moins jours_reveil(cfg) jours, dernier réveil il y a au moins un an (eligible) ; aucun si
    le réglage vaut 0."""
    cfg = cfg or RC.config()
    jours = jours_reveil(cfg)
    if jours <= 0 or not frappe.db.has_column("Customer", "custom_sms_reveil_le"):
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
    # « Non » à l'entretien (sauf réglage) : pas de réveil non plus — 61 « À requalifier » « Non » acceptant les SMS l'auraient
    # reçu une fois leur délai écoulé (relecture du 26/11/2026 ; le trou existait avant).
    if not cint(cfg.get("relancer_non_interesses")):
        exclus |= set(frappe.get_all("Customer", filters={"custom_intéressé_par_le_service_entretien": "Non"}, pluck="name"))
    out = []
    for r in rows:
        if r.name in exclus or (r.reportee and getdate(r.reportee) > getdate(today)):
            continue
        if not eligible(r.statut, r.envoi_sms, r.dernier_appel, r.dernier_reveil, today, jours):
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
    jours = jours_reveil(cfg)
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
                        "content": texte_trace(today, jours, texte)}).insert(ignore_permissions=True)
        resume["envoyes"] += 1
    log("[REVEIL SUMMARY] %s (délai %d j)" % (resume, jours))
    if not dry_run:
        _noter_entonnoir(resume, jours)
    return resume


def texte_trace(today, jours, texte) -> str:
    """Le commentaire de la fiche client (il disait « un an sans contact » quel que soit le délai). PURE."""
    return "📨 SMS de réveil envoyé le %s (aucun contact depuis au moins %d j) : %s" % (getdate(today).strftime("%d/%m/%Y"), cint(jours), texte)


def compteurs_entonnoir(resume, jours) -> dict:
    """Ce que l'entonnoir garde d'un passage. Les vrais compteurs sont des nombres (l'entonnoir les additionne d'un
    passage à l'autre le même jour) ; le délai et le mode sont du TEXTE, qu'il remplace (relecture du 26/11/2026 : en
    nombres, un passage à 10:05 et un à la main affichaient « jours = 360, simules = 2 »). PURE."""
    return {"candidats": cint(resume.get("candidats")), "envoyes": cint(resume.get("envoyes")),
            "sans_numero": cint(resume.get("sans_numero")), "echecs": cint(resume.get("echecs")),
            "delai": "%d j" % cint(jours), "mode": "simulé" if resume.get("simules") else "réel"}


def _noter_entonnoir(resume, jours):
    """Les compteurs du jour dans l'entonnoir des relances (module du lot « entonnoir », peut ne pas encore exister)."""
    try:
        from customization_app.entonnoir_relances import noter
        noter("reveil", compteurs_entonnoir(resume, jours))
    except Exception:
        pass


def run_cron():
    return run_safely("Cron - SMS de réveil", envoyer_reveils)
