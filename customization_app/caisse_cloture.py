"""
Validation (clôture) de la caisse journalière.

L'ÉTAT AVANT / APRÈS (décision utilisateur 19/08) :
  - AVANT  : le solde d'ouverture = le report de la DERNIÈRE clôture validée de
             cette caisse — les espèces COMPTÉES si l'employé les a saisies,
             sinon le théorique. Première clôture : 0 ;
  - le jour : encaissements espèces (totaux de la période, avances hors période
             exclues) − dépenses espèces de la caisse ;
  - APRÈS  : solde théorique = ouverture + encaissements − dépenses. L'employé
             saisit les espèces réellement comptées ; l'écart est figé avec.

QUI VALIDE QUOI :
  - chaque employé valide SA caisse ;
  - la DIRECTION (Wassim, Jamel, Néjib — même cercle que la visibilité
    Economiq) valide n'importe quelle caisse ET la caisse globale
    « Tous les employés ».

L'INSTANTANÉ : la clôture est un document SOUMIS (immuable), avec un PDF du
récapitulatif (état, modes, dépenses, détail par employé pour la globale)
attaché au moment de la validation. Une seule clôture par caisse et par jour.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from customization_app.rapport_caisse_journaliere import (
    PEUVENT_VOIR_ECONOMIQ as DIRECTION,
    _exclusions,
    _ma_caisse,
    get_data,
)

CAISSE_GLOBALE = "Tous les employés"
ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")


def _controler_droits(caisse):
    """Chaque employé -> sa caisse ; la direction -> toutes + la globale."""
    if frappe.session.user in DIRECTION or "System Manager" in frappe.get_roles():
        return
    # _ma_caisse a besoin des listes ; on la reconstruit a minima.
    exclus_u, exclus_e = _exclusions()
    employees = [e for e in frappe.db.sql(
        """SELECT e.name AS employee_id, e.employee_name, e.user_id AS user_email
           FROM `tabEmployee` e""", as_dict=True)
        if e.employee_id not in exclus_e and (e.user_email or "") not in exclus_u]
    users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"},
                           fields=["name", "full_name"])
    users = [u for u in users if u.name not in exclus_u]
    mienne = _ma_caisse(employees, users)
    if caisse != mienne:
        frappe.throw(_("Vous ne pouvez valider que votre propre caisse ({0}).")
                     .format(mienne or _("aucune")))


def _mesures(caisse, date):
    """Les chiffres du jour pour cette caisse, depuis la MÊME source que la page."""
    data = get_data(str(date), str(date), employe=("" if caisse == CAISSE_GLOBALE else caisse))
    recap = data.get("recap") or {}
    par_mode = recap.get("par_mode") or {}
    depenses = data.get("depenses") or {}
    dep_par_mode = depenses.get("par_mode") or {}
    especes = flt(par_mode.get("Espèces"), 3)
    cheques = flt(par_mode.get("Chèque"), 3)
    autres = flt(flt(recap.get("grand_total"), 3) - especes - cheques, 3)
    return {
        "encaissements_especes": especes,
        "total_cheques": cheques,
        "total_autres_modes": autres,
        "depenses_especes": flt(dep_par_mode.get("Espèces"), 3),
        "data": data,
    }


def _ouverture(caisse, date):
    """Le report de la dernière clôture validée : espèces comptées si saisies,
    sinon le théorique."""
    ligne = frappe.db.sql(
        """SELECT solde_theorique, especes_comptees FROM `tabCloture Caisse`
           WHERE docstatus = 1 AND caisse = %s AND date_cloture < %s
           ORDER BY date_cloture DESC, creation DESC LIMIT 1""",
        (caisse, date), as_dict=True)
    if not ligne:
        return 0.0
    r = ligne[0]
    return flt(r.especes_comptees if r.especes_comptees is not None else r.solde_theorique, 3)


def _controles(data):
    """Les points de contrôle de la clôture (décisions utilisateur 19/08) :

      tache_ouverte    : une intervention du jour reste ouverte -> JUSTIFIER ;
      bl_non_valide    : tâche terminée mais bon de livraison en brouillon ->
                         BLOQUANT, le BL doit être validé (bouton dédié) ;
      dette_hors_aramex: commande validée, tâche terminée, un paiement en
                         « Dette non payée » hors flux Aramex -> JUSTIFIER ;
      ancien_exclu     : un paiement d'ancienne commande a été EXCLU de la
                         caisse -> JUSTIFIER l'exclusion.
    """
    points = []
    for e in data.get("employees") or []:
        for o in e.get("orders") or []:
            if o.get("task_open"):
                points.append({
                    "cle": "tache_ouverte:%s" % o["sales_order"],
                    "type": "tache_ouverte", "bloquant": 0,
                    "commande": o["sales_order"], "client": o.get("customer"),
                    "libelle": "Tâche ouverte sur %s (%s)" % (
                        o["sales_order"], o.get("customer") or "?"),
                })
            if o.get("tache_status") == "Completed":
                for dn in o.get("delivery_notes") or []:
                    if dn.get("docstatus") == 0:
                        points.append({
                            "cle": "bl_non_valide:%s" % dn["name"],
                            "type": "bl_non_valide", "bloquant": 1,
                            "commande": o["sales_order"], "bl": dn["name"],
                            "libelle": "Tâche terminée mais BL %s non validé (%s)" % (
                                dn["name"], o["sales_order"]),
                        })
                if (o.get("is_validated") and not o.get("is_aramex")
                        and any((p.get("mode") == "Dette non payée")
                                for p in o.get("payments") or [])):
                    points.append({
                        "cle": "dette_hors_aramex:%s" % o["sales_order"],
                        "type": "dette_hors_aramex", "bloquant": 0,
                        "commande": o["sales_order"], "client": o.get("customer"),
                        "libelle": "Commande validée, tâche terminée, mais paiement "
                                   "en dette (hors Aramex) sur %s" % o["sales_order"],
                    })
    for pmt in (data.get("anciens") or {}).get("paiements") or []:
        if pmt.get("exclu"):
            points.append({
                "cle": "ancien_exclu:%s" % pmt["name"],
                "type": "ancien_exclu", "bloquant": 0,
                "paiement": pmt["name"], "client": pmt.get("customer_name"),
                "libelle": "Paiement %s (%s, %s DT) exclu de la caisse" % (
                    pmt["name"], pmt.get("customer_name") or "?", pmt.get("amount")),
            })
    return points


@frappe.whitelist()
def valider_bl(bl):
    """Valide un bon de livraison en brouillon depuis le contrôle de clôture —
    le droit demandé : même geste que le magasin (workflow « Validation
    Magasin » : Approved puis soumission)."""
    frappe.only_for(ROLES)
    doc = frappe.get_doc("Delivery Note", bl)
    if doc.docstatus == 1:
        return {"bl": bl, "deja": True}
    if doc.docstatus != 0:
        frappe.throw(_("Le BL {0} est annulé — rien à valider.").format(bl))
    if doc.get("workflow_state") is not None:
        doc.db_set("workflow_state", "Approved", update_modified=False)
        doc.reload()
    frappe.flags.in_import = True
    try:
        doc.submit()
    finally:
        frappe.flags.in_import = False
    frappe.db.commit()
    return {"bl": bl, "valide": True}


@frappe.whitelist()
def etat(caisse, date):
    """L'état de la caisse pour le dialogue : avant, mouvements, après."""
    frappe.only_for(ROLES)
    caisse = (caisse or "").strip() or CAISSE_GLOBALE
    _controler_droits(caisse)
    date = getdate(date)
    m = _mesures(caisse, date)
    ouverture = _ouverture(caisse, date)
    theorique = flt(ouverture + m["encaissements_especes"] - m["depenses_especes"], 3)
    deja = frappe.db.get_value("Cloture Caisse",
                               {"caisse": caisse, "date_cloture": date, "docstatus": 1})
    pdf_url = None
    if deja:
        pdf_url = frappe.db.get_value(
            "File", {"attached_to_doctype": "Cloture Caisse", "attached_to_name": deja,
                     "file_name": ["like", "%.pdf"]}, "file_url")
    return {
        "caisse": caisse, "date": str(date),
        "controles": _controles(m["data"]),
        "solde_ouverture": ouverture,
        "encaissements_especes": m["encaissements_especes"],
        "depenses_especes": m["depenses_especes"],
        "solde_theorique": theorique,
        "total_cheques": m["total_cheques"],
        "total_autres_modes": m["total_autres_modes"],
        "deja_validee": deja,
        "pdf_url": pdf_url,
    }


@frappe.whitelist()
def valider(caisse, date, especes_comptees=None, note=None, justifications=None):
    """Fige la caisse : document soumis + PDF instantané attaché."""
    frappe.only_for(ROLES)
    caisse = (caisse or "").strip() or CAISSE_GLOBALE
    _controler_droits(caisse)
    date = getdate(date)
    if frappe.db.get_value("Cloture Caisse",
                           {"caisse": caisse, "date_cloture": date, "docstatus": 1}):
        frappe.throw(_("La caisse « {0} » du {1} est déjà validée.").format(caisse, date))

    m = _mesures(caisse, date)

    # Les contrôles sont REJOUÉS côté serveur : un BL encore en brouillon bloque,
    # chaque autre point exige sa justification écrite.
    import json as _json
    justifs = (_json.loads(justifications) if isinstance(justifications, str)
               else (justifications or {}))
    points = _controles(m["data"])
    bloquants = [p for p in points if p["bloquant"]]
    if bloquants:
        frappe.throw(_("Validation refusée — bon(s) de livraison à valider d'abord : {0}")
                     .format(", ".join(p["bl"] for p in bloquants)))
    manquantes = []
    for p in points:
        if not (justifs.get(p["cle"]) or "").strip():
            manquantes.append(p["libelle"])
    if manquantes:
        frappe.throw(_("Justification manquante :<br>• {0}")
                     .format("<br>• ".join(frappe.utils.escape_html(x)
                                           for x in manquantes)))
    lignes_controles = []
    for p in points:
        lignes_controles.append("%s\n  → %s" % (p["libelle"], justifs.get(p["cle"]).strip()))
    controles_txt = "\n".join(lignes_controles)

    ouverture = _ouverture(caisse, date)
    theorique = flt(ouverture + m["encaissements_especes"] - m["depenses_especes"], 3)
    comptees = flt(especes_comptees, 3) if especes_comptees not in (None, "") else None

    doc = frappe.get_doc({
        "doctype": "Cloture Caisse",
        "caisse": caisse,
        "date_cloture": date,
        "valide_par": frappe.session.user,
        "solde_ouverture": ouverture,
        "encaissements_especes": m["encaissements_especes"],
        "depenses_especes": m["depenses_especes"],
        "solde_theorique": theorique,
        "especes_comptees": comptees,
        "ecart": (flt(comptees - theorique, 3) if comptees is not None else 0),
        "total_cheques": m["total_cheques"],
        "total_autres_modes": m["total_autres_modes"],
        "note": (note or "").strip(),
        "controles": controles_txt,
    })
    doc.insert(ignore_permissions=True)
    doc.submit()

    from frappe.utils.pdf import get_pdf
    pdf = get_pdf(_html_instantane(doc, m["data"]))
    from frappe.utils.file_manager import save_file
    save_file("caisse-%s-%s.pdf" % (caisse.replace(" ", "_"), date), pdf,
              "Cloture Caisse", doc.name, is_private=1)
    frappe.db.commit()
    return {"name": doc.name}


def _html_instantane(doc, data):
    """Le récapitulatif figé de la caisse, prêt pour le PDF."""
    esc = frappe.utils.escape_html
    fmt = lambda v: frappe.utils.fmt_money(flt(v), 3, "TND")  # noqa: E731

    recap = data.get("recap") or {}
    depenses = data.get("depenses") or {}
    lignes_modes = "".join(
        "<tr><td>%s</td><td class='n'>%s</td></tr>" % (esc(m), fmt(v))
        for m, v in (recap.get("par_mode") or {}).items() if flt(v))
    lignes_dep = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='n'>%s</td></tr>" % (
            esc(l["date"]), esc(l["saisi_par"]), esc(l["type"]),
            esc(l["description"]), fmt(l["montant"]))
        for l in (depenses.get("lignes") or []))
    lignes_emp = "".join(
        "<tr><td>%s</td><td class='n'>%s</td></tr>" % (esc(e["employe"]), fmt(e["total"]))
        for e in (recap.get("par_employe") or []))

    return """
    <style>
      body { font-family: sans-serif; font-size: 12px; color: #222; }
      h1 { font-size: 18px; margin-bottom: 2px; } .sous { color: #666; margin-bottom: 14px; }
      table { border-collapse: collapse; width: 100%%; margin: 8px 0 14px; }
      th, td { border: 1px solid #ccc; padding: 5px 8px; text-align: left; }
      th { background: #f2f2f2; } td.n { text-align: right; }
      .etat td { font-size: 13px; } .etat .val { font-weight: 700; text-align: right; }
      h3 { font-size: 13px; margin: 12px 0 2px; }
    </style>
    <h1>Clôture de caisse — %(caisse)s</h1>
    <div class="sous">%(date)s · validée par %(par)s · %(horodatage)s · %(nom)s</div>
    <table class="etat">
      <tr><td>Solde d'ouverture (avant)</td><td class="val">%(ouverture)s</td></tr>
      <tr><td>Encaissements espèces du jour</td><td class="val">+ %(enc)s</td></tr>
      <tr><td>Dépenses espèces du jour</td><td class="val">− %(dep)s</td></tr>
      <tr><td><b>Solde théorique (après)</b></td><td class="val">%(theo)s</td></tr>
      <tr><td>Espèces comptées</td><td class="val">%(comptees)s</td></tr>
      <tr><td><b>Écart</b></td><td class="val">%(ecart)s</td></tr>
    </table>
    <h3>Encaissements du jour par mode</h3>
    <table><tr><th>Mode</th><th>Montant</th></tr>%(modes)s</table>
    %(bloc_emp)s
    %(bloc_dep)s
    %(controles)s
    %(note)s
    """ % {
        "caisse": esc(doc.caisse), "date": esc(str(doc.date_cloture)),
        "par": esc(doc.valide_par), "nom": esc(doc.name),
        "horodatage": esc(str(now_datetime())[:19]),
        "ouverture": fmt(doc.solde_ouverture), "enc": fmt(doc.encaissements_especes),
        "dep": fmt(doc.depenses_especes), "theo": fmt(doc.solde_theorique),
        "comptees": (fmt(doc.especes_comptees)
                     if doc.especes_comptees is not None else "—"),
        "ecart": fmt(doc.ecart),
        "modes": lignes_modes or "<tr><td colspan='2'>aucun</td></tr>",
        "bloc_emp": ("<h3>Par employé</h3><table><tr><th>Employé</th><th>Total</th></tr>%s</table>"
                     % lignes_emp if doc.caisse == CAISSE_GLOBALE and lignes_emp else ""),
        "bloc_dep": ("<h3>Dépenses de la caisse</h3><table><tr><th>Date</th><th>Saisie par</th>"
                     "<th>Type</th><th>Description</th><th>Montant</th></tr>%s</table>"
                     % lignes_dep if lignes_dep else ""),
        "note": ("<h3>Note</h3><p>%s</p>" % esc(doc.note) if doc.note else ""),
        "controles": ("<h3>Points de contrôle justifiés</h3><pre style='font-size:11px'>%s</pre>"
                      % esc(doc.controles) if doc.get("controles") else ""),
    }
