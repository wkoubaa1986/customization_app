/* Dialogue partagé par Relance et le rapport de caisse : régulariser un chèque impayé par une ou
   plusieurs lignes (espèces, redépôt, nouveau chèque, traite, virement, carte) — ou l'abandonner
   en perte de non paiement (écriture de journal) — sans toucher au
   paiement d'origine. */
frappe.provide('customization_app');
customization_app.encaisser_impaye = function ({client, piece, on_success} = {}) {
    const api = 'customization_app.caisse_impayes';
    const MODES = ['Espèces', 'Redépôt du même chèque', 'Nouveau chèque', 'Traite bancaire', 'Virement', 'Carte de crédit', 'Perte de non paiement'];
    const PIECE_LABEL = {
        'Nouveau chèque': __('N° du nouveau chèque'), 'Traite bancaire': __('N° de traite'),
        'Virement': __('Référence du virement'), 'Carte de crédit': __('N° ticket TPE'),
    };
    let lignes = [], generation = 0, en_cours = false;
    const d = new frappe.ui.Dialog({
        title: __('Régulariser un chèque impayé'),
        size: 'large',
        fields: [
            {fieldname: 'client', label: __('Client'), fieldtype: 'Link', options: 'Customer', reqd: 1,
                onchange: charger},
            {fieldname: 'piece', label: __('Pièce impayée'), fieldtype: 'Select', options: [], reqd: 1,
                onchange: () => { choisir(); }},
            {fieldname: 'cb', fieldtype: 'Column Break'},
            {fieldname: 'restant', label: __('Restant sur la pièce'), fieldtype: 'Currency', read_only: 1},
            {fieldname: 'total', label: __('Total des lignes'), fieldtype: 'Currency', read_only: 1},
            {fieldname: 'sb', fieldtype: 'Section Break', label: __('Règlements (une ligne par mode ; fractionnez librement)')},
            {fieldname: 'paiements', fieldtype: 'Table', label: __('Règlements'), cannot_add_rows: false,
                in_place_edit: true, data: [], get_data: () => d.fields_dict.paiements.df.data,
                fields: [
                    {fieldname: 'mode', label: __('Le client a…'), fieldtype: 'Select', options: MODES.join('\n'),
                        default: 'Espèces', in_list_view: 1, reqd: 1, columns: 3},
                    {fieldname: 'montant', label: __('Montant'), fieldtype: 'Currency', in_list_view: 1, reqd: 1, columns: 2},
                    {fieldname: 'n_piece', label: __('N° / référence'), fieldtype: 'Data', in_list_view: 1, columns: 2,
                        description: __('N° du nouveau chèque ou de la traite, référence du virement, ticket TPE.')},
                    {fieldname: 'banque', label: __('Banque'), fieldtype: 'Data', in_list_view: 1, columns: 2},
                    {fieldname: 'date_piece', label: __('Date / échéance'), fieldtype: 'Date', in_list_view: 1, columns: 1},
                    {fieldname: 'photo', label: __('Photo de la pièce'), fieldtype: 'Attach'},
                ]},
            {fieldname: 'info', fieldtype: 'HTML'},
        ],
        primary_action_label: __('Régulariser et valider'),
        async primary_action(values) {
            if (en_cours) return;
            const ligne = ligne_choisie();
            const rows = (values.paiements || []).filter(r => r.mode || r.montant);
            const total = rows.reduce((s, r) => s + (flt(r.montant) || 0), 0);
            if (!ligne || !rows.length || !(total > 0) || total > flt(ligne.restant) + 0.0005) {
                frappe.msgprint(__('Vérifiez la pièce, les lignes et le total (restant : {0}).', [ligne ? ligne.restant : '—']));
                return;
            }
            for (const r of rows) {
                if (['Nouveau chèque', 'Traite bancaire'].includes(r.mode) && !(r.n_piece || '').trim()) {
                    frappe.msgprint(__('Le numéro est obligatoire pour « {0} ».', [r.mode])); return;
                }
                if (r.mode === 'Nouveau chèque' && !(r.banque || '').trim()) { frappe.msgprint(__('La banque du nouveau chèque est obligatoire.')); return; }
                if (r.mode === 'Traite bancaire' && !r.date_piece) { frappe.msgprint(__("L'échéance de la traite est obligatoire.")); return; }
            }
            en_cours = true;
            d.disable_primary_action();
            try {
                const r = await frappe.call({method: api + '.encaisser', freeze: true, args: {
                    client: values.client, piece: values.piece,
                    paiements: rows.map(x => ({mode: x.mode, montant: x.montant, n_piece: x.n_piece, banque: x.banque,
                                               date_piece: x.date_piece, photo: x.photo})),
                }});
                if (!r.message) return;
                d.hide();
                frappe.show_alert({message: __('Régularisation enregistrée : {0} ({1})', [r.message.paiements.join(', '), r.message.references.join(' ; ')]),
                                   indicator: 'green'});
                if (on_success) on_success();
            } finally {
                en_cours = false;
                d.enable_primary_action();
            }
        },
    });
    function ligne_choisie() { return lignes.find(x => x.name === d.get_value('piece')); }
    function poser_lignes(rows) {
        const grid = d.fields_dict.paiements;
        grid.df.data = rows;
        grid.grid.refresh();
        maj_total();
    }
    function maj_total() {
        const rows = d.fields_dict.paiements.df.data || [];
        d.set_value('total', rows.reduce((s, r) => s + (flt(r.montant) || 0), 0));
    }
    function choisir() {
        const ligne = ligne_choisie();
        d.set_value('restant', ligne ? ligne.restant : 0);
        poser_lignes(ligne ? [{mode: 'Espèces', montant: ligne.restant}] : []);
        const esc = frappe.utils.escape_html;
        d.fields_dict.info.$wrapper.html(`<p class="text-muted">${__("« Perte de non paiement » : la créance est abandonnée (charge), à saisir seule pour le reste de la pièce.")} ${__(
            "Espèces → entrée de caisse. Redépôt / nouveau chèque / traite → la pièce entre en portefeuille et part sur un bordereau. Virement / carte → directement sur la banque. Le paiement impayé d’origine n’est jamais modifié ; chaque ligne le cite (« Impayé d’origine »). Si une pièce de régularisation revient impayée, son transfert est annulé et la créance réapparaît.")}${
            ligne && ligne.numero ? ' <b>' + __('Chèque impayé n° {0} ({1}).', [esc(ligne.numero), esc(ligne.banque || '—')]) + '</b>' : ''}</p>`);
    }
    async function charger() {
        const numero = ++generation;
        lignes = [];
        d.set_df_property('piece', 'options', []);
        d.set_value('piece', '');
        poser_lignes([]);
        const customer = d.get_value('client');
        if (!customer) return;
        const r = await frappe.call({method: api + '.pieces', args: {client: customer}});
        if (numero !== generation) return;
        lignes = r.message || [];
        d.set_df_property('piece', 'options', lignes.map(x => ({value: x.name,
            label: `${x.numero ? __('Chèque') + ' ' + x.numero + (x.banque ? ' (' + x.banque + ')' : '') + ' — ' : ''}${Number(x.restant).toFixed(3)} TND — ${x.name}`})));
        const choisie = lignes.find(x => x.name === piece) || lignes[0];
        d.set_value('piece', choisie?.name || '');
        choisir();
        if (!lignes.length) frappe.msgprint(__('Aucun chèque impayé restant pour ce client.'));
    }
    d.show();
    // le total suit la saisie dans la grille
    d.$wrapper.on('change input', '.grid-body input', () => setTimeout(maj_total, 50));
    if (client) d.set_value('client', client);
};
