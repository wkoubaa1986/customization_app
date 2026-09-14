/**
 * Sales Order — groupe « Avoir » : créer un avoir, puis l'utiliser.
 *
 * 1. BL de retour — la marchandise revient.
 * 2. « Créer un avoir » sur la commande d'origine : Journal Entry flottant,
 *    crédit « Débiteurs - A&S » (client) / débit « Compte temporaire ».
 * 3. « Utiliser un avoir » sur une AUTRE commande : ajoute à son échéancier une
 *    ligne au mode « Avoir client » et diminue d'autant une autre ligne. Les
 *    Server Scripts existants créent alors l'écriture d'utilisation.
 *
 * L'étape 3 ne se voyait nulle part : d'où le bandeau qui annonce le solde
 * d'avoir du client dès l'ouverture de la commande.
 */

frappe.ui.form.on("Sales Order", {
  refresh(frm) {
    charger_contexte_avoir(frm);

    if (frm.doc.docstatus !== 1) return;

    frm.add_custom_button(
      __("Créer un avoir"),
      () => open_avoir_dialog(frm),
      __("Avoir")
    );
  },
});

// ─────────────────────────── Bandeau « avoir disponible » ───────────────────
// Injection DOM directe au-dessus du formulaire (comme le bandeau des tâches) :
// la zone dashboard est repliable et vidée par le cycle de refresh.
function charger_contexte_avoir(frm) {
  const $ancien = frm.layout.wrapper.find(".so-avoir-bandeau");
  if (frm.is_new()) {
    $ancien.remove();
    return;
  }
  const commande = frm.doc.name;
  frappe.call({
    method: "customization_app.avoir_client.contexte_avoir",
    args: { sales_order: commande },
    callback: (r) => {
      // le doc peut avoir changé pendant l'appel (navigation SPA)
      if (frm.doc.name !== commande) return;
      frm.layout.wrapper.find(".so-avoir-bandeau").remove();
      const ctx = r.message;
      if (!ctx || flt(ctx.disponible) <= 0) return;

      afficher_bandeau_avoir(frm, ctx);
      if (ctx.imputable) {
        frm.add_custom_button(
          __("Utiliser un avoir"),
          () => open_utiliser_avoir_dialog(frm, ctx),
          __("Avoir")
        );
      }
    },
  });
}

function afficher_bandeau_avoir(frm, ctx) {
  const esc = frappe.utils.escape_html;
  const ecritures = (ctx.ecritures || [])
    .map(
      (e) => `<a href="/app/journal-entry/${encodeURIComponent(e.journal_entry)}"
                 style="font-weight:700;">${esc(e.journal_entry)}</a>
              <span style="color:#6b7280;">(${esc(frappe.datetime.str_to_user(e.date) || e.date)} ·
              ${format_currency(e.montant, frm.doc.currency)})</span>`
    )
    .join(" &nbsp;·&nbsp; ");

  const rappel = ctx.imputable
    ? __("Utilisez « Avoir > Utiliser un avoir » pour l'imputer sur cette commande.")
    : esc(ctx.empechement || "");

  const $bandeau = $(`<div class="so-avoir-bandeau" style="margin:8px 0 4px;">
    <div style="padding:6px 12px; border:1px solid var(--border-color,#e4e8ee);
                border-left:4px solid #d46b08; border-radius:8px;
                font-size:12.5px; background:var(--card-bg,#fff);">
      <div style="margin-bottom:3px;">
        🧾 ${__("Avoir disponible pour ce client")} :
        <b>${format_currency(ctx.disponible, frm.doc.currency)}</b>
        <span style="color:#6b7280;">
          (${__("créés")} ${format_currency(ctx.cree, frm.doc.currency)} ·
           ${__("utilisés")} ${format_currency(ctx.utilise, frm.doc.currency)})</span>
      </div>
      ${ecritures ? `<div style="margin-bottom:3px;">${ecritures}</div>` : ""}
      <div style="color:#6b7280;">${rappel}</div>
    </div>
  </div>`);

  // SOUS le message bleu (« Valider ce document… »), AU-DESSUS de la barre
  // d'onglets Détails / Adresse & Contact.
  const $tabs = frm.layout.wrapper.find(".form-tabs-list").first();
  if ($tabs.length) {
    $bandeau.insertBefore($tabs);
  } else {
    $bandeau.prependTo(frm.layout.wrapper);
  }
}

// ──────────────────────────── Utiliser un avoir ─────────────────────────────
function open_utiliser_avoir_dialog(frm, ctx) {
  const devise = frm.doc.currency;
  const options_lignes = (ctx.lignes || []).map((l) => ({
    value: l.nom,
    label: `${__("Ligne")} ${l.idx} · ${l.mode_of_payment || __("sans mode")} · ${format_currency(
      l.payment_amount,
      devise
    )}`,
  }));

  const d = new frappe.ui.Dialog({
    title: __("Utiliser un avoir client"),
    fields: [
      {
        fieldname: "process",
        fieldtype: "HTML",
        options: `<div style="margin-bottom:8px;color:#6b7280;font-size:12px;line-height:1.6;">
          <b>${__("Le process")}</b> :
          1. ${__("BL de retour")} &nbsp;→&nbsp;
          2. ${__("« Créer un avoir » sur la commande d'origine")} &nbsp;→&nbsp;
          3. ${__("« Utiliser un avoir » ici.")}<br>
          ${__("Avoirs créés")} : <b>${format_currency(ctx.cree, devise)}</b> &nbsp;·&nbsp;
          ${__("utilisés")} : <b>${format_currency(ctx.utilise, devise)}</b> &nbsp;·&nbsp;
          ${__("disponible")} : <b>${format_currency(ctx.disponible, devise)}</b><br>
          ${__(
            "L'avoir est ajouté à l'échéancier au mode « Avoir client » ; la ligne choisie est diminuée d'autant, le total reste égal au TTC."
          )}</div>`,
      },
      {
        fieldname: "montant",
        label: __("Montant à imputer"),
        fieldtype: "Currency",
        reqd: 1,
        default: ctx.montant_propose,
      },
      {
        // Les lignes « Avoir client » ne sont pas dans la liste : le serveur
        // refuse de diminuer un avoir déjà imputé.
        fieldname: "ligne",
        label: __("Ligne d'échéancier à diminuer"),
        fieldtype: "Select",
        reqd: 1,
        options: options_lignes,
        default: ctx.ligne_par_defaut,
        change() {
          // On ne peut pas imputer plus que ce que porte la ligne choisie.
          const choisie = (ctx.lignes || []).find((l) => l.nom === d.get_value("ligne"));
          if (!choisie) return;
          d.set_value("montant", Math.min(flt(ctx.disponible), flt(choisie.payment_amount)));
        },
      },
    ],
    primary_action_label: __("Imputer l'avoir"),
    primary_action(values) {
      frappe.call({
        method: "customization_app.avoir_client.appliquer_avoir",
        args: {
          sales_order: frm.doc.name,
          montant: values.montant,
          ligne: values.ligne,
        },
        freeze: true,
        freeze_message: __("Imputation de l'avoir…"),
        callback(r) {
          if (!r.message) return;
          d.hide();
          frappe.show_alert({
            message: __("Avoir de {0} imputé sur la commande.", [
              format_currency(r.message.montant, devise),
            ]),
            indicator: "green",
          });
          frm.reload_doc();
        },
      });
    },
  });
  d.show();
}

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
