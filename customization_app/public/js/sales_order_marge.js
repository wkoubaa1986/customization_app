// Marge minimale (customization_app.marge_minimale) — commande client ET devis :
//  1. TEMPS RÉEL : dès qu'un article, une quantité, un prix ou une remise change sur une ligne, le formulaire
//     demande au serveur le prix plancher de la ligne et relève le prix sous les yeux de l'utilisateur.
//     Le hook serveur `validate` refait le même calcul à l'enregistrement (filet de sécurité).
//  2. Document EXONÉRÉ de TVA (aucune taxe incluse dans le prix) : le prix de liste TTC devient HT
//     (TTC ÷ (1 + TVA de l'article)) dès le choix de l'article ; un prix tapé à la main est gardé.
//  3. Mise en évidence des lignes relevées : bandeau orange sous le titre + fond orangé + puce « ⬆ marge ».
(() => {
    const FLAG = "custom_marge_corrigee", AVANT = "custom_prix_avant_marge", TOL = 0.0005, DELAI = 450;
    const minuteries = {};
    const DOCTYPES = ["Sales Order", "Quotation"];

    function exonere(frm) { return !!(frm.doc.taxes || []).length && !(frm.doc.taxes || []).some((t) => t.included_in_print_rate); }

    function relevees(frm) { return (frm.doc.items || []).filter((l) => l[FLAG]); }

    function bandeau(frm) {
        const lignes = relevees(frm);
        frm.set_intro("", "orange");
        const exo = exonere(frm) && (frm.doc.items || []).some((l) => l.item_code);
        if (!lignes.length && !exo) return;
        const parts = [];
        if (exo) parts.push(`<span class="so-exonere">🧾 Document exonéré de TVA : les prix de liste sont posés <b>hors taxe</b> (TTC ÷ (1 + TVA de l’article)).</span>`);
        if (lignes.length) {
            const detail = lignes.map((l) => `Ligne ${l.idx} — ${frappe.utils.escape_html(l.item_name || l.item_code)} : `
                + `<span dir="ltr">${format_currency(l[AVANT], frm.doc.currency)}</span> → <b dir="ltr">${format_currency(l.rate, frm.doc.currency)}</b>`).join("<br>");
            parts.push(`<span class="so-marge-bandeau"><b>⬆ ${lignes.length} prix relevé(s) à la marge minimale</b> — le prix laissait moins que la marge plancher.<br>${detail}</span>`);
        }
        // set_intro : bandeau sous le titre, visible quel que soit l'onglet du formulaire.
        frm.set_intro(parts.join("<br>"), "orange");
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

    function peindre(frm) { bandeau(frm); colorer(frm); }

    /** Les composants d'un ensemble (Product Bundle) tels que le formulaire les connaît. */
    function composants(frm, l) {
        return (frm.doc.packed_items || [])
            .filter((p) => (p.parent_detail_docname ? p.parent_detail_docname === l.name : p.parent_item === l.item_code))
            .map((p) => ({ item_code: p.item_code, qty: p.qty, warehouse: p.warehouse || l.warehouse }));
    }

    /** Vérifie UNE ligne (après un court délai : ERPNext finit d'abord de poser prix de liste et règle de prix). */
    function verifier(frm, cdn) {
        if (frm.doc.docstatus !== 0) return;
        const cdt = frm.doctype + " Item";
        clearTimeout(minuteries[cdn]);
        minuteries[cdn] = setTimeout(async () => {
            const l = locals[cdt] && locals[cdt][cdn];
            if (!l || !l.item_code) return;
            let rep;
            try {
                rep = await frappe.xcall("customization_app.marge_minimale.plancher_ligne", {
                    ligne: { item_code: l.item_code, warehouse: l.warehouse, qty: l.qty, conversion_factor: l.conversion_factor,
                        item_tax_rate: l.item_tax_rate, item_group: l.item_group, is_free_item: l.is_free_item, rate: l.rate,
                        price_list: frm.doc.selling_price_list, uom: l.uom, composants: composants(frm, l) },
                    taxes: (frm.doc.taxes || []).map((t) => ({ account_head: t.account_head, rate: t.rate, charge_type: t.charge_type, included_in_print_rate: t.included_in_print_rate })),
                    woocommerce_id: frm.doc.woocommerce_id || null,
                });
            } catch (e) { return; }
            const l2 = locals[cdt] && locals[cdt][cdn];
            if (!l2) return;
            // Document exonéré : le prix de liste passe en HT ; le prix de la ligne suit (remise de règle gardée
            // en %, prix tapé à la main gardé), puis le prix de liste déclenche une nouvelle vérification.
            if (rep && rep.prix_liste_ht != null && Math.abs(flt(l2.price_list_rate) - flt(rep.prix_liste_ht)) > TOL) {
                const manuel = !l2.pricing_rules && (flt(l2.discount_percentage) !== 0 || l2.margin_type);
                const rateAvant = flt(l2.rate);
                await frappe.model.set_value(cdt, cdn, "price_list_rate", flt(rep.prix_liste_ht));
                if (manuel) await frappe.model.set_value(cdt, cdn, "rate", rateAvant);
                peindre(frm);
                return;
            }
            if (!rep || rep.plancher == null) { if (l2[FLAG] && rep && rep.plancher == null) { oublier(frm, l2); } return; }
            const rate = flt(l2.rate), plancher = flt(rep.plancher);
            if (rate + TOL >= plancher) {
                // Au-dessus du plancher : la trace reste si on est exactement sur le prix relevé, sinon elle s'efface.
                if (l2[FLAG] && Math.abs(rate - plancher) > TOL) oublier(frm, l2);
                return;
            }
            // Sous le plancher : on relève. set_value("rate") laisse ERPNext poser remise / marge et refaire les totaux.
            if (!l2[FLAG]) frappe.model.set_value(l2.doctype, l2.name, AVANT, rate);
            frappe.model.set_value(l2.doctype, l2.name, FLAG, 1);
            frappe.model.set_value(l2.doctype, l2.name, "rate", plancher).then(() => {
                peindre(frm);
                frappe.show_alert({ indicator: "orange",
                    message: __("⬆ {0} : prix relevé de {1} à {2} (marge minimale {3} %)",
                        [l2.item_name || l2.item_code, format_currency(rate, frm.doc.currency), format_currency(plancher, frm.doc.currency), rep.taux]) }, 6);
            });
        }, DELAI);
    }

    function oublier(frm, l) {
        frappe.model.set_value(l.doctype, l.name, FLAG, 0);
        frappe.model.set_value(l.doctype, l.name, AVANT, 0);
        peindre(frm);
    }

    function verifierTout(frm) { (frm.doc.items || []).forEach((l) => verifier(frm, l.name)); }

    const ligne = {};
    ["item_code", "qty", "uom", "conversion_factor", "warehouse", "rate", "price_list_rate", "discount_percentage",
     "discount_amount", "margin_rate_or_amount", "item_tax_template"].forEach((f) => { ligne[f] = (frm, cdt, cdn) => verifier(frm, cdn); });
    ligne.items_remove = (frm) => peindre(frm);
    const entete = {
        refresh(frm) { peindre(frm); },
        items_on_form_rendered(frm) { colorer(frm); },
        selling_price_list(frm) { verifierTout(frm); },
        ignore_pricing_rule(frm) { verifierTout(frm); },
        // Le modèle de taxes change (exonération ↔ standard) : ERPNext recharge les taxes puis on repasse les lignes.
        taxes_and_charges(frm) { setTimeout(() => { peindre(frm); verifierTout(frm); }, 600); },
        customer(frm) { verifierTout(frm); },
        party_name(frm) { verifierTout(frm); },
    };
    DOCTYPES.forEach((dt) => { frappe.ui.form.on(dt + " Item", ligne); frappe.ui.form.on(dt, entete); });
})();
