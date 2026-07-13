frappe.pages["caisse-especes"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: "Caisse Espèces",
		single_column: true,
	});

	// Inject the HTML template
	$(wrapper).find(".layout-main-section").html(
		frappe.render_template("caisse_especes", {})
	);

	new CaisseEspacesPage(wrapper);
};

class CaisseEspacesPage {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this._data = null;           // last API response
		this._excluded = new Set();  // set of versement_ref currently excluded

		this._init_defaults();
		this._bind_events();
		this._load_config().then(() => this._fetch());
	}

	// ── Defaults ─────────────────────────────────────────────────────────────
	_init_defaults() {
		const now  = new Date();
		const y    = now.getFullYear();
		const m    = String(now.getMonth() + 1).padStart(2, "0");
		const last = new Date(y, now.getMonth() + 1, 0).getDate();
		$("#caisse-d1").val(`${y}-${m}-01`);
		$("#caisse-d2").val(`${y}-${m}-${String(last).padStart(2, "0")}`);
	}

	// ── Load saved config (dates + solde_initial + exclusions) ─────────────────
	async _load_config() {
		try {
			const r = await frappe.call({
				method: "customization_app.api.get_caisse_config",
			});
			const cfg = r.message || {};
			// Restore saved date range if available
			if (cfg.date_debut) $("#caisse-d1").val(cfg.date_debut);
			if (cfg.date_fin)   $("#caisse-d2").val(cfg.date_fin);
			const initial = parseFloat(cfg.solde_initial) || 0;
			$("#caisse-solde-initial").val(initial);
			this._excluded = new Set((cfg.excluded_versements || []).map(v => v.versement_ref));
		} catch (e) {
			console.warn("Caisse config not loaded:", e);
		}
	}

	// ── Event bindings ────────────────────────────────────────────────────────
	_bind_events() {
		$("#caisse-refresh-btn").on("click", () => this._fetch());
		$("#caisse-save-btn").on("click", () => this._save_and_back());
		$("#caisse-solde-initial").on("input", () => this._recalc());
		$("#caisse-chart-btn").on("click", () => this._toggle_chart());
		$("#caisse-excel-btn").on("click", () => this._export_excel());
		$("#caisse-chart-close").on("click", () => {
			$("#caisse-chart-section").hide();
		});
	}

	// ── Fetch data ────────────────────────────────────────────────────────────
	async _fetch() {
		const d1 = $("#caisse-d1").val();
		const d2 = $("#caisse-d2").val();
		if (!d1 || !d2) { frappe.msgprint("Veuillez saisir les dates."); return; }

		this._show_loading(true);
		try {
			const r = await frappe.call({
				method: "customization_app.api.get_caisse_dashboard",
				args: { d1, d2 },
			});
			this._data = r.message || {};
			this._render();
		} catch (e) {
			frappe.msgprint({ title: "Erreur", message: String(e), indicator: "red" });
		} finally {
			this._show_loading(false);
		}
	}

	// ── Render ────────────────────────────────────────────────────────────────
	_render() {
		if (!this._data) return;
		this._render_entrees(this._data.entrees_detail || []);
		this._render_sorties(this._data.sorties_achat || [], this._data.sorties_dep || []);
		this._render_versements(this._data.versements || []);
		this._recalc();
	}

	_render_entrees(rows) {
		const tbody = $("#table-entrees-body").empty();
		if (!rows.length) {
			tbody.append('<tr><td colspan="3" style="text-align:center;color:#999;">Aucune entrée</td></tr>');
			return;
		}
		rows.forEach(r => {
			tbody.append(`<tr style="background:#e8f5e9;">
				<td>${r.date || ""}</td>
				<td>${frappe.utils.escape_html(r.client || r.invoice_number || "")}</td>
				<td style="text-align:right;">${this._fmt(r.montant)}</td>
			</tr>`);
		});
	}

	_render_sorties(achats, deps) {
		const tbody = $("#table-sorties-body").empty();
		const all = [
			...achats.map(r => ({ ...r, _type: "achat" })),
			...deps.map(r => ({ ...r, _type: "dep" })),
		].sort((a, b) => (a.date || "").localeCompare(b.date || ""));

		if (!all.length) {
			tbody.append('<tr><td colspan="4" style="text-align:center;color:#999;">Aucune sortie</td></tr>');
			return;
		}
		all.forEach(r => {
			const ref  = r.invoice_number || r.journal_entry_number || "";
			const who  = r._type === "achat"
				? frappe.utils.escape_html(r.supplier || "")
				: frappe.utils.escape_html(r.description || "");
			tbody.append(`<tr style="background:#ffebee;">
				<td>${r.date || ""}</td>
				<td style="font-size:11px;">${frappe.utils.escape_html(ref)}</td>
				<td>${who}</td>
				<td style="text-align:right;">${this._fmt(r.montant)}</td>
			</tr>`);
		});
	}

	_render_versements(rows) {
		const tbody = $("#table-versements-body").empty();
		if (!rows.length) {
			tbody.append('<tr><td colspan="5" style="text-align:center;color:#999;">Aucun versement</td></tr>');
			return;
		}
		rows.forEach(r => {
			const ref      = r.journal_entry_number || r.ref || "";
			const excluded = this._excluded.has(ref);
			const bg       = excluded ? "background:#fff9c4;" : "background:#f3e5f5;";
			const checked  = excluded ? "" : "checked";
			tbody.append(`<tr data-ref="${frappe.utils.escape_html(ref)}" data-montant="${parseFloat(r.montant)||0}" style="${bg}">
				<td style="text-align:center;">
					<input type="checkbox" class="versement-check" ${checked} style="cursor:pointer;" />
				</td>
				<td>${r.date || ""}</td>
				<td style="font-size:11px;">${frappe.utils.escape_html(ref)}</td>
				<td style="font-size:11px;">${frappe.utils.escape_html(r.description || "")}</td>
				<td style="text-align:right;">${this._fmt(r.montant)}</td>
			</tr>`);
		});

		// bind checkbox changes
		tbody.find(".versement-check").on("change", (e) => {
			const $row = $(e.target).closest("tr");
			const ref  = $row.data("ref");
			if ($(e.target).is(":checked")) {
				this._excluded.delete(ref);
				$row.css("background", "#f3e5f5");
			} else {
				this._excluded.add(ref);
				$row.css("background", "#fff9c4");
			}
			this._recalc();
		});
	}

	// ── Recalculate solde ─────────────────────────────────────────────────────
	_recalc() {
		if (!this._data) return;

		const initial  = parseFloat($("#caisse-solde-initial").val()) || 0;
		const entrees  = this._sum(this._data.entrees_detail || []);
		const sorties  = this._sum(this._data.sorties_achat || []) + this._sum(this._data.sorties_dep || []);

		// only count versements that are NOT excluded
		let versements = 0;
		(this._data.versements || []).forEach(r => {
			const ref = r.journal_entry_number || r.ref || "";
			if (!this._excluded.has(ref)) {
				versements += parseFloat(r.montant) || 0;
			}
		});

		const solde = initial + entrees - sorties - versements;

		$("#card-entrees").text(this._fmt(entrees) + " DT");
		$("#card-sorties").text(this._fmt(sorties) + " DT");
		$("#card-versements").text(this._fmt(versements) + " DT");

		const $solde = $("#card-solde");
		$solde.text(this._fmt(solde) + " DT");
		const parent = $solde.closest(".caisse-solde");
		if (solde < 0) {
			parent.css("background", "#ffe0b2");
			$solde.css("color", "#e65100");
		} else {
			parent.css("background", "#bbdefb");
			$solde.css("color", "#1565c0");
		}
	}

	// ── Evolution Chart ───────────────────────────────────────────────────────
	async _toggle_chart() {
		const $section = $("#caisse-chart-section");
		if ($section.is(":visible")) {
			$section.hide();
			return;
		}
		$section.show();
		await this._render_chart();
	}

	async _render_chart() {
		if (!this._data) {
			frappe.msgprint("Veuillez d'abord actualiser les données.");
			return;
		}
		const d1      = $("#caisse-d1").val();
		const d2      = $("#caisse-d2").val();
		const initial = parseFloat($("#caisse-solde-initial").val()) || 0;

		// Build per-day maps from already-fetched data
		const daily_entrees   = {};
		const daily_sorties   = {};
		const daily_versements = {};

		(this._data.entrees_detail || []).forEach(r => {
			daily_entrees[r.date] = (daily_entrees[r.date] || 0) + (parseFloat(r.montant) || 0);
		});
		[...(this._data.sorties_achat || []), ...(this._data.sorties_dep || [])].forEach(r => {
			daily_sorties[r.date] = (daily_sorties[r.date] || 0) + (parseFloat(r.montant) || 0);
		});
		(this._data.versements || []).forEach(r => {
			const ref = r.journal_entry_number || r.ref || "";
			if (!this._excluded.has(ref)) {
				daily_versements[r.date] = (daily_versements[r.date] || 0) + (parseFloat(r.montant) || 0);
			}
		});

		// Generate all dates in range
		const dates = [];
		let cur = new Date(d1);
		const end = new Date(d2);
		while (cur <= end) {
			dates.push(cur.toISOString().slice(0, 10));
			cur.setDate(cur.getDate() + 1);
		}

		// Compute cumulative solde per day
		let running = initial;
		const labels  = [];
		const values  = [];
		dates.forEach(d => {
			running += (daily_entrees[d] || 0);
			running -= (daily_sorties[d] || 0);
			running -= (daily_versements[d] || 0);
			labels.push(d);
			values.push(parseFloat(running.toFixed(3)));
		});

		const $container = $("#caisse-chart-container").empty();

		new frappe.Chart($container[0], {
			title: `Solde Caisse — ${d1} → ${d2}`,
			data: {
				labels,
				datasets: [{
					name: "Solde (DT)",
					values,
					chartType: "line",
				}],
			},
			type: "axis-mixed",
			height: 300,
			colors: ["#7e57c2"],
			lineOptions: { regionFill: 1, hideDots: labels.length > 60 ? 1 : 0 },
			axisOptions: { xIsSeries: true },
			tooltipOptions: {
				formatTooltipY: v => this._fmt(v) + " DT",
			},
		});
	}

	// ── Export Excel (données + courbe d'évolution intégrée) ─────────────────
	// Reflète l'état courant de l'écran : solde initial saisi + exclusions
	// cochées, même non enregistrés. Tout le calcul est fait côté serveur.
	_export_excel() {
		const d1 = $("#caisse-d1").val();
		const d2 = $("#caisse-d2").val();
		if (!d1 || !d2) {
			frappe.msgprint("Veuillez renseigner les deux dates.");
			return;
		}
		const initial  = parseFloat($("#caisse-solde-initial").val()) || 0;
		const excluded = encodeURIComponent(JSON.stringify([...this._excluded]));
		window.open(
			`/api/method/customization_app.api.download_caisse_excel` +
			`?d1=${d1}&d2=${d2}&solde_initial=${initial}&excluded=${excluded}`
		);
	}

	// ── Save config and go back ───────────────────────────────────────────────
	async _save_and_back() {
		const solde_initial = parseFloat($("#caisse-solde-initial").val()) || 0;

		// Build excluded list from current _data versements + _excluded set
		const excluded_versements = [];
		(this._data ? this._data.versements || [] : []).forEach(r => {
			const ref = r.journal_entry_number || r.ref || "";
			if (this._excluded.has(ref)) {
				excluded_versements.push({
					versement_ref: ref,
					description:   r.description || "",
					montant:       parseFloat(r.montant) || 0,
				});
			}
		});
		// Also keep previously saved exclusions that may not be in current date range
		// (already in _excluded but not in current data) — they remain untouched in DB

		this._show_loading(true);
		const date_debut = $("#caisse-d1").val();
		const date_fin   = $("#caisse-d2").val();
		try {
			await frappe.call({
				method: "customization_app.api.save_caisse_config",
				args: { solde_initial, excluded_versements: JSON.stringify(excluded_versements), date_debut, date_fin },
			});
			frappe.show_alert({ message: "Configuration sauvegardée", indicator: "green" });
			setTimeout(() => frappe.set_route("Workspaces", "Accounting"), 800);
		} catch (e) {
			frappe.msgprint({ title: "Erreur de sauvegarde", message: String(e), indicator: "red" });
		} finally {
			this._show_loading(false);
		}
	}

	// ── Helpers ───────────────────────────────────────────────────────────────
	_sum(rows) {
		return rows.reduce((acc, r) => acc + (parseFloat(r.montant) || 0), 0);
	}

	_fmt(val) {
		const n = parseFloat(val) || 0;
		return n.toLocaleString("fr-TN", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
	}

	_show_loading(show) {
		$("#caisse-loading").css("display", show ? "flex" : "none");
	}
}
