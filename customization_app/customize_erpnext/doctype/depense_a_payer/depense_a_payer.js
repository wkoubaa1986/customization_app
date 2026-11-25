// Fiche « Dépense à payer » — pour le comptable (Accounts Manager, System Manager), les BL déjà reçus que la dépense
// couvre (25/11/2026, customization_app.caisse_bl) :
//   · « ↩ Rouvrir les BL » : défait leur solde (écritures « Réception soldée — » annulées, commande et réception
//     rouvertes, fiche « À saisir ») — avant une correction de la dépense, ou si le mauvais BL avait été coché ;
//   · « 📦 Rattacher des BL déjà reçus » : couvre après coup un BL oublié à la saisie (mêmes contrôles, même solde).
// La dépense elle-même ne bouge jamais.
frappe.ui.form.on("Depense A Payer", {
  refresh(frm) {
    if (frm.is_new() || !frappe.user.has_role(["Accounts Manager", "System Manager"])) return;
    const esc = frappe.utils.escape_html;
    frappe.call({ method: "customization_app.caisse_bl.bls_de_la_depense", args: { depense: frm.doc.name } }).then((r) => {
      const m = r.message || {};
      const couverts = (m.couverts || []).filter((f) => f.statut === "Saisie");
      if (couverts.length) {
        frm.add_custom_button(__("↩ Rouvrir les BL ({0})", [couverts.length]), () => {
          frappe.confirm(
            __("Rouvrir {0} ? Leurs écritures de solde sont annulées, commande et réception rouvertes, et chaque fiche redevient « À saisir ». La dépense ne change pas.",
              [esc(couverts.map((f) => "BL " + (f.numero_bl || f.name)).join(", "))]),
            () => frappe.call({ method: "customization_app.caisse_bl.rouvrir_bls", args: { depense: frm.doc.name },
                                freeze: true, freeze_message: __("Réouverture des BL…") })
              .then((x) => {
                frappe.show_alert({ message: __("BL rouverts : {0}", [esc(((x.message || {}).rouverts || []).join(", "))]),
                                    indicator: "orange" }, 8);
                frm.reload_doc();
              }));
        }, __("BL"));
      }
      if ((m.ouverts || []).length) {
        frm.add_custom_button(__("📦 Rattacher des BL déjà reçus"), () => {
          const d = new frappe.ui.Dialog({
            title: __("BL déjà reçus couverts par cette dépense"),
            fields: [{ fieldtype: "HTML", fieldname: "liste" }],
            primary_action_label: __("Rattacher"),
            primary_action() {
              const fiches = d.$wrapper.find("input.dap-bl:checked").map((i, e) => $(e).attr("data-bl")).get();
              if (!fiches.length) { frappe.msgprint(__("Cochez au moins un BL.")); return; }
              frappe.call({ method: "customization_app.caisse_bl.couvrir_bls_apres",
                            args: { depense: frm.doc.name, fiches: JSON.stringify(fiches) },
                            freeze: true, freeze_message: __("Solde des réceptions…") })
                .then(() => { d.hide(); frappe.show_alert({ message: __("BL rattachés : réceptions soldées."), indicator: "green" }); frm.reload_doc(); });
            },
          });
          d.fields_dict.liste.$wrapper.html(`<p class="text-muted" style="font-size:12.5px">${
            __("Cochez les BL de {0} que cette facture couvre : ils ne seront pas rachetés, leur réception est soldée et le BL clos.", [esc(m.supplier || "")])}</p>`
            + m.ouverts.map((b) => `<label style="display:block;border:1px solid #e2e6e9;border-radius:6px;padding:6px 8px;margin-bottom:6px;font-weight:normal">
                <input type="checkbox" class="dap-bl" data-bl="${esc(b.name)}">
                <b>BL ${esc(b.numero_bl || "?")}</b>${b.date ? " " + __("du") + " " + esc(frappe.datetime.str_to_user(b.date)) : ""}
                — ${format_currency(b.montant)} · ${__("reçu {0} HT", [format_currency(b.recu)])}
                <span class="text-muted">(${esc(b.name)}${b.supplier && b.supplier !== m.supplier ? " · " + esc(b.supplier) : ""})</span></label>`).join(""));
          d.show();
        }, __("BL"));
      }
    });
  },
});
