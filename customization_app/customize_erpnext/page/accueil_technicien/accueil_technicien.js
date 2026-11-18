/**
 * « 🏠 Accueil » — la page d’accueil du technicien en écran simplifié (demande du 18/11/2026) :
 * « une page d’accueil pour lui, dans laquelle il peut voir les listes — client, article, facture — seulement ».
 *
 * Trois grands boutons, rien d’autre : chacun ouvre la liste standard (ses droits de lecture sont ceux de son
 * profil, rien n’est accordé ici). La barre de navigation de l’écran simplifié (sous_traitant.js) reste en bas :
 * « Accueil » y reste allumé tant qu’il est dans l’une de ces trois listes ou sur une de leurs fiches.
 *
 * Pour ajouter ou retirer un lien : la liste LIENS ci-dessous, et ACCUEIL_LISTES dans sous_traitant.js.
 */
frappe.pages["accueil-technicien"].on_page_load = function (wrapper) {
    frappe.ui.make_app_page({ parent: wrapper, title: __("Accueil"), single_column: true });

    const LIENS = [
        { doctype: "Customer",      ic: "👤", libelle: __("Clients"),  aide: __("Chercher un client, voir sa fiche") },
        { doctype: "Item",          ic: "📦", libelle: __("Articles"), aide: __("Chercher un article, voir son prix") },
        { doctype: "Sales Invoice", ic: "🧾", libelle: __("Factures"), aide: __("Voir les factures de vente") },
    ];

    if (!document.getElementById("at-css")) {
        const s = document.createElement("style");
        s.id = "at-css";
        s.textContent = `
        .at { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; padding: 4px 0 16px; }
        .at-lien { display: flex; align-items: center; gap: 14px; width: 100%; min-height: 84px; padding: 14px 16px;
                   background: #fff; border: 1px solid var(--border-color, #e4e8ee); border-radius: 14px;
                   text-align: left; cursor: pointer; }
        .at-lien:active { background: #f3f6fa; }
        .at-lien .ic { font-size: 30px; line-height: 1; }
        .at-lien b { display: block; font-size: 17px; }
        .at-lien small { display: block; color: var(--text-muted, #6b7280); font-size: 12.5px; margin-top: 2px; }
        .at-lien .fl { margin-left: auto; color: var(--text-muted, #6b7280); font-size: 20px; }`;
        document.head.appendChild(s);
    }

    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const $el = $('<div class="at"></div>').appendTo($(wrapper).find(".layout-main-section"));
    LIENS.forEach((l) => {
        $(`<button type="button" class="at-lien"><span class="ic">${l.ic}</span>
               <span><b>${esc(l.libelle)}</b><small>${esc(l.aide)}</small></span><span class="fl">›</span></button>`)
            .appendTo($el)
            .on("click", () => frappe.set_route("List", l.doctype));
    });
};
