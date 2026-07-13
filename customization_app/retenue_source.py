"""
Backend du rapport Retenue à la source — Ventes (Page custom).

Porte la logique de l'ancien Script Report "Rapport Retenue à la source vente"
vers une API whitelisted qui renvoie du JSON structuré, rendu par une Page Desk
(customize_erpnext/page/retenue_source_vente). Toutes les règles métier vivent
ici : le front ne fait qu'afficher.

Règles métier :
- Factures de vente validées dont la date de comptabilisation est dans la
  période, réglées (en tout ou partie) par une écriture de paiement validée
  en mode RAS_MODE ("Retenue a la source vente").
- Montant de retenue = somme des montants alloués de ces écritures (chaque
  allocation compte, même si deux retenues ont le même montant — l'ancien
  rapport les fusionnait via GROUP BY).
- Taux de retenue = retenue / total TTC de la facture.
- Justificatifs = fichiers attachés à la facture ET aux écritures de
  paiement concernées (l'ancien rapport ignorait ces dernières).
- Relance des certificats manquants (même mécanique que Relance Paiements :
  SMS via Compagne SMS, email via frappe.sendmail, téléphone tracé à la main ;
  chaque relance laisse un commentaire marqué sur la fiche client).
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

RAS_MODE = "Retenue a la source vente"
PRECISION = 3

# Marqueur des commentaires créés par cette UI (distinct de [RELANCE-PAIEMENT]).
RAS_RELANCE_MARKER = "[RELANCE-RAS]"


def _invoice_rows(start, end):
    return frappe.db.sql(
        """SELECT si.name, si.customer, si.posting_date, si.grand_total, si.status,
                  c.customer_name, c.tax_id,
                  per.allocated_amount, pe.name AS payment_entry,
                  pe.posting_date AS pe_date, pe.reference_no, pe.reference_date
           FROM `tabSales Invoice` si
           INNER JOIN `tabPayment Entry Reference` per
                   ON per.reference_doctype = 'Sales Invoice'
                  AND per.reference_name = si.name
           INNER JOIN `tabPayment Entry` pe
                   ON pe.name = per.parent AND pe.docstatus = 1
           LEFT JOIN `tabCustomer` c ON c.name = si.customer
           WHERE si.docstatus = 1
             AND si.posting_date BETWEEN %s AND %s
             AND pe.mode_of_payment = %s
           ORDER BY si.posting_date DESC, si.name, pe.posting_date, pe.name""",
        (start, end, RAS_MODE), as_dict=True,
    )


def _attachments(si_names, pe_names):
    """Fichiers attachés aux factures et aux écritures de paiement."""
    out = {}
    for doctype, names in (("Sales Invoice", si_names), ("Payment Entry", pe_names)):
        if not names:
            continue
        ph = ",".join(["%s"] * len(names))
        rows = frappe.db.sql(
            f"""SELECT attached_to_name, file_name, file_url, is_private
                FROM `tabFile`
                WHERE attached_to_doctype = %s AND attached_to_name IN ({ph})
                  AND IFNULL(file_url, '') != ''
                ORDER BY creation""",
            (doctype, *names), as_dict=True,
        )
        for r in rows:
            ext = (r.file_name or r.file_url or "").rsplit(".", 1)
            out.setdefault((doctype, r.attached_to_name), []).append({
                "file_name": r.file_name or r.file_url.rsplit("/", 1)[-1],
                "file_url": r.file_url,
                "is_private": r.is_private,
                "extension": ext[1].lower() if len(ext) == 2 else "",
            })
    return out


@frappe.whitelist()
def get_data(from_date=None, to_date=None):
    if not frappe.has_permission("Sales Invoice", "read"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)

    # Période par défaut : l'année civile en cours complète.
    today = getdate(nowdate())
    start = getdate(from_date) if from_date else getdate(f"{today.year}-01-01")
    end = getdate(to_date) if to_date else getdate(f"{today.year}-12-31")

    rows = _invoice_rows(start, end)

    invoices = {}
    order = []
    for r in rows:
        inv = invoices.get(r.name)
        if not inv:
            inv = invoices[r.name] = {
                "name": r.name,
                "customer": r.customer,
                "customer_name": r.customer_name or r.customer,
                "tax_id": r.tax_id,
                "posting_date": str(r.posting_date) if r.posting_date else None,
                "status": r.status,
                "grand_total": flt(r.grand_total, PRECISION),
                "ras_total": 0.0,
                "payments": [],
            }
            order.append(r.name)
        amount = flt(r.allocated_amount, PRECISION)
        inv["ras_total"] = flt(inv["ras_total"] + amount, PRECISION)
        inv["payments"].append({
            "payment_entry": r.payment_entry,
            "posting_date": str(r.pe_date) if r.pe_date else None,
            "amount": amount,
            "reference_no": r.reference_no,
            "reference_date": str(r.reference_date) if r.reference_date else None,
        })

    files = _attachments(
        list(invoices),
        list({p["payment_entry"] for inv in invoices.values() for p in inv["payments"]}),
    )

    result = []
    for name in order:
        inv = invoices[name]
        attachments = [
            {**f, "source": "Facture", "source_name": name}
            for f in files.get(("Sales Invoice", name), [])
        ]
        for p in inv["payments"]:
            attachments.extend(
                {**f, "source": "Paiement", "source_name": p["payment_entry"]}
                for f in files.get(("Payment Entry", p["payment_entry"]), [])
            )
        inv["attachments"] = attachments
        inv["ras_rate"] = flt(inv["ras_total"] / inv["grand_total"] * 100, 2) if inv["grand_total"] else 0.0
        result.append(inv)

    total_invoiced = flt(sum(i["grand_total"] for i in result), PRECISION)
    total_ras = flt(sum(i["ras_total"] for i in result), PRECISION)
    missing = [i for i in result if not i["attachments"]]

    return {
        "period": {"from": str(start), "to": str(end)},
        "currency": frappe.defaults.get_global_default("currency") or "TND",
        "kpis": {
            "invoice_count": len(result),
            "total_invoiced": total_invoiced,
            "total_ras": total_ras,
            "avg_rate": flt(total_ras / total_invoiced * 100, 2) if total_invoiced else 0.0,
            "missing_proof": len(missing),
            "missing_proof_amount": flt(sum(i["ras_total"] for i in missing), PRECISION),
        },
        "invoices": result,
    }


# ---------------------------------------------------------------- relances

def _log_ras_comment(customer, type_relance, note=""):
    """Trace une relance RAS en commentaire sur la fiche client.
    Format : [RELANCE-RAS] | <type> | <note> (même mécanique que Relance Paiements)."""
    if not customer or not frappe.db.exists("Customer", customer):
        return
    cmt = frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Comment",
        "reference_doctype": "Customer",
        "reference_name": customer,
        "content": f"{RAS_RELANCE_MARKER} | {type_relance} | {(note or '').strip()}",
    })
    cmt.insert(ignore_permissions=True)
    return cmt.name


def _fmt_tnd(val):
    try:
        return f"{float(val):,.3f}".replace(",", " ").replace(".", ",")
    except Exception:
        return str(val)


def _fmt_date(val):
    try:
        return getdate(val).strftime("%d/%m/%Y")
    except Exception:
        return str(val or "")


def _build_ras_message(customer_name, invoices):
    """Message de relance : certificats de retenue manquants pour ces factures."""
    from customization_app.api import _fac_display_name

    lignes = []
    for inv in invoices:
        fac = _fac_display_name(inv.get("numero_facture"), inv["posting_date"]) or inv["name"]
        lignes.append(
            f"- {fac} du {_fmt_date(inv['posting_date'])} : "
            f"retenue {_fmt_tnd(inv['ras_total'])} TND"
        )
    if len(invoices) > 1:
        intro = "les certificats de retenue à la source pour les factures suivantes"
        outro = "Merci de bien vouloir nous les faire parvenir."
    else:
        intro = "le certificat de retenue à la source pour la facture suivante"
        outro = "Merci de bien vouloir nous le faire parvenir."
    return (
        f"Bonjour {customer_name}, sauf erreur de notre part, nous n'avons pas "
        f"encore reçu {intro} :\n" + "\n".join(lignes) + "\n"
        f"{outro} AquaWorld & Servicing."
    )


@frappe.whitelist()
def preparer_relances(from_date=None, to_date=None, customers=None):
    """Prépare les messages de relance pour les factures SANS justificatif de la
    période, groupées par client. `customers` (JSON list) limite aux clients donnés."""
    if isinstance(customers, str):
        customers = json.loads(customers) if customers.strip() else None

    data = get_data(from_date, to_date)
    missing = [i for i in data["invoices"] if not i["attachments"]]
    if customers:
        missing = [i for i in missing if i["customer"] in set(customers)]
    if not missing:
        return []

    numeros = frappe.get_all(
        "Sales Invoice",
        filters={"name": ("in", [i["name"] for i in missing])},
        fields=["name", "custom_numero_facture"],
    )
    numero_map = {r.name: r.custom_numero_facture for r in numeros}

    contacts = {
        r.name: r for r in frappe.get_all(
            "Customer",
            filters={"name": ("in", list({i["customer"] for i in missing}))},
            fields=["name", "customer_name", "custom_liste_telephone", "email_id"],
        )
    }

    by_customer = {}
    for inv in missing:
        by_customer.setdefault(inv["customer"], []).append({
            "name": inv["name"],
            "numero_facture": numero_map.get(inv["name"]),
            "posting_date": inv["posting_date"],
            "ras_total": inv["ras_total"],
        })

    out = []
    for customer, invs in by_customer.items():
        c = contacts.get(customer)
        customer_name = (c.customer_name if c else None) or customer
        out.append({
            "customer": customer,
            "customer_name": customer_name,
            "telephone": (c.custom_liste_telephone if c else "") or "",
            "email": (c.email_id if c else "") or "",
            "invoices": [i["name"] for i in invs],
            "total_ras": flt(sum(i["ras_total"] for i in invs), PRECISION),
            "message": _build_ras_message(customer_name, invs),
        })
    out.sort(key=lambda x: x["total_ras"], reverse=True)
    return out


@frappe.whitelist()
def envoyer_relance(payload, channel="both"):
    """Envoie les relances de certificats RAS.
    `payload` : JSON list de {customer, telephone, email, message, invoices}.
    `channel` : 'sms', 'email' ou 'both'. Retourne {sent, failed}."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    channels = ["sms", "email"] if channel == "both" else [channel]
    sent, failed = [], []

    for item in payload:
        cust = item.get("customer")
        message = (item.get("message") or "").strip()
        invoices = ", ".join(item.get("invoices") or [])
        if not message:
            failed.append({"customer": cust, "reason": "Message vide"})
            continue

        for ch in channels:
            try:
                if ch == "sms":
                    phone = (item.get("telephone") or "").strip()
                    if not phone:
                        failed.append({"customer": cust, "reason": "Téléphone manquant (SMS)"})
                        continue
                    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
                        _send_sms_with_fallback,
                    )
                    _send_sms_with_fallback([phone], message)
                    sent.append({"customer": cust, "channel": "sms", "to": phone})
                    _log_ras_comment(cust, "SMS", f"Envoyé au {phone} — {invoices}")
                elif ch == "email":
                    email = (item.get("email") or "").strip()
                    if not email:
                        failed.append({"customer": cust, "reason": "Email manquant (Email)"})
                        continue
                    frappe.sendmail(
                        recipients=[email],
                        subject="Certificat de retenue à la source - AquaWorld & Servicing",
                        message=message.replace("\n", "<br>"),
                        reference_doctype="Customer",
                        reference_name=cust,
                    )
                    sent.append({"customer": cust, "channel": "email", "to": email})
                    _log_ras_comment(cust, "Email", f"Envoyé à {email} — {invoices}")
                else:
                    failed.append({"customer": cust, "reason": f"Canal inconnu: {ch}"})
            except Exception as e:
                frappe.log_error(frappe.get_traceback(), "Relance RAS: échec envoi")
                failed.append({"customer": cust, "reason": f"{ch}: {str(e)[:100]}"})

    frappe.db.commit()
    return {"sent": sent, "failed": failed}


@frappe.whitelist()
def enregistrer_relance_telephone(customer, commentaire=""):
    """Trace une relance téléphonique (certificat RAS) sur la fiche client."""
    if not customer:
        frappe.throw(_("Client manquant."))
    name = _log_ras_comment(customer, "Téléphone", commentaire)
    frappe.db.commit()
    return {"ok": True, "comment": name}


@frappe.whitelist()
def get_historique_relances(customer):
    """Historique des relances RAS de ce client (commentaires marqués),
    du plus récent au plus ancien."""
    if not customer:
        return []
    rows = frappe.db.sql(
        """SELECT name, content, creation, owner
           FROM `tabComment`
           WHERE reference_doctype = 'Customer'
             AND reference_name = %(cust)s
             AND comment_type = 'Comment'
             AND content LIKE %(marker)s
           ORDER BY creation DESC""",
        {"cust": customer, "marker": "%" + RAS_RELANCE_MARKER + "%"},
        as_dict=True,
    )
    out = []
    for r in rows:
        parts = (r.content or "").split("|", 2)
        out.append({
            "name": r.name,
            "date": frappe.utils.format_datetime(r.creation, "dd/MM/yyyy HH:mm"),
            "type": parts[1].strip() if len(parts) > 1 else "—",
            "note": parts[2].strip() if len(parts) > 2 else "",
            "user": r.owner,
        })
    return out


# ---------------------------------------------------------------- export

def _excel_rows(data):
    rows = [
        [f"Retenue à la source — Ventes",
         f"du {data['period']['from']} au {data['period']['to']}"],
        [],
        ["Facture", "Date", "Client", "Matricule fiscale", "Total TTC",
         "Retenue", "Taux %", "Écriture de paiement", "Date paiement",
         "Référence", "Montant alloué", "Justificatifs"],
    ]
    for inv in data["invoices"]:
        count = max(len(inv["payments"]), 1)
        att = " ; ".join(f"{a['file_name']} ({a['source']})" for a in inv["attachments"])
        for i in range(count):
            p = inv["payments"][i] if i < len(inv["payments"]) else None
            rows.append([
                inv["name"] if i == 0 else "",
                inv["posting_date"] if i == 0 else "",
                inv["customer_name"] if i == 0 else "",
                (inv["tax_id"] or "") if i == 0 else "",
                inv["grand_total"] if i == 0 else "",
                inv["ras_total"] if i == 0 else "",
                inv["ras_rate"] if i == 0 else "",
                p["payment_entry"] if p else "",
                (p["posting_date"] or "") if p else "",
                (p["reference_no"] or "") if p else "",
                p["amount"] if p else "",
                att if i == 0 else "",
            ])
    k = data["kpis"]
    rows.append([])
    rows.append(["Totaux", "", f"{k['invoice_count']} factures", "",
                 k["total_invoiced"], k["total_ras"], k["avg_rate"]])
    return rows


@frappe.whitelist()
def download_excel(from_date=None, to_date=None):
    from frappe.utils.xlsxutils import make_xlsx

    data = get_data(from_date, to_date)
    xlsx = make_xlsx(_excel_rows(data), "Retenue à la source")
    frappe.response["filename"] = f"retenue-source-vente-{data['period']['from']}-{data['period']['to']}.xlsx"
    frappe.response["filecontent"] = xlsx.getvalue()
    frappe.response["type"] = "binary"
