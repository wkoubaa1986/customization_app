// Rattachement des BONS DE LIVRAISON capturés en caisse à une facture d'achat.
//
// Les BL d'un fournisseur (fiches « Facture Achat a Saisir » marquées est_bl)
// s'accumulent en attendant la facture — souvent mensuelle, couvrant plusieurs
// BL. Sur la facture en BROUILLON, le bouton « 📦 Rattacher des BL » ouvre la
// sélection MANUELLE (décision utilisateur 24/08) ; à la soumission, chaque
// fiche passe « Saisie » et chaque avance devient un paiement de la facture
// (hooks `pi_marquer_fiche_saisie`).

frappe.provide("customization_app");
// Dialogue partagé (fiche de caisse, facture d'achat) : confirmer puis basculer.
customization_app.basculer_fiche_pas_paye = function (fiche, mode, apres) {
  frappe.confirm(
    __("Passer la fiche {0} de « {1} » à « Pas payé » ? L'avance de caisse (ou le paiement qui en est né) sera annulée : la facture redevient due et rejoint « Factures à payer ».", [fiche, mode || "—"]),
    () => frappe.call({
      method: "customization_app.caisse_depenses.basculer_pas_paye",
      args: { fiche }, freeze: true, freeze_message: __("Bascule…"),
      callback: (r) => {
        const m = r.message || {};
        frappe.show_alert({ message: __("Fiche {0} : « Pas payé ». {1} écriture(s) supprimée(s), {2} paiement(s) annulé(s).",
          [m.fiche, (m.ecritures || []).length, (m.paiements || []).length]), indicator: "green" }, 8);
        if (apres) apres();
      },
    })
  );
};

frappe.ui.form.on("Purchase Invoice", {
  refresh(frm) {
    if (frm.doc.docstatus === 1) {
      // facture saisie depuis une fiche de caisse payée par erreur
      frappe.db.get_list("Facture Achat a Saisir", {
        filters: { purchase_invoice: frm.doc.name, mode_paiement: ["!=", "Pas payé"] },
        fields: ["name", "mode_paiement", "payment_entry", "payment_entries"],
      }).then((fiches) => {
        (fiches || []).filter((f) => f.payment_entry || f.payment_entries).forEach((f) => {
          frm.add_custom_button(__("🔁 Fiche {0} : basculer en « Pas payé »", [f.name]), () =>
            customization_app.basculer_fiche_pas_paye(f.name, f.mode_paiement, () => frm.reload_doc()),
            __("Caisse"));
        });
      });
    }
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
