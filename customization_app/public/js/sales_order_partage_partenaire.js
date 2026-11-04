// Commande en litige : la partager avec le partenaire (Economiq) la lui rend visible et la fait compter
// DIRECTEMENT au Bilan Vente (partage_commande_partenaire.py, décision du 04/11/2026). Boutons réservés à
// System Manager / Accounts Manager ; un partage venu de la tâche partenaire ne se retire pas ici.
frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 1) return;
		const methode = (nom) => "customization_app.partage_commande_partenaire." + nom;
		frappe
			.call({ method: methode("etat_partage"), args: { sales_order: frm.doc.name } })
			.then(({ message: m }) => {
				if (!m) return;
				if (m.etat !== "aucun") {
					frm.dashboard.add_comment(
						__("Commande partagée avec le partenaire : elle compte dans le Bilan Vente."),
						"blue",
						true
					);
				}
				if (!m.peut_modifier) return;
				const agir = (action) =>
					frappe
						.call({ method: methode(action), args: { sales_order: frm.doc.name }, freeze: true })
						.then(() => frm.reload_doc());
				if (m.etat === "aucun") {
					frm.add_custom_button(
						__("Partager avec le partenaire"),
						() =>
							frappe.confirm(
								__("Partager cette commande avec le partenaire ? Elle lui sera visible et comptera dans le Bilan Vente."),
								() => agir("partager")
							),
						__("Bilan partenaire")
					);
				} else if (m.etat === "manuel") {
					frm.add_custom_button(
						__("Retirer le partage"),
						() =>
							frappe.confirm(
								__("Retirer le partage ? La commande ne comptera plus dans le Bilan Vente (mois non validés)."),
								() => agir("retirer")
							),
						__("Bilan partenaire")
					);
				}
			});
	},
});
