/**
 * « Stock par entrepôt » — le stock vu du téléphone.
 *
 *  - Solde : le stock d'un entrepôt (ou de tous). L'employé qui a un entrepôt (son véhicule) le voit par défaut.
 *    Toucher un article ouvre ses sorties.
 *  - Sorties : sur une période, les sorties par article ; chaque sortie dit sa pièce, sa commande, son client
 *    et les tâches de la commande.
 *  - Transfert (responsable magasin) : d'un entrepôt à un autre, articles suivis en stock seulement.
 *  - À zéro (responsable magasin) : ramène un entrepôt à zéro par transferts avec le Magasin, pour que
 *    l'écart ne reste que sur le Magasin.
 * Toute la règle est côté serveur (customization_app.stock_entrepots).
 */

frappe.pages["stock-entrepots"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Stock", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("stock_entrepots", {}));
  wrapper.se = new StockEntrepots(wrapper);
};
frappe.pages["stock-entrepots"].on_page_show = function (wrapper) {
  if (wrapper.se && wrapper.se.pret) wrapper.se.rafraichir();
};

const SE_API = "customization_app.stock_entrepots.";
const SE_PAGE = 80;
const se_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const se_q = (n) => (Math.round((+n || 0) * 1000) / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 3 });
const se_signe = (n) => (n > 0 ? "+" : "") + se_q(n);
const se_dt = (d) => (d ? `${d.slice(8, 10)}/${d.slice(5, 7)}` : "");
const se_court = (wh) => String(wh || "").replace(/ - [^-]+$/, "");
const se_img = (url) => (url ? `<img class="se-img" src="${se_esc(url)}" loading="lazy">` : `<div class="se-img">📦</div>`);
const se_lien = (doctype, name, texte) => `<a class="se-lien" href="${frappe.utils.get_form_link(doctype, name)}">${se_esc(texte || name)}</a>`;
const se_argent = (v) => `${Math.round(+v || 0).toLocaleString("fr-FR")} DT`;
const SE_STATUTS = { Completed: "✅", Open: "🕒", Cancelled: "✖️" };

class StockEntrepots {
  constructor(wrapper) {
    this.$r = $(wrapper).find(".se-page");
    this.onglet = "solde";
    this.negatifs = false;
    this.periode = "30j";
    this.panier = [];
    this.jetons = {};
    this.init();
  }

  async init() {
    this.ctx = (await frappe.call(SE_API + "get_context")).message;
    const r = this.$r, ctx = this.ctx;
    this.entrepot = ctx.defaut || "";
    if (!ctx.responsable) r.find('[data-onglet="transfert"], [data-onglet="zero"]').remove();
    r.find(".se-onglet").on("click", (e) => this.montrer($(e.currentTarget).attr("data-onglet")));
    this.peindreEntrepots();

    let t1 = null, t2 = null;
    r.find("#se-s-recherche").on("input", () => { clearTimeout(t1); t1 = setTimeout(() => this.chargerSolde(), 300); });
    r.find("#se-s-negatifs").on("click", (e) => { this.negatifs = !this.negatifs; $(e.currentTarget).toggleClass("on", this.negatifs); this.chargerSolde(); });
    r.find("#se-o-recherche").on("input", () => { clearTimeout(t2); t2 = setTimeout(() => this.chargerSorties(), 300); });
    r.find("#se-periodes .se-chip").on("click", (e) => {
      this.periode = $(e.currentTarget).attr("data-periode");
      r.find("#se-periodes .se-chip").each((_, el) => $(el).toggleClass("on", $(el).attr("data-periode") === this.periode));
      r.find("#se-dates").css("display", this.periode === "dates" ? "flex" : "none");
      if (this.periode === "dates" && !r.find("#se-debut").val()) {
        r.find("#se-debut").val(frappe.datetime.month_start());
        r.find("#se-fin").val(frappe.datetime.get_today());
      }
      this.chargerSorties();
    });
    r.find("#se-debut, #se-fin").on("change", () => this.chargerSorties());
    if (ctx.responsable) this.initTransfert();
    this.pret = true;
    this.montrer("solde");
  }

  montrer(onglet) {
    this.onglet = onglet;
    const r = this.$r;
    r.find(".se-onglet").each((_, el) => $(el).toggleClass("on", $(el).attr("data-onglet") === onglet));
    r.find(".se-vue").each((_, el) => $(el).toggle($(el).attr("data-vue") === onglet));
    r.find("#se-entrepots").toggle(onglet === "solde" || onglet === "sorties");
    this.rafraichir();
  }

  rafraichir() {
    ({ solde: () => this.chargerSolde(), sorties: () => this.chargerSorties(),
       transfert: () => { this.chargerHistorique(); this.majQuantitesPanier(); },
       zero: () => (this.zeroEntrepot ? this.ouvrirZero(this.zeroEntrepot) : this.chargerZero()) })[this.onglet]();
  }

  /** Appel serveur dont seule la DERNIÈRE réponse compte (frappe rapide au clavier, changement d'onglet). */
  async appel(cle, method, args) {
    const jeton = (this.jetons[cle] = (this.jetons[cle] || 0) + 1);
    const r = await frappe.call({ method: SE_API + method, args });
    return jeton === this.jetons[cle] ? r.message : undefined;
  }

  // ── Choix de l'entrepôt (Solde et Sorties) ───────────────────────────────
  peindreEntrepots() {
    const ctx = this.ctx;
    const rang = (w) => (w.name === ctx.mien ? 0 : w.magasin ? 1 : 2);
    const liste = ctx.entrepots.slice().sort((a, b) => rang(a) - rang(b));
    this.$r.find("#se-entrepots").html(liste.map((w) => `<span class="se-chip ${w.name === this.entrepot ? "on" : ""}" data-wh="${se_esc(w.name)}">`
        + `${w.name === ctx.mien ? "👤 " : w.magasin ? "🏬 " : ""}${se_esc(w.libelle)}</span>`).join("")
      + `<span class="se-chip ${this.entrepot ? "" : "on"}" data-wh="">Tous</span>`);
    this.$r.find("#se-entrepots .se-chip").on("click", (e) => {
      this.entrepot = $(e.currentTarget).attr("data-wh");
      this.peindreEntrepots();
      this.rafraichir();
    });
  }

  libelle(wh) {
    const w = this.ctx.entrepots.find((x) => x.name === wh);
    return w ? w.libelle : se_court(wh);
  }

  // ── Solde ──────────────────────────────────────────────────────────────
  async chargerSolde() {
    const r = this.$r;
    const res = await this.appel("solde", "get_solde", { entrepot: this.entrepot || null,
      recherche: r.find("#se-s-recherche").val() || null, negatifs: this.negatifs ? 1 : 0 });
    if (!res) return;
    this.solde = res;
    this.nSolde = SE_PAGE;
    this.peindreSolde();
  }

  peindreSolde() {
    const r = this.$r, res = this.solde, tous = !this.entrepot;
    r.find("#se-s-info").html(`${res.articles.length} article(s)`
      + (res.negatifs ? ` · <span class="neg">${res.negatifs} négatif(s)</span>` : "")
      + (res.valeur != null ? ` · valeur ${se_esc(se_argent(res.valeur))}` : "")
      + ` · ${tous ? "tous les entrepôts" : se_esc(this.libelle(this.entrepot))}`);
    const cartes = res.articles.slice(0, this.nSolde).map((a) => `<div class="se-art" data-item="${se_esc(a.item_code)}">
        ${se_img(a.image)}
        <div class="txt"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
          <div class="se-code">${se_esc(a.item_code)}${a.zones ? ` · 📍 ${se_esc(a.zones)}` : ""}</div>
          ${tous ? `<div class="se-wh">${a.entrepots.map((e) => `<span class="${e.qte < 0 ? "neg" : ""}">${se_esc(e.libelle)} <b>${se_q(e.qte)}</b></span>`).join("")}</div>` : ""}
        </div>
        <div class="se-qte ${a.qte < 0 ? "neg" : ""}">${se_q(a.qte)}<small>${se_esc(a.uom || "")}</small></div>
      </div>`).join("");
    r.find("#se-s-liste").html((cartes || `<div class="se-vide">Aucun stock pour ces filtres.</div>`)
      + (res.articles.length > this.nSolde ? `<button type="button" class="btn btn-default se-plus">Afficher plus (${res.articles.length - this.nSolde})</button>` : ""));
    r.find("#se-s-liste .se-plus").on("click", () => { this.nSolde += SE_PAGE; this.peindreSolde(); });
    r.find("#se-s-liste .se-art").on("click", (e) => this.voirSorties($(e.currentTarget).attr("data-item")));
  }

  /** Depuis le solde : les sorties de cet article, détail déjà ouvert. */
  voirSorties(item) {
    this.$r.find("#se-o-recherche").val(item);
    this.ouvrirApres = item;
    this.montrer("sorties");
  }

  // ── Sorties ────────────────────────────────────────────────────────────
  periodeDates() {
    const auj = frappe.datetime.get_today(), debutMois = frappe.datetime.month_start();
    switch (this.periode) {
      case "jour": return [auj, auj];
      case "7j": return [frappe.datetime.add_days(auj, -6), auj];
      case "30j": return [frappe.datetime.add_days(auj, -29), auj];
      case "mois": return [debutMois, auj];
      case "mois_prec": return [frappe.datetime.add_months(debutMois, -1), frappe.datetime.add_days(debutMois, -1)];
      case "dates": return [this.$r.find("#se-debut").val() || auj, this.$r.find("#se-fin").val() || auj];
      default: return [frappe.datetime.add_days(auj, -29), auj];
    }
  }

  async chargerSorties() {
    const r = this.$r, [debut, fin] = this.periodeDates();
    const res = await this.appel("sorties", "get_sorties", { entrepot: this.entrepot || null, debut, fin,
      recherche: r.find("#se-o-recherche").val() || null });
    if (!res) return;
    this.sorties = res;
    this.details = {};
    this.nSorties = SE_PAGE;
    this.peindreSorties();
    const cible = this.ouvrirApres;
    this.ouvrirApres = null;
    if (cible && res.articles.some((a) => a.item_code === cible)) this.basculerSortie(cible);
  }

  peindreSorties() {
    const r = this.$r, res = this.sorties;
    const qte = res.articles.reduce((s, a) => s + a.qte, 0);
    r.find("#se-o-info").html(`${res.articles.length} article(s) · ${res.mouvements} sortie(s) · ${se_q(qte)} unité(s)`
      + ` · du ${se_dt(res.debut)} au ${se_dt(res.fin)} · ${this.entrepot ? se_esc(this.libelle(this.entrepot)) : "tous les entrepôts"}`);
    const cartes = res.articles.slice(0, this.nSorties).map((a) => `<div class="se-sortie" data-item="${se_esc(a.item_code)}">
        <div class="se-art">${se_img(a.image)}
          <div class="txt"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
            <div class="se-code">${se_esc(a.item_code)} · ${a.mouvements} sortie(s) · dernière le ${se_dt(a.derniere)}</div></div>
          <div class="se-qte neg">−${se_q(a.qte)}<small>${se_esc(a.uom || "")}</small></div>
          <span class="fleche">▶</span>
        </div>
        <div class="se-lignes" style="display:none"></div>
      </div>`).join("");
    r.find("#se-o-liste").html((cartes || `<div class="se-vide">Aucune sortie sur cette période.</div>`)
      + (res.articles.length > this.nSorties ? `<button type="button" class="btn btn-default se-plus">Afficher plus (${res.articles.length - this.nSorties})</button>` : ""));
    r.find("#se-o-liste .se-plus").on("click", () => { this.nSorties += SE_PAGE; this.peindreSorties(); });
    r.find("#se-o-liste .se-sortie > .se-art").on("click", (e) => this.basculerSortie($(e.currentTarget).closest(".se-sortie").attr("data-item")));
  }

  async basculerSortie(item) {
    const $c = this.$r.find(`#se-o-liste .se-sortie[data-item="${CSS.escape(item)}"]`);
    if (!$c.length) return;
    const $l = $c.find(".se-lignes");
    if ($c.hasClass("ouvert")) { $c.removeClass("ouvert"); $l.hide(); return; }
    $c.addClass("ouvert");
    $l.show().html(`<div class="se-note" style="padding:8px 0">Chargement…</div>`);
    if (!this.details[item]) {
      const [debut, fin] = [this.sorties.debut, this.sorties.fin];
      const res = (await frappe.call({ method: SE_API + "get_sorties_article",
        args: { item_code: item, entrepot: this.entrepot || null, debut, fin } })).message;
      this.details[item] = res;
    }
    const d = this.details[item];
    $l.html(d.lignes.map((l) => this.ligneSortie(l)).join("")
      + (d.lignes.length >= d.limite ? `<div class="se-note">Les ${d.limite} plus récentes seulement : resserrez la période.</div>` : ""));
  }

  ligneSortie(l) {
    const p = [`<div><b>${se_dt(l.date)}</b> ${se_esc(l.heure)} · <b class="neg">−${se_q(l.qte)}</b>`
      + (this.entrepot ? "" : ` <span class="se-badge">${se_esc(this.libelle(l.entrepot))}</span>`) + `</div>`];
    p.push(`<div>${se_lien(l.type, l.piece, `${l.libelle} ${l.piece}`)}${l.vers ? ` → <b>${se_esc(this.libelle(l.vers))}</b>` : ""}</div>`);
    if (l.commande) p.push(`<div>🧾 ${se_lien("Sales Order", l.commande)}${l.client ? ` · ${se_esc(l.client)}` : ""}</div>`);
    else if (l.client) p.push(`<div>👤 ${se_esc(l.client)} <span class="l3">· sans commande</span></div>`);
    if (l.par) p.push(`<div class="l3">🚚 livré par ${se_esc(l.par)}</div>`);
    (l.taches || []).forEach((t) => p.push(`<div class="tache">🛠️ ${se_lien("Tache de travail", t.name, t.type || "Tâche")}`
      + `${t.employe ? ` · ${se_esc(t.employe)}` : ""}${t.date ? ` · ${se_dt(t.date)}` : ""} ${SE_STATUTS[t.statut] || se_esc(t.statut || "")}`
      + ` <span class="l3">${se_esc(t.name)}</span></div>`));
    if (l.commande && !(l.taches || []).length) p.push(`<div class="l3">Aucune tâche liée à cette commande</div>`);
    if (l.remarque && !l.commande) p.push(`<div class="l3">${se_esc(l.remarque)}</div>`);
    return `<div class="se-mvt">${p.join("")}</div>`;
  }

  // ── Transfert (responsable magasin) ──────────────────────────────────────
  initTransfert() {
    const r = this.$r, ctx = this.ctx;
    const options = ctx.entrepots.map((w) => `<option value="${se_esc(w.name)}">${se_esc(w.libelle)}${w.employes.length ? ` (${se_esc(w.employes.join(", "))})` : ""}</option>`).join("");
    r.find("#se-t-de, #se-t-vers").html(options);
    const autre = ctx.entrepots.find((w) => w.name !== ctx.magasin && w.employes.length) || ctx.entrepots.find((w) => w.name !== ctx.magasin);
    r.find("#se-t-de").val(ctx.magasin);
    r.find("#se-t-vers").val(ctx.mien && ctx.mien !== ctx.magasin ? ctx.mien : autre ? autre.name : ctx.magasin);
    r.find("#se-t-de, #se-t-vers").on("change", () => { this.chercherArticles(); this.majQuantitesPanier(); });
    r.find("#se-t-inverser").on("click", () => {
      const de = r.find("#se-t-de").val();
      r.find("#se-t-de").val(r.find("#se-t-vers").val());
      r.find("#se-t-vers").val(de);
      this.chercherArticles();
      this.majQuantitesPanier();
    });
    let t = null;
    r.find("#se-t-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.chercherArticles(), 250); });
    r.find("#se-t-recherche").on("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
    r.find("#se-t-trouves").on("click", "[data-ajouter]", (e) => this.ajouterAuPanier($(e.currentTarget).attr("data-ajouter")));
    r.find("#se-t-panier").on("click", "[data-pas]", (e) => {
      const $b = $(e.currentTarget), l = this.panier.find((x) => x.item_code === $b.attr("data-item"));
      if (!l) return;
      l.qte = Math.max(1, Math.round(((+l.qte || 0) + (+$b.attr("data-pas"))) * 1000) / 1000);
      this.peindrePanier();
    });
    r.find("#se-t-panier").on("click", "[data-enlever]", (e) => {
      this.panier = this.panier.filter((x) => x.item_code !== $(e.currentTarget).attr("data-enlever"));
      this.peindrePanier();
      this.peindreTrouves();
    });
    r.find("#se-t-panier").on("change", "input[data-qte]", (e) => {
      const l = this.panier.find((x) => x.item_code === $(e.currentTarget).attr("data-qte"));
      if (l) { l.qte = Math.max(0, +String($(e.currentTarget).val()).replace(",", ".") || 0); this.peindrePanier(); }
    });
    r.find("#se-t-valider").on("click", () => this.validerTransfert());
    r.find("#se-t-historique").on("click", "[data-annuler]", (e) => this.annulerTransfert($(e.currentTarget).attr("data-annuler")));
    this.trouves = [];
    this.peindrePanier();
  }

  sens() {
    return [this.$r.find("#se-t-de").val(), this.$r.find("#se-t-vers").val()];
  }

  async chercherArticles() {
    const txt = (this.$r.find("#se-t-recherche").val() || "").trim();
    if (txt.length < 2) { this.trouves = []; this.peindreTrouves(); return; }
    const [source, cible] = this.sens();
    const res = await this.appel("recherche", "rechercher_articles", { txt, source, cible });
    if (!res) return;
    this.trouves = res;
    this.peindreTrouves();
  }

  peindreTrouves() {
    const [de, vers] = this.sens(), dans = new Set(this.panier.map((l) => l.item_code));
    this.$r.find("#se-t-trouves").html(this.trouves.map((a) => `<div class="se-z-ligne">${se_img(a.image)}
        <div class="txt" style="flex:1;min-width:0"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
          <div class="se-code">${se_esc(a.item_code)} · ${se_esc(this.libelle(de))} <b class="${a.qte_source < 0 ? "neg" : ""}">${se_q(a.qte_source)}</b>
            · ${se_esc(this.libelle(vers))} <b class="${a.qte_cible < 0 ? "neg" : ""}">${se_q(a.qte_cible)}</b></div></div>
        <span class="se-bt ${dans.has(a.item_code) ? "fait" : ""}" data-ajouter="${se_esc(a.item_code)}" title="Ajouter">${dans.has(a.item_code) ? "✓" : "＋"}</span>
      </div>`).join("") || ((this.$r.find("#se-t-recherche").val() || "").trim().length >= 2 ? `<div class="se-vide">Aucun article suivi en stock ne correspond.</div>` : ""));
  }

  ajouterAuPanier(item) {
    const a = this.trouves.find((x) => x.item_code === item);
    if (!a) return;
    const l = this.panier.find((x) => x.item_code === item);
    if (l) l.qte = (+l.qte || 0) + 1;
    else this.panier.push(Object.assign({}, a, { qte: 1 }));
    this.peindrePanier();
    this.peindreTrouves();
  }

  async majQuantitesPanier() {
    if (!this.panier.length) return;
    const [source, cible] = this.sens();
    const res = await this.appel("qtes", "get_quantites", { items: this.panier.map((l) => l.item_code), source, cible });
    if (!res) return;
    this.panier.forEach((l) => { if (res[l.item_code]) [l.qte_source, l.qte_cible] = res[l.item_code]; });
    this.peindrePanier();
  }

  peindrePanier() {
    const r = this.$r, [de, vers] = this.sens();
    r.find("#se-t-panier").html(this.panier.map((l) => `<div class="se-panier-ligne">
        <div class="haut">${se_img(l.image)}<div style="flex:1;min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div>
          <div class="se-code">${se_esc(l.item_code)} · ${se_esc(this.libelle(de))} ${se_q(l.qte_source)} → ${se_esc(this.libelle(vers))} ${se_q(l.qte_cible)}</div></div></div>
        <div class="se-pas">
          <span class="b" data-pas="-1" data-item="${se_esc(l.item_code)}">−</span>
          <input type="number" inputmode="decimal" min="0" step="any" class="form-control" data-qte="${se_esc(l.item_code)}" value="${se_esc(l.qte)}">
          <span class="b" data-pas="1" data-item="${se_esc(l.item_code)}">＋</span>
          <span style="font-size:12px;color:#64748b">${se_esc(l.uom || "")}</span>
          <span class="b x" data-enlever="${se_esc(l.item_code)}" title="Retirer">✕</span>
        </div>
        ${l.qte > l.qte_source ? `<div class="se-alerte">⚠️ Plus que le stock de ${se_esc(this.libelle(de))} (${se_q(l.qte_source)}) : il passera en négatif.</div>` : ""}
      </div>`).join("") || `<div class="se-note">Cherchez un article ci-dessus puis touchez ＋.</div>`);
    const n = this.panier.filter((l) => l.qte > 0).length;
    r.find("#se-t-valider").prop("disabled", !n || de === vers)
      .text(de === vers ? "Choisissez deux entrepôts différents" : n ? `Valider le transfert (${n} article${n > 1 ? "s" : ""})` : "Valider le transfert");
  }

  validerTransfert() {
    const [source, cible] = this.sens();
    const lignes = this.panier.filter((l) => l.qte > 0).map((l) => ({ item_code: l.item_code, qte: l.qte }));
    if (!lignes.length || source === cible) return;
    frappe.confirm(`Transférer <b>${lignes.length}</b> article(s) de <b>${se_esc(this.libelle(source))}</b> vers <b>${se_esc(this.libelle(cible))}</b> ?`, async () => {
      const $b = this.$r.find("#se-t-valider").prop("disabled", true);
      try {
        const res = (await frappe.call({ method: SE_API + "creer_transfert", freeze: true, freeze_message: "Transfert…",
          args: { source, cible, lignes, remarque: this.$r.find("#se-t-remarque").val() || null } })).message;
        frappe.show_alert({ message: `✅ Transfert ${se_esc(res.name)} : ${res.lignes} article(s)`, indicator: "green" }, 6);
        this.panier = [];
        this.$r.find("#se-t-remarque").val("");
        this.chercherArticles();
        this.chargerHistorique();
      } finally {
        $b.prop("disabled", false);
        this.peindrePanier();
      }
    });
  }

  async chargerHistorique() {
    const res = await this.appel("historique", "transferts_recents", { limite: 15 });
    if (!res) return;
    this.$r.find("#se-t-historique").html(res.map((t) => `<div class="se-card se-tr ${t.docstatus === 2 ? "annule" : ""}">
        <div class="tete"><span>${se_lien("Stock Entry", t.name)} ${t.docstatus === 2 ? `<span class="se-badge">annulé</span>` : ""}</span>
          <span class="se-note">${se_dt(t.date)} ${se_esc(t.heure)} · ${se_esc(t.par)}</span></div>
        <div class="sens">${se_esc(this.libelle(t.de || (t.lignes[0] || {}).de))} → ${se_esc(this.libelle(t.vers || (t.lignes[0] || {}).vers))}</div>
        <div class="det">${t.lignes.slice(0, 6).map((l) => `${se_esc(l.item_name || l.item_code)} <b>${se_q(l.qte)}</b>`).join(" · ")}${t.lignes.length > 6 ? ` · … +${t.lignes.length - 6}` : ""}</div>
        ${t.remarque ? `<div class="se-note">${se_esc(t.remarque)}</div>` : ""}
        ${t.docstatus === 1 ? `<div class="actions"><button type="button" class="btn btn-xs btn-default" data-annuler="${se_esc(t.name)}">Annuler ce transfert</button></div>` : ""}
      </div>`).join("") || `<div class="se-vide">Aucun transfert.</div>`);
  }

  annulerTransfert(name) {
    frappe.confirm(`Annuler le transfert <b>${se_esc(name)}</b> ? Les quantités reviennent à leur place.`, async () => {
      await frappe.call({ method: SE_API + "annuler_transfert", args: { name }, freeze: true });
      frappe.show_alert({ message: `Transfert ${se_esc(name)} annulé`, indicator: "orange" }, 5);
      this.chargerHistorique();
      this.majQuantitesPanier();
    });
  }

  // ── Remise à zéro (responsable magasin) ──────────────────────────────────
  async chargerZero() {
    this.zeroEntrepot = null;
    const res = await this.appel("zero", "entrepots_a_zero", {});
    if (!res) return;
    const $c = this.$r.find("#se-z-contenu");
    $c.html(`<div class="se-note" style="margin:0 2px 10px">L’écart d’inventaire reste sur <b>${se_esc(this.libelle(res.magasin))}</b> :
        les quantités négatives sont apportées depuis lui, les positives y retournent.
        ${res.exclus.length ? `Jamais remis à zéro : ${res.exclus.map((e) => se_esc(this.libelle(e))).join(", ")}.` : ""}
        <a class="se-lien" href="/app/config-stock-entrepot">⚙️ Réglages</a></div>`
      + (res.entrepots.map((w) => `<div class="se-card se-wz">
          <div class="t">🏬 ${se_esc(w.libelle)}</div>
          ${w.employes.length ? `<div class="emp">👤 ${se_esc(w.employes.join(", "))}</div>` : ""}
          ${w.negatifs || w.positifs ? `<div class="stats">
              ${w.negatifs ? `<span class="neg">${w.negatifs} article(s) négatif(s) · ${se_signe(-w.qte_neg)}</span>` : ""}
              ${w.positifs ? `<span class="pos">${w.positifs} article(s) en stock · +${se_q(w.qte_pos)}</span>` : ""}
              <span class="se-note">valeur ${se_esc(se_argent(w.valeur))}</span></div>
            <button type="button" class="btn btn-sm btn-default" data-zero="${se_esc(w.name)}" style="width:100%">Voir et remettre à zéro</button>`
            : `<div class="ok">✓ Déjà à zéro</div>`}
        </div>`).join("") || `<div class="se-vide">Aucun entrepôt à remettre à zéro.</div>`));
    $c.find("[data-zero]").on("click", (e) => this.ouvrirZero($(e.currentTarget).attr("data-zero")));
  }

  async ouvrirZero(entrepot) {
    this.zeroEntrepot = entrepot;
    const res = await this.appel("zero", "apercu_zero", { entrepot });
    if (!res) return;
    this.zero = res;
    this.zeroCoches = new Set(res.lignes.filter((l) => !l.bloque).map((l) => l.item_code));
    this.peindreZero();
    window.scrollTo({ top: 0 });
  }

  peindreZero() {
    const res = this.zero, m = this.libelle(res.magasin), $c = this.$r.find("#se-z-contenu");
    const choisies = res.lignes.filter((l) => !l.bloque && this.zeroCoches.has(l.item_code));
    const apports = choisies.filter((l) => l.sens === "apport"), retours = choisies.filter((l) => l.sens === "retour");
    const ajust = apports.reduce((s, l) => s + (l.ajustement || 0), 0);
    const toutes = res.lignes.filter((l) => !l.bloque);
    $c.html(`<span class="se-retour" data-retour>← Tous les entrepôts</span>
      <div class="se-card">
        <div class="se-wz"><div class="t">🏬 ${se_esc(this.libelle(res.entrepot))}</div></div>
        ${res.lignes.length ? `<div class="se-resume">
            ${apports.length ? `<div>⬅️ <b>${apports.length}</b> apport(s) depuis ${se_esc(m)} : ${se_signe(apports.reduce((s, l) => s - l.qte, 0))} unité(s)</div>` : ""}
            ${retours.length ? `<div>➡️ <b>${retours.length}</b> retour(s) vers ${se_esc(m)} : ${se_q(retours.reduce((s, l) => s + l.qte, 0))} unité(s)</div>` : ""}
            ${Math.abs(ajust) >= 1 ? `<div class="se-note">Ajustement de valorisation estimé : <b>${se_esc(se_argent(ajust))}</b>
              (le stock négatif garde une valeur calculée à ses propres taux ; l’apport entre au taux de ${se_esc(m)}).</div>` : ""}
          </div>
          <div style="margin-top:8px"><span class="se-chip" data-tout>${choisies.length === toutes.length ? "Tout décocher" : "Tout cocher"}</span></div>`
          : `<div class="ok" style="color:#15803d;font-weight:600;margin-top:6px">✓ Cet entrepôt est à zéro.</div>`}
      </div>
      ${res.lignes.length ? `<div class="se-card">${res.lignes.map((l) => `<div class="se-z-ligne ${l.bloque ? "bloque" : ""}" data-item="${se_esc(l.item_code)}">
          ${l.bloque ? `<span class="se-coche" style="cursor:default">–</span>` : `<span class="se-coche ${this.zeroCoches.has(l.item_code) ? "on" : ""}">${this.zeroCoches.has(l.item_code) ? "✓" : ""}</span>`}
          ${se_img(l.image)}
          <div style="flex:1;min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div>
            <div class="se-code">${se_esc(l.item_code)} · stock <b class="${l.qte < 0 ? "neg" : "pos"}">${se_q(l.qte)}</b> ${se_esc(l.uom || "")}</div>
            ${l.bloque ? `<div class="se-alerte">Non transférable : ${se_esc(l.bloque)}</div>` : ""}</div>
          <div class="mvt ${l.sens === "apport" ? "pos" : ""}">${l.sens === "apport" ? `+${se_q(-l.qte)}<small>depuis ${se_esc(m)}</small>` : `−${se_q(l.qte)}<small>vers ${se_esc(m)}</small>`}</div>
        </div>`).join("")}
        <div class="se-barre"><button type="button" class="btn btn-danger" data-valider ${choisies.length ? "" : "disabled"}>
          Remettre à zéro (${choisies.length} article${choisies.length > 1 ? "s" : ""})</button></div></div>` : ""}`);
    $c.find("[data-retour]").on("click", () => this.chargerZero());
    $c.find("[data-tout]").on("click", () => {
      this.zeroCoches = choisies.length === toutes.length ? new Set() : new Set(toutes.map((l) => l.item_code));
      this.peindreZero();
    });
    $c.find(".se-z-ligne:not(.bloque)").on("click", (e) => {
      const c = $(e.currentTarget).attr("data-item");
      this.zeroCoches.has(c) ? this.zeroCoches.delete(c) : this.zeroCoches.add(c);
      this.peindreZero();
    });
    $c.find("[data-valider]").on("click", () => this.validerZero(choisies, apports.length, retours.length));
  }

  validerZero(choisies, nApports, nRetours) {
    const res = this.zero, m = se_esc(this.libelle(res.magasin)), cible = se_esc(this.libelle(res.entrepot));
    frappe.confirm(`Remettre <b>${cible}</b> à zéro sur ${choisies.length} article(s) ?<br>`
      + (nApports ? `• un transfert ${m} → ${cible} (${nApports} article(s))<br>` : "")
      + (nRetours ? `• un transfert ${cible} → ${m} (${nRetours} article(s))<br>` : "")
      + `L’écart restera sur ${m}.`, async () => {
      const r = (await frappe.call({ method: SE_API + "remettre_a_zero", freeze: true, freeze_message: "Remise à zéro…",
        args: { entrepot: res.entrepot, items: choisies.map((l) => l.item_code) } })).message;
      frappe.msgprint({ title: "Remise à zéro faite", indicator: "green",
        message: `${r.ecritures.map((n) => se_lien("Stock Entry", n)).join("<br>")}`
          + (r.restant.length ? `<br><br>⚠️ Encore non nul (a bougé pendant l’opération ?) : ${r.restant.map(se_esc).join(", ")}` : "") });
      this.ouvrirZero(res.entrepot);
    });
  }
}
