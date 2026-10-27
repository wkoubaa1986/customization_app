// Marge minimale : met en évidence les lignes dont le prix a été relevé (customization_app.marge_minimale).
// Bandeau orange en tête du formulaire + fond orangé sur la ligne dans le tableau des articles.
(() => {
    const FLAG = "custom_marge_corrigee", AVANT = "custom_prix_avant_marge";

    function relevees(frm) { return (frm.doc.items || []).filter((l) => l[FLAG]); }

    function bandeau(frm) {
        const lignes = relevees(frm);
        frm.set_intro("", "orange");
        if (!lignes.length) return;
        const detail = lignes.map((l) => `Ligne ${l.idx} — ${frappe.utils.escape_html(l.item_name || l.item_code)} : `
            + `<span dir="ltr">${format_currency(l[AVANT], frm.doc.currency)}</span> → <b dir="ltr">${format_currency(l.rate, frm.doc.currency)}</b>`).join("<br>");
        // set_intro : bandeau sous le titre, visible quel que soit l'onglet du formulaire.
        frm.set_intro(`<span class="so-marge-bandeau"><b>⬆ ${lignes.length} prix relevé(s) à la marge minimale</b> — le prix laissait moins que la marge plancher.<br>${detail}</span>`, "orange");
    }

    function colorer(frm) {
        const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
        if (!grid) return;
        (grid.grid_rows || []).forEach((r) => {
            const on = !!(r.doc && r.doc[FLAG]);
            r.wrapper.toggleClass("so-marge-ligne", on)
                .find(".grid-static-col").css("background", on ? "#fff7ed" : "");
            const $c = r.wrapper.find('.grid-static-col[data-fieldname="item_code"]').first();
            $c.find(".so-marge-puce").remove();
            if (on) $c.append(`<span class="so-marge-puce" title="Prix relevé à la marge minimale (avant : ${format_currency(r.doc[AVANT], frm.doc.currency)})" style="margin-left:4px;font-size:11px;color:#b45309">⬆ marge</span>`);
        });
    }

    frappe.ui.form.on("Sales Order", {
        refresh(frm) { bandeau(frm); colorer(frm); },
        items_on_form_rendered(frm) { colorer(frm); },
    });
    frappe.ui.form.on("Sales Order Item", {
        items_remove(frm) { colorer(frm); },
    });
})();
