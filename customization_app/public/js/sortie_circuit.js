// Sorties de circuit — bloc partagé : page /app/sortie-circuit et onglet « Sorties de circuit » du suivi terrain.
// Chargé par `frappe.require("/assets/customization_app/js/sortie_circuit.js")`. Rôle « Sortie Circuit » (Wassim, Néjib).

const SC_TYPE = { "Arrêt inexpliqué": ["arret", "🅿️ arrêt inexpliqué"], "Hors horaires": ["horaires", "🌙 hors horaires"], "Détour": ["detour", "↪️ détour"] };

class SortieCircuit {
  // Le gabarit (styles compris), pour l'injecter dans n'importe quel conteneur.
  static html() {
    return `<div class="sc-page">
  <style>
    .sc-page { padding: 4px 0 30px; }
    .sc-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
    .sc-bar input[type=date], .sc-bar select { height: 30px; border: 1px solid var(--border-color); border-radius: 6px; padding: 0 8px; background: var(--control-bg); color: var(--text-color); }
    .sc-bar .sc-info { color: var(--text-muted); font-size: 12px; margin-left: auto; }
    .sc-intro { color: var(--text-muted); font-size: 12.5px; margin-bottom: 10px; }
    .sc-bloc { border: 1px solid var(--border-color); border-radius: 10px; background: var(--card-bg, var(--fg-color)); margin-bottom: 14px; overflow: hidden; }
    .sc-bloc .t { padding: 9px 12px; font-weight: 700; background: var(--subtle-fg, var(--bg-color)); border-bottom: 1px solid var(--border-color); display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .sc-bloc .t .sc-dim { font-weight: 400; }
    table.sc-t { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    table.sc-t th { text-align: left; font-weight: 600; color: var(--text-muted); padding: 5px 8px; border-bottom: 1px solid var(--border-color); white-space: nowrap; }
    table.sc-t td { padding: 5px 8px; border-bottom: 1px solid var(--border-color); vertical-align: top; }
    table.sc-t tr:last-child td { border-bottom: 0; }
    table.sc-t td.num, table.sc-t th.num { text-align: right; white-space: nowrap; }
    table.sc-t tr.sc-just td { color: var(--text-muted); }
    table.sc-t tr.sc-anomalie td { background: #fff1f2; }
    .sc-type { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11.5px; font-weight: 600; white-space: nowrap; }
    .sc-type.arret { background: #fee2e2; color: #991b1b; } .sc-type.horaires { background: #ede9fe; color: #5b21b6; } .sc-type.detour { background: #fde9c8; color: #92400e; }
    .sc-dim { color: var(--text-muted); font-size: 12px; }
    .sc-just select { height: 26px; font-size: 12px; border: 1px solid var(--border-color); border-radius: 6px; background: var(--control-bg); color: var(--text-color); max-width: 170px; }
    .sc-just input { height: 26px; font-size: 12px; border: 1px solid var(--border-color); border-radius: 6px; background: var(--control-bg); color: var(--text-color); width: 160px; margin-left: 4px; }
    .sc-kpi { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 12px; }
    .sc-kpi div { border: 1px solid var(--border-color); border-radius: 8px; padding: 6px 10px; min-width: 120px; }
    .sc-kpi b { display: block; font-size: 18px; }
    .sc-vide { padding: 14px 12px; color: var(--text-muted); }
    .sc-jour { font-weight: 700; padding: 7px 12px; background: var(--subtle-fg, var(--bg-color)); border-top: 1px solid var(--border-color); }
    .sc-page a.sc-carte { white-space: nowrap; }
    .sc-graphs { display: grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr)); gap: 10px; margin: 10px 0; }
    .sc-graph { border: 1px solid var(--border-color); border-radius: 8px; padding: 8px 10px; }
    .sc-chip { display: inline-block; border: 1px solid var(--border-color); border-radius: 10px; padding: 1px 7px; margin: 1px 0; font-size: 11.5px; white-space: nowrap; }
  </style>
  <div class="sc-bar">
    <label class="sc-dim">Du</label> <input type="date" id="sc-du">
    <label class="sc-dim">au</label> <input type="date" id="sc-au">
    <select id="sc-employe"><option value="">Tous les techniciens</option></select>
    <select id="sc-filtre">
      <option value="a_voir">À regarder (non justifiés)</option>
      <option value="tous">Tous les écarts</option>
      <option value="anomalies">Anomalies confirmées</option>
    </select>
    <button class="btn btn-default btn-sm" data-action="refresh">🔄 Actualiser</button>
    <button class="btn btn-default btn-sm" data-action="recalculer" title="Relit la plateforme GPS jour par jour (au plus deux mois)">🧮 Recalculer la période</button>
    <span class="sc-info" id="sc-info"></span>
  </div>
  <div class="sc-intro" id="sc-intro"></div>
  <div class="sc-bloc" id="sc-journee"></div>
  <div class="sc-bloc" id="sc-stats"></div>
  <div class="sc-bloc" id="sc-lieux"></div>
  <div class="sc-bloc" id="sc-liste"></div>
</div>
`;
  }

  constructor(racine) {
    this.$root = $(racine).find(".sc-page");
    this.data = null;
    // Période par défaut : le réglage « Statistiques depuis le » (Config Flotte GPS), sinon 30 jours — décidé côté serveur.
    this.$root.find("#sc-au").val(frappe.datetime.get_today());
    try { const f = localStorage.getItem("sc_filtre"); if (f) this.$root.find("#sc-filtre").val(f); } catch (e) { /* stockage indisponible */ }
    this._bind();
    this.charger();
  }

  _bind() {
    const $r = this.$root;
    $r.find("[data-action='refresh'], #sc-du, #sc-au, #sc-employe").on("change click", (e) => { if (e.type === "click" && e.currentTarget.tagName !== "BUTTON") return; this.charger(); });
    $r.find("#sc-filtre").on("change", () => {
      try { localStorage.setItem("sc_filtre", $r.find("#sc-filtre").val()); } catch (e) { /* ignoré */ }
      this._render();
    });
    $r.find("[data-action='recalculer']").on("click", () => this.recalculer());
    $r.on("change", "[data-just]", (e) => {
      const $s = $(e.currentTarget), name = $s.attr("data-just");
      const note = $r.find(`[data-note='${name}']`).val() || "";
      this.justifier(name, $s.val(), note);
    });
    $r.on("keydown", "[data-note]", (e) => {
      if (e.key !== "Enter") return;
      const name = $(e.currentTarget).attr("data-note");
      this.justifier(name, $r.find(`[data-just='${name}']`).val(), $(e.currentTarget).val());
    });
  }

  async charger() {
    const $r = this.$root;
    try {
      const r = await frappe.call({ method: "customization_app.flotte_circuit.sorties",
        args: { du: $r.find("#sc-du").val(), au: $r.find("#sc-au").val(), employe: $r.find("#sc-employe").val() || null }, freeze: true });
      this.data = r.message;
      $r.find("#sc-du").val(this.data.du);
      $r.find("#sc-au").val(this.data.au);
      const $e = $r.find("#sc-employe"), cur = $e.val();
      $e.find("option:not(:first)").remove();
      this.data.employes.forEach((x) => $e.append(`<option value="${frappe.utils.escape_html(x.employe)}">${frappe.utils.escape_html(x.nom || x.employe)}</option>`));
      $e.val(cur || "");
      this._render();
    } catch (e) {
      $r.find("#sc-liste").html(`<div class="sc-vide">${frappe.utils.escape_html(String(e.message || e))}</div>`);
    }
  }

  _h(dt) { return dt ? String(dt).slice(11, 16) : ""; }
  _min(m) { m = Math.round(m || 0); return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`; }
  _km(k) { return k == null ? "" : String(Math.round(k * 10) / 10).replace(".", ",") + " km"; }

  _render() {
    const d = this.data, esc = frappe.utils.escape_html, $r = this.$root;
    if (!d) return;
    const s = d.seuils;
    $r.find("#sc-intro").html(`Le circuit = les clients planifiés du jour (+ Magasin et domicile). Un écart est un fait à <b>regarder</b>, pas une faute : fournisseur, client non planifié, course Aramex, pause… justifiez-le et il sort de la liste. Seuils : arrêt ≥ ${s.arret_min} min à plus de ${s.distance_km} km de tout client (${s.distance_approx_km} km si la position du client est approchée) · trajet 2 h avant / 3 h après les horaires ou un dimanche · km réels ≥ ${String(s.ratio_detour).replace(".", ",")} × le circuit et + ${s.km_detour_min} km. Calculé chaque soir à 21 h 10.`);
    $r.find("#sc-info").html(`${d.lignes.length} écart${d.lignes.length > 1 ? "s" : ""} du ${frappe.datetime.str_to_user(d.du)} au ${frappe.datetime.str_to_user(d.au)}${d.stats_depuis && d.stats_depuis === d.du ? ` <span class="sc-dim">(réglage)</span>` : ""}`);
    // Statistiques par technicien
    let html = `<div class="t">📊 Par technicien <span class="sc-dim">— km et jours d'après les journées GPS, écarts d'après ce calcul</span></div>`;
    if (!d.stats.length) html += `<div class="sc-vide">Aucune journée GPS ni écart sur la période. Cliquez « Recalculer la période » si les journées existent.</div>`;
    else {
      html += `<table class="sc-t"><thead><tr><th>Technicien</th><th class="num">Jours</th><th class="num">Km</th><th class="num">Km / jour</th><th class="num">Arrêts inexpliqués</th><th class="num">Temps</th><th class="num">Hors horaires</th><th class="num">Détours</th><th class="num">Km en plus</th><th class="num">À regarder</th><th class="num">Justifiés</th><th class="num">Anomalies</th></tr></thead><tbody>`;
      d.stats.forEach((e) => {
        html += `<tr><td><b>${esc(e.employe_nom || e.employe || "?")}</b></td><td class="num">${e.jours}</td><td class="num">${this._km(e.km)}</td><td class="num">${e.km_par_jour != null ? this._km(e.km_par_jour) : ""}</td>
          <td class="num">${e.arrets || ""}</td><td class="num">${e.minutes_arrets ? this._min(e.minutes_arrets) : ""}</td><td class="num">${e.hors_horaires || ""}</td><td class="num">${e.detours || ""}</td><td class="num">${e.km_detour ? this._km(e.km_detour) : ""}</td>
          <td class="num">${e.a_voir ? `<b style="color:#b91c1c">${e.a_voir}</b>` : "0"}</td><td class="num">${e.justifies || ""}</td><td class="num">${e.anomalies ? `<b style="color:#b91c1c">${e.anomalies}</b>` : ""}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    $r.find("#sc-stats").html(html);
    this._render_journee();
    // Lieux récurrents : un domicile non réglé, un fournisseur, un café — à justifier d'un coup
    html = `<div class="t">📍 Mêmes endroits, plusieurs fois <span class="sc-dim">— un domicile non réglé (Réglages des tournées → domiciles), un fournisseur, un café… à justifier d'un coup</span></div>`;
    const lieux = (d.lieux || []).filter((c) => filtre_lieux(c));
    function filtre_lieux(c) { const f = $r.find("#sc-filtre").val(); return f === "tous" || (f === "anomalies" ? (c.justifications["Anomalie confirmée"] || 0) > 0 : c.a_voir > 0); }
    if (!lieux.length) html += `<div class="sc-vide">Aucun endroit récurrent avec ce filtre.</div>`;
    else {
      html += `<table class="sc-t"><thead><tr><th>Endroit</th><th>Qui</th><th class="num">Arrêts</th><th class="num">Temps</th><th class="num">À regarder</th><th>Justifier tous</th></tr></thead><tbody>`;
      lieux.forEach((c, i) => {
        const just = Object.entries(c.justifications).map(([j, n]) => `${j} ×${n}`).join(", ");
        const opts = d.justifications.map((j) => `<option value="${esc(j)}">${j || "— choisir —"}</option>`).join("");
        html += `<tr><td><a class="sc-carte" href="https://www.google.com/maps?q=${c.lat},${c.lng}" target="_blank" rel="noopener">🗺 ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}</a>${just ? `<div class="sc-dim">${esc(just)}</div>` : ""}</td>
          <td>${c.employes.map(([e, n]) => `${esc(e)} ×${n}`).join(", ")}</td><td class="num">${c.n}</td><td class="num">${this._min(c.minutes)}</td><td class="num">${c.a_voir ? `<b style="color:#b91c1c">${c.a_voir}</b>` : "0"}</td>
          <td class="sc-just"><select data-lieu="${i}">${opts}</select></td></tr>`;
      });
      html += `</tbody></table>`;
    }
    $r.find("#sc-lieux").html(html);
    $r.find("[data-lieu]").on("change", async (e) => {
      const c = lieux[+$(e.currentTarget).attr("data-lieu")], j = $(e.currentTarget).val();
      if (!j) return;
      try {
        await frappe.call({ method: "customization_app.flotte_circuit.justifier_lot", args: { names: c.noms, justification: j }, freeze: true });
        frappe.show_alert({ message: __("{0} arrêt(s) justifié(s) : {1}", [c.noms.length, j]), indicator: "green" });
        this.charger();
      } catch (ex) { frappe.msgprint(String(ex.message || ex)); }
    });
    // Liste des écarts
    const filtre = $r.find("#sc-filtre").val();
    const lignes = d.lignes.filter((l) => filtre === "tous" || (filtre === "anomalies" ? l.justification === "Anomalie confirmée" : !l.justification));
    html = `<div class="t">🧭 Écarts <span class="sc-dim">${lignes.length} affiché${lignes.length > 1 ? "s" : ""}</span></div>`;
    if (!lignes.length) html += `<div class="sc-vide">Rien à regarder sur la période avec ce filtre.</div>`;
    else {
      let jour = null;
      html += `<table class="sc-t"><thead><tr><th>Technicien</th><th>Type</th><th class="num">Heure</th><th>Détail</th><th></th><th>Justification</th></tr></thead><tbody>`;
      lignes.forEach((l) => {
        if (l.date !== jour) { jour = l.date; html += `<tr><td colspan="6" class="sc-jour">${frappe.datetime.str_to_user(l.date)}</td></tr>`; }
        const [tc, tl] = SC_TYPE[l.type] || ["autre", l.type];
        const carte = l.lat ? `<a class="sc-carte" href="https://www.google.com/maps?q=${l.lat},${l.lng}" target="_blank" rel="noopener">🗺 carte</a>` : "";
        const tache = l.tache_proche ? ` · <a href="/app/tache-de-travail/${encodeURIComponent(l.tache_proche)}" target="_blank">${esc(l.client_proche || l.tache_proche)}</a>` : "";
        const cls = l.justification === "Anomalie confirmée" ? "sc-anomalie" : l.justification ? "sc-just" : "";
        const opts = d.justifications.map((j) => `<option value="${esc(j)}"${j === (l.justification || "") ? " selected" : ""}>${j || "— à regarder —"}</option>`).join("");
        const qui = l.justifie_par ? `<div class="sc-dim">${esc(l.justifie_par.split("@")[0])} · ${frappe.datetime.str_to_user(String(l.justifie_le || "").slice(0, 10))}</div>` : "";
        html += `<tr class="${cls}"><td>${esc(l.employe_nom || l.employe)}<div class="sc-dim">${esc(l.vehicule || "")}</div></td><td><span class="sc-type ${tc}">${tl}</span></td>
          <td class="num">${this._h(l.debut)}${l.fin && l.type !== "Détour" ? "–" + this._h(l.fin) : ""}</td><td>${esc(l.detail || "")}${tache}</td><td>${carte}</td>
          <td class="sc-just"><select data-just="${esc(l.name)}">${opts}</select><input type="text" data-note="${esc(l.name)}" placeholder="note (Entrée)" value="${esc(l.note || "")}">${qui}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    $r.find("#sc-liste").html(html);
  }

  // Journée de travail 08:00–18:00 : chaque arrêt classé (client apparié, tâche non appariée probable, Magasin, domicile, autre)
  _render_journee() {
    const d = this.data, esc = frappe.utils.escape_html, $r = this.$root;
    const CAT = { client: ["🧑", "client"], probable: ["❓", "tâche non appariée (probable)"], magasin: ["🏢", "Magasin"], domicile: ["🏠", "domicile"], trajet: ["🚗", "en chemin (trajet domicile ↔ Aquaworld)"], autre: ["⚠️", "autre arrêt"] };
    const [p0, p1] = d.plage_jour || [480, 1080];
    const hm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    let html = `<div class="t">🕗 Journée de travail ${hm(p0)}–${hm(p1)} : où s'arrêtent-ils ? <span class="sc-dim">— chaque arrêt ≥ 5 min commencé dans la plage est classé : passage confirmé d'une tâche, tâche non appariée à qui on l'affecte (≤ 1 km, sinon la plus proche en heure à ± 3 h), Magasin, retour au domicile, en chemin (les arrêts des trajets domicile ↔ Aquaworld de l'onglet Présence : exclus des « autres »), autre = en plus. Calculé avec les sorties de circuit.</span></div>`;
    const t = d.travail || [];
    if (!t.length) html += `<div class="sc-vide">Pas encore de journée classée sur la période : « Tout recalculer » (onglet Présence Aquaworld) ou « Recalculer la période ».</div>`;
    else {
      html += `<table class="sc-t"><thead><tr><th>Technicien</th><th class="num">Jours</th><th class="num">🧑 Clients appariés</th><th class="num">❓ Probables</th><th class="num">🏢 Magasin</th><th class="num">🏠 Retours au domicile</th><th class="num">Jours avec retour</th><th class="num">Temps chez soi (médiane / jour)</th><th class="num">🚗 En chemin</th><th class="num">⚠️ Autres arrêts</th><th class="num">Temps</th><th class="num">Jours avec autres</th></tr></thead><tbody>`;
      t.forEach((e) => {
        html += `<tr><td><b>${esc(e.employe_nom)}</b></td><td class="num">${e.jours}</td><td class="num">${e.clients}</td><td class="num">${e.probables}</td><td class="num">${e.magasin}</td>
          <td class="num">${e.retours ? `<b style="color:#b45309">${e.retours}</b>` : "0"}</td><td class="num">${e.jours_retour} <span class="sc-dim">(${e.part_jours_retour} %)</span></td><td class="num">${e.minutes_domicile_mediane ? this._min(e.minutes_domicile_mediane) : "—"}</td><td class="num">${e.trajets || 0}</td>
          <td class="num">${e.autres}</td><td class="num">${e.minutes_autres ? this._min(e.minutes_autres) : ""}</td><td class="num">${e.jours_autres}</td></tr>`;
      });
      html += `</tbody></table>`;
      // Graphiques : composition des arrêts de la journée, et répartition des journées selon le nombre d'arrêts « en plus »
      // (autres) et de clients (appariés + probables) — demande du 16/10 : « un graph qui facilite la lecture ».
      const COL = { client: "#3b82f6", probable: "#818cf8", magasin: "#9ca3af", domicile: "#f59e0b", trajet: "#67e8f9", autre: "#ef4444" };
      const LIB = { client: "clients appariés", probable: "probables", magasin: "Magasin", domicile: "domicile", trajet: "en chemin", autre: "autres (en plus)" };
      const barres = (vals, col, titre, lib) => {
        const max = Math.max(1, ...vals), W = 220, H = 84, haut = 14, bas = 14, bw = W / vals.length;
        return `<div style="flex:1 1 220px"><div class="sc-dim" style="margin-top:6px">${titre}</div><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="display:block">
          ${vals.map((v, i) => { const h = (H - haut - bas) * v / max; return `<rect x="${i * bw + 4}" y="${H - bas - h}" width="${bw - 8}" height="${h}" rx="2" fill="${col}"><title>${lib(i)} : ${v} journée(s)</title></rect>
            <text x="${i * bw + bw / 2}" y="${H - bas - h - 3}" font-size="9" text-anchor="middle" fill="currentColor" opacity=".8">${v || ""}</text>
            <text x="${i * bw + bw / 2}" y="${H - 3}" font-size="9" text-anchor="middle" fill="currentColor" opacity=".6">${i === 5 ? "5+" : i}</text>`; }).join("")}</svg></div>`;
      };
      html += `<div class="sc-graphs">`;
      t.forEach((e) => {
        const comp = { client: e.clients, probable: e.probables, magasin: e.magasin, domicile: e.retours, trajet: e.trajets || 0, autre: e.autres };
        const tot = Object.values(comp).reduce((a, b) => a + b, 0) || 1;
        let x = 0;
        const stack = Object.entries(comp).map(([k, v]) => { const w = 100 * v / tot; const r = `<rect x="${x}" y="0" width="${w}" height="14" fill="${COL[k]}"><title>${LIB[k]} : ${v} (${Math.round(w)} %)</title></rect>`; x += w; return r; }).join("");
        const legende = Object.entries(comp).map(([k, v]) => `<span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${COL[k]};vertical-align:middle"></i> ${LIB[k]} ${Math.round(100 * v / tot)} %</span>`).join(" · ");
        html += `<div class="sc-graph"><b>${esc(e.employe_nom)}</b> <span class="sc-dim">· ${e.jours} journées · ${(tot / e.jours).toFixed(1).replace(".", ",")} arrêts / jour</span>
          <svg viewBox="0 0 100 14" width="100%" height="14" preserveAspectRatio="none" style="display:block;margin:4px 0 2px;border-radius:3px;overflow:hidden">${stack}</svg>
          <div class="sc-dim" style="font-size:11px">${legende}</div>
          <div style="display:flex;gap:14px;flex-wrap:wrap">${barres(e.jours_par_autres || [], COL.autre, "Journées selon le nombre d'arrêts en plus (autres)", (i) => (i === 5 ? "5 arrêts ou plus" : i + " arrêt(s) en plus"))}
          ${barres(e.jours_par_termines || [], COL.client, "Journées selon le nombre d'arrêts clients (appariés + probables)", (i) => (i === 5 ? "5 clients ou plus" : i + " client(s)"))}</div></div>`;
      });
      html += `</div>`;
      const jours = d.jours_travail || [];
      if (jours.length) {
        html += `<div class="sc-dim" style="margin:8px 0 4px">Jour par jour (${jours.length} journée${jours.length > 1 ? "s" : ""}${jours.length >= 120 ? ", les 120 plus récentes" : ""}) — passez la souris sur un arrêt pour la tâche :</div>`;
        html += `<table class="sc-t"><thead><tr><th>Jour</th><th>Technicien</th><th>Arrêts de la journée</th></tr></thead><tbody>`;
        jours.forEach((j) => {
          const chips = j.arrets.map((a) => {
            const [ic, lib] = CAT[a.c] || ["•", a.c];
            const titre = `${lib}${a.cl ? " — " + a.cl : ""}${a.t ? " (" + a.t + ")" : ""}${a.q ? " — " + a.q : ""}`;
            const style = a.c === "domicile" ? "background:#fef3c7" : a.c === "autre" ? "background:#fee2e2" : a.c === "probable" ? "background:#e0e7ff" : a.c === "trajet" ? "background:#cffafe" : "";
            return `<span class="sc-chip" style="${style}" title="${esc(titre)}">${a.h} ${ic} ${a.c === "client" || a.c === "probable" ? esc((a.cl || lib).slice(0, 18)) : lib} ${this._min(a.m)} <a class="sc-carte" href="https://www.google.com/maps?q=${a.la},${a.lo}" target="_blank" rel="noopener">🗺</a></span>`;
          }).join(" ");
          html += `<tr><td>${frappe.datetime.str_to_user(j.date)}</td><td>${esc(j.employe_nom || "")}<div class="sc-dim">${esc(j.vehicule || "")}</div></td><td>${chips || "<span class='sc-dim'>aucun arrêt dans la plage</span>"}</td></tr>`;
        });
        html += `</tbody></table>`;
      }
    }
    $r.find("#sc-journee").html(html);
  }

  async justifier(name, justification, note) {
    try {
      await frappe.call({ method: "customization_app.flotte_circuit.justifier", args: { name, justification, note } });
      const l = this.data.lignes.find((x) => x.name === name);
      if (l) { l.justification = justification; l.note = note; l.justifie_par = justification ? frappe.session.user : null; l.justifie_le = justification ? frappe.datetime.now_datetime() : null; }
      // Les compteurs « à regarder / justifiés » suivent sans relire
      const r = await frappe.call({ method: "customization_app.flotte_circuit.sorties", args: { du: this.data.du, au: this.data.au, employe: this.$root.find("#sc-employe").val() || null } });
      this.data = r.message;
      this._render();
      frappe.show_alert({ message: justification ? __("Justifié : {0}", [justification]) : __("Remis « à regarder »"), indicator: "green" });
    } catch (e) {
      frappe.msgprint(String(e.message || e));
    }
  }

  recalculer() {
    const du = this.$root.find("#sc-du").val(), au = this.$root.find("#sc-au").val();
    frappe.confirm(__("Relire la plateforme GPS du {0} au {1} et recalculer les écarts ? Les justifications sont conservées.", [frappe.datetime.str_to_user(du), frappe.datetime.str_to_user(au)]), async () => {
      try {
        const r = await frappe.call({ method: "customization_app.flotte_circuit.recalculer", args: { du, au }, freeze: true, freeze_message: __("Lecture de la plateforme GPS, jour par jour…") });
        const b = r.message || {};
        frappe.show_alert({ message: __("{0} jour(s) recalculé(s), {1} écart(s)", [b.jours, b.ecarts]), indicator: b.erreurs && b.erreurs.length ? "orange" : "green" });
        if (b.erreurs && b.erreurs.length) frappe.msgprint(b.erreurs.join("<br>"));
        this.charger();
      } catch (e) {
        frappe.msgprint(String(e.message || e));
      }
    });
  }
}

window.SortieCircuit = SortieCircuit;
