// Fiche Sales Order : le bordereau Aramex se demande à Aramex, il ne se recopie plus.
//
// Boutons dans le groupe « 🚚 Aramex », selon l'état rendu par aramex_expedition.etat
// (aucun appel externe au chargement de la fiche) :
//   - « Créer le bordereau » : commande soumise, en livraison Aramex, sans bordereau,
//     création activée dans Config Livraison Aramex. Ouvre le dialogue PARTAGÉ
//     (aramex_dialogue.js — le même que dans « Ma journée ») puis crée.
//   - « Manifeste » : la sélection des colis à remettre au coursier.
//   - « Étiquette » : ouvre le PDF attaché, ou le redemande à Aramex (PrintLabel).
//   - « Mettre en attente » : responsables seulement, commentaire obligatoire.
frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        // Brouillon compris : le colis part souvent avant la validation de la commande web.
        if (frm.is_new() || frm.doc.docstatus === 2) return;
        frappe.call({
            method: "customization_app.aramex_expedition.etat",
            args: { commande: frm.doc.name },
            callback: (r) => {
                const e = r.message || {};
                if (!e.api_active || !e.aramex) return;
                const groupe = __("🚚 Aramex");
                if (!e.bordereau && e.creation_active) {
                    frm.add_custom_button(__("Créer le bordereau"), () => window.aramex_dialogue_creation({
                        titre: frm.doc.name,
                        preparer: { method: "customization_app.aramex_expedition.preparer", args: { commande: frm.doc.name } },
                        creer: { method: "customization_app.aramex_expedition.creer_bordereau", args: { commande: frm.doc.name } },
                        on_success: () => frm.reload_doc(),
                    }), groupe);
                }
                frm.add_custom_button(__("Manifeste"), () => window.open("/manifeste-aramex", "_blank"), groupe);
                if (e.bordereau) {
                    frm.add_custom_button(__("Étiquette"), () => aramex_etiquette(frm, e), groupe);
                    if (e.peut_attente) {
                        frm.add_custom_button(__("Mettre en attente"), () => aramex_attente(frm, e), groupe);
                    }
                }
            },
        });
    },
});

function aramex_etiquette(frm, e) {
    if (e.etiquette) {
        window.open(e.etiquette, "_blank");
        return;
    }
    frappe.call({
        method: "customization_app.aramex_expedition.etiquette",
        args: { commande: frm.doc.name },
        freeze: true,
        freeze_message: __("Demande de l'étiquette à Aramex…"),
        callback: (r) => {
            const m = r.message || {};
            const url = m.etiquette || m.etiquette_url;
            if (url) window.open(url, "_blank");
            frm.reload_doc();
        },
    });
}

function aramex_attente(frm, e) {
    frappe.prompt(
        [{ fieldtype: "Small Text", fieldname: "commentaire", label: __("Motif de la mise en attente"), reqd: 1 }],
        (v) => {
            frappe.call({
                method: "customization_app.aramex_expedition.mettre_en_attente",
                args: { commande: frm.doc.name, commentaire: v.commentaire },
                freeze: true,
                callback: () => {
                    frappe.show_alert({ message: __("Colis {0} mis en attente chez Aramex.", [e.bordereau]), indicator: "orange" });
                    frm.reload_doc();
                },
            });
        },
        __("Mettre le colis {0} en attente", [e.bordereau]),
        __("Confirmer")
    );
}
