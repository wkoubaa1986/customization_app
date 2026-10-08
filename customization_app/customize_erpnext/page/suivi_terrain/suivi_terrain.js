frappe.pages["suivi-terrain"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Suivi terrain", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("suivi_terrain", {}));
  wrapper.suivi_terrain = new SuiviTerrain(wrapper);
};

frappe.pages["suivi-terrain"].on_page_show = function (wrapper) {
  wrapper.suivi_terrain && wrapper.suivi_terrain.reprendre();
};

// Rendu pur : tout vient de customization_app.flotte_gps.suivi / statistiques.
const ST_ETAPE = {
  "passée": ["passee", "✅ passée"], "sur place": ["surplace", "🟡 sur place"], "en route": ["enroute", "🚗 en route"],
  "à venir": ["avenir", "⏳ à venir"], "en retard": ["manque", "⏰ heure dépassée"], "sautée": ["manque", "⛔ non faite (sautée ?)"], "sans passage": ["manque", "❌ sans passage"],
  "clôturée sans passage": ["manque", "⚠️ clôturée sans passage GPS"], "clôturée": ["autre", "✔ clôturée (non suivie)"],
  "non suivie": ["autre", "– non suivie"], "sans position": ["autre", "📍 sans position"], "annulée": ["annulee", "annulée"],
};
const ST_RAFRAICHIR_S = 60;
const ST_COULEURS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#be185d", "#4d7c0f"];
const ST_FOND = { "passée": "#16a34a", "clôturée": "#16a34a", "sur place": "#eab308", "en route": "#2563eb", "à venir": "#60a5fa",
  "en retard": "#dc2626", "sans passage": "#dc2626", "clôturée sans passage": "#dc2626", "annulée": "#9ca3af" };

class SuiviTerrain {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".st-page");
    this.timer = null;
    this.data = null;
    this.$root.find("#st-jour").val(frappe.datetime.get_today());
    this.$root.find("#st-du").val(frappe.datetime.add_days(frappe.datetime.get_today(), -29));
    this.$root.find("#st-au").val(frappe.datetime.get_today());
    this._bind();
    this.charger();
  }

  _bind() {
    const $r = this.$root;
    $r.find("[data-action='refresh']").on("click", () => this.charger());
    $r.find("#st-jour").on("change", () => this.charger());
    $r.find("[data-action='reglages']").on("click", () => frappe.set_route("Form", "Config Flotte GPS"));
    $r.find("[data-action='apparier']").on("click", () => this.apparier());
    $r.find("[data-action='stats']").on("click", () => this.stats());
    $r.find("[data-action='backfill']").on("click", () => this.backfill());
    $r.find("[data-action='apercu']").on("click", () => this.apercu_messages());
    $r.find("[data-action='simuler']").on("click", () => this.simuler_messages());
    let carte_voulue = true;
    try { carte_voulue = localStorage.getItem("st_carte") !== "0"; } catch (e) { /* stockage indisponible */ }
    this.carte_visible = carte_voulue;
    $r.find("[data-action='carte']").on("click", () => {
      this.carte_visible = !this.carte_visible;
      try { localStorage.setItem("st_carte", this.carte_visible ? "1" : "0"); } catch (e) { /* ignoré */ }
      if (this.data) this._render_carte();
    });
    $r.on("click", ".st-nom", (e) => {
      const nom = $(e.currentTarget).text();
      const emp = this.data && this.data.employes.find((x) => x.nom === nom);
      if (emp && emp.position && this.carte) this.carte.setView([emp.position.lat, emp.position.lng], 14);
    });
    $r.find(".st-tab").on("click", (e) => {
      const t = $(e.currentTarget).attr("data-tab");
      $r.find(".st-tab").removeClass("active"); $(e.currentTarget).addClass("active");
      $r.find(".st-panel").removeClass("active"); $r.find(`.st-panel[data-panel='${t}']`).addClass("active");
      if (t === "stats" && !this._stats_faites) this.stats();
    });
    $r.on("click", ".st-msg, [data-copie]", (e) => {
      const txt = $(e.currentTarget).attr("data-copie") || $(e.currentTarget).text();
      frappe.utils.copy_to_clipboard(txt.trim());
    });
  }

  reprendre() { this._planifier(); }

  _planifier() {
    clearTimeout(this.timer);
    if (this.data && this.data.aujourdhui) {
      this.timer = setTimeout(() => { if (document.visibilityState === "visible") this.charger(true); else this._planifier(); }, ST_RAFRAICHIR_S * 1000);
    }
  }

  async charger(silencieux) {
    clearTimeout(this.timer);
    const jour = this.$root.find("#st-jour").val();
    try {
      const r = await frappe.call({ method: "customization_app.flotte_gps.suivi", args: { jour }, freeze: !silencieux, freeze_message: __("Lecture de la plateforme GPS…") });
      this.data = r.message;
      this._render_live();
    } catch (e) {
      this.$root.find("#st-live").html(`<div class="st-alert">${frappe.utils.escape_html(String(e.message || e))}</div>`);
    }
    this._planifier();
  }

  async apparier() {
    const jour = this.$root.find("#st-jour").val();
    const r = await frappe.call({ method: "customization_app.flotte_gps.apparier_jour", args: { jour }, freeze: true, freeze_message: __("Appariement…") });
    frappe.msgprint({ title: __("Appariement"), message: frappe.utils.escape_html(r.message.message || JSON.stringify(r.message)), indicator: "green" });
    this._stats_faites = false;
    this.charger(true);
  }

  async backfill() {
    const du = this.$root.find("#st-du").val(), au = this.$root.find("#st-au").val();
    const n = frappe.datetime.get_day_diff(au, du) + 1;
    if (n < 1 || n > 120) { frappe.msgprint(__("Choisir une période de 1 à 120 jours.")); return; }
    frappe.confirm(__("Reprendre {0} jour(s) depuis la plateforme GPS ? (environ 5 s par jour)", [n]), async () => {
      const r = await frappe.call({ method: "customization_app.flotte_gps.apparier_periode", args: { du, au }, freeze: true, freeze_message: __("Reprise de {0} jour(s)…", [n]) });
      const ko = (r.message || []).filter((x) => x.erreur);
      frappe.msgprint({ title: __("Reprise terminée"), indicator: ko.length ? "orange" : "green",
        message: `${(r.message || []).length - ko.length} jour(s) appariés` + (ko.length ? `<br>${ko.length} en erreur : ` + ko.map((x) => `${x.jour} (${frappe.utils.escape_html(x.erreur)})`).join(", ") : "") });
      this._stats_faites = false;
      this.stats();
    });
  }

  // ── Maintenant ────────────────────────────────────────────────────────────
  _h(dt) { return dt ? frappe.datetime.str_to_obj(dt).toTimeString().slice(0, 5) : ""; }
  _min(n) { if (n == null) return ""; n = Math.round(n); return n >= 60 ? `${Math.floor(n / 60)} h ${String(n % 60).padStart(2, "0")}` : `${n} min`; }
  _ecart(n, tol) {
    if (n == null) return "";
    const cls = n < -tol ? "avance" : n > tol ? "retard" : "ok";
    const txt = n < -tol ? `${this._min(-n)} en avance` : n > tol ? `${this._min(n)} de retard` : "à l’heure";
    return `<span class="st-ecart ${cls}">${txt}</span>`;
  }

  _phrase(e, t, d) {
    // Ce qu’on peut dire au client qui appelle.
    const qui = `votre technicien ${e.nom.split(" ")[0]}`;
    const ann = t.debut ? ` (heure annoncée ${this._h(t.debut)})` : "";
    switch (t.etape) {
      case "passée": return `${qui[0].toUpperCase() + qui.slice(1)} est passé chez vous de ${this._h(t.gps_live.arrivee)} à ${this._h(t.gps_live.depart)}.`;
      case "sur place": return `${qui[0].toUpperCase() + qui.slice(1)} est chez vous depuis ${this._h(t.arrivee_reelle)}.`;
      case "en route": return `${qui[0].toUpperCase() + qui.slice(1)} est en route, arrivée estimée vers ${this._h(t.eta)}${ann}.`;
      case "à venir": case "en retard": {
        if (!t.eta) return `Votre intervention est prévue à ${this._h(t.debut)} avec ${e.nom}.`;
        const avant = e.taches.filter((x) => ["sur place", "en route", "à venir", "en retard"].includes(x.etape) && x.debut < t.debut).length;
        if (!avant) return `${qui[0].toUpperCase() + qui.slice(1)} vient chez vous juste après, arrivée estimée vers ${this._h(t.eta)}${ann}.`;
        return `${qui[0].toUpperCase() + qui.slice(1)} a encore ${avant} intervention${avant > 1 ? "s" : ""} avant la vôtre, arrivée estimée vers ${this._h(t.eta)}${ann}.`;
      }
      case "sautée": return `Votre intervention n’a pas pu être réalisée à l’heure prévue ; nous vous rappelons pour la reprogrammer.`;
      case "sans passage": case "clôturée sans passage": return `Aucun passage du véhicule n’a été relevé chez vous${ann} — à vérifier avec ${e.nom}.`;
      case "annulée": return `Cette intervention a été annulée.`;
      default: return `Intervention prévue à ${this._h(t.debut)} avec ${e.nom}${t.statut === "Completed" ? " (clôturée)" : ""}.`;
    }
  }

  _render_live() {
    const d = this.data, esc = frappe.utils.escape_html, $out = this.$root.find("#st-live");
    this.$root.find("#st-info").text(`${d.aujourdhui ? "Live — lu à " + this._h(d.maintenant) + ", actualisé toutes les " + ST_RAFRAICHIR_S + " s" : "Relecture du " + frappe.datetime.str_to_user(d.jour)}`);
    let html = "";
    if (!d.configure) html += `<div class="st-alert">Plateforme GPS non configurée : renseigner l’utilisateur, le mot de passe et les véhicules dans <a href="/app/config-flotte-gps">Config Flotte GPS</a>.</div>`;
    if (d.erreur) html += `<div class="st-alert">Plateforme GPS injoignable : ${esc(d.erreur)}</div>`;
    if (!d.employes.length) { $out.html(html + `<div class="st-empty">Aucune tâche ni véhicule ce jour.</div>`); return; }
    html += `<div class="st-grid">`;
    for (const e of d.employes) {
      let etat = "", cls = "off";
      if (e.erreur) { etat = "journal indisponible"; }
      else if (e.etat === "en mouvement") { etat = `🚗 en mouvement${e.position ? " · " + e.position.vitesse + " km/h" : ""}`; cls = "mouv"; }
      else if (e.etat === "à l’arrêt") { etat = `⏸ à l’arrêt${e.lieu ? " " + esc(e.lieu) : ""}${e.arret_depuis ? " depuis " + this._h(e.arret_depuis) : ""}`; cls = "arret"; }
      else if (e.etat === "journée terminée") { etat = "journée terminée"; cls = "fin"; }
      else if (!e.cbox) { etat = "pas de véhicule suivi"; }
      else { etat = "pas de données aujourd’hui"; }
      const pos = e.position ? `<a href="${e.position.lien}" target="_blank" rel="noopener">📍 position</a> ${e.position.age_min != null ? "(il y a " + this._min(e.position.age_min) + ")" : ""}${e.position.en_ligne ? "" : " · boîtier hors ligne"}` : "";
      html += `<div class="st-card"><div class="st-card-head"><span class="st-nom">${esc(e.nom)}</span><span class="st-veh">${esc(e.vehicule || "")}${e.vehicule_detecte ? " <span title=\"Véhicule reconnu d’après les arrêts du jour, différent du véhicule habituel\">(reconnu)</span>" : ""}</span>
        <span class="st-etat ${cls}">${etat}</span><span class="st-prog">${e.faites}/${e.total} faites</span></div>
        <div class="st-sub">${pos ? `<span>${pos}</span>` : ""}${e.km != null ? `<span>🛣 ${e.km} km</span>` : ""}${e.premiere_sortie ? `<span>départ ${this._h(e.premiere_sortie)}</span>` : ""}${e.dernier_mouvement && !d.aujourdhui ? `<span>retour ${this._h(e.dernier_mouvement)}</span>` : ""}</div>`;
      const prochaine = e.taches.find((t) => ["sur place", "en route", "à venir", "en retard"].includes(t.etape));
      if (prochaine && d.aujourdhui) html += `<div class="st-msg" title="Cliquer pour copier">💬 ${esc(this._phrase(e, prochaine, d))}</div>`;
      if (e.taches.length) {
        html += `<table class="st-t"><thead><tr><th>Annoncé</th><th>Client</th><th>Étape</th><th>Réel / estimé</th><th class="num">Durée</th></tr></thead><tbody>`;
        for (const t of e.taches) {
          const [ec, el] = ST_ETAPE[t.etape] || ["autre", t.etape];
          let reel = "", duree = "";
          if (t.etape === "passée") { reel = `${this._h(t.gps_live.arrivee)} → ${this._h(t.gps_live.depart)} ${this._ecart(t.gps_live.ecart, d.tolerance)}`; duree = this._min(t.gps_live.duree); }
          else if (t.etape === "sur place") { reel = `arrivé ${this._h(t.arrivee_reelle)}${t.eta_depart ? ", fin estimée " + this._h(t.eta_depart) : ""}`; duree = this._min((new Date(d.maintenant) - new Date(t.arrivee_reelle)) / 60000); }
          else if (t.eta) { reel = `arrivée estimée ${this._h(t.eta)} ${this._ecart(t.ecart_prevu, d.tolerance)}${t.route_min ? ` <span class="st-dim">(${t.route_min} min de route)</span>` : ""}`; }
          const prevenu = (t.messages || []).filter((m) => !["Alerte interne", "Non faite"].includes(m.type)).map((m) => `📨 ${m.type} ${this._h(m.heure)}${m.statut === "Simulé" ? " (simulé)" : m.statut === "Échec" ? " ❌" : ""}`).join(", ");
          html += `<tr><td class="num">${this._h(t.debut)}${t.fin ? "–" + this._h(t.fin) : ""}</td>
            <td><span class="st-client">${esc(t.client || t.titre || t.name)}</span><br><span class="st-dim">${esc(t.type)}${t.secteur ? " · " + esc(t.secteur) : ""}${t.position_src && t.position_src !== "tâche" ? " · position " + esc(t.position_src) : ""}</span></td>
            <td><span class="st-etape ${ec}">${el}</span></td><td>${reel} <a href="#" class="st-dim" data-copie="${esc(this._phrase(e, t, d))}" title="Copier la phrase pour le client">📋</a></td><td class="num">${duree}${prevenu ? `<br><span class="st-dim">${prevenu}</span>` : ""}</td></tr>`;
        }
        html += `</tbody></table>`;
      } else html += `<div class="st-sub">Aucune tâche planifiée.</div>`;
      html += `</div>`;
    }
    $out.html(html + `</div>`);
    this._render_carte();
    this._render_retards();
    this._render_messages();
  }

  // ── Retards prévisibles (pour Salma) : arrivée estimée au-delà de la tolérance ───────────────────────
  _render_retards() {
    const d = this.data, esc = frappe.utils.escape_html, $el = this.$root.find("#st-retards");
    const lignes = [];
    for (const e of d.employes) for (const t of e.taches) {
      if ((["à venir", "en route", "en retard", "sur place"].includes(t.etape) && t.ecart_prevu != null && t.ecart_prevu > d.tolerance) || t.etape === "sautée")
        lignes.push({ e, t });
    }
    if (!d.aujourdhui || !lignes.length) { $el.empty(); return; }
    const ns = lignes.filter((l) => l.t.etape === "sautée").length, nr = lignes.length - ns;
    let html = `<div class="st-retard-bloc"><div class="t">⚠️ ${nr ? nr + " retard" + (nr > 1 ? "s" : "") + " prévisible" + (nr > 1 ? "s" : "") : ""}${nr && ns ? " · " : ""}${ns ? ns + " intervention" + (ns > 1 ? "s" : "") + " non faite" + (ns > 1 ? "s" : "") + " à reprogrammer" : ""} — à prévenir</div><table class="st-t"><thead><tr><th>Client</th><th>Tél.</th><th>Technicien</th><th class="num">Annoncé</th><th class="num">Estimé</th><th>Retard</th><th>Client prévenu ?</th><th></th></tr></thead><tbody>`;
    for (const { e, t } of lignes) {
      const sms = (t.messages || []).filter((m) => !["Alerte interne", "Non faite"].includes(m.type));
      const prevenu = sms.length ? sms.map((m) => `${m.type} ${this._h(m.heure)} (${m.statut})`).join(", ") : "<span class='st-ecart retard'>non</span>";
      const estime = t.etape === "sautée" ? `<span class="st-ecart retard">⛔ non faite, une suivante déjà faite</span>` : `${this._h(t.eta)}</td><td>${this._ecart(t.ecart_prevu, d.tolerance)}`;
      html += `<tr><td class="st-client">${esc(t.client || t.name)}</td><td>${esc(t.tel || "")}</td><td>${esc(e.nom)}</td><td class="num">${this._h(t.debut)}</td><td class="num">${estime}</td><td>${prevenu}</td><td><a href="#" data-copie="${esc(this._phrase(e, t, d))}" title="Copier la phrase">📋</a></td></tr>`;
    }
    $el.html(html + `</tbody></table></div>`);
  }

  _render_messages(extra) {
    const d = this.data, esc = frappe.utils.escape_html, $el = this.$root.find("#st-messages");
    const msgs = [];
    for (const e of d.employes) for (const t of e.taches) for (const m of (t.messages || [])) msgs.push({ e, t, m });
    msgs.sort((a, b) => (a.m.heure < b.m.heure ? 1 : -1));
    let html = "";
    if (extra) {
      html += `<div class="st-retard-bloc" style="background:#eff6ff;border-color:#93c5fd"><div class="t" style="color:#1e40af">${esc(extra.titre)}</div>`;
      if (!extra.actif) html += `<div>Aucun type de message n’est activé dans Config Flotte GPS (SMS en route / retard / alerte Appels).</div>`;
      else if (!extra.messages.length) html += `<div>Rien à envoyer maintenant.</div>`;
      else {
        html += `<table class="st-t"><thead><tr><th>Type</th><th>Client</th><th>Tél.</th><th>Message</th><th>Pourquoi</th><th>Verdict</th></tr></thead><tbody>`;
        for (const m of extra.messages) html += `<tr><td>${esc(m.type)}</td><td>${esc(m.client)}</td><td>${esc(m.telephone || "")}</td><td>${esc(m.texte || "")}</td><td class="st-dim">${esc(m.raison || "")}</td><td>${esc(m.statut || "")} <span class="st-dim">${esc(m.detail || "")}</span></td></tr>`;
        html += `</tbody></table>`;
      }
      html += `<div class="st-dim" style="margin-top:4px">${extra.simulation ? "🧪 developer_mode : tout est simulé, rien ne part." : "Envoi réel (production)."}</div></div>`;
    }
    if (!msgs.length) { $el.html(html + `<span class="st-dim">Aucun message aujourd’hui.</span>`); return; }
    html += `<div class="st-msgs"><table class="st-t"><thead><tr><th>Heure</th><th>Type</th><th>Client</th><th>Technicien</th><th>Tél.</th><th>Message</th><th>Verdict</th></tr></thead><tbody>`;
    for (const { e, t, m } of msgs) html += `<tr><td class="num">${this._h(m.heure)}</td><td>${esc(m.type)}</td><td>${esc(t.client || t.name)}</td><td>${esc(e.nom)}</td><td>${esc(m.telephone || "")}</td><td>${esc(m.texte || "")}</td><td>${esc(m.statut)}</td></tr>`;
    $el.html(html + `</tbody></table></div>`);
  }

  async apercu_messages() {
    const r = await frappe.call({ method: "customization_app.flotte_gps_messages.apercu", freeze: true, freeze_message: __("Calcul…") });
    this._render_messages({ titre: "👁 Ce qui partirait maintenant (rien n’a été envoyé)", ...r.message });
  }

  async simuler_messages() {
    const r = await frappe.call({ method: "customization_app.flotte_gps_messages.simuler", freeze: true, freeze_message: __("Simulation…") });
    await this.charger(true);
    this._render_messages({ titre: "🧪 Tour simulé — journalisé comme « Simulé »", ...r.message });
  }

  // ── Carte : véhicules en live, itinéraire prévu, trajet réel, clients numérotés ─────────────────────────
  _render_carte() {
    const d = this.data, el = this.$root.find("#st-carte")[0], esc = frappe.utils.escape_html;
    if (!this.carte_visible || !window.L || !d.employes.some((e) => e.taches.length || e.position)) {
      $(el).hide(); return;
    }
    $(el).show();
    if (!this.carte) {
      this.carte = L.map(el, { scrollWheelZoom: true });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(this.carte);
      this.couches = L.layerGroup().addTo(this.carte);
      this.carte_cadree = null;
    }
    this.couches.clearLayers();
    const bornes = [];
    const pt = (lat, lng) => { bornes.push([lat, lng]); return [lat, lng]; };
    const depots = new Set();
    d.employes.forEach((e, i) => {
      const c = ST_COULEURS[i % ST_COULEURS.length];
      const dep = e.depart || d.depot;
      if (dep && !depots.has(dep.join())) {
        depots.add(dep.join());
        L.marker(pt(dep[0], dep[1]), { icon: L.divIcon({ className: "", html: `<div style="font-size:20px;line-height:20px">${dep.join() === (d.depot || []).join() ? "🏬" : "🏠"}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }) })
          .bindTooltip(dep.join() === (d.depot || []).join() ? "Magasin" : `Domicile de ${esc(e.nom)}`, { direction: "top" }).addTo(this.couches);
      }
      if (e.itineraire && e.itineraire.length > 1) L.polyline(e.itineraire, { color: c, weight: 3, opacity: 0.55, dashArray: "7 7" }).addTo(this.couches);
      if (e.trace && e.trace.length > 1) {
        const ligne = L.polyline(e.trace.map((p) => [p[0], p[1]]), { color: c, weight: 3, opacity: 0.8 }).addTo(this.couches);
        ligne.bindTooltip(`${esc(e.nom)} · trajet réel ${esc(e.trace[0][2])} → ${esc(e.trace[e.trace.length - 1][2])}${e.km != null ? " · " + e.km + " km" : ""}`, { sticky: true });
      }
      let n = 0;
      e.taches.forEach((t) => {
        if (!(t.lat && t.lng) || t.etape === "annulée") return;
        n++;
        const fond = ST_FOND[t.etape] || "#6b7280", approx = t.position_src && t.position_src !== "tâche";
        let detail = `${this._h(t.debut)} annoncé`;
        if (t.etape === "passée") detail += ` · réel ${this._h(t.gps_live.arrivee)} → ${this._h(t.gps_live.depart)} (${this._min(t.gps_live.duree)})`;
        else if (t.etape === "sur place") detail += ` · sur place depuis ${this._h(t.arrivee_reelle)}`;
        else if (t.eta) detail += ` · arrivée estimée ${this._h(t.eta)}`;
        L.marker(pt(t.lat, t.lng), { icon: L.divIcon({ className: "", iconSize: [24, 24], iconAnchor: [12, 12],
          html: `<div style="width:24px;height:24px;border-radius:12px;background:${fond};color:#fff;font-weight:700;font-size:11px;display:flex;align-items:center;justify-content:center;border:3px ${approx ? "dashed" : "solid"} ${c};box-shadow:0 0 0 1px #fff">${n}</div>` }) })
          .bindTooltip(`${n} · ${esc(t.client || t.titre || t.name)} <span style="color:#64748b">${this._h(t.debut)}</span>`, { direction: "right", offset: [12, 0] })
          .bindPopup(`<b>${n} · ${esc(t.client || t.titre || t.name)}</b><br>${esc(e.nom)} · ${esc(t.type)} · <b>${esc(t.etape)}</b><br>${esc(detail)}${approx ? "<br>≈ position approchée" : ""}<br><a href="/app/tache-de-travail/${encodeURIComponent(t.name)}">ouvrir la tâche</a>`)
          .addTo(this.couches);
      });
      if (e.position) {
        const p = e.position, age = p.age_min != null ? ` · il y a ${this._min(p.age_min)}` : "";
        L.marker(pt(p.lat, p.lng), { zIndexOffset: 1000, icon: L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17],
          html: `<div style="width:34px;height:34px;border-radius:17px;background:#fff;border:3px solid ${c};display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 1px 4px rgba(0,0,0,.4)">🚗</div>` }) })
          .bindTooltip(`<b>${esc(e.nom)}</b> · ${esc(e.vehicule || "")}<br>${esc(e.etat)}${e.lieu ? " " + esc(e.lieu) : ""}${p.vitesse ? " · " + p.vitesse + " km/h" : ""}${age}`, { permanent: true, direction: "top", offset: [0, -18], className: "st-etiq" })
          .addTo(this.couches);
      }
    });
    if (bornes.length && !this.carte_cadree) { this.carte.fitBounds(bornes, { padding: [30, 30] }); this.carte_cadree = d.jour; }
    else if (bornes.length && this.carte_cadree !== d.jour) { this.carte.fitBounds(bornes, { padding: [30, 30] }); this.carte_cadree = d.jour; }
    setTimeout(() => this.carte && this.carte.invalidateSize(), 50);
  }

  // ── Statistiques ──────────────────────────────────────────────────────────
  async stats() {
    const args = { du: this.$root.find("#st-du").val(), au: this.$root.find("#st-au").val(), employe: this.$root.find("#st-emp").val() || null };
    const r = await frappe.call({ method: "customization_app.flotte_gps.statistiques", args, freeze: true });
    this._stats_faites = true;
    this._render_stats(r.message);
  }

  _hm(min) { return min == null ? "–" : `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`; }

  _render_stats(s) {
    const esc = frappe.utils.escape_html, $out = this.$root.find("#st-stats");
    const $sel = this.$root.find("#st-emp"), cur = $sel.val();
    if ($sel.find("option").length <= 1) for (const e of s.employes) $sel.append(`<option value="${esc(e.employe)}">${esc(e.nom)}</option>`);
    $sel.val(cur);
    if (!s.taches) { $out.html(`<div class="st-empty">Aucune tâche appariée sur la période : lancer « Apparier ce jour » ou « Reprendre la période ».</div>`); return; }
    const pc = (n, d) => (d ? Math.round((n / d) * 100) + " %" : "–");
    const sel_nom = cur ? ($sel.find("option:selected").text() || "") : "";
    this._stats_data = s;
    const B = s.buckets, nb = B["avance"] + B["à l’heure"] + B["retard"];
    let html = `<div class="st-kpis">
      <div class="st-kpi"><div class="v">${s.taches}</div><div class="l">tâches appariées</div></div>
      <div class="st-kpi"><div class="v">${pc(s.confirmes, s.taches)}</div><div class="l">passage GPS confirmé (${s.confirmes})</div></div>
      <div class="st-kpi"><div class="v">${pc(B["à l’heure"], nb)}</div><div class="l">à l’heure (± ${s.tolerance} min)</div></div>
      <div class="st-kpi"><div class="v">${pc(B["avance"], nb)}</div><div class="l">en avance de plus de ${s.tolerance} min</div></div>
      <div class="st-kpi"><div class="v">${pc(B["retard"], nb)}</div><div class="l">en retard de plus de ${s.tolerance} min</div></div>
      <div class="st-kpi"><div class="v">${s.manquants.length}</div><div class="l">clôturées sans passage GPS</div></div></div>`;
    html += `<div class="st-section">Par employé</div><table class="st-t"><thead><tr><th>Employé</th><th class="num">Tâches</th><th class="num">Confirmées</th><th class="num">Sans passage</th><th class="num">Durée moy.</th><th class="num">Durée méd.</th><th class="num">Écart moy.</th><th class="num">Avance</th><th class="num">À l’heure</th><th class="num">Retard</th></tr></thead><tbody>`;
    for (const e of s.employes) {
      html += `<tr><td>${esc(e.nom)}</td><td class="num">${e.taches}</td><td class="num">${e.confirmes}</td><td class="num">${e.aucun}</td><td class="num">${e.duree.moyenne ?? "–"}</td><td class="num">${e.duree.mediane ?? "–"}</td><td class="num">${e.ecart.moyenne != null ? (e.ecart.moyenne > 0 ? "+" : "") + e.ecart.moyenne + " min" : "–"}</td><td class="num">${e["avance"]}</td><td class="num">${e["à l’heure"]}</td><td class="num">${e["retard"]}</td></tr>`;
    }
    html += `</tbody></table>`;
    // Tableau croisé : durée réelle sur place par technicien ET par type (moyenne / médiane / nombre).
    const emps = s.employes.filter((e) => e.confirmes);
    if (emps.length) {
      html += `<div class="st-section">Durée sur place par technicien et par type d’intervention <span class="st-dim">(moyenne · médiane · nombre de passages)</span></div>
        <table class="st-t"><thead><tr><th>Type</th><th class="num">Standard</th>${emps.map((e) => `<th class="num">${esc(e.nom)}</th>`).join("")}<th class="num">Tous</th></tr></thead><tbody>`;
      for (const t of s.types) {
        if (!t.confirmes) continue;
        html += `<tr><td>${esc(t.type)}</td><td class="num">${t.standard != null ? t.standard + " min" : "–"}</td>`;
        for (const e of emps) {
          const x = e.types.find((y) => y.type === t.type && y.n);
          if (!x) { html += `<td class="num st-dim">–</td>`; continue; }
          const lent = t.standard && x.duree.moyenne > t.standard * 1.25, rapide = t.standard && x.duree.moyenne < t.standard * 0.75;
          html += `<td class="num"><span class="st-ecart ${lent ? "retard" : rapide ? "avance" : "ok"}">${x.duree.moyenne} min</span> <span class="st-dim">· ${x.duree.mediane} · ×${x.n}</span></td>`;
        }
        html += `<td class="num">${t.duree.moyenne} min <span class="st-dim">· ${t.duree.mediane} · ×${t.confirmes}</span></td></tr>`;
      }
      html += `</tbody></table><div class="st-dim" style="margin-top:4px">Rouge : plus de 25 % au-dessus du standard du planning ; bleu : plus de 25 % en dessous.</div>`;
    }
    html += `<div class="st-section">Par type d’intervention — durée réelle sur place vs standard de l’optimiseur</div><table class="st-t"><thead><tr><th>Type</th><th class="num">Tâches</th><th class="num">Confirmées</th><th class="num">Standard</th><th class="num">Réel moyen</th><th class="num">Réel médian</th><th class="num">Max</th><th class="num">Écart moyen</th></tr></thead><tbody>`;
    html += `<div id="st-graphe" style="margin:6px 0 10px"></div>`;
    for (const t of s.types) html += `<tr><td>${esc(t.type)}</td><td class="num">${t.taches}</td><td class="num">${t.confirmes}</td><td class="num">${t.standard ?? "–"}</td><td class="num">${t.duree.moyenne ?? "–"}</td><td class="num">${t.duree.mediane ?? "–"}</td><td class="num">${t.duree.max ?? "–"}</td><td class="num">${t.ecart.moyenne != null ? (t.ecart.moyenne > 0 ? "+" : "") + t.ecart.moyenne + " min" : "–"}</td></tr>`;
    html += `</tbody></table>`;
    if (s.vehicules.length) {
      html += `<div class="st-section">Par véhicule</div><table class="st-t"><thead><tr><th>Véhicule</th><th>Employé</th><th class="num">Jours</th><th class="num">Km</th><th class="num">Km / jour</th><th class="num">Conduite / jour</th><th class="num">Chez clients / jour</th><th class="num">Départ moyen</th><th class="num">Retour moyen</th><th class="num">Passages / tâches</th></tr></thead><tbody>`;
      for (const v of s.vehicules) html += `<tr><td>${esc(v.vehicule)}</td><td>${esc(v.employe_nom || "")}</td><td class="num">${v.jours}</td><td class="num">${v.km}</td><td class="num">${v.km_jour}</td><td class="num">${this._min(v.minutes_conduite / v.jours)}</td><td class="num">${this._min(v.minutes_chez_clients / v.jours)}</td><td class="num">${this._hm(v.sortie_moyenne)}</td><td class="num">${this._hm(v.retour_moyen)}</td><td class="num">${v.nb_passages} / ${v.nb_taches}</td></tr>`;
      html += `</tbody></table>`;
    }
    if (s.par_jour.length) {
      html += `<div class="st-section">Par jour${sel_nom ? " — " + esc(sel_nom) : ""}</div><table class="st-t"><thead><tr><th>Date</th><th>Véhicule</th><th class="num">Tâches</th><th class="num">Passages</th><th class="num">Chez les clients</th><th class="num">Conduite</th><th class="num">Km</th><th class="num">Départ</th><th class="num">Retour</th><th class="num">Écart moyen</th></tr></thead><tbody>`;
      for (const j of s.par_jour) html += `<tr><td>${frappe.datetime.str_to_user(j.date)}</td><td>${esc(j.vehicule || "–")}</td><td class="num">${j.taches}</td><td class="num">${j.confirmes}</td><td class="num">${this._min(j.minutes)}</td><td class="num">${j.conduite != null ? this._min(j.conduite) : "–"}</td><td class="num">${j.km != null ? j.km : "–"}</td><td class="num">${j.depart || "–"}</td><td class="num">${j.retour || "–"}</td><td class="num">${j.ecart_moyen != null ? (j.ecart_moyen > 0 ? "+" : "") + j.ecart_moyen + " min" : "–"}</td></tr>`;
      html += `</tbody></table>`;
    }
    if (s.detail.length) {
      html += `<div class="st-section">Détail tâche par tâche <button class="btn btn-default btn-xs" data-action="csv" style="margin-left:8px">⬇ CSV</button></div>
        <table class="st-t"><thead><tr><th>Date</th><th>Annoncé</th><th>Technicien</th><th>Client</th><th>Type</th><th>Passage</th><th class="num">Arrivée</th><th class="num">Départ</th><th class="num">Sur place</th><th class="num">Planifié</th><th>Écart</th></tr></thead><tbody>`;
      for (const t of s.detail.slice().reverse()) {
        const cls = t.passage === "Passage confirmé" ? "passee" : t.passage === "Aucun passage" ? "manque" : "autre";
        html += `<tr><td>${frappe.datetime.str_to_user(t.date)}</td><td class="num">${t.annonce}</td><td>${esc(t.employe || "")}</td><td><a href="/app/tache-de-travail/${encodeURIComponent(t.tache)}">${esc(t.client || t.tache)}</a></td><td>${esc(t.type || "")}</td><td><span class="st-etape ${cls}">${esc(t.passage)}</span></td><td class="num">${t.arrivee}</td><td class="num">${t.depart}</td><td class="num">${t.duree != null ? this._min(t.duree) : ""}</td><td class="num">${t.planifie != null ? this._min(t.planifie) : ""}</td><td>${t.ecart != null ? this._ecart(t.ecart, s.tolerance) : ""}</td></tr>`;
      }
      html += `</tbody></table>`;
    }
    if (s.manquants.length) {
      html += `<div class="st-section">Clôturées sans passage GPS à l’adresse (à vérifier)</div><table class="st-t"><thead><tr><th>Date</th><th>Tâche</th><th>Employé</th><th>Client</th><th>Type</th><th>Véhicule</th></tr></thead><tbody>`;
      for (const m of s.manquants.slice().reverse()) html += `<tr><td>${esc(m.date)}</td><td><a href="/app/tache-de-travail/${encodeURIComponent(m.tache)}">${esc(m.tache)}</a></td><td>${esc(m.employe || "")}</td><td>${esc(m.client || "")}</td><td>${esc(m.type)}</td><td>${esc(m.vehicule || "")}</td></tr>`;
      html += `</tbody></table>`;
    }
    $out.html(html);
    const types = s.types.filter((t) => t.confirmes);
    if (types.length && window.frappe.Chart) {
      new frappe.Chart("#st-graphe", { type: "bar", height: 200, colors: ["#94a3b8", "#2563eb", "#16a34a"],
        data: { labels: types.map((t) => `${t.type} (${t.confirmes})`),
          datasets: [{ name: "Standard (planning)", values: types.map((t) => t.standard || 0) },
                     { name: "Réel moyen", values: types.map((t) => t.duree.moyenne || 0) },
                     { name: "Réel médian", values: types.map((t) => t.duree.mediane || 0) }] },
        tooltipOptions: { formatTooltipY: (v) => v + " min" } });
    }
    $out.find("[data-action='csv']").on("click", () => this._csv());
  }

  _csv() {
    const s = this._stats_data; if (!s) return;
    const cols = ["date", "annonce", "fin_annoncee", "employe", "client", "type", "statut", "passage", "vehicule", "arrivee", "depart", "duree", "planifie", "ecart", "tache"];
    const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, "\"\"")}"`;
    const lignes = [cols.join(";")].concat(s.detail.map((t) => cols.map((c) => q(t[c])).join(";")));
    const blob = new Blob(["\ufeff" + lignes.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `passages_gps_${s.du}_${s.au}.csv`; a.click();
  }
}
