frappe.pages["conges-recuperations"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Congés & récupérations", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("conges_recuperations", {}));
  new CongesRecuperations(wrapper);
};

// Rendu pur : planning, soldes, règles viennent de customization_app.conges_recuperations.get_context.
// Les absences et droits sont de vrais documents HRMS (Leave Application / Leave Allocation).

class CongesRecuperations {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".cr-page");
    const auj = new Date();
    this.annee = auj.getFullYear();
    this.mois = auj.getMonth() + 1;
    this.employee = "";
    this._data = null;
    this._sel = null; // sélection en cours dans le planning {employee, from, to}
    this._bind();
    this._fetch();
  }

  _bind() {
    this.$root.find("#cr-annee").on("change", (e) => { this.annee = parseInt(e.target.value, 10); this._fetch(); });
    this.$root.find("#cr-employe").on("change", (e) => { this.employee = e.target.value; this._fetch(); });
    this.$root.find("[data-action='mois-prec']").on("click", () => this._mois(-1));
    this.$root.find("[data-action='mois-suiv']").on("click", () => this._mois(1));
    this.$root.find("[data-action='mois-auj']").on("click", () => { const d = new Date(); this.annee = d.getFullYear(); this.mois = d.getMonth() + 1; this._fetch(); });
    this.$root.find("[data-action='print']").on("click", () => window.print());
    this.$root.find("[data-action='attribuer-tous']").on("click", () => this._dialog_attribuer(null));
    this.$root.find("[data-action='generer']").on("click", () => this._planifier());
    this.$root.on("click", ".cr-regle-suppr", (e) => {
      const employee = $(e.currentTarget).data("employee");
      frappe.confirm("Supprimer cette règle ? Les jours déjà posés restent dans le planning.", async () => {
        await frappe.call({ method: "customization_app.conges_recuperations.supprimer_regle", args: { employee } });
        this._fetch();
      });
    });
    this.$root.on("click", ".cr-attribuer", (e) => this._dialog_attribuer($(e.currentTarget).data("employee")));
    this.$root.on("click", ".cr-sel", (e) => { this.employee = $(e.currentTarget).data("employee"); this.$root.find("#cr-employe").val(this.employee); this._fetch(); });
    this.$root.on("click", ".cr-annuler-abs", (e) => this._annuler_absence($(e.currentTarget).data("name")));
    this.$root.on("click", ".cr-suppr-alloc", (e) => this._supprimer_allocation($(e.currentTarget).data("name")));
    this.$root.on("click", ".cr-regle", (e) => this._dialog_regle($(e.currentTarget).data("employee")));

    // Sélection cliquer-glisser sur une ligne du planning
    this.$root.on("mousedown", "td.d.libre", (e) => {
      if (e.button !== 0) return;
      const $c = $(e.currentTarget);
      this._sel = { employee: $c.data("employee"), from: $c.data("date"), to: $c.data("date") };
      this._peindre_sel();
      e.preventDefault();
    });
    this.$root.on("mouseover", "td.d", (e) => {
      if (!this._sel) return;
      const $c = $(e.currentTarget);
      if ($c.data("employee") !== this._sel.employee) return;
      this._sel.to = $c.data("date");
      this._peindre_sel();
    });
    $(document).on("mouseup.cr", () => {
      if (!this._sel) return;
      const s = this._sel; this._sel = null;
      const [a, b] = [s.from, s.to].sort();
      this.$root.find("td.d.sel").removeClass("sel");
      this._dialog_absence({ employee: s.employee, from_date: a, to_date: b });
    });
    this.$root.on("click", "td.d.pris", (e) => {
      const $c = $(e.currentTarget);
      this._popup_absence($c.data("name"));
    });
  }

  _peindre_sel() {
    const s = this._sel;
    const [a, b] = [s.from, s.to].sort();
    this.$root.find("td.d").each((_, el) => {
      const $c = $(el);
      $c.toggleClass("sel", $c.data("employee") === s.employee && $c.data("date") >= a && $c.data("date") <= b);
    });
  }

  _mois(delta) {
    let m = this.mois + delta, a = this.annee;
    if (m < 1) { m = 12; a -= 1; } else if (m > 12) { m = 1; a += 1; }
    this.mois = m; this.annee = a; this._fetch();
  }

  async _fetch() {
    try {
      const r = await frappe.call({ method: "customization_app.conges_recuperations.get_context",
        args: { annee: this.annee, mois: this.mois, employee: this.employee || null } });
      this._data = r.message;
    } catch (err) {
      if (err && err.exc_type === "PermissionError") { this.$root.find("#cr-main").hide(); this.$root.find("#cr-denied").show(); }
      throw err;
    }
    this._render();
  }

  _fmt(v) { return format_number(v || 0, null, 1).replace(/\.0$/, ""); }
  _cls_lettre(l) { return l === "V" || l === "V′" ? "t-V" : l === "R" ? "t-R" : l === "M" ? "t-M" : l === "S" ? "t-S" : "t-X"; }

  _render() {
    const d = this._data;
    // filtres
    const $a = this.$root.find("#cr-annee").empty();
    const auj = new Date().getFullYear();
    for (let y = auj + 1; y >= auj - 3; y--) $a.append(`<option value="${y}">${y}</option>`);
    $a.val(String(d.annee));
    const $e = this.$root.find("#cr-employe").empty().append('<option value="">Tous</option>');
    d.employes.forEach((e) => $e.append(`<option value="${e.employee}">${frappe.utils.escape_html(e.employee_name)}</option>`));
    $e.val(this.employee || "");
    this.$root.find("#cr-mois").text(d.libelle_mois.charAt(0).toUpperCase() + d.libelle_mois.slice(1));

    this._render_planning();
    this._render_soldes();
    this._render_detail();
    this._render_regles();
  }

  _render_planning() {
    const d = this._data;
    let h = '<table class="cr-grid"><thead><tr><th class="emp">Employé</th>';
    d.jours.forEach((j) => { h += `<th class="${j.weekend ? "we" : ""}">${j.semaine}<br>${j.jour}</th>`; });
    h += "</tr></thead><tbody>";
    d.planning.filter((p) => !this.employee || p.employee === this.employee).forEach((p) => {
      h += `<tr><td class="emp">${p.image ? `<img class="cr-avatar" src="${p.image}">` : ""}<span class="cr-sel" data-employee="${p.employee}">${frappe.utils.escape_html(p.employee_name)}</span></td>`;
      d.jours.forEach((j) => {
        const c = p.cases[j.date], f = p.feries[j.date];
        const auj = j.date === d.aujourdhui ? " auj" : "";
        if (c) {
          h += `<td class="d pris ${this._cls_lettre(c.lettre)}${c.demi ? " demi" : ""}${auj}" data-employee="${p.employee}" data-date="${j.date}" data-name="${c.name}" title="${frappe.utils.escape_html(c.type)}${c.demi ? " (demi-journée)" : ""}">${c.lettre}</td>`;
        } else if (f) {
          h += `<td class="d ferie${auj}" data-employee="${p.employee}" data-date="${j.date}" title="${frappe.utils.escape_html(f.description || "Férié")}"></td>`;
        } else {
          h += `<td class="d libre${j.weekend ? " we" : ""}${auj}" data-employee="${p.employee}" data-date="${j.date}"></td>`;
        }
      });
      h += "</tr>";
    });
    h += "</tbody></table>";
    this.$root.find("#cr-planning").html(h);
  }

  _render_soldes() {
    const d = this._data;
    const types = d.types.filter((t) => d.employes.some((e) => d.soldes[e.employee] && d.soldes[e.employee][t]) || t === d.type_recup);
    if (!types.length) { this.$root.find("#cr-soldes").html('<div class="cr-empty">Aucun droit attribué cette année.</div>'); return; }
    let h = '<table class="cr-tbl"><thead><tr><th rowspan="2">Employé</th>';
    types.forEach((t) => { h += `<th class="grp" colspan="3">${frappe.utils.escape_html(t)}</th>`; });
    h += '<th rowspan="2"></th></tr><tr>';
    types.forEach(() => { h += '<th class="num first">Acquis</th><th class="num">Pris</th><th class="num">Reste</th>'; });
    h += "</tr></thead><tbody>";
    d.employes.forEach((e) => {
      const s = d.soldes[e.employee] || {};
      h += `<tr class="${this.employee === e.employee ? "actif" : ""}"><td><b><span class="cr-sel" data-employee="${e.employee}">${frappe.utils.escape_html(e.employee_name)}</span></b></td>`;
      types.forEach((t) => {
        const x = s[t];
        if (!x) { h += '<td class="num first cr-muted">—</td><td class="num cr-muted">—</td><td class="num cr-muted">—</td>'; return; }
        const cls = x.reste > 0.001 ? "cr-pos" : x.reste < -0.001 ? "cr-neg" : "cr-muted";
        h += `<td class="num first">${this._fmt(x.acquis)}${x.expire ? `<span class="cr-muted" title="expirés"> (−${this._fmt(x.expire)})</span>` : ""}</td><td class="num">${this._fmt(x.pris)}</td><td class="num ${cls}">${this._fmt(x.reste)}</td>`;
      });
      h += `<td>${d.peut_modifier ? `<button class="btn btn-default btn-xs cr-attribuer" data-employee="${e.employee}">+ Attribuer</button>` : ""}</td></tr>`;
    });
    h += "</tbody></table>";
    this.$root.find("#cr-soldes").html(h);
  }

  _render_detail() {
    const d = this._data;
    const $card = this.$root.find("#cr-card-detail");
    if (!this.employee) { $card.hide(); return; }
    const emp = d.employes.find((e) => e.employee === this.employee);
    $card.show();
    this.$root.find("#cr-detail-titre").html(`${frappe.utils.escape_html(emp ? emp.employee_name : this.employee)} <span class="cr-sub">absences et droits ${d.annee}</span>`);
    let h = "<div style='display:grid;grid-template-columns:1fr 1fr;gap:14px'><div>";
    h += `<div class="cr-title" style="font-size:12.5px">Absences (${d.absences_annee.length})</div>`;
    if (!d.absences_annee.length) h += '<div class="cr-empty">Aucune absence cette année.</div>';
    else {
      h += '<table class="cr-tbl"><thead><tr><th>Du</th><th>Au</th><th>Type</th><th class="num">Jours</th><th>Motif</th><th></th></tr></thead><tbody>';
      d.absences_annee.forEach((a) => {
        h += `<tr><td>${frappe.datetime.str_to_user(a.from_date)}</td><td>${frappe.datetime.str_to_user(a.to_date)}</td>
          <td><span class="cr-leg ${this._cls_lettre(a.leave_type ? this._lettre(a.leave_type) : "X")}">${this._lettre(a.leave_type)}</span> ${frappe.utils.escape_html(a.leave_type)}</td>
          <td class="num">${this._fmt(a.total_leave_days)}</td><td class="cr-muted">${frappe.utils.escape_html(a.description || "")}</td>
          <td><a href="/app/leave-application/${a.name}" target="_blank" title="Ouvrir">↗</a> ${d.peut_modifier ? `<span class="cr-del cr-annuler-abs" data-name="${a.name}" title="Annuler">🗑️</span>` : ""}</td></tr>`;
      });
      h += "</tbody></table>";
    }
    h += "</div><div>";
    h += `<div class="cr-title" style="font-size:12.5px">Droits attribués (${d.allocations_annee.length})</div>`;
    if (!d.allocations_annee.length) h += '<div class="cr-empty">Aucune allocation cette année.</div>';
    else {
      h += '<table class="cr-tbl"><thead><tr><th>Type</th><th>Période</th><th class="num">Nouveaux</th><th class="num">Reportés</th><th class="num">Total</th><th></th></tr></thead><tbody>';
      d.allocations_annee.forEach((a) => {
        h += `<tr><td>${frappe.utils.escape_html(a.leave_type)}</td><td>${frappe.datetime.str_to_user(a.from_date)} → ${frappe.datetime.str_to_user(a.to_date)}</td>
          <td class="num">${this._fmt(a.new_leaves_allocated)}</td><td class="num">${this._fmt(a.unused_leaves)}</td><td class="num"><b>${this._fmt(a.total_leaves_allocated)}</b></td>
          <td><a href="/app/leave-allocation/${a.name}" target="_blank" title="Ouvrir">↗</a> ${d.peut_modifier ? `<span class="cr-del cr-suppr-alloc" data-name="${a.name}" title="Annuler l’allocation">🗑️</span>` : ""}</td></tr>`;
      });
      h += "</tbody></table>";
    }
    h += "</div></div>";
    this.$root.find("#cr-detail").html(h);
  }

  _lettre(t) {
    const x = (t || "").toLowerCase();
    if (x.includes("récup") || x.includes("recup")) return "R";
    if (x.includes("maladie")) return "M";
    if (x.includes("sans solde")) return "S";
    if (x.includes("report")) return "V′";
    if (x.includes("vacance") || x.includes("congé")) return "V";
    return (t || "?").charAt(0).toUpperCase();
  }

  _render_regles() {
    const d = this._data;
    let h = '<table class="cr-tbl"><thead><tr><th>Employé</th><th>Premier jour</th><th class="num">Toutes les</th><th class="num">Planifié à l’avance</th><th>Dernier jour planifié</th><th class="num">Jours cette année</th><th>État</th><th></th></tr></thead><tbody>';
    d.regles.forEach((r) => {
      const etat = !r.existe ? '<span class="cr-muted">aucune règle</span>' : r.actif ? '<span class="cr-badge" style="background:#d1fae5;color:#065f46">active</span>' : '<span class="cr-badge" style="background:#eee;color:#666">inactive</span>';
      h += `<tr><td><b>${frappe.utils.escape_html(r.employee_name)}</b></td>
        <td>${r.date_premiere ? frappe.datetime.str_to_user(r.date_premiere) : "—"}</td>
        <td class="num">${r.existe ? `${r.periodicite_semaines} sem.` : "—"}</td>
        <td class="num">${r.existe ? `${r.horizon_semaines} sem.` : "—"}</td>
        <td>${r.derniere ? frappe.datetime.str_to_user(r.derniere) : '<span class="cr-muted">—</span>'}</td>
        <td class="num">${this._fmt(r.jours_annee)}</td><td>${etat}</td>
        <td style="white-space:nowrap">${d.peut_modifier ? `<button class="btn btn-default btn-xs cr-regle" data-employee="${r.employee}">✏️ Règle</button> ${r.existe ? `<span class="cr-del cr-regle-suppr" data-employee="${r.employee}" title="Supprimer la règle">🗑️</span>` : ""}` : ""}</td></tr>`;
    });
    h += "</tbody></table>";
    this.$root.find("#cr-regles").html(h);
  }

  // ---------------------------------------------------------------- dialogues

  _dialog_absence(pre) {
    const d = this._data;
    const emp = d.employes.find((e) => e.employee === pre.employee);
    const dlg = new frappe.ui.Dialog({
      title: `Poser une absence — ${emp ? emp.employee_name : pre.employee}`,
      fields: [
        { fieldname: "leave_type", fieldtype: "Select", label: "Type", reqd: 1, options: d.types, default: pre.leave_type || d.types[0] },
        { fieldname: "from_date", fieldtype: "Date", label: "Du", reqd: 1, default: pre.from_date },
        { fieldname: "to_date", fieldtype: "Date", label: "Au", reqd: 1, default: pre.to_date },
        { fieldname: "half_day", fieldtype: "Check", label: "Demi-journée" },
        { fieldname: "half_day_date", fieldtype: "Date", label: "Date de la demi-journée", depends_on: "half_day", default: pre.from_date },
        { fieldname: "motif", fieldtype: "Small Text", label: "Motif" },
      ],
      primary_action_label: "Enregistrer (approuvée)",
      primary_action: async (v) => {
        const r = await frappe.call({ method: "customization_app.conges_recuperations.affecter_absence",
          args: { employee: pre.employee, ...v }, freeze: true });
        dlg.hide();
        frappe.show_alert({ message: `Absence ${r.message.name} posée (${r.message.jours} j)`, indicator: "green" });
        this._fetch();
      },
    });
    dlg.show();
  }

  async _popup_absence(name) {
    const r = await frappe.db.get_doc("Leave Application", name);
    const dlg = new frappe.ui.Dialog({
      title: `${r.employee_name} — ${r.leave_type}`,
      fields: [{ fieldname: "h", fieldtype: "HTML", options: `
        <div>Du <b>${frappe.datetime.str_to_user(r.from_date)}</b> au <b>${frappe.datetime.str_to_user(r.to_date)}</b> · <b>${r.total_leave_days}</b> jour(s)${r.half_day ? " · demi-journée" : ""}</div>
        <div class="cr-muted" style="margin-top:6px">${frappe.utils.escape_html(r.description || "")}</div>
        <div style="margin-top:8px"><a href="/app/leave-application/${r.name}" target="_blank">${r.name} ↗</a></div>` }],
      primary_action_label: "Annuler cette absence",
      primary_action: () => { dlg.hide(); this._annuler_absence(name); },
    });
    dlg.show();
  }

  _annuler_absence(name) {
    frappe.confirm(`Annuler l’absence ${name} ? Les jours reviennent au solde.`, async () => {
      await frappe.call({ method: "customization_app.conges_recuperations.annuler_absence", args: { name }, freeze: true });
      frappe.show_alert({ message: "Absence annulée", indicator: "orange" });
      this._fetch();
    });
  }

  _dialog_attribuer(employee) {
    const d = this._data;
    const tous = !employee;
    const emp = employee ? d.employes.find((e) => e.employee === employee) : null;
    const dlg = new frappe.ui.Dialog({
      title: tous ? `Attribuer des congés à tous (${d.employes.length} employés)` : `Attribuer des congés — ${emp.employee_name}`,
      fields: [
        { fieldname: "leave_type", fieldtype: "Select", label: "Type", reqd: 1, options: d.types.filter((t) => !t.toLowerCase().includes("sans solde")), default: d.types[0] },
        { fieldname: "jours", fieldtype: "Float", label: "Nombre de jours", reqd: 1, default: 18 },
        { fieldname: "from_date", fieldtype: "Date", label: "Du", reqd: 1, default: `${d.annee}-01-01` },
        { fieldname: "to_date", fieldtype: "Date", label: "Au", reqd: 1, default: `${d.annee}-12-31` },
        { fieldname: "carry_forward", fieldtype: "Check", label: "Reporter les jours non pris de l’année précédente",
          description: "Ne s’applique qu’à une nouvelle allocation, pour un type reportable." },
        { fieldname: "completer", fieldtype: "Check", label: "S’il existe déjà une allocation sur la période, l’augmenter", default: 1 },
      ],
      primary_action_label: "Attribuer",
      primary_action: async (v) => {
        const employees = tous ? d.employes.map((e) => e.employee) : [employee];
        const r = await frappe.call({ method: "customization_app.conges_recuperations.attribuer",
          args: { employees, ...v }, freeze: true });
        dlg.hide();
        const n = (r.message || []).length, aug = (r.message || []).filter((x) => x.action === "augmentée").length;
        frappe.show_alert({ message: `${n} allocation(s) : ${n - aug} créée(s), ${aug} augmentée(s)`, indicator: "green" });
        this._fetch();
      },
    });
    dlg.show();
  }

  _supprimer_allocation(name) {
    frappe.confirm(`Annuler l’allocation ${name} ? Refusé par HRMS si des congés ont été pris dessus.`, async () => {
      await frappe.call({ method: "customization_app.conges_recuperations.supprimer_allocation", args: { name }, freeze: true });
      frappe.show_alert({ message: "Allocation annulée", indicator: "orange" });
      this._fetch();
    });
  }

  _dialog_regle(employee) {
    const d = this._data;
    const r = d.regles.find((x) => x.employee === employee);
    const dlg = new frappe.ui.Dialog({
      title: `Récupération — ${r.employee_name}`,
      fields: [
        { fieldname: "date_premiere", fieldtype: "Date", label: "Premier jour de récupération", reqd: 1, default: r.date_premiere || frappe.datetime.get_today(),
          description: "Les suivants tombent le même jour de la semaine." },
        { fieldname: "periodicite_semaines", fieldtype: "Int", label: "Toutes les N semaines", reqd: 1, default: r.periodicite_semaines || 2 },
        { fieldname: "horizon_semaines", fieldtype: "Int", label: "Planifier à l’avance (semaines)", reqd: 1, default: r.horizon_semaines || 8,
          description: "Chaque jour planifié = absence approuvée « Récupération » + tâche « Jour de récupération » sur toute la journée dans le calendrier. Le solde est crédité d’office." },
        { fieldname: "actif", fieldtype: "Check", label: "Règle active", default: r.existe ? r.actif : 1 },
      ],
      primary_action_label: "Enregistrer",
      primary_action: async (v) => {
        const r = await frappe.call({ method: "customization_app.conges_recuperations.enregistrer_regle", args: { employee, ...v }, freeze: true, freeze_message: "Planification…" });
        dlg.hide();
        const m = r.message || {};
        const poses = (m.poses || []).map((p) => frappe.datetime.str_to_user(p));
        frappe.msgprint({ title: "Règle enregistrée", indicator: "green",
          message: (poses.length ? `${poses.length} jour(s) de récupération posé(s) : ${poses.join(", ")}.` : "Aucun nouveau jour à poser pour l’instant.")
            + ((m.sautes || []).length ? `<div class="cr-muted">Sautés : ${m.sautes.map((x) => frappe.utils.escape_html(x)).join(", ")}</div>` : "")
            + "<div class='cr-muted' style='margin-top:6px'>Chaque jour = absence approuvée « Récupération » + tâche « Jour de récupération » toute la journée dans le calendrier. La suite est posée automatiquement chaque jour, jusqu’à l’horizon.</div>" });
        this._fetch();
      },
    });
    dlg.show();
  }

  _planifier() {
    frappe.confirm("Poser les jours de récupération de chaque règle active jusqu’à son horizon ? Chaque jour devient une absence approuvée et une tâche « Jour de récupération » dans le calendrier.", async () => {
      const r = await frappe.call({ method: "customization_app.conges_recuperations.planifier_recuperations", freeze: true, freeze_message: "Planification…" });
      const res = r.message || [];
      const lignes = res.map((x) => `<li><b>${frappe.utils.escape_html(x.employee_name)}</b> : ${x.poses.length} jour(s) posé(s)${x.poses.length ? " (" + x.poses.map((p) => frappe.datetime.str_to_user(p)).join(", ") + ")" : ""}${x.sautes.length ? `<div class="cr-muted">sautés : ${x.sautes.map((p) => frappe.utils.escape_html(p)).join(", ")}</div>` : ""}</li>`);
      frappe.msgprint({ title: "Récupérations planifiées", indicator: "green",
        message: lignes.length ? "<ul>" + lignes.join("") + "</ul>" : "Rien à poser : tout est déjà planifié jusqu’à l’horizon, ou aucune règle active." });
      this._fetch();
    });
  }
}
