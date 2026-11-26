"""Visites FAITES restées « Pending » : leur statut passe « Fully Completed » (proposition 9 de l'analyse « cartographie
entretien », 25/11/2026 ; corrigé le 26/11/2026).

LE CAS MESURÉ (copie de prod du 25/11/2026) : 76 visites d'échéanciers soumis (73 échéanciers, 72 clients) ont une date
réelle mais le statut « Pending » — 3 de 2023, 36 de 2024, 35 de 2025, 2 de 2026 (la dernière le 28/04/2026) ; 72 sans
commande : c'étaient des SAISIES À LA MAIN sur l'échéancier (la date réelle tapée, le statut oublié), les 4 autres viennent
de l'ancien passage de nuit (avant le 03/10/2026). Pour chacune, date prévue = date réelle (le Server Script de recalage
les a alignées). Par visite, le client est : « Oui » relancé 50 fois, « À requalifier » 12, « Non » ou « Perdu » 14.

CRITÈRE : échéancier soumis, date réelle renseignée, date réelle ≤ aujourd'hui, statut différent de « Fully Completed ».
Une date réelle dans le futur serait une faute de frappe : laissée, citée dans le compte rendu (0 sur la copie).
L'inverse — « Fully Completed » SANS date réelle (1 visite : MAT-MSH-2023-00051, AP-5 du 24/06/2024, cliente « Non ») — est
laissé : aucune date à poser ; elle est citée dans le compte rendu.

EFFET : aucun sur les relances, la prolongation ni le compteur de lassitude — tous jugent déjà « faite » une visite qui a
une date réelle (actual_date) : choisir_relances (SMS), creation_liste_appelle (listes : actual_date IS NULL),
update_schedule.visite_a_venir / echeanciers_epuises / cycles_sans_reponse_par_client. Vérifié sur la copie : mêmes
candidats SMS, mêmes visites à appeler, mêmes échéanciers épuisés, mêmes cycles avant / après. Ce qui change : les vues et
filtres sur le statut (liste ERPNext des visites « Pending », Historique client affichait déjà « réalisée »), et le hook
echeancier_hooks.before_update_after_submit n'a plus rien à aligner. Les nouvelles saisies à la main passent désormais
« Fully Completed » d'elles-mêmes (ce même hook).

SQL direct (comme annulation_commande) : ni date, ni SMS, ni appel, ni date de modification touchés ; aucun enregistrement
d'échéancier (aucun hook, aucun Server Script). Rejouable sans effet. → compte rendu (aussi dans le Journal des erreurs)."""
import frappe
from frappe.utils import getdate, nowdate

FAIT = "Fully Completed"


def a_corriger(r, aujourd_hui) -> bool:
    """La visite a une date réelle passée (ou du jour) et n'est pas « Fully Completed ». PURE."""
    return bool(r.get("actual_date")) and getdate(r["actual_date"]) <= getdate(aujourd_hui) \
        and (r.get("completion_status") or "") != FAIT


def classer(lignes, aujourd_hui) -> dict:
    """{"corriger": [...], "futur": [...] (date réelle à venir : faute de frappe), "sans_date": [...] (« Fully Completed »
    sans date réelle)}. PURE. `lignes` : visites d'échéanciers soumis {name, parent, item_code, scheduled_date,
    actual_date, completion_status}."""
    out = {"corriger": [], "futur": [], "sans_date": []}
    for r in lignes or []:
        statut = r.get("completion_status") or ""
        if r.get("actual_date"):
            if statut == FAIT:
                continue
            out["corriger" if a_corriger(r, aujourd_hui) else "futur"].append(r)
        elif statut == FAIT:
            out["sans_date"].append(r)
    return out


def _jj(d):
    return getdate(d).strftime("%d/%m/%Y") if d else "—"


def compte_rendu(c) -> str:
    """Le compte rendu en clair, une ligne par visite. PURE."""
    lignes = ["%d visite(s) faite(s) passée(s) « Fully Completed » (%d échéancier(s))."
              % (len(c["corriger"]), len({r["parent"] for r in c["corriger"]}))]
    for r in c["corriger"]:
        lignes.append("  %s %s : faite le %s (prévue le %s, commande %s, statut avant : %s)"
                      % (r["parent"], r["item_code"], _jj(r["actual_date"]), _jj(r["scheduled_date"]),
                         r.get("custom_sales_order") or "aucune", r.get("completion_status") or "vide"))
    for cle, titre in (("futur", "laissée(s) : date réelle dans le futur (faute de frappe ?)"),
                       ("sans_date", "laissée(s) : « Fully Completed » sans date réelle, aucune date à poser")):
        if c[cle]:
            lignes.append("%d visite(s) %s :" % (len(c[cle]), titre))
            lignes += ["  %s %s prévue le %s" % (r["parent"], r["item_code"], _jj(r["scheduled_date"])) for r in c[cle]]
    return "\n".join(lignes)


def execute(aujourd_hui=None):
    jour = getdate(aujourd_hui or nowdate())
    lignes = frappe.db.sql("""
        select d.name, d.parent, d.item_code, d.scheduled_date, d.actual_date, d.completion_status, d.custom_sales_order
        from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
        where ms.docstatus = 1 and d.parenttype = 'Maintenance Schedule'
          and ((d.actual_date is not null and ifnull(d.completion_status, '') != %(fait)s)
               or (d.actual_date is null and d.completion_status = %(fait)s))
        order by d.parent, d.idx""", {"fait": FAIT}, as_dict=True)
    c = classer(lignes, jour)
    for r in c["corriger"]:
        frappe.db.set_value("Maintenance Schedule Detail", r["name"], "completion_status", FAIT, update_modified=False)
    texte = compte_rendu(c)
    frappe.log_error(title="Visites faites restées « Pending » : statut corrigé", message=texte)
    try:
        from customization_app.entonnoir_relances import noter
        noter("patch_visites_faites", {"corrigees": len(c["corriger"]), "futur": len(c["futur"]), "sans_date": len(c["sans_date"])})
    except ImportError:
        pass
    return {"corrigees": len(c["corriger"]), "echeanciers": len({r["parent"] for r in c["corriger"]}),
            "futur": len(c["futur"]), "sans_date": len(c["sans_date"]), "texte": texte}
