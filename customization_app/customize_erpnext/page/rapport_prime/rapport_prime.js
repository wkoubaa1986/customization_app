frappe.pages["rapport-prime"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Rapport Prime",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("rapport_prime", {}));
  new RapportPrime(wrapper);
};

// Rendu pur : tous les montants (prime, versé, reste, cumuls) viennent de
// customization_app.rapport_prime.get_data. Les filtres sont dans l'URL
// (?annee=2026&employee=HR-EMP-00002) comme l'ancien rapport.

class RapportPrime {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".rp-page");
    this._data = null;
    const q = frappe.utils.get_query_params ? frappe.utils.get_query_params() : {};
    this.annee = q.annee || String(new Date().getFullYear());
    this.employee = q.employee || "";
    this._bind();
    this._fetch();
  }

  _bind() {
    this.$root.find("#rp-annee").on("change", (e) => { this.annee = e.target.value; this._fetch(); });
    this.$root.find("#rp-employe").on("change", (e) => { this.employee = e.target.value; this._fetch(); });
    this.$root.find("[data-action='refresh']").on("click", () => this._fetch());
    this.$root.find("[data-action='print']").on("click", () => window.print());
    this.$root.find("[data-action='config']").on("click", () => frappe.set_route("Form", "Config Prime"));
    this.$root.find("[data-action='ajouter']").on("click", () => this._dialog_versement({}));
    this.$root.on("click", "tr.rp-emp", (e) => {
      this.employee = $(e.currentTarget).data("employee");
      this.$root.find("#rp-employe").val(this.employee);
      this._fetch();
    });
    this.$root.on("click", ".rp-del", (e) => this._supprimer($(e.currentTarget).data("name")));
    this.$root.on("click", ".rp-appr", (e) => {
      e.preventDefault();
      this._dialog_appreciation($(e.currentTarget).data("trimestre"));
    });
    this.$root.on("click", ".rp-coef", (e) => {
      e.preventDefault();
      const $a = $(e.currentTarget);
      this._dialog_coefficient($a.data("trimestre"), $a.data("coef"), $a.data("remarque"));
    });
    this.$root.on("click", ".rp-rattacher", (e) => {
      const $b = $(e.currentTarget);
      this._dialog_versement({
        journal_entry: $b.data("name"), montant: $b.data("montant"),
        total_ecriture: $b.data("total"), deja_rattache: $b.data("deja"),
        date_versement: $b.data("date"), remarque: $b.data("remarque"),
      });
    });
    this.$root.on("click", ".rp-toggle", (e) => {
      const t = $(e.currentTarget).data("trimestre");
      this.$root.find(`tr[data-trim='${t}']`).toggle();
    });
  }

  async _fetch() {
    this.$root.find("#rp-synthese").html('<div class="rp-loading">⏳ Calcul en cours…</div>');
    try {
      const r = await frappe.call({
        method: "customization_app.rapport_prime.get_data",
        args: { annee: this.annee, employee: this.employee || null },
        freeze: false,
      });
      this._data = r.message;
    } catch (err) {
      if (err && err.exc_type === "PermissionError") {
        this.$root.find("#rp-main").hide();
        this.$root.find("#rp-denied").show();
      }
      throw err;
    }
    this._sync_url();
    this._render();
  }

  _sync_url() {
    const params = new URLSearchParams();
    params.set("annee", this.annee);
    if (this.employee) params.set("employee", this.employee);
    history.replaceState(null, "", `${location.pathname}?${params}`);
  }

  // Montants sans symbole (« د.ت » sur ce site) : la synthèse tient en largeur et se lit
  // comme un tableau de chiffres ; la devise est rappelée dans la ligne de règle.
  _fmt(v) {
    return format_number(v || 0, null, 3);
  }

  _reste(v) {
    const n = flt(v);
    const cls = n > 0.0005 ? "rp-pos" : n < -0.0005 ? "rp-neg" : "rp-zero";
    return `<span class="${cls}">${this._fmt(n)}</span>`;
  }

  _render() {
    const d = this._data;
    // Filtres
    const $a = this.$root.find("#rp-annee").empty();
    d.annees.forEach((y) => $a.append(`<option value="${y}">${y}</option>`));
    $a.val(String(d.annee));
    const $e = this.$root.find("#rp-employe").empty().append('<option value="">Tous (synthèse)</option>');
    d.employes.forEach((e) => $e.append(`<option value="${e.employee}">${frappe.utils.escape_html(e.employee_name)}</option>`));
    if (d.detail && !d.employes.some((e) => e.employee === d.detail.employee)) {
      $e.append(`<option value="${d.detail.employee}">${frappe.utils.escape_html(d.detail.employee_name)}</option>`);
    }
    $e.val(this.employee || "");

    this.$root.find("#rp-regle").html(
      `Ventes : <b>${flt(d.taux_vente * 100, 2)} %</b> du HT éligible (hors main-d'œuvre et livraison). ` +
      `Main-d'œuvre : <b>${flt(d.taux_mo * 100, 2)} %</b> du HT de main-d'œuvre des commandes dont l'employé a réalisé la tâche (partagé si plusieurs techniciens). ` +
      `Commandes livrées du <b>${frappe.datetime.str_to_user(d.periode.debut)}</b> au ` +
      `<b>${frappe.datetime.str_to_user(d.periode.fin)}</b> (fin du ${d.periode.libelle}), ` +
      `rangées dans le trimestre de leur première livraison. Reste à verser = calculé − versé. Montants en ${d.currency}.`
    );

    this._render_synthese();
    if (d.detail) {
      this.$root.find("#rp-detail").show();
      this._render_detail();
    } else {
      this.$root.find("#rp-detail").hide();
    }
  }

  _render_synthese() {
    const d = this._data;
    if (!d.synthese.length) {
      this.$root.find("#rp-synthese").html('<div class="rp-empty">Aucun vendeur pour cette année.</div>');
      return;
    }
    // Seuls les trimestres déjà ouverts : les suivants sont à zéro et repousseraient
    // la colonne « Total à ce jour » hors de l'écran.
    const trims = d.synthese[0].trimestres.filter((t) => t.ouvert);
    let h = '<table class="rp-tbl"><thead><tr><th rowspan="2">Employé</th>';
    trims.forEach((t) => { h += `<th class="grp" colspan="3">${t.libelle}</th>`; });
    h += '<th class="grp" colspan="3">Total à ce jour</th></tr><tr>';
    for (let i = 0; i <= trims.length; i++) h += '<th class="num first">Calculé</th><th class="num">Versé</th><th class="num">Reste</th>';
    h += "</tr></thead><tbody>";
    const tot = { t: trims.map(() => ({ c: 0, v: 0, r: 0 })), c: 0, v: 0, r: 0 };
    d.synthese.forEach((s) => {
      const actif = s.employee === this.employee ? " actif" : "";
      h += `<tr class="rp-emp${actif}" data-employee="${s.employee}"><td><b>${frappe.utils.escape_html(s.employee_name)}</b>
        <div class="rp-muted" style="font-size:10.5px">${frappe.utils.escape_html(s.mode || "Ventes")}</div></td>`;
      s.trimestres.filter((t) => t.ouvert).forEach((t, i) => {
        const coef = flt(t.coefficient, 2) !== 100 ? ` <span class="rp-muted" title="brut ${this._fmt(t.brut)}">×${flt(t.coefficient, 2)} %</span>` : "";
        h += `<td class="num first">${this._fmt(t.calcule)}${coef}</td><td class="num">${this._fmt(t.verse)}</td><td class="num">${this._reste(t.reste)}</td>`;
        tot.t[i].c += t.calcule; tot.t[i].v += t.verse; tot.t[i].r += t.reste;
      });
      h += `<td class="num first">${this._fmt(s.total_calcule)}</td><td class="num">${this._fmt(s.total_verse)}</td><td class="num">${this._reste(s.reste)}</td></tr>`;
      tot.c += s.total_calcule; tot.v += s.total_verse; tot.r += s.reste;
    });
    h += '<tr class="rp-total"><td>Total</td>';
    tot.t.forEach((t) => { h += `<td class="num first">${this._fmt(t.c)}</td><td class="num">${this._fmt(t.v)}</td><td class="num">${this._reste(t.r)}</td>`; });
    h += `<td class="num first">${this._fmt(tot.c)}</td><td class="num">${this._fmt(tot.v)}</td><td class="num">${this._reste(tot.r)}</td></tr>`;
    h += "</tbody></table>";
    this.$root.find("#rp-synthese").html(h);
  }

  _render_detail() {
    const d = this._data, det = d.detail;
    this.$root.find("#rp-det-nom").html(
      `${frappe.utils.escape_html(det.employee_name)} <span class="rp-sub">${det.employee} · ${d.annee} · <span class="rp-badge">${frappe.utils.escape_html(det.mode || "Ventes")}</span></span>
       <button class="btn btn-default btn-xs" data-action="pdf" style="margin-left:auto" title="PDF de cet employé : trimestres, versements, commandes">📄 PDF</button>`);
    this.$root.find("[data-action='pdf']").on("click", () => {
      const params = new URLSearchParams({ annee: d.annee, employee: det.employee });
      window.open(`/api/method/customization_app.rapport_prime.telecharger_pdf?${params}`);
    });

    // KPI par trimestre + total
    let k = "";
    det.trimestres.forEach((t) => {
      k += `<div class="rp-kpi${t.ouvert ? "" : " ferme"}">
        <div class="k-lbl">${t.libelle}${t.ouvert ? "" : " · à venir"}</div>
        <div class="k-val">${this._fmt(t.calcule)}</div>
        ${this._coef_line(det, t, d)}
        ${this._ventilation(det, t.calcule_vente, t.calcule_mo, t.commandes, t.taches)}
        <div class="k-line"><span>Versé</span><b>${this._fmt(t.verse)}</b></div>
        <div class="k-line"><span>Reste</span>${t.ouvert ? this._reste(t.reste) : "—"}</div>
        <div class="k-line rp-muted"><span>Cumul reste</span><span>${t.ouvert ? this._fmt(t.cumul_reste) : "—"}</span></div>
        ${this._appreciation_bloc(t, d)}
      </div>`;
    });
    k += `<div class="rp-kpi total">
        <div class="k-lbl">Total à ce jour</div>
        <div class="k-val">${this._reste(det.reste)}</div>
        <div class="k-line"><span>Calculé</span><b>${this._fmt(det.total_calcule)}</b></div>
        ${flt(det.total_brut) !== flt(det.total_calcule) ? `<div class="k-line rp-muted"><span>brut avant coefficient</span><span>${this._fmt(det.total_brut)}</span></div>` : ""}
        ${this._ventilation(det, det.total_vente, det.total_mo)}
        <div class="k-line"><span>Versé</span><b>${this._fmt(det.total_verse)}</b></div>
        <div class="k-line rp-muted"><span>reste à verser</span></div>
      </div>`;
    if (det.hors_annee) {
      k += `<div class="rp-kpi"><div class="k-lbl">Hors année</div><div class="k-val rp-muted">${this._fmt(det.hors_annee)}</div>
        <div class="k-line rp-muted"><span>commandes livrées avant ${d.annee}, non comptées</span></div></div>`;
    }
    this.$root.find("#rp-kpis").html(k);

    // Versements
    let v = "";
    if (!det.versements.length) {
      v = '<div class="rp-empty">Aucun versement enregistré pour cette année.</div>';
    } else {
      v = '<table class="rp-tbl"><thead><tr><th>Date</th><th>Trimestre</th><th class="num">Montant</th><th>Écriture de caisse</th><th>Remarque</th><th></th></tr></thead><tbody>';
      det.versements.forEach((x) => {
        v += `<tr><td>${frappe.datetime.str_to_user(x.date_versement)}</td><td><span class="rp-badge">${x.trimestre}</span></td>
          <td class="num"><b>${this._fmt(x.montant)}</b></td>
          <td>${x.journal_entry ? `<a href="/app/journal-entry/${x.journal_entry}" target="_blank">${x.journal_entry}</a>${cint(x.ecriture_soldee) ? ' <span class="rp-muted" title="Le reste de l\'écriture est une autre prime">(part)</span>' : ""}` : '<span class="rp-muted">—</span>'}</td>
          <td class="rp-muted">${frappe.utils.escape_html(x.remarque || "")}</td>
          <td>${d.peut_modifier ? `<span class="rp-del" data-name="${x.name}" title="Supprimer">🗑️</span>` : ""}</td></tr>`;
      });
      v += `<tr class="rp-total"><td colspan="2">Total versé</td><td class="num">${this._fmt(det.total_verse)}</td><td colspan="3"></td></tr></tbody></table>`;
    }
    this.$root.find("#rp-versements").html(v);
    this.$root.find("[data-action='ajouter']").toggle(!!d.peut_modifier);

    // Écritures de caisse « prime » non rattachées (tous employés : c'est à l'utilisateur de dire à qui)
    let c = "";
    if (d.peut_modifier && d.ecritures_candidates.length) {
      c = `<div class="rp-title" style="font-size:12.5px">Écritures de caisse « prime » de ${d.annee} pas entièrement rattachées
        <span class="rp-sub">« Rattacher » affecte le reste à ${frappe.utils.escape_html(det.employee_name)} — réduisez le montant si la prime n'en est qu'une part (écriture partagée entre deux vendeurs, ou qui contient autre chose)</span></div>
        <table class="rp-tbl rp-cand"><thead><tr><th>Date</th><th>Écriture</th><th class="num">Montant</th><th class="num">Déjà rattaché</th><th class="num">Reste</th><th>Remarque</th><th></th></tr></thead><tbody>`;
      d.ecritures_candidates.forEach((x) => {
        c += `<tr><td>${frappe.datetime.str_to_user(x.date)}</td><td><a href="/app/journal-entry/${x.name}" target="_blank">${x.name}</a></td>
          <td class="num">${this._fmt(x.montant)}</td><td class="num${x.deja_rattache ? "" : " rp-muted"}">${this._fmt(x.deja_rattache)}</td>
          <td class="num"><b>${this._fmt(x.reste)}</b></td><td class="rp-muted">${frappe.utils.escape_html(x.remarque)}</td>
          <td><button class="btn btn-default btn-xs rp-rattacher" data-name="${x.name}" data-montant="${x.reste}" data-total="${x.montant}"
              data-deja="${x.deja_rattache}" data-date="${x.date}" data-remarque="${frappe.utils.escape_html(x.remarque)}">Rattacher</button></td></tr>`;
      });
      c += "</tbody></table>";
    }
    this.$root.find("#rp-candidates").html(c);

    // Main-d'œuvre réalisée (tâches)
    this._render_taches(det, d);

    // Commandes par trimestre
    this.$root.find("#rp-det-sub").text(`${det.lignes.length} commande${det.lignes.length > 1 ? "s" : ""}`);
    this.$root.find("#rp-card-lignes").toggle(!!(det.lignes.length || this._compte_ventes(det.mode)));
    if (!det.lignes.length) {
      this.$root.find("#rp-lignes").html('<div class="rp-empty">Aucune commande livrée sur la période.</div>');
      return;
    }
    let h = `<table class="rp-tbl"><thead><tr><th>Commande</th><th>Date commande</th><th>Livraison</th><th>Client</th>
      <th class="num">TTC</th><th class="num">TTC éligible</th><th class="num">HT</th><th class="num">HT éligible</th><th class="num">Prime</th><th>Type</th></tr></thead><tbody>`;
    const groupes = {};
    det.lignes.forEach((l) => { (groupes[l.trimestre || "hors"] = groupes[l.trimestre || "hors"] || []).push(l); });
    const ordre = ["Q1", "Q2", "Q3", "Q4", "hors"];
    ordre.forEach((t) => {
      const ls = groupes[t];
      if (!ls) return;
      const trim = det.trimestres.find((x) => x.code === t);
      const lib = trim ? trim.libelle : `Hors ${d.annee} (non comptées)`;
      const tot = ls.reduce((a, l) => ({ ttc: a.ttc + l.ttc, ttc_e: a.ttc_e + l.ttc_e, ht: a.ht + l.ht, ht_e: a.ht_e + l.ht_e, p: a.p + l.prime }),
        { ttc: 0, ttc_e: 0, ht: 0, ht_e: 0, p: 0 });
      h += `<tr class="rp-trim"><td colspan="4"><span class="rp-toggle" data-trimestre="${t}">▾ ${lib}</span> <span class="rp-muted">· ${ls.length} commande${ls.length > 1 ? "s" : ""}</span></td>
        <td class="num">${this._fmt(tot.ttc)}</td><td class="num">${this._fmt(tot.ttc_e)}</td><td class="num">${this._fmt(tot.ht)}</td><td class="num">${this._fmt(tot.ht_e)}</td><td class="num">${this._fmt(tot.p)}</td><td></td></tr>`;
      ls.forEach((l) => {
        h += `<tr data-trim="${t}"><td><a href="/app/sales-order/${l.commande}" target="_blank">${l.commande}</a></td>
          <td>${frappe.datetime.str_to_user(l.date_commande)}</td><td>${frappe.datetime.str_to_user(l.date_livraison)}</td>
          <td><a href="/app/customer/${encodeURIComponent(l.client)}" target="_blank">${frappe.utils.escape_html(l.client_nom || l.client)}</a></td>
          <td class="num">${this._fmt(l.ttc)}</td><td class="num">${this._fmt(l.ttc_e)}</td><td class="num">${this._fmt(l.ht)}</td><td class="num">${this._fmt(l.ht_e)}</td>
          <td class="num"><b>${this._fmt(l.prime)}</b></td><td>${l.type ? `<span class="rp-badge">${l.type}</span>` : ""}</td></tr>`;
      });
    });
    h += "</tbody></table>";
    this.$root.find("#rp-lignes").html(h);
  }

  // Coefficient du trimestre : « 100 % » cliquable (✏️) ; si ≠ 100 %, on montre le brut.
  _coef_line(det, t, d) {
    if (!t.ouvert) return "";
    const coef = flt(t.coefficient, 2);
    const btn = d.peut_modifier
      ? `<a href="#" class="rp-coef" data-trimestre="${t.code}" data-coef="${coef}" data-remarque="${frappe.utils.escape_html(t.coefficient_remarque || "")}" title="Modifier le coefficient">${coef} % ✏️</a>`
      : `<span>${coef} %</span>`;
    let h = `<div class="k-line"><span>Coefficient</span>${btn}</div>`;
    if (coef !== 100) h += `<div class="k-line rp-muted"><span>brut</span><span>${this._fmt(t.brut)}</span></div>`;
    if (t.coefficient_remarque) h += `<div class="k-line rp-muted" style="font-size:11px"><span>${frappe.utils.escape_html(t.coefficient_remarque)}</span></div>`;
    return h;
  }

  // Appréciation du responsable sous le trimestre (rouge dans le PDF), modifiable.
  _appreciation_bloc(t, d) {
    if (!t.ouvert) return "";
    const texte = (t.appreciation || "").trim();
    const lien = d.peut_modifier
      ? `<a href="#" class="rp-appr" data-trimestre="${t.code}" title="Appréciation du trimestre">${texte ? "✏️ modifier" : "✏️ Ajouter une appréciation"}</a>`
      : "";
    return `<div class="rp-appr-bloc" data-trimestre="${t.code}">
      ${texte ? `<div class="rp-appr-txt">${frappe.utils.escape_html(texte)}</div>` : ""}
      ${lien ? `<div class="k-line" style="font-size:11px">${lien}</div>` : ""}
    </div>`;
  }

  _dialog_appreciation(trimestre) {
    const det = this._data.detail;
    const t = det.trimestres.find((x) => x.code === trimestre);
    const dlg = new frappe.ui.Dialog({
      title: `Appréciation — ${det.employee_name}, ${t.libelle} ${this._data.annee}`,
      fields: [
        { fieldname: "appreciation", fieldtype: "Small Text", label: "Votre appréciation", default: t.appreciation || "",
          description: "Imprimée en rouge sous le trimestre dans le PDF. Vide = aucune appréciation." },
        { fieldname: "ia", fieldtype: "Button", label: "✨ Améliorer la formulation avec l'IA",
          click: async () => {
            const brut = dlg.get_value("appreciation");
            if (!brut || brut.trim().length < 3) { frappe.show_alert({ message: "Écrivez d'abord quelques mots.", indicator: "orange" }); return; }
            dlg.get_field("ia").$input.prop("disabled", true).text("⏳ Reformulation…");
            try {
              const r = await frappe.call({
                method: "customization_app.rapport_prime.ameliorer_appreciation",
                args: { texte: brut, employee: det.employee, annee: this._data.annee, trimestre,
                        contexte: { brut: t.brut, coefficient: t.coefficient, commandes: t.commandes, taches: t.taches } },
              });
              if (r.message) {
                dlg.set_value("appreciation", r.message);
                frappe.show_alert({ message: "Texte reformulé — relisez avant d'enregistrer", indicator: "blue" });
              }
            } finally {
              dlg.get_field("ia").$input.prop("disabled", false).text("✨ Améliorer la formulation avec l'IA");
            }
          } },
      ],
      primary_action_label: "Enregistrer",
      primary_action: async (values) => {
        await frappe.call({
          method: "customization_app.rapport_prime.enregistrer_appreciation",
          args: { employee: det.employee, annee: this._data.annee, trimestre, appreciation: values.appreciation || "" },
        });
        dlg.hide();
        frappe.show_alert({ message: "Appréciation enregistrée", indicator: "green" });
        this._fetch();
      },
    });
    dlg.show();
  }

  _dialog_coefficient(trimestre, coef, remarque) {
    const det = this._data.detail;
    const t = det.trimestres.find((x) => x.code === trimestre);
    const dlg = new frappe.ui.Dialog({
      title: `Coefficient — ${det.employee_name}, ${t.libelle} ${this._data.annee}`,
      fields: [
        { fieldname: "coefficient", fieldtype: "Percent", label: "Coefficient (%)", reqd: 1, default: coef,
          description: `Prime brute du trimestre : ${this._fmt(t.brut)}. 100 = tout, 50 = la moitié, 0 = rien.` },
        { fieldname: "remarque", fieldtype: "Small Text", label: "Motif", default: remarque || "" },
      ],
      primary_action_label: "Enregistrer",
      primary_action: async (values) => {
        await frappe.call({
          method: "customization_app.rapport_prime.enregistrer_coefficient",
          args: { employee: det.employee, annee: this._data.annee, trimestre, ...values },
        });
        dlg.hide();
        frappe.show_alert({ message: "Coefficient enregistré", indicator: "green" });
        this._fetch();
      },
    });
    dlg.show();
  }

  _compte_ventes(mode) { return !mode || mode === "Ventes" || mode === "Ventes + Main d'œuvre"; }
  _compte_mo(mode) { return mode === "Main d'œuvre" || mode === "Ventes + Main d'œuvre"; }

  // Sous-lignes « ventes / main-d'œuvre » d'une carte, seulement si l'employé a les deux.
  _ventilation(det, vente, mo, commandes, taches) {
    const deux = this._compte_ventes(det.mode) && this._compte_mo(det.mode);
    const nb = [];
    if (this._compte_ventes(det.mode) && commandes !== undefined) nb.push(`${commandes} commande${commandes > 1 ? "s" : ""}`);
    if (this._compte_mo(det.mode) && taches !== undefined) nb.push(`${taches} tâche${taches > 1 ? "s" : ""}`);
    let h = nb.length ? `<div class="k-line"><span>${nb.join(" · ")}</span></div>` : "";
    if (deux) {
      h += `<div class="k-line rp-muted"><span>dont ventes</span><span>${this._fmt(vente)}</span></div>
            <div class="k-line rp-muted"><span>dont main-d'œuvre</span><span>${this._fmt(mo)}</span></div>`;
    }
    return h;
  }

  _render_taches(det, d) {
    const $card = this.$root.find("#rp-card-taches");
    if (!this._compte_mo(det.mode) && !(det.taches || []).length) { $card.hide(); return; }
    $card.show();
    const taches = det.taches || [];
    this.$root.find("#rp-taches-sub").text(`${taches.length} commande${taches.length > 1 ? "s" : ""} · ${flt(d.taux_mo * 100, 2)} % du HT de main-d'œuvre`);
    if (!taches.length) {
      this.$root.find("#rp-taches").html('<div class="rp-empty">Aucune tâche terminée sur la période.</div>');
      return;
    }
    let h = `<table class="rp-tbl"><thead><tr><th>Commande</th><th>Date commande</th><th>1re tâche</th><th>Client</th><th>Types</th>
      <th class="num">Tâches</th><th class="num">HT main-d'œuvre</th><th class="num">Techniciens</th><th class="num">Part</th><th class="num">Prime</th></tr></thead><tbody>`;
    const groupes = {};
    taches.forEach((t) => { (groupes[t.trimestre || "hors"] = groupes[t.trimestre || "hors"] || []).push(t); });
    ["Q1", "Q2", "Q3", "Q4", "hors"].forEach((q) => {
      const ls = groupes[q];
      if (!ls) return;
      const trim = det.trimestres.find((x) => x.code === q);
      const lib = trim ? trim.libelle : `Hors ${d.annee} (non comptées)`;
      const tot = ls.reduce((a, t) => ({ mo: a.mo + t.ht_mo, part: a.part + t.part, p: a.p + t.prime }), { mo: 0, part: 0, p: 0 });
      h += `<tr class="rp-trim"><td colspan="6"><span class="rp-toggle" data-trimestre="t${q}">▾ ${lib}</span> <span class="rp-muted">· ${ls.length} commande${ls.length > 1 ? "s" : ""}</span></td>
        <td class="num">${this._fmt(tot.mo)}</td><td></td><td class="num">${this._fmt(tot.part)}</td><td class="num">${this._fmt(tot.p)}</td></tr>`;
      ls.forEach((t) => {
        h += `<tr data-trim="t${q}"><td><a href="/app/sales-order/${t.commande}" target="_blank">${t.commande}</a></td>
          <td>${frappe.datetime.str_to_user(t.date_commande)}</td><td>${frappe.datetime.str_to_user(t.date_tache)}</td>
          <td><a href="/app/customer/${encodeURIComponent(t.client)}" target="_blank">${frappe.utils.escape_html(t.client_nom || t.client)}</a></td>
          <td class="rp-muted">${frappe.utils.escape_html(t.types || "")}</td><td class="num">${t.nb_taches}</td>
          <td class="num">${this._fmt(t.ht_mo)}</td><td class="num">${t.nb_techniciens}${t.nb_techniciens > 1 ? ' <span class="rp-muted" title="Main-d\'œuvre partagée">÷</span>' : ""}</td>
          <td class="num">${this._fmt(t.part)}</td><td class="num"><b>${this._fmt(t.prime)}</b></td></tr>`;
      });
    });
    h += "</tbody></table>";
    this.$root.find("#rp-taches").html(h);
  }

  _dialog_versement(pre) {
    const det = this._data.detail;
    const trims = det.trimestres.filter((t) => t.ouvert);
    const defaut = trims.length ? trims[trims.length - 1].code : "Q1";
    const dlg = new frappe.ui.Dialog({
      title: `Versement de prime — ${det.employee_name}`,
      fields: [
        { fieldname: "trimestre", fieldtype: "Select", label: "Trimestre", reqd: 1,
          options: det.trimestres.map((t) => ({ value: t.code, label: `${t.libelle} (reste ${this._fmt(t.reste)})` })),
          default: pre.trimestre || defaut },
        { fieldname: "date_versement", fieldtype: "Date", label: "Date du versement", reqd: 1,
          default: pre.date_versement || frappe.datetime.get_today() },
        { fieldname: "montant", fieldtype: "Currency", label: "Montant versé", reqd: 1, default: pre.montant || 0,
          description: pre.journal_entry
            ? `Écriture de ${this._fmt(pre.total_ecriture)}${flt(pre.deja_rattache) ? `, déjà rattaché ${this._fmt(pre.deja_rattache)}` : ""}. `
              + `Ne saisir que la part qui revient à ${frappe.utils.escape_html(det.employee_name)} ; le reste de l'écriture restera proposé.`
            : "Une prime payée en plusieurs fois = un versement par tranche, sur le même trimestre." },
        { fieldname: "journal_entry", fieldtype: "Link", label: "Écriture de caisse (facultatif)", options: "Journal Entry",
          default: pre.journal_entry || "" },
        { fieldname: "remarque", fieldtype: "Small Text", label: "Remarque", default: pre.remarque || "" },
        { fieldname: "ecriture_soldee", fieldtype: "Check", depends_on: "journal_entry",
          label: "Le reste de l'écriture est une autre prime (ne plus la proposer)",
          description: "À cocher quand l'écriture de caisse contient d'autres primes que celle des ventes : seule la part saisie est déduite." },
      ],
      primary_action_label: "Enregistrer",
      primary_action: async (values) => {
        await frappe.call({
          method: "customization_app.rapport_prime.enregistrer_versement",
          args: { employee: det.employee, annee: this._data.annee, ...values },
        });
        dlg.hide();
        frappe.show_alert({ message: "Versement enregistré", indicator: "green" });
        this._fetch();
      },
    });
    dlg.show();
  }

  _supprimer(name) {
    frappe.confirm(`Supprimer le versement ${name} ?`, async () => {
      await frappe.call({ method: "customization_app.rapport_prime.supprimer_versement", args: { name } });
      frappe.show_alert({ message: "Versement supprimé", indicator: "orange" });
      this._fetch();
    });
  }
}
