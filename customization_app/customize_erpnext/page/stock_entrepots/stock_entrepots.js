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
    this.reappro = false;
    this.selSolde = new Set();
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
    if (!ctx.verification) r.find('[data-onglet="verif"]').remove();
    r.find("#se-s-reappro").on("click", (e) => { this.reappro = !this.reappro; $(e.currentTarget).toggleClass("on", this.reappro); this.chargerSolde(); });
    r.find("#se-s-aucun").on("click", () => { this.selSolde.clear(); this.peindreSolde(); });
    r.find("#se-s-tout").on("click", () => { (this.solde ? this.solde.articles : []).forEach((a) => this.selSolde.add(a.item_code)); this.peindreSolde(); });
    r.find("#se-s-transferer").on("click", () => this.transfererSelection());
    r.find("#se-s-vers-cible").on("click", () => this.ajouterSelectionAuCible());
    r.find("#se-s-cible").on("click", () => this.ouvrirCible());
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
    r.find("#se-s-attente").on("click", "[data-valider]", (e) => this.ouvrirValidation($(e.currentTarget).attr("data-valider")));
    frappe.realtime.on("se_transferts_a_valider", () => { this.chargerAttente(); if (this.onglet === "transfert") this.chargerHistorique(); });
    this.pret = true;
    this.montrer("solde");
    await this.chargerAttente();
    const voulu = frappe.route_options && frappe.route_options.valider;
    if (voulu) { frappe.route_options = null; this.ouvrirValidation(voulu); }
  }

  // ── Transferts en attente de validation (double validation Magasin → stock d'un employé) ──
  /** Les demandes en attente : les miennes en bandeau (Solde), toutes en badge de l'onglet Transfert. */
  async chargerAttente() {
    const liste = (await frappe.call({ method: SE_API + "transferts_a_valider" })).message || [];
    this.attente = liste;
    const miens = liste.filter((t) => t.employe && t.employe === this.ctx.employe_id);
    this.$r.find("#se-s-attente").html(miens.map((t) => `<div class="se-bandeau">
        <div class="t">📥 Transfert ${se_esc(t.name)} à valider — ${se_esc(this.libelle(t.de))} → votre stock</div>
        <div class="d">${t.lignes.length} article(s) · ${se_esc(t.par)} · ${se_esc(t.quand)}${t.remarque ? ` · ${se_esc(t.remarque)}` : ""}</div>
        <button type="button" class="btn btn-primary btn-sm" data-valider="${se_esc(t.name)}">✅ Vérifier et confirmer la réception</button>
      </div>`).join(""));
    this.$r.find("#se-t-badge").toggle(!!liste.length).text(liste.length);
  }

  /** Feuille de détail d'un transfert : photo, code, nom, quantité ; état de validation. */
  ouvrirDetail(t) {
    const lignes = (t.lignes || []).map((l) => `<div class="se-z-ligne">${se_img(l.image)}
        <div style="flex:1;min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div><div class="se-code">${se_esc(l.item_code)}</div></div>
        <div class="mvt">${se_q(l.qte)}<small>${se_esc(l.uom || "")}</small></div></div>`).join("");
    const etat = t.attente ? `<div class="se-attente ${t.attente.age_h > 24 ? "vieux" : ""}">⏳ En attente de validation de ${se_esc(t.attente.employe_nom)}</div>`
      : t.validation ? `<div class="se-valide">✅ Réception confirmée par ${se_esc(t.validation.par)} le ${se_esc(t.validation.le)}${t.validation.ecart ? `<div class="ecart">⚠️ ${se_esc(t.validation.ecart)}</div>` : ""}</div>`
      : t.docstatus === 2 ? `<span class="se-badge">annulé</span>` : "";
    const d = new frappe.ui.Dialog({ title: `🔁 ${t.name}`, size: "large", fields: [{ fieldtype: "HTML", fieldname: "corps" }] });
    d.fields_dict.corps.$wrapper.html(`<div class="se-note">${se_esc(this.libelle(t.de))} → <b>${se_esc(this.libelle(t.vers))}</b> · ${se_esc(t.par)} · ${se_esc(t.quand || `${se_dt(t.date)} ${t.heure || ""}`)}</div>
      ${etat}${t.remarque ? `<div class="se-note">${se_esc(t.remarque)}</div>` : ""}
      <div style="margin-top:8px">${lignes}</div>
      <div class="se-note" style="margin-top:8px">${(t.lignes || []).length} article(s) · ${se_q((t.lignes || []).reduce((a, l) => a + l.qte, 0))} unité(s) · ${se_lien("Stock Entry", t.name, "ouvrir la fiche")}</div>`);
    d.show();
  }

  /** L'employé confirme la réception, quantité par quantité : baisser = écart tracé, 0 = non reçu. */
  async ouvrirValidation(name) {
    const t = (this.attente || []).find((x) => x.name === name)
      || ((await frappe.call({ method: SE_API + "transferts_a_valider" })).message || []).find((x) => x.name === name);
    if (!t) { frappe.show_alert({ message: `Le transfert ${se_esc(name)} n’est plus en attente.`, indicator: "orange" }, 5); this.chargerAttente(); return; }
    const d = new frappe.ui.Dialog({ title: `📥 Réception ${t.name}`, size: "large", fields: [{ fieldtype: "HTML", fieldname: "corps" }],
      primary_action_label: "✅ Confirmer la réception", primary_action: async () => {
        const lignes = t.lignes.map((l) => ({ item_code: l.item_code, qte: +String(d.$wrapper.find(`input[data-recu="${CSS.escape(l.item_code)}"]`).val()).replace(",", ".") || 0 }));
        const ecarts = lignes.filter((l, i) => l.qte !== t.lignes[i].qte).length;
        const go = async () => {
          d.hide();
          const res = (await frappe.call({ method: SE_API + "valider_transfert", args: { name: t.name, lignes }, freeze: true, freeze_message: "Réception…" })).message;
          frappe.show_alert({ message: res.supprime ? `Rien reçu : demande ${se_esc(t.name)} retirée` : `✅ Réception confirmée : ${res.lignes} article(s)${res.ecarts.length ? ` · ${res.ecarts.length} écart(s) signalé(s)` : ""}`,
            indicator: res.supprime ? "orange" : res.ecarts.length ? "yellow" : "green" }, 6);
          this.chargerAttente(); this.rafraichir();
        };
        if (ecarts) frappe.confirm(`<b>${ecarts}</b> quantité(s) différente(s) de l’envoi : l’écart sera signalé à ${se_esc(t.par)}. Confirmer ?`, go);
        else go();
      } });
    d.fields_dict.corps.$wrapper.html(`<div class="se-note">${se_esc(this.libelle(t.de))} → <b>${se_esc(this.libelle(t.vers))}</b> · demandé par ${se_esc(t.par)} · ${se_esc(t.quand)}${t.remarque ? ` · ${se_esc(t.remarque)}` : ""}</div>
      <div class="se-note">Vérifiez chaque article : corrigez la quantité si le carton ne correspond pas (0 = non reçu).</div>
      <div style="margin-top:8px">${t.lignes.map((l) => `<div class="se-cb-ligne">${se_img(l.image)}
        <div class="txt"><div class="se-nom" style="font-size:13.5px">${se_esc(l.item_name || l.item_code)}</div><div class="se-code">${se_esc(l.item_code)} · envoyé <b>${se_q(l.qte)}</b> ${se_esc(l.uom || "")}</div></div>
        <input type="number" inputmode="decimal" min="0" max="${se_esc(l.qte)}" step="any" class="form-control c" data-recu="${se_esc(l.item_code)}" value="${se_esc(l.qte)}"></div>`).join("")}</div>`);
    d.show();
  }

  /** Transfert : complète le panier avec ce qui manque au stock cible de la destination (articles ciblés seulement). */
  async reassortCible() {
    const [source, cible] = this.sens();
    // Sans cible propre au véhicule, le modèle générique du réglage sert de cible.
    const d = (await frappe.call({ method: SE_API + "get_stock_cible", args: { entrepot: cible, avec_modele: 1 } })).message;
    if (!(d.lignes || []).length) {
      frappe.msgprint({ title: "Pas de stock cible", indicator: "orange", message: `Ni stock cible pour <b>${se_esc(this.libelle(cible))}</b>, ni modèle générique.<br>Remplissez le <b>stock cible générique</b> dans <a href="/app/config-stock-entrepot">les réglages</a>, ou définissez celui de ce stock depuis l’onglet <b>Solde</b> → <b>🎯 Stock cible</b>.` });
      return;
    }
    const base = d.modele ? "modèle générique (ce stock n’a pas de cible propre)" : "stock cible";
    const manque = (d.lignes || []).filter((l) => l.manque > 0);
    if (!manque.length) { frappe.show_alert({ message: `Rien à compléter : ${se_esc(this.libelle(cible))} est au niveau du ${base}.`, indicator: "green" }, 5); return; }
    let ajoutes = 0;
    manque.forEach((l) => {
      if (this.panier.some((x) => x.item_code === l.item_code)) return;
      this.panier.push({ item_code: l.item_code, item_name: l.item_name, image: l.image, uom: l.uom, qte: l.manque, qte_source: l.qte_source, qte_cible: l.qte });
      ajoutes += 1;
    });
    await this.majQuantitesPanier();
    this.peindreTrouves();
    frappe.show_alert({ message: `🎯 ${ajoutes} article(s) ajouté(s) selon le ${base} de ${se_esc(this.libelle(cible))}${manque.length - ajoutes ? ` (${manque.length - ajoutes} déjà dans le panier)` : ""} — vérifiez puis validez`, indicator: "blue" }, 6);
  }

  majBoutonReassort() {
    const [, cible] = this.sens();
    const w = this.ctx.entrepots.find((x) => x.name === cible);
    // Visible pour tout stock d'employé, cible définie ou non : sans cible, le bouton dit où la définir.
    this.$r.find("#se-t-reassort").toggle(!!(w && !w.magasin)).toggleClass("on", (this.ctx.cibles || []).includes(cible));
  }

  montrer(onglet) {
    this.onglet = onglet;
    if (onglet !== "verif") this.comptage = null;
    const r = this.$r;
    r.find(".se-onglet").each((_, el) => $(el).toggleClass("on", $(el).attr("data-onglet") === onglet));
    r.find(".se-vue").each((_, el) => $(el).toggle($(el).attr("data-vue") === onglet));
    r.find("#se-entrepots").toggle(onglet === "solde" || onglet === "sorties");
    this.rafraichir();
  }

  rafraichir() {
    ({ solde: () => this.chargerSolde(), sorties: () => this.chargerSorties(),
       transfert: () => { this.chargerHistorique(); this.majQuantitesPanier(); },
       zero: () => (this.zeroEntrepot ? this.ouvrirZero(this.zeroEntrepot) : this.chargerZero()),
       verif: () => (this.comptage ? this.ouvrirComptage(this.comptage) : this.chargerVerif()) })[this.onglet]();
    this.majBarreSolde();
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
    const seuil = this.entrepot && this.ctx.seuils[this.entrepot];
    const cibleOk = !!(this.ctx.responsable && this.entrepot && this.entrepot !== this.ctx.magasin);
    r.find("#se-s-reappro").toggle(!!((seuil || (this.ctx.cibles || []).includes(this.entrepot)) && this.ctx.responsable));
    r.find("#se-s-cible").toggle(cibleOk).toggleClass("on", (this.ctx.cibles || []).includes(this.entrepot));
    r.find("#se-s-cible-vue").hide(); r.find("#se-s-solde").show();
    if (!seuil) this.reappro = false;
    r.find("#se-s-reappro").toggleClass("on", this.reappro);
    const res = await this.appel("solde", "get_solde", { entrepot: this.entrepot || null,
      recherche: r.find("#se-s-recherche").val() || null, negatifs: this.negatifs ? 1 : 0, a_reappro: this.reappro ? 1 : 0 });
    if (!res) return;
    this.solde = res;
    this.nSolde = SE_PAGE;
    this.selSolde.clear();
    this.peindreSolde();
  }

  peindreSolde() {
    const r = this.$r, res = this.solde, tous = !this.entrepot;
    const coches = !!(this.ctx.responsable && this.entrepot);
    r.find("#se-s-info").html(`${res.articles.length} article(s)`
      + (res.negatifs ? ` · <span class="neg">${res.negatifs} négatif(s)</span>` : "")
      + (res.a_reappro ? ` · <span style="color:#c2410c">${res.a_reappro} à réapprovisionner</span>` : "")
      + (res.valeur != null ? ` · valeur ${se_esc(se_argent(res.valeur))}` : "")
      + ` · ${tous ? "tous les entrepôts" : se_esc(this.libelle(this.entrepot))}`);
    const cartes = res.articles.slice(0, this.nSolde).map((a) => `<div class="se-art ${this.selSolde.has(a.item_code) ? "sel" : ""}" data-item="${se_esc(a.item_code)}">
        ${coches ? `<span class="se-coche ${this.selSolde.has(a.item_code) ? "on" : ""}" data-coche>${this.selSolde.has(a.item_code) ? "✓" : ""}</span>` : ""}
        ${se_img(a.image)}
        <div class="txt"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
          <div class="se-code">${se_esc(a.item_code)}${a.zones ? ` · 📍 ${se_esc(a.zones)}` : ""}</div>
          ${a.a_reappro ? `<span class="se-reappro">🔻 à réapprovisionner · ${a.cible != null ? `cible ${se_q(a.cible)}` : `seuil ${se_q(a.seuil)}`} · manque ${se_q(a.a_transferer)}</span>` : ""}
          ${tous ? `<div class="se-wh">${a.entrepots.map((e) => `<span class="${e.qte < 0 ? "neg" : ""}">${se_esc(e.libelle)} <b>${se_q(e.qte)}</b></span>`).join("")}</div>` : ""}
        </div>
        <div class="se-qte ${a.qte < 0 ? "neg" : ""}">${se_q(a.qte)}<small>${se_esc(a.uom || "")}</small></div>
      </div>`).join("");
    r.find("#se-s-liste").html((cartes || `<div class="se-vide">Aucun stock pour ces filtres.</div>`)
      + (res.articles.length > this.nSolde ? `<button type="button" class="btn btn-default se-plus">Afficher plus (${res.articles.length - this.nSolde})</button>` : ""));
    r.find("#se-s-liste .se-plus").on("click", () => { this.nSolde += SE_PAGE; this.peindreSolde(); });
    r.find("#se-s-liste .se-art").on("click", (e) => this.voirSorties($(e.currentTarget).attr("data-item")));
    r.find("#se-s-liste [data-coche]").on("click", (e) => {
      e.stopPropagation();
      const c = $(e.currentTarget).closest(".se-art").attr("data-item");
      const visibles = res.articles.slice(0, this.nSolde).map((a) => a.item_code);
      const idx = visibles.indexOf(c);
      if (e.shiftKey && this.dernierCoche != null && this.dernierCoche !== idx) {
        // Maj + clic : toute la plage entre la dernière case cliquée et celle-ci prend l'état de celle-ci.
        const coche = !this.selSolde.has(c);
        const [d, f] = [Math.min(this.dernierCoche, idx), Math.max(this.dernierCoche, idx)];
        visibles.slice(d, f + 1).forEach((code) => (coche ? this.selSolde.add(code) : this.selSolde.delete(code)));
        window.getSelection && window.getSelection().removeAllRanges();
        this.dernierCoche = idx;
        this.peindreSolde();
        return;
      }
      this.dernierCoche = idx;
      this.selSolde.has(c) ? this.selSolde.delete(c) : this.selSolde.add(c);
      $(e.currentTarget).toggleClass("on", this.selSolde.has(c)).text(this.selSolde.has(c) ? "✓" : "");
      $(e.currentTarget).closest(".se-art").toggleClass("sel", this.selSolde.has(c));
      this.majBarreSolde();
    });
    this.majBarreSolde();
  }

  majBarreSolde() {
    const n = this.selSolde.size, $b = this.$r.find("#se-s-barre");
    this.$r.find("#se-s-compte").text(`${n} article(s) coché(s)`);
    const feuilleCible = this.$r.find("#se-s-cible-vue").is(":visible");
    n && this.onglet === "solde" && this.ctx.responsable && !feuilleCible ? $b.show() : $b.hide();
  }

  /** Le véhicule visé : celui affiché, ou, depuis le Magasin / « Tous », un choix dans un dialogue. */
  choisirVehicule(titre) {
    if (this.entrepot && this.entrepot !== this.ctx.magasin) return Promise.resolve(this.entrepot);
    const choix = this.ctx.entrepots.filter((w) => w.name !== this.ctx.magasin);
    if (!choix.length) return Promise.resolve(null);
    return new Promise((resolve) => {
      const d = new frappe.ui.Dialog({ title: titre, fields: [{ fieldtype: "Select", fieldname: "vehicule", label: "Vers quel stock ?", reqd: 1,
          options: choix.map((w) => ({ value: w.name, label: w.libelle + (w.employes.length ? ` (${w.employes.join(", ")})` : "") })),
          default: this.ctx.mien && this.ctx.mien !== this.ctx.magasin ? this.ctx.mien : choix[0].name }],
        primary_action_label: "Continuer", primary_action: (v) => { choisi = v.vehicule; d.hide(); } });
      let choisi = null;
      d.onhide = () => resolve(choisi);           // hide() déclenche onhide : le choix est posé AVANT
      d.show();
    });
  }

  async ajouterSelectionAuCible() {
    const items = Array.from(this.selSolde);
    if (!items.length) return;
    const cible = await this.choisirVehicule("🎯 Dans le stock cible de…");
    if (!cible) return;
    const r = (await frappe.call({ method: SE_API + "ajouter_au_stock_cible", args: { entrepot: cible, items }, freeze: true })).message;
    if (!(this.ctx.cibles || []).includes(cible)) (this.ctx.cibles = this.ctx.cibles || []).push(cible);
    frappe.show_alert({ message: `🎯 ${r.ajoutes} article(s) ajouté(s) au stock cible de ${se_esc(this.libelle(cible))} (${r.total} au total) — réglez les quantités`, indicator: "green" }, 5);
    this.selSolde.clear();
    if (this.entrepot !== cible) { this.entrepot = cible; this.peindreEntrepots(); }
    this.ouvrirCible();
  }

  /** Feuille « Stock cible » de l'entrepôt : quantité cible par article, manque, réassort en un geste. */
  async ouvrirCible() {
    const entrepot = this.entrepot, r = this.$r;
    const d = (await frappe.call({ method: SE_API + "get_stock_cible", args: { entrepot } })).message;
    const modele = (await frappe.call({ method: SE_API + "get_modele_stock_cible" })).message || { articles: 0 };
    const $v = r.find("#se-s-cible-vue");
    r.find("#se-s-solde").hide(); $v.show();
    this.selSolde.clear(); this.majBarreSolde();
    const lignes = d.lignes.map((l) => Object.assign({}, l));
    const ligne = (l) => `<div class="se-cb-ligne" data-item="${se_esc(l.item_code)}">${se_img(l.image)}
        <div class="txt"><div class="se-nom" style="font-size:13.5px">${se_esc(l.item_name || l.item_code)}</div>
          <div class="se-code">${se_esc(l.item_code)} · ici <b class="${l.qte < 0 ? "neg" : ""}">${se_q(l.qte)}</b> · ${se_esc(this.libelle(d.source))} ${se_q(l.qte_source)}</div></div>
        <input type="number" inputmode="decimal" min="0" step="any" class="form-control c" value="${se_esc(l.qte_cible)}">
        <div class="m ${l.manque > 0 ? "neg" : "pos"}">${l.manque > 0 ? `manque ${se_q(l.manque)}` : "✓"}</div>
        <span class="x" data-enlever title="Retirer">✕</span></div>`;
    const peindre = () => {
      const n = lignes.filter((l) => l.manque > 0).length, u = lignes.reduce((s, l) => s + l.manque, 0);
      $v.html(`<span class="se-retour" data-retour>← Solde</span>
        <div class="se-card">
          <div class="se-v-stock"><div class="t">🎯 Stock cible — ${se_esc(this.libelle(entrepot))}</div>
            <div class="plan">Ce que ce stock doit contenir. Réassort depuis <b>${se_esc(this.libelle(d.source))}</b> : cible − stock actuel.
              · <a class="se-lien" href="/app/config-stock-entrepot">⚙️ Réglages</a></div>
            ${modele.articles ? `<button type="button" class="btn btn-default btn-sm" data-modele style="margin-top:6px">📋 Appliquer le modèle générique (${modele.articles} articles)</button>`
              : `<div class="se-note">Pas de modèle générique : définissez-le dans les réglages pour l’appliquer à chaque véhicule.</div>`}</div>
          <input type="search" class="form-control se-saisie" id="se-cb-recherche" style="margin-top:8px" placeholder="➕ Ajouter un article (nom ou code)…">
          <div id="se-cb-trouves"></div>
          <div id="se-cb-lignes" style="margin-top:6px">${lignes.map(ligne).join("") || `<div class="se-note" style="padding:10px 0">Aucun article : cherchez ci-dessus, ou cochez des articles dans le solde puis « Dans le stock cible ».</div>`}</div>
          <div class="se-c-barre"><button type="button" class="btn btn-default" data-enregistrer>💾 Enregistrer</button>
            <button type="button" class="btn btn-primary" data-reassort ${n ? "" : "disabled"}>🔁 Réassort (${n} art. · ${se_q(u)} u.)</button></div>
        </div>`);
      let t = null;
      $v.find("#se-cb-recherche").on("input", (e) => { clearTimeout(t); const txt = e.currentTarget.value.trim(); t = setTimeout(async () => {
        const tr = txt.length < 2 ? [] : (await frappe.call({ method: SE_API + "rechercher_articles", args: { txt, source: d.source, cible: entrepot } })).message;
        const dans = new Set(lignes.map((l) => l.item_code));
        $v.find("#se-cb-trouves").html(tr.map((a) => `<div class="se-z-ligne">${se_img(a.image)}<div style="flex:1;min-width:0"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
            <div class="se-code">${se_esc(a.item_code)} · ici ${se_q(a.qte_cible)} · ${se_esc(this.libelle(d.source))} ${se_q(a.qte_source)}</div></div>
            <span class="se-bt ${dans.has(a.item_code) ? "fait" : ""}" data-ajouter="${se_esc(a.item_code)}">${dans.has(a.item_code) ? "✓" : "＋"}</span></div>`).join(""));
        $v.find("[data-ajouter]").on("click", (ev) => {
          const a = tr.find((x) => x.item_code === $(ev.currentTarget).attr("data-ajouter"));
          if (!a || dans.has(a.item_code)) return;
          lignes.push({ item_code: a.item_code, item_name: a.item_name, image: a.image, uom: a.uom, qte_cible: Math.max(a.qte_cible, 1), qte: a.qte_cible, qte_source: a.qte_source, manque: 0 });
          lignes.forEach((l) => { l.manque = Math.max(l.qte_cible - l.qte, 0); });
          peindre(); $v.find("#se-cb-recherche").val(txt);
        });
      }, 250); });
      $v.find("input.c").on("change", (e) => {
        const l = lignes.find((x) => x.item_code === $(e.currentTarget).closest(".se-cb-ligne").attr("data-item"));
        l.qte_cible = Math.max(0, +String(e.currentTarget.value).replace(",", ".") || 0); l.manque = Math.max(l.qte_cible - l.qte, 0); peindre();
      });
      $v.find("[data-enlever]").on("click", (e) => { const c = $(e.currentTarget).closest(".se-cb-ligne").attr("data-item"); lignes.splice(lignes.findIndex((x) => x.item_code === c), 1); peindre(); });
      $v.find("[data-retour]").on("click", () => { $v.hide(); r.find("#se-s-solde").show(); this.chargerSolde(); });
      $v.find("[data-modele]").on("click", () => {
        const dlg = new frappe.ui.Dialog({ title: "📋 Appliquer le modèle générique", fields: [
            { fieldtype: "HTML", options: `<div class="se-note">Les ${modele.articles} articles du modèle sont ajoutés au stock cible de <b>${se_esc(this.libelle(entrepot))}</b>. Les quantités déjà saisies ici (non enregistrées) seront perdues : enregistrez d’abord si besoin.</div>` },
            { fieldtype: "Check", fieldname: "remplacer", label: "Remplacer les quantités déjà définies par celles du modèle" }],
          primary_action_label: "Appliquer", primary_action: async (v) => {
            dlg.hide();
            const res = (await frappe.call({ method: SE_API + "appliquer_modele_cible", args: { entrepot, remplacer: v.remplacer ? 1 : 0 }, freeze: true })).message;
            if (!(this.ctx.cibles || []).includes(entrepot)) (this.ctx.cibles = this.ctx.cibles || []).push(entrepot);
            frappe.show_alert({ message: `📋 Modèle appliqué : ${res.lignes.length} article(s) dans le stock cible`, indicator: "green" }, 4);
            this.ouvrirCible();
          } });
        dlg.show();
      });
      const sauver = async () => {
        const res = (await frappe.call({ method: SE_API + "definir_stock_cible", args: { entrepot, lignes: lignes.map((l) => ({ item_code: l.item_code, qte_cible: l.qte_cible })) }, freeze: true })).message;
        if (!(this.ctx.cibles || []).includes(entrepot) && res.lignes.length) (this.ctx.cibles = this.ctx.cibles || []).push(entrepot);
        return res;
      };
      $v.find("[data-enregistrer]").on("click", async () => { await sauver(); frappe.show_alert({ message: "Stock cible enregistré", indicator: "green" }, 3); });
      $v.find("[data-reassort]").on("click", async () => {
        const res = await sauver();
        const aFaire = res.lignes.filter((l) => l.manque > 0);
        if (!aFaire.length) return;
        this.$r.find("#se-t-de").val(res.source); this.$r.find("#se-t-vers").val(entrepot);
        this.panier = aFaire.map((l) => ({ item_code: l.item_code, item_name: l.item_name, image: l.image, uom: l.uom, qte: l.manque, qte_source: l.qte_source, qte_cible: l.qte }));
        this.trouves = []; this.$r.find("#se-t-recherche").val("");
        $v.hide(); r.find("#se-s-solde").show();
        this.montrer("transfert"); this.peindreTrouves(); this.peindrePanier();
        frappe.show_alert({ message: `🔁 Réassort préparé : ${aFaire.length} article(s) — vérifiez puis validez`, indicator: "blue" }, 5);
      });
    };
    peindre();
  }

  /** Les articles cochés du solde partent dans le panier de l'onglet Transfert : Magasin → cet entrepôt,
   *  quantité proposée = ce qui manque pour revenir à la cible (sinon 1). */
  async transfererSelection() {
    const source = this.ctx.magasin;
    const items = (this.solde ? this.solde.articles : []).filter((a) => this.selSolde.has(a.item_code));
    if (!items.length) return;
    const cible = await this.choisirVehicule("🔁 Transférer depuis le Magasin vers…");
    if (!cible || cible === source) return;
    const qtes = (await frappe.call({ method: SE_API + "get_quantites", args: { items: items.map((a) => a.item_code), source, cible } })).message || {};
    this.$r.find("#se-t-de").val(source);
    this.$r.find("#se-t-vers").val(cible);
    this.panier = items.map((a) => ({ item_code: a.item_code, item_name: a.item_name, image: a.image, uom: a.uom,
      qte: a.a_transferer && a.a_transferer > 0 ? a.a_transferer : 1,
      qte_source: (qtes[a.item_code] || [0, 0])[0], qte_cible: (qtes[a.item_code] || [0, 0])[1] }));
    this.selSolde.clear();
    this.trouves = [];
    this.$r.find("#se-t-recherche").val("");
    this.montrer("transfert");
    this.peindreTrouves();
    this.peindrePanier();
    frappe.show_alert({ message: `${items.length} article(s) dans le panier — vérifiez les quantités puis validez`, indicator: "blue" }, 5);
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
    r.find("#se-t-de, #se-t-vers").on("change", () => { this.chercherArticles(); this.majQuantitesPanier(); this.majBoutonReassort(); });
    r.find("#se-t-reassort").on("click", () => this.reassortCible());
    r.find("#se-t-inverser").on("click", () => {
      const de = r.find("#se-t-de").val();
      r.find("#se-t-de").val(r.find("#se-t-vers").val());
      r.find("#se-t-vers").val(de);
      this.chercherArticles();
      this.majQuantitesPanier();
      this.majBoutonReassort();
    });
    let t = null;
    r.find("#se-t-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.chercherArticles(), 250); });
    r.find("#se-t-recherche").on("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
    r.find("#se-t-trouves").on("click", "[data-ajouter]", (e) => {
      const $b = $(e.currentTarget), $q = $b.closest(".se-z-ligne").find("input[data-qte-trouve]");
      this.ajouterAuPanier($b.attr("data-ajouter"), +String($q.val()).replace(",", ".") || 1);
    });
    r.find("#se-t-trouves").on("keydown", "input[data-qte-trouve]", (e) => {
      if (e.key === "Enter") { e.preventDefault(); $(e.currentTarget).closest(".se-z-ligne").find("[data-ajouter]").trigger("click"); }
    });
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
    r.find("#se-t-historique").on("click", "[data-detail]", (e) => {
      const t = (this.historique || []).find((x) => x.name === $(e.currentTarget).attr("data-detail"));
      if (t) this.ouvrirDetail(t);
    });
    r.find("#se-t-historique").on("click", "[data-valider]", (e) => this.ouvrirValidation($(e.currentTarget).attr("data-valider")));
    this.trouves = [];
    this.peindrePanier();
    this.majBoutonReassort();
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
        <input type="number" inputmode="decimal" min="0" step="any" class="form-control se-qte-trouve" data-qte-trouve value="1" title="Quantité à ajouter">
        <span class="se-bt ${dans.has(a.item_code) ? "fait" : ""}" data-ajouter="${se_esc(a.item_code)}" title="Ajouter">${dans.has(a.item_code) ? "✓" : "＋"}</span>
      </div>`).join("") || ((this.$r.find("#se-t-recherche").val() || "").trim().length >= 2 ? `<div class="se-vide">Aucun article suivi en stock ne correspond.</div>` : ""));
  }

  ajouterAuPanier(item, qte) {
    const a = this.trouves.find((x) => x.item_code === item);
    if (!a) return;
    const n = Math.max(qte > 0 ? qte : 1, 0.001);
    const l = this.panier.find((x) => x.item_code === item);
    if (l) l.qte = (+l.qte || 0) + n;
    else this.panier.push(Object.assign({}, a, { qte: n }));
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
        frappe.show_alert({ message: res.en_attente ? `⏳ Transfert ${se_esc(res.name)} : ${res.lignes} article(s) — en attente de la validation de ${se_esc(res.employe_nom)} (le stock bougera à sa confirmation)`
            : `✅ Transfert ${se_esc(res.name)} : ${res.lignes} article(s)`, indicator: res.en_attente ? "yellow" : "green" }, 8);
        this.panier = [];
        this.chargerAttente();
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
    this.historique = res;
    const moi = this.ctx.employe_id;
    this.$r.find("#se-t-historique").html(res.map((t) => `<div class="se-card se-tr ${t.docstatus === 2 ? "annule" : ""} ${t.attente ? `attente ${t.attente.age_h > 24 ? "vieux" : ""}` : ""}">
        <div class="tete"><span>${se_lien("Stock Entry", t.name)} ${t.docstatus === 2 ? `<span class="se-badge">annulé</span>` : ""}</span>
          <span class="se-note">${se_dt(t.date)} ${se_esc(t.heure)} · ${se_esc(t.par)}</span></div>
        <div class="sens">${se_esc(this.libelle(t.de || (t.lignes[0] || {}).de))} → ${se_esc(this.libelle(t.vers || (t.lignes[0] || {}).vers))}</div>
        ${t.attente ? `<div class="se-attente ${t.attente.age_h > 24 ? "vieux" : ""}">⏳ En attente de validation de ${se_esc(t.attente.employe_nom)}${t.attente.age_h > 24 ? ` · depuis ${Math.round(t.attente.age_h / 24)} j` : ""}</div>` : ""}
        ${t.validation ? `<div class="se-valide">✅ Réception confirmée par ${se_esc(t.validation.par)} le ${se_esc(t.validation.le)}${t.validation.ecart ? `<div class="ecart">⚠️ ${se_esc(t.validation.ecart)}</div>` : ""}</div>` : ""}
        <div class="det">${t.lignes.slice(0, 6).map((l) => `${se_esc(l.item_name || l.item_code)} <b>${se_q(l.qte)}</b>`).join(" · ")}${t.lignes.length > 6 ? ` · … +${t.lignes.length - 6}` : ""}</div>
        ${t.remarque ? `<div class="se-note">${se_esc(t.remarque)}</div>` : ""}
        <div class="actions"><button type="button" class="btn btn-xs btn-default" data-detail="${se_esc(t.name)}">🔍 Détail</button>
          ${t.attente && t.attente.employe === moi ? `<button type="button" class="btn btn-xs btn-primary" data-valider="${se_esc(t.name)}">✅ Confirmer la réception</button>` : ""}
          ${t.docstatus === 1 ? `<button type="button" class="btn btn-xs btn-default" data-annuler="${se_esc(t.name)}">Annuler ce transfert</button>` : ""}
          ${t.attente ? `<button type="button" class="btn btn-xs btn-default" data-annuler="${se_esc(t.name)}">Annuler la demande</button>` : ""}</div>
      </div>`).join("") || `<div class="se-vide">Aucun transfert.</div>`);
  }

  annulerTransfert(name) {
    const t = (this.historique || []).find((x) => x.name === name);
    const texte = t && t.attente ? `Retirer la demande <b>${se_esc(name)}</b> en attente de ${se_esc(t.attente.employe_nom)} ? Rien n’a bougé en stock.`
      : `Annuler le transfert <b>${se_esc(name)}</b> ? Les quantités reviennent à leur place.`;
    frappe.confirm(texte, async () => {
      await frappe.call({ method: SE_API + "annuler_transfert", args: { name }, freeze: true });
      frappe.show_alert({ message: `Transfert ${se_esc(name)} annulé`, indicator: "orange" }, 5);
      this.chargerHistorique();
      this.chargerAttente();
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

  // ── Vérification des stocks des employés ─────────────────────────────────
  async chargerVerif() {
    this.comptage = null;
    const res = await this.appel("verif", "verifications_etat", {});
    if (!res) return;
    const $c = this.$r.find("#se-v-contenu");
    const hist = (s) => s.dernieres.map((v) => `<div class="se-v-h" data-detail="${se_esc(v.name)}">
        <span class="d">${se_dt(v.termine_le)}</span>
        <span class="n">${v.nb_comptes}/${v.nb_lignes} comptés · <b class="${v.nb_ecarts ? "neg" : "pos"}">${v.nb_ecarts} écart(s)</b></span>
        <span class="v ${v.valeur_ecarts < 0 ? "neg" : v.valeur_ecarts > 0 ? "pos" : ""}">${v.nb_ecarts ? se_argent(v.valeur_ecarts) : "✓"}</span>
        <span class="fleche">▶</span></div><div class="se-v-det" style="display:none" data-det="${se_esc(v.name)}"></div>`).join("");
    $c.html(`<div class="se-note" style="margin:0 2px 10px">Chaque semaine, le jour fixé, une fiche de comptage est ouverte et deux tâches créées :
        l’employé du stock et ${res.responsable_nom ? `<b>${se_esc(res.responsable_nom)}</b>` : "le responsable magasin"}.
        L’employé compte et termine ; un <b>autre responsable magasin</b> vérifie et valide ses quantités ; l’employé les accepte
        (s’il en change une, le responsable revalide — il a le dernier mot) ; un <b>rapprochement de stock</b> aligne alors le véhicule.
        ${res.peut_planifier ? `<a class="se-lien" href="/app/config-stock-entrepot">⚙️ Réglages</a> · <span class="se-lien" data-planifier>▶ Ouvrir les vérifications du jour</span>` : ""}</div>`
      + (() => { const att = res.stocks.filter((s) => s.en_cours && s.en_cours.statut !== "En cours" && s.en_cours.actions.length);
          return att.length ? `<div class="se-bandeau"><div class="t">🧾 ${att.length} vérification(s) attendent votre ${att.some((s) => s.en_cours.actions.includes("valider")) ? "validation" : "confirmation"}</div>
            <div class="d">${att.map((s) => se_esc(s.libelle)).join(" · ")}</div></div>` : ""; })()
      + (res.stocks.map((s) => `<div class="se-card se-v-stock">
          <div class="t">🚐 ${se_esc(s.libelle)}</div>
          <div class="plan">👤 ${se_esc(s.employe)} · ${s.actif ? `chaque <b>${se_esc(s.jour)}</b> à ${se_esc(s.heure)} · prochaine le ${se_dt(s.prochaine)}` : `<span style="color:#c2410c">pas de jour fixé</span>`}</div>
          ${s.en_cours ? this.blocVerif(s) : `<button type="button" class="btn btn-default" data-commencer="${se_esc(s.entrepot)}">Compter maintenant</button>`}
          <div class="se-v-hist">${hist(s) || `<div class="se-note">Aucune vérification terminée.</div>`}</div>
        </div>`).join("") || `<div class="se-vide">Aucun stock d’employé.</div>`));
    $c.find("[data-planifier]").on("click", async () => {
      const r = (await frappe.call({ method: SE_API + "planifier_maintenant", freeze: true })).message || [];
      frappe.show_alert({ message: r.length ? `${r.length} vérification(s) ouverte(s) : ${r.map(se_esc).join(", ")}` : "Rien à ouvrir aujourd’hui (jour non prévu, ou déjà ouverte)", indicator: r.length ? "green" : "orange" }, 6);
      this.chargerVerif();
    });
    $c.find("[data-commencer]").on("click", async (e) => {
      const name = (await frappe.call({ method: SE_API + "commencer_verification", args: { entrepot: $(e.currentTarget).attr("data-commencer") }, freeze: true })).message;
      this.ouvrirComptage(name);
    });
    $c.find("[data-reprendre]").on("click", (e) => this.ouvrirComptage($(e.currentTarget).attr("data-reprendre")));
    $c.find("[data-detail]").on("click", async (e) => {
      const name = $(e.currentTarget).attr("data-detail"), $d = $c.find(`[data-det="${CSS.escape(name)}"]`);
      if ($d.is(":visible")) { $d.hide(); return; }
      const d = (await frappe.call({ method: SE_API + "detail_verification", args: { name } })).message;
      const ec = d.lignes.filter((l) => l.ecart != null && Math.abs(l.ecart) > 1e-6);
      $d.html((ec.map((l) => `<div><b class="${l.ecart < 0 ? "neg" : "pos"}">${se_signe(l.ecart)}</b> ${se_esc(l.item_name || l.item_code)}
          <span class="se-note">(système ${se_q(l.qte_systeme)}, compté ${se_q(l.qte_comptee)}, ${se_argent(l.valeur_ecart)})${l.commentaire ? ` — ${se_esc(l.commentaire)}` : ""}</span></div>`).join("")
        || `<div class="pos">Aucun écart.</div>`) + `<div class="se-note">${se_lien("Verification Stock", d.fiche.name)}${d.fiche.tache_employe ? ` · ${se_lien("Tache de travail", d.fiche.tache_employe, "tâche employé")}` : ""}
          ${d.fiche.valide_employe_par ? ` · comptage ${se_esc(d.fiche.valide_employe_par)}` : ""}${d.fiche.valide_responsable_par ? ` · validé ${se_esc(d.fiche.valide_responsable_par)}` : ""}
          ${d.fiche.rapprochement ? ` · ${se_lien("Stock Reconciliation", d.fiche.rapprochement, "rapprochement " + d.fiche.rapprochement)}` : " · sans rapprochement (aucun écart)"}</div>`).show();
    });
  }

  /** La fiche ouverte d'un stock, selon son statut et ce que l'utilisateur peut y faire. */
  blocVerif(s) {
    const v = s.en_cours, a = v.actions || [];
    if (v.statut === "En cours") return v.photo
      ? `<div class="plan">📝 Comptage en cours du ${se_dt(v.date)} : ${v.nb_comptes}/${v.nb_lignes} comptés${v.renvois ? ` · renvoyé ${v.renvois}×` : ""}${v.nb_a_recompter ? ` · <b class="neg">${v.nb_a_recompter} à recompter</b>` : ""}</div>
         <button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">Reprendre le comptage</button>`
      : `<div class="plan">📅 Vérification ${v.prevue ? "prévue le" : "du"} <b>${se_dt(v.date)}</b>${v.tache_employe ? " · tâches créées" : ""}</div>
         <button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">Commencer le comptage</button>`;
    if (v.statut === "À valider") return `<div class="plan">📝 Comptage du ${se_dt(v.date)} ${v.renvois ? "revu" : "terminé"} par ${se_esc(v.valide_employe_par)} : <b class="${v.nb_ecarts ? "neg" : "pos"}">${v.nb_ecarts} écart(s)</b>${v.nb_ecarts ? ` · ${se_argent(v.valeur_ecarts)}` : ""}</div>
         ${a.includes("valider") ? `<button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">🔍 Vérifier et valider</button>`
           : `<div class="se-attente">⏳ En attente de validation d’un responsable magasin</div>`}`;
    if (v.statut === "À confirmer") return `<div class="plan">📝 Validé par ${se_esc(v.valide_responsable_par)}${v.nb_ajustes ? ` · <b class="neg">${v.nb_ajustes} quantité(s) ajustée(s)</b>` : " · sans changement"}</div>
         ${a.includes("confirmer") ? `<button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">🔍 Voir et confirmer</button>`
           : `<div class="se-attente">⏳ En attente de la confirmation de ${se_esc(s.employe)}</div>`}`;
    return "";
  }

  /** Feuille d'une vérification : comptage (employé), validation / ajustement (responsable), confirmation (employé). */
  async ouvrirComptage(name) {
    this.comptage = name;
    const d = (await frappe.call({ method: SE_API + "detail_verification", args: { name } })).message;
    const $c = this.$r.find("#se-v-contenu"), statut = d.fiche.statut, a = d.fiche.actions || [];
    const mode = statut === "En cours" ? "comptage" : statut === "À valider" && a.includes("valider") ? "validation"
      : statut === "À confirmer" && a.includes("confirmer") ? "confirmation" : "lecture";
    const fini = mode === "lecture";
    const etat = { "À valider": "⏳ à valider", "À confirmer": "⏳ à confirmer", "Terminée": "terminée" }[statut];
    const saisies = {};
    d.lignes.forEach((l) => { saisies[l.item_code] = { qte_comptee: l.qte_comptee, commentaire: l.commentaire || "" }; });
    const ligne = (l) => `<div class="se-c-ligne ${l.qte_comptee != null ? "fait" : ""}" data-item="${se_esc(l.item_code)}" data-cle="${se_esc(((l.item_name || "") + " " + l.item_code).toLowerCase())}">
        ${se_img(l.image)}
        <div class="txt"><div class="se-nom" style="font-size:13.5px">${se_esc(l.item_name || l.item_code)}</div>
          <div class="se-code">${se_esc(l.item_code)}${l.zones ? ` · 📍 ${se_esc(l.zones)}` : ""}</div>${l.a_recompter ? `<div class="se-alerte">⚠️ à recompter — ${se_esc(l.commentaire || "stock modifié")}</div>` : ""}</div>
        <div class="sys">${se_q(l.qte_systeme)}${l.ajuste ? `<small style="display:block;color:#b45309;font-size:10.5px">employé : ${se_q(l.qte_employe)}</small>` : ""}</div>
        <input type="number" inputmode="decimal" step="any" class="form-control q" ${fini ? "disabled" : ""} style="${l.ajuste ? "border-color:#f59e0b;background:#fffbeb" : ""}" value="${l.qte_comptee == null ? "" : se_esc(l.qte_comptee)}" placeholder="?">
        <div class="ec ${l.ecart ? (l.ecart < 0 ? "neg" : "pos") : ""}">${l.ecart == null ? "" : se_signe(l.ecart)}</div>
      </div>`;
    $c.html(`<span class="se-retour" data-retour>← Vérifications</span>
      <div class="se-card">
        <div class="se-v-stock"><div class="t">📝 ${se_esc(this.libelle(d.fiche.entrepot))} — ${se_dt(d.fiche.date)} ${etat ? `<span class="se-badge">${etat}</span>` : ""}</div>
          ${mode === "comptage" ? `<div class="se-note">Quantités système photographiées à la première ouverture de cette feuille.</div>`
            : mode === "validation" ? `<div class="se-note">Comptage de <b>${se_esc(d.fiche.valide_employe_par)}</b> (${se_esc(d.fiche.valide_employe_le)}). Validez tel quel, corrigez une quantité (l’employé devra confirmer), ou renvoyez au comptage.<br>Quantités système actualisées à l’instant : un article qui a bougé depuis le comptage serait à recompter.</div>`
            : mode === "confirmation" ? `<div class="se-note">Quantités validées par <b>${se_esc(d.fiche.valide_responsable_par)}</b>${d.fiche.nb_ajustes ? " (ajustées : surlignées, votre comptage en dessous)" : ""}. D’accord : validez. Sinon corrigez la quantité : la fiche repart chez le responsable, qui a le dernier mot.</div>`
            : `<div class="se-note">${d.fiche.valide_employe_par ? `Comptage ${se_esc(d.fiche.valide_employe_par)}` : ""}${d.fiche.valide_responsable_par ? ` · validé par ${se_esc(d.fiche.valide_responsable_par)}` : ""}${d.fiche.rapprochement ? ` · ${se_lien("Stock Reconciliation", d.fiche.rapprochement, "rapprochement " + d.fiche.rapprochement)}` : ""}</div>`}
          ${d.fiche.nb_a_recompter ? `<div class="se-alerte">⚠️ ${d.fiche.nb_a_recompter} article(s) ont bougé depuis le comptage : leur comptage est effacé, à recompter (lignes marquées).</div>` : ""}
          ${d.fiche.note ? `<div class="se-note">${se_esc(d.fiche.note)}</div>` : ""}
          <div class="plan" id="se-c-bilan"></div></div>
        <input type="search" class="form-control se-saisie" id="se-c-filtre" placeholder="🔎 Filtrer un article…" style="margin-top:8px">
        <div class="se-c-tete"><span class="txt">Article</span><span class="sys">Système</span><span class="q">Compté</span><span class="ec">Écart</span></div>
        <div id="se-c-lignes">${d.lignes.map(ligne).join("")}</div>
        ${mode === "comptage" ? `<div class="se-c-barre"><button type="button" class="btn btn-default" data-enregistrer>💾 Enregistrer</button>
          <button type="button" class="btn btn-primary" data-terminer>✅ Terminer le comptage</button></div>`
          : mode === "validation" ? `<div class="se-c-barre"><button type="button" class="btn btn-default" data-renvoyer>↩ Renvoyer au comptage</button>
          <button type="button" class="btn btn-primary" data-valider>✅ Valider et rapprocher</button></div>`
          : mode === "confirmation" ? `<div class="se-c-barre"><button type="button" class="btn btn-default" data-renvoyer>🔁 Tout recompter</button>
          <button type="button" class="btn btn-primary" data-confirmer>✅ Valider</button></div>` : ""}
      </div>`);
    const bilan = () => {
      let n = 0, e = 0, v = 0;
      d.lignes.forEach((l) => { const s = saisies[l.item_code]; if (s.qte_comptee == null || s.qte_comptee === "") return; n++;
        const ec = Math.round((+s.qte_comptee - l.qte_systeme) * 1e6) / 1e6; if (ec) { e++; v += ec * l.taux; } });
      $c.find("#se-c-bilan").html(`${n}/${d.lignes.length} comptés · <b class="${e ? "neg" : "pos"}">${e} écart(s)</b>${e ? ` · ${se_argent(v)}` : ""}`);
    };
    bilan();
    $c.find("[data-retour]").on("click", () => this.chargerVerif());
    $c.find("#se-c-filtre").on("input", (e) => { const q = (e.currentTarget.value || "").toLowerCase().trim();
      $c.find(".se-c-ligne").each((_, el) => $(el).toggle(!q || ($(el).attr("data-cle") || "").includes(q))); });
    $c.find("input.q").on("input", (e) => {
      const $l = $(e.currentTarget).closest(".se-c-ligne"), code = $l.attr("data-item"), l = d.lignes.find((x) => x.item_code === code);
      const v = String(e.currentTarget.value).replace(",", ".");
      saisies[code].qte_comptee = v === "" ? null : +v;
      const ec = v === "" ? null : Math.round((+v - l.qte_systeme) * 1e6) / 1e6;
      $l.toggleClass("fait", v !== "").find(".ec").text(ec == null ? "" : se_signe(ec)).attr("class", "ec " + (ec ? (ec < 0 ? "neg" : "pos") : ""));
      bilan();
    });
    const comptes = () => JSON.stringify(saisies);
    $c.find("[data-enregistrer]").on("click", async () => {
      await frappe.call({ method: SE_API + "enregistrer_verification", args: { name, comptes: comptes() }, freeze: true });
      frappe.show_alert({ message: "Comptage enregistré — vous pourrez reprendre", indicator: "green" }, 3);
    });
    const retour = () => { this.comptage = null; this.chargerVerif(); };
    $c.find("[data-terminer]").on("click", () => {
      const n = Object.values(saisies).filter((s) => s.qte_comptee != null && s.qte_comptee !== "").length;
      frappe.confirm(`Terminer le comptage avec <b>${n}</b> article(s) compté(s) sur ${d.lignes.length} ?<br>Les écarts seront figés et soumis à la validation d’un responsable magasin ; le rapprochement de stock se fera à sa validation.`, async () => {
        const r = (await frappe.call({ method: SE_API + "terminer_verification", args: { name, comptes: comptes() }, freeze: true })).message;
        frappe.msgprint({ title: "Comptage terminé — à valider", indicator: r.nb_ecarts ? "orange" : "green",
          message: `${r.nb_comptes} article(s) comptés, <b>${r.nb_ecarts} écart(s)</b>${r.nb_ecarts ? ` · valeur nette ${se_esc(se_argent(r.valeur_ecarts))} · manquants ${se_esc(se_argent(r.valeur_manquants))}` : ""}<br>Un responsable magasin doit maintenant valider.` });
        retour();
      });
    });
    $c.find("[data-valider]").on("click", () => {
      const changes = d.lignes.filter((l) => { const s = saisies[l.item_code]; const q = s.qte_comptee === "" ? null : s.qte_comptee; return (q == null) !== (l.qte_comptee == null) || (q != null && Math.abs(q - l.qte_comptee) > 1e-6); }).length;
      const e = Object.values(saisies).filter((s) => s.qte_comptee != null && s.qte_comptee !== "").length;
      const ecarts = d.lignes.filter((l) => { const s = saisies[l.item_code]; return s.qte_comptee != null && s.qte_comptee !== "" && Math.abs(+s.qte_comptee - l.qte_systeme) > 1e-6; }).length;
      frappe.confirm(`Valider ${changes ? `avec <b>${changes}</b> quantité(s) corrigée(s)` : `le comptage (${e} article(s))`} ?<br>L’employé devra accepter ces quantités ; ${ecarts ? `un <b>rapprochement de stock</b> alignera alors ${ecarts} article(s).` : "aucun écart : pas de rapprochement."}`, async () => {
        const r = (await frappe.call({ method: SE_API + "valider_verification", args: { name, comptes: changes ? comptes() : null }, freeze: true })).message;
        if (r.renvoye) { frappe.msgprint({ title: "À recompter", indicator: "orange", message: `${r.a_recompter.length} article(s) ont bougé depuis le comptage : leur comptage est effacé et la fiche repart chez l’employé.<br>${r.a_recompter.map(se_esc).join(", ")}` }); retour(); return; }
        frappe.msgprint({ title: "Validé — à confirmer par l’employé", indicator: "green",
          message: `${r.ajustes.length ? r.ajustes.map(se_esc).join("<br>") + "<br>" : "Aucune quantité changée.<br>"}L’employé du stock doit maintenant accepter ces quantités ; le rapprochement sera passé à son accord.` });
        retour();
      });
    });
    $c.find("[data-confirmer]").on("click", () => {
      const changes = d.lignes.filter((l) => { const s = saisies[l.item_code]; const q = s.qte_comptee === "" ? null : s.qte_comptee; return (q == null) !== (l.qte_comptee == null) || (q != null && Math.abs(q - l.qte_comptee) > 1e-6); }).length;
      frappe.confirm(changes
        ? `Vous avez changé <b>${changes}</b> quantité(s) : la fiche repart chez le responsable magasin pour revalidation. Continuer ?`
        : `D’accord avec les quantités validées ? Le rapprochement de stock sera passé sur votre stock.`, async () => {
        const r = (await frappe.call({ method: SE_API + "confirmer_verification", args: { name, comptes: changes ? comptes() : null }, freeze: true })).message;
        if (r.renvoye) { frappe.msgprint({ title: "À recompter", indicator: "orange", message: `${r.a_recompter.length} article(s) ont bougé depuis votre comptage : recomptez-les (lignes marquées) puis terminez à nouveau.` }); retour(); return; }
        if (r.statut === "À valider") { frappe.msgprint({ title: "Renvoyé au responsable", indicator: "orange", message: `${r.changes.map(se_esc).join("<br>")}<br>Le responsable magasin doit revalider.` }); retour(); return; }
        frappe.msgprint({ title: "Vérification terminée", indicator: "green", message: r.rapprochement ? `Validation mutuelle acquise. Rapprochement ${se_lien("Stock Reconciliation", r.rapprochement)} passé.` : "Validation mutuelle acquise. Aucun écart : pas de rapprochement." });
        retour();
      });
    });
    $c.find("[data-renvoyer]").on("click", () => {
      frappe.prompt({ fieldtype: "Small Text", fieldname: "motif", label: "Motif (facultatif)" }, async (v) => {
        await frappe.call({ method: SE_API + "renvoyer_verification", args: { name, motif: v.motif || null }, freeze: true });
        frappe.show_alert({ message: "Fiche renvoyée au comptage", indicator: "orange" }, 4);
        retour();
      }, mode === "confirmation" ? "Recompter" : "Renvoyer au comptage", "Confirmer");
    });
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
