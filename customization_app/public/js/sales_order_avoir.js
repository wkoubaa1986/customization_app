/**
 * Sales Order — bouton « Créer un avoir ».
 *
 * Crée un avoir client (Journal Entry) à partir d'une commande payée dont le client
 * a fait un retour : crédit « Débiteurs - A&S » (client) / débit « Compte temporaire ».
 * C'est un crédit client flottant, appliqué ensuite comme paiement par la logique
 * existante (ligne d'échéancier au mode « Avoir client » du même montant).
 */

frappe.ui.form.on("Sales Order", {
  refresh(frm) {
    if (frm.doc.docstatus !== 1) return;

    frm.add_custom_button(
      __("Créer un avoir"),
      () => open_avoir_dialog(frm),
      __("Avoir")
    );
  },
});

function open_avoir_dialog(frm) {
  const paid = flt(frm.doc.advance_paid);
  const d = new frappe.ui.Dialog({
    title: __("Créer un avoir client"),
    fields: [
      {
        fieldname: "info",
        fieldtype: "HTML",
        options: `<div style="margin-bottom:8px;color:#6b7280;font-size:12px;">
          Client : <b>${frappe.utils.escape_html(frm.doc.customer || "")}</b>
          &nbsp;·&nbsp; Total payé : <b>${format_currency(paid, frm.doc.currency)}</b><br>
          Crédite le client (Débiteurs) — avoir réutilisable comme paiement.</div>`,
      },
      {
        fieldname: "amount",
        label: __("Montant de l'avoir"),
        fieldtype: "Currency",
        reqd: 1,
        default: paid > 0 ? paid : null,
      },
      {
        fieldname: "debit_account",
        label: __("Compte de contrepartie (débit)"),
        fieldtype: "Link",
        options: "Account",
        default: "Compte temporaire - compte  d'overture - A&S",
        get_query: () => ({ filters: { is_group: 0, company: frm.doc.company } }),
      },
      {
        fieldname: "reference",
        label: __("Référence"),
        fieldtype: "Data",
        default: "Avoir " + (frm.doc.customer || ""),
      },
      {
        fieldname: "remark",
        label: __("Motif"),
        fieldtype: "Small Text",
      },
      {
        fieldname: "posting_date",
        label: __("Date"),
        fieldtype: "Date",
        default: frappe.datetime.get_today(),
      },
    ],
    primary_action_label: __("Créer l'avoir"),
    primary_action(values) {
      if (flt(values.amount) > paid && paid > 0) {
        frappe.confirm(
          __("Le montant ({0}) dépasse le total payé ({1}). Continuer ?", [
            format_currency(values.amount, frm.doc.currency),
            format_currency(paid, frm.doc.currency),
          ]),
          () => submit_avoir(frm, d, values)
        );
      } else {
        submit_avoir(frm, d, values);
      }
    },
  });
  d.show();
}

function submit_avoir(frm, d, values) {
  frappe.call({
    method: "customization_app.avoir_client.create_avoir_from_sales_order",
    args: {
      sales_order: frm.doc.name,
      amount: values.amount,
      debit_account: values.debit_account,
      reference: values.reference,
      remark: values.remark,
      posting_date: values.posting_date,
    },
    freeze: true,
    freeze_message: __("Création de l'avoir…"),
    callback(r) {
      if (!r.message) return;
      d.hide();
      const je = r.message.journal_entry;
      frappe.msgprint({
        title: __("Avoir créé"),
        indicator: "green",
        message: __("Avoir de {0} créé pour {1} : {2}", [
          format_currency(r.message.amount, frm.doc.currency),
          frappe.utils.escape_html(r.message.customer || ""),
          `<a href="/app/journal-entry/${encodeURIComponent(je)}" target="_blank">${je}</a>`,
        ]),
      });
    },
  });
}
