/**
 * Sales Order — livraison partielle : la dette suit le livré.
 *
 * Bandeau sur la fiche (dès qu'au moins un BL est validé et que la commande
 * n'est pas entièrement livrée) : livré / réglé / dette affichée / dette
 * réelle / non livré, avec les BL. Et le bouton « Régulariser sur le livré »
 * quand le client ne prendra pas le reste : la commande est ramenée aux
 * quantités livrées (au tarif des BL), la dette au reste dû — dont une part
 * (rien, un montant, ou tout) peut être absorbée en « Perte de paiement ».
 *
 * Un BL de RETOUR (colis Aramex refusé en partie, article rendu) compte en
 * négatif dans le livré : la commande devient une livraison partielle comme
 * une autre — le bandeau dit ce qui est revenu et marque les BL de retour.
 *
 * Le dialogue de régularisation est partagé avec la vue liste (bouton
 * « Livraisons partielles », sales_order_list_livraisons_partielles.js) :
 * customization_app.livraison_partielle.confirmer(nom, devise, ctx, apres).
 *
 * Serveur : customization_app.livraison_partielle (contexte / regulariser).
 */

frappe.provide("customization_app.livraison_partielle");

frappe.ui.form.on("Sales Order", {
  refresh(frm) {
    charger_contexte_livraison(frm);
  },
});

function charger_contexte_livraison(frm) {
  frm.layout.wrapper.find(".so-livraison-bandeau").remove();
  if (frm.is_new() || frm.doc.docstatus !== 1) return;

  const commande = frm.doc.name;
  frappe.call({
    method: "customization_app.livraison_partielle.contexte",
    args: { sales_order: commande },
    callback: (r) => {
      if (frm.doc.name !== commande) return; // navigation pendant l'appel
      frm.layout.wrapper.find(".so-livraison-bandeau").remove();
      const ctx = r.message;
      if (!ctx || !ctx.partielle) return;

      afficher_bandeau_livraison(frm, ctx);
      if (ctx.regularisable) {
        frm.add_custom_button(
          __("Régulariser sur le livré"),
          () => confirmer_regularisation(frm.doc.name, frm.doc.currency, ctx, () => frm.reload_doc()),
          __("Livraison")
        );
      }
    },
  });
}

function afficher_bandeau_livraison(frm, ctx) {
  const esc = frappe.utils.escape_html;
  const dev = frm.doc.currency;
  // <bdi> : le symbole « د.ت » s'écrit de droite à gauche et, sans isolement,
  // le navigateur retourne tout ce qui sépare deux montants (flèche comprise).
  const f = (v) => `<bdi>${format_currency(v, dev)}</bdi>`;

  const bons = (ctx.bons || [])
    .map(
      (b) => `${b.retour ? `<span style="color:#b91c1c;" title="${__("BL de retour")}${b.retour_de ? " — " + esc(b.retour_de) : ""}">↩</span> ` : ""}<a href="/app/delivery-note/${encodeURIComponent(b.name)}"
                 style="font-weight:600;${b.retour ? "color:#b91c1c;" : ""}">${esc(b.name)}</a>
              <span style="color:#6b7280;">(${esc(frappe.datetime.str_to_user(b.date) || b.date)} ·
              ${f(b.montant)})</span>`
    )
    .join(" &nbsp;·&nbsp; ");
  const retourne = flt(ctx.retourne) > 0
    ? `&nbsp;·&nbsp; <span style="color:#b91c1c;">${__("revenu (retour)")} <b>${f(ctx.retourne)}</b></span>`
    : "";

  const couleur = ctx.surevaluee ? "#d46b08" : "#2563eb";
  const verdict = ctx.surevaluee
    ? __("La ligne « Dette non payée » ({0}) compte {1} de marchandise jamais sortie.", [
        `<b>${f(ctx.dette_ligne)}</b>`,
        `<b>${f(ctx.dette_ligne - ctx.dette_reelle)}</b>`,
      ])
    : __("La dette affichée correspond au livré.");
  const suite = ctx.regularisable
    ? __("Si le client ne prendra pas le reste : « Livraison > Régulariser sur le livré ».")
    : esc(ctx.empechement || "");

  const $bandeau = $(`<div class="so-livraison-bandeau" style="margin:8px 0 4px;">
    <div style="padding:6px 12px; border:1px solid var(--border-color,#e4e8ee);
                border-left:4px solid ${couleur}; border-radius:8px;
                font-size:12.5px; background:var(--card-bg,#fff);">
      <div style="margin-bottom:3px;">
        🚚 ${__("Livraison partielle")} :
        ${__("livré")} <b>${f(ctx.livre)}</b> ${__("sur")} ${f(ctx.total)}
        &nbsp;·&nbsp; ${__("réglé")} <b>${f(ctx.regle)}</b>
        &nbsp;·&nbsp; ${__("dette réelle sur le livré")} <b>${f(ctx.dette_reelle)}</b>
        &nbsp;·&nbsp; ${__("non livré")} <b>${f(ctx.non_livre)}</b>
        ${retourne}
        ${flt(ctx.trop_percu) > 0 ? `&nbsp;·&nbsp; <span style="color:#b91c1c;">${__("trop-perçu")} <b>${f(ctx.trop_percu)}</b></span>` : ""}
      </div>
      ${bons ? `<div style="margin-bottom:3px;">${bons}</div>` : ""}
      <div>${verdict}</div>
      <div style="color:#6b7280;">${suite}</div>
    </div>
  </div>`);

  // Même emplacement que le bandeau « avoir » : sous le message bleu de
  // frappe, au-dessus de la barre d'onglets.
  const $tabs = frm.layout.wrapper.find(".form-tabs-list").first();
  if ($tabs.length) {
    $bandeau.insertBefore($tabs);
  } else {
    $bandeau.prependTo(frm.layout.wrapper);
  }
}

// `ctx` : total, livre, dette_ligne, dette_reelle (contexte de la fiche ou
// ligne de la liste) ; `apres` : que faire une fois la commande régularisée.
function confirmer_regularisation(nom, dev, ctx, apres) {
  const f = (v) => `<bdi>${format_currency(v, dev)}</bdi>`;
  const reste = flt(ctx.dette_reelle);

  const d = new frappe.ui.Dialog({
    title: __("Régulariser sur le livré") + " — " + nom,
    fields: [
      {
        fieldname: "resume",
        fieldtype: "HTML",
        options: `<div style="line-height:1.7; margin-bottom:6px;">
          ${__("La commande sera ramenée à ce qui a été livré :")}<br>
          • ${__("total")} : ${f(ctx.total)} → <b>${f(ctx.livre)}</b>
            (${__("au tarif réellement sorti sur les BL")})<br>
          • ${__("dette")} : ${f(ctx.dette_ligne)} → <b>${f(reste)}</b><br>
          • ${__("les articles jamais livrés sont retirés, la commande passe à 100 % livré.")}<br>
          <span style="color:#6b7280;">${__("Le client ne prendra pas le reste ? Cette action ne se défait pas d'un clic.")}</span>
        </div>`,
      },
      {
        // Que faire du reste dû ? L'attendre du client (dette), ou en absorber
        // une partie ou la totalité : cette part passe en « Perte de paiement »
        // et le tandem passe l'écriture sur « Perte de non paiement - A&S ».
        fieldname: "perte",
        label: __("Part du reste dû absorbée en perte de paiement"),
        fieldtype: "Currency",
        options: "currency",
        default: 0,
        hidden: reste <= 0,
        description: __("0 = tout reste en « Dette non payée » ; {0} = on n'attend plus rien du client.", [
          format_currency(reste, dev),
        ]),
        onchange() {
          const perte = flt(d.get_value("perte"));
          if (perte < 0) d.set_value("perte", 0);
          if (perte > reste) d.set_value("perte", reste);
          afficher_partage(d, reste, dev);
        },
      },
      {
        fieldname: "tout_en_perte",
        fieldtype: "Button",
        label: __("Tout absorber en perte"),
        hidden: reste <= 0,
        click() {
          d.set_value("perte", reste);
        },
      },
      {
        fieldname: "partage",
        fieldtype: "HTML",
        hidden: reste <= 0,
      },
    ],
    primary_action_label: __("Régulariser"),
    primary_action(values) {
      const perte = reste > 0 ? Math.min(Math.max(flt(values.perte), 0), reste) : 0;
      d.hide();
      frappe.call({
        method: "customization_app.livraison_partielle.regulariser",
        args: { sales_order: nom, perte },
        freeze: true,
        freeze_message: __("Régularisation de la commande…"),
        callback(r) {
          if (!r.message) return;
          const m = r.message;
          let message = __("Commande ramenée à {0}, dette {1}.", [
            format_currency(m.total, dev),
            format_currency(m.dette, dev),
          ]);
          if (flt(m.perte) > 0) {
            message = __("Commande ramenée à {0} : dette {1}, {2} passés en perte de paiement.", [
              format_currency(m.total, dev),
              format_currency(m.dette, dev),
              format_currency(m.perte, dev),
            ]);
          }
          frappe.show_alert({ message, indicator: "green" });
          if (apres) apres(m);
        },
      });
    },
  });
  afficher_partage(d, reste, dev);
  d.show();
}
customization_app.livraison_partielle.confirmer = confirmer_regularisation;

// Sous le champ : ce que donnera l'échéancier (dette attendue / perte), au fil de la saisie.
function afficher_partage(d, reste, dev) {
  if (reste <= 0) return;
  const f = (v) => `<bdi>${format_currency(v, dev)}</bdi>`;
  const perte = Math.min(Math.max(flt(d.get_value("perte")), 0), reste);
  const dette = flt(reste - perte, 3);
  d.fields_dict.partage.$wrapper.html(`<div style="line-height:1.7; margin-top:-4px;">
    → ${__("Dette non payée")} : <b>${f(dette)}</b>
    &nbsp;·&nbsp; ${__("Perte de paiement")} : <b style="color:${perte > 0 ? "#b91c1c" : "inherit"};">${f(perte)}</b>
  </div>`);
}
