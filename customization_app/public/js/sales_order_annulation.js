// Annulation d'une commande : la cascade est SERVEUR (annulation_commande.py +
// Server Script « cancel sales order payment ») — BL annulés puis supprimés
// avec la gymnastique du magasin désactivé, échéanciers supprimés, calendrier
// remis en attente, paiements/écritures supprimés.
//
// Sans ceci, frappe affiche « Annuler tous les documents » (BL, échéancier…)
// et son « Tout annuler » annule ces pièces AVANT la cascade : pas de
// réactivation du magasin désactivé (échec sur la validation SLE), pas de
// transfert du stock repris, pas de suppression. On retire donc de ce dialogue
// les doctypes que la cascade gère déjà : le bouton Annuler déroule
// directement l'annulation, qui fait tout.
//
// En `refresh` et pas en `onload` : sales_order.js d'ERPNext RÉASSIGNE
// frm.ignore_doctypes_on_cancel_all dans son onload, une fusion posée avant
// serait écrasée.
frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		const geres_par_la_cascade = [
			"Delivery Note",
			"Maintenance Schedule",
			"Payment Entry",
			"Journal Entry",
		];
		frm.ignore_doctypes_on_cancel_all = [...new Set([
			...(frm.ignore_doctypes_on_cancel_all || []),
			...geres_par_la_cascade,
		])];
	},
});
