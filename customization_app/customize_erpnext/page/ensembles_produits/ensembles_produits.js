/**
 * « Ensembles de produits » — tous les Product Bundle d'un coup d'œil, et leur gestion au doigt.
 *
 * Une carte par ensemble : photo, prix de vente, ventes de l'année, exemplaires assemblables avec le stock du
 * Magasin, composants en pastilles (stock de chacun, rouge s'il manque). Recherche par nom, code ou composant,
 * filtres groupe / actifs / non assemblables. Toucher une carte = fiche : composants et quantités, prix,
 * activer / désactiver, dupliquer, supprimer. « Nouvel ensemble » crée l'article parent (non stocké) si besoin.
 * Toute la règle est côté serveur (customization_app.ensembles_produits).
 */

frappe.pages["ensembles-produits"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Ensembles de produits", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("ensembles_produits", {}));
  wrapper.ep = new EnsemblesProduits(wrapper);
};
frappe.pages["ensembles-produits"].on_page_show = function (wrapper) {
  if (wrapper.ep && wrapper.ep.pret) wrapper.ep.charger();
};

const EP_API = "customization_app.ensembles_produits.";
const ep_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const ep_q = (n) => (Math.round((+n || 0) * 1000) / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 3 });
const ep_dt = (v) => `${(Math.round((+v || 0) * 1000) / 1000).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} DT`;
const ep_img = (url, cls = "ep-img") => (url ? `<img class="${cls}" src="${ep_esc(url)}" loading="lazy">` : `<div class="${cls}">📦</div>`);
const ep_mini = (url) => (url ? `<img src="${ep_esc(url)}" loading="lazy">` : `<span class="ph">📦</span>`);
const ep_lien = (doctype, name, texte) => `<a href="${frappe.utils.get_form_link(doctype, name)}">${ep_esc(texte || name)}</a>`;

class EnsemblesProduits {
  constructor(wrapper) {
    this.$r = $(wrapper).find(".ep-page");
    this.etat = "actifs";
    this.groupe = "";
    this.nonAss = false;
    this.ensembles = [];
    this.init();
  }

  async init() {
    this.ctx = (await frappe.call(EP_API + "get_context")).message;
    const r = this.$r, ctx = this.ctx;
    r.find("#ep-nouveau").toggle(!!ctx.peut_modifier).on("click", () => this.nouvel());
    r.find("#ep-groupes").html(`<span class="ep-chip on" data-groupe="">Tous les groupes</span>`
      + ctx.groupes.map((g) => `<span class="ep-chip" data-groupe="${ep_esc(g)}">${ep_esc(g)}</span>`).join(""));
    r.find("#ep-groupes .ep-chip").on("click", (e) => {
      this.groupe = $(e.currentTarget).attr("data-groupe");
      r.find("#ep-groupes .ep-chip").each((_, el) => $(el).toggleClass("on", $(el).attr("data-groupe") === this.groupe));
      this.charger();
    });
    r.find("#ep-etats [data-etat]").on("click", (e) => {
      this.etat = $(e.currentTarget).attr("data-etat");
      r.find("#ep-etats [data-etat]").each((_, el) => $(el).toggleClass("on", $(el).attr("data-etat") === this.etat));
      this.charger();
    });
    r.find("#ep-non-ass").on("click", (e) => { this.nonAss = !this.nonAss; $(e.currentTarget).toggleClass("on", this.nonAss); this.peindre(); });
    let t = null;
    r.find("#ep-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.charger(), 300); });
    r.find("#ep-plus").on("click", () => this.charger(true));
    this.pret = true;
    this.charger();
  }

  async charger(suite = false) {
    const r = this.$r, jeton = (this.jeton = (this.jeton || 0) + 1);
    const res = (await frappe.call({ method: EP_API + "get_ensembles", args: {
      recherche: r.find("#ep-recherche").val() || null, groupe: this.groupe || null, etat: this.etat,
      start: suite ? this.suivant || 0 : 0, limite: this.nonAss ? 200 : undefined } })).message;
    if (jeton !== this.jeton) return;
    this.ensembles = suite ? this.ensembles.concat(res.ensembles) : res.ensembles;
    this.suivant = res.suivant;
    this.total = res.total;
    this.peindre();
  }

  badgeAss(e) {
    if (e.assemblables == null) return `<span class="ep-ass na">stock non suivi</span>`;
    const cls = e.assemblables === 0 ? "zero" : e.assemblables < 3 ? "bas" : "ok";
    return `<span class="ep-ass ${cls}">${e.assemblables} assemblable${e.assemblables > 1 ? "s" : ""}</span>`;
  }

  carte(e) {
    const comps = e.composants.map((c) => {
      const manque = c.is_stock_item && c.stock < c.qty;
      return `<span class="ep-comp ${manque ? "manque" : ""}" title="${ep_esc(c.item_code)}">${ep_mini(c.image)}<span class="q">${ep_q(c.qty)}×</span>
        <span class="n">${ep_esc(c.item_name || c.item_code)}</span>${c.is_stock_item ? `<span class="s">(${ep_q(c.stock)})</span>` : ""}</span>`;
    }).join("");
    return `<div class="ep-ens ${e.disabled ? "off" : ""}" data-name="${ep_esc(e.name)}">
      <div class="ep-tete">${ep_img(e.image)}<div class="txt">
        <div class="ep-nom">${ep_esc(e.item_name || e.item_code)}${e.disabled ? ` <span class="ep-ass na">désactivé</span>` : ""}</div>
        <div class="ep-code">${ep_esc(e.item_code)} · ${ep_esc(e.item_group || "")}</div>
        <div class="ep-kpi">${e.prix != null ? `<span>💰 <b>${ep_dt(e.prix)}</b></span>` : `<span class="neg">sans prix</span>`}
          <span>🧾 <b>${e.ventes}</b> vente${e.ventes > 1 ? "s" : ""} ${this.ctx.annee}</span>${this.badgeAss(e)}</div>
      </div></div>
      <div class="ep-comps">${comps || `<span class="ep-note">aucun composant</span>`}</div></div>`;
  }

  peindre() {
    const r = this.$r;
    const liste = this.nonAss ? this.ensembles.filter((e) => e.assemblables === 0) : this.ensembles;
    r.find("#ep-info").html(`${this.total || 0} ensemble(s)` + (this.nonAss ? ` · <b class="neg">${liste.length} non assemblable(s)</b> parmi les ${this.ensembles.length} chargés` : "")
      + (this.ensembles.length < (this.total || 0) && !this.nonAss ? ` — ${this.ensembles.length} affichés` : "")
      + ` · stock du <b>${ep_esc(this.ctx.magasin.replace(/ - [^-]+$/, ""))}</b>`);
    r.find("#ep-grille").html(liste.length ? liste.map((e) => this.carte(e)).join("") : `<div class="ep-vide">Aucun ensemble pour ces filtres.</div>`);
    r.find("#ep-grille .ep-ens").on("click", (e) => this.ouvrir($(e.currentTarget).attr("data-name")));
    r.find("#ep-plus").toggle(!!this.suivant);
  }

  /** Fiche d'un ensemble : composants modifiables, prix, activation, duplication, suppression. */
  ouvrir(name) {
    const e = this.ensembles.find((x) => x.name === name);
    if (!e) return;
    const modif = this.ctx.peut_modifier;
    const lignes = e.composants.map((c) => Object.assign({}, c));
    let trouves = [], saisie = "";
    const d = new frappe.ui.Dialog({ title: `🧩 ${e.item_name || e.item_code}`, size: "large", fields: [{ fieldtype: "HTML", fieldname: "corps" }] });
    const $w = d.fields_dict.corps.$wrapper;
    const ligne = (c) => `<div class="ep-f-ligne" data-item="${ep_esc(c.item_code)}">${ep_img(c.image)}
        <div class="txt"><div class="ep-nom" style="font-size:13.5px">${ep_esc(c.item_name || c.item_code)}</div>
          <div class="ep-code">${ep_esc(c.item_code)}${c.is_stock_item ? ` · stock <b class="${c.stock < c.qty ? "neg" : ""}">${ep_q(c.stock)}</b>` : " · non stocké"}</div></div>
        ${modif ? `<span class="b" data-pas="-1">−</span><input type="number" inputmode="decimal" min="0" step="any" class="form-control q" value="${ep_esc(c.qty)}"><span class="b" data-pas="1">＋</span><span class="b x" data-enlever>✕</span>`
                : `<span class="ep-code" style="font-size:14px;font-weight:700">${ep_q(c.qty)}×</span>`}</div>`;
    const peindre = () => {
      const dans = new Set(lignes.map((l) => l.item_code));
      $w.html(`<div class="ep-f-tete">${ep_img(e.image)}<div class="txt">
          <div class="ep-code">${ep_esc(e.item_code)} · ${ep_esc(e.item_group || "")} · ${ep_lien("Item", e.item_code, "fiche article")}</div>
          <div class="ep-kpi">🧾 <b>${e.ventes}</b> vente(s) ${this.ctx.annee} ${this.badgeAss(Object.assign({}, e, { composants: lignes }))}</div>
          <div class="ep-prix" style="margin-top:6px">💰 ${modif ? `<input type="number" inputmode="decimal" step="any" class="form-control" id="ep-f-prix" value="${e.prix == null ? "" : ep_esc(e.prix)}" placeholder="prix"> <span class="ep-note">DT (${ep_esc("Vente standard")})</span>` : `<b>${e.prix == null ? "sans prix" : ep_dt(e.prix)}</b>`}</div>
        </div></div>
        ${modif ? `<input type="search" class="form-control ep-saisie" id="ep-f-recherche" placeholder="➕ Ajouter un composant (nom ou code)…">
          <div id="ep-f-trouves">${trouves.map((a) => `<div class="ep-f-ligne">${ep_img(a.image)}<div class="txt"><div class="ep-nom" style="font-size:13.5px">${ep_esc(a.item_name || a.item_code)}</div>
              <div class="ep-code">${ep_esc(a.item_code)} · stock ${ep_q(a.stock)}</div></div>
              <span class="b ${dans.has(a.item_code) ? "fait" : "ajout"}" data-ajouter="${ep_esc(a.item_code)}">${dans.has(a.item_code) ? "✓" : "＋"}</span></div>`).join("")}</div>` : ""}
        <span class="ep-lbl">${lignes.length} composant(s)</span>
        <div id="ep-f-lignes">${lignes.map(ligne).join("") || `<div class="ep-note">Aucun composant.</div>`}</div>
        ${modif ? `<span class="ep-lbl">Description (facultatif)</span><input type="text" class="form-control" id="ep-f-desc" value="${ep_esc(e.description || "")}" maxlength="140">
          <div class="ep-f-actions">
            <button type="button" class="btn btn-primary" data-enregistrer>💾 Enregistrer</button>
            <button type="button" class="btn btn-default" data-activer>${e.disabled ? "✅ Réactiver" : "⏸️ Désactiver"}</button>
            <button type="button" class="btn btn-default" data-dupliquer>📋 Dupliquer vers…</button>
            <button type="button" class="btn btn-default" data-supprimer style="color:#dc2626">🗑️ Supprimer</button>
          </div>` : ""}`);
      $w.find("#ep-f-recherche").val(saisie);
      let t = null;
      $w.find("#ep-f-recherche").on("input", (ev) => { saisie = ev.currentTarget.value; clearTimeout(t); t = setTimeout(async () => {
        trouves = saisie.trim().length < 2 ? [] : (await frappe.call({ method: EP_API + "rechercher_composants", args: { txt: saisie.trim() } })).message;
        peindre(); $w.find("#ep-f-recherche").focus();
      }, 250); });
      $w.find("[data-ajouter]").on("click", (ev) => {
        const a = trouves.find((x) => x.item_code === $(ev.currentTarget).attr("data-ajouter"));
        if (!a || dans.has(a.item_code)) return;
        lignes.push({ item_code: a.item_code, item_name: a.item_name, image: a.image, uom: a.uom, qty: 1, stock: a.stock, is_stock_item: a.is_stock_item });
        peindre();
      });
      $w.find("[data-pas]").on("click", (ev) => {
        const l = lignes.find((x) => x.item_code === $(ev.currentTarget).closest(".ep-f-ligne").attr("data-item"));
        l.qty = Math.max(0.001, Math.round((l.qty + +$(ev.currentTarget).attr("data-pas")) * 1000) / 1000); peindre();
      });
      $w.find("input.q").on("change", (ev) => {
        const l = lignes.find((x) => x.item_code === $(ev.currentTarget).closest(".ep-f-ligne").attr("data-item"));
        l.qty = Math.max(0, +String(ev.currentTarget.value).replace(",", ".") || 0); peindre();
      });
      $w.find("[data-enlever]").on("click", (ev) => { const c = $(ev.currentTarget).closest(".ep-f-ligne").attr("data-item"); lignes.splice(lignes.findIndex((x) => x.item_code === c), 1); peindre(); });
      $w.find("[data-enregistrer]").on("click", async () => {
        const prix = $w.find("#ep-f-prix").val();
        await frappe.call({ method: EP_API + "enregistrer_ensemble", args: { name: e.name, lignes: lignes.filter((l) => l.qty > 0).map((l) => ({ item_code: l.item_code, qty: l.qty })), description: $w.find("#ep-f-desc").val() }, freeze: true });
        if (prix !== "" && +prix !== e.prix) await frappe.call({ method: EP_API + "definir_prix", args: { item_code: e.item_code, prix: +prix } });
        d.hide(); frappe.show_alert({ message: "Ensemble enregistré", indicator: "green" }, 3); this.charger();
      });
      $w.find("[data-activer]").on("click", async () => {
        await frappe.call({ method: EP_API + "activer_ensemble", args: { name: e.name, actif: e.disabled ? 1 : 0 }, freeze: true });
        d.hide(); frappe.show_alert({ message: e.disabled ? "Ensemble réactivé" : "Ensemble désactivé", indicator: "orange" }, 3); this.charger();
      });
      $w.find("[data-dupliquer]").on("click", () => { d.hide(); this.nouvel(lignes, e); });
      $w.find("[data-supprimer]").on("click", () => frappe.confirm(`Supprimer la définition de l’ensemble <b>${ep_esc(e.item_name || e.item_code)}</b> ? L’article reste ; refusé s’il a déjà été vendu (désactivez alors).`, async () => {
        await frappe.call({ method: EP_API + "supprimer_ensemble", args: { name: e.name }, freeze: true });
        d.hide(); frappe.show_alert({ message: "Ensemble supprimé", indicator: "orange" }, 3); this.charger();
      }));
    };
    peindre();
    d.show();
  }

  /** Nouvel ensemble : parent existant (non stocké, sans ensemble) ou article créé ici, puis composants. */
  nouvel(lignesInit = [], source = null) {
    const lignes = lignesInit.map((c) => Object.assign({}, c));
    let parent = null, trouvesP = [], trouves = [], saisie = "", saisieP = "", creer = !source && false;
    const d = new frappe.ui.Dialog({ title: source ? `📋 Dupliquer « ${source.item_name || source.item_code} »` : "➕ Nouvel ensemble", size: "large", fields: [{ fieldtype: "HTML", fieldname: "corps" }] });
    const $w = d.fields_dict.corps.$wrapper;
    const peindre = () => {
      const dans = new Set(lignes.map((l) => l.item_code));
      $w.html(`<span class="ep-lbl">1. Article parent (non stocké)</span>
        <div class="ep-chips" style="padding-top:0"><span class="ep-chip ${creer ? "" : "on"}" data-mode="existant">Article existant</span><span class="ep-chip ${creer ? "on" : ""}" data-mode="nouveau">Créer l’article</span></div>
        ${creer ? `<div class="ep-n-form">
            <input type="text" class="form-control" id="ep-n-code" placeholder="Code article (ex. AP-M-AJ-5-SM)" value="${ep_esc(this._n && this._n.code || "")}">
            <input type="text" class="form-control" id="ep-n-nom" placeholder="Désignation" value="${ep_esc(this._n && this._n.nom || (source ? source.item_name + " (copie)" : ""))}">
            <select class="form-control" id="ep-n-groupe">${this.ctx.groupes_articles.map((g) => `<option ${((this._n && this._n.groupe) || (source && source.item_group)) === g ? "selected" : ""}>${ep_esc(g)}</option>`).join("")}</select>
            <input type="number" inputmode="decimal" step="any" class="form-control" id="ep-n-prix" placeholder="Prix de vente standard (DT)" value="${ep_esc(this._n && this._n.prix || (source && source.prix) || "")}"></div>`
          : `${parent ? `<div class="ep-f-ligne">${ep_img(parent.image)}<div class="txt"><div class="ep-nom">${ep_esc(parent.item_name)}</div><div class="ep-code">${ep_esc(parent.item_code)} · ${ep_esc(parent.item_group || "")}</div></div><span class="b x" data-parent-x>✕</span></div>`
            : `<input type="search" class="form-control ep-saisie" id="ep-n-recherche" placeholder="🔎 Article non stocké sans ensemble…"><div>${trouvesP.map((a) => `<div class="ep-f-ligne" data-parent="${ep_esc(a.item_code)}" style="cursor:pointer">${ep_img(a.image)}<div class="txt"><div class="ep-nom" style="font-size:13.5px">${ep_esc(a.item_name)}</div><div class="ep-code">${ep_esc(a.item_code)} · ${ep_esc(a.item_group || "")}</div></div><span class="b ajout">＋</span></div>`).join("")}</div>`}`}
        <span class="ep-lbl">2. Composants (${lignes.length})</span>
        <input type="search" class="form-control ep-saisie" id="ep-n-comp" placeholder="➕ Ajouter un composant (nom ou code)…">
        <div>${trouves.map((a) => `<div class="ep-f-ligne">${ep_img(a.image)}<div class="txt"><div class="ep-nom" style="font-size:13.5px">${ep_esc(a.item_name || a.item_code)}</div><div class="ep-code">${ep_esc(a.item_code)} · stock ${ep_q(a.stock)}</div></div>
            <span class="b ${dans.has(a.item_code) ? "fait" : "ajout"}" data-ajouter="${ep_esc(a.item_code)}">${dans.has(a.item_code) ? "✓" : "＋"}</span></div>`).join("")}</div>
        <div>${lignes.map((c) => `<div class="ep-f-ligne" data-item="${ep_esc(c.item_code)}">${ep_img(c.image)}<div class="txt"><div class="ep-nom" style="font-size:13.5px">${ep_esc(c.item_name || c.item_code)}</div><div class="ep-code">${ep_esc(c.item_code)}</div></div>
            <span class="b" data-pas="-1">−</span><input type="number" inputmode="decimal" min="0" step="any" class="form-control q" value="${ep_esc(c.qty)}"><span class="b" data-pas="1">＋</span><span class="b x" data-enlever>✕</span></div>`).join("")}</div>
        <div class="ep-f-actions"><button type="button" class="btn btn-primary" data-creer ${lignes.length && (creer || parent) ? "" : "disabled"}>✅ Créer l’ensemble</button></div>`);
      $w.find("#ep-n-recherche").val(saisieP); $w.find("#ep-n-comp").val(saisie);
      const lireForm = () => { this._n = { code: $w.find("#ep-n-code").val(), nom: $w.find("#ep-n-nom").val(), groupe: $w.find("#ep-n-groupe").val(), prix: $w.find("#ep-n-prix").val() }; };
      $w.find("[data-mode]").on("click", (ev) => { lireForm(); creer = $(ev.currentTarget).attr("data-mode") === "nouveau"; peindre(); });
      let t1 = null, t2 = null;
      $w.find("#ep-n-recherche").on("input", (ev) => { saisieP = ev.currentTarget.value; clearTimeout(t1); t1 = setTimeout(async () => {
        trouvesP = saisieP.trim().length < 2 ? [] : (await frappe.call({ method: EP_API + "rechercher_parents", args: { txt: saisieP.trim() } })).message; lireForm(); peindre(); $w.find("#ep-n-recherche").focus(); }, 250); });
      $w.find("[data-parent]").on("click", (ev) => { parent = trouvesP.find((x) => x.item_code === $(ev.currentTarget).attr("data-parent")); peindre(); });
      $w.find("[data-parent-x]").on("click", () => { parent = null; peindre(); });
      $w.find("#ep-n-comp").on("input", (ev) => { saisie = ev.currentTarget.value; clearTimeout(t2); t2 = setTimeout(async () => {
        trouves = saisie.trim().length < 2 ? [] : (await frappe.call({ method: EP_API + "rechercher_composants", args: { txt: saisie.trim() } })).message; lireForm(); peindre(); $w.find("#ep-n-comp").focus(); }, 250); });
      $w.find("[data-ajouter]").on("click", (ev) => { const a = trouves.find((x) => x.item_code === $(ev.currentTarget).attr("data-ajouter")); if (!a || dans.has(a.item_code)) return;
        lignes.push({ item_code: a.item_code, item_name: a.item_name, image: a.image, qty: 1, stock: a.stock, is_stock_item: a.is_stock_item }); lireForm(); peindre(); });
      $w.find("[data-pas]").on("click", (ev) => { const l = lignes.find((x) => x.item_code === $(ev.currentTarget).closest(".ep-f-ligne").attr("data-item")); l.qty = Math.max(0.001, Math.round((l.qty + +$(ev.currentTarget).attr("data-pas")) * 1000) / 1000); lireForm(); peindre(); });
      $w.find("input.q").on("change", (ev) => { const l = lignes.find((x) => x.item_code === $(ev.currentTarget).closest(".ep-f-ligne").attr("data-item")); l.qty = Math.max(0, +String(ev.currentTarget.value).replace(",", ".") || 0); lireForm(); peindre(); });
      $w.find("[data-enlever]").on("click", (ev) => { const c = $(ev.currentTarget).closest(".ep-f-ligne").attr("data-item"); lignes.splice(lignes.findIndex((x) => x.item_code === c), 1); lireForm(); peindre(); });
      $w.find("[data-creer]").on("click", async () => {
        lireForm();
        const args = { lignes: lignes.filter((l) => l.qty > 0).map((l) => ({ item_code: l.item_code, qty: l.qty })) };
        if (creer) args.nouveau = { item_code: this._n.code, item_name: this._n.nom, item_group: this._n.groupe, prix: this._n.prix, modele: source ? source.item_code : null };
        else args.parent = parent.item_code;
        const name = (await frappe.call({ method: EP_API + "creer_ensemble", args, freeze: true })).message;
        this._n = null; d.hide();
        frappe.show_alert({ message: `Ensemble ${ep_esc(name)} créé`, indicator: "green" }, 4);
        this.$r.find("#ep-recherche").val(name); this.charger();
      });
    };
    this._n = null;
    peindre();
    d.show();
  }
}
