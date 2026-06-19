// Item — customization_app
// 1) Désactive la case "Sync avec WooCommerce" tant qu'aucune image n'est présente
//    (impossible de cocher la sync sans image).
// 2) Bouton "Prix de vente" : popup pour saisir / mettre à jour tous les prix
//    de vente de l'article d'un seul coup.

frappe.ui.form.on("Item", {
	refresh(frm) {
		toggle_woocommerce_sync(frm);

		if (!frm.is_new()) {
			frm
				.add_custom_button(__("💰 Prix de vente"), () =>
					open_selling_prices_dialog(frm)
				)
				.removeClass("btn-default")
				.addClass("btn-primary");
		}
	},

	image(frm) {
		toggle_woocommerce_sync(frm);
	},
});

// --- 1) Verrou sync WooCommerce sans image -------------------------------

function toggle_woocommerce_sync(frm) {
	const has_image = !!frm.doc.image;

	// Case non cochable tant qu'il n'y a pas d'image
	frm.set_df_property("custom_sync_avec_woocommerce", "read_only", has_image ? 0 : 1);

	// Si pas d'image mais la case est cochée -> on décoche pour empêcher la sync
	if (!has_image && frm.doc.custom_sync_avec_woocommerce) {
		frm.set_value("custom_sync_avec_woocommerce", 0);
		frappe.show_alert({
			message: __(
				"Ajoutez une image pour activer la synchronisation WooCommerce."
			),
			indicator: "orange",
		});
	}

	frm.set_df_property(
		"custom_sync_avec_woocommerce",
		"description",
		has_image
			? __("Active la synchronisation WooCommerce pour cet article.")
			: __(
					"⚠️ Ajoutez une image à l'article pour pouvoir activer la synchronisation."
			  )
	);
}

// --- 2) Popup saisie groupée des prix de vente ---------------------------

function open_selling_prices_dialog(frm) {
	frappe.call({
		method: "customization_app.api.get_item_selling_prices",
		args: { item_code: frm.doc.name },
		freeze: true,
		freeze_message: __("Chargement des prix..."),
		callback(r) {
			if (!r.message) return;
			if (!(r.message.price_lists || []).length) {
				frappe.msgprint(
					__("Aucune liste de prix de vente active n'est configurée.")
				);
				return;
			}
			build_selling_prices_dialog(frm, r.message);
		},
	});
}

function build_selling_prices_dialog(frm, data) {
	const cost = flt(data.cost);
	const tva = flt(data.tva);
	const margeBlock = flt(data.marge_block);
	const margeMin = flt(data.marge_min);
	const margePref = flt(data.marge_pref);
	const hasCost = cost > 0;

	const val_rate =
		data.valuation_rate != null ? format_currency(data.valuation_rate) : "—";
	const buy_rate =
		data.last_purchase_rate != null
			? format_currency(data.last_purchase_rate)
			: "—";

	const fields = [
		{
			fieldtype: "HTML",
			fieldname: "info",
			options: `
				<div class="text-muted" style="margin-bottom:8px;">
					<div><b>${__("Dernière valorisation")} :</b> ${val_rate} ${__("HT")}</div>
					<div><b>${__("Dernier prix d'achat")} :</b> ${buy_rate}</div>
					<div><b>${__("TVA")} :</b> ${format_number(tva, null, 0)} %</div>
					<div><b>${__("UdM")} :</b> ${frappe.utils.escape_html(data.uom || "—")}</div>
				</div>
				<div style="font-size:12px;background:#f8f9fa;border:1px solid #e9ecef;border-radius:5px;padding:8px;margin-bottom:6px;">
					<div style="font-weight:600;margin-bottom:3px;">${__("Instructions")}</div>
					<div>• ${__(
						"Vous pouvez saisir <b>soit le prix (TTC) soit la marge</b> : l'autre se calcule automatiquement."
					)}</div>
					<div>• ${__("Marge conseillée ≥ {0}% · minimum {1}%.", [
						format_number(margePref, null, 0),
						format_number(margeMin, null, 0),
					])}</div>
					<div>• <span style="color:#d68102;font-weight:600;">${__(
						"Marge < {0}% : orange",
						[format_number(margePref, null, 0)]
					)}</span> · <span style="color:#c0392b;font-weight:600;">${__(
						"Marge < {0}% : rouge",
						[format_number(margeMin, null, 0)]
					)}</span></div>
					<div>• ${__(
						"Enregistrement <b>bloqué</b> uniquement si marge < {0}%.",
						[format_number(margeBlock, null, 0)]
					)}</div>
					${
						hasCost
							? ""
							: `<div style="color:#c0392b;">${__(
									"⚠️ Coût inconnu : marge non calculable."
							  )}</div>`
					}
				</div>
				<hr style="margin:6px 0;">
				<div style="font-weight:600;margin-bottom:4px;">
					${__("Prix de vente (TTC) et marge par liste")}
				</div>`,
		},
	];

	data.price_lists.forEach((pl) => {
		const key = frappe.scrub(pl.price_list);
		fields.push(
			{
				fieldtype: "Section Break",
				label: pl.price_list + (pl.currency ? ` (${pl.currency})` : ""),
			},
			{
				fieldtype: "Currency",
				fieldname: "prix__" + key,
				label: __("Prix TTC"),
				default: pl.rate,
				reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Float",
				fieldname: "marge__" + key,
				label: __("Marge %"),
				default: pl.marge != null ? pl.marge : undefined,
				read_only: hasCost ? 0 : 1,
				precision: 1,
			}
		);
	});

	const d = new frappe.ui.Dialog({
		title: __("Prix de vente — {0}", [frm.doc.item_name || frm.doc.name]),
		fields: fields,
		size: "large",
		primary_action_label: __("Enregistrer"),
		primary_action() {
			const values = d.get_values(); // valide les champs obligatoires
			if (!values) return;

			const payload = [];
			const bloquantes = [];
			const faibles = [];

			data.price_lists.forEach((pl) => {
				const key = frappe.scrub(pl.price_list);
				const prix = flt(d.get_value("prix__" + key));
				payload.push({ price_list: pl.price_list, rate: prix });

				if (hasCost) {
					const m = _marge_from_prix(prix, cost, tva);
					if (m != null && m < margeBlock) {
						bloquantes.push(`${pl.price_list} (${m.toFixed(0)}%)`);
					} else if (m != null && m < margePref) {
						faibles.push(`${pl.price_list} (${m.toFixed(0)}%)`);
					}
				}
			});

			// Blocage dur uniquement sous le seuil de blocage (5%)
			if (bloquantes.length) {
				frappe.msgprint({
					title: __("Marge trop faible"),
					message: __(
						"Marge sous le minimum de {0}% (enregistrement bloqué) : {1}",
						[margeBlock.toFixed(0), bloquantes.join(", ")]
					),
					indicator: "red",
				});
				return;
			}

			const doSave = () => _save_prices(frm, d, payload);

			// Avertissement NON bloquant entre le seuil de blocage et le seuil conseillé
			if (faibles.length) {
				frappe.confirm(
					__(
						"Certaines marges sont sous le seuil conseillé de {0}% : {1}. Enregistrer quand même ?",
						[margePref.toFixed(0), faibles.join(", ")]
					),
					doSave
				);
			} else {
				doSave();
			}
		},
	});

	d.show();

	// --- Synchronisation bidirectionnelle prix <-> marge ---
	if (!hasCost) return;

	let syncing = false;

	const paint = (key) => {
		const f = d.fields_dict["marge__" + key];
		if (!f || !f.$input) return;
		const $inp = f.$input;
		const raw = d.get_value("marge__" + key);

		if (raw == null || raw === "") {
			$inp.attr("style", "");
			return;
		}

		const m = flt(raw);
		let color = "#1e7e34"; // texte
		let bg = "#e8f5e9"; // fond vert : >= conseillé
		if (m < margeMin) {
			color = "#c0392b";
			bg = "#fdecea"; // rouge : < 15%
		} else if (m < margePref) {
			color = "#d68102";
			bg = "#fff4e5"; // orange : < 25%
		}
		// style en ligne avec !important pour passer devant le thème frappe
		$inp.attr(
			"style",
			`background-color:${bg} !important; color:${color} !important; font-weight:700 !important;`
		);
	};

	data.price_lists.forEach((pl) => {
		const key = frappe.scrub(pl.price_list);
		const pk = "prix__" + key;
		const mk = "marge__" + key;

		// Init : la marge vient déjà du serveur (default du champ) — on colore.
		// Filet de sécurité : si elle est vide mais qu'un prix existe, on la calcule.
		const mCur = d.get_value(mk);
		if ((mCur == null || mCur === "") && flt(d.get_value(pk)) > 0) {
			syncing = true;
			const m0 = _marge_from_prix(flt(d.get_value(pk)), cost, tva);
			if (m0 != null) d.set_value(mk, flt(m0, 1));
			syncing = false;
		}
		paint(key);

		// Prix saisi -> recalcule la marge
		d.fields_dict[pk].$input.on("change", () => {
			if (syncing) return;
			syncing = true;
			const m = _marge_from_prix(flt(d.get_value(pk)), cost, tva);
			if (m != null) d.set_value(mk, flt(m, 1));
			syncing = false;
			paint(key);
		});

		// Marge saisie -> recalcule le prix
		d.fields_dict[mk].$input.on("change", () => {
			if (syncing) return;
			syncing = true;
			const p = _prix_from_marge(flt(d.get_value(mk)), cost, tva);
			if (p != null) d.set_value(pk, flt(p, 3));
			syncing = false;
			paint(key);
		});

		// Entrée dans prix/marge -> recalcule (ne PAS soumettre la fenêtre)
		_block_enter_submit(d.fields_dict[pk].$input);
		_block_enter_submit(d.fields_dict[mk].$input);
	});

	// Re-peindre après le rendu complet du dialog : les valeurs par défaut
	// (marges) sont appliquées après notre 1er passage, sinon les couleurs manquent.
	setTimeout(() => {
		data.price_lists.forEach((pl) => paint(frappe.scrub(pl.price_list)));
	}, 150);
}

// Empêche la touche Entrée de déclencher l'action principale (Enregistrer)
// du dialog : à la place, on valide le champ (déclenche le recalcul via "change").
function _block_enter_submit($inp) {
	const el = $inp && $inp.get(0);
	if (!el) return;
	el.addEventListener(
		"keydown",
		(e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				e.stopImmediatePropagation();
				$inp.trigger("change");
			}
		},
		true // phase capture : passe avant le handler "submit on Enter" de frappe
	);
}

// Marge (taux sur PV) = (PV_HT - coût) / PV_HT * 100 ; PV_HT = PV_TTC / (1 + TVA/100)
function _marge_from_prix(prix_ttc, cost, tva) {
	const pv_ht = flt(prix_ttc) / (1 + flt(tva) / 100);
	if (pv_ht <= 0) return null;
	return ((pv_ht - flt(cost)) / pv_ht) * 100;
}

// Arrondi au pas de 0,05 SUPÉRIEUR (centième à 0 ou 5). Ex : 4.666 -> 4.70, 4.646 -> 4.65
function _round_up_5(x) {
	return flt(Math.ceil(flt(x, 4) / 0.05 - 1e-9) * 0.05, 2);
}

// PV_TTC depuis la marge : PV_HT = coût / (1 - marge/100) ; PV_TTC = PV_HT * (1 + TVA/100)
function _prix_from_marge(marge, cost, tva) {
	const m = flt(marge);
	if (m >= 100) return null;
	const pv_ht = flt(cost) / (1 - m / 100);
	return _round_up_5(pv_ht * (1 + flt(tva) / 100));
}

function _save_prices(frm, d, payload) {
	frappe.call({
		method: "customization_app.api.save_item_selling_prices",
		args: { item_code: frm.doc.name, prices: JSON.stringify(payload) },
		freeze: true,
		freeze_message: __("Enregistrement des prix..."),
		callback(r) {
			if (!r.message) return;
			const msg = r.message;
			frappe.show_alert({
				message: __("Prix enregistrés : {0} mis à jour, {1} créé(s)", [
					(msg.updated || []).length,
					(msg.created || []).length,
				]),
				indicator: "green",
			});
			d.hide();
		},
	});
}
