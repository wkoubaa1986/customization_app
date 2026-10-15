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
  "non repérée": ["autre", "❔ non repérée"],
};
// Le statut de la FICHE (ouverte / clôturée / annulée), à côté de l'étape GPS : « passée » ne dit pas si le technicien a clôturé.
const ST_STATUT = { Open: ["ouverte", "🔓 ouverte"], Completed: ["cloturee", "✔ clôturée"], Cancelled: ["annulee", "✖ annulée"] };
const ST_RAFRAICHIR_S = 60;
const ST_RAYON_SECTEUR_M = 4300;     // 3 clients sur 4 sont à moins de 4,3 km du centre de leur secteur (mesuré le 12/10/2026)
const ST_COULEURS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#be185d", "#4d7c0f"];
const ST_FOND = { "passée": "#16a34a", "clôturée": "#16a34a", "sur place": "#eab308", "en route": "#2563eb", "à venir": "#60a5fa",
  "en retard": "#dc2626", "sans passage": "#dc2626", "clôturée sans passage": "#dc2626", "annulée": "#9ca3af", "non repérée": "#9ca3af" };

class SuiviTerrain {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".st-page");
    this.timer = null;
    this.data = null;
    // Direction (Wassim, Néjib) : statistiques, appariement manuel, reprise, réglages. Les autres : le suivi live seulement.
    this.direction = frappe.user.has_role("Direction Suivi Terrain");
    if (!this.direction) this.$root.find(".st-tab[data-tab='stats'], [data-action='apparier'], [data-action='reglages'], [data-action='backfill']").hide();
    // Sorties de circuit (arrêts inexpliqués, hors horaires, détours) : Wassim et Néjib seulement — rôle « Sortie Circuit ».
    if (!frappe.user.has_role("Sortie Circuit")) this.$root.find(".st-tab[data-tab='circuit']").hide();
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
      if (t === "circuit" && !this.circuit) {
        frappe.require("/assets/customization_app/js/sortie_circuit.js", () => {
          const $p = $r.find(".st-panel[data-panel='circuit']");
          $p.html(SortieCircuit.html());
          this.circuit = new SortieCircuit($p[0]);
        });
      }
    });
    // Demande du 12/10/2026 : une tâche s'ouvre en fenêtre sans quitter le suivi, et chaque adresse
    // mène à l'intervention sur la carte de la page (Google Maps si la position n'est pas connue).
    $r.on("click", "[data-tache]", (e) => {
      // Un lien À L'INTÉRIEUR de la tâche (adresse, Google Maps, copie) garde son propre rôle.
      const lien = $(e.target).closest("[data-carte], [data-copie], a[href]")[0];
      if (lien && lien !== e.currentTarget) return;
      e.preventDefault();
      this.ouvrir_tache($(e.currentTarget).attr("data-tache"));
    });
    $r.on("click", "[data-carte]", (e) => {
      e.preventDefault(); e.stopPropagation();
      this.voir_sur_carte($(e.currentTarget).attr("data-carte"));
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
          else if (t.eta) { reel = `arrivée estimée ${t.eta_approx ? "<span title=\"Position approchée : heure indicative, aucun SMS\">≈ </span>" : ""}${this._h(t.eta)} ${this._ecart(t.ecart_prevu, d.tolerance)}${t.route_min ? ` <span class="st-dim">(${t.route_min} min de route)</span>` : ""}`; }
          if ((t.pistes || []).length) reel += `${reel ? "<br>" : ""}${this._pistes_resume(t)}`;
          const prevenu = (t.messages || []).filter((m) => !["Alerte interne", "Non faite"].includes(m.type)).map((m) => `📨 ${m.type} ${this._h(m.heure)}${m.statut === "Simulé" ? " (simulé)" : m.statut === "Échec" ? " ❌" : ""}`).join(", ");
          html += `<tr><td class="num">${this._h(t.debut)}${t.fin ? "–" + this._h(t.fin) : ""}</td>
            <td class="st-ouvrir" data-tache="${esc(t.name)}" title="Ouvrir la tâche"><span class="st-client">${esc(t.client || t.titre || t.name)}</span><br><span class="st-dim">${esc(t.type)}${t.secteur ? " · " + esc(t.secteur) : ""}${t.position_src && t.position_src !== "tâche" ? " · position " + esc(t.position_src) : ""}</span>${this._adresse(t)}</td>
            <td><span class="st-etape ${ec}">${el}</span>${this._statut(t)}</td><td>${reel} <a href="#" class="st-dim" data-copie="${esc(this._phrase(e, t, d))}" title="Copier la phrase pour le client">📋</a></td><td class="num">${duree}${prevenu ? `<br><span class="st-dim">${prevenu}</span>` : ""}</td></tr>`;
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

  // ── Tâche : adresse cliquable, carte, fenêtre ─────────────────────────────
  _trouver(nom) {
    for (const e of (this.data && this.data.employes) || []) {
      const t = e.taches.find((x) => x.name === nom);
      if (t) return { e, t };
    }
    return {};
  }

  /** Google Maps : la position de la tâche, sinon son lien enregistré, sinon une recherche sur l'adresse. */
  _lien_maps(t) {
    if (t.lat && t.lng) return `https://www.google.com/maps/search/?api=1&query=${t.lat},${t.lng}`;
    if (/^https?:\/\//.test(t.google_map || "")) return t.google_map;
    const q = [t.adresse, t.secteur].filter(Boolean).join(", ");
    return q ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}` : "";
  }

  /** Ligne d'adresse sous le client : vers la carte de la page si la position est connue, sinon Google Maps. */
  _adresse(t) {
    const esc = frappe.utils.escape_html, txt = (t.adresse || "").replace(/\s+/g, " ").trim();
    if (t.etape === "annulée") return "";
    if (t.lat && t.lng) return `<br><a href="#" class="st-adr" data-carte="${esc(t.name)}" title="Voir l’intervention sur la carte">📍 ${esc(txt || "voir sur la carte")}</a>`;
    if (t.approx) return `<br><a href="#" class="st-adr" data-carte="${esc(t.name)}" title="Position approchée : voir où elle tombe sur la carte">📍≈ ${esc(txt || "voir sur la carte")}</a>`;
    const lien = this._lien_maps(t);
    return lien ? `<br><a href="${esc(lien)}" class="st-adr" target="_blank" rel="noopener" title="Position exacte inconnue : ouvrir dans Google Maps">🌐 ${esc(txt || "Google Maps")} ↗</a>` : "";
  }

  /** Tâche sans position : les arrêts du véhicule qui POURRAIENT être l'intervention (le GPS ne tranche pas). */
  _statut(t) {
    const [cls, lib] = ST_STATUT[t.statut] || ["autre", t.statut || ""];
    return lib ? `<br><span class="st-statut ${cls}">${lib}</span>` : "";
  }

  _pistes_resume(t) {
    const p = t.pistes || [], esc = frappe.utils.escape_html;
    const encours = p.find((x) => x.en_cours), photo = p.find((x) => x.photo === "pendant");
    const txt = encours ? `🅿️ arrêté depuis ${this._h(encours.arrivee)} — chez ce client ?`
      : photo ? `📸 photos envoyées pendant l’arrêt de ${this._h(photo.arrivee)} — c’est là ?`
      : `🅿️ ${p.length} arrêt${p.length > 1 ? "s" : ""} possible${p.length > 1 ? "s" : ""}`;
    return `<a href="#" class="st-dim st-pistes" data-tache="${esc(t.name)}" title="Arrêts non identifiés autour du créneau : ouvrir pour choisir le bon">${txt}</a>`;
  }

  montrer_point(lat, lng, html) {
    if (!this.carte_visible) {
      this.carte_visible = true;
      try { localStorage.setItem("st_carte", "1"); } catch (e) { /* ignoré */ }
      this._render_carte();
    }
    if (!this.carte) return;
    if (!this.point_temp) this.point_temp = L.layerGroup().addTo(this.carte);
    this.point_temp.clearLayers();
    const m = L.marker([lat, lng], { zIndexOffset: 2000, icon: L.divIcon({ className: "", iconSize: [28, 28], iconAnchor: [14, 14],
      html: `<div style="width:28px;height:28px;border-radius:14px;background:#fff;border:3px dashed #7c3aed;display:flex;align-items:center;justify-content:center;font-size:14px">🅿️</div>` }) })
      .bindPopup(html).addTo(this.point_temp);
    this.$root.find("#st-carte")[0].scrollIntoView({ behavior: "smooth", block: "center" });
    this.carte.invalidateSize();
    this.carte.setView([lat, lng], 16);
    m.openPopup();
  }

  fixer_position(t, p, dlg) {
    frappe.confirm(`Enregistrer l’arrêt de <b>${this._h(p.arrivee)}–${this._h(p.depart)}</b> comme position de <b>${frappe.utils.escape_html(t.client || t.name)}</b> ?
      <br><span class="text-muted">Le passage sera reconnu avec ses heures réelles, et l’adresse du client gardera cette position pour les prochaines fois.</span>`, async () => {
      await frappe.call({ method: "customization_app.flotte_gps.fixer_position", args: { tache: t.name, lat: p.lat, lng: p.lng }, freeze: true });
      if (dlg) dlg.hide();
      frappe.show_alert({ message: `📍 Position enregistrée pour ${frappe.utils.escape_html(t.client || t.name)}`, indicator: "green" }, 5);
      if (this.point_temp) this.point_temp.clearLayers();
      this.charger();
    });
  }

  /** D'où vient une position approchée — à dire tel quel, sans fausse précision. */
  _origine_approx(t) {
    if (t.approx_src === "secteur") return `≈ centre du ${frappe.utils.escape_html(t.secteur || "secteur")} : aucune position connue pour cette adresse (3 clients sur 4 du secteur sont à moins de 4,3 km de ce point, cercle pointillé)`;
    if (t.approx_src === "grossiere") return "≈ position réseau du téléphone (bouton « Ma position » sans GPS précis) : à 1 à 3 km du client — choisissez le bon arrêt ci-dessous pour corriger le lien";
    return "≈ lue dans le texte de l’adresse par OpenStreetMap : la rue si elle est connue, sinon le quartier ou la ville — pas de lien Google Maps";
  }

  voir_sur_carte(nom) {
    const { t } = this._trouver(nom);
    if (!t) return;
    if (!this.carte_visible) {
      this.carte_visible = true;
      try { localStorage.setItem("st_carte", "1"); } catch (e) { /* ignoré */ }
      this._render_carte();
    }
    const m = this.marqueurs && this.marqueurs[nom];
    if (!m) { window.open(this._lien_maps(t), "_blank", "noopener"); return; }
    const el = this.$root.find("#st-carte")[0];
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    this.carte.invalidateSize();
    this.carte.setView(m.getLatLng(), t.lat ? 17 : t.approx_src === "secteur" ? 12 : 15);
    m.openPopup();
  }

  _pistes_fenetre(t) {
    if (!t.gps_live || !["Sans position", "Aucun passage"].includes(t.gps_live.statut)) return "";
    const p = t.pistes || [], esc = frappe.utils.escape_html, modif = frappe.model.can_write("Tache de travail");
    const sans_passage = t.gps_live.statut === "Aucun passage";
    let html = `<div class="st-pistes-bloc"><div class="t">🅿️ Arrêts possibles autour du créneau</div>
      <div class="st-dim" style="margin-bottom:6px">${p.some((x) => x.photo) ? "Le technicien envoie ses photos de clôture chez le client : l’arrêt marqué 📸 est le plus probable (juste 4 fois sur 5) — vérifiez sur la carte avant de confirmer. " : ""}${sans_passage
        ? "Le véhicule ne s’est pas arrêté à moins de " + esc(String(this.data.rayon)) + " m du lien de la tâche : le lien est peut-être faux. Si l’un de ces arrêts est le client, « C’est ici » corrige le lien et l’adresse."
        : "Sans la position exacte du client, le GPS ne peut pas savoir lequel est le bon : regardez sur la carte et confirmez."}</div>`;
    if (!p.length) return html + `<div class="st-dim">Aucun arrêt non identifié de plus de 5 min entre 3 h avant et 5 h après l’heure annoncée.</div></div>`;
    const ref = t.approx ? (t.approx_src === "grossiere" ? "du point réseau" : t.approx_src === "secteur" ? "du secteur" : "de l’adresse lue") : "du lien";
    html += p.map((x, i) => `<div class="st-piste">
        <span><b>${this._h(x.arrivee)}–${this._h(x.depart)}</b> · ${this._min(x.duree)}${x.en_cours ? " · <b>en cours</b>" : ""}
          ${x.photo === "pendant" ? `<br><b>📸 photos de clôture envoyées pendant cet arrêt</b>` : x.photo === "juste avant" ? `<br>📸 photos de clôture envoyées ${x.photo_min} min après cet arrêt` : ""}
          <span class="st-dim">${x.ecart > 0 ? "+" : ""}${x.ecart} min / annoncé${x.km_approx != null ? ` · à ${String(x.km_approx).replace(".", ",")} km ${ref}` : ""}</span></span>
        <span class="st-piste-btns"><button class="btn btn-default btn-xs" data-piste-voir="${i}">🗺 voir</button>
          ${modif ? `<button class="btn btn-primary btn-xs" data-piste-ici="${i}">📌 C’est ici</button>` : ""}</span></div>`).join("");
    return html + `</div>`;
  }

  ouvrir_tache(nom) {
    const { e, t } = this._trouver(nom), d = this.data, esc = frappe.utils.escape_html;
    if (!t) { window.open(`/app/tache-de-travail/${encodeURIComponent(nom)}`, "_blank"); return; }
    const [ec, el] = ST_ETAPE[t.etape] || ["autre", t.etape];
    let reel = "";
    if (t.etape === "passée") reel = `${this._h(t.gps_live.arrivee)} → ${this._h(t.gps_live.depart)} ${this._ecart(t.gps_live.ecart, d.tolerance)} · ${this._min(t.gps_live.duree)} sur place`;
    else if (t.etape === "sur place") reel = `arrivé ${this._h(t.arrivee_reelle)}${t.eta_depart ? ", fin estimée " + this._h(t.eta_depart) : ""}`;
    else if (t.eta) reel = `arrivée estimée ${this._h(t.eta)} ${this._ecart(t.ecart_prevu, d.tolerance)}${t.route_min ? ` (${t.route_min} min de route)` : ""}`;
    const ligne = (ico, lbl, val) => (val ? `<div class="st-pop-l"><span class="st-pop-k">${ico} ${lbl}</span><span>${val}</span></div>` : "");
    const lien = this._lien_maps(t), adresse = (t.adresse || "").replace(/\s+/g, " ").trim();
    const sms = (t.messages || []).map((m) => `${esc(m.type)} ${this._h(m.heure)} (${esc(m.statut)})`).join("<br>");
    const phrase = this._phrase(e, t, d);
    const dlg = new frappe.ui.Dialog({
      title: `${esc(t.client || t.titre || t.name)}`, size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "corps" }],
      primary_action_label: "📄 Ouvrir la fiche complète",
      primary_action: () => window.open(`/app/tache-de-travail/${encodeURIComponent(t.name)}`, "_blank"),
    });
    dlg.fields_dict.corps.$wrapper.html(`<div class="st-pop">
      ${ligne("🗂", "Tâche", `${esc(t.type)}${!t.client && t.titre ? " · " + esc(t.titre) : ""} <span class="st-dim">${esc(t.name)}</span>`)}
      ${ligne("🕘", "Annoncé", `${this._h(t.debut)}${t.fin ? "–" + this._h(t.fin) : ""}`)}
      ${ligne("👷", "Technicien", `${esc(e.nom)}${e.vehicule ? ` <span class="st-dim">${esc(e.vehicule)}</span>` : ""}`)}
      ${ligne("🚦", "Étape", `<span class="st-etape ${ec}">${el}</span> ${this._statut(t).replace("<br>", "")}${reel ? " · " + reel : ""}`)}
      ${ligne("📞", "Téléphone", t.tel ? `<a href="tel:${esc(String(t.tel).replace(/\s+/g, ""))}">${esc(t.tel)}</a>` : "")}
      ${ligne("🏠", "Adresse", `${esc(adresse || "—")}${t.secteur ? ` <span class="st-dim">· ${esc(t.secteur)}</span>` : ""}${t.position_src && t.position_src !== "tâche" ? ` <span class="st-dim">· position ${esc(t.position_src)}</span>` : ""}${t.approx ? `<br><span class="st-dim">${this._origine_approx(t)}</span>` : ""}`)}
      ${/^https?:\/\//.test(t.google_map || "") ? ligne("🔗", "Lien Maps", `<a href="${esc(t.google_map)}" target="_blank" rel="noopener" title="${esc(t.google_map)}" class="st-pop-url">${esc(t.google_map.replace(/^https?:\/\/(www\.)?/, ""))} ↗</a>`) : ""}
      ${ligne("🧾", "Commande", t.commande ? `<a href="/app/sales-order/${encodeURIComponent(t.commande)}" target="_blank">${esc(t.commande)}</a>` : "")}
      ${ligne("📨", "Messages", sms)}
      ${this._pistes_fenetre(t)}
      <div class="st-msg" data-copie="${esc(phrase)}" title="Cliquer pour copier" style="margin:10px 0 0">💬 ${esc(phrase)}</div>
      <div class="st-pop-btns">
        ${(t.lat && t.lng) || t.approx ? `<button class="btn btn-default btn-sm" data-pop="carte">🗺 Voir sur la carte${t.lat ? "" : " (≈)"}</button>` : ""}
        ${lien ? `<a class="btn btn-default btn-sm" href="${esc(lien)}" target="_blank" rel="noopener">🌐 Google Maps ↗</a>` : ""}
      </div></div>`);
    dlg.fields_dict.corps.$wrapper.on("click", "[data-pop='carte']", () => { dlg.hide(); this.voir_sur_carte(t.name); });
    dlg.fields_dict.corps.$wrapper.on("click", "[data-piste-voir]", (ev) => {
      const p = t.pistes[+$(ev.currentTarget).attr("data-piste-voir")];
      dlg.hide();
      this.montrer_point(p.lat, p.lng, `<b>🅿️ Arrêt ${this._h(p.arrivee)}–${this._h(p.depart)}</b> (${this._min(p.duree)})<br>${esc(e.nom)} · piste pour ${esc(t.client || t.name)}`
        + `<br><a href="https://www.google.com/maps?q=${p.lat},${p.lng}" target="_blank" rel="noopener">Google Maps ↗</a>`);
    });
    dlg.fields_dict.corps.$wrapper.on("click", "[data-piste-ici]", (ev) => this.fixer_position(t, t.pistes[+$(ev.currentTarget).attr("data-piste-ici")], dlg));
    dlg.fields_dict.corps.$wrapper.on("click", "[data-copie]", (ev) => frappe.utils.copy_to_clipboard($(ev.currentTarget).attr("data-copie")));
    dlg.show();
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
      const estime = t.etape === "sautée" ? `<span class="st-ecart retard">⛔ non faite, une suivante déjà faite</span>` : `${t.eta_approx ? "≈ " : ""}${this._h(t.eta)}</td><td>${this._ecart(t.ecart_prevu, d.tolerance)}`;
      html += `<tr><td class="st-client st-ouvrir" data-tache="${esc(t.name)}" title="Ouvrir la tâche">${esc(t.client || t.name)}</td><td>${esc(t.tel || "")}</td><td>${esc(e.nom)}</td><td class="num">${this._h(t.debut)}</td><td class="num">${estime}</td><td>${prevenu}</td><td><a href="#" data-copie="${esc(this._phrase(e, t, d))}" title="Copier la phrase">📋</a></td></tr>`;
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
      html += `<div class="st-dim" style="margin-top:4px">${extra.simulation ? "🧪 developer_mode : tout est simulé, rien ne part." : "Envoi réel (production)."}${extra.sms_ok === false ? ` · SMS clients éteints jusqu’au ${frappe.datetime.str_to_user(extra.sms_date_debut)} (réglage).` : ""}</div></div>`;
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
    this.marqueurs = {};
    const bornes = [], bornes_approx = [];
    const pt = (lat, lng) => { bornes.push([lat, lng]); return [lat, lng]; };
    // Une position approchée (livraison hors secteur à Djerba…) ne doit pas élargir le cadrage à tout le pays.
    const pt_approx = (lat, lng) => { bornes_approx.push([lat, lng]); return [lat, lng]; };
    const depots = new Set();
    // Plusieurs interventions au même endroit (même lien Google Maps, même immeuble) : les pastilles se
    // décalent côte à côte — sinon la dernière cache les autres (n°7 « à venir » sur n°2 « passée », 12/10/2026).
    const occupes = {};
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
        const exact = t.lat && t.lng, ap = !exact && t.approx;
        if (!(exact || ap) || t.etape === "annulée") return;
        n++;
        const [lat, lng] = exact ? [t.lat, t.lng] : t.approx;
        const fond = ST_FOND[t.etape] || "#6b7280", approx = ap || (t.position_src && t.position_src !== "tâche");
        const cle = `${lat.toFixed(4)},${lng.toFixed(4)}`, rang = occupes[cle] = (occupes[cle] || 0) + 1;
        if (ap && t.approx_src === "secteur") {
          L.circle([lat, lng], { radius: ST_RAYON_SECTEUR_M, color: c, weight: 1, dashArray: "4 6", fillOpacity: 0.04, interactive: false }).addTo(this.couches);
        }
        let detail = `${this._h(t.debut)} annoncé`;
        if (t.etape === "passée") detail += ` · réel ${this._h(t.gps_live.arrivee)} → ${this._h(t.gps_live.depart)} (${this._min(t.gps_live.duree)})`;
        else if (t.etape === "sur place") detail += ` · sur place depuis ${this._h(t.arrivee_reelle)}`;
        else if (t.eta) detail += ` · arrivée estimée ${this._h(t.eta)}`;
        this.marqueurs[t.name] = L.marker(ap ? pt_approx(lat, lng) : pt(lat, lng), { icon: L.divIcon({ className: "", iconSize: [24, 24], iconAnchor: [12 - 22 * (rang - 1), 12],
          html: `<div style="width:24px;height:24px;border-radius:12px;background:${fond};${ap ? "opacity:.75;" : ""}color:#fff;font-weight:700;font-size:${ap ? 10 : 11}px;display:flex;align-items:center;justify-content:center;border:3px ${approx ? "dashed" : "solid"} ${c};box-shadow:0 0 0 1px #fff">${ap ? "≈" : ""}${n}</div>` }) })
          .bindTooltip(`${n} · ${esc(t.client || t.titre || t.name)} <span style="color:#64748b">${this._h(t.debut)}${ap ? " · ≈ position approchée" : ""}</span>`, { direction: "right", offset: [12, 0] })
          .bindPopup(`<b>${n} · ${esc(t.client || t.titre || t.name)}</b><br>${esc(e.nom)} · ${esc(t.type)} · <b>${esc(t.etape)}</b><br>${esc(detail)}${ap ? `<br>${this._origine_approx(t)}` : approx ? "<br>≈ position approchée" : ""}<br><a href="#" data-tache="${esc(t.name)}">ouvrir la tâche</a> · <a href="${esc(this._lien_maps(t))}" target="_blank" rel="noopener">Google Maps ↗</a>`)
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
    if (!bornes.length) bornes.push(...bornes_approx);
    if (bornes.length && !this.carte_cadree) { this.carte.fitBounds(bornes, { padding: [30, 30] }); this.carte_cadree = d.jour; }
    else if (bornes.length && this.carte_cadree !== d.jour) { this.carte.fitBounds(bornes, { padding: [30, 30] }); this.carte_cadree = d.jour; }
    setTimeout(() => this.carte && this.carte.invalidateSize(), 50);
  }

  // ── Statistiques ──────────────────────────────────────────────────────────
  // ── Analyse : distributions, comparaisons, granularité, d'où vient le retard (15/10/2026) ───────────────────────
  _render_analyse() {
    const s = this._stats_data, esc = frappe.utils.escape_html, $a = this.$root.find("#st-analyse");
    if (!s || !(s.lignes || []).length) return;
    const L = s.lignes, tol = s.tolerance;
    const o = this._analyse_opts = this._analyse_opts || { gran: "semaine", metrique: "aheure", emps: null };
    const noms = {}; L.forEach((l) => { noms[l.e] = l.n; });
    const tous = Object.keys(noms).filter((e) => L.some((l) => l.e === e && l.st === "Passage confirmé")).sort((a, b) => (noms[a] || "").localeCompare(noms[b] || ""));
    if (!o.emps) o.emps = tous.slice();
    const sel = tous.filter((e) => o.emps.includes(e));
    const med = (v) => { if (!v.length) return null; const t = v.slice().sort((a, b) => a - b), m = Math.floor(t.length / 2); return t.length % 2 ? t[m] : Math.round((t[m - 1] + t[m]) / 2); };
    const pctl = (v, q) => { if (!v.length) return null; const t = v.slice().sort((a, b) => a - b); return t[Math.min(t.length - 1, Math.floor(q * t.length))]; };
    const pc = (n, d) => (d ? Math.round((n / d) * 100) + " %" : "–");
    const signe = (m) => (m == null ? "–" : (m > 0 ? "+" : "") + m + " min");
    const conf = (l) => l.st === "Passage confirmé" && l.ec != null;
    const aheure = (l) => Math.abs(l.ec) <= tol;
    const couleur = (v, bon, mauvais) => v == null ? "" : v >= bon ? "ok" : v <= mauvais ? "retard" : "";
    const METRIQUES = { aheure: "% à l'heure (± " + tol + " min)", retard: "% en retard de plus de " + tol + " min", ecart: "écart médian (min)", p90: "écart P90 (min)", confirme: "% passage confirmé", duree: "durée médiane sur place (min)", taches: "tâches clôturées", parjour: "tâches par jour travaillé" };
    const mesure = (ls) => {
      const c = ls.filter(conf), ec = c.map((l) => l.ec), du = c.filter((l) => l.du != null).map((l) => l.du);
      const jours = new Set(ls.map((l) => l.d)).size;
      switch (o.metrique) {
        case "aheure": return c.length ? Math.round(100 * c.filter(aheure).length / c.length) : null;
        case "retard": return c.length ? Math.round(100 * c.filter((l) => l.ec > tol).length / c.length) : null;
        case "ecart": return med(ec);
        case "p90": return pctl(ec, 0.9);
        case "confirme": return ls.length ? Math.round(100 * ls.filter((l) => l.st === "Passage confirmé").length / ls.length) : null;
        case "duree": return med(du);
        case "taches": return ls.length;
        case "parjour": return jours ? Math.round(10 * ls.length / jours) / 10 : null;
      }
    };
    const fmt = (v) => (v == null ? "–" : ["aheure", "retard", "confirme"].includes(o.metrique) ? v + " %" : ["ecart", "p90"].includes(o.metrique) ? signe(v) : String(v).replace(".", ","));
    const bonne = (v) => (v == null ? "" : o.metrique === "aheure" || o.metrique === "confirme" ? couleur(v, 60, 35) : o.metrique === "retard" ? couleur(-v, -25, -50) : o.metrique === "ecart" || o.metrique === "p90" ? couleur(-Math.abs(v), -tol, -2 * tol) : "");
    let html = `<div class="st-section">🔎 Analyse <span class="st-dim">— tâches clôturées appariées de la période ; ponctualité et durées sur les passages confirmés seulement</span></div>
      <div class="st-bar" style="margin:4px 0 8px">
        <label class="st-dim">Période</label><select id="st-an-gran"><option value="semaine"${o.gran === "semaine" ? " selected" : ""}>par semaine</option><option value="mois"${o.gran === "mois" ? " selected" : ""}>par mois</option><option value="jsem"${o.gran === "jsem" ? " selected" : ""}>par jour de la semaine</option></select>
        <label class="st-dim">Mesure</label><select id="st-an-metrique">${Object.entries(METRIQUES).map(([k, v]) => `<option value="${k}"${o.metrique === k ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>
        <span class="st-dim">Techniciens :</span> ${tous.map((e) => `<label style="margin:0 6px 0 0;font-weight:400"><input type="checkbox" data-an-emp="${esc(e)}"${sel.includes(e) ? " checked" : ""}> ${esc(noms[e])}</label>`).join("")}
      </div>`;
    // F. Qualité de mesure : sur quoi reposent les chiffres de chacun
    html += `<div class="st-section" style="font-size:13px">Qualité de la mesure <span class="st-dim">— un taux de retard calculé sur 40 % de passages confirmés ne vaut pas celui calculé sur 90 %</span></div>
      <table class="st-t"><thead><tr><th>Technicien</th><th class="num">Tâches</th><th class="num">Passage confirmé</th><th class="num">Sans position</th><th class="num">Aucun passage</th><th class="num">Pas de véhicule</th><th class="num">Mesure fiable ?</th></tr></thead><tbody>`;
    for (const e of tous) {
      const ls = L.filter((l) => l.e === e), n = ls.length, c = ls.filter((l) => l.st === "Passage confirmé").length;
      const cpt = (st) => ls.filter((l) => l.st === st).length, taux = n ? Math.round(100 * c / n) : 0;
      html += `<tr><td>${esc(noms[e])}</td><td class="num">${n}</td><td class="num"><span class="st-ecart ${couleur(taux, 70, 45)}">${pc(c, n)}</span> <span class="st-dim">(${c})</span></td><td class="num">${pc(cpt("Sans position"), n)}</td><td class="num">${pc(cpt("Aucun passage"), n)}</td><td class="num">${pc(cpt("Pas de véhicule"), n)}</td><td class="num">${taux >= 70 ? "✅ oui" : taux >= 45 ? "⚠️ partielle" : "❌ non"}</td></tr>`;
    }
    html += `</tbody></table>`;
    // B. Distribution des écarts
    const TR = [["< −60", -1e9, -60], ["−60 / −30", -60, -30], ["−30 / −10", -30, -10], ["−10 / +10", -10, 10], ["+10 / +30", 10, 30], ["+30 / +60", 30, 60], ["+60 / +120", 60, 120], ["> +120", 120, 1e9]];
    html += `<div class="st-section" style="font-size:13px">Distribution des écarts à l'heure annoncée <span class="st-dim">— en % des passages confirmés de chacun · négatif = en avance</span></div>
      <table class="st-t"><thead><tr><th>Technicien</th><th class="num">Passages</th>${TR.map((t) => `<th class="num">${t[0]}</th>`).join("")}<th class="num">Médiane</th><th class="num">P90</th><th class="num">À l'heure</th></tr></thead><tbody>`;
    const lignesDist = sel.map((e) => [noms[e], L.filter((l) => l.e === e && conf(l))]).concat([["Tous", L.filter((l) => sel.includes(l.e) && conf(l))]]);
    for (const [nom, c] of lignesDist) {
      if (!c.length) continue;
      const ec = c.map((l) => l.ec);
      html += `<tr><td>${nom === "Tous" ? "<b>Tous</b>" : esc(nom)}</td><td class="num">${c.length}</td>`;
      for (const [, a, b] of TR) {
        const n = ec.filter((x) => x >= a && x < b).length, p = Math.round(100 * n / ec.length);
        const fond = a >= -10 && b <= 10 ? "rgba(22,163,74," + (0.15 + p / 100) + ")" : a >= 30 ? "rgba(220,38,38," + (0.1 + p / 60) + ")" : b <= -30 ? "rgba(37,99,235," + (0.1 + p / 60) + ")" : "transparent";
        html += `<td class="num" style="background:${fond}">${p ? p + " %" : ""}</td>`;
      }
      html += `<td class="num">${signe(med(ec))}</td><td class="num">${signe(pctl(ec, 0.9))}</td><td class="num"><b>${pc(c.filter(aheure).length, c.length)}</b></td></tr>`;
    }
    html += `</tbody></table>`;
    // C. Comparaison période × technicien
    const JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
    const cle = (l) => o.gran === "mois" ? l.d.slice(0, 7) : o.gran === "jsem" ? String(l.jsem) : `${l.a}-S${String(l.s).padStart(2, "0")}`;
    const lib = (k) => o.gran === "mois" ? frappe.datetime.str_to_obj(k + "-01").toLocaleDateString("fr-FR", { month: "long", year: "numeric" }) : o.gran === "jsem" ? JOURS[+k] : "semaine " + k.split("-S")[1] + " <span class='st-dim'>" + k.split("-S")[0] + "</span>";
    const periodes = [...new Set(L.filter((l) => sel.includes(l.e)).map(cle))].sort((a, b) => (o.gran === "jsem" ? +a - +b : b.localeCompare(a)));
    html += `<div class="st-section" style="font-size:13px">Comparaison ${o.gran === "jsem" ? "par jour de la semaine" : o.gran === "mois" ? "mois par mois" : "semaine par semaine"} — ${esc(METRIQUES[o.metrique])}</div>
      <table class="st-t"><thead><tr><th>Période</th>${sel.map((e) => `<th class="num">${esc(noms[e])}</th>`).join("")}<th class="num">Tous</th></tr></thead><tbody>`;
    for (const k of periodes) {
      const lp = L.filter((l) => cle(l) === k);
      html += `<tr><td>${lib(k)}</td>${sel.map((e) => { const v = mesure(lp.filter((l) => l.e === e)); return `<td class="num"><span class="st-ecart ${bonne(v)}">${fmt(v)}</span></td>`; }).join("")}<td class="num"><b>${fmt(mesure(lp.filter((l) => sel.includes(l.e))))}</b></td></tr>`;
    }
    html += `</tbody></table>`;
    // D. D'où vient le retard : heure annoncée, rang dans la tournée, type
    const cellule = (ls) => { const c = ls.filter(conf); if (!c.length) return `<td class="num st-dim">–</td>`; const m = med(c.map((l) => l.ec)), a = Math.round(100 * c.filter(aheure).length / c.length); return `<td class="num"><span class="st-ecart ${couleur(-Math.abs(m), -tol, -2 * tol)}">${signe(m)}</span> <span class="st-dim">· ${a} % · ×${c.length}</span></td>`; };
    const bloc = (titre, cats, f) => {
      let h = `<div class="st-section" style="font-size:13px">${titre} <span class="st-dim">(écart médian · % à l'heure · passages)</span></div><table class="st-t"><thead><tr><th></th>${sel.map((e) => `<th class="num">${esc(noms[e])}</th>`).join("")}<th class="num">Tous</th></tr></thead><tbody>`;
      for (const [libc, test] of cats) {
        const lc = L.filter((l) => sel.includes(l.e) && test(l));
        if (!lc.some(conf)) continue;
        h += `<tr><td>${libc}</td>${sel.map((e) => cellule(lc.filter((l) => l.e === e))).join("")}${cellule(lc)}</tr>`;
      }
      return h + `</tbody></table>`;
    };
    html += bloc("D'où vient le retard — par heure annoncée", [["avant 9 h", (l) => l.h < 9], ["9 h – 10 h", (l) => l.h === 9], ["10 h – 11 h", (l) => l.h === 10], ["11 h – 12 h", (l) => l.h === 11], ["12 h – 14 h", (l) => l.h >= 12 && l.h < 14], ["14 h – 15 h", (l) => l.h === 14], ["15 h – 16 h", (l) => l.h === 15], ["16 h – 17 h", (l) => l.h === 16], ["17 h et après", (l) => l.h >= 17]]);
    html += bloc("Par rang dans la tournée du jour", [["1re tâche du jour", (l) => l.r === 1], ["2e", (l) => l.r === 2], ["3e", (l) => l.r === 3], ["4e", (l) => l.r === 4], ["5e et plus", (l) => l.r >= 5]]);
    html += bloc("Par type d'intervention", [...new Set(L.map((l) => l.t))].sort().map((t) => [esc(t), (l) => l.t === t]));
    html += `<div class="st-dim" style="margin-top:4px">Lecture : si l'écart grandit avec l'heure ou le rang, ce sont les durées planifiées ou les trajets qui sont trop courts ; s'il est déjà là à la 1re tâche, c'est l'heure de départ.</div>`;
    $a.html(html);
    $a.find("#st-an-gran").on("change", (e) => { o.gran = e.target.value; this._render_analyse(); });
    $a.find("#st-an-metrique").on("change", (e) => { o.metrique = e.target.value; this._render_analyse(); });
    $a.find("[data-an-emp]").on("change", () => { o.emps = $a.find("[data-an-emp]:checked").map((i, x) => $(x).attr("data-an-emp")).get(); this._render_analyse(); });
  }

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
    // Par mois et par employé (15/10/2026) : l'évolution, mois après mois.
    if ((s.par_mois || []).length) {
      html += `<div class="st-section">Par mois et par employé <span class="st-dim">(tâches clôturées appariées · km des journées GPS)</span></div>
        <table class="st-t"><thead><tr><th>Mois</th><th>Employé</th><th class="num">Tâches</th><th class="num">Confirmées</th><th class="num">Sans passage</th><th class="num">À l’heure</th><th class="num">Avance</th><th class="num">Retard</th><th class="num">Durée moy.</th><th class="num">Écart moy.</th><th class="num">Jours GPS</th><th class="num">Km</th><th class="num">Km / jour</th></tr></thead><tbody>`;
      let mois = null;
      for (const m of s.par_mois) {
        if (!m.confirmes && !m.jours && !m.aucun) continue;        // sans véhicule suivi ce mois-là : rien à mesurer
        const nb = m["avance"] + m["à l’heure"] + m["retard"];
        const lib = m.mois !== mois ? frappe.datetime.str_to_obj(m.mois + "-01").toLocaleDateString("fr-FR", { month: "long", year: "numeric" }) : "";
        mois = m.mois;
        html += `<tr><td>${lib ? `<b>${esc(lib)}</b>` : ""}</td><td>${esc(m.nom)}</td><td class="num">${m.taches}</td><td class="num">${m.confirmes} <span class="st-dim">${pc(m.confirmes, m.taches)}</span></td><td class="num">${m.aucun || ""}</td>
          <td class="num">${nb ? pc(m["à l’heure"], nb) : "–"}</td><td class="num">${m["avance"] || ""}</td><td class="num">${m["retard"] || ""}</td><td class="num">${m.duree.moyenne ?? "–"}</td><td class="num">${m.ecart.moyenne != null ? (m.ecart.moyenne > 0 ? "+" : "") + m.ecart.moyenne + " min" : "–"}</td>
          <td class="num">${m.jours || ""}</td><td class="num">${m.km ? String(m.km).replace(".", ",") : ""}</td><td class="num">${m.jours && m.km ? String(Math.round(m.km / m.jours)) : ""}</td></tr>`;
      }
      html += `</tbody></table>`;
    }
    html += `<div id="st-analyse"></div>`;
    // Tableau croisé : durée réelle sur place par technicien ET par type (moyenne / médiane / nombre).
    const emps = s.employes.filter((e) => e.confirmes);
    if (emps.length) {
      html += `<div class="st-section">Durée sur place par technicien et par type d’intervention <span class="st-dim">(moyenne · médiane · nombre de passages)</span></div>
        <table class="st-t"><thead><tr><th>Type</th><th class="num">Standard</th><th class="num" title="Durée réelle retenue pour l’arrivée estimée (mesurée 14/09 → 14/10/2026)">Réelle retenue</th>${emps.map((e) => `<th class="num">${esc(e.nom)}</th>`).join("")}<th class="num">Tous</th></tr></thead><tbody>`;
      for (const t of s.types) {
        if (!t.confirmes) continue;
        html += `<tr><td>${esc(t.type)}</td><td class="num">${t.standard != null ? t.standard + " min" : "–"}</td><td class="num">${t.terrain != null ? t.terrain + " min" : "–"}</td>`;
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
    this._render_analyse();
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
