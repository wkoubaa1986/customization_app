// La file des factures capturées en caisse, à transformer en vraies Purchase
// Invoice. Le bouton préremplit la facture (fournisseur, n°, date) ; le
// rattachement (lien, justificatif copié, statut) se fait côté serveur par
// l'appariement (fournisseur, n°) — hooks Purchase Invoice de caisse_depenses.
frappe.ui.form.on("Facture Achat a Saisir", {
	refresh(frm) {
		if (frm.doc.statut === "À saisir" && !frm.doc.purchase_invoice) {
			if (frm.doc.est_bl && !frm.doc.purchase_order) {
				// Un BL devient une COMMANDE d'achat (décision utilisateur
				// 24/08) : à sa soumission, l'avance de caisse devient un
				// paiement lié à la commande ; la facture se créera ensuite
				// depuis une ou plusieurs commandes.
				frm.add_custom_button(__("➕ Créer la commande d'achat"), () => {
					frappe.route_options = {
						supplier: frm.doc.supplier || undefined,
						custom_fiche_caisse: frm.doc.name,
					};
					frappe.new_doc("Purchase Order");
				}).addClass("btn-primary");
			} else if (!frm.doc.est_bl) {
				frm.add_custom_button(__("➕ Créer la facture d'achat"), () => {
					frappe.route_options = {
						supplier: frm.doc.supplier || undefined,
						bill_no: frm.doc.numero_facture || undefined,
						bill_date: frm.doc.date_facture || undefined,
					};
					frappe.new_doc("Purchase Invoice");
				}).addClass("btn-primary");
			}
		}
		if (frm.doc.purchase_order) {
			frm.add_custom_button(__("Commande d'achat"), () =>
				frappe.set_route("Form", "Purchase Order", frm.doc.purchase_order));
		}
		if (frm.doc.journal_entry) {
			frm.add_custom_button(__("Écriture d'avance"), () =>
				frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry));
		}
		// Erreur de saisie fréquente : « Espèces » coché alors que la facture n'est
		// pas payée. La bascule garde la fiche et la facture ; l'avance de caisse
		// (ou le paiement né de l'avance) disparaît, la facture redevient due.
		if (frm.doc.mode_paiement && frm.doc.mode_paiement !== "Pas payé"
			&& (frm.doc.journal_entry || frm.doc.journal_entries || frm.doc.payment_entry || frm.doc.payment_entries)) {
			frm.add_custom_button(__("🔁 Basculer en « Pas payé »"), () =>
				customization_app.basculer_fiche_pas_paye(frm.doc.name, frm.doc.mode_paiement, () => frm.reload_doc()));
		}
	},
});
