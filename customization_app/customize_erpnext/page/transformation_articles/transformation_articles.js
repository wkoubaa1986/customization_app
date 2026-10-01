frappe.pages["transformation-articles"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Transformation d’articles", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("transformation_articles", {}));
  new TransformationArticles(wrapper);
};

// Rendu pur : stocks, taux, répartition de valeur et alertes viennent de
// customization_app.transformation_articles.simuler ; la page ne fait qu'afficher et saisir.

class TransformationArticles {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".ta-page");
    this.conso = [];   // [{item_code, qty}]
    this.obt = [];     // [{item_code, qty, taux_fixe}]
    this.sim = null;
    this.ctx = null;
    this._bind();
    this._init();
  }

  async _init() {
    try {
      const r = await frappe.call({ method: "customization_app.transformation_articles.get_context" });
      this.ctx = r.message;
    } catch (err) {
      if (err && err.exc_type === "PermissionError") { this.$root.find("#ta-main").hide(); this.$root.find("#ta-denied").show(); }
      throw err;
    }
    const $m = this.$root.find("#ta-magasin").empty();
    this.ctx.magasins.forEach((m) => $m.append(`<option value="${frappe.utils.escape_html(m)}">${frappe.utils.escape_html(m)}</option>`));
    $m.val(this.ctx.magasin_defaut);
    this.$root.find("#ta-date").val(this.ctx.aujourdhui);
    this._render_hist(this.ctx.historique);
    this._render_modeles(this.ctx.modeles || []);
    this._simuler();
  }

  _magasin() { return this.$root.find("#ta-magasin").val(); }
  _fmt(v) { return format_number(v || 0, null, 3); }

  _bind() {
    let timer = null;
    this.$root.find("#ta-search").on("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => this._rechercher(e.target.value), 300);
    });
    this.$root.find("#ta-magasin").on("change", () => { this._rechercher(this.$root.find("#ta-search").val()); this._simuler(); });
    this.$root.on("click", ".ta-add", (e) => {
      const $b = $(e.currentTarget);
      this._ajouter($b.data("cible"), $b.data("item"), 1);
    });
    this.$root.on("click", ".ta-del", (e) => {
      const $x = $(e.currentTarget);
      this[$x.data("cible")].splice($x.data("idx"), 1);
      this._simuler();
    });
    this.$root.on("change", ".ta-qty", (e) => {
      const $i = $(e.currentTarget);
      this[$i.data("cible")][$i.data("idx")].qty = flt($i.val());
      this._simuler();
    });
    this.$root.on("change", ".ta-taux", (e) => {
      const $i = $(e.currentTarget);
      const v = flt($i.val());
      this.obt[$i.data("idx")].taux_fixe = v > 0 ? v : null;
      this._simuler();
    });
    this.$root.on("click", ".ta-taux-auto", (e) => {
      this.obt[$(e.currentTarget).data("idx")].taux_fixe = null;
      this._simuler();
    });
    this.$root.find("[data-action='inverser']").on("click", () => {
      const c = this.conso.map((l) => ({ item_code: l.item_code, qty: l.qty }));
      this.conso = this.obt.map((l) => ({ item_code: l.item_code, qty: l.qty }));
      this.obt = c.map((l) => ({ item_code: l.item_code, qty: l.qty, taux_fixe: null }));
      this._simuler();
    });
    this.$root.find("[data-action='vider']").on("click", () => { this.conso = []; this.obt = []; this._simuler(); });
    this.$root.find("[data-action='composants-conso']").on("click", () => this._composants("obt", "conso"));
    this.$root.find("[data-action='composants-obt']").on("click", () => this._composants("conso", "obt"));
    this.$root.find("[data-action='valider']").on("click", () => this._valider());
    this.$root.find("[data-action='modele']").on("click", () => this._dialog_modele());
    this.$root.on("click", ".ta-mod-charger", async (e) => {
      const $b = $(e.currentTarget);
      const fois = flt($b.closest("tr").find(".ta-fois").val()) || 1;
      const r = await frappe.call({ method: "customization_app.transformation_articles.appliquer_modele", args: { name: $b.data("name"), facteur: fois } });
      this.conso = r.message.consommes.map((l) => ({ item_code: l.item_code, qty: l.qty }));
      this.obt = r.message.obtenus.map((l) => ({ item_code: l.item_code, qty: l.qty, taux_fixe: null }));
      this._simuler();
      frappe.show_alert({ message: `Modèle « ${r.message.nom} » chargé × ${fois}. Vérifiez puis validez.`, indicator: "blue" });
      $("html, body").animate({ scrollTop: 0 }, 200);
    });
    this.$root.on("click", ".ta-mod-suppr", (e) => {
      const name = $(e.currentTarget).data("name");
      frappe.confirm(`Supprimer le modèle « ${name} » ?`, async () => {
        await frappe.call({ method: "customization_app.transformation_articles.supprimer_modele", args: { name } });
        this._recharger_modeles();
      });
    });
    this.$root.on("click", ".ta-annuler", (e) => this._annuler($(e.currentTarget).data("name")));
    this.$root.on("click", ".ta-refaire", (e) => this._refaire($(e.currentTarget).data("name"), $(e.currentTarget).data("sens")));
  }

  async _rechercher(txt) {
    const $r = this.$root.find("#ta-results");
    if (!txt || txt.trim().length < 2) { $r.html('<div class="ta-muted" style="padding:14px;text-align:center">Tapez au moins 2 caractères.</div>'); return; }
    const r = await frappe.call({ method: "customization_app.transformation_articles.rechercher", args: { txt, warehouse: this._magasin() } });
    const res = r.message || [];
    if (!res.length) { $r.html('<div class="ta-muted" style="padding:14px;text-align:center">Aucun article suivi en stock ne correspond.</div>'); return; }
    $r.html(res.map((a) => `<div class="ta-res">
        ${a.image ? `<img class="ta-img" src="${a.image}" loading="lazy">` : '<span class="ta-img-empty">📦</span>'}
        <div style="min-width:0">
          <div class="ta-code">${frappe.utils.escape_html(a.item_code)} ${a.est_bundle ? '<span class="ta-badge">bundle</span>' : ""}${a.modele ? '<span class="ta-badge">modèle</span>' : ""}</div>
          <div class="ta-name" title="${frappe.utils.escape_html(a.item_name || "")}">${frappe.utils.escape_html(a.item_name || "")}</div>
          <div class="ta-meta">Stock <span class="${a.stock > 0 ? "ta-stock-ok" : "ta-stock-ko"}">${this._fmt(a.stock)}</span> ${frappe.utils.escape_html(a.uom || "")} · taux ${this._fmt(a.taux)}</div>
        </div>
        <div class="ta-btns">
          <button class="btn btn-default btn-xs ta-add" data-cible="conso" data-item="${frappe.utils.escape_html(a.item_code)}">− Consommer</button>
          <button class="btn btn-default btn-xs ta-add" data-cible="obt" data-item="${frappe.utils.escape_html(a.item_code)}">+ Obtenir</button>
        </div>
      </div>`).join(""));
  }

  _ajouter(cible, item_code, qty) {
    const liste = this[cible];
    const existant = liste.find((l) => l.item_code === item_code);
    if (existant) existant.qty = flt(existant.qty) + flt(qty);
    else liste.push(cible === "obt" ? { item_code, qty, taux_fixe: null } : { item_code, qty });
    this._simuler();
  }

  // Composants du bundle présent d'un côté, ajoutés de l'autre côté (× sa quantité).
  async _composants(depuis, vers) {
    const src = (this.sim ? this.sim[depuis === "obt" ? "obtenus" : "consommes"] : []).find((l) => l.est_bundle);
    if (!src) return;
    const r = await frappe.call({ method: "customization_app.transformation_articles.composants", args: { item_code: src.item_code } });
    (r.message || []).forEach((c) => this._ajouter(vers, c.item_code, flt(c.qty) * flt(src.qty)));
    if (!(r.message || []).length) frappe.show_alert({ message: "Aucun composant suivi en stock dans ce bundle.", indicator: "orange" });
  }

  async _simuler() {
    const r = await frappe.call({
      method: "customization_app.transformation_articles.simuler",
      args: { consommes: this.conso, obtenus: this.obt, warehouse: this._magasin() },
    });
    this.sim = r.message;
    this._render();
  }

  _render() {
    const s = this.sim;
    const img = (l) => (l.image ? `<img class="ta-img" src="${l.image}" style="width:34px;height:34px">` : '<span class="ta-img-empty" style="width:34px;height:34px;font-size:14px">📦</span>');
    const nom = (l) => `<div class="ta-code">${frappe.utils.escape_html(l.item_code)}${l.est_bundle ? ' <span class="ta-badge">bundle</span>' : ""}</div><div class="ta-name">${frappe.utils.escape_html(l.item_name || "")}</div>`;

    // Je consomme
    let h = "";
    if (!s.consommes.length) h = '<div class="ta-empty">Aucun article. Cherchez à gauche puis « − Consommer ».</div>';
    else {
      h = '<table class="ta-tbl"><thead><tr><th></th><th>Article</th><th class="num">Stock</th><th class="num">Quantité</th><th class="num">Taux</th><th class="num">Valeur</th><th></th></tr></thead><tbody>';
      s.consommes.forEach((l, i) => {
        const ko = flt(l.qty) > flt(l.stock);
        h += `<tr><td>${img(l)}</td><td>${nom(l)}</td><td class="num ${ko ? "ta-stock-ko" : "ta-stock-ok"}">${this._fmt(l.stock)}</td>
          <td class="num"><input type="number" step="any" min="0" class="form-control ta-qty" data-cible="conso" data-idx="${i}" value="${l.qty}"></td>
          <td class="num">${this._fmt(l.taux)}</td><td class="num"><b>${this._fmt(l.valeur)}</b></td>
          <td><span class="ta-del" data-cible="conso" data-idx="${i}" title="Retirer">✕</span></td></tr>`;
      });
      h += `<tr class="ta-tot"><td colspan="5">Total consommé</td><td class="num">${this._fmt(s.total_consomme)}</td><td></td></tr></tbody></table>`;
    }
    this.$root.find("#ta-conso").html(h);

    // J'obtiens
    h = "";
    if (!s.obtenus.length) h = '<div class="ta-empty">Aucun article. Cherchez à gauche puis « + Obtenir ».</div>';
    else {
      h = '<table class="ta-tbl"><thead><tr><th></th><th>Article</th><th class="num">Stock</th><th class="num">Quantité</th><th class="num">Taux proposé</th><th class="num">Taux habituel</th><th class="num">Valeur</th><th></th></tr></thead><tbody>';
      s.obtenus.forEach((l, i) => {
        const fixe = this.obt[i] && this.obt[i].taux_fixe;
        const ecart = l.ecart_pct === null || l.ecart_pct === undefined ? "" :
          `<div class="${Math.abs(l.ecart_pct) > 20 ? "ta-neg" : "ta-muted"}" style="font-size:10.5px">${l.ecart_pct > 0 ? "+" : ""}${l.ecart_pct} %</div>`;
        h += `<tr><td>${img(l)}</td><td>${nom(l)}</td><td class="num">${this._fmt(l.stock)}</td>
          <td class="num"><input type="number" step="any" min="0" class="form-control ta-qty" data-cible="obt" data-idx="${i}" value="${l.qty}"></td>
          <td class="num"><input type="number" step="any" min="0" class="form-control ta-taux" data-idx="${i}" value="${l.taux}" title="Modifier pour fixer ce taux ; le reste de la valeur se répartit sur les autres lignes">
            ${fixe ? `<div style="font-size:10.5px"><a href="#" class="ta-taux-auto" data-idx="${i}">fixé · revenir en auto</a></div>` : ""}</td>
          <td class="num">${this._fmt(l.taux_ref)}${ecart}</td><td class="num"><b>${this._fmt(l.valeur)}</b></td>
          <td><span class="ta-del" data-cible="obt" data-idx="${i}" title="Retirer">✕</span></td></tr>`;
      });
      h += `<tr class="ta-tot"><td colspan="6">Total obtenu</td><td class="num">${this._fmt(s.total_obtenu)}</td><td></td></tr></tbody></table>`;
    }
    this.$root.find("#ta-obt").html(h);

    // Boutons composants : visibles seulement s'il y a un bundle de l'autre côté
    this.$root.find("[data-action='composants-conso']").toggle(s.obtenus.some((l) => l.est_bundle));
    this.$root.find("[data-action='composants-obt']").toggle(s.consommes.some((l) => l.est_bundle));

    // Bilan + alertes
    const ecart = flt(s.ecart_valeur);
    this.$root.find("#ta-bilan").html(s.consommes.length || s.obtenus.length ? `
      <span>Consommé <b>${this._fmt(s.total_consomme)}</b></span><span>Obtenu <b>${this._fmt(s.total_obtenu)}</b></span>
      <span>Écart <b class="${Math.abs(ecart) > 0.005 ? "ta-neg" : "ta-pos"}">${this._fmt(ecart)}</b>${Math.abs(ecart) > 0.005 ? ' <span class="ta-muted">(taux fixés : la différence partira en écart de stock)</span>' : ""}</span>
      <span class="ta-muted">${this.ctx ? this.ctx.currency : ""}</span>` : "");
    this.$root.find("#ta-alertes").html(s.alertes.map((a) => `<div class="ta-alerte ko">⚠️ ${frappe.utils.escape_html(a)}</div>`).join(""));
    this.$root.find("[data-action='valider']").prop("disabled", !s.valide);
    this.$root.find("[data-action='modele']").prop("disabled", !(s.consommes.length && s.obtenus.length));
    this.$root.find("#ta-etat").text(s.valide ? "Crée et soumet une écriture Reconditionnement." : "");
  }

  _valider() {
    const s = this.sim;
    const msg = `Consommer ${s.consommes.map((l) => `${l.qty} × ${l.item_code}`).join(", ")}<br>Obtenir ${s.obtenus.map((l) => `${l.qty} × ${l.item_code}`).join(", ")}<br>Magasin ${frappe.utils.escape_html(this._magasin())}. Confirmer ?`;
    frappe.confirm(msg, async () => {
      const r = await frappe.call({
        method: "customization_app.transformation_articles.valider",
        args: { consommes: this.conso, obtenus: this.obt, warehouse: this._magasin(),
                posting_date: this.$root.find("#ta-date").val() || null, remarque: this.$root.find("#ta-remarque").val() || null },
        freeze: true, freeze_message: "Création de l’écriture…",
      });
      const m = r.message;
      frappe.msgprint({ title: "Transformation validée", indicator: "green",
        message: `Écriture <a href="/app/stock-entry/${m.name}" target="_blank">${m.name}</a> soumise : ${this._fmt(m.total_consomme)} consommés → ${this._fmt(m.total_obtenu)} obtenus.` });
      this.conso = []; this.obt = []; this.$root.find("#ta-remarque").val("");
      this._simuler(); this._recharger_hist();
    });
  }

  _annuler(name) {
    frappe.confirm(`Annuler l’écriture ${name} ? Les quantités et valeurs seront remises comme avant.`, async () => {
      await frappe.call({ method: "customization_app.transformation_articles.annuler", args: { name }, freeze: true });
      frappe.show_alert({ message: `${name} annulée`, indicator: "orange" });
      this._recharger_hist(); this._simuler();
    });
  }

  // Recharge une transformation passée dans l'éditeur, dans le même sens ou à l'envers.
  _refaire(name, sens) {
    const t = (this._hist || []).find((x) => x.name === name);
    if (!t) return;
    const c = t.consommes.map((l) => ({ item_code: l.item_code, qty: l.qty }));
    const o = t.obtenus.map((l) => ({ item_code: l.item_code, qty: l.qty, taux_fixe: null }));
    if (sens === "inverse") { this.conso = o.map((l) => ({ item_code: l.item_code, qty: l.qty })); this.obt = c.map((l) => ({ ...l, taux_fixe: null })); }
    else { this.conso = c; this.obt = o; }
    this._simuler();
    $("html, body").animate({ scrollTop: 0 }, 200);
  }

  // Les quantités saisies représentent `facteur` applications : le modèle garde les quantités unitaires.
  _dialog_modele() {
    const s = this.sim;
    const dlg = new frappe.ui.Dialog({
      title: "Enregistrer comme modèle",
      fields: [
        { fieldname: "nom", fieldtype: "Data", label: "Nom du modèle", reqd: 1,
          description: "Un nom existant est remplacé." },
        { fieldname: "facteur", fieldtype: "Float", label: "Les quantités saisies correspondent à combien d’applications ?", default: 1, reqd: 1,
          description: `Ex. : vous avez saisi ${s.consommes.map((l) => `${l.qty} × ${l.item_code}`).join(", ")} ; si c’est pour 1 unité, laissez 1.` },
        { fieldname: "remarque", fieldtype: "Small Text", label: "Remarque" },
      ],
      primary_action_label: "Enregistrer",
      primary_action: async (v) => {
        await frappe.call({ method: "customization_app.transformation_articles.enregistrer_modele",
          args: { nom: v.nom, consommes: this.conso, obtenus: this.obt, remarque: v.remarque || null, facteur: v.facteur || 1 } });
        dlg.hide();
        frappe.show_alert({ message: `Modèle « ${v.nom} » enregistré`, indicator: "green" });
        this._recharger_modeles();
      },
    });
    dlg.show();
  }

  async _recharger_modeles() {
    const r = await frappe.call({ method: "customization_app.transformation_articles.modeles" });
    this._render_modeles(r.message || []);
  }

  _render_modeles(mods) {
    if (!mods.length) {
      this.$root.find("#ta-modeles").html('<div class="ta-muted" style="padding:10px">Aucun modèle. Composez une transformation puis « 💾 Enregistrer comme modèle ».</div>');
      return;
    }
    const lignes = (ls, cls) => ls.map((l) => `<span class="${cls}">${l.qty} × ${frappe.utils.escape_html(l.item_code)}</span>`).join(", ");
    let h = '<table class="ta-tbl"><thead><tr><th>Modèle</th><th>Consommé → Obtenu (pour 1)</th><th class="num">Applications</th><th></th></tr></thead><tbody>';
    mods.forEach((m) => {
      h += `<tr><td><b>${frappe.utils.escape_html(m.nom)}</b>${m.remarque ? `<div class="ta-muted" style="font-size:11px">${frappe.utils.escape_html(m.remarque)}</div>` : ""}</td>
        <td class="ta-hist-lines">${lignes(m.consommes, "c")} <b>→</b> ${lignes(m.obtenus, "o")}</td>
        <td class="num">× <input type="number" min="1" step="1" class="form-control ta-fois" value="1"></td>
        <td style="white-space:nowrap"><button class="btn btn-primary btn-xs ta-mod-charger" data-name="${frappe.utils.escape_html(m.name)}">⬆️ Charger</button>
          <button class="btn btn-default btn-xs ta-mod-suppr" data-name="${frappe.utils.escape_html(m.name)}" title="Supprimer le modèle">🗑️</button></td></tr>`;
    });
    h += "</tbody></table>";
    this.$root.find("#ta-modeles").html(h);
  }

  async _recharger_hist() {
    const r = await frappe.call({ method: "customization_app.transformation_articles.historique", args: { limite: 20 } });
    this._render_hist(r.message || []);
  }

  _render_hist(hist) {
    this._hist = hist;
    if (!hist.length) { this.$root.find("#ta-hist").html('<div class="ta-muted" style="padding:10px">Aucune transformation encore.</div>'); return; }
    const lignes = (ls, cls) => ls.map((l) => `<span class="${cls}">${l.qty} × ${frappe.utils.escape_html(l.item_code)}</span>`).join(", ");
    let h = '<table class="ta-tbl"><thead><tr><th>Date</th><th>Écriture</th><th>Consommé → Obtenu</th><th class="num">Valeur</th><th>Magasin</th><th></th></tr></thead><tbody>';
    hist.forEach((t) => {
      const annulee = t.docstatus === 2;
      h += `<tr${annulee ? ' style="opacity:.55"' : ""}><td>${frappe.datetime.str_to_user(t.date)}</td>
        <td><a href="/app/stock-entry/${t.name}" target="_blank">${t.name}</a>${annulee ? ' <span class="ta-badge" style="background:#eee;color:#666">annulée</span>' : ""}</td>
        <td class="ta-hist-lines">${lignes(t.consommes, "c")} <b>→</b> ${lignes(t.obtenus, "o")}${t.remarque && t.remarque.includes("—") ? `<div class="ta-muted">${frappe.utils.escape_html(t.remarque.split("—").slice(1).join("—").trim())}</div>` : ""}</td>
        <td class="num">${this._fmt(t.valeur)}</td><td class="ta-muted">${frappe.utils.escape_html(t.warehouse || "")}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-default btn-xs ta-refaire" data-name="${t.name}" data-sens="meme" title="Recharger dans l’éditeur">↻ Refaire</button>
          <button class="btn btn-default btn-xs ta-refaire" data-name="${t.name}" data-sens="inverse" title="Recharger à l’envers">🔁 Inverser</button>
          ${annulee ? "" : `<button class="btn btn-default btn-xs ta-annuler" data-name="${t.name}">🗑️ Annuler</button>`}
        </td></tr>`;
    });
    h += "</tbody></table>";
    this.$root.find("#ta-hist").html(h);
  }
}
