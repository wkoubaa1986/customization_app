/**
 * « ✅ Terminer mon intervention » sur la fiche Tâche de travail.
 *
 * Le technicien — ou le partenaire — mène sa tâche au bout : commande validée,
 * bon de livraison validé, tâche fermée. Tout se décide CÔTÉ SERVEUR
 * (customization_app.cloture_partenaire) : l'écran ne fait que demander.
 *
 * Le bouton n'apparaît que si la tâche est AFFECTÉE à l'utilisateur connecté,
 * et le serveur le revérifie — un bouton caché n'est pas une sécurité.
 */

frappe.ui.form.on("Tache de travail", {
    refresh(frm) {
        if (frm.is_new() || frm.doc.status !== "Open") return;
        frappe.call({
            method: "customization_app.cloture_partenaire.peut_cloturer",
            args: { tache: frm.doc.name },
            callback: (r) => {
                const e = r.message || {};
                if (!e.mienne) return;
                frm.add_custom_button(__("✅ Terminer mon intervention"),
                    () => _dialogue(frm, e)).addClass("btn-primary");
            },
        });
    },
});

function _dialogue(frm, etat) {
    const esc = frappe.utils.escape_html;
    const quoi = etat.commande
        ? `<div>Commande <b>${esc(etat.commande)}</b> — ${
             etat.commande_validee ? "déjà validée" : "sera <b>validée</b>"}</div>
           <div>Bon de livraison — ${etat.bon_livraison
             ? `<b>${esc(etat.bon_livraison)}</b>, sera <b>validé</b>`
             : "sera <b>créé puis validé</b>"}</div>`
        : `<div>Aucune commande liée : seule l'intervention sera fermée.
             Le bon de livraison de main d'œuvre reste produit par « Générer BL ».</div>`;

    const d = new frappe.ui.Dialog({
        title: __("Terminer l'intervention"),
        fields: [
            { fieldtype: "HTML", fieldname: "quoi",
              options: `<div style="padding:10px 12px;border-radius:9px;background:#e0f2fe;
                          color:#075985;font-size:12.5px">${quoi}
                          <div style="margin-top:6px">⚠️ La validation du bon de livraison
                          <b>sort le stock</b>.</div></div>` },
            { fieldtype: "Small Text", fieldname: "rapport_visite",
              label: __("Compte rendu (facultatif)"),
              default: frm.doc.rapport_visite || "" },
        ],
        primary_action_label: __("Terminer"),
        primary_action: (v) => frappe.call({
            method: "customization_app.cloture_partenaire.cloturer",
            args: { tache: frm.doc.name, rapport_visite: v.rapport_visite },
            freeze: true,
            freeze_message: __("Validation en cours…"),
            callback: (r) => {
                const m = r.message || {};
                d.hide();
                frappe.msgprint({
                    title: __("Intervention terminée"),
                    indicator: "green",
                    message: (m.etapes || []).map((x) =>
                        `<div><b>${esc(x.quoi)}</b> : ${esc(x.doc)} — ${esc(x.etat)}</div>`).join(""),
                });
                frm.reload_doc();
            },
        }),
    });
    d.show();
}
