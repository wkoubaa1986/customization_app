// La file des factures capturées en caisse, à transformer en vraies Purchase
// Invoice. Le bouton préremplit la facture (fournisseur, n°, date) ; le
// rattachement (lien, justificatif copié, statut) se fait côté serveur par
// l'appariement (fournisseur, n°) — hooks Purchase Invoice de caisse_depenses.
frappe.ui.form.on("Facture Achat a Saisir", {
	refresh(frm) {
		if (frm.doc.statut === "À saisir" && !frm.doc.purchase_invoice) {
			frm.add_custom_button(__("➕ Créer la facture d'achat"), () => {
				frappe.route_options = {
					supplier: frm.doc.supplier || undefined,
					bill_no: frm.doc.numero_facture || undefined,
					bill_date: frm.doc.date_facture || undefined,
				};
				frappe.new_doc("Purchase Invoice");
			}).addClass("btn-primary");
		}
		if (frm.doc.journal_entry) {
			frm.add_custom_button(__("Écriture d'avance"), () =>
				frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry));
		}
	},
});
