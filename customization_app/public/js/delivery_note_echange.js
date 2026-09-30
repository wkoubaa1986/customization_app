/**
 * Delivery Note — échange d'article : bandeau qui relie le BL d'échange et
 * son BL retour de reprise (créé automatiquement à la validation, voir
 * customization_app.retour_echange).
 */

frappe.ui.form.on("Delivery Note", {
  refresh(frm) {
    frm.layout.wrapper.find(".dn-echange-bandeau").remove();
    if (frm.is_new()) return;
    const esc = frappe.utils.escape_html;
    const lien = (nom) =>
      `<a href="/app/delivery-note/${encodeURIComponent(nom)}" style="font-weight:600;">${esc(nom)}</a>`;

    let texte = "";
    let couleur = "#2563eb";
    if (frm.doc.custom_retour_echange) {
      texte = __("Échange d'article : la pièce reprise est rentrée en stock par le BL retour {0}.", [
        lien(frm.doc.custom_retour_echange),
      ]);
    } else if (frm.doc.custom_echange_de) {
      couleur = "#d46b08";
      texte = __("Reprise automatique de l'échange {0} : ce BL retour s'annule et se supprime avec lui.", [
        lien(frm.doc.custom_echange_de),
      ]);
    } else {
      return;
    }

    const $bandeau = $(`<div class="dn-echange-bandeau" style="margin:8px 0 4px;">
      <div style="padding:6px 12px; border:1px solid var(--border-color,#e4e8ee);
                  border-left:4px solid ${couleur}; border-radius:8px; font-size:12.5px;
                  background:var(--card-bg,#fff);">🔁 ${texte}</div>
    </div>`);
    const $tabs = frm.layout.wrapper.find(".form-tabs-list").first();
    if ($tabs.length) $bandeau.insertBefore($tabs);
    else $bandeau.prependTo(frm.layout.wrapper);
  },
});
