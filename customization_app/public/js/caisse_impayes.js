/* Dialogue partagé par Relance et le rapport de caisse. */
frappe.provide('customization_app');
customization_app.encaisser_impaye = function ({client, piece, on_success} = {}) {
    const api = 'customization_app.caisse_impayes';
    let lignes = [], generation = 0, en_cours = false;
    const d = new frappe.ui.Dialog({
        title: __('Encaisser un chèque impayé en espèces'),
        fields: [
            {fieldname: 'client', label: __('Client'), fieldtype: 'Link', options: 'Customer', reqd: 1,
                onchange: charger},
            {fieldname: 'piece', label: __('Pièce impayée'), fieldtype: 'Select', options: [], reqd: 1,
                onchange: () => {
                    const ligne = lignes.find(x => x.name === d.get_value('piece'));
                    d.set_value('montant', ligne ? ligne.restant : 0);
                }},
            {fieldname: 'montant', label: __('Montant reçu en espèces'), fieldtype: 'Currency', reqd: 1},
            {fieldname: 'info', fieldtype: 'HTML', options: '<p>' +
                __('Le règlement crée un transfert interne lié à la pièce d’origine. Saisissez uniquement les espèces effectivement reçues.') + '</p>'},
        ],
        primary_action_label: __('Encaisser et valider'),
        async primary_action(values) {
            if (en_cours) return;
            const ligne = lignes.find(x => x.name === values.piece);
            if (!ligne || !(values.montant > 0) || values.montant > ligne.restant) {
                frappe.msgprint(__('Vérifiez la pièce et le montant restant.'));
                return;
            }
            en_cours = true;
            d.disable_primary_action();
            try {
                const r = await frappe.call({method: api + '.encaisser', args: values, freeze: true});
                if (!r.message) return;
                d.hide();
                frappe.show_alert({message: __('Règlement enregistré : {0}', [r.message.name]), indicator: 'green'});
                if (on_success) on_success();
            } finally {
                en_cours = false;
                d.enable_primary_action();
            }
        },
    });
    async function charger() {
        const numero = ++generation;
        lignes = [];
        d.set_df_property('piece', 'options', []);
        d.set_value('piece', '');
        d.set_value('montant', 0);
        const customer = d.get_value('client');
        if (!customer) return;
        const r = await frappe.call({method: api + '.pieces', args: {client: customer}});
        if (numero !== generation) return;
        lignes = r.message || [];
        d.set_df_property('piece', 'options', lignes.map(x => ({value: x.name,
            label: `${x.name} — ${Number(x.restant).toFixed(3)} TND`} )));
        const choisie = lignes.find(x => x.name === piece) || lignes[0];
        d.set_value('piece', choisie?.name || '');
        d.set_value('montant', choisie?.restant || 0);
        if (!lignes.length) frappe.msgprint(__('Aucun chèque impayé restant pour ce client.'));
    }
    d.show();
    if (client) d.set_value('client', client);
};
