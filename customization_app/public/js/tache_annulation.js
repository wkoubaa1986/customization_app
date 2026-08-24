// Annulation d'une tâche de travail : dialogue de confirmation quand une
// commande est liée, puis cascade côté serveur (annulation_tache.py).
//
// Chargé GLOBALEMENT (app_include_js) : « Tache de travail » est un DocType
// custom et doctype_js est ignoré pour les doctypes custom (FormMeta.add_code
// sort immédiatement) — même piège documenté dans bank_retenue_sync/hooks.py.
// Le fichier ne fait qu'enregistrer un frappe.ui.form.on, inerte ailleurs.
//
// Le déclencheur est le PASSAGE du statut à « Cancelled » lors d'une
// sauvegarde du formulaire — pas une simple valeur déjà en base : une tâche
// annulée qu'on ré-enregistre (correction d'un champ) ne repose pas la
// question. La cascade n'est jamais lancée sans le « Continuer » explicite.

frappe.ui.form.on("Tache de travail", {
	onload(frm) {
		frm.__statut_charge = frm.doc.status;
	},

	after_save(frm) {
		frm.__statut_charge = frm.doc.status;
	},

	before_save(frm) {
		if (frm.__annulation_geree) {
			frm.__annulation_geree = false;
			return;
		}
		const bascule =
			frm.doc.status === "Cancelled" &&
			frm.__statut_charge !== "Cancelled" &&
			!frm.doc.__islocal &&
			frm.doc.commande_client;
		if (!bascule) return;

		// On bloque CETTE sauvegarde : la décision passe par le dialogue.
		frappe.validated = false;

		frappe.call({
			method: "customization_app.annulation_tache.impact_annulation",
			args: { tache: frm.doc.name, commande: frm.doc.commande_client },
		}).then(({ message: m }) => {
			if (!m) return;

			// La sauvegarde reprend son cours normal (statut Cancelled compris),
			// avec ou sans cascade ensuite.
			const enregistrer = (apres) => {
				frm.__annulation_geree = true;
				return frm.save().then(apres || (() => {}));
			};

			if (m.cas === "cascade") {
				const lignes = [
					__("La commande {0} ({1} DT — {2}) va être <b>annulée</b> avec la tâche :", [
						`<b>${m.commande}</b>`,
						format_number(m.total, null, 3),
						frappe.utils.escape_html(m.client || ""),
					]),
					m.bls.length
						? __("• BL {0} : annulé puis supprimé", [m.bls.join(", ")])
						: __("• aucun BL lié"),
					__("• {0} paiement(s)/écriture(s) supprimé(s)", [m.nb_paiements]),
					m.factures.length
						? __("• facture {0} : annulée puis supprimée", [m.factures.join(", ")])
						: __("• aucune facture liée"),
					__("• mention « Commande annulée avec tâche {0} » affichée en haut de la commande", [
						frm.doc.name,
					]),
				];
				frappe.confirm(
					lignes.join("<br>"),
					// Oui : la tâche est enregistrée annulée, PUIS la cascade tourne.
					() =>
						enregistrer(() =>
							frappe
								.call({
									method: "customization_app.annulation_tache.annuler_commande_de_tache",
									args: { tache: frm.doc.name },
									freeze: true,
									freeze_message: __("Annulation de la commande…"),
								})
								.then(({ message: r }) => {
									frappe.msgprint({
										title: __("Commande annulée"),
										indicator: "green",
										message: __(
											"Commande {0} annulée — BL supprimé(s) : {1} — {2} paiement(s) supprimé(s) — facture(s) : {3}.",
											[
												r.commande,
												r.bls_supprimes.join(", ") || __("aucun"),
												r.paiements_supprimes,
												r.factures_supprimees.join(", ") || __("aucune"),
											]
										),
									});
									frm.reload_doc();
								})
						),
					// Non : rien n'est enregistré, le statut revient à sa valeur chargée.
					() => frm.set_value("status", frm.__statut_charge)
				);
				return;
			}

			// Pas de cascade possible : la tâche s'annule quand même, avec le message
			// qui explique pourquoi la commande n'est pas touchée.
			const MESSAGES = {
				plusieurs_bl: __(
					"La commande {0} a plusieurs BL : rien n'est annulé sur la commande.",
					[m.commande]
				),
				bl_different: __(
					"La commande {0} a un BL d'un montant différent ({1} DT pour une commande de {2} DT) : rien n'est annulé sur la commande.",
					[m.commande, format_number(m.total_bl, null, 3), format_number(m.total, null, 3)]
				),
				paiement_partage: __(
					"Un paiement de la commande {0} est partagé avec une autre pièce ({1}) : rien n'est annulé sur la commande.",
					[m.commande, (m.pieces_partagees || []).join(", ")]
				),
				brouillon: __("La commande {0} est en brouillon : rien n'est annulé.", [m.commande]),
				deja_annulee: __("La commande {0} est déjà annulée.", [m.commande]),
				commande_introuvable: __("La commande {0} est introuvable.", [m.commande]),
			};
			const texte = MESSAGES[m.cas];
			if (texte) frappe.msgprint({ message: texte, indicator: "orange" });
			enregistrer();
		});
	},
});
