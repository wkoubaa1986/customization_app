/**
 * « 🔧 Machine reprise » — le technicien n'a pas pu finir chez le client et repart avec la machine (23/11/2026).
 *
 * UN dialogue, partagé par « Ma journée » (carte de l'intervention) et par la FICHE de la tâche (bouton orange,
 * demande du même jour : « et l'équivalent directement sur la tâche existe ? ») : une phrase sur le problème, puis la
 * photo de la machine — c'est son dépôt qui fait tout (`reparation_osmoseur.reprendre_machine` : dossier de
 * réparation créé, intervention fermée avec la raison « Machine reprise à l'atelier »). Le post-it et la garantie se
 * font au magasin, à la réception.
 */
const TMR_LABEL = "🔧 Machine reprise";
const TMR_LABEL_DOSSIER = "🔧 Dossier de réparation";
const TMR_TYPES = ["Entretien", "Réparation", "Visite"];
const TMR_RAISON = "Machine reprise à l'atelier";
const TMR_ORANGE = "#ea580c";

/**
 * Le dialogue. `opts` : { tache, client, type, debut, apres(resultat) }.
 * `apres` est appelé quand le dossier est créé ; `resultat` = { machine, message, … }.
 */
window.machine_reprise_dialogue = function (opts) {
    const esc = frappe.utils.escape_html;
    const tache = opts.tache;
    const d = new frappe.ui.Dialog({
        title: "🔧 " + __("Machine reprise à l'atelier"),
        fields: [
            { fieldtype: "HTML", fieldname: "qui", options: `<div style="margin-bottom:6px"><b>${esc(opts.client || "")}</b>${opts.type ? " · " + esc(opts.type) : ""}${opts.debut ? " · " + esc(opts.debut) : ""}</div>
                <div class="text-muted" style="font-size:12px;margin-bottom:8px">${__("Vous ne pouvez pas finir sur place et vous repartez avec la machine. Un dossier de réparation est créé tout de suite ; au magasin, il restera à coller le post-it et à le photographier.")}</div>` },
            { fieldtype: "Small Text", fieldname: "probleme", label: __("Qu'est-ce qui ne va pas sur la machine ?"), reqd: 1 },
        ],
        primary_action_label: "📷 " + __("Photo de la machine et valider"),
        primary_action: (v) => {
            const probleme = (v.probleme || "").trim();
            if (probleme.length < 3) { frappe.msgprint(__("Dites en une phrase ce qui ne va pas sur la machine.")); return; }
            if (window.flotte_position_capturer) window.flotte_position_capturer("photo", "Tache de travail", tache);
            new frappe.ui.FileUploader({
                doctype: "Tache de travail", docname: tache, folder: "Home/Attachments",
                allow_multiple: false,
                restrictions: { allowed_file_types: ["image/*"] },
                on_success: (file) => frappe.call({
                    method: "customization_app.reparation_osmoseur.reprendre_machine",
                    args: { tache, photo: file.file_url, probleme },
                    freeze: true, freeze_message: __("Création du dossier de réparation…"),
                    callback: (r) => {
                        const m = r.message || {};
                        d.hide();
                        frappe.msgprint({ title: "🔧 " + (m.machine || ""), indicator: "orange", message: esc(m.message || "") });
                        if (opts.apres) opts.apres(m);
                    },
                }),
            });
        },
    });
    d.show();
    return d;
};

/** La reprise est-elle possible depuis cette fiche ? Même règle que le serveur (`refus_reprise`). */
function tmr_possible(frm) {
    const doc = frm.doc;
    return !frm.is_new() && doc.status === "Open" && TMR_TYPES.includes(doc.custom_type_dintervention)
        && !!doc.custom_client && !doc.machine_reparation && !(frappe.boot && frappe.boot.sous_traitant);
}

function tmr_orange($btn) {
    if (!$btn) return;
    $btn.removeClass("btn-default").css({ background: TMR_ORANGE, borderColor: TMR_ORANGE, color: "#fff", fontWeight: 600 });
}

frappe.ui.form.on("Tache de travail", {
    refresh(frm) {
        if (frm.is_new()) return;
        if (tmr_possible(frm)) {
            if (!frm.custom_buttons || !frm.custom_buttons[TMR_LABEL]) {
                frm.add_custom_button(TMR_LABEL, () => {
                    if (frm.is_dirty()) {
                        frappe.msgprint(__("Enregistrez d'abord vos modifications sur la tâche, puis cliquez de nouveau « Machine reprise »."));
                        return;
                    }
                    window.machine_reprise_dialogue({
                        tache: frm.doc.name, client: frm.doc.nom_client || frm.doc.custom_client,
                        type: frm.doc.custom_type_dintervention,
                        debut: frm.doc.starts_on ? String(frm.doc.starts_on).slice(11, 16) : "",
                        apres: () => frm.reload_doc(),
                    });
                });
            }
            const $btn = frm.custom_buttons[TMR_LABEL];
            tmr_orange($btn);
            if ($btn) $btn.attr("title", __("Vous ne pouvez pas finir sur place et vous repartez avec la machine : un dossier de réparation est créé"));
            return;
        }
        // Intervention fermée par une reprise : le chemin vers son dossier de réparation.
        if (frm.doc.status === "Cancelled" && frm.doc.raison_annulation === TMR_RAISON && frappe.model.can_read("Machine Reparation")) {
            frappe.db.get_value("Machine Reparation", { tache_origine: frm.doc.name }, ["name", "statut"]).then((r) => {
                const m = r.message || {};
                if (!m.name || (frm.custom_buttons && frm.custom_buttons[TMR_LABEL_DOSSIER])) return;
                tmr_orange(frm.add_custom_button(TMR_LABEL_DOSSIER, () =>
                    window.open("/app/reparation-osmoseurs?dossier=" + encodeURIComponent(m.name), "_blank")));
                frm.dashboard.set_headline(`🔧 ${__("Machine reprise à l'atelier")} — ${frappe.utils.escape_html(m.name)} · ${frappe.utils.escape_html(m.statut || "")}`);
            });
        }
    },
});
