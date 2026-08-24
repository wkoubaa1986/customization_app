// Rattachement des BONS DE LIVRAISON capturés en caisse à une facture d'achat.
//
// Les BL d'un fournisseur (fiches « Facture Achat a Saisir » marquées est_bl)
// s'accumulent en attendant la facture — souvent mensuelle, couvrant plusieurs
// BL. Sur la facture en BROUILLON, le bouton « 📦 Rattacher des BL » ouvre la
// sélection MANUELLE (décision utilisateur 24/08) ; à la soumission, chaque
// fiche passe « Saisie » et chaque avance devient un paiement de la facture
// (hooks `pi_marquer_fiche_saisie`).

frappe.ui.form.on("Purchase Invoice", {
  refresh(frm) {
    if (frm.doc.docstatus !== 0 || !frm.doc.supplier) return;
    frappe.call({
      method: "customization_app.caisse_depenses.bls_en_attente",
      args: { supplier: frm.doc.supplier },
      callback: (r) => {
        const bls = r.message || [];
        if (!bls.length) return;
        frm.add_custom_button(__("📦 Rattacher des BL ({0})", [bls.length]), () => {
          pi_caisse_dialog_bls(frm, bls);
        });
      },
    });
  },
});

function pi_caisse_dialog_bls(frm, bls) {
  const esc = frappe.utils.escape_html;
  const d = new frappe.ui.Dialog({
    title: __("📦 BL en attente — {0}", [frm.doc.supplier]),
    size: "large",
    fields: [{ fieldtype: "HTML", fieldname: "liste" }],
    primary_action_label: __("🔗 Rattacher la sélection"),
    primary_action() {
      const choisis = d.fields_dict.liste.$wrapper
        .find(".pi-bl-choix:checked").map((_, el) => $(el).val()).get();
      if (!choisis.length) {
        frappe.msgprint(__("Cochez au moins un BL."));
        return;
      }
      frappe.call({
        method: "customization_app.caisse_depenses.rattacher_bls",
        args: { purchase_invoice: frm.doc.name, fiches: JSON.stringify(choisis) },
        freeze: true, freeze_message: __("Rattachement…"),
        callback: (r) => {
          d.hide();
          frappe.show_alert({
            message: __("{0} BL rattaché(s) — ils passeront « Saisie » à la soumission de la facture.",
              [(r.message.rattaches || []).length]),
            indicator: "green",
          }, 7);
          frm.reload_doc();
        },
      });
    },
  });
  const maj_total = () => {
    let total = 0;
    d.fields_dict.liste.$wrapper.find(".pi-bl-choix:checked").each((_, el) => {
      total += flt($(el).data("montant"));
    });
    d.fields_dict.liste.$wrapper.find(".pi-bl-total").text(format_currency(total, "TND"));
  };
  d.fields_dict.liste.$wrapper.html(`
    <table class="table table-bordered" style="font-size:12.5px">
      <thead><tr>
        <th style="width:30px"><input type="checkbox" class="pi-bl-tout"></th>
        <th>${__("N° BL")}</th><th>${__("Date")}</th><th>${__("Description")}</th>
        <th>${__("Payé")}</th>
        <th style="text-align:right">${__("Montant")}</th>
      </tr></thead>
      <tbody>
        ${bls.map((b) => `
          <tr>
            <td><input type="checkbox" class="pi-bl-choix" value="${esc(b.name)}" data-montant="${b.montant}"></td>
            <td><a href="/app/facture-achat-a-saisir/${encodeURIComponent(b.name)}" target="_blank">${esc(b.numero_bl || b.name)}</a></td>
            <td>${esc(b.date_facture || "")}</td>
            <td>${esc(b.description || "")}</td>
            <td>${b.mode_paiement === "Pas payé" ? "—" : esc(b.mode_paiement || "")}</td>
            <td style="text-align:right;font-weight:700">${format_currency(b.montant, "TND")}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    <div style="font-weight:700">${__("Total sélectionné")} : <span class="pi-bl-total">0</span>
      &nbsp;·&nbsp; <span class="text-muted" style="font-weight:400">${__("Total facture")} :
      ${format_currency(frm.doc.rounded_total || frm.doc.grand_total || 0, "TND")}</span></div>
    <div class="text-muted" style="font-size:11px;margin-top:4px">
      ${__("Un BL déjà payé en caisse verra son avance transformée en paiement de cette facture à la soumission ; un BL non payé laissera la dette naître avec la facture.")}
    </div>`);
  d.fields_dict.liste.$wrapper.find(".pi-bl-choix").on("change", maj_total);
  d.fields_dict.liste.$wrapper.find(".pi-bl-tout").on("change", function () {
    d.fields_dict.liste.$wrapper.find(".pi-bl-choix").prop("checked", this.checked);
    maj_total();
  });
  d.show();
}
