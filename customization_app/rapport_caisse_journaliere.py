"""
Backend du Rapport Caisse Journalière (Page custom).

Porte la logique de l'ancien Script Report vers une API whitelisted qui renvoie
du JSON structuré, rendu par une Page Desk (customize_erpnext/page/rapport_caisse_journaliere).
"""

import re

import frappe
from frappe.utils import flt, getdate

from customization_app.caisse_depenses import TYPE_AVANCE_COMMANDE
from customization_app.caisse_depenses import TYPE_REGLEMENT as TYPE_REGLEMENT_FOURNISSEUR

COMPANY = "AquaWorld & Servicing"

# Hors caisse (décision utilisateur 19/08) — exclus par IDENTIFIANTS, jamais par nom
# (Economiq a deux fiches aux orthographes différentes) :
#   - Ayman Tekfa, Nizar Maddouri : hors équipe ;
#   - Catalogue POS : compte TECHNIQUE, pas une personne.
# L'équipe de caisse : Akram, Jamel Aloui, Sadok Bouziri, Mohamed Hedi Chouchane,
# Nejib Koubaa, Wassim Koubaa.
EXCLUS_UTILISATEURS = {
    "aymen.tekfa96@gmail.com",
    "aquaworld.commercial@gmail.com",
    "catalogue.pos@aquaworld.com",
}
EXCLUS_EMPLOYES = {"HR-EMP-00008", "HR-EMP-00009"}

# Le PARTENAIRE Economiq : sa caisse n'est visible que de la direction (décision 19/08).
ECONOMIQ_UTILISATEUR = "economiqaquasolutions23@gmail.com"
ECONOMIQ_EMPLOYE = "HR-EMP-00007"
PEUVENT_VOIR_ECONOMIQ = {
    "koubaawassim@gmail.com",        # Wassim Koubaa
    "jimmyks007@gmail.com",          # Jamel Aloui
    "aquaworld.servicing@gmail.com", # Nejib Koubaa
}


def _exclusions():
    """Exclusions effectives pour l'utilisateur CONNECTÉ : Economiq ne se montre
    qu'à la direction, les comptes techniques/hors équipe jamais."""
    exclus_u = set(EXCLUS_UTILISATEURS)
    exclus_e = set(EXCLUS_EMPLOYES)
    if frappe.session.user not in PEUVENT_VOIR_ECONOMIQ:
        exclus_u.add(ECONOMIQ_UTILISATEUR)
        exclus_e.add(ECONOMIQ_EMPLOYE)
    return exclus_u, exclus_e


def _ma_caisse(employees, users):
    """Le nom affiché de la caisse de l'utilisateur connecté, s'il en a une."""
    moi = frappe.session.user
    for e in employees:
        if (e.user_email or "") == moi:
            return e.employee_name
    for u in users:
        if u.name == moi:
            return u.full_name
    return None

SO_STATUS_FR = {
    "Draft": "Brouillon",
    "To Bill": "À facturer",
    "To Deliver": "À livrer",
    "To Deliver and Bill": "À livrer et facturer",
    "Completed": "Terminé",
    "Closed": "Fermé",
    "Cancelled": "Annulé",
    "On Hold": "En attente",
}


# Les rôles qui donnent une caisse à un compte SANS fiche employé (la direction). Un compte technique — les assistants
# du MCP « Assistant ChatGPT » / « Assistant Articles », créés le 23/11/2026 — n'en a aucun : il n'a pas de caisse.
DOMAINE_TECHNIQUE = "@aquaworld.local"
ROLES_CAISSE = ("Sales User", "Sales Manager", "Accounts User", "Accounts Manager", "System Manager")


def noms_du_selecteur(employees, users, avec_role_caisse, debut=None) -> list:
    """Les caisses proposées par la liste déroulante. PURE.

    Demande du 26/11/2026 (« pourquoi j'ai toute cette liste ? ») : la liste montrait les deux assistants du MCP et les
    employés partis (Akram, Mohamed Hedi).
      · un employé de la société a une caisse ; PARTI, il n'est proposé que si la période affichée commence au plus tard
        le jour de son départ (son historique reste consultable en remontant la date) ;
      · un compte sans fiche employé n'en a une que s'il porte un rôle de caisse (`avec_role_caisse`).
    `employees` : [{employee_name, user_email, status, relieving_date}] ; `users` : [{name, full_name}] ;
    `debut` : le premier jour affiché (None = pas de période : tous les employés, pour les réglages et l'historique)."""
    noms = set()
    for e in employees or []:
        if not e.get("employee_name"):
            continue
        depart = e.get("relieving_date")
        if debut and (e.get("status") or "Active") == "Left" and depart and str(debut)[:10] > str(depart)[:10]:
            continue
        noms.add(e["employee_name"])
    emails_employes = {e.get("user_email") for e in employees or [] if e.get("user_email")}
    for u in users or []:
        # Les comptes techniques (@aquaworld.local : assistants du MCP, agent du CRM) n'ont jamais de caisse, quels que
        # soient leurs rôles.
        if (u.get("name") or "").lower().endswith(DOMAINE_TECHNIQUE):
            continue
        if u.get("full_name") and u.get("name") not in emails_employes and u.get("name") in (avec_role_caisse or ()):
            noms.add(u["full_name"])
    return sorted(noms)


def _avec_role_caisse(users) -> set:
    noms = [u.get("name") for u in users or [] if u.get("name")]
    if not noms:
        return set()
    return set(frappe.get_all("Has Role", filters={"parenttype": "User", "parent": ["in", noms], "role": ["in", ROLES_CAISSE]},
                              pluck="parent"))


def noms_caisses():
    """Les noms de caisse acceptés (réglages, argent liquide, caisse payeuse) : employés de la société (nom d'employé,
    partis compris : leur historique existe) + comptes sans fiche employé qui ont un rôle de caisse, exclusions comprises."""
    exclus_u, exclus_e = _exclusions()
    employees = [e for e in frappe.db.sql(
        """SELECT e.name AS employee_id, e.employee_name, e.user_id AS user_email, e.status, e.relieving_date
           FROM `tabEmployee` e WHERE e.company = %s""", COMPANY, as_dict=True)
        if e.employee_id not in exclus_e and (e.user_email or "") not in exclus_u]
    users = [u for u in frappe.get_all(
        "User", filters={"enabled": 1, "user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]},
        fields=["name", "full_name"]) if u.name not in exclus_u]
    return noms_du_selecteur(employees, users, _avec_role_caisse(users))


def _unbilled_sos(user, start_date, end_date):
    conds = ["so.transaction_date BETWEEN %s AND %s"]
    vals = [start_date, end_date]
    if user:
        conds.append("so.owner = %s")
        vals.append(user)
    conds.append(
        "NOT EXISTS (SELECT 1 FROM `tabTache de travail` tt "
        "WHERE tt.commande_client = so.name AND tt.docstatus < 2)"
    )
    where = " AND ".join(conds)
    return frappe.db.sql(
        f"""SELECT DISTINCT so.name, so.customer, so.transaction_date, so.grand_total, so.owner
            FROM `tabSales Order` so WHERE {where} ORDER BY so.transaction_date DESC""",
        tuple(vals), as_dict=True,
    )


def _taches(staff, start_date, end_date):
    conds = []
    vals = []
    if staff:
        conds.append("tt.custom_choix_du_staff = %s")
        vals.append(staff)
    conds.append("tt.starts_on BETWEEN %s AND %s")
    vals.extend([f"{start_date} 00:00:00", f"{end_date} 23:59:59"])
    where = " AND ".join(conds) if conds else "1=1"
    return frappe.db.sql(
        f"""SELECT DISTINCT tt.name, tt.custom_type_dintervention, tt.custom_employé,
                   tt.custom_client, tt.commande_client, tt.status, tt.starts_on
            FROM `tabTache de travail` tt WHERE {where} ORDER BY tt.starts_on DESC""",
        tuple(vals), as_dict=True,
    )


def _links(sales_order):
    sis = frappe.db.sql(
        """SELECT DISTINCT si.name, si.grand_total, si.docstatus FROM `tabSales Invoice` si
           INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
           WHERE sii.sales_order = %s AND si.docstatus < 2""",
        (sales_order,), as_dict=True,
    )
    si_names = [r.name for r in sis]

    dns = frappe.db.sql(
        """SELECT DISTINCT dn.name, dn.grand_total, dn.docstatus FROM `tabDelivery Note` dn
           INNER JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
           WHERE dni.against_sales_order = %s AND dn.docstatus < 2""",
        (sales_order,), as_dict=True,
    )

    pes = []
    if si_names:
        ph = ",".join(["%s"] * len(si_names))
        inv_pes = frappe.db.sql(
            f"""SELECT DISTINCT pe.name, pe.posting_date, DATE(pe.creation) AS creation_date,
                pe.paid_amount, pe.mode_of_payment, pe.reference_no
                FROM `tabPayment Entry` pe
                INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
                WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name IN ({ph})
                  AND pe.docstatus = 1 AND pe.payment_type = 'Receive'""",
            tuple(si_names), as_dict=True,
        )
        for pe in inv_pes:
            pe["link_type"] = "Invoice"
            pes.append(pe)
        direct = frappe.db.sql(
            f"""SELECT DISTINCT pe.name, pe.posting_date, DATE(pe.creation) AS creation_date,
                pe.paid_amount, pe.mode_of_payment, pe.reference_no
                FROM `tabPayment Entry` pe
                INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
                WHERE per.reference_doctype = 'Sales Order' AND per.reference_name = %s
                  AND pe.docstatus = 1 AND pe.payment_type = 'Receive'
                  AND pe.name NOT IN (
                      SELECT DISTINCT per2.parent FROM `tabPayment Entry Reference` per2
                      WHERE per2.reference_doctype = 'Sales Invoice' AND per2.reference_name IN ({ph}))""",
            (sales_order,) + tuple(si_names), as_dict=True,
        )
    else:
        direct = frappe.db.sql(
            """SELECT DISTINCT pe.name, pe.posting_date, DATE(pe.creation) AS creation_date,
                pe.paid_amount, pe.mode_of_payment, pe.reference_no
               FROM `tabPayment Entry` pe
               INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
               WHERE per.reference_doctype = 'Sales Order' AND per.reference_name = %s
                 AND pe.docstatus = 1 AND pe.payment_type = 'Receive'""",
            (sales_order,), as_dict=True,
        )
    for pe in direct:
        pe["link_type"] = "Direct SO"
        pes.append(pe)

    return sis, dns, pes


def _dans_periode(valeur, d1, d2):
    return bool(valeur) and getdate(d1) <= getdate(valeur) <= getdate(d2)


# Ni la dette ni la perte de non paiement ne sont des AVANCES (décision utilisateur 06/10/2026) :
# l'avance, c'est de l'argent reçu avant la journée — tous les autres modes. Une dette (ou une perte)
# saisie avant reste celle de la journée de l'intervention : elle compte dans la caisse de ce jour.
MODES_PAS_AVANCE = ("Dette non payée", "Perte de paiement")


def _hors_periode(p, d1, d2):
    """VRAIE avance antérieure : ni la date comptable ni la date de SAISIE ne
    tombent dans la période — exclue des totaux du jour. Un paiement saisi
    aujourd'hui mais antidaté (mauvaise date entrée par l'employé) COMPTE :
    l'argent est entré aujourd'hui (même règle que les anciennes commandes).
    Une dette ou une perte n'est jamais une avance (MODES_PAS_AVANCE)."""
    if not (d1 and d2):
        return False
    if (p.get("mode_of_payment") or p.get("mode")) in MODES_PAS_AVANCE:
        return False
    return not (_dans_periode(p.get("posting_date"), d1, d2)
                or _dans_periode(p.get("creation_date"), d1, d2))


def _antidate(p, d1, d2):
    """Saisi dans la période mais daté hors : compté, signalé ⚠."""
    if not (d1 and d2):
        return False
    return (_dans_periode(p.get("creation_date"), d1, d2)
            and not _dans_periode(p.get("posting_date"), d1, d2))


def _build_order(so_name, task, so_meta, d1=None, d2=None):
    """Assemble un dict commande prêt pour le front. `d1`/`d2` bornent la CAISSE :
    un paiement daté hors période (l'AVANCE d'une commande finalisée aujourd'hui)
    reste visible dans le détail mais est marqué `hors_periode` — il ne doit
    jamais entrer dans les totaux du jour."""
    sis, dns, pes = _links(so_name)

    ptt = so_meta.get("payment_terms_template")
    is_aramex = (ptt == "Livraison Aramex")
    has_dette = any((p.get("mode_of_payment") == "Dette non payée") for p in pes)

    intervention = task.get("custom_type_dintervention") if task else None
    if is_aramex and has_dette:
        intervention = "Livraison Aramex"

    total_paid = sum(flt(p.get("paid_amount")) for p in pes)
    gt = flt(so_meta.get("grand_total"))
    if total_paid < gt - 0.5:
        alert = "underpaid"
    elif total_paid > gt + 0.5:
        alert = "overpaid"
    elif not sis:
        alert = "unbilled"
    else:
        alert = None

    tache_status = task.get("status") if task else None
    task_open = (tache_status == "Open")

    # Avertissements de cohérence
    is_validated = (so_meta.get("docstatus") == 1)
    warnings = []
    if is_validated:
        draft_invoices = [s.name for s in sis if s.docstatus == 0]
        draft_bls = [d.name for d in dns if d.docstatus == 0]
        inv_valid = sum(flt(s.grand_total) for s in sis if s.docstatus == 1)
        dn_valid = sum(flt(d.grand_total) for d in dns if d.docstatus == 1)
        has_valid_invoice = inv_valid > 0.5

        # 1) Contrôle des paiements : basé sur la facture si elle existe (validée), sinon sur la commande.
        if has_valid_invoice:
            if abs(total_paid - inv_valid) > 0.5:
                warnings.append({
                    "type": "payment",
                    "message": "Total des paiements (%.3f) ≠ Total des factures TTC (%.3f)" % (total_paid, inv_valid),
                })
        else:
            if abs(total_paid - gt) > 0.5:
                warnings.append({
                    "type": "payment",
                    "message": "Total des paiements (%.3f) ≠ Total TTC de la commande (%.3f)" % (total_paid, gt),
                })

        # 2) Facture(s) liée(s) mais non validée(s) (brouillon)
        if draft_invoices:
            warnings.append({
                "type": "invoice_draft",
                "message": "Facture(s) non validée(s) : " + ", ".join(draft_invoices),
            })

        # 3) Bon(s) de livraison non validé(s) (brouillon)
        if draft_bls:
            warnings.append({
                "type": "delivery_draft",
                "message": "Bon(s) de livraison non validé(s) : " + ", ".join(draft_bls),
            })

        # 4) Total des BL validés ≠ Total des factures TTC
        if has_valid_invoice and dn_valid > 0.5 and abs(dn_valid - inv_valid) > 0.5:
            warnings.append({
                "type": "delivery",
                "message": "Total des BL validés (%.3f) ≠ Total des factures TTC (%.3f)" % (dn_valid, inv_valid),
            })

        # 5) Paiement en « Dette non payée » hors Livraison Aramex
        if has_dette and not is_aramex:
            warnings.append({
                "type": "dette",
                "message": "Commande validée avec paiement en « Dette non payée » (hors Livraison Aramex)",
            })

    # 6) Tâche de travail ouverte (indépendant de la validation)
    if task_open:
        warnings.append({
            "type": "task_open",
            "message": "Tâche de travail ouverte : à fermer, ou à déplacer si le client souhaite un autre rendez-vous.",
        })

    return {
        "sales_order": so_name,
        "customer": so_meta.get("customer"),
        "date": str(so_meta.get("transaction_date") or ""),
        "grand_total": gt,
        "status": SO_STATUS_FR.get(so_meta.get("status"), so_meta.get("status")),
        "status_raw": so_meta.get("status"),
        "intervention": intervention,
        "tache_reference": task.get("name") if task else None,
        "tache_employee": task.get("custom_employé") if task else None,
        "tache_status": tache_status,
        "task_open": task_open,
        "total_paid": total_paid,
        "alert": alert,
        "is_validated": is_validated,
        "warnings": warnings,
        "payments": [
            {
                "name": p.get("name"),
                "date": str(p.get("posting_date") or ""),
                "mode": p.get("mode_of_payment") or "",
                "amount": flt(p.get("paid_amount")),
                "reference_no": p.get("reference_no") or "",
                "link_type": p.get("link_type"),
                "creation_date": str(p.get("creation_date") or ""),
                "hors_periode": _hors_periode(p, d1, d2),
                "antidate": _antidate(p, d1, d2),
            }
            for p in pes
        ],
        "avance_anterieure": {
            "total": flt(sum(flt(p.get("paid_amount")) for p in pes
                             if _hors_periode(p, d1, d2)), 3),
            "modes": sorted({(p.get("mode_of_payment") or "?") for p in pes
                             if _hors_periode(p, d1, d2)}),
        },
        "is_aramex": is_aramex,
        "delivery_notes": [{"name": d.name, "grand_total": flt(d.grand_total),
                            "docstatus": d.docstatus} for d in dns],
        "sales_invoices": [{"name": s.name, "grand_total": flt(s.grand_total)} for s in sis],
    }


# Comptes de caisse surveillés pour les paiements « hors périmètre »
# (règlements d'anciennes commandes saisis pendant la période de vérification).
# « Chèques - % » exclut volontairement « Chèques sans provision - … » (impayés).
CAISSE_ACCOUNTS_LIKE = ("Espèces%", "Chèques - %", "Traite Bancaire%")


#: Un paiement est « né de la validation » s'il a été créé à moins de ce délai de la validation de sa commande.
DELAI_VALIDATION_S = 120


def caisse_du_paiement(auteur, technicien) -> str:
    """Dans quelle caisse compte un paiement encaissé hors des commandes du jour. Fonction pure.
    `auteur` : le nom de celui qui a saisi le paiement ; `technicien` : {caisse, tache} quand le paiement est né de la
    validation, par un compte SANS caisse, d'une commande liée à une tâche — None sinon.
    Règle générale : l'argent est chez celui qui a saisi le paiement (une dette réglée au magasin, une avance prise à
    la commande pour une intervention à venir). Exception (27/11/2026) : la commande validée par le système — le
    bouton « ✅ Valider » de la fenêtre de clôture d'une tâche valide sous « Administrator », qui n'est la caisse de
    personne. L'argent a été encaissé chez le client par le technicien de la tâche : il va dans SA caisse."""
    return (technicien or {}).get("caisse") or auteur


def ne_de_la_validation(cree_le, valide_le, delai=DELAI_VALIDATION_S) -> bool:
    """Le paiement a-t-il été créé au moment où sa commande a été validée ? Fonction pure (deux datetimes)."""
    if not cree_le or not valide_le:
        return False
    return abs((cree_le - valide_le).total_seconds()) < delai


def _techniciens_des_paiements_systeme(pes, owner_names, refs_by_pe) -> dict:
    """{paiement: {caisse, tache}} pour les paiements créés par un compte sans caisse, au moment de la validation d'une
    commande liée à une tâche. Ne regarde que ceux-là : presque toujours aucun (un seul cas du 05/10 au 27/11/2026)."""
    from customization_app import caisse_collecte as CC

    out = {}
    for p in pes:
        auteur = owner_names.get(p.owner) or p.owner
        if auteur not in CC.CAISSES_TECHNIQUES and p.owner not in CC.CAISSES_TECHNIQUES:
            continue
        commande = next((r["name"] for r in refs_by_pe.get(p.name, []) if r["doctype"] == "Sales Order"), None)
        if not commande:
            continue
        valide_le = frappe.db.sql(
            """SELECT MIN(creation) FROM `tabVersion` WHERE ref_doctype = 'Sales Order' AND docname = %s
               AND data LIKE %s""", (commande, '%"docstatus",0,1%'))[0][0]
        cree_le = frappe.db.get_value("Payment Entry", p.name, "creation")
        if not ne_de_la_validation(cree_le, valide_le):
            continue
        # La tâche de la commande : la dernière commencée au plus tard le jour du paiement, sinon la première.
        taches = frappe.db.sql(
            """SELECT t.name, e.employee_name, DATE(t.starts_on) AS jour FROM `tabTache de travail` t
               JOIN `tabEmployee` e ON e.name = t.custom_choix_du_staff
               WHERE t.commande_client = %s AND t.docstatus < 2 AND IFNULL(t.status, '') != 'Cancelled'
               ORDER BY t.starts_on""", commande, as_dict=True)
        if not taches:
            continue
        passees = [t for t in taches if t.jour and str(t.jour) <= str(p.posting_date)]
        t = (passees or taches)[-1 if passees else 0]
        if t.employee_name and t.employee_name not in CC.CAISSES_TECHNIQUES:
            out[p.name] = {"caisse": t.employee_name, "tache": t.name}
    return out


def _paiements_anciennes_commandes(d1, d2, exclude_names):
    """Payment Entries validés encaissés sur un compte de caisse pendant la
    période (date comptable OU date de création — attrape les saisies
    antidatées), et qui n'appartiennent à aucune commande du rapport :
    ce sont des règlements d'anciennes commandes à tracer dans la caisse.
    Un paiement RÉÉMIS (ACC-PAY-…-1, créé par l'annulation d'une facture qui
    réémet ses paiements) n'est pas une saisie antidatée : l'argent est entré
    avec l'original — c'est la date de création de l'original qui compte
    (SCPS.Tn, 61,301 DT du 03/08 réapparu dans la caisse du 08/10/2026)."""
    like_conds = " OR ".join(["pe.paid_to LIKE %s"] * len(CAISSE_ACCOUNTS_LIKE))
    vals = [d1, d2, "%s 00:00:00" % d1, d1, d2, d1, d2, *CAISSE_ACCOUNTS_LIKE]
    exclude_sql = ""
    if exclude_names:
        exclude_sql = "AND pe.name NOT IN ({})".format(",".join(["%s"] * len(exclude_names)))
        vals += list(exclude_names)

    pes = frappe.db.sql(
        f"""SELECT pe.name, pe.posting_date,
                   DATE(COALESCE(racine.creation, pe.creation)) AS creation_date,
                   pe.owner, COALESCE(origine.party, pe.party) AS party,
                   COALESCE(origine.party_name, pe.party_name) AS party_name,
                   pe.mode_of_payment, origine.name AS origine_impaye,
                   pe.paid_to, pe.paid_amount, pe.reference_no,
                   IFNULL(pe.custom_exclu_caisse, 0) AS exclu
            FROM `tabPayment Entry` pe
            LEFT JOIN `tabPayment Entry` origine
              ON pe.payment_type = 'Internal Transfer'
             AND pe.paid_from = 'Chèques sans provision - A&S'
             AND (origine.name = pe.custom_impaye_origine
                  OR (pe.paid_to = 'Espèces - A&S' AND origine.name = pe.reference_no))
             AND origine.docstatus = 1
             AND origine.party_type = 'Customer'
            LEFT JOIN `tabPayment Entry` racine
              ON pe.amended_from IS NOT NULL
             AND racine.name = SUBSTRING_INDEX(pe.name, '-', 4)
            WHERE pe.docstatus = 1
              AND (pe.payment_type = 'Receive' OR origine.name IS NOT NULL)
              -- Filtre que les index peuvent servir (25/11/2026 : 0,7 s par appel sans lui, toute la table parcourue
              -- avec une jointure calculée) — impliqué par le suivant : la création d'un paiement réémis suit celle
              -- de son original, donc « création de la racine ≥ d1 » donne « création ≥ d1 ».
              AND (pe.posting_date BETWEEN %s AND %s OR pe.creation >= %s)
              AND (pe.posting_date BETWEEN %s AND %s
                   OR DATE(COALESCE(racine.creation, pe.creation)) BETWEEN %s AND %s)
              AND ({like_conds})
              {exclude_sql}
            ORDER BY pe.posting_date, pe.name""",
        tuple(vals), as_dict=True,
    )
    if not pes:
        return {"paiements": [], "par_mode": {}, "total": 0.0}

    # pièces référencées (commandes / factures) avec leur date d'origine
    names = [p.name for p in pes]
    ph = ",".join(["%s"] * len(names))
    refs = frappe.db.sql(
        f"""SELECT per.parent, per.reference_doctype, per.reference_name,
                   so.transaction_date AS so_date, si.posting_date AS si_date
            FROM `tabPayment Entry Reference` per
            LEFT JOIN `tabSales Order` so
                   ON per.reference_doctype = 'Sales Order' AND so.name = per.reference_name
            LEFT JOIN `tabSales Invoice` si
                   ON per.reference_doctype = 'Sales Invoice' AND si.name = per.reference_name
            WHERE per.parent IN ({ph})""",
        tuple(names), as_dict=True,
    )
    refs_by_pe = {}
    for r in refs:
        refs_by_pe.setdefault(r.parent, []).append({
            "doctype": r.reference_doctype,
            "name": r.reference_name,
            "date": str(r.so_date or r.si_date or ""),
        })

    # qui a saisi le paiement (nom lisible)
    owners = {p.owner for p in pes}
    owner_names = {u.name: u.full_name for u in frappe.get_all(
        "User", filters={"name": ("in", list(owners))}, fields=["name", "full_name"])}
    # VALIDÉ PAR LE SYSTÈME (27/11/2026) : le paiement né de la validation d'une commande sous un compte sans caisse
    # (« ✅ Valider » de la fenêtre de clôture d'une tâche) va dans la caisse du technicien de la tâche.
    techniciens = _techniciens_des_paiements_systeme(pes, owner_names, refs_by_pe)

    paiements, par_mode = [], {}
    for p in pes:
        # Une CORRECTION DE SAISIE exclue par l'utilisateur reste visible (et
        # réintégrable) mais ne pèse dans aucun total.
        if not p.exclu:
            par_mode[p.mode_of_payment or "—"] = flt(par_mode.get(p.mode_of_payment or "—", 0)) \
                + flt(p.paid_amount)
        paiements.append({
            "exclu": bool(p.exclu),
            "name": p.name,
            "date": str(p.posting_date or ""),
            "creation_date": str(p.creation_date or ""),
            "antidate": bool(p.posting_date and p.creation_date
                             and str(p.posting_date) != str(p.creation_date)),
            "saisi_par": caisse_du_paiement(owner_names.get(p.owner) or p.owner, techniciens.get(p.name)),
            # Qui l'a réellement créé, et la tâche qui le range chez son technicien (None : rangé chez l'auteur).
            "saisi_par_reel": owner_names.get(p.owner) or p.owner,
            "tache_technicien": (techniciens.get(p.name) or {}).get("tache"),
            "customer": p.party,
            "customer_name": p.party_name or p.party,
            "mode": p.mode_of_payment or "",
            "compte": p.paid_to,
            "amount": flt(p.paid_amount),
            "reference_no": p.reference_no or "",
            "pieces": refs_by_pe.get(p.name, []) + (
                [{"doctype": "Payment Entry", "name": p.origine_impaye, "date": ""}]
                if p.origine_impaye else []),
        })
    return {
        "paiements": paiements,
        "par_mode": {m: flt(v) for m, v in par_mode.items()},
        "total": flt(sum(x["amount"] for x in paiements if not x["exclu"])),
    }


COMPTE_DECOUVERT = "Compte de découvert bancaire - A&S"


def _mode_depense(compte_credit, remarque, credit):
    """Le mode d'une ligne de crédit d'une écriture de caisse, ou "" si la ligne n'est pas
    un règlement (part « pas payé » sur le découvert). Fonction pure."""
    remarque = remarque or ""
    if compte_credit == "Espèces - A&S":
        return "Espèces"
    if compte_credit == COMPTE_DECOUVERT:
        from customization_app.caisse_depenses import parser_traites

        for t in parser_traites(remarque):
            if abs(round(float(t["montant"] or 0), 3) - round(float(credit or 0), 3)) <= 0.001:
                return "Traite bancaire"
        return ""
    if "Chq N°" in remarque:
        return "Chèque"
    if "virement" in remarque.lower():
        return "Virement"
    return "Carte de crédit"


def _payeur_de_remarque(remarque):
    from customization_app.caisse_depenses import payeur_de_remarque
    return payeur_de_remarque(remarque)


def _depenses_caisse(d1, d2, noms_par_user):
    """Les dépenses saisies en caisse (écritures « Dépense caisse — … ») sur la
    période, attribuées à leur AUTEUR. Le mode se lit sur l'écriture elle-même
    (`_mode_depense`) : crédit Espèces -> Espèces ; crédit découvert -> Traite
    bancaire (seulement si la remarque porte « Traite N° … » du même montant, sinon
    c'est une part « pas payé » qui ne sort de rien) ; « Chq N° » -> Chèque ;
    « virement » -> Virement ; sinon carte."""
    # UNE LIGNE PAR CRÉDIT de l'écriture — un paiement fractionné (espèces +
    # chèque) doit compter sa part espèces dans le solde théorique, pas le TTC.
    # Le découvert entre dans la lecture pour les TRAITES (16/09/2026) : la ligne
    # est ensuite retenue ou écartée par `_mode_depense`.
    rows = frappe.db.sql(
        """SELECT je.name, je.posting_date, je.owner, je.cheque_no, je.user_remark,
                  jea.account AS compte_credit,
                  jea.credit_in_account_currency AS credit
           FROM `tabJournal Entry` je
           INNER JOIN `tabJournal Entry Account` jea
                   ON jea.parent = je.name AND jea.credit_in_account_currency > 0
           WHERE je.docstatus = 1 AND je.posting_date BETWEEN %s AND %s
             AND je.cheque_no LIKE 'Dépense caisse —%%'
           ORDER BY je.posting_date DESC, je.creation DESC, jea.idx""",
        (d1, d2), as_dict=True)
    rows = [r for r in rows if _mode_depense(r.compte_credit, r.user_remark, r.credit)]
    lignes = []
    nb_credits = {}
    for r in rows:
        nb_credits[r.name] = nb_credits.get(r.name, 0) + 1
    for r in rows:
        remark = r.user_remark or ""
        mode = _mode_depense(r.compte_credit, remark, r.credit)
        m = re.search(r"Type : (.+)", remark)
        description = (r.cheque_no or "").replace("Dépense caisse — ", "")
        if nb_credits.get(r.name, 1) > 1:
            description += " (part %s)" % mode.lower()
        saisi_par = noms_par_user.get(r.owner, r.owner)
        # « Payée de la caisse de : … » (24/11/2026) : la dépense compte dans la caisse du PAYEUR.
        payeur = _payeur_de_remarque(remark)
        lignes.append({
            "name": r.name,
            "doctype": "Journal Entry",
            "date": str(r.posting_date),
            "saisi_par": saisi_par,
            "caisse": payeur or saisi_par,
            "payee_par_collegue": bool(payeur and payeur != saisi_par),
            "type": (m.group(1).strip() if m else ""),
            "description": description,
            "mode": mode,
            "montant": flt(r.credit, 3),
        })

    # ⚠️ ET LES PAIEMENTS QUI ONT REMPLACÉ UNE ÉCRITURE DE CAISSE. Quand le
    # comptable valide une facture d'achat capturée en caisse, l'écriture
    # « Dépense caisse — … » est détruite au profit d'un Payment Entry qui porte
    # la référence de la facture (cf. `caisse_depenses`). Sans cette seconde
    # lecture, la dépense DISPARAISSAIT du rapport du jour où elle a été payée,
    # et le solde théorique remontait de son montant. On ne prend que les
    # paiements NÉS DE LA CAISSE — ceux qu'une fiche « Facture Achat a Saisir »
    # désigne — jamais les règlements fournisseurs saisis ailleurs, qui
    # réécriraient des années d'historique.
    paiements = frappe.db.sql(
        """SELECT pe.name, pe.posting_date, pe.owner, pe.paid_amount, pe.paid_from,
                  pe.mode_of_payment, pe.remarks, pe.reference_no, pe.party,
                  f.description AS fiche_description, MAX(f.caisse_payeur) AS caisse_payeur,
                  MAX(f.saisi_par) AS fiche_saisi_par
           FROM `tabPayment Entry` pe
           INNER JOIN `tabFacture Achat a Saisir` f
                   ON f.payment_entry = pe.name
                   OR (f.payment_entries IS NOT NULL
                       AND f.payment_entries LIKE CONCAT('%%"', pe.name, '"%%'))
           WHERE pe.docstatus = 1 AND pe.payment_type = 'Pay'
             AND pe.posting_date BETWEEN %s AND %s
           GROUP BY pe.name
           ORDER BY pe.posting_date DESC, pe.creation DESC""",
        (d1, d2), as_dict=True)
    for r in paiements:
        if r.paid_from == "Espèces - A&S":
            mode = "Espèces"
        elif (r.mode_of_payment or "") in ("Chèque", "Carte de crédit"):
            mode = r.mode_of_payment
        else:
            mode = r.mode_of_payment or "Virement"
        # La caisse de CELUI QUI A SAISI la fiche, pas de celui qui a validé la facture (26/11/2026 : la facture SCPS
        # payée par chèque par Sadok passait dans la caisse du comptable à la validation — 4 paiements sur 17 en prod).
        auteur = r.fiche_saisi_par or r.owner
        saisi_par = noms_par_user.get(auteur, auteur)
        lignes.append({
            "name": r.name,
            "doctype": "Payment Entry",
            "date": str(r.posting_date),
            "saisi_par": saisi_par,
            "caisse": r.caisse_payeur or saisi_par,
            "payee_par_collegue": bool(r.caisse_payeur and r.caisse_payeur != saisi_par),
            "type": "Facture d'achat",
            "description": (r.fiche_description or r.remarks or r.reference_no
                            or r.party or ""),
            "mode": mode,
            "montant": flt(r.paid_amount, 3),
        })
    # ⚠️ ET LES RÈGLEMENTS DE FACTURES FOURNISSEURS FAITS DEPUIS LA CAISSE
    # (« 💸 Factures à payer » -> « Payer », cf. `caisse_depenses.payer_factures`).
    # Ils ne naissent d'aucune fiche de caisse : c'est le drapeau
    # `custom_reglement_caisse` posé à la création qui les désigne — jamais les
    # règlements saisis ailleurs. Leur part ESPÈCES sort du tiroir et doit peser
    # sur le solde théorique de la clôture.
    #
    # ⚠️ La colonne naît d'un patch (`ensure_reglement_caisse_field`) : entre le
    # déploiement du code et le `bench migrate`, elle n'existe pas encore et la
    # requête ferait tomber TOUT le rapport de caisse. On regarde avant de lire.
    deja = {l["name"] for l in lignes}
    reglements = []
    if frappe.db.has_column("Payment Entry", "custom_reglement_caisse"):
        reglements = frappe.db.sql(
            """SELECT pe.name, pe.posting_date, pe.owner, pe.paid_amount, pe.paid_from,
                      pe.mode_of_payment, pe.remarks, pe.reference_no, pe.party
               FROM `tabPayment Entry` pe
               WHERE pe.docstatus = 1 AND pe.payment_type = 'Pay'
                 AND IFNULL(pe.custom_reglement_caisse, 0) = 1
                 AND pe.posting_date BETWEEN %s AND %s
               ORDER BY pe.posting_date DESC, pe.creation DESC""",
            (d1, d2), as_dict=True)
    for r in reglements:
        if r.name in deja:
            continue
        if r.paid_from == "Espèces - A&S":
            mode = "Espèces"
        else:
            mode = r.mode_of_payment or "Virement"
        lignes.append({
            "name": r.name,
            "doctype": "Payment Entry",
            "date": str(r.posting_date),
            "saisi_par": noms_par_user.get(r.owner, r.owner),
            "type": TYPE_REGLEMENT_FOURNISSEUR,
            "description": (r.party or "") + (
                " — %s" % r.reference_no if r.reference_no else ""),
            "mode": mode,
            "montant": flt(r.paid_amount, 3),
        })

    # ⚠️ ET LES PAIEMENTS FAITS SUR UNE COMMANDE D'ACHAT (ticket #16). Depuis le
    # formulaire d'une commande fournisseur (« Créer > Paiement »), l'employé
    # sort l'argent du tiroir sans passer par la caisse : le paiement n'a ni
    # fiche « Facture Achat a Saisir » ni drapeau `custom_reglement_caisse`, et
    # la dépense manquait au rapport comme au solde théorique de la clôture.
    # C'est sa RÉFÉRENCE à une commande d'achat qui le désigne. Les avances nées
    # de la caisse (`caisse_depenses.po_convertir_avances`) portent déjà une
    # fiche : `deja` les garde à UNE seule ligne.
    deja = {l["name"] for l in lignes}
    avances = frappe.db.sql(
        """SELECT pe.name, pe.posting_date, pe.owner, pe.paid_amount, pe.paid_from,
                  pe.mode_of_payment, pe.reference_no, pe.party, pe.party_name,
                  GROUP_CONCAT(DISTINCT per.reference_name
                               ORDER BY per.reference_name SEPARATOR ', ') AS commandes
           FROM `tabPayment Entry` pe
           INNER JOIN `tabPayment Entry Reference` per
                   ON per.parent = pe.name
                  AND per.reference_doctype = 'Purchase Order'
           WHERE pe.docstatus = 1 AND pe.payment_type = 'Pay'
             AND pe.posting_date BETWEEN %s AND %s
           GROUP BY pe.name
           ORDER BY pe.posting_date DESC, pe.creation DESC""",
        (d1, d2), as_dict=True)
    for r in avances:
        if r.name in deja:
            continue
        deja.add(r.name)
        if r.paid_from == "Espèces - A&S":
            mode = "Espèces"
        else:
            mode = r.mode_of_payment or "Virement"
        description = " — ".join(x for x in (r.party_name or r.party,
                                             r.commandes, r.reference_no) if x)
        lignes.append({
            "name": r.name,
            "doctype": "Payment Entry",
            "date": str(r.posting_date),
            "saisi_par": noms_par_user.get(r.owner, r.owner),
            "type": TYPE_AVANCE_COMMANDE,
            "description": description,
            "mode": mode,
            "montant": flt(r.paid_amount, 3),
        })

    # la PIÈCE JOINTE de chaque écriture/paiement (le scan de la caisse) — une
    # colonne d'aperçu dans le tableau, cliquable sans ouvrir le document.
    noms = list({l["name"] for l in lignes})
    piece = {}
    if noms:
        for f in frappe.get_all(
                "File",
                filters={"attached_to_doctype": ["in", ["Journal Entry", "Payment Entry"]],
                         "attached_to_name": ["in", noms]},
                fields=["attached_to_name", "file_url"],
                order_by="creation"):
            piece.setdefault(f.attached_to_name, f.file_url)
    for l in lignes:
        l["piece"] = piece.get(l["name"])

    lignes.sort(key=lambda l: (l["date"], l["name"]), reverse=True)
    return lignes


def selection_caisses(employe, employes) -> set:
    """Les caisses à montrer. PURE. `employes` : la sélection multiple de la page (liste, ou JSON d'une liste) ; vide →
    `employe` seul ; rien du tout → toutes (ensemble vide)."""
    import json

    if isinstance(employes, str):
        employes = json.loads(employes) if employes.strip() else []
    noms = {str(n).strip() for n in employes or [] if str(n or "").strip()}
    if noms:
        return noms
    employe = (employe or "").strip()
    return {employe} if employe else set()


@frappe.whitelist()
def get_data(d1, d2, employe=None, employes=None):
    """Renvoie le rapport de caisse journalière groupé par employé, pour [d1, d2].

    `employe` (nom affiché) restreint le rapport à UNE caisse : chaque employé peut
    traiter la sienne, la supervision garde « Tous ». La liste complète des noms est
    toujours retournée (`employes`) pour alimenter la liste déroulante du filtre.

    `employes` (26/11/2026, sélection multiple de la page) : plusieurs caisses à la fois — JSON d'une liste de noms.
    Avec une seule, c'est `employe`. Une vue restreinte (un employé : sa caisse seulement) l'ignore.
    """
    choix_multiple = selection_caisses(None, employes)
    if len(choix_multiple) == 1:
        employe = next(iter(choix_multiple))
        choix_multiple = set()
    start_date, end_date = d1, d2
    # `employe` absent (premier chargement) -> la caisse de l'utilisateur CONNECTÉ ;
    # chaîne vide -> « Tous les employés » choisi explicitement.
    defaut_demande = employe is None
    employe = (employe or "").strip()

    employees = frappe.db.sql(
        """SELECT e.name AS employee_id, e.employee_name, e.user_id AS user_email, e.status, e.relieving_date
           FROM `tabEmployee` e WHERE e.company = %s""",
        COMPANY, as_dict=True,
    )
    users = frappe.get_all(
        "User",
        filters={"enabled": 1, "user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]},
        fields=["name", "full_name"],
    )
    modes = [m.name for m in frappe.get_all("Mode of Payment", fields=["name"], order_by="name")]

    seen_so = set()
    processed_users = set()
    result_employees = []
    grand_par_mode = {}

    def process(display_name, employee_id, user_id):
        tasks = _taches(employee_id, start_date, end_date) if employee_id else []
        tasks_by_so = {}
        for t in tasks:
            if t.get("commande_client"):
                tasks_by_so.setdefault(t["commande_client"], t)

        unbilled = _unbilled_sos(user_id, start_date, end_date)
        so_names = list(tasks_by_so.keys()) + [
            so["name"] for so in unbilled if so["name"] not in tasks_by_so
        ]

        orders = []
        totaux = {}
        for so_name in so_names:
            if so_name in seen_so:
                continue
            seen_so.add(so_name)
            meta = frappe.db.get_value(
                "Sales Order", so_name,
                ["customer", "transaction_date", "grand_total", "status", "payment_terms_template", "docstatus"],
                as_dict=True,
            )
            if not meta:
                continue
            order = _build_order(so_name, tasks_by_so.get(so_name), meta,
                                 d1=start_date, d2=end_date)
            orders.append(order)
            for p in order["payments"]:
                # L'avance reçue AVANT la période ne compte pas dans la caisse du jour.
                if p.get("hors_periode"):
                    continue
                totaux[p["mode"]] = flt(totaux.get(p["mode"], 0)) + flt(p["amount"])

        if not orders:
            return

        for m, v in totaux.items():
            grand_par_mode[m] = flt(grand_par_mode.get(m, 0)) + flt(v)

        # tri : commandes en alerte / tâche open d'abord, puis par date décroissante
        orders.sort(key=lambda o: (0 if (o["task_open"] or o["alert"]) else 1, o["date"]), reverse=False)

        result_employees.append({
            "employe": display_name,
            "orders": orders,
            "totaux_par_mode": totaux,
            "total": flt(sum(totaux.values())),
            "nb_commandes": len(orders),
        })

    # Exclusions effectives (Economiq n'est visible que de la direction).
    exclus_u, exclus_e = _exclusions()
    employees = [e for e in employees
                 if e.employee_id not in exclus_e
                 and (e.user_email or "") not in exclus_u]
    users = [u for u in users if u.name not in exclus_u]

    if defaut_demande:
        employe = _ma_caisse(employees, users) or ""

    # Visibilité (04/10/2026) : hors responsables de collecte, délégués en période et direction,
    # chacun ne voit QUE sa caisse — ni « Tous les employés », ni celle d'un collègue.
    from customization_app.caisse_collecte import peut_voir_toutes_les_caisses
    # `caisse_vue_complete` (drapeau serveur, jamais venu du navigateur) : l'état des caisses calcule
    # l'activité de toutes les caisses puis ne rend à l'employé que la sienne.
    restreint = not peut_voir_toutes_les_caisses() and not frappe.flags.get("caisse_vue_complete")
    # Responsable sous-traitant sans autre droit : sa caisse + celles des sous-traitants (31/10/2026).
    from customization_app.caisse_collecte import perimetre_sous_traitants
    perimetre = perimetre_sous_traitants() if restreint else set()
    if restreint and employe not in perimetre:
        employe = _ma_caisse(employees, users) or "—"
    if restreint:
        choix_multiple = set()
    # Plusieurs caisses (26/11/2026) : `employe` reste vide (aucune clôture d'UNE caisse ne s'applique), le filtre de
    # restitution les garde toutes.
    if choix_multiple:
        employe = ""
    garder = (lambda nom: nom in choix_multiple) if choix_multiple else ((lambda nom: nom == employe) if employe else None)

    # La liste déroulante du filtre montre TOUT le monde, même quand on filtre.
    # Déduplication par COMPTE et non par nom : Economiq a deux fiches aux orthographes
    # différentes (« Economic … » côté Employé, « Economiq … » côté User) — un User déjà
    # porté par une fiche Employé n'entre pas une seconde fois.
    # 26/11/2026 : sans les comptes techniques (assistants du MCP), ni les employés partis avant la période affichée ;
    # la caisse en cours de consultation reste toujours dans la liste.
    noms_disponibles = noms_du_selecteur(employees, users, _avec_role_caisse(users), start_date)
    tous_noms = set(noms_du_selecteur(employees, users, {u.name for u in users}))
    for nom in ({employe} if employe and employe != "—" else set()) | choix_multiple:
        if nom not in noms_disponibles and nom in tous_noms:
            noms_disponibles = sorted(noms_disponibles + [nom])
    if restreint:
        noms_disponibles = sorted(({employe} if employe != "—" else set()) | perimetre)

    # ⚠️ ANTI-DOUBLE-COMPTAGE : on traite TOUTES les caisses, même filtré — la
    # liste d'exclusion des « anciennes commandes » doit connaître les paiements
    # des commandes de TOUS les employés. Le filtre ne joue qu'à la restitution.
    for emp in employees:
        process(emp.employee_name, emp.employee_id, emp.user_email)
        if emp.user_email:
            processed_users.add(emp.user_email)

    for u in users:
        if u.name in processed_users:
            continue
        processed_users.add(u.name)
        process(u.full_name, None, u.name)

    # paiements encaissés sur la période mais hors commandes du rapport
    # (= règlements d'anciennes commandes à tracer dans la caisse du jour)
    seen_pes = set()
    for e in result_employees:
        for o in e["orders"]:
            for p in o["payments"]:
                seen_pes.add(p["name"])
    anciens = _paiements_anciennes_commandes(start_date, end_date, seen_pes)
    # Un paiement pris par une clôture ne compte QUE dans la caisse et le jour de celle-ci ; ceux qu'une clôture de
    # la période a repris (saisis après le comptage de leur jour) y apparaissent (25/11/2026, caisse_paiements).
    from customization_app.caisse_paiements import appliquer_clotures
    appliquer_clotures(result_employees, anciens, grand_par_mode, start_date, end_date, employe or None)

    # La restitution suit le filtre ; les totaux globaux sont recalculés sur le
    # sous-ensemble (grand_par_mode a accumulé TOUTES les caisses ci-dessus).
    if garder:
        result_employees = [e for e in result_employees if garder(e["employe"])]
        grand_par_mode = {}
        for e in result_employees:
            for m_, v_ in e["totaux_par_mode"].items():
                grand_par_mode[m_] = flt(grand_par_mode.get(m_, 0)) + flt(v_)
    # Le filtre par caisse s'applique aussi aux encaissements d'anciennes commandes :
    # chacun est attribué à l'employé qui l'a SAISI.
    if garder:
        anciens["paiements"] = [p for p in anciens["paiements"]
                                if garder(p.get("saisi_par") or "—")]
        par_mode_filtre = {}
        for p in anciens["paiements"]:
            if p.get("exclu") or p.get("deja_compte"):
                continue
            m = p["mode"] or "—"
            par_mode_filtre[m] = flt(par_mode_filtre.get(m, 0)) + flt(p["amount"])
        anciens["par_mode"] = par_mode_filtre
        anciens["total"] = flt(sum(par_mode_filtre.values()))

    # Ces encaissements comptent dans le récap : chaque paiement est attribué
    # à l'employé qui l'a SAISI, sous son mode de paiement (et entre donc dans
    # les totaux par mode / total encaissé).
    anciens_par_emp = {}
    for p in anciens["paiements"]:
        if p.get("exclu") or p.get("deja_compte"):
            continue
        emp = p["saisi_par"] or "—"
        mode = p["mode"] or "—"
        anciens_par_emp.setdefault(emp, {})
        anciens_par_emp[emp][mode] = flt(anciens_par_emp[emp].get(mode, 0)) + flt(p["amount"])
        grand_par_mode[mode] = flt(grand_par_mode.get(mode, 0)) + flt(p["amount"])

    # récap : modes réellement utilisés
    used_modes = [m for m in modes if flt(grand_par_mode.get(m, 0)) != 0]
    # inclure d'éventuels modes hors liste standard
    for m in grand_par_mode:
        if m and m not in used_modes and flt(grand_par_mode[m]) != 0:
            used_modes.append(m)

    recap_par_employe = []
    seen_emp = set()
    for e in result_employees:
        extra = anciens_par_emp.get(e["employe"], {})
        par_mode = {m: flt(e["totaux_par_mode"].get(m, 0)) + flt(extra.get(m, 0))
                    for m in used_modes}
        total = flt(sum(par_mode.values()))
        seen_emp.add(e["employe"])
        if total != 0:
            recap_par_employe.append({
                "employe": e["employe"],
                "total": total,
                "par_mode": par_mode,
                "anciens": flt(sum(extra.values())),
            })
    # employés qui n'ont QUE des encaissements d'anciennes commandes
    for emp, modes_emp in anciens_par_emp.items():
        if emp in seen_emp:
            continue
        par_mode = {m: flt(modes_emp.get(m, 0)) for m in used_modes}
        recap_par_employe.append({
            "employe": emp,
            "total": flt(sum(par_mode.values())),
            "par_mode": par_mode,
            "anciens": flt(sum(par_mode.values())),
        })
    recap_par_employe.sort(key=lambda x: x["total"], reverse=True)

    # Les DÉPENSES de la caisse : attribuées à leur auteur, filtrées comme le reste.
    noms_par_user = {e.user_email: e.employee_name for e in employees if e.user_email}
    for u in users:
        noms_par_user.setdefault(u.name, u.full_name)
    depenses = _depenses_caisse(start_date, end_date, noms_par_user)
    if garder:
        # La caisse d'une dépense : celle du PAYEUR quand un collègue a payé (24/11/2026), sinon celle de la saisie.
        depenses = [l for l in depenses if garder(l.get("caisse") or l["saisi_par"])]
    depenses_par_mode = {}
    for l in depenses:
        depenses_par_mode[l["mode"]] = flt(depenses_par_mode.get(l["mode"], 0)) + l["montant"]

    return {
        "periode": {"d1": start_date, "d2": end_date},
        "employe": employe or None,
        "employes": noms_disponibles,
        "employes_choisis": sorted(choix_multiple) if choix_multiple else ([employe] if employe and employe != "—" else []),
        "restreint": restreint,
        "anciens": anciens,
        "depenses": {"lignes": depenses,
                     "total": flt(sum(l["montant"] for l in depenses)),
                     "par_mode": depenses_par_mode},
        "modes": used_modes,
        "employees": result_employees,
        "recap": {
            "modes": used_modes,
            "par_employe": recap_par_employe,
            "par_mode": {m: flt(grand_par_mode.get(m, 0)) for m in used_modes},
            "grand_total": flt(sum(grand_par_mode.values())),
        },
    }


@frappe.whitelist()
def exclure_ancien_paiement(pe, exclure):
    """Exclut (ou réintègre) un paiement d'ancienne commande de la caisse —
    typiquement une CORRECTION DE SAISIE qui n'est pas de l'argent entré ce
    jour. Le drapeau vit sur la Payment Entry (custom_exclu_caisse), la
    décision est tracée en commentaire sur la pièce."""
    frappe.only_for(("System Manager", "Accounts Manager", "Accounts User",
                     "Sales Manager", "Sales User"))
    exclure = 1 if frappe.utils.cint(exclure) else 0
    frappe.db.set_value("Payment Entry", pe, "custom_exclu_caisse", exclure,
                        update_modified=False)
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Payment Entry", "reference_name": pe,
        "content": ("Exclu de la caisse journalière par {0}"
                    if exclure else "Réintégré à la caisse journalière par {0}")
                   .format(frappe.session.user),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"pe": pe, "exclu": exclure}
