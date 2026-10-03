/**
 * « Campagne SMS » — un seul écran : cibler, vérifier la liste, écrire, tester, envoyer, suivre.
 * Les règles (consentement, numéros valides, coût, envoi en tâche de fond) sont côté serveur (customization_app.campagne_sms).
 */
frappe.pages["campagne-sms"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Campagne SMS", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("campagne_sms", {}));
  wrapper.cs = new CampagneSMS(wrapper);
};
frappe.pages["campagne-sms"].on_page_show = function (wrapper) {
  if (wrapper.cs && wrapper.cs.pret) {
    wrapper.cs.charger_recentes();
    const nom = frappe.route_options && frappe.route_options.campagne;
    if (nom) { frappe.route_options = null; wrapper.cs.ouvrir(nom); }
  }
};

const CS_API = "customization_app.campagne_sms";
const cs_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

class CampagneSMS {
  constructor(wrapper) {
    this.$ = (sel) => $(wrapper).find(sel);
    this.filtres = { groupes: [], secteurs: [], clients: [] };
    this.lignes = [];
    this.coches = new Set();
    this.name = null;
    this.pret = true;
    this._brancher();
    this.init();
  }

  async init() {
    const o = (await frappe.call({ method: CS_API + ".options" })).message || {};
    this.options = o;
    if (o.simulation) this.$("#cs-sim").show().html("🧪 <b>Mode développement</b> : aucun SMS ne part réellement, tout est simulé. Les résultats affichent « Simulé ».");
    this.$("#cs-groupes").html((o.groupes || []).map((g) => `<span class="cs-chip" data-g="${cs_esc(g.nom)}">${cs_esc(g.nom || "— sans groupe")} <small>${g.clients} · ${g.joignables}</small></span>`).join(""));
    this.$("#cs-secteurs").html((o.secteurs || []).map((s) => `<span class="cs-chip" data-s="${cs_esc(s)}">${cs_esc(s)}</span>`).join(""));
    this.$("#cs-balises").html((o.balises || []).map((b) => `<span class="cs-balise" data-b="${cs_esc(b)}">${cs_esc(b)}</span>`).join(""));
    if (o.mon_numero) this.$("#cs-test-num").val(o.mon_numero);
    this.rendre_recentes(o.recentes || []);
    const nom = frappe.route_options && frappe.route_options.campagne;
    if (nom) { frappe.route_options = null; this.ouvrir(nom); }
  }

  _brancher() {
    this.$("#cs-groupes").on("click", ".cs-chip", (e) => { $(e.currentTarget).toggleClass("on"); this._lire_filtres(); });
    this.$("#cs-secteurs").on("click", ".cs-chip", (e) => { $(e.currentTarget).toggleClass("on"); this._lire_filtres(); });
    this.$("#cs-cibler").on("click", () => this.cibler());
    let t;
    this.$("#cs-ajout").on("input", (e) => {
      clearTimeout(t);
      const v = e.target.value;
      t = setTimeout(async () => {
        if (v.trim().length < 5) { this.$("#cs-ajout-res").html(""); return; }
        const r = (await frappe.call({ method: CS_API + ".rechercher_clients", args: { texte: v } })).message || [];
        this.$("#cs-ajout-res").html(r.length ? `<div class="cs-chips" style="margin-top:4px">${r.map((c) => `<span class="cs-chip" data-add="${cs_esc(c.name)}" title="${cs_esc(c.custom_liste_telephone || "")}">＋ ${cs_esc(c.customer_name)} <small>${cs_esc(c.customer_group || "")}</small></span>`).join("")}</div>` : `<div class="cs-vide">Aucun client.</div>`);
      }, 350);
    });
    this.$("#cs-ajout-res").on("click", "[data-add]", (e) => {
      const n = $(e.currentTarget).data("add");
      if (!this.filtres.clients.includes(n)) this.filtres.clients.push(n);
      this.$("#cs-ajout").val(""); this.$("#cs-ajout-res").html("");
      this._rendre_ajoutes(); this.cibler();
    });
    this.$("#cs-ajoutes").on("click", "[data-del]", (e) => {
      this.filtres.clients = this.filtres.clients.filter((c) => c !== $(e.currentTarget).data("del"));
      this._rendre_ajoutes(); this.cibler();
    });
    this.$("#cs-liste").on("change", ".cs-coche", (e) => {
      const c = e.target.value;
      if (e.target.checked) this.coches.add(c); else this.coches.delete(c);
      this._kpis(); this.apercu();
    });
    this.$("#cs-tout").on("click", () => { this.lignes.filter((l) => !l.exclu).forEach((l) => this.coches.add(l.client)); this.rendre_liste(); this.apercu(); });
    this.$("#cs-rien").on("click", () => { this.coches.clear(); this.rendre_liste(); this.apercu(); });
    this.$("#cs-filtre, #cs-voir-exclus").on("input change", () => this.rendre_liste());
    this.$("#cs-balises").on("click", ".cs-balise", (e) => {
      const ta = this.$("#cs-message")[0], b = $(e.currentTarget).data("b");
      const s = ta.selectionStart || 0, f = ta.selectionEnd || 0;
      ta.value = ta.value.slice(0, s) + b + ta.value.slice(f);
      ta.focus(); ta.selectionStart = ta.selectionEnd = s + b.length;
      this.apercu();
    });
    let t2;
    this.$("#cs-message").on("input", () => { clearTimeout(t2); t2 = setTimeout(() => this.apercu(), 450); });
    this.$("#cs-test").on("click", () => this.test());
    this.$("#cs-enregistrer").on("click", () => this.enregistrer());
    this.$("#cs-envoyer").on("click", () => this.envoyer());
    this.$("#cs-recentes").on("click", "[data-ouvrir]", (e) => this.ouvrir($(e.currentTarget).data("ouvrir")));
    this.$("#cs-recentes").on("click", "[data-reutiliser]", (e) => { this.$("#cs-message").val($(e.currentTarget).data("reutiliser")); this.apercu(); frappe.show_alert({ message: "Message repris.", indicator: "blue" }); });
    this.$("#cs-recentes").on("click", "[data-dupliquer]", async (e) => {
      const r = (await frappe.call({ method: CS_API + ".dupliquer", args: { name: $(e.currentTarget).data("dupliquer"), seulement_echecs: $(e.currentTarget).data("echecs") ? 1 : 0 }, freeze: true })).message;
      frappe.show_alert({ message: `Brouillon ${r.name} créé (${r.clients} client(s)).`, indicator: "green" });
      this.ouvrir(r.name);
    });
    frappe.realtime.on("campagne_sms_progress", (d) => { if (d.name === this.name) this._progression(d); });
  }

  _lire_filtres() {
    this.filtres.groupes = this.$("#cs-groupes .cs-chip.on").map((_i, el) => $(el).data("g")).get();
    this.filtres.secteurs = this.$("#cs-secteurs .cs-chip.on").map((_i, el) => $(el).data("s")).get();
    this.filtres.consent = this.$("#cs-consent").is(":checked");
    this.filtres.autorisation = this.$("#cs-autorisation").is(":checked");
    this.filtres.exclure_partenaire = this.$("#cs-partenaire").is(":checked");
    this.filtres.interesse = this.$("#cs-interesse").val() || "";
    return this.filtres;
  }

  _rendre_ajoutes() {
    this.$("#cs-ajoutes").html(this.filtres.clients.map((c) => `<span class="cs-chip on">${cs_esc(c)} <span data-del="${cs_esc(c)}" style="cursor:pointer;margin-left:4px">✕</span></span>`).join(""));
  }

  async cibler() {
    this._lire_filtres();
    if (!this.filtres.groupes.length && !this.filtres.clients.length) {
      frappe.show_alert({ message: "Choisissez au moins un groupe ou ajoutez un client.", indicator: "orange" });
      return;
    }
    const r = (await frappe.call({ method: CS_API + ".cibler", args: { filtres: JSON.stringify(this.filtres) }, freeze: true })).message || {};
    this.lignes = r.lignes || [];
    this.coches = new Set(this.lignes.filter((l) => !l.exclu).map((l) => l.client));
    this.stats = r.stats;
    this.rendre_liste();
    this.apercu();
  }

  _kpis() {
    const cibles = this.lignes.filter((l) => !l.exclu && this.coches.has(l.client));
    this.$("#cs-k-cibles").text(cibles.length);
    this.$("#cs-k-numeros").text(cibles.reduce((n, l) => n + (l.numeros || []).length, 0));
    this.$("#cs-k-exclus").text(this.lignes.filter((l) => l.exclu).length);
  }

  rendre_liste() {
    const q = (this.$("#cs-filtre").val() || "").toLowerCase(), voir = this.$("#cs-voir-exclus").is(":checked");
    const ex = this.stats ? Object.entries(this.stats.exclus || {}).map(([m, n]) => `${n} ${m}`).join(" · ") : "";
    this.$("#cs-liste-titre").text(ex ? `— écartés : ${ex}` : "");
    const lignes = this.lignes.filter((l) => (voir || !l.exclu) && (!q || `${l.nom} ${l.groupe} ${l.secteur} ${(l.numeros || []).join(" ")}`.toLowerCase().includes(q)));
    this.$("#cs-liste").html(lignes.length ? `<table class="cs-table"><thead><tr><th></th><th>Client</th><th>Groupe</th><th>Secteur</th><th>Numéros</th></tr></thead><tbody>${lignes.map((l) => `
      <tr class="${l.exclu ? "exclu" : ""}">
        <td>${l.exclu ? "" : `<input type="checkbox" class="cs-coche" value="${cs_esc(l.client)}" ${this.coches.has(l.client) ? "checked" : ""}>`}</td>
        <td><a href="/app/customer/${encodeURIComponent(l.client)}" target="_blank">${cs_esc(l.nom)}</a>${l.ajoute ? ` <span class="cs-badge enc">ajouté</span>` : ""}${l.exclu ? `<div class="motif">⛔ ${cs_esc(l.exclu)}</div>` : ""}</td>
        <td>${cs_esc(l.groupe)}</td><td>${cs_esc(l.secteur)}</td><td>${cs_esc((l.numeros || []).join(", "))}</td></tr>`).join("")}</tbody></table>`
      : `<div class="cs-vide">${this.lignes.length ? "Aucune ligne pour ce filtre." : "Choisissez des groupes puis « Calculer la cible »."}</div>`);
    this._kpis();
  }

  _lignes_cochees() {
    return this.lignes.filter((l) => !l.exclu && this.coches.has(l.client)).map((l) => ({ ...l, coche: true }));
  }

  async apercu() {
    const message = this.$("#cs-message").val() || "";
    if (!message.trim()) { this.$("#cs-cout").text("—").attr("class", "cs-cout"); this.$("#cs-apercus").html(""); this.$("#cs-k-segments").text(0); return; }
    try {
      const r = (await frappe.call({ method: CS_API + ".apercu", args: { message, lignes: JSON.stringify(this._lignes_cochees()) } })).message || {};
      const a = r.analyse_brute || {};
      this.$("#cs-cout").attr("class", "cs-cout " + (a.unicode ? "ko" : "ok")).html(
        `${a.unicode ? `⚠️ caractères hors alphabet SMS (${cs_esc((a.hors_gsm || []).join(" "))}) : message en unicode` : "✅ alphabet GSM"} · ${a.longueur} car. · <b>${r.segments_par_sms} segment(s) par SMS</b> × ${r.numeros} numéro(s) = <b>${r.segments} SMS facturés</b>`);
      this.$("#cs-k-segments").text(r.segments || 0);
      this.$("#cs-apercus").html((r.apercus || []).map((p) => `<div class="cs-apercu"><b>${cs_esc(p.nom)} · ${p.analyse.longueur} car. · ${p.analyse.segments} seg.</b>${cs_esc(p.texte)}</div>`).join(""));
    } catch (e) { /* message mal formé : l'erreur serveur s'affiche déjà */ }
  }

  async test() {
    const message = this.$("#cs-message").val() || "", numero = this.$("#cs-test-num").val();
    const l = this._lignes_cochees()[0] || {};
    const r = (await frappe.call({ method: CS_API + ".envoyer_test", args: { numero, message, client: l.client, nom: l.nom, groupe: l.groupe }, freeze: true })).message || {};
    frappe.msgprint({ title: `Test : ${r.statut}`, indicator: r.statut === "Échec" ? "red" : "green",
      message: `<pre style="white-space:pre-wrap;font-family:inherit">${cs_esc(r.texte)}</pre><div class="text-muted">${r.analyse.longueur} car. · ${r.analyse.segments} segment(s)${r.detail ? " · " + cs_esc(r.detail) : ""}</div>` });
  }

  async enregistrer(silencieux) {
    const r = (await frappe.call({ method: CS_API + ".enregistrer", freeze: true, args: {
      name: this.name, titre: this.$("#cs-titre").val(), message: this.$("#cs-message").val(),
      filtres: JSON.stringify(this._lire_filtres()), lignes: JSON.stringify(this._lignes_cochees()) } })).message;
    this.name = r.name;
    if (!silencieux) frappe.show_alert({ message: `Campagne ${r.name} enregistrée : ${r.clients} client(s), ${r.numeros} numéro(s), ${r.segments} SMS.`, indicator: "green" });
    this.charger_recentes();
    return r;
  }

  async envoyer() {
    const r = await this.enregistrer(true);
    const sim = this.options && this.options.simulation;
    frappe.confirm(`${sim ? "🧪 SIMULATION (dev) — " : ""}Envoyer <b>${r.segments} SMS</b> à <b>${r.numeros} numéro(s)</b> (${r.clients} client(s)) ?<br>La campagne sera figée une fois envoyée.`, async () => {
      await frappe.call({ method: CS_API + ".lancer", args: { name: this.name }, freeze: true });
      this.$("#cs-etat").html(`<div class="cs-progress"><div style="width:0"></div></div><div class="text-muted" style="font-size:12px" id="cs-prog-txt">Envoi en cours…</div>`);
      this.$("#cs-envoyer, #cs-enregistrer").prop("disabled", true);
    });
  }

  _progression(d) {
    const pct = d.total ? Math.round((d.fait / d.total) * 100) : 0;
    this.$("#cs-etat .cs-progress > div").css("width", pct + "%");
    this.$("#cs-prog-txt").text(`${d.fait} / ${d.total} client(s) · ${d.envoyes} envoyé(s) · ${d.echecs} échec(s)`);
    if (d.fini) {
      frappe.show_alert({ message: `Campagne terminée : ${d.statut}`, indicator: d.echecs ? "orange" : "green" });
      this.ouvrir(this.name);
      this.charger_recentes();
    }
  }

  async ouvrir(name) {
    const e = (await frappe.call({ method: CS_API + ".etat", args: { name } })).message;
    this.name = e.docstatus === 0 ? e.name : null;        // une campagne envoyée n'est plus modifiable : on la consulte
    this.$("#cs-titre").val(e.titre || ""); this.$("#cs-message").val(e.message || "");
    const f = e.filtres || {};
    this.$("#cs-groupes .cs-chip").each((_i, el) => $(el).toggleClass("on", (f.groupes || []).includes($(el).data("g"))));
    this.$("#cs-secteurs .cs-chip").each((_i, el) => $(el).toggleClass("on", (f.secteurs || []).includes($(el).data("s"))));
    this.filtres.clients = f.clients || []; this._rendre_ajoutes();
    this.lignes = e.lignes.map((l) => ({ ...l, exclu: null })); this.coches = new Set(e.lignes.filter((l) => l.envoyer).map((l) => l.client)); this.stats = null;
    this.rendre_liste(); this.apercu();
    const badge = (s) => `<span class="cs-badge ${["Envoyé", "Simulé", "Envoyée"].includes(s) ? "ok" : ["Échec", "Partiel", "Partielle"].includes(s) ? "ko" : s === "En cours" ? "enc" : "gris"}">${cs_esc(s || "—")}</span>`;
    const resultats = e.docstatus !== 0 || e.statut !== "Brouillon" ? `<div style="margin-top:6px"><b>${cs_esc(e.titre || e.name)}</b> ${badge(e.statut)} <span class="text-muted">${cs_esc(e.envoye_le)} · ${e.envoyes || 0} envoyé(s) · ${e.echecs || 0} échec(s) · ${e.invalides || 0} sans numéro · ${e.segments || 0} segments</span>
        ${e.echecs ? `<button class="btn btn-xs btn-default" data-dupliquer="${cs_esc(e.name)}" data-echecs="1" style="margin-left:6px">↻ Refaire avec les échecs</button>` : ""}
        <div class="cs-scroll" style="max-height:260px;margin-top:6px"><table class="cs-table"><thead><tr><th>Client</th><th>Numéros</th><th>Statut</th><th>Détail</th></tr></thead><tbody>${e.lignes.map((l) => `<tr><td>${cs_esc(l.nom)}</td><td>${cs_esc((l.numeros || []).join(", "))}</td><td>${badge(l.statut)}</td><td class="text-muted" style="font-size:11px">${cs_esc(l.detail)} ${cs_esc(l.le)}</td></tr>`).join("")}</tbody></table></div></div>` : `<div class="text-muted" style="font-size:12px">Brouillon ${cs_esc(e.name)} chargé : modifiez puis « Lancer l’envoi ».</div>`;
    this.$("#cs-etat").html(resultats);
    this.$("#cs-etat").off("click", "[data-dupliquer]").on("click", "[data-dupliquer]", async (ev) => {
      const r = (await frappe.call({ method: CS_API + ".dupliquer", args: { name: $(ev.currentTarget).data("dupliquer"), seulement_echecs: 1 }, freeze: true })).message;
      this.ouvrir(r.name);
    });
    this.$("#cs-envoyer, #cs-enregistrer").prop("disabled", e.docstatus !== 0);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async charger_recentes() {
    const o = (await frappe.call({ method: CS_API + ".options" })).message || {};
    this.rendre_recentes(o.recentes || []);
  }

  rendre_recentes(recentes) {
    this.$("#cs-recentes").html(recentes.length ? `<table class="cs-table cs-rec"><tbody>${recentes.map((c) => `<tr>
      <td><a href="#" data-ouvrir="${cs_esc(c.name)}"><b>${cs_esc(c.titre || c.name)}</b></a><div class="text-muted" style="font-size:11px">${cs_esc(c.name)} · ${cs_esc(String(c.envoye_le || c.creation).slice(0, 16))}</div></td>
      <td><span class="cs-badge ${c.statut === "Envoyée" ? "ok" : ["Partielle", "Échec"].includes(c.statut) ? "ko" : c.statut === "En cours" ? "enc" : "gris"}">${cs_esc(c.statut || (c.docstatus ? "Envoyée" : "Brouillon"))}</span></td>
      <td class="text-muted">${c.envoyes || 0} envoyé(s) · ${c.echecs || 0} échec(s)</td>
      <td style="font-size:11.5px;color:#475569;max-width:420px">${cs_esc((c.message || "").slice(0, 110))}${(c.message || "").length > 110 ? "…" : ""}</td>
      <td style="white-space:nowrap"><button class="btn btn-xs btn-default" data-reutiliser="${cs_esc(c.message || "")}">↪ Reprendre le message</button>
          <button class="btn btn-xs btn-default" data-dupliquer="${cs_esc(c.name)}">⧉ Dupliquer</button></td></tr>`).join("")}</tbody></table>` : `<div class="cs-vide">Aucune campagne.</div>`);
  }
}
