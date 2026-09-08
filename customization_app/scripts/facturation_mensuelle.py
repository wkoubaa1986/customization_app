today = frappe.form_dict.get("facturation_month_end") or frappe.utils.nowdate()
SEND_EMAILS = frappe.form_dict.get("facturation_send_emails", "1") == "1"
SEND_SMS = SEND_EMAILS and frappe.form_dict.get("facturation_send_sms", "1") == "1"
created_invoices = []
ADMIN_EMAIL = "koubaawassim@gmail.com"

disabled_items = frappe.get_all(
    "Item",
    filters={"disabled": 1},
    pluck="name"
)

for name in disabled_items:
    frappe.db.set_value("Item", name, "disabled", 0, update_modified=False)


# ----------------------------------------------------------------------
# 1) Récupérer les clients "Compte Pro" à facturer mensuellement
# ----------------------------------------------------------------------
CP_SL = frappe.db.sql("""
    SELECT dn.customer,
           GROUP_CONCAT(DISTINCT dn.name ORDER BY dn.name SEPARATOR ',') AS delivery_notes_list
    FROM `tabDelivery Note` dn
    JOIN `tabCustomer` Cust ON Cust.name=dn.customer AND Cust.disabled=0
    JOIN `tabDelivery Note Item` dni ON dni.parent=dn.name
    LEFT JOIN `tabSales Order` so ON so.name=dni.against_sales_order
    WHERE dn.docstatus=1 AND dn.status!='Completed'
      AND Cust.customer_group IN ('Technicien', 'Compte Pro', 'Quincaillerie', 'Pro Grand Rayon')
      AND Cust.custom_generation_facture_mensuelle='Oui'
      AND ((so.docstatus=1 AND so.status!='Closed' AND so.delivery_status='Fully Delivered')
           OR COALESCE(dn.custom_reconciliation_stock, '')!='')
      AND MONTH(dn.posting_date)=MONTH(%s) AND YEAR(dn.posting_date)=YEAR(%s)
      AND NOT EXISTS (
          SELECT 1 FROM `tabSales Invoice Item` sii
          JOIN `tabSales Invoice` si ON si.name=sii.parent
          WHERE sii.delivery_note=dn.name AND si.docstatus<2
      )
    GROUP BY dn.customer ORDER BY dn.customer
""", (today, today), as_dict=True)

log(CP_SL)


# ----------------------------------------------------------------------
# 2) Helper : récupérer les paiements directs liés aux SO d’un BL
# ----------------------------------------------------------------------
def get_direct_payments_for_sales_order_of_delivery_note(delivery_note_name):
    results = []

    sales_orders = frappe.get_all(
        "Delivery Note Item",
        filters={"parent": delivery_note_name},
        fields=["against_sales_order"],
        group_by="against_sales_order",
    )

    for so in sales_orders:
        sales_order = so.get("against_sales_order")

        if not sales_order:
            continue

        payment_refs = frappe.get_all(
            "Payment Entry Reference",
            filters={
                "reference_doctype": "Sales Order",
                "reference_name": sales_order,
            },
            fields=["parent", "allocated_amount"],
        )

        for ref in payment_refs:
            pe = frappe.get_doc("Payment Entry", ref["parent"])

            results.append({
                "sales_order": sales_order,
                "payment_entry": pe.name,
                "payment_date": pe.posting_date,
                "paid_amount": pe.paid_amount,
                "allocated_amount": ref["allocated_amount"],
                "mode_of_payment": pe.mode_of_payment,
                "party": pe.party,
            })

    return results


# ----------------------------------------------------------------------
# 3) Helper : envoi email avec la facture en PJ
# ----------------------------------------------------------------------
def send_email_with_attachment(doctype, docname, recipients, subject, message):
    if not SEND_EMAILS or not recipients:
        return

    if isinstance(recipients, str):
        recipients_list = [recipients]
    else:
        recipients_list = recipients

    attachments = [
        frappe.attach_print(
            doctype=doctype,
            name=docname,
            print_letterhead=True,
        )
    ]

    frappe.sendmail(
        recipients=recipients_list,
        cc=recipients_list,
        sender="aquaworld.servicing@gmail.com",
        subject=subject,
        message=message,
        delayed=True,
        attachments=attachments,
    )


# ----------------------------------------------------------------------
# 4) Récupérer le dernier numéro de facture mensuelle
# ----------------------------------------------------------------------
Max_invoice = frappe.db.sql("""
    SELECT MAX(CAST(custom_numero_facture AS UNSIGNED)) AS max_invoice_number
    FROM `tabSales Invoice` so
    WHERE
        so.docstatus = 1
        AND MONTH(so.posting_date) = MONTH(%s)
        AND YEAR(so.posting_date) = YEAR(%s)
""", (today, today), as_dict=True)

max_val = Max_invoice[0]["max_invoice_number"] if Max_invoice and Max_invoice[0].get("max_invoice_number") else 0
Max_invoice = int(max_val)

errors = []
i = 1


# ----------------------------------------------------------------------
# 5) Boucle clients
# ----------------------------------------------------------------------
for icus in CP_SL:
    customer = icus["customer"]

    try:
        BL_list = icus["delivery_notes_list"].split(",")
        log(BL_list)

        invoice_items = []

        # Liste des commandes liées aux BL.
        # Elle sert à cumuler les remises supplémentaires une seule fois par commande.
        sales_orders_for_discount = []

        template_total_BL = """
        <p><strong>Récapitulatif des bons de livraison :</strong></p>
        """

        Total_NP = 0

        # ------------------------------------------------------------------
        # 5.1 Parcourir les BL du client
        # ------------------------------------------------------------------
        for BL in BL_list:
            BL_i = frappe.get_doc("Delivery Note", BL)
            info = get_direct_payments_for_sales_order_of_delivery_note(BL)

            if not info:
                # BL d'échange (article réel livré à la place du placeholder, réconciliation de stock)
                # ou BL offert (total 0) : aucun paiement à chercher, on l'inclut et on le signale.
                if frappe.utils.flt(BL_i.grand_total) == 0 or BL_i.get("custom_reconciliation_stock"):
                    info = [{"mode_of_payment": "Échange / offert", "allocated_amount": 0}]
                else:
                    raise Exception(f"Aucun paiement trouvé pour le BL {BL}")

            date_i = frappe.utils.getdate(BL_i.posting_date)
            BL_TTC = BL_i.grand_total

            template_BL = """
            <p>
                <strong>Date :</strong> {{ context.date }},
                <strong>Numéro BL :</strong> {{ context.name }} ,
                <strong>Total BL TTC :</strong> {{ context.BL_TL }} ,
            </p>
            """

            context_bl = {
                "date": date_i,
                "name": BL,
                "BL_TL": str(BL_TTC),
                "pay_type": info[0]["mode_of_payment"],
                "pay_all": str(info[0]["allocated_amount"]),
            }

            html_message_i = frappe.render_template(template_BL, {"context": context_bl})
            template_total_BL = template_total_BL + html_message_i

            log(info)

            if info[0]["mode_of_payment"] == "Dette non payée":
                Total_NP += info[0]["allocated_amount"]

            # --------------------------------------------------------------
            # Items de BL vers facture
            # --------------------------------------------------------------
            for item in BL_i.items:
                item_i = {
                    "item_code": item.item_code,
                    "qty": item.qty,
                    "rate": item.rate,
                    "sales_order": item.against_sales_order,
                    "delivery_note": BL_i.name,
                    # lignes reliées : ERPNext peut alors passer le BL et la commande à « facturé »
                    "dn_detail": item.name,
                    "so_detail": item.so_detail,
                }

                # Collecter la commande liée à cette ligne BL
                # pour récupérer son discount_amount.
                if item.against_sales_order and item.against_sales_order not in sales_orders_for_discount:
                    sales_orders_for_discount.append(item.against_sales_order)

                invoice_items.append(item_i)

        # ------------------------------------------------------------------
        # 5.2 Message dette, si nécessaire
        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # 5.3 Création de la facture
        # ------------------------------------------------------------------
        new_invoice = frappe.new_doc("Sales Invoice")
        new_invoice.customer = customer
        # Lignes d'échange sans commande liée : ERPNext exige une commande sauf si le client y est autorisé.
        if not frappe.db.get_value("Customer", customer, "so_required"):
            frappe.db.set_value("Customer", customer, "so_required", 1, update_modified=False)
        new_invoice.company = "Aquaworld & Servicing"
        new_invoice.posting_date = today
        new_invoice.due_date = today
        new_invoice.set_posting_time = 1
        new_invoice.disable_rounded_total = 1
        new_invoice.ignore_pricing_rule = 1

        for item in invoice_items:
            new_invoice.append("items", item)

            # gérer les lignes gratuites
            if item["rate"] == 0:
                new_invoice.items[-1].rate = 0.00
                new_invoice.items[-1].is_free_item = 1
                new_invoice.items[-1].discount_percentage = 100

        # ------------------------------------------------------------------
        # 5.3.1 Cumul des remises supplémentaires des commandes client
        # ------------------------------------------------------------------
        # Le hook Sales Invoice partage la même règle de ristourne que
        # les factures manuelles et conserve la trace de son application.
        new_invoice.taxes_and_charges = "Vente Standard avec Timbre Fiscal - A&S"
        new_invoice.set_taxes()

        new_invoice.allocate_advances_automatically = 1
        new_invoice.only_include_allocated_payments = 1

        new_invoice.ignore_default_payment_terms_template = 1
        new_invoice.payment_terms_template = "Dette"

        # Calculer les taxes et totaux avec la remise avant insertion
        new_invoice.calculate_taxes_and_totals()

        new_invoice.insert()
        new_invoice.set_advances()

        while frappe.db.exists("Sales Invoice", {
            "custom_numero_facture": str(Max_invoice + i), "docstatus": ["<", 2],
            "name": ["!=", new_invoice.name]
        }):
            i = i + 1
        new_invoice.custom_numero_facture = str(Max_invoice + i)

        # Recalcul final après allocation des avances
        new_invoice.calculate_taxes_and_totals()
        new_invoice.save()
        prior_credit = frappe.call(
            "customization_app.facturation_credits.apply_available_credits",
            invoice=new_invoice.name,
        )
        new_invoice.reload()
        # Facture validée automatiquement : plus de brouillon à soumettre à la main, plus de doublon possible.
        new_invoice.submit()
        total_discount_amount = new_invoice.discount_amount

        # ------------------------------------------------------------------
        # 5.3.2 Résumé total des ristournes pour l'email
        # ------------------------------------------------------------------
        template_discount_summary = """
        <p>
            <strong>Total ristourne appliquée :</strong> {{ context.total_discount }} TND<br>
            <strong>Total facture TTC après ristourne :</strong> {{ context.invoice_total }} TND
        </p>
        """

        context_discount_summary = {
            "total_discount": str(frappe.utils.flt(total_discount_amount, 3)),
            "invoice_total": str(frappe.utils.flt(new_invoice.grand_total, 3)),
        }

        html_discount_summary = frappe.render_template(
            template_discount_summary,
            {"context": context_discount_summary}
        )

        # ------------------------------------------------------------------
        # 5.4 Récupérer les contacts du client
        # ------------------------------------------------------------------
        sql_query = """
            SELECT DISTINCT
                con.first_name,
                con.last_name,
                GROUP_CONCAT(DISTINCT email.email_id SEPARATOR '/*/') AS emails,
                GROUP_CONCAT(DISTINCT phone.phone SEPARATOR '/*/') AS phones
            FROM
                `tabContact` con
            JOIN
                `tabDynamic Link` dl ON con.name = dl.parent
            LEFT JOIN
                `tabContact Email` email ON con.name = email.parent
            LEFT JOIN
                `tabContact Phone` phone ON con.name = phone.parent
            WHERE
                dl.link_doctype = 'Customer'
                AND dl.link_name = %s
        """

        contact_details = frappe.db.sql(sql_query, (customer,), as_dict=1)

        if SEND_EMAILS and not contact_details:
            raise Exception(f"Aucun contact trouvé pour le client {customer}")

        contact = contact_details[0] if contact_details else {}

        if SEND_EMAILS and not contact.get("emails"):
            raise Exception(f"Aucun email pour le client {customer}")

        emails = (contact.get("emails") or "").split("/*/")
        date_i = frappe.utils.getdate(today)

        Nom_fac = (
            "FAC"
            + "-"
            + str(date_i.month).zfill(2)
            + "-"
            + str(date_i.year)
            + "-"
            + str(new_invoice.custom_numero_facture).zfill(5)
        )

        template_email = """
            <p>Bonsoir {{ context.name }},</p>

            <p>
                Nous espérons que ce message vous trouve bien.
                Veuillez trouver ci-joint la facture pour le mois de
                [{{ context.mois }}/{{ context.annee }}], couvrant les produits fournis pendant cette période :
            </p>

            <p>
                <strong>Numéro de Facture :</strong> {{ context.NF }}<br>
                <strong>Date de la facture :</strong> {{ context.date }}<br>
                <strong>Montant Total TTC :</strong> {{ context.total_ttc }} TND
            </p>

            <p>Voici les détails de vos commandes pour le mois de [{{ context.mois }}/{{ context.annee }}] :</p>
            {{ context.BL }}

            {{ context.discount_summary }}

            <p>
                Veuillez trouver ci-joint la facture {{ context.nomFac }}.
                Nous vous prions de vérifier les détails et de nous contacter en cas de divergence
                ou pour toute autre question.
            </p>

            <p>
                Nous apprécions votre confiance envers Aqua World et espérons continuer à vous servir
                avec la même excellence à l'avenir.
            </p>

            {{ context.payment_summary }}

            <p>Cordialement,<br>AquaWorld & Servicing</p>
        """

        context_mail = {
            "name": contact.get("first_name") or customer,
            "mois": str(date_i.month).zfill(2),
            "annee": date_i.year,
            "NF": Nom_fac,
            "date": str(today),
            "BL": template_total_BL,
            "discount_summary": html_discount_summary,
            "total_ttc": str(frappe.utils.flt(new_invoice.grand_total, 3)),
            "nomFac": Nom_fac,
            "payment_summary": frappe.call(
                "customization_app.facturation_paiements.invoice_payment_summary",
                invoice=new_invoice.name, prior_credit=prior_credit,
            ),
        }

        html_message = frappe.render_template(template_email, {"context": context_mail})
        log(html_message)

        iemail = emails[0]

        i = i + 1

        # ------------------------------------------------------------------
        # 5.5 Envoi de l'email avec la facture
        # ------------------------------------------------------------------
        send_email_with_attachment(
            "Sales Invoice",
            new_invoice.name,
            iemail,
            Nom_fac,
            html_message,
        )

        if SEND_SMS:
            frappe.call("customization_app.facturation_sms.enqueue_invoice_ready", invoice=new_invoice.name)

        created_invoices.append({"name": new_invoice.name, "customer": customer,
            "grand_total": new_invoice.grand_total, "discount_amount": new_invoice.discount_amount,
            "outstanding_amount": new_invoice.outstanding_amount,
            "delivery_notes": BL_list, "docstatus": new_invoice.docstatus})

    except Exception as e:
        frappe.log_error(
            str(e),
            f"Erreur génération facture mensuelle Compte Pro - Client {customer}"
        )

        errors.append({
            "customer": customer,
            "error": str(e),
        })


# ----------------------------------------------------------------------
# Réactiver l'état disabled des articles désactivés avant le script
# ----------------------------------------------------------------------
for name in disabled_items:
    frappe.db.set_value("Item", name, "disabled", 1, update_modified=False)


# ----------------------------------------------------------------------
# 6) Si erreurs, envoi d’un email récap à l’admin
# ----------------------------------------------------------------------
if errors and SEND_EMAILS:
    rows = []

    for err in errors:
        rows.append(f"- Client: {err['customer']}, Erreur: {err['error']}")

    body = (
        "<p>Bonjour Wassim,</p>"
        "<p>Le script planifié <b>Facturation mensuelle Compte Pro</b> a rencontré des erreurs :</p>"
        "<pre style='white-space: pre-wrap;'>"
        + "\n".join(rows) +
        "</pre>"
        "<p>Vérifie les logs dans ERPNext (Error Log) pour plus de détails.</p>"
    )

    try:
        frappe.sendmail(
            recipients=[ADMIN_EMAIL],
            subject="⚠️ Erreurs Cron - Facturation mensuelle Compte Pro",
            message=body,
            delayed=True,
        )
    except Exception as e:
        frappe.log_error(
            str(e),
            "Erreur envoi email admin - Facturation mensuelle Compte Pro"
        )

# Aucun commit intermédiaire : une erreur annule tout le lot du scheduler.
if errors:
    raise Exception("Facturation mensuelle annulée : " + str(errors))
