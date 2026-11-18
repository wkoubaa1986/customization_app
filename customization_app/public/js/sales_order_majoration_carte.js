// Majoration « paiement par carte » (customization_app.majoration_carte) — commande client.
//
// La part réglée par « Carte de crédit » est majorée du taux réglé (2 %), et la majoration répartie sur les
// prix de tous les articles. Le calcul se fait au serveur, à l'enregistrement. Ici, UN bandeau, posé dans
// « Termes de paiement » juste au-dessus de l'échéancier, dit au vendeur :
//   - avant l'enregistrement : de combien la vente va augmenter et le montant EXACT à passer sur le terminal
//     (demandé au serveur, qui fait le même calcul que l'enregistrement, sur la commande telle qu'à l'écran) ;
//   - après : la majoration appliquée et le montant réglé par carte.
// Sans paiement par carte, ou si le taux réglé vaut 0 : rien ne s'affiche.
(() => {
    const MODE = "Carte de crédit", CLASSE = "so-majoration-carte", DELAI = 500;
    let minuterie = null, tour = 0;

    const nombre = (v) => `<span dir="ltr">${format_number(flt(v, 3), null, 3)}</span>`;
    const taux = (t) => String(flt(t)).replace(".", ",");
    const parCarte = (frm) => (frm.doc.payment_schedule || []).some((e) => e.mode_of_payment === MODE);

    function bandeau(frm) {
        const champ = frm.fields_dict.payment_schedule;
        if (!champ || !champ.$wrapper) return $();
        let $b = champ.$wrapper.prevAll("." + CLASSE);
        if (!$b.length) {
            $b = $(`<div class="${CLASSE}" style="display:none;margin:0 0 12px;padding:10px 14px;border-radius:8px;
                border:1px solid #bfdbfe;background:#eff6ff;color:#1e3a8a;font-size:13px;line-height:1.55"></div>`);
            $b.insertBefore(champ.$wrapper);
        }
        return $b;
    }

    function montrer(frm, html, couleur) {
        const $b = bandeau(frm);
        if (!html) { $b.hide().empty(); return; }
        const tons = couleur === "vert" ? ["#bbf7d0", "#f0fdf4", "#14532d"] : ["#bfdbfe", "#eff6ff", "#1e3a8a"];
        $b.css({ "border-color": tons[0], background: tons[1], color: tons[2] }).html(html).show();
    }

    function texte(e, enregistre) {
        if (!e || flt(e.majoration) <= 0) return "";
        if (enregistre) {
            return `💳 <b>Majoration carte de ${taux(e.taux)} % appliquée : + ${nombre(e.majoration)}</b>,
                répartie sur les prix de tous les articles.<br>
                Montant réglé par carte : <b>${nombre(e.carte_apres)}</b> — total de la commande : ${nombre(e.total_apres)}.`;
        }
        return `💳 <b>Paiement par carte : + ${taux(e.taux)} %</b> sur la part réglée par carte.<br>
            Majoration : <b>+ ${nombre(e.majoration)}</b> — montant à passer sur le terminal :
            <b style="font-size:15px">${nombre(e.carte_apres)}</b> (au lieu de ${nombre(e.carte_avant)}).<br>
            Les prix des articles seront augmentés d’autant à l’enregistrement (total : ${nombre(e.total_avant)} → ${nombre(e.total_apres)}).`;
    }

    /** Commande validée : ce qu'elle porte, sans rien demander au serveur. */
    function afficherEnregistre(frm) {
        const m = flt(frm.doc.custom_majoration_carte, 3);
        if (m <= 0) { montrer(frm, ""); return; }
        const carte = (frm.doc.payment_schedule || []).filter((e) => e.mode_of_payment === MODE)
            .reduce((s, e) => s + flt(e.payment_amount), 0);
        montrer(frm, texte({ majoration: m, taux: frm.doc.custom_majoration_carte_taux, carte_apres: carte,
            total_apres: frm.doc.rounded_total || frm.doc.grand_total }, true), "vert");
    }

    function actualiser(frm, delai = DELAI) {
        if (frm.doctype !== "Sales Order") return;
        if (frm.doc.docstatus !== 0) { afficherEnregistre(frm); return; }
        if (!parCarte(frm) && flt(frm.doc.custom_majoration_carte) <= 0) { montrer(frm, ""); return; }
        clearTimeout(minuterie);
        const moi = ++tour;
        minuterie = setTimeout(async () => {
            let e;
            try {
                e = await frappe.xcall("customization_app.majoration_carte.apercu", { doc: frm.doc });
            } catch (err) { return; }
            if (moi !== tour || frm.doc.docstatus !== 0) return;        // une demande plus récente est partie
            if (!e || flt(e.majoration) <= 0) {
                // La carte vient d'être retirée d'une commande majorée : les prix reviendront à l'enregistrement.
                montrer(frm, e && e.change && flt(frm.doc.custom_majoration_carte) > 0
                    ? `💳 Plus de paiement par carte : la majoration de ${nombre(frm.doc.custom_majoration_carte)}
                       sera retirée à l’enregistrement, les articles retrouveront leurs prix.` : "");
                return;
            }
            // `change` faux : la commande à l'écran porte déjà exactement cette majoration.
            montrer(frm, texte(e, !e.change), e.change ? "bleu" : "vert");
        }, delai);
    }

    frappe.ui.form.on("Sales Order", {
        refresh(frm) { actualiser(frm, 50); },
        payment_terms_template(frm) { actualiser(frm, 900); },          // l'échéancier arrive par un appel serveur
        discount_amount(frm) { actualiser(frm); },
        additional_discount_percentage(frm) { actualiser(frm); },
        taxes_and_charges(frm) { actualiser(frm, 1500); },
    });
    frappe.ui.form.on("Payment Schedule", {
        mode_of_payment(frm) { actualiser(frm); },
        payment_amount(frm) { actualiser(frm); },
        invoice_portion(frm) { actualiser(frm); },
        payment_schedule_add(frm) { actualiser(frm); },
        payment_schedule_remove(frm) { actualiser(frm); },
    });
    const ligne = {};
    ["item_code", "qty", "rate", "discount_percentage", "discount_amount", "price_list_rate"]
        .forEach((f) => { ligne[f] = (frm) => actualiser(frm, 900); });
    ligne.items_remove = (frm) => actualiser(frm, 900);
    frappe.ui.form.on("Sales Order Item", ligne);
})();
