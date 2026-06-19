// Item (vue liste) — customization_app
// Bouton "Vérification base article" : liste les articles à problème
// (synchronisés sans image, prix de vente manquant, marge < 20%) dans un popup.
//
// NB : woocommerce_fusion réassigne entièrement frappe.listview_settings['Item']
// et se charge APRÈS customization_app, ce qui écraserait un onload mergé.
// On ajoute donc le bouton directement sur la vue liste vivante via un hook de
// changement de route — indépendant de l'ordre de chargement des apps.

frappe.router.on("change", () => ensure_verif_button());

// Cas où le script se charge alors qu'on est déjà sur la liste Item
ensure_verif_button();

function ensure_verif_button(attempt = 0) {
	const route = frappe.get_route() || [];
	if (route[0] !== "List" || route[1] !== "Item") return;

	const view = window.cur_list;
	if (!view || view.doctype !== "Item" || !view.page) {
		// La vue n'est pas encore prête : on réessaie brièvement
		if (attempt < 20) setTimeout(() => ensure_verif_button(attempt + 1), 150);
		return;
	}
	if (view.__verif_btn_added) return;
	view.__verif_btn_added = true;

	const $btn = view.page.add_inner_button(
		__("🔍 Vérification base article"),
		() => {
			frappe.call({
				method: "customization_app.api.scan_item_database",
				freeze: true,
				freeze_message: __("Analyse des articles..."),
				callback(r) {
					if (r.message) show_item_check_dialog(r.message);
				},
			});
		}
	);
	if ($btn) $btn.removeClass("btn-default").addClass("btn-danger");
}

function show_item_check_dialog(data) {
	const d = new frappe.ui.Dialog({
		title: __("Vérification base article"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "content" }],
		primary_action_label: __("Fermer"),
		primary_action() {
			d.hide();
		},
		secondary_action_label: __("🔄 Actualiser"),
		secondary_action() {
			frappe.call({
				method: "customization_app.api.scan_item_database",
				freeze: true,
				freeze_message: __("Analyse des articles..."),
				callback(r) {
					if (r.message) d.fields_dict.content.$wrapper.html(build_check_body(r.message));
				},
			});
		},
	});

	d.fields_dict.content.$wrapper.html(build_check_body(data));
	d.show();
}

function build_check_body(data) {
	const problems = data.problems || [];

	let body;
	if (!problems.length) {
		body = `<div class="text-muted" style="padding:20px;text-align:center;">
					✅ ${__("Aucun article à problème. Base saine.")}
				</div>`;
	} else {
		const rows = problems
			.map((p) => {
				const img = p.image
					? `<img src="${frappe.utils.escape_html(p.image)}"
							style="width:40px;height:40px;object-fit:cover;border-radius:4px;">`
					: `<span class="text-muted" style="font-size:11px;">${__(
							"(aucune)"
					  )}</span>`;

				const reasons = (p.reasons || [])
					.map(
						(rs) =>
							`<div style="color:#c0392b;font-size:12px;">• ${frappe.utils.escape_html(
								rs
							)}</div>`
					)
					.join("");

				const url = `/app/item/${encodeURIComponent(p.item_code)}`;

				return `
					<tr>
						<td style="text-align:center;">${img}</td>
						<td><code>${frappe.utils.escape_html(p.item_code)}</code></td>
						<td>${frappe.utils.escape_html(p.item_name || "")}</td>
						<td>${reasons}</td>
						<td style="text-align:center;">
							<a href="${url}" target="_blank" rel="noopener"
							   class="btn btn-danger btn-xs">${__("Corriger")}</a>
						</td>
					</tr>`;
			})
			.join("");

		body = `
			<div style="margin-bottom:8px;font-weight:600;color:#c0392b;">
				${__("{0} article(s) à corriger", [problems.length])}
			</div>
			<div style="max-height:60vh;overflow:auto;">
				<table class="table table-bordered" style="font-size:13px;">
					<thead>
						<tr style="background:#f5f5f5;">
							<th style="width:55px;">${__("Image")}</th>
							<th style="width:120px;">${__("Code article")}</th>
							<th>${__("Nom article")}</th>
							<th>${__("Raison")}</th>
							<th style="width:90px;">${__("Action")}</th>
						</tr>
					</thead>
					<tbody>${rows}</tbody>
				</table>
			</div>`;
	}

	return body;
}
