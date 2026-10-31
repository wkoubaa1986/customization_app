/**
 * Facture d'achat : la date de comptabilisation suit la date de la facture du fournisseur (demande 31/10/2026).
 * Dès que « Date de la facture du fournisseur » est renseignée, la comptabilisation prend cette date (la case
 * « Modifier la date et l'heure » se coche pour qu'ERPNext la garde). L'utilisateur peut encore la changer après.
 * Même règle côté serveur : customization_app.achats_dates.suivre_date_facture.
 */
frappe.ui.form.on("Purchase Invoice", {
    bill_date(frm) {
        if (frm.doc.docstatus !== 0 || !frm.doc.bill_date || frm.doc.posting_date === frm.doc.bill_date) return;
        frm.set_value("set_posting_time", 1);
        frm.set_value("posting_date", frm.doc.bill_date);
    },
});
