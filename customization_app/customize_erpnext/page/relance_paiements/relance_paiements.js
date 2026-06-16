frappe.pages['relance-paiements'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Relance Paiements Clients'),
		single_column: true,
	});

	new RelancePaiements(page);
};

class RelancePaiements {
	constructor(page) {
		this.page = page;
		this.$body = $(page.body);
		this.clients = [];
		this.filtered = [];
		this.selected = new Set();
		this.kpi = {};
		this.sort_desc = true;

		this.inject_styles();
		this.setup_filters();
		this.render_layout();
		this.bind_actions();
		this.load();
	}

	// ---------------------------------------------------------------
	//  Helpers de formatage
	// ---------------------------------------------------------------
	fmt(val) {
		const n = Number(val || 0);
		return n.toLocaleString('fr-FR', {
			minimumFractionDigits: 3,
			maximumFractionDigits: 3,
		});
	}

	tnd(val) {
		return this.fmt(val) + ' TND';
	}

	esc(s) {
		return frappe.utils.escape_html(s == null ? '' : String(s));
	}

	// ---------------------------------------------------------------
	//  Styles
	// ---------------------------------------------------------------
	inject_styles() {
		if (document.getElementById('relance-paiements-styles')) return;
		const css = `
		.rp-kpis { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:18px; }
		.rp-kpi { flex:1 1 160px; background:#fff; border:1px solid #e2e8f0; border-radius:10px;
			padding:14px 16px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
		.rp-kpi .lbl { font-size:11px; text-transform:uppercase; letter-spacing:.4px; color:#64748b; }
		.rp-kpi .val { font-size:20px; font-weight:700; margin-top:4px; }
		.rp-kpi.main { background:#1e293b; border-color:#1e293b; }
		.rp-kpi.main .lbl, .rp-kpi.main .val { color:#fff; }
		.rp-kpi.dettes .val { color:#1e40af; }
		.rp-kpi.cheques .val { color:#dc2626; }
		.rp-kpi.traites .val { color:#ea580c; }
		.rp-kpi.clients .val { color:#0f766e; }

		.rp-toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin-bottom:14px; }
		.rp-toolbar .form-group { margin-bottom:0; }
		.rp-actions { margin-left:auto; display:flex; gap:8px; align-items:center; }

		.rp-table-wrap { background:#fff; border:1px solid #e2e8f0; border-radius:10px; overflow:auto; }
		table.rp-table { width:100%; border-collapse:collapse; font-size:13px; }
		table.rp-table th { background:#f8fafc; text-align:left; padding:10px 12px; font-weight:600;
			color:#475569; border-bottom:1px solid #e2e8f0; white-space:nowrap; position:sticky; top:0; }
		table.rp-table td { padding:9px 12px; border-bottom:1px solid #f1f5f9; vertical-align:middle; }
		table.rp-table tr:hover td { background:#f8fafc; }
		table.rp-table .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
		.rp-amt-dettes { color:#1e40af; font-weight:600; }
		.rp-amt-cheques { color:#dc2626; font-weight:600; }
		.rp-amt-traites { color:#ea580c; font-weight:600; }
		.rp-amt-total { font-weight:700; color:#0f172a; }
		.rp-grp { display:inline-block; padding:2px 8px; border-radius:20px; background:#eef2ff;
			color:#4338ca; font-size:11px; }
		.rp-th-sort { cursor:pointer; }
		.rp-empty { padding:40px; text-align:center; color:#94a3b8; }
		.rp-no-phone { color:#cbd5e1; font-style:italic; }
		.rp-btn-mini { padding:3px 8px; font-size:12px; border:1px solid #cbd5e1; background:#fff;
			border-radius:6px; cursor:pointer; white-space:nowrap; }
		.rp-btn-mini:hover { background:#f1f5f9; }
		.rp-selbar { display:none; align-items:center; gap:12px; background:#1e293b; color:#fff;
			padding:8px 14px; border-radius:8px; margin-bottom:12px; }
		.rp-selbar.show { display:flex; }

		.rp-detail-table { width:100%; border-collapse:collapse; font-size:12px; }
		.rp-detail-table th { background:#f8fafc; text-align:left; padding:7px 9px; border-bottom:1px solid #e2e8f0; }
		.rp-detail-table td { padding:6px 9px; border-bottom:1px solid #f1f5f9; }
		.rp-ref { display:inline-block; font-size:11px; background:#f1f5f9; border-radius:4px;
			padding:1px 6px; margin:1px; }
		.rp-note-row td { background:#fcfcfd !important; padding-top:0 !important; }
		.rp-note { font-size:11px; border-radius:6px; padding:4px 8px; margin:2px 0; display:block; }
		.rp-note-open { background:#fffbeb; color:#92400e; border:1px solid #fde68a; }
		.rp-note-done { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; font-weight:600; }
		.rp-note-neutral { background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; }
		.rp-msg-card { border:1px solid #e2e8f0; border-radius:8px; padding:10px; margin-bottom:10px; background:#fff; }
		.rp-msg-card .hdr { font-weight:600; margin-bottom:6px; display:flex; justify-content:space-between; }
		.rp-msg-card textarea { width:100%; font-size:12px; border:1px solid #cbd5e1; border-radius:6px; padding:6px; }
		.rp-msg-card .meta { font-size:11px; color:#64748b; margin-top:4px; }
		.rp-histo-wrap { margin-top:14px; border-top:1px dashed #e2e8f0; padding-top:10px; }
		.rp-histo-title { font-weight:600; font-size:12px; color:#334155; margin-bottom:8px; }
		.rp-histo-item { border-left:3px solid #cbd5e1; padding:4px 0 4px 10px; margin-bottom:6px; }
		.rp-histo-type { font-size:12px; font-weight:600; color:#0f172a; }
		.rp-histo-date { font-size:11px; color:#94a3b8; margin-left:8px; }
		.rp-histo-note { font-size:12px; color:#475569; margin-top:2px; }
		.rp-histo-empty { font-size:12px; color:#94a3b8; padding:6px 0; }
		.rp-detail-histo { padding:0 2px; }
		.rp-guide { font-size:13px; color:#334155; line-height:1.55; }
		.rp-guide h4 { margin:16px 0 6px; font-size:14px; color:#0f172a; border-bottom:1px solid #e2e8f0; padding-bottom:4px; }
		.rp-guide ul, .rp-guide ol { margin:0 0 6px 18px; padding:0; }
		.rp-guide li { margin-bottom:4px; }
		.rp-guide-intro { background:#f0f9ff; border-left:3px solid #0ea5e9; padding:8px 12px; border-radius:6px; }
		.rp-guide-note { background:#f8fafc; border:1px solid #e2e8f0; padding:8px 12px; border-radius:6px; margin-top:14px; font-size:12px; color:#475569; }
		`;
		$('<style>', { id: 'relance-paiements-styles', html: css }).appendTo('head');
	}

	// ---------------------------------------------------------------
	//  Filtres dans le header de la page
	// ---------------------------------------------------------------
	setup_filters() {
		this.search_field = this.page.add_field({
			fieldname: 'search',
			label: __('Recherche (nom / téléphone)'),
			fieldtype: 'Data',
			change: () => this.apply_filters(),
		});

		this.group_field = this.page.add_field({
			fieldname: 'customer_group',
			label: __('Groupe client'),
			fieldtype: 'Link',
			options: 'Customer Group',
			change: () => this.apply_filters(),
		});

		this.type_field = this.page.add_field({
			fieldname: 'debt_type',
			label: __('Type de dette'),
			fieldtype: 'Select',
			options: [
				{ value: '', label: __('Tous') },
				{ value: 'dettes', label: __('Dettes') },
				{ value: 'cheques', label: __('Chèques sans provision') },
				{ value: 'traites', label: __('Traites sans provision') },
			],
			change: () => this.apply_filters(),
		});
	}

	bind_actions() {
		this.page.set_primary_action(__('Actualiser'), () => this.load(), 'refresh');
		this.page.add_menu_item(__('Exporter Excel'), () => this.export_excel());
		this.page.add_menu_item(__('📖 Guide d\'utilisation'), () => this.open_guide_dialog());
		this.page.set_secondary_action(__('📖 Guide'), () => this.open_guide_dialog());
	}

	// ---------------------------------------------------------------
	//  Guide d'utilisation
	// ---------------------------------------------------------------
	open_guide_dialog() {
		const d = new frappe.ui.Dialog({
			title: __('📖 Guide d\'utilisation — Suivi dettes client'),
			size: 'large',
			fields: [{ fieldtype: 'HTML', fieldname: 'guide', options: this.guide_html() }],
			primary_action_label: __('J\'ai compris'),
			primary_action: () => d.hide(),
		});
		d.show();
	}

	guide_html() {
		return `
		<div class="rp-guide">
			<p class="rp-guide-intro">
				Cette page regroupe les clients ayant des <b>paiements problématiques</b>
				(dettes, chèques et traites sans provision) à partir des
				<b>écritures de paiement</b>, et permet de les relancer par SMS et/ou Email.
			</p>

			<h4>1 · Comprendre le tableau</h4>
			<ul>
				<li><b>Dettes / Chèques / Traites</b> : montant restant dû par type de problème.</li>
				<li><b>Total</b> : somme à relancer pour le client.</li>
				<li>Les indicateurs en haut résument le total global et le nombre de clients.</li>
			</ul>

			<h4>2 · Filtrer</h4>
			<ul>
				<li><b>Recherche</b> : par nom de client.</li>
				<li><b>Groupe client</b> : limiter à un groupe (Pro, Technicien…).</li>
				<li><b>Type de dette</b> : n'afficher qu'un seul type de problème.</li>
			</ul>

			<h4>3 · Voir le détail d'un client</h4>
			<ul>
				<li>Bouton <b>Détails</b> : liste chaque paiement, sa date, la pièce (facture
					<b>FAC-…</b>), le document lié et son total.</li>
				<li>Une <b>note de tâche</b> peut apparaître :
					<span style="color:#b45309">⏳ transaction non finie</span> ou
					<span style="color:#dc2626">⚠️ tâche terminée à vérifier</span>.</li>
				<li>L'<b>historique des relances</b> du client s'affiche en bas du détail.</li>
			</ul>

			<h4>4 · Relancer un ou plusieurs clients</h4>
			<ol>
				<li>Cochez les clients (ou utilisez <b>Relancer</b> sur une ligne).</li>
				<li>Choisissez le <b>canal</b> : SMS, Email, ou les deux.</li>
				<li>Le message est <b>pré-rempli</b> avec le détail des paiements — modifiable.</li>
				<li>Cliquez <b>Envoyer</b>. Chaque envoi est <b>tracé</b> dans l'historique du client.</li>
			</ol>

			<h4>5 · Relance téléphonique</h4>
			<ul>
				<li>Bouton <b>Tél.</b> : enregistrez un appel avec un commentaire.</li>
				<li>Cela crée une trace 📞 dans l'historique (date, utilisateur, note).</li>
			</ul>

			<h4>6 · Exporter</h4>
			<ul>
				<li>Menu <b>⋯ → Exporter Excel</b> : télécharge la liste affichée.</li>
			</ul>

			<p class="rp-guide-note">
				ℹ️ Les calculs se basent uniquement sur les <b>écritures de paiement</b>
				des comptes : Dettes, Chèques sans provision et Traites sans provision.
			</p>
		</div>`;
	}

	// ---------------------------------------------------------------
	//  Layout statique
	// ---------------------------------------------------------------
	render_layout() {
		this.$body.html(`
			<div class="rp-kpis"></div>
			<div class="rp-selbar">
				<span class="rp-selcount">0 sélectionné(s)</span>
				<button class="btn btn-sm btn-light rp-clear-sel">Désélectionner</button>
				<button class="btn btn-sm btn-warning rp-relance-sel">📣 Préparer la relance</button>
			</div>
			<div class="rp-table-wrap">
				<table class="rp-table">
					<thead></thead>
					<tbody></tbody>
				</table>
			</div>
		`);

		this.$kpis = this.$body.find('.rp-kpis');
		this.$selbar = this.$body.find('.rp-selbar');
		this.$thead = this.$body.find('.rp-table thead');
		this.$tbody = this.$body.find('.rp-table tbody');

		this.$body.on('click', '.rp-clear-sel', () => this.clear_selection());
		this.$body.on('click', '.rp-relance-sel', () => this.open_relance_dialog([...this.selected]));
	}

	// ---------------------------------------------------------------
	//  Chargement des données
	// ---------------------------------------------------------------
	load() {
		const args = {
			search: this.search_field.get_value() || null,
			customer_group: this.group_field.get_value() || null,
			debt_type: this.type_field.get_value() || null,
		};
		this.$tbody.html(`<tr><td colspan="9" class="rp-empty">${__('Chargement…')}</td></tr>`);
		frappe.call({
			method: 'customization_app.api.get_relance_clients',
			args,
			callback: (r) => {
				const data = r.message || { clients: [], kpi: {} };
				this.clients = data.clients || [];
				this.kpi = data.kpi || {};
				this.selected.clear();
				this.update_selbar();
				this.render_kpis();
				this.apply_filters();
			},
		});
	}

	// Filtrage côté client (rapide) + délègue au backend uniquement au load()
	apply_filters() {
		const s = (this.search_field.get_value() || '').trim().toLowerCase();
		const grp = this.group_field.get_value() || '';
		const type = this.type_field.get_value() || '';

		this.filtered = this.clients.filter((c) => {
			if (grp && c.customer_group !== grp) return false;
			if (type === 'dettes' && c.dettes <= 0.009) return false;
			if (type === 'cheques' && c.cheques <= 0.009) return false;
			if (type === 'traites' && c.traites <= 0.009) return false;
			if (s) {
				const hay = `${c.customer_name} ${c.customer} ${c.telephone}`.toLowerCase();
				if (!hay.includes(s)) return false;
			}
			return true;
		});

		this.filtered.sort((a, b) =>
			this.sort_desc ? b.total - a.total : a.total - b.total
		);
		this.render_table();
	}

	// ---------------------------------------------------------------
	//  KPI cards
	// ---------------------------------------------------------------
	render_kpis() {
		const k = this.kpi;
		this.$kpis.html(`
			<div class="rp-kpi main"><div class="lbl">Total à relancer</div><div class="val">${this.tnd(k.total_a_relancer)}</div></div>
			<div class="rp-kpi clients"><div class="lbl">Clients concernés</div><div class="val">${k.nb_clients || 0}</div></div>
			<div class="rp-kpi dettes"><div class="lbl">Total dettes</div><div class="val">${this.tnd(k.total_dettes)}</div></div>
			<div class="rp-kpi cheques"><div class="lbl">Chèques sans provision</div><div class="val">${this.tnd(k.total_cheques)}</div></div>
			<div class="rp-kpi traites"><div class="lbl">Traites sans provision</div><div class="val">${this.tnd(k.total_traites)}</div></div>
		`);
	}

	// ---------------------------------------------------------------
	//  Tableau principal
	// ---------------------------------------------------------------
	render_table() {
		const allChecked =
			this.filtered.length > 0 &&
			this.filtered.every((c) => this.selected.has(c.customer));

		this.$thead.html(`
			<tr>
				<th style="width:34px"><input type="checkbox" class="rp-check-all" ${allChecked ? 'checked' : ''}></th>
				<th>Client</th>
				<th>Groupe</th>
				<th>Téléphone</th>
				<th class="num">Dettes</th>
				<th class="num">Chèques</th>
				<th class="num">Traites</th>
				<th class="num rp-th-sort">Total ${this.sort_desc ? '▼' : '▲'}</th>
				<th style="width:160px">Actions</th>
			</tr>
		`);

		if (!this.filtered.length) {
			this.$tbody.html(`<tr><td colspan="9" class="rp-empty">${__('Aucun client à relancer.')}</td></tr>`);
		} else {
			const rows = this.filtered
				.map((c) => {
					const checked = this.selected.has(c.customer) ? 'checked' : '';
					const phone = c.telephone
						? this.esc(c.telephone)
						: `<span class="rp-no-phone">— manquant —</span>`;
					return `
					<tr data-cust="${this.esc(c.customer)}">
						<td><input type="checkbox" class="rp-row-check" ${checked}></td>
						<td><b>${this.esc(c.customer_name)}</b></td>
						<td>${c.customer_group ? `<span class="rp-grp">${this.esc(c.customer_group)}</span>` : ''}</td>
						<td>${phone}</td>
						<td class="num rp-amt-dettes">${c.dettes > 0.009 ? this.fmt(c.dettes) : '—'}</td>
						<td class="num rp-amt-cheques">${c.cheques > 0.009 ? this.fmt(c.cheques) : '—'}</td>
						<td class="num rp-amt-traites">${c.traites > 0.009 ? this.fmt(c.traites) : '—'}</td>
						<td class="num rp-amt-total">${this.fmt(c.total)}</td>
						<td>
							<button class="rp-btn-mini rp-detail">👁 Détails</button>
							<button class="rp-btn-mini rp-relance-one">📣 Relancer</button>
							<button class="rp-btn-mini rp-relance-tel">📞 Tél.</button>
						</td>
					</tr>`;
				})
				.join('');
			this.$tbody.html(rows);
		}

		this.bind_table_events();
	}

	bind_table_events() {
		// Retire systématiquement les anciens écouteurs pour éviter qu'ils
		// s'accumulent à chaque rendu (sinon "Détails" ouvrait N fenêtres).
		this.$thead.off('.rp');
		this.$tbody.off('.rp');

		this.$thead.on('change.rp', '.rp-check-all', (e) => {
			const on = e.target.checked;
			this.filtered.forEach((c) => {
				if (on) this.selected.add(c.customer);
				else this.selected.delete(c.customer);
			});
			this.render_table();
			this.update_selbar();
		});

		this.$thead.on('click.rp', '.rp-th-sort', () => {
			this.sort_desc = !this.sort_desc;
			this.apply_filters();
		});

		this.$tbody.on('change.rp', '.rp-row-check', (e) => {
			const cust = $(e.target).closest('tr').data('cust');
			if (e.target.checked) this.selected.add(cust);
			else this.selected.delete(cust);
			this.update_selbar();
		});

		this.$tbody.on('click.rp', '.rp-detail', (e) => {
			const cust = $(e.target).closest('tr').data('cust');
			this.open_detail(cust);
		});

		this.$tbody.on('click.rp', '.rp-relance-one', (e) => {
			const cust = $(e.target).closest('tr').data('cust');
			this.open_relance_dialog([cust]);
		});

		this.$tbody.on('click.rp', '.rp-relance-tel', (e) => {
			const cust = $(e.target).closest('tr').data('cust');
			this.open_relance_tel(cust);
		});
	}

	clear_selection() {
		this.selected.clear();
		this.render_table();
		this.update_selbar();
	}

	update_selbar() {
		const n = this.selected.size;
		this.$selbar.toggleClass('show', n > 0);
		this.$selbar.find('.rp-selcount').text(`${n} sélectionné(s)`);
	}

	// ---------------------------------------------------------------
	//  Modal détail
	// ---------------------------------------------------------------
	open_detail(customer) {
		const client = this.clients.find((c) => c.customer === customer);
		const title = client ? client.customer_name : customer;
		const d = new frappe.ui.Dialog({
			title: __('Détail — {0}', [title]),
			size: 'extra-large',
		});
		d.$body.html(`<div class="rp-empty">${__('Chargement…')}</div>`);
		d.show();

		frappe.call({
			method: 'customization_app.api.get_relance_detail',
			args: { customer },
			callback: (r) => {
				const rows = r.message || [];
				if (!rows.length) {
					d.$body.html(`<div class="rp-empty">${__('Aucune écriture.')}</div>`);
					return;
				}
				const body = rows
					.map((x) => {
						const refs = (x.references || [])
							.map(
								(ref) =>
									`<a class="rp-ref" href="/app/${frappe.router.slug(
										ref.reference_doctype
									)}/${encodeURIComponent(ref.reference_name)}" target="_blank">${this.esc(
										ref.reference_doctype
									)}: ${this.esc(ref.reference_name)}</a>`
							)
							.join(' ') || '<span class="rp-no-phone">—</span>';
						const notes = this.render_task_notes(x.tasks || []);
						const noteRow = notes
							? `<tr class="rp-note-row"><td></td><td colspan="8">${notes}</td></tr>`
							: '';
						return `
						<tr>
							<td>${this.esc(x.posting_date)}</td>
							<td>${this.esc(x.account_label)}</td>
							<td><a href="/app/payment-entry/${encodeURIComponent(x.voucher_no)}" target="_blank">${this.esc(x.voucher_no)}</a></td>
							<td class="num">${x.debit ? this.fmt(x.debit) : '—'}</td>
							<td class="num">${x.credit ? this.fmt(x.credit) : '—'}</td>
							<td class="num">${this.fmt(x.balance)}</td>
							<td>${this.esc(x.docstatus)}</td>
							<td>${refs}</td>
							<td style="max-width:220px;font-size:11px;color:#64748b">${this.esc(x.remarks)}</td>
						</tr>${noteRow}`;
					})
					.join('');
				d.$body.html(`
					<div style="overflow:auto;max-height:60vh">
					<table class="rp-detail-table">
						<thead><tr>
							<th>Date</th><th>Compte</th><th>Pièce</th>
							<th class="num">Débit</th><th class="num">Crédit</th><th class="num">Solde</th>
							<th>Statut</th><th>Pièces liées</th><th>Remarque</th>
						</tr></thead>
						<tbody>${body}</tbody>
					</table></div>
					<div class="rp-detail-histo"></div>
				`);
				this.load_historique(customer, d.$body.find('.rp-detail-histo'));
			},
		});

		d.set_secondary_action_label(__('Relancer ce client'));
		d.set_secondary_action(() => {
			d.hide();
			this.open_relance_dialog([customer]);
		});
	}

	// ---------------------------------------------------------------
	//  Notes de vérification basées sur les Tâches de travail liées
	// ---------------------------------------------------------------
	render_task_notes(tasks) {
		if (!tasks || !tasks.length) return '';
		return tasks
			.map((t) => {
				const type = t.type || __('Intervention');
				const link = `<a href="/app/tache-de-travail/${encodeURIComponent(
					t.name
				)}" target="_blank">${this.esc(t.name)}</a>`;

				if (t.status === 'Open') {
					return `<div class="rp-note rp-note-open">⏳ ${__(
						'Transaction non finie'
					)} — ${__('tâche de travail')} <b>${this.esc(type)}</b> ${__(
						'est prévue'
					)} ${
						t.starts_on ? `(${this.esc(t.starts_on)})` : ''
					} · ${link}</div>`;
				}
				if (t.status === 'Completed') {
					const dateFin = t.ends_on || t.starts_on || '';
					return `<div class="rp-note rp-note-done">⚠️ ${__('tâche de travail')} <b>${this.esc(
						type
					)}</b> ${__('terminée')}${
						dateFin ? ` ${__('le')} ${this.esc(dateFin)}` : ''
					} — ${__('merci de vérifier l\'état')} · ${link}</div>`;
				}
				// Cancelled ou autre : info neutre
				return `<div class="rp-note rp-note-neutral">ℹ️ ${__('tâche')} <b>${this.esc(
					type
				)}</b> — ${this.esc(t.status)} · ${link}</div>`;
			})
			.join('');
	}

	// ---------------------------------------------------------------
	//  Relance téléphonique (saisie d'un commentaire, traçabilité)
	// ---------------------------------------------------------------
	open_relance_tel(customer) {
		const client = this.clients.find((c) => c.customer === customer);
		const title = client ? client.customer_name : customer;
		const d = new frappe.ui.Dialog({
			title: __('Relance téléphonique — {0}', [title]),
			fields: [
				{
					fieldname: 'tel_info',
					fieldtype: 'HTML',
					options: client && client.telephone
						? `<div style="margin-bottom:8px">📱 <b>${this.esc(client.telephone)}</b></div>`
						: `<div style="margin-bottom:8px;color:#dc2626">📱 ${__('Téléphone manquant')}</div>`,
				},
				{
					fieldname: 'commentaire',
					label: __('Commentaire de l\'appel'),
					fieldtype: 'Small Text',
					reqd: 1,
					description: __('Ex : Client promet de régler avant le 30/06.'),
				},
				{ fieldname: 'histo', fieldtype: 'HTML' },
			],
			primary_action_label: __('Enregistrer'),
			primary_action: () => {
				const note = (d.get_value('commentaire') || '').trim();
				if (!note) {
					frappe.msgprint(__('Veuillez saisir un commentaire.'));
					return;
				}
				frappe.call({
					method: 'customization_app.api.enregistrer_relance_telephone',
					args: { customer, commentaire: note },
					freeze: true,
					freeze_message: __('Enregistrement…'),
					callback: () => {
						frappe.show_alert({
							message: __('Relance téléphonique enregistrée.'),
							indicator: 'green',
						});
						d.set_value('commentaire', '');
						this.load_historique(customer, d.fields_dict.histo.$wrapper);
					},
				});
			},
		});
		d.show();
		this.load_historique(customer, d.fields_dict.histo.$wrapper);
	}

	load_historique(customer, $target) {
		$target.html(`<div class="rp-empty" style="padding:10px">${__('Chargement de l\'historique…')}</div>`);
		frappe.call({
			method: 'customization_app.api.get_historique_relances',
			args: { customer },
			callback: (r) => {
				$target.html(this.render_historique(r.message || []));
			},
		});
	}

	render_historique(rows) {
		if (!rows.length) {
			return `<div class="rp-histo-empty">${__('Aucune relance enregistrée pour ce client.')}</div>`;
		}
		const icons = { 'Téléphone': '📞', SMS: '📱', Email: '✉️' };
		const items = rows
			.map((h) => {
				const icon = icons[h.type] || '🔔';
				return `<div class="rp-histo-item">
					<span class="rp-histo-type">${icon} ${this.esc(h.type)}</span>
					<span class="rp-histo-date">${this.esc(h.date)}</span>
					${h.note ? `<div class="rp-histo-note">${this.esc(h.note)}</div>` : ''}
				</div>`;
			})
			.join('');
		return `<div class="rp-histo-wrap">
			<div class="rp-histo-title">🗂 ${__('Historique des relances')}</div>
			${items}
		</div>`;
	}

	// ---------------------------------------------------------------
	//  Dialog relance (préparation + envoi)
	// ---------------------------------------------------------------
	open_relance_dialog(customers) {
		if (!customers.length) {
			frappe.msgprint(__('Aucun client sélectionné.'));
			return;
		}
		frappe.call({
			method: 'customization_app.api.preparer_messages_relance',
			args: { customers: JSON.stringify(customers) },
			freeze: true,
			freeze_message: __('Préparation des messages…'),
			callback: (r) => {
				const msgs = r.message || [];
				if (!msgs.length) {
					frappe.msgprint(__('Aucun message à préparer.'));
					return;
				}
				this.show_relance_dialog(msgs);
			},
		});
	}

	show_relance_dialog(msgs) {
		const d = new frappe.ui.Dialog({
			title: __('Préparer la relance ({0} client(s))', [msgs.length]),
			size: 'large',
			fields: [
				{
					fieldname: 'channel',
					label: __('Canal'),
					fieldtype: 'Select',
					options: [
						{ value: 'sms', label: __('SMS') },
						{ value: 'email', label: __('Email') },
						{ value: 'both', label: __('SMS + Email') },
					],
					default: 'both',
					reqd: 1,
				},
				{ fieldname: 'cards', fieldtype: 'HTML' },
			],
			primary_action_label: __('Envoyer'),
			primary_action: () => this.send_relance(d, msgs),
		});

		const $wrap = d.fields_dict.cards.$wrapper;
		const contactBadge = (m, channel) => {
			const sms = m.telephone
				? `📱 ${this.esc(m.telephone)}`
				: `<span style="color:#dc2626">📱 téléphone manquant</span>`;
			const email = m.email
				? `✉️ ${this.esc(m.email)}`
				: `<span style="color:#dc2626">✉️ email manquant</span>`;
			if (channel === 'sms') return sms;
			if (channel === 'email') return email;
			return `${sms} &nbsp; ${email}`;
		};
		const renderCards = () => {
			const channel = d.get_value('channel');
			const cards = msgs
				.map((m, i) => {
					const contact = contactBadge(m, channel);
					return `
					<div class="rp-msg-card" data-i="${i}">
						<div class="hdr"><span>${this.esc(m.customer_name)}</span><span>${this.tnd(m.total)}</span></div>
						<textarea rows="3" data-i="${i}">${this.esc(m.message)}</textarea>
						<div class="meta">${contact}</div>
					</div>`;
				})
				.join('');
			$wrap.html(cards);
		};

		renderCards();
		d.fields_dict.channel.$input.on('change', renderCards);
		d.show();
	}

	send_relance(d, msgs) {
		const channel = d.get_value('channel');
		// Récupère les messages éventuellement édités
		const payload = msgs.map((m, i) => {
			const $ta = d.fields_dict.cards.$wrapper.find(`textarea[data-i="${i}"]`);
			return {
				customer: m.customer,
				telephone: m.telephone,
				email: m.email,
				message: $ta.length ? $ta.val() : m.message,
			};
		});

		const missing = payload.filter((p) => {
			if (channel === 'sms') return !p.telephone;
			if (channel === 'email') return !p.email;
			return !p.telephone && !p.email; // both : manquant seulement si aucun des deux
		});
		const proceed = () => {
			frappe.call({
				method: 'customization_app.api.envoyer_relance',
				args: { payload: JSON.stringify(payload), channel },
				freeze: true,
				freeze_message: __('Envoi en cours…'),
				callback: (r) => {
					const res = r.message || { sent: [], failed: [] };
					d.hide();
					frappe.msgprint({
						title: __('Résultat de la relance'),
						indicator: res.failed.length ? 'orange' : 'green',
						message:
							`<b>${res.sent.length}</b> ${__('envoyé(s)')}<br>` +
							(res.failed.length
								? `<b>${res.failed.length}</b> ${__('échec(s)')}:<br>` +
								  res.failed
										.map((f) => `• ${this.esc(f.customer)} — ${this.esc(f.reason)}`)
										.join('<br>')
								: ''),
					});
				},
			});
		};

		if (missing.length) {
			frappe.confirm(
				__('{0} client(s) sans coordonnée pour ce canal seront ignorés. Continuer ?', [
					missing.length,
				]),
				proceed
			);
		} else {
			proceed();
		}
	}

	// ---------------------------------------------------------------
	//  Export Excel (CSV compatible Excel)
	// ---------------------------------------------------------------
	export_excel() {
		if (!this.filtered.length) {
			frappe.msgprint(__('Rien à exporter.'));
			return;
		}
		const headers = [
			'Client',
			'Groupe',
			'Téléphone',
			'Email',
			'Dettes',
			'Chèques sans provision',
			'Traites sans provision',
			'Total',
		];
		const data = this.filtered.map((c) => [
			c.customer_name,
			c.customer_group,
			c.telephone,
			c.email,
			c.dettes,
			c.cheques,
			c.traites,
			c.total,
		]);
		frappe.tools.downloadify([headers, ...data], null, 'Relance_Paiements');
	}
}
