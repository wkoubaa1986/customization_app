// Présence Aquaworld — onglet du suivi terrain (rôle « Sortie Circuit » : Wassim, Néjib), à côté des Sorties de circuit.
// Chargé par `frappe.require("/assets/customization_app/js/presence_aquaworld.js")`.
// Heures d'arrivée / départ à Aquaworld et lieux de nuit du véhicule (Journee Flotte GPS, customization_app.flotte_presence) ;
// domiciles détectés d'après les nuits, à régler d'un clic dans le réglage des tournées.

const PA_LIEU = { Domicile: ["dom", "🏠 domicile"], Magasin: ["mag", "🏢 Magasin"], Autre: ["autre", "❓ autre"] };

class PresenceAquaworld {
  static html() {
    return `<div class="pa-page">
  <style>
    .pa-page { padding: 4px 0 30px; }
    .pa-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
    .pa-bar input[type=date], .pa-bar select { height: 30px; border: 1px solid var(--border-color); border-radius: 6px; padding: 0 8px; background: var(--control-bg); color: var(--text-color); }
    .pa-bar .pa-info { color: var(--text-muted); font-size: 12px; margin-left: auto; }
    .pa-intro { color: var(--text-muted); font-size: 12.5px; margin-bottom: 10px; }
    .pa-bloc { border: 1px solid var(--border-color); border-radius: 10px; background: var(--card-bg, var(--fg-color)); margin-bottom: 14px; overflow: hidden; }
    .pa-bloc .t { padding: 9px 12px; font-weight: 700; background: var(--subtle-fg, var(--bg-color)); border-bottom: 1px solid var(--border-color); display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .pa-bloc .t .pa-dim { font-weight: 400; }
    table.pa-t { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    table.pa-t th { text-align: left; font-weight: 600; color: var(--text-muted); padding: 5px 8px; border-bottom: 1px solid var(--border-color); white-space: nowrap; }
    table.pa-t td { padding: 5px 8px; border-bottom: 1px solid var(--border-color); vertical-align: top; }
    table.pa-t tr:last-child td { border-bottom: 0; }
    table.pa-t td.num, table.pa-t th.num { text-align: right; white-space: nowrap; }
    .pa-dim { color: var(--text-muted); font-size: 12px; }
    .pa-lieu { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11.5px; font-weight: 600; white-space: nowrap; }
    .pa-lieu.dom { background: #dcfce7; color: #166534; } .pa-lieu.mag { background: #dbeafe; color: #1e40af; } .pa-lieu.autre { background: #fee2e2; color: #991b1b; }
    .pa-lieu.ok { background: #dcfce7; color: #166534; } .pa-lieu.warn { background: #fde9c8; color: #92400e; } .pa-lieu.bad { background: #fee2e2; color: #991b1b; } .pa-lieu.dim { background: var(--bg-color); color: var(--text-muted); }
    .pa-jour { font-weight: 700; padding: 7px 12px; background: var(--subtle-fg, var(--bg-color)); border-top: 1px solid var(--border-color); }
    .pa-vide { padding: 14px 12px; color: var(--text-muted); }
    .pa-page a.pa-carte { white-space: nowrap; }
    .pa-lieux { margin: 0; padding: 0; list-style: none; }
    .pa-lieux li { padding: 2px 0; }
    .pa-h { font-variant-numeric: tabular-nums; }
    .pa-h.vide { color: var(--text-muted); }
    .pa-sous { color: var(--text-muted); font-size: 11px; white-space: nowrap; }
    .pa-trajets { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(460px, 100%), 1fr)); gap: 12px; padding: 12px; }
    .pa-emp { min-width: 0; }
    .pa-emp { border: 1px solid var(--border-color); border-radius: 10px; padding: 10px 12px; }
    .pa-emp h4 { margin: 0 0 8px; font-size: 14px; }
    .pa-deux { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .pa-sens b.t { display: block; margin-bottom: 4px; }
    .pa-kpi { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; line-height: 1.5; }
    .pa-kpi strong { color: var(--text-color); font-size: 15px; }
    .pa-histo { display: grid; grid-template-columns: 52px 1fr 28px; gap: 2px 6px; font-size: 11.5px; align-items: center; }
    .pa-histo .b { height: 12px; background: var(--border-color); border-radius: 3px; overflow: hidden; }
    .pa-histo .b i { display: block; height: 100%; background: #3b82f6; }
    .pa-histo.soir .b i { background: #f59e0b; }
    .pa-histo .n { text-align: right; color: var(--text-muted); }
    .pa-histo .l { color: var(--text-muted); white-space: nowrap; }
    .pa-histo .l.med { color: var(--text-color); font-weight: 700; }
    .pa-cumul { margin-top: 4px; }
    .pa-cumul span { white-space: nowrap; }
    .pa-equip { padding: 10px 12px; display: flex; gap: 10px; align-items: flex-start; flex-wrap: wrap; }
    .pa-equip textarea { width: 320px; min-height: 48px; font-size: 12.5px; border: 1px solid var(--border-color); border-radius: 6px; padding: 4px 8px; background: var(--control-bg); color: var(--text-color); }
  </style>
  <div class="pa-bar">
    <label class="pa-dim">Du</label> <input type="date" id="pa-du">
    <label class="pa-dim">au</label> <input type="date" id="pa-au">
    <select id="pa-employe"><option value="">Tous les techniciens</option></select>
    <button class="btn btn-default btn-sm" data-action="refresh">🔄 Actualiser</button>
    <button class="btn btn-default btn-sm" data-action="recalculer" title="Relit la plateforme GPS jour par jour (au plus deux mois)">🧮 Recalculer la période</button>
    <button class="btn btn-default btn-sm" data-action="tout" title="Après un changement de domicile, d'équipage ou de règle : refait présence, sorties de circuit et appariement des tâches sur toute la période du réglage, en arrière-plan">♻️ Tout recalculer</button>
    <span class="pa-info" id="pa-info"></span>
    <span class="pa-info" id="pa-rattrapage"></span>
  </div>
  <div class="pa-intro" id="pa-intro"></div>
  <div class="pa-bloc" id="pa-trajets"></div>
  <div class="pa-bloc" id="pa-domiciles"></div>
  <div class="pa-bloc" id="pa-stats"></div>
  <div class="pa-bloc" id="pa-liste"></div>
</div>
`;
  }

  constructor(racine) {
    this.$root = $(racine).find(".pa-page");
    this.data = null;
    // Période par défaut : le réglage « Statistiques depuis le » (Config Flotte GPS), sinon 30 jours — décidé côté serveur.
    this.$root.find("#pa-au").val(frappe.datetime.get_today());
    this._bind();
    this.charger();
  }

  _bind() {
    const $r = this.$root;
    $r.find("[data-action='refresh'], #pa-du, #pa-au, #pa-employe").on("change click", (e) => { if (e.type === "click" && e.currentTarget.tagName !== "BUTTON") return; this.charger(); });
    $r.find("[data-action='recalculer']").on("click", () => this.recalculer());
    $r.on("click", "[data-action='equipages']", () => this.definir_equipages());
    $r.on("click", "[data-action='rattraper']", () => this.rattraper(false));
    $r.find("[data-action='tout']").on("click", () => this.rattraper(true));
    $r.on("click", "[data-definir]", (e) => {
      const [i, lat, lng] = $(e.currentTarget).attr("data-definir").split("|");
      const emp = $r.find(`[data-qui='${i}']`).val();
      if (!emp) { frappe.msgprint(__("Choisissez d'abord le technicien qui habite là.")); return; }
      const nom = (this.data.techniciens.find((t) => t.employe === emp) || {}).nom || emp;
      this.fixer_domicile(emp, +lat, +lng, nom);
    });
    $r.on("click", "[data-domicile]", (e) => {
      const [emp, lat, lng, nom] = $(e.currentTarget).attr("data-domicile").split("|");
      this.fixer_domicile(emp, +lat, +lng, nom);
    });
  }

  async charger() {
    const $r = this.$root;
    try {
      const r = await frappe.call({ method: "customization_app.flotte_presence.presence",
        args: { du: $r.find("#pa-du").val(), au: $r.find("#pa-au").val(), employe: $r.find("#pa-employe").val() || null }, freeze: true });
      this.data = r.message;
      $r.find("#pa-du").val(this.data.du);
      $r.find("#pa-au").val(this.data.au);
      const $e = $r.find("#pa-employe"), cur = $e.val();
      $e.find("option:not(:first)").remove();
      this.data.employes.forEach((x) => $e.append(`<option value="${frappe.utils.escape_html(x.employe)}">${frappe.utils.escape_html(x.nom || x.employe)}</option>`));
      $e.val(cur || "");
      this._render();
    } catch (e) {
      $r.find("#pa-liste").html(`<div class="pa-vide">${frappe.utils.escape_html(String(e.message || e))}</div>`);
    }
  }

  _h(dt) { return dt ? `<span class="pa-h">${String(dt).slice(11, 16)}</span>` : `<span class="pa-h vide">—</span>`; }
  _m(a, b) { const r = Math.PI / 180, dl = (b[1] - a[1]) * r, dp = (b[0] - a[0]) * r, h = Math.sin(dp / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dl / 2) ** 2; return 12742000 * Math.asin(Math.sqrt(h)); }
  _min(m) { m = Math.round(m || 0); return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`; }
  _lieu(l, lat, lng) {
    if (!l) return `<span class="pa-dim">—</span>`;
    const [c, t] = PA_LIEU[l] || ["autre", l];
    const carte = l === "Autre" && lat ? ` <a class="pa-carte" href="https://www.google.com/maps?q=${lat},${lng}" target="_blank" rel="noopener" title="${lat.toFixed(5)}, ${lng.toFixed(5)}">🗺</a>` : "";
    return `<span class="pa-lieu ${c}">${t}</span>${carte}`;
  }

  _render() {
    const d = this.data, esc = frappe.utils.escape_html, $r = this.$root;
    if (!d) return;
    $r.find("#pa-intro").html(`Tout vient du <b>véhicule</b> : la nuit d'avant (l'arrêt qui chevauche minuit) donne le <b>départ du matin</b> et où il a dormi ; le premier arrêt à moins de ${d.seuils.rayon_m} m du Magasin donne l'<b>arrivée à Aquaworld</b>, le dernier le <b>départ d'Aquaworld</b> ; la nuit d'après donne le <b>retour du soir</b>. Un véhicule qui dort au Magasin ne dit pas quand son technicien arrive. Le lieu d'une nuit se lit contre les domiciles du réglage des tournées : régler un domicile requalifie tout l'historique. Calculé chaque soir à 21 h avec l'appariement.`);
    const n = d.lignes.length;                                   // les journées calculées (coéquipiers hérités compris) ; les non calculées sont à part
    $r.find("#pa-info").html(`${n} journée${n > 1 ? "s" : ""} du ${frappe.datetime.str_to_user(d.du)} au ${frappe.datetime.str_to_user(d.au)}${d.stats_depuis && d.stats_depuis === d.du ? ` <span class="pa-dim">(réglage)</span>` : ""}${d.non_calculees ? ` · ${d.non_calculees} sans présence calculée` : ""}${d.jours_manquants ? ` · <b>${d.jours_manquants} jour${d.jours_manquants > 1 ? "s" : ""} sans calcul</b> <button class="btn btn-xs btn-default" data-action="rattraper">⏳ Rattraper les jours manquants</button>` : ""}`);
    this.suivre_rattrapage(false);

    this._render_trajets();
    // Où dorment les voitures la nuit — toutes voitures confondues ; c'est l'utilisateur qui dit quel endroit est la maison de qui
    let html = `<div class="t">🏠 Où dorment les voitures la nuit <span class="pa-dim">— toutes voitures confondues, nuits regroupées à ${d.seuils.lieu_m} m. Choisissez le technicien et « Définir comme domicile » : son point de départ est écrit dans le réglage des tournées, et tout véhicule qui dort à moins de ${d.seuils.rayon_nuit_m >= 1000 ? Math.round(d.seuils.rayon_nuit_m / 1000) + " km" : d.seuils.rayon_nuit_m + " m"} de chez lui lui est attribué ce jour-là, même dans l'historique</span></div>`;
    if (!d.lieux_nuit.length) html += `<div class="pa-vide">Aucune nuit connue sur la période. Cliquez « Recalculer la période » pour lire la plateforme GPS.</div>`;
    else {
      const opts = (sel) => `<option value="">— technicien —</option>` + d.techniciens.map((t) => `<option value="${esc(t.employe)}"${t.nom === sel ? " selected" : ""}>${esc(t.nom)}</option>`).join("");
      html += `<table class="pa-t"><thead><tr><th>Lieu</th><th class="num">Nuits</th><th>Véhicules</th><th>Attribué ces jours-là (tâches)</th><th>Statut</th><th>Domicile de…</th></tr></thead><tbody>`;
      d.lieux_nuit.forEach((c, i) => {
        const veh = Object.entries(c.vehicules).map(([v, n]) => `${esc(v)} ×${n}`).join(", ");
        const emp = Object.entries(c.employes).map(([e, n]) => `${esc(e)} ×${n}`).join(", ");
        const statut = c.statut === "magasin" ? `<span class="pa-lieu mag">🏢 Magasin</span>` : c.statut === "domicile" ? `<span class="pa-lieu dom">🏠 domicile de ${esc(c.employe_nom)}</span>` : c.statut === "a_regler" ? `<span class="pa-lieu autre">❓ à régler</span>` : `<span class="pa-dim">rare</span>`;
        const action = c.statut === "magasin" ? "" : `<select class="form-control input-sm pa-qui" data-qui="${i}" style="width:170px;display:inline-block;height:26px;padding:0 6px">${opts(c.statut === "domicile" ? c.employe_nom : c.suggestion)}</select> <button class="btn btn-xs btn-default" data-definir="${i}|${c.lat}|${c.lng}">📍 Définir</button>`;
        html += `<tr><td><a class="pa-carte" href="https://www.google.com/maps?q=${c.lat},${c.lng}" target="_blank" rel="noopener">🗺 ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}</a></td>
          <td class="num"><b>${c.n}</b>${c.n ? `<div class="pa-dim">${frappe.datetime.str_to_user(c.premiere)} → ${frappe.datetime.str_to_user(c.derniere)}</div>` : `<div class="pa-dim">aucune sur la période</div>`}</td>
          <td>${veh || "—"}</td><td>${emp || "—"}</td><td>${statut}</td><td>${action}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    $r.find("#pa-domiciles").html(html);

    // Statistiques (médianes)
    html = `<div class="t">📊 Par technicien <span class="pa-dim">— heures médianes sur la période</span></div>`;
    if (!d.stats.length) html += `<div class="pa-vide">Aucune journée calculée sur la période.</div>`;
    else {
      html += `<table class="pa-t"><thead><tr><th>Technicien</th><th class="num">Jours</th><th class="num">Passés à Aquaworld</th><th class="num">Départ matin</th><th class="num">Arrivée Aquaworld</th><th class="num">Départ Aquaworld</th><th class="num">Retour soir</th><th class="num">Temps à Aquaworld</th><th>Nuits</th></tr></thead><tbody>`;
      d.stats.forEach((e) => {
        const nuits = Object.entries(e.nuits).filter(([, v]) => v).map(([k, v]) => `${PA_LIEU[k][1]} ×${v}`).join(", ");
        html += `<tr><td><b>${esc(e.employe_nom || e.employe || "?")}</b>${e.passager ? `<div class="pa-dim">${e.passager} jour(s) comme passager</div>` : ""}${e.immobiles ? `<div class="pa-dim">${e.immobiles} jour(s) sans bouger</div>` : ""}</td><td class="num">${e.jours}</td><td class="num">${e.jours_magasin} <span class="pa-dim">(${e.jours ? Math.round(100 * e.jours_magasin / e.jours) : 0} %)</span></td>
          <td class="num">${e.depart_matin || "—"}</td><td class="num">${e.arrivee || "—"}</td><td class="num">${e.depart || "—"}</td><td class="num">${e.retour || "—"}</td><td class="num">${e.minutes_aquaworld ? this._min(e.minutes_aquaworld) : "—"}</td><td>${nuits}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    $r.find("#pa-stats").html(html);

    // Journées
    const lignes = d.lignes.filter((l) => l.nuit_avant_lat || l.nuit_lat || l.arrivee_aquaworld);
    html = `<div class="t">📅 Jour par jour <span class="pa-dim">${lignes.length} journée${lignes.length > 1 ? "s" : ""}</span></div>`;
    if (!lignes.length) html += `<div class="pa-vide">Rien sur la période. Les journées se calculent chaque soir à 21 h ; « Recalculer la période » remonte l'historique.</div>`;
    else {
      let jour = null;
      html += `<table class="pa-t"><thead><tr><th>Technicien</th><th>Nuit d'avant</th><th class="num">Départ matin</th><th class="num">Arrivée Aquaworld</th><th class="num">Départ Aquaworld</th><th class="num">Retour soir</th><th>Nuit d'après</th><th class="num">À Aquaworld</th><th class="num">Km</th></tr></thead><tbody>`;
      lignes.forEach((l) => {
        if (l.date !== jour) { jour = l.date; html += `<tr><td colspan="9" class="pa-jour">${frappe.datetime.str_to_user(l.date)}</td></tr>`; }
        const note = (l.attribue_par_taches ? `<div class="pa-dim" title="Le véhicule a dormi à moins de ${d.seuils.rayon_nuit_m >= 1000 ? Math.round(d.seuils.rayon_nuit_m / 1000) + " km" : d.seuils.rayon_nuit_m + " m"} de son domicile : la nuit prime sur les tâches">🌙 par la nuit (tâches : ${esc(l.attribue_par_taches)})</div>` : "") + (l.passager ? `<div class="pa-dim">🚗 avec ${esc(l.avec)}</div>` : "") + (l.immobile ? `<div class="pa-dim">véhicule immobile toute la journée</div>` : l.en_cours ? `<div class="pa-dim">journée en cours</div>` : "");
        const tm = l.duree_matin != null && l.duree_matin <= d.seuils.trajet_max_min ? `<div class="pa-sous">↑ ${this._min(l.duree_matin)}${l.arrets_matin ? ` · ${l.arrets_matin} arrêt${l.arrets_matin > 1 ? "s" : ""}` : " direct"}</div>` : "";
        const ts = l.duree_soir != null && l.duree_soir <= d.seuils.trajet_max_min ? `<div class="pa-sous">↓ ${this._min(l.duree_soir)}${l.arrets_soir ? ` · ${l.arrets_soir} arrêt${l.arrets_soir > 1 ? "s" : ""}` : " direct"}</div>` : "";
        html += `<tr><td>${esc(l.employe_nom || "")}${l.employe_nom ? `<div class="pa-dim">${esc(l.vehicule || "")}</div>` : `<b>${esc(l.vehicule || "")}</b><div class="pa-dim">sans employé reconnu</div>`}${note}</td>
          <td>${this._lieu(l.nuit_avant_lieu, l.nuit_avant_lat, l.nuit_avant_lng)}</td><td class="num">${this._h(l.depart_matin)}</td><td class="num">${this._h(l.arrivee_aquaworld)}${tm}</td>
          <td class="num">${this._h(l.depart_aquaworld)}</td><td class="num">${this._h(l.retour_soir)}${ts}</td><td>${this._lieu(l.nuit_lieu, l.nuit_lat, l.nuit_lng)}</td>
          <td class="num">${l.minutes_aquaworld ? this._min(l.minutes_aquaworld) : "—"}</td><td class="num">${l.km ? String(Math.round(l.km)).replace(".", ",") : ""}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    $r.find("#pa-liste").html(html);
  }

  _histo(tr, mediane, cls) {
    const max = Math.max(1, ...tr.map(([, n]) => n));
    return `<div class="pa-histo ${cls}">${tr.map(([l, n]) => `<span class="l${l === mediane.slice(0, 2) + ":" + (mediane.slice(3) < "30" ? "00" : "30") ? " med" : ""}">${l}</span><span class="b"><i style="width:${Math.round(100 * n / max)}%"></i></span><span class="n">${n || ""}</span>`).join("")}</div>`;
  }

  // Distribution cumulative : « à telle heure, x % sont arrivés / partis » (demande du 16/10). tr = [[libellé, n]] par demi-heure,
  // première et dernière tranches ouvertes ; la courbe monte à chaque borne, les repères donnent le % atteint.
  _cumul(tr, cls, quoi) {
    const total = tr.reduce((a, [, n]) => a + n, 0);
    if (!total) return "";
    const bornes = tr.slice(1).map(([l]) => l.replace("≥ ", ""));            // borne de fin de chaque tranche sauf la dernière ouverte
    let acc = 0;
    const pts = tr.slice(0, -1).map(([, n], i) => { acc += n; return { h: bornes[i], pct: Math.round(100 * acc / total) }; });
    const W = 300, H = 70, mx = 24, my = 10, ix = (i) => mx + (i * (W - 2 * mx)) / Math.max(1, pts.length - 1), iy = (pct) => H - my - ((H - 2 * my) * pct) / 100;
    let path = `M ${ix(0)} ${iy(0)}`;
    pts.forEach((p, i) => { path += ` L ${ix(i)} ${iy(pts[i - 1] ? pts[i - 1].pct : 0)} L ${ix(i)} ${iy(p.pct)}`; });
    const q = (pct) => { const i = pts.findIndex((p) => p.pct >= pct); return i >= 0 ? pts[i].h : "—"; };
    const reperes = [25, 50, 75, 90].map((pct) => `<span><b>${pct} %</b> avant ${q(pct)}</span>`).join(" · ");
    const ticks = pts.filter((p, i) => i % 2 === 0).map((p, i) => `<text x="${ix(i * 2)}" y="${H - 1}" font-size="8" text-anchor="middle" fill="currentColor" opacity=".6">${p.h}</text>`).join("");
    const col = cls === "soir" ? "#f59e0b" : "#3b82f6";
    return `<div class="pa-cumul"><div class="pa-dim" style="margin:6px 0 2px">Cumul ${quoi} : ${reperes}</div>
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" style="display:block;overflow:visible">
        ${[25, 50, 75].map((pct) => `<line x1="${mx}" x2="${W - mx}" y1="${iy(pct)}" y2="${iy(pct)}" stroke="currentColor" stroke-opacity=".12" stroke-dasharray="2 3"/>`).join("")}
        <text x="2" y="${iy(50) + 3}" font-size="8" fill="currentColor" opacity=".6">50 %</text><text x="2" y="${iy(100) + 3}" font-size="8" fill="currentColor" opacity=".6">100 %</text>
        <path d="${path}" fill="none" stroke="${col}" stroke-width="2"/>
        ${pts.map((p, i) => `<circle cx="${ix(i)}" cy="${iy(p.pct)}" r="2" fill="${col}"><title>${p.h} : ${p.pct} %</title></circle>`).join("")}
        ${ticks}
      </svg></div>`;
  }

  _render_trajets() {
    const d = this.data, esc = frappe.utils.escape_html, $r = this.$root, s = d.seuils;
    const equip = d.equipages.length ? d.equipages.map((g) => g.join(" + ")).join(" · ") : "aucun";
    let html = `<div class="t">🚗 Trajets domicile ↔ Aquaworld <span class="pa-dim">— le matin : du départ de chez soi au premier arrêt au Magasin ; le soir : du dernier départ du Magasin au retour chez soi. Un trajet de plus de ${Math.round(s.trajet_max_min / 60)} h est « par le terrain » (hors distribution) ; un arrêt en chemin ≥ ${s.arret_trajet_min} min est compté (dépose ou prise en charge d'un collègue)</span></div>`;
    html += `<div class="pa-equip"><div><div class="pa-dim">Qui rentre avec qui <span title="Un coéquipier sans véhicule à lui ce jour-là hérite des heures du véhicule de son équipage (marqué « avec X »). Une ligne par équipage, noms séparés par +.">ⓘ</span></div><textarea id="pa-covoiturage" placeholder="Sadok Bouziri + Jamel Aloui">${esc(d.covoiturage || "")}</textarea></div>
      <div><div class="pa-dim">Statistiques depuis le <span title="Période par défaut des onglets Sorties de circuit et Présence Aquaworld. Les jours sans calcul depuis cette date se rattrapent en arrière-plan (bouton en haut).">ⓘ</span></div><input type="date" id="pa-depuis" value="${esc(d.stats_depuis || "")}"></div>
      <div><button class="btn btn-default btn-sm" data-action="equipages">💾 Enregistrer les réglages</button><div class="pa-dim" style="margin-top:6px">Équipages lus : ${esc(equip)}</div></div></div>`;
    // Qui habite là ? Si la majorité des arrêts viennent de journées héritées (l'employé était passager), c'est sa propre
    // dépose → son domicile. Sinon il conduisait : c'est là qu'il dépose (ou prend) un coéquipier → le domicile du coéquipier.
    const lieux_html = (b, emp, quoi) => b.lieux.length ? `<div class="pa-kpi">${quoi} : ${b.lieux.map((c) => {
      const partenaires = d.equipages.filter((g) => g.includes(emp.employe_nom)).flat().filter((n) => n !== emp.employe_nom);
      const lui_meme = c.passagers * 2 > c.n;
      const cible = lui_meme ? d.employes.filter((x) => x.employe === emp.employe) : d.employes.filter((x) => partenaires.includes(x.nom));
      // Un endroit déjà connu (Magasin, domicile réglé de n'importe qui — le conducteur passe chez lui avant de repartir) est
      // nommé, jamais proposé comme domicile d'un autre.
      const connu = this._m([c.lat, c.lng], d.depot) <= s.rayon_m ? "🏪 Magasin"
        : (d.reglages.filter((dm) => this._m([c.lat, c.lng], [dm.lat, dm.lng]) <= s.lieu_m).map((dm) => `🏠 domicile de ${esc(dm.employe_nom)}`)[0] || "");
      const btns = connu ? ` <span class="pa-dim">${connu}</span>` : cible.map((x) =>
        ` <button class="btn btn-xs btn-default" data-domicile="${esc(x.employe)}|${c.lat}|${c.lng}|${esc(x.nom)}" title="Ce point récurrent est probablement le domicile de ${esc(x.nom)}">📍 domicile de ${esc(x.nom)}</button>`).join("");
      const qui = lui_meme ? ` <span class="pa-dim">(en passager)</span>` : "";
      return `<span><a class="pa-carte" href="https://www.google.com/maps?q=${c.lat},${c.lng}" target="_blank" rel="noopener">🗺 ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}</a> ×${c.n} (${this._min(Math.round(c.minutes / c.n))} en moyenne)${qui}${connu || c.n >= s.nuits_min ? btns : ""}</span>`;
    }).join(" · ")}</div>` : "";
    if (!d.trajets.length) html += `<div class="pa-vide">Aucune journée calculée sur la période.</div>`;
    else {
      html += `<div class="pa-trajets">`;
      d.trajets.forEach((e) => {
        const m = e.matin, so = e.soir;
        const pct = (a, b) => (b ? Math.round(100 * a / b) : 0);
        html += `<div class="pa-emp"><h4>${esc(e.employe_nom)}</h4><div class="pa-deux">
          <div class="pa-sens"><b class="t">🌅 Matin : domicile → Aquaworld</b>
            <div class="pa-kpi">${m.n ? `Arrive à <strong>${m.mediane}</strong> en médiane (de ${m.premiere} à ${m.derniere}) · trajet <strong>${this._min(m.duree_mediane)}</strong> · ${m.n} jour${m.n > 1 ? "s" : ""}, ${pct(m.directs, m.n)} % directs` : `Aucun trajet domicile → Aquaworld mesuré`}${m.sans_passage ? `<br>${m.sans_passage} matin${m.sans_passage > 1 ? "s" : ""} parti${m.sans_passage > 1 ? "s" : ""} de chez soi directement sur le terrain` : ""}${m.par_terrain ? `<br>${m.par_terrain} par le terrain avant Aquaworld` : ""}${m.demi_journee ? `<br>${m.demi_journee} demi-journée${m.demi_journee > 1 ? "s" : ""} (arrivé à Aquaworld après midi, hors distribution)` : ""}</div>
            ${m.n ? this._histo(m.tranches, m.mediane, "matin") + this._cumul(m.tranches, "matin", "des arrivées") : ""}${lieux_html(m, e, "Arrêts en chemin (prise en charge ?)")}
            ${m.magasin && m.magasin.n ? `<div class="pa-kpi" style="margin-top:8px;border-top:1px dashed var(--border-color);padding-top:6px"><b>${m.magasin.n} matin${m.magasin.n > 1 ? "s" : ""} où la voiture dormait au Magasin</b> (arrivée non mesurable) : première sortie du Magasin à <strong>${m.magasin.mediane}</strong> en médiane (de ${m.magasin.premiere} à ${m.magasin.derniere}) — il était là avant.</div>${this._histo(m.magasin.tranches, m.magasin.mediane, "matin")}` : ""}</div>
          <div class="pa-sens"><b class="t">🌇 Soir : Aquaworld → domicile</b>
            <div class="pa-kpi">${so.n ? `Part à <strong>${so.mediane}</strong> en médiane (de ${so.premiere} à ${so.derniere}) · trajet <strong>${this._min(so.duree_mediane)}</strong> · ${so.n} soir${so.n > 1 ? "s" : ""}, ${pct(so.directs, so.n)} % directs` : `Aucun trajet Aquaworld → domicile mesuré`}${so.sans_passage ? `<br>${so.sans_passage} soir${so.sans_passage > 1 ? "s" : ""} rentré${so.sans_passage > 1 ? "s" : ""} du terrain sans repasser par Aquaworld` : ""}${so.par_terrain ? `<br>${so.par_terrain} par le terrain après Aquaworld` : ""}${so.demi_journee ? `<br>${so.demi_journee} demi-journée${so.demi_journee > 1 ? "s" : ""} (Magasin quitté avant 13 h, hors distribution)` : ""}</div>
            ${so.n ? this._histo(so.tranches, so.mediane, "soir") + this._cumul(so.tranches, "soir", "des départs") : ""}${lieux_html(so, e, "Arrêts en chemin (dépose ?)")}
            ${so.magasin && so.magasin.n ? `<div class="pa-kpi" style="margin-top:8px;border-top:1px dashed var(--border-color);padding-top:6px"><b>${so.magasin.n} soir${so.magasin.n > 1 ? "s" : ""} où la voiture rentre dormir au Magasin</b> (départ non mesurable) : dernier retour au Magasin à <strong>${so.magasin.mediane}</strong> en médiane (de ${so.magasin.premiere} à ${so.magasin.derniere}) — fin du terrain.</div>${this._histo(so.magasin.tranches, so.magasin.mediane, "soir")}` : ""}</div>
        </div></div>`;
      });
      html += `</div>`;
    }
    $r.find("#pa-trajets").html(html);
  }

  async definir_equipages() {
    try {
      await frappe.call({ method: "customization_app.flotte_presence.definir_equipages",
        args: { texte: this.$root.find("#pa-covoiturage").val(), stats_depuis: this.$root.find("#pa-depuis").val() || "" }, freeze: true });
      frappe.show_alert({ message: __("Réglages enregistrés"), indicator: "green" });
      this.$root.find("#pa-du").val("");                           // la période par défaut repart du réglage
      this.charger();
    } catch (e) { frappe.msgprint(String(e.message || e)); }
  }

  async rattraper(tout) {
    if (tout) {
      const ok = await new Promise((res) => frappe.confirm(__("Tout recalculer depuis la date de début des statistiques jusqu'à hier : présence, sorties de circuit et appariement des tâches (qui conduisait quoi), avec les domiciles et équipages d'aujourd'hui ? Les justifications des sorties de circuit sont conservées. Une lecture de la plateforme par jour, en arrière-plan."), () => res(true), () => res(false)));
      if (!ok) return;
    }
    try {
      const r = await frappe.call({ method: "customization_app.flotte_presence.rattraper", args: { au: this.$root.find("#pa-au").val(), tout: tout ? 1 : 0 }, freeze: true });
      const b = r.message || {};
      if (b.deja) frappe.show_alert({ message: __("Un rattrapage tourne déjà"), indicator: "orange" });
      else if (!b.jours) frappe.show_alert({ message: __("Rien à rattraper"), indicator: "green" });
      else frappe.show_alert({ message: __("{0} jour(s) en cours de calcul en arrière-plan (présence + sorties de circuit)", [b.jours]), indicator: "blue" });
      this.suivre_rattrapage(true);
    } catch (e) { frappe.msgprint(String(e.message || e)); }
  }

  // Avancement du rattrapage (tâche de fond) : lu toutes les 8 s tant qu'il tourne, puis la période est rechargée.
  async suivre_rattrapage(force) {
    if (this._suivi) { clearTimeout(this._suivi); this._suivi = null; }
    if (!force && !this._suivi_en_cours) return;
    try {
      const r = await frappe.call({ method: "customization_app.flotte_presence.etat_rattrapage" });
      const e = r.message || {};
      const $i = this.$root.find("#pa-rattrapage");
      if (e.actif || e.statut === "en attente" || e.statut === "en cours") {
        this._suivi_en_cours = true;
        $i.html(`⏳ ${e.taches ? "Recalcul complet" : "Rattrapage"} ${e.faits || 0} / ${e.total || "?"} jour(s)${e.jour ? ` · ${frappe.datetime.str_to_user(e.jour)}` : ""}${e.erreurs && e.erreurs.length ? ` · ${e.erreurs.length} erreur(s)` : ""}`);
        this._suivi = setTimeout(() => this.suivre_rattrapage(true), 8000);
      } else {
        const fini = this._suivi_en_cours;
        this._suivi_en_cours = false;
        $i.html(e.statut === "terminé" && e.fin ? `<span class="pa-dim">Dernier rattrapage : ${e.faits || 0} jour(s), terminé le ${frappe.datetime.str_to_user(e.fin.slice(0, 10))} à ${e.fin.slice(11, 16)}${e.erreurs && e.erreurs.length ? ` · ${e.erreurs.length} erreur(s)` : ""}</span>` : "");
        if (e.erreurs && e.erreurs.length && fini) frappe.msgprint(e.erreurs.join("<br>"));
        if (fini) this.charger();
      }
    } catch (err) { /* état indisponible : on n'insiste pas */ }
  }

  fixer_domicile(employe, lat, lng, nom) {
    frappe.confirm(__("Régler le domicile de {0} sur ce lieu de nuit ({1}, {2}) ? Il devient son point de départ dans le réglage des tournées et requalifie ses nuits.", [nom, lat.toFixed(5), lng.toFixed(5)]), async () => {
      try {
        await frappe.call({ method: "customization_app.flotte_presence.fixer_domicile", args: { employe, lat, lng }, freeze: true });
        frappe.show_alert({ message: __("Domicile de {0} réglé", [nom]), indicator: "green" });
        this.charger();
      } catch (e) { frappe.msgprint(String(e.message || e)); }
    });
  }

  recalculer() {
    const du = this.$root.find("#pa-du").val(), au = this.$root.find("#pa-au").val();
    frappe.confirm(__("Relire la plateforme GPS du {0} au {1} et recalculer les présences ?", [frappe.datetime.str_to_user(du), frappe.datetime.str_to_user(au)]), async () => {
      try {
        const r = await frappe.call({ method: "customization_app.flotte_presence.recalculer", args: { du, au }, freeze: true, freeze_message: __("Lecture de la plateforme GPS, jour par jour…") });
        const b = r.message || {};
        frappe.show_alert({ message: __("{0} jour(s) recalculé(s), {1} journée(s) de véhicule", [b.jours, b.vehicules]), indicator: b.erreurs && b.erreurs.length ? "orange" : "green" });
        if (b.erreurs && b.erreurs.length) frappe.msgprint(b.erreurs.join("<br>"));
        this.charger();
      } catch (e) {
        frappe.msgprint(String(e.message || e));
      }
    });
  }
}

window.PresenceAquaworld = PresenceAquaworld;
