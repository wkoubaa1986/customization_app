/* Dialogue partagé par Relance et le rapport de caisse : régulariser un chèque impayé
   (espèces, redépôt du même chèque, nouveau chèque) sans toucher au paiement d'origine. */
frappe.provide('customization_app');
customization_app.encaisser_impaye = function ({client, piece, on_success} = {}) {
    const api = 'customization_app.caisse_impayes';
    const MODES = ['Espèces', 'Redépôt du même chèque', 'Nouveau chèque'];
    let lignes = [], generation = 0, en_cours = false;
    const d = new frappe.ui.Dialog({
        title: __('Régulariser un chèque impayé'),
        fields: [
            {fieldname: 'client', label: __('Client'), fieldtype: 'Link', options: 'Customer', reqd: 1,
                onchange: charger},
            {fieldname: 'piece', label: __('Pièce impayée'), fieldtype: 'Select', options: [], reqd: 1,
                onchange: () => { choisir(); }},
            {fieldname: 'mode', label: __('Le client a…'), fieldtype: 'Select', options: MODES.join('\n'),
                default: MODES[0], reqd: 1, onchange: () => { rafraichir_mode(); }},
            {fieldname: 'montant', label: __('Montant'), fieldtype: 'Currency', reqd: 1},
            {fieldname: 'cb', fieldtype: 'Column Break'},
            {fieldname: 'n_cheque', label: __('N° du nouveau chèque'), fieldtype: 'Data',
                depends_on: "eval:doc.mode=='Nouveau chèque'", mandatory_depends_on: "eval:doc.mode=='Nouveau chèque'"},
            {fieldname: 'banque', label: __('Banque'), fieldtype: 'Data',
                depends_on: "eval:doc.mode=='Nouveau chèque'", mandatory_depends_on: "eval:doc.mode=='Nouveau chèque'"},
            {fieldname: 'date_cheque', label: __('Date du chèque'), fieldtype: 'Date',
                depends_on: "eval:doc.mode!='Espèces'"},
            {fieldname: 'photo', label: __('Photo du chèque'), fieldtype: 'Attach',
                depends_on: "eval:doc.mode!='Espèces'"},
            {fieldname: 'sb', fieldtype: 'Section Break'},
            {fieldname: 'info', fieldtype: 'HTML'},
        ],
        primary_action_label: __('Régulariser et valider'),
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
                frappe.show_alert({message: __('Régularisation enregistrée : {0} ({1})', [r.message.name, r.message.reference]),
                                   indicator: 'green'});
                if (on_success) on_success();
            } finally {
                en_cours = false;
                d.enable_primary_action();
            }
        },
    });
    function ligne_choisie() { return lignes.find(x => x.name === d.get_value('piece')); }
    function choisir() {
        const ligne = ligne_choisie();
        d.set_value('montant', ligne ? ligne.restant : 0);
        rafraichir_mode();
    }
    function rafraichir_mode() {
        const ligne = ligne_choisie();
        const mode = d.get_value('mode');
        const esc = frappe.utils.escape_html;
        let texte;
        if (mode === 'Espèces') {
            texte = __("Entrée de caisse : transfert « Chèques sans provision » → « Espèces », lié à la pièce d’origine. Le paiement du chèque impayé n’est pas modifié.");
        } else if (mode === 'Redépôt du même chèque') {
            texte = __("Le chèque n° {0} ({1}) revient en portefeuille « Chèques - A&S » : il apparaîtra dans les chèques à remettre et passera sur la banque à la remise. S’il revient impayé, le transfert est annulé et l’impayé d’origine reste.",
                [esc(ligne ? ligne.numero : '—'), esc(ligne ? ligne.banque : '—')]);
        } else {
            texte = __("Le nouveau chèque entre en portefeuille « Chèques - A&S » à la place de l’impayé n° {0} : même parcours qu’un chèque normal (remise, banque). La pièce d’origine reste, citée par le transfert.",
                [esc(ligne ? ligne.numero : '—')]);
        }
        d.fields_dict.info.$wrapper.html(`<p class="text-muted">${texte}</p>`);
    }
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
            label: `${x.numero ? __('Chèque') + ' ' + x.numero + (x.banque ? ' (' + x.banque + ')' : '') + ' — ' : ''}${Number(x.restant).toFixed(3)} TND — ${x.name}`})));
        const choisie = lignes.find(x => x.name === piece) || lignes[0];
        d.set_value('piece', choisie?.name || '');
        d.set_value('montant', choisie?.restant || 0);
        rafraichir_mode();
        if (!lignes.length) frappe.msgprint(__('Aucun chèque impayé restant pour ce client.'));
    }
    d.show();
    rafraichir_mode();
    if (client) d.set_value('client', client);
};
