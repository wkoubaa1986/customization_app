/**
 * Boutons de suivi des appels de confirmation, sur les commandes WEB.
 *
 * Une commande WooCommerce n'est expédiée qu'après confirmation téléphonique.
 * Chaque bouton horodate un appel resté sans réponse, une seule fois, et le 2e
 * n'apparaît qu'une fois le 1er enregistré.
 *
 * frappe.ui.form.on AJOUTE un gestionnaire, il n'écrase pas ceux des autres
 * apps ni les Client Scripts existants sur Sales Order — contrairement à
 * frappe.listview_settings, réassigné en entier par woocommerce_fusion.
 */

frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        // Discriminant WEB : woocommerce_id. Les 270 commandes WEB en ont un,
        // aucune commande saisie au Desk n'en a.
        if (!frm.doc.woocommerce_id || frm.doc.__islocal) return;

        const appels = [
            { rang: 1, champ: "custom_appel_1_sans_reponse", libelle: __("1er appel sans réponse") },
            { rang: 2, champ: "custom_appel_2_sans_reponse", libelle: __("2e appel sans réponse") },
        ];

        const groupe = __("Suivi des appels");

        for (const { rang, champ, libelle } of appels) {
            if (frm.doc[champ]) continue;
            // Séquentiel : pas de 2e appel tant que le 1er n'est pas posé.
            if (rang === 2 && !frm.doc[appels[0].champ]) continue;

            frm.add_custom_button(`📞 ${libelle}`, () => _enregistrer(frm, rang, libelle), groupe);
            break; // un seul bouton à la fois, celui de l'étape en cours
        }
    },
});

function _enregistrer(frm, rang, libelle) {
    frappe.confirm(
        __("Enregistrer un {0} maintenant ? Cette action est définitive.", [libelle]),
        () => {
            frappe.call({
                method: "customization_app.suivi_appels.enregistrer_appel",
                args: { commande: frm.doc.name, rang: rang },
                freeze: true,
                freeze_message: __("Enregistrement de l'appel..."),
                callback(r) {
                    if (!r.message) return;
                    frappe.show_alert(
                        { message: __("{0} enregistré.", [libelle]), indicator: "green" },
                        5
                    );
                    frm.reload_doc();
                },
            });
        }
    );
}
