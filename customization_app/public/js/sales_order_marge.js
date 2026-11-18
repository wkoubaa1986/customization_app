// Marge minimale (customization_app.marge_minimale) — commande client ET devis :
//  1. TEMPS RÉEL : dès qu'un article, une quantité, un prix ou une remise change sur une ligne, le formulaire
//     demande au serveur le prix plancher de la ligne et relève le prix sous les yeux de l'utilisateur.
//     Le hook serveur `validate` refait le même calcul à l'enregistrement (filet de sécurité).
//  2. Document EXONÉRÉ de TVA (aucune taxe incluse dans le prix) : le prix de liste TTC devient HT
//     (TTC ÷ (1 + TVA de l'article)) dès le choix de l'article ; un prix tapé à la main est gardé.
//  3. Mise en évidence des lignes relevées : bandeau orange sous le titre + fond orangé + puce « ⬆ marge ».
//  4. (18/11/2026) Le plancher est calculé pour TOUTE la commande d'un coup par le serveur
//     (marge_minimale.planchers_commande — le même calcul que l'enregistrement). Le formulaire applique ce qu'il
//     rend, ligne par ligne : « relever », « retablir », « oublier ». La RISTOURNE N'ENTRE PAS dans le calcul
//     (règle de l'utilisateur : elle se déduit après, une fois la marge assurée sur les prix). La marge raisonne
//     sur le prix HORS majoration carte, qui se refait par-dessus.
(() => {
    const FLAG = "custom_marge_corrigee", AVANT = "custom_prix_avant_marge", POSE = "custom_prix_plancher_pose", TOL = 0.0005, DELAI = 450;
    const minuteries = {};
    const DOCTYPES = ["Sales Order", "Quotation"];

    /** Le prix de la ligne SANS la majoration pour paiement par carte (elle se refait par-dessus, après la marge). */
    function prixHorsCarte(l) {
        const avant = flt(l.custom_prix_avant_carte), avec = flt(l.custom_prix_avec_carte);
        return avant && Math.abs(flt(l.rate) - avec) <= Math.max(0.005, Math.abs(avec) * 2e-5) ? avant : flt(l.rate);
    }

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
    function verifier(frm, cdn, delai = DELAI) {
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
                        price_list: frm.doc.selling_price_list, uom: l.uom, price_list_rate: l.price_list_rate, composants: composants(frm, l) },
                    taxes: (frm.doc.taxes || []).map((t) => ({ account_head: t.account_head, rate: t.rate, charge_type: t.charge_type, included_in_print_rate: t.included_in_print_rate })),
                    woocommerce_id: frm.doc.woocommerce_id || null,
                });
            } catch (e) { return; }
            const l2 = locals[cdt] && locals[cdt][cdn];
            if (!l2) return;
            // Document exonéré : le prix de liste passe en HT ; le prix de la ligne suit (remise de règle gardée
            // en %, prix tapé à la main gardé), puis le prix de liste déclenche une nouvelle vérification.
            if (rep && rep.prix_liste_ht != null && Math.abs(flt(l2.price_list_rate) - flt(rep.prix_liste_ht)) > TOL) {
                // Un prix relevé par la marge n'est pas un prix manuel : on repart du prix de liste, la marge
                // le relèvera à nouveau sur la bonne base (HT ou TTC).
                const releve = !!l2[FLAG];
                const manuel = !releve && !l2.pricing_rules && (flt(l2.discount_percentage) !== 0 || l2.margin_type);
                const rateAvant = flt(l2.rate);
                if (releve) { await frappe.model.set_value(cdt, cdn, FLAG, 0); await frappe.model.set_value(cdt, cdn, AVANT, 0); }
                await frappe.model.set_value(cdt, cdn, "price_list_rate", flt(rep.prix_liste_ht));
                if (manuel) await frappe.model.set_value(cdt, cdn, "rate", rateAvant);
                else if (releve) await frappe.model.set_value(cdt, cdn, "rate", flt(rep.prix_liste_ht));
                peindre(frm);
                return;
            }
            if (!rep) return;
            if (rep.plancher == null && rep.exonere) { if (l2[FLAG]) oublier(frm, l2); return; }
            // Document standard : un seul calcul pour toute la commande, côté serveur (le même que l'enregistrement).
            // Document exonéré : le plancher de la ligne, rendu par l'appel ci-dessus.
            if (!rep.exonere) { verifierCommande(frm); return; }
            const prix = prixHorsCarte(l2), plancher = flt(rep.plancher);
            const parNous = !!l2[FLAG] && (!flt(l2[POSE]) || Math.abs(prix - flt(l2[POSE])) <= TOL);
            const base = parNous && flt(l2[AVANT]) > 0 ? flt(l2[AVANT]) : prix;
            const cible = Math.max(base, plancher);
            if (cible > base + TOL) appliquer(frm, l2, { action: l2[FLAG] && Math.abs(prix - cible) <= TOL ? "rien" : "relever", cible, base }, rep.taux);
            else if (parNous && prix > base + TOL) appliquer(frm, l2, { action: "retablir", cible: base, base }, rep.taux);
            else if (l2[FLAG]) appliquer(frm, l2, { action: "oublier" }, rep.taux);
        }, DELAI);
    }

    /** Applique à une ligne ce que le calcul a décidé : {action, cible, base}. */
    function appliquer(frm, l, c, taux) {
        const prix = prixHorsCarte(l);
        if (c.action === "oublier") { if (l[FLAG]) oublier(frm, l); return; }
        if (c.action === "retablir") {
            // Le plancher est repassé sous le prix d'origine : ce prix revient.
            frappe.model.set_value(l.doctype, l.name, FLAG, 0);
            frappe.model.set_value(l.doctype, l.name, AVANT, 0);
            l[POSE] = 0;
            frappe.model.set_value(l.doctype, l.name, "rate", c.cible).then(() => {
                peindre(frm);
                frappe.show_alert({ indicator: "blue",
                    message: __("↩ {0} : le prix d’origine {1} revient (la marge minimale est respectée)",
                        [l.item_name || l.item_code, format_currency(c.cible, frm.doc.currency)]) }, 6);
            });
            return;
        }
        if (c.action !== "relever") { if (l[FLAG] && !flt(l[POSE])) l[POSE] = prix; return; }
        // Sous le plancher : on relève. set_value("rate") laisse ERPNext poser remise / marge et refaire les totaux.
        frappe.model.set_value(l.doctype, l.name, AVANT, c.base);
        frappe.model.set_value(l.doctype, l.name, FLAG, 1);
        l[POSE] = c.cible;
        if (Math.abs(prix - c.cible) <= TOL) { peindre(frm); return; }
        frappe.model.set_value(l.doctype, l.name, "rate", c.cible).then(() => {
            peindre(frm);
            frappe.show_alert({ indicator: "orange",
                message: __("⬆ {0} : prix relevé de {1} à {2} (marge minimale {3} %)",
                    [l.item_name || l.item_code, format_currency(c.base, frm.doc.currency), format_currency(c.cible, frm.doc.currency), taux]) }, 6);
        });
    }

    // Le calcul de TOUTE la commande (document standard) : une seule demande à la fois, la plus récente gagne.
    let minuterieCommande = null, tourCommande = 0;
    function verifierCommande(frm, delai = DELAI) {
        if (frm.doc.docstatus !== 0) return;
        clearTimeout(minuterieCommande);
        const moi = ++tourCommande;
        minuterieCommande = setTimeout(async () => {
            let rep;
            try { rep = await frappe.xcall("customization_app.marge_minimale.planchers_commande", { doc: frm.doc }); } catch (e) { return; }
            if (moi !== tourCommande || frm.doc.docstatus !== 0 || !rep || !rep.actif) return;
            const cdt = frm.doctype + " Item";
            (rep.lignes || []).forEach((c) => { const l = locals[cdt] && locals[cdt][c.name]; if (l) appliquer(frm, l, c, rep.taux); });
            peindre(frm);
        }, delai);
    }

    function oublier(frm, l) {
        frappe.model.set_value(l.doctype, l.name, FLAG, 0);
        frappe.model.set_value(l.doctype, l.name, AVANT, 0);
        l[POSE] = 0;
        peindre(frm);
    }

    function verifierTout(frm, delai) { (frm.doc.items || []).forEach((l) => verifier(frm, l.name, delai)); }

    const ligne = {};
    ["item_code", "qty", "uom", "conversion_factor", "warehouse", "rate", "price_list_rate", "discount_percentage",
     "discount_amount", "margin_rate_or_amount", "item_tax_template"].forEach((f) => { ligne[f] = (frm, cdt, cdn) => verifier(frm, cdn); });
    ligne.items_remove = (frm) => peindre(frm);
    const entete = {
        refresh(frm) { peindre(frm); },
        items_on_form_rendered(frm) { colorer(frm); },
        selling_price_list(frm) { verifierTout(frm); },
        ignore_pricing_rule(frm) { verifierTout(frm); },
        // Le modèle de taxes change (exonération ↔ standard) : ERPNext recharge les taxes par un appel serveur,
        // on repasse les lignes quand elles arrivent (événement `taxes`), et à deux reprises par sécurité.
        // Les taxes arrivent ~0,5 s après le choix : on repasse les lignes dès qu'elles ont changé (toutes les 150 ms
        // pendant 3 s), sans le délai habituel, puis une dernière fois par sécurité.
        taxes_and_charges(frm) {
            const avant = JSON.stringify((frm.doc.taxes || []).map((t) => [t.account_head, t.included_in_print_rate]));
            let n = 0;
            const tic = setInterval(() => {
                const apres = JSON.stringify((frm.doc.taxes || []).map((t) => [t.account_head, t.included_in_print_rate]));
                if (apres !== avant || ++n > 20) { clearInterval(tic); peindre(frm); verifierTout(frm, 50); }
            }, 150);
            setTimeout(() => { peindre(frm); verifierTout(frm); }, 3000);
        },
        taxes(frm) { peindre(frm); verifierTout(frm, 50); },
        customer(frm) { verifierTout(frm); },
        party_name(frm) { verifierTout(frm); },
    };
    DOCTYPES.forEach((dt) => { frappe.ui.form.on(dt + " Item", ligne); frappe.ui.form.on(dt, entete); });
    // Lignes de taxes ajoutées / retirées à la main : même effet qu'un changement de modèle.
    frappe.ui.form.on("Sales Taxes and Charges", {
        taxes_add(frm) { verifierTout(frm); }, taxes_remove(frm) { peindre(frm); verifierTout(frm); },
        included_in_print_rate(frm) { peindre(frm); verifierTout(frm); },
    });
})();
