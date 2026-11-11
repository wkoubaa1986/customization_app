/**
 * « Nouvelle liste de prix » — l'assistant qui remplace la saisie du formulaire « Liste prix documents » :
 * 1) pour qui (client facultatif, liste de prix), 2) quoi (recherche ou famille entière), 3) créer.
 * Les prix, remises et paliers viennent du serveur (customization_app.liste_prix_assistant) ; l'écran ne
 * garde que les codes choisis et leurs quantités (repris après un rechargement de la page).
 */
frappe.pages["liste-prix-assistant"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Nouvelle liste de prix", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("liste_prix_assistant", {}));
  wrapper.lpa = new ListePrixAssistant(wrapper);
};
frappe.pages["liste-prix-assistant"].on_page_show = function (wrapper) {
  if (wrapper.lpa) wrapper.lpa.depuis_route();
};

const LPA_API = "customization_app.liste_prix_assistant";
const LPA_BROUILLON = "lpa_brouillon";
const LPA_AFFICHES_MAX = 150;      // au-delà, la famille s'ajoute en bloc
const lpa_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const lpa_dt = (v) => (v == null ? "—"
  : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT");
const lpa_img = (src) => (src ? `<img src="${lpa_esc(src)}" loading="lazy" alt="">` : `<div class="sansimg"></div>`);

class ListePrixAssistant {
  constructor(wrapper) {
    this.page = wrapper.page;
    this.$root = $(wrapper).find(".lpa-page");
    this.ctx = null;
    this.client = null;
    this.liste = null;
    this.sel = new Map();            // code -> quantité, dans l'ordre d'ajout
    this.lignes = [];                // dernier aperçu du serveur
    this.vue = { type: "familles" }; // familles | recherche | famille
    this.resultats = [];
    this.jeton = 0;                  // ignore les réponses périmées
    this.jeton_apercu = 0;

    this.ctrl_client = frappe.ui.form.make_control({
      df: { fieldtype: "Link", options: "Customer", fieldname: "client", label: "Client",
            placeholder: "Aucun client (liste générale)", change: () => this.client_change() },
      parent: this.$root.find("#lpa-client"), render_input: true,
    });
    this.page.add_inner_button("Toutes les listes", () => frappe.set_route("List", "Liste prix documents"));

    const $q = this.$root.find("#lpa-q");
    $q.on("input", frappe.utils.debounce(() => this.chercher(), 350));
    $q.on("keydown", (e) => { if (e.key === "Enter") this.chercher(); });
    this.$root.find("#lpa-q-x").on("click", () => { $q.val(""); this.chercher(); $q.trigger("focus"); });
    this.$root.on("click", ".lpa-chip", (e) => this.choisir_liste($(e.currentTarget).data("liste")));
    this.$root.on("click", ".lpa-fam .nom", (e) => this.ouvrir_famille($(e.currentTarget).closest(".lpa-fam").data("famille")));
    this.$root.on("click", ".lpa-fam .lpa-tout", (e) => this.ajouter_famille($(e.currentTarget).closest(".lpa-fam").data("famille")));
    this.$root.on("click", "#lpa-retour", () => { $q.val(""); this.chercher(); });
    this.$root.on("click", "#lpa-tout-vue", () => this.ajouter_codes(this.codes_vue()));
    this.$root.on("click", ".lpa-plus[data-code]", (e) => this.basculer(String($(e.currentTarget).data("code"))));
    this.$root.on("click", ".lpa-plus[data-modele]", (e) => {
      const m = this.resultats.find((r) => r.item_code === String($(e.currentTarget).data("modele")));
      if (m) this.ajouter_codes((m.variantes || []).filter((v) => v.prix_ttc != null).map((v) => v.item_code));
    });
    this.$root.on("click", ".lpa-suppr[data-code]", (e) => this.retirer([String($(e.currentTarget).data("code"))]));
    this.$root.on("click", ".lpa-suppr[data-groupe]", (e) => {
      const g = String($(e.currentTarget).data("groupe"));
      this.retirer(this.lignes.filter((l) => l.item_group === g).map((l) => l.item_code));
    });
    this.$root.on("change", ".lpa-qte", (e) => {
      const code = String($(e.currentTarget).data("code"));
      const q = Math.max(1, parseInt($(e.currentTarget).val(), 10) || 1);
      if (this.sel.has(code)) { this.sel.set(code, q); this.maj_selection(); }
    });
    this.$root.find("#lpa-vider").on("click", () => {
      if (!this.sel.size) return;
      frappe.confirm("Vider la liste en cours ?", () => { this.sel.clear(); this.maj_selection(); this.marquer_ajoutes(); });
    });
    this.$root.find("#lpa-creer").on("click", () => this.creer());

    frappe.call({ method: LPA_API + ".contexte" }).then((r) => {
      this.ctx = r.message;
      this.reprendre_brouillon();
      if (!this.liste) this.liste = this.ctx.listes.includes(this.ctx.defaut) ? this.ctx.defaut : this.ctx.listes[0];
      this.rendre_listes();
      this.rendre_gauche();
      this.depuis_route();
      this.maj_selection();
    });
  }

  // ── 1. Pour qui ───────────────────────────────────────────────────────────
  /** Lien depuis un autre écran : frappe.set_route("liste-prix-assistant", {client: "…"}). */
  depuis_route() {
    const o = frappe.route_options;
    if (!this.ctx || !o || !o.client) return;
    frappe.route_options = null;
    this.ctrl_client.set_value(o.client);
  }

  client_change() {
    const c = this.ctrl_client.get_value() || null;
    if (c === this.client) return;
    this.client = c;
    const $info = this.$root.find("#lpa-info-client");
    if (!c) {
      $info.text("Facultatif. Avec un client, sa liste de prix est choisie toute seule.");
      this.sauver();
      return;
    }
    frappe.call({ method: LPA_API + ".liste_du_client", args: { client: c } }).then((r) => {
      if (this.client !== c) return;
      const m = r.message;
      $info.html(`${lpa_esc(m.nom)} — sa liste de prix : <b>${lpa_esc(m.liste)}</b>`);
      this.choisir_liste(m.liste);
    });
  }

  choisir_liste(liste) {
    if (!liste || liste === this.liste) { this.sauver(); return; }
    this.liste = liste;
    this.rendre_listes();
    this.sauver();
    // Les prix affichés sont ceux de la liste : on relit la vue en cours et la sélection.
    if (this.vue.type === "recherche") this.chercher();
    else if (this.vue.type === "famille") this.ouvrir_famille(this.vue.famille);
    this.maj_selection();
  }

  rendre_listes() {
    this.$root.find("#lpa-listes").html((this.ctx.listes || []).map((l) =>
      `<span class="lpa-chip ${l === this.liste ? "actif" : ""}" data-liste="${lpa_esc(l)}">${lpa_esc(l)}</span>`).join(""));
  }

  // ── 2. Ajouter ────────────────────────────────────────────────────────────
  chercher() {
    const q = (this.$root.find("#lpa-q").val() || "").trim();
    this.$root.find("#lpa-q-x").toggle(!!q);
    const jeton = ++this.jeton;
    if (q.length < 2) { this.vue = { type: "familles" }; this.resultats = []; this.rendre_gauche(); return; }
    this.vue = { type: "recherche", q };
    this.$root.find("#lpa-gauche").html(`<div class="lpa-vide">Recherche de « ${lpa_esc(q)} »…</div>`);
    frappe.call({ method: LPA_API + ".rechercher", args: { query: q, price_list: this.liste } }).then((r) => {
      if (jeton !== this.jeton) return;
      this.resultats = (r.message || {}).results || [];
      this.rendre_gauche();
    });
  }

  ouvrir_famille(famille) {
    const jeton = ++this.jeton;
    this.vue = { type: "famille", famille };
    this.$root.find("#lpa-gauche").html(`<div class="lpa-vide">Lecture de la famille « ${lpa_esc(famille)} »…</div>`);
    return frappe.call({ method: LPA_API + ".famille", args: { item_group: famille, price_list: this.liste } }).then((r) => {
      if (jeton !== this.jeton) return null;
      this.resultats = r.message.results || [];
      this.vue.sans_prix = r.message.sans_prix || 0;
      this.rendre_gauche();
      return r.message;
    });
  }

  /** « ＋ Tout » depuis l'arbre : ajoute la famille entière sans l'ouvrir. */
  ajouter_famille(famille) {
    frappe.call({ method: LPA_API + ".famille", args: { item_group: famille, price_list: this.liste },
                  freeze: true, freeze_message: "Lecture de la famille…" }).then((r) => {
      const codes = (r.message.results || []).map((f) => f.item_code);
      if (!codes.length) {
        frappe.show_alert({ message: `Aucun article de « ${famille} » n’a de prix dans « ${this.liste} ».`, indicator: "orange" });
        return;
      }
      this.ajouter_codes(codes);
    });
  }

  /** Les codes ajoutables de la vue (les variantes d'un modèle, jamais le modèle lui-même). */
  codes_vue() {
    const out = [];
    this.resultats.forEach((r) => {
      if (r.est_modele) (r.variantes || []).forEach((v) => { if (v.prix_ttc != null) out.push(v.item_code); });
      else if (r.prix_ttc != null) out.push(r.item_code);
    });
    return out;
  }

  rendre_gauche() {
    const $g = this.$root.find("#lpa-gauche");
    if (this.vue.type === "familles") {
      const fams = (this.ctx.familles || []).map((f) => `
        <div class="lpa-fam n${f.niveau}" data-famille="${lpa_esc(f.name)}" style="padding-left:${6 + f.niveau * 18}px">
          <span class="nom" title="Voir les articles">${lpa_esc(f.name)}</span>
          <span class="nb">${f.nb}</span>
          <button class="btn btn-xs btn-default lpa-tout" title="Ajouter toute la famille">＋ Tout</button>
        </div>`).join("");
      $g.html(`<div class="lpa-entete-vue"><b>Familles</b>
          <span class="lpa-muted">— cliquez un nom pour choisir article par article, ou « ＋ Tout » pour la famille entière</span></div>
        <div class="lpa-defile">${fams}</div>`);
      return;
    }
    const codes = this.codes_vue();
    const titre = this.vue.type === "famille"
      ? `<b>${lpa_esc(this.vue.famille)}</b> <span class="lpa-muted">${codes.length} article(s)${
          this.vue.sans_prix ? ` · ${this.vue.sans_prix} sans prix dans « ${lpa_esc(this.liste)} », non proposé(s)` : ""}</span>`
      : `<b>${this.resultats.length} résultat(s)</b> <span class="lpa-muted">pour « ${lpa_esc(this.vue.q)} »</span>`;
    const entete = `<div class="lpa-entete-vue">
        <button class="btn btn-xs btn-default" id="lpa-retour">← Familles</button> ${titre}
        ${codes.length > 1 ? `<button class="btn btn-xs btn-primary" id="lpa-tout-vue" style="margin-left:auto">＋ Tout ajouter (${codes.length})</button>` : ""}
      </div>`;
    if (!this.resultats.length) { $g.html(entete + `<div class="lpa-vide">Aucun article trouvé.</div>`); return; }
    const visibles = this.resultats.slice(0, LPA_AFFICHES_MAX);
    const reste = this.resultats.length - visibles.length;
    $g.html(entete + `<div class="lpa-defile">${visibles.map((r) => this.html_resultat(r)).join("")}
      ${reste > 0 ? `<div class="lpa-vide">… et ${reste} autre(s) : utilisez « ＋ Tout ajouter », ou la recherche pour en trouver un.</div>` : ""}</div>`);
    this.marquer_ajoutes();
  }

  html_prix(f) {
    if (f.prix_ttc == null) return `<span class="lpa-muted">pas de prix</span>`;
    const rem = f.remise_pct > 0 && f.prix_standard_ttc
      ? `<br><s>${lpa_dt(f.prix_standard_ttc)}</s> <span class="rem">-${f.remise_pct} %</span>` : "";
    return `${lpa_dt(f.prix_ttc)}${rem}`;
  }

  html_article(f, sous) {
    const libelle = sous && f.attributs ? Object.values(f.attributs).join(" · ") || f.item_name : f.item_name;
    return `<div class="lpa-art ${sous ? "sous" : ""}">
        ${sous ? "" : lpa_img(f.image)}
        <div class="txt"><div class="nom">${lpa_esc(libelle)}</div>
          <div class="code">${lpa_esc(f.item_code)}${f.brand ? " · " + lpa_esc(f.brand) : ""}${
            !sous && f.item_group && this.vue.type !== "famille" ? " · " + lpa_esc(f.item_group) : ""}</div></div>
        <div class="prix">${this.html_prix(f)}</div>
        ${f.prix_ttc == null ? `<span class="lpa-plus"></span>`
          : `<button class="btn btn-sm btn-default lpa-plus" data-code="${lpa_esc(f.item_code)}">＋ Ajouter</button>`}
      </div>`;
  }

  html_resultat(r) {
    if (!r.est_modele) return this.html_article(r, false);
    const prix = r.prix_min == null ? `<span class="lpa-muted">pas de prix</span>`
      : (r.prix_min === r.prix_max ? lpa_dt(r.prix_min) : `${lpa_dt(r.prix_min)} – ${lpa_dt(r.prix_max)}`);
    return `<div class="lpa-modele">
        <div class="lpa-art">${lpa_img(r.image)}
          <div class="txt"><div class="nom">${lpa_esc(r.item_name)}</div>
            <div class="code">${lpa_esc(r.item_code)} · ${r.nb_variantes} variante(s)</div></div>
          <div class="prix">${prix}</div>
          <button class="btn btn-sm btn-default lpa-plus" data-modele="${lpa_esc(r.item_code)}">＋ Toutes</button>
        </div>
        ${(r.variantes || []).map((v) => this.html_article(v, true)).join("")}
      </div>`;
  }

  marquer_ajoutes() {
    this.$root.find(".lpa-plus[data-code]").each((_, el) => {
      const dedans = this.sel.has(String($(el).data("code")));
      $(el).toggleClass("ajoute", dedans).text(dedans ? "✓ Ajouté" : "＋ Ajouter");
    });
  }

  // ── 3. La liste ───────────────────────────────────────────────────────────
  basculer(code) {
    if (this.sel.has(code)) this.sel.delete(code); else if (!this.plein(1)) this.sel.set(code, 1);
    this.marquer_ajoutes();
    this.maj_selection();
  }

  ajouter_codes(codes) {
    const nouveaux = codes.filter((c) => !this.sel.has(c));
    if (!nouveaux.length) { frappe.show_alert({ message: "Ces articles sont déjà dans la liste.", indicator: "blue" }); return; }
    if (this.plein(nouveaux.length)) return;
    nouveaux.forEach((c) => this.sel.set(c, 1));
    frappe.show_alert({ message: `${nouveaux.length} article(s) ajouté(s).`, indicator: "green" });
    this.marquer_ajoutes();
    this.maj_selection();
  }

  plein(n) {
    const max = (this.ctx || {}).maximum || 1500;
    if (this.sel.size + n <= max) return false;
    frappe.msgprint(`Une liste ne peut pas dépasser ${max} articles.`);
    return true;
  }

  retirer(codes) {
    codes.forEach((c) => this.sel.delete(c));
    this.marquer_ajoutes();
    this.maj_selection();
  }

  articles() {
    return Array.from(this.sel, ([item_code, qte]) => ({ item_code, qte }));
  }

  sauver() {
    try {
      localStorage.setItem(LPA_BROUILLON, JSON.stringify({ client: this.client, liste: this.liste, articles: this.articles() }));
    } catch (e) { /* stockage indisponible : la liste vit le temps de la page */ }
  }

  reprendre_brouillon() {
    let b = null;
    try { b = JSON.parse(localStorage.getItem(LPA_BROUILLON) || "null"); } catch (e) { b = null; }
    if (!b) return;
    if (b.liste && this.ctx.listes.includes(b.liste)) this.liste = b.liste;
    (b.articles || []).forEach((a) => { if (a && a.item_code) this.sel.set(String(a.item_code), Math.max(1, parseInt(a.qte, 10) || 1)); });
    if (b.client) this.ctrl_client.set_value(b.client);   // relit aussi la liste de prix du client
  }

  maj_selection() {
    this.sauver();
    const jeton = ++this.jeton_apercu;
    if (!this.sel.size) { this.lignes = []; this.rendre_selection({ lignes: [], total: 0, sans_prix: [], introuvables: [] }); return; }
    frappe.call({ method: LPA_API + ".apercu", args: { articles: JSON.stringify(this.articles()), price_list: this.liste } })
      .then((r) => { if (jeton === this.jeton_apercu) { this.lignes = r.message.lignes; this.rendre_selection(r.message); } });
  }

  rendre_selection(a) {
    const n = a.lignes.length;
    this.$root.find("#lpa-titre-liste").html(`Votre liste <span class="lpa-muted">— ${n} article(s) · ${lpa_esc(this.liste || "")}</span>`);
    this.$root.find("#lpa-creer").prop("disabled", !n);
    this.$root.find("#lpa-vider").toggle(!!this.sel.size);
    this.$root.find("#lpa-total").html(n ? `Total TTC pour ces quantités : <b>${lpa_dt(a.total)}</b>` : "");
    const ecartes = (a.sans_prix || []).concat(a.introuvables || []);
    this.$root.find("#lpa-alertes").html(ecartes.length
      ? `<div class="lpa-alerte">⚠️ ${ecartes.length} article(s) sans prix dans « ${lpa_esc(this.liste)} » (ou plus en vente) :
           ils ne seront pas dans la liste — ${ecartes.slice(0, 8).map(lpa_esc).join(", ")}${ecartes.length > 8 ? "…" : ""}
           <a href="#" id="lpa-ecarter" style="margin-left:6px">Les retirer</a></div>` : "");
    this.$root.find("#lpa-ecarter").on("click", (e) => { e.preventDefault(); this.retirer(ecartes); });
    const $s = this.$root.find("#lpa-selection");
    if (!n) {
      $s.html(`<div class="lpa-vide">La liste est vide.<br>Cherchez un article ou choisissez une famille à gauche, puis « ＋ Ajouter ».</div>`);
      return;
    }
    let html = `<div class="lpa-colonnes"><span></span><span>Article</span><span class="num">Prix TTC</span><span>Qté</span><span class="num">Total</span><span></span></div>
      <div class="lpa-defile">`;
    let groupe = null;
    a.lignes.forEach((l) => {
      if (l.item_group !== groupe) {
        groupe = l.item_group;
        const nb = a.lignes.filter((x) => x.item_group === groupe).length;
        html += `<div class="lpa-groupe"><span style="flex:1">${lpa_esc(groupe || "Sans famille")} <span class="nb">· ${nb}</span></span>
          <span class="lpa-suppr" data-groupe="${lpa_esc(groupe)}" title="Retirer toute la famille">✕</span></div>`;
      }
      const palier = l.palier ? `<div class="pal ok">${lpa_esc(l.palier)}</div>`
        : (l.prochain ? `<div class="pal">prochain palier : ${lpa_esc(l.prochain)}</div>` : "");
      html += `<div class="lpa-sel-ligne">
          ${lpa_img(l.image)}
          <div><div class="nom">${lpa_esc(l.item_name)}</div><div class="code">${lpa_esc(l.item_code)}</div>${palier}</div>
          <div class="num"><b>${lpa_dt(l.prix_ttc)}</b>${l.remise_pct > 0 && l.prix_standard_ttc
            ? `<br><s>${lpa_dt(l.prix_standard_ttc)}</s> <span class="rem">-${l.remise_pct} %</span>` : ""}${
            l.prix_promo != null ? `<br><span class="promo" title="Prix TTC minimum avec promotion">promo ${lpa_dt(l.prix_promo)}</span>` : ""}</div>
          <input type="number" min="1" step="1" class="form-control lpa-qte" data-code="${lpa_esc(l.item_code)}" value="${l.qte}">
          <div class="num">${lpa_dt(l.total)}</div>
          <span class="lpa-suppr" data-code="${lpa_esc(l.item_code)}" title="Retirer">✕</span>
        </div>`;
    });
    $s.html(html + "</div>");
  }

  creer() {
    if (!this.sel.size) return;
    frappe.call({
      method: LPA_API + ".creer", type: "POST",
      args: { articles: JSON.stringify(this.articles()), price_list: this.liste, client: this.client },
      freeze: true, freeze_message: "Création de la liste de prix…",
    }).then((r) => {
      const m = r.message;
      this.sel.clear();
      this.maj_selection();
      this.marquer_ajoutes();
      const libelles = { "Liste prix client": "📄 PDF simple (prix, remise, quantités)", "Liste des prix": "📘 PDF catalogue (avec descriptions)" };
      const d = new frappe.ui.Dialog({
        title: "Liste de prix créée",
        fields: [{ fieldtype: "HTML", fieldname: "corps" }],
        primary_action_label: "Nouvelle liste",
        primary_action: () => d.hide(),
      });
      d.fields_dict.corps.$wrapper.html(`
        <p>✅ <b>${lpa_esc(m.name)}</b> — ${m.nb_articles} article(s), liste « ${lpa_esc(this.liste)} »${
          this.client ? `, client ${lpa_esc(this.client)}` : ""}.</p>
        ${(m.sans_prix || []).length ? `<p class="text-muted">${m.sans_prix.length} article(s) sans prix écarté(s).</p>` : ""}
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:10px">
          ${(m.pdf || []).map((p) => `<a class="btn btn-default" target="_blank" href="${lpa_esc(p.url)}">${libelles[p.format] || lpa_esc(p.format)}</a>`).join("")}
          <a class="btn btn-default" href="/app/liste-prix-documents/${encodeURIComponent(m.name)}">Ouvrir le document</a>
        </div>`);
      d.show();
    });
  }
}
