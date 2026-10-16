// Présence Aquaworld — onglet du suivi terrain (rôle « Sortie Circuit » : Wassim, Néjib), à côté des Sorties de circuit.
// Chargé par `frappe.require("/assets/customization_app/js/presence_aquaworld.js")`.
// Heures d'arrivée / départ à Aquaworld et lieux de nuit du véhicule (Journee Flotte GPS, customization_app.flotte_presence) ;
// domiciles détectés d'après les nuits, à régler d'un clic dans le réglage des tournées.

const PA_LIEU = { Domicile: ["dom", "🏠 domicile"], Magasin: ["mag", "🏢 Magasin"], Autre: ["autre", "❓ autre"] };
const PA_VERDICT = {
  ok: ["ok", "✅ domicile réglé = lieu de nuit"], a_regler: ["warn", "📍 à régler"], different: ["bad", "⚠️ réglé ailleurs"],
  magasin: ["mag", "🏢 le véhicule dort au Magasin"], chez_autre: ["mag", "🚗 véhicule rendu chez un collègue"], insuffisant: ["dim", "pas assez de nuits"],
};

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
  </style>
  <div class="pa-bar">
    <label class="pa-dim">Du</label> <input type="date" id="pa-du">
    <label class="pa-dim">au</label> <input type="date" id="pa-au">
    <select id="pa-employe"><option value="">Tous les techniciens</option></select>
    <button class="btn btn-default btn-sm" data-action="refresh">🔄 Actualiser</button>
    <button class="btn btn-default btn-sm" data-action="recalculer" title="Relit la plateforme GPS jour par jour (au plus deux mois)">🧮 Recalculer la période</button>
    <span class="pa-info" id="pa-info"></span>
  </div>
  <div class="pa-intro" id="pa-intro"></div>
  <div class="pa-bloc" id="pa-domiciles"></div>
  <div class="pa-bloc" id="pa-stats"></div>
  <div class="pa-bloc" id="pa-liste"></div>
</div>
`;
  }

  constructor(racine) {
    this.$root = $(racine).find(".pa-page");
    this.data = null;
    this.$root.find("#pa-du").val(frappe.datetime.add_days(frappe.datetime.get_today(), -29));
    this.$root.find("#pa-au").val(frappe.datetime.get_today());
    this._bind();
    this.charger();
  }

  _bind() {
    const $r = this.$root;
    $r.find("[data-action='refresh'], #pa-du, #pa-au, #pa-employe").on("change click", (e) => { if (e.type === "click" && e.currentTarget.tagName !== "BUTTON") return; this.charger(); });
    $r.find("[data-action='recalculer']").on("click", () => this.recalculer());
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
    const n = d.lignes.length - d.non_calculees;
    $r.find("#pa-info").text(`${n} journée${n > 1 ? "s" : ""} du ${frappe.datetime.str_to_user(d.du)} au ${frappe.datetime.str_to_user(d.au)}${d.non_calculees ? ` · ${d.non_calculees} sans présence calculée (Recalculer)` : ""}`);

    // Domiciles d'après les nuits
    let html = `<div class="t">🏠 Où dort le véhicule de chacun <span class="pa-dim">— nuits regroupées à ${d.seuils.lieu_m} m ; « Définir comme domicile » écrit le point de départ de l'employé dans le réglage des tournées (au moins ${d.seuils.nuits_min} nuits)</span></div>`;
    if (!d.domiciles.length) html += `<div class="pa-vide">Aucune nuit connue sur la période. Cliquez « Recalculer la période » pour lire la plateforme GPS.</div>`;
    else {
      html += `<table class="pa-t"><thead><tr><th>Technicien</th><th>Verdict</th><th>Lieux de nuit (du plus fréquent au moins)</th><th>Domicile réglé</th></tr></thead><tbody>`;
      d.domiciles.forEach((e) => {
        const [vc, vt] = PA_VERDICT[e.verdict] || ["dim", e.verdict];
        const lieux = e.lieux.slice(0, 4).map((c) => {
          const quoi = c.magasin ? `🏢 Magasin` : c.regle ? `🏠 domicile réglé` : c.chez ? `🚗 domicile de ${esc(c.chez_nom || c.chez)}` : `❓ ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
          const btn = !c.exclu && !c.regle && c.n >= d.seuils.nuits_min
            ? ` <button class="btn btn-xs btn-default" data-domicile="${esc(e.employe)}|${c.lat}|${c.lng}|${esc(e.employe_nom)}">📍 Définir comme domicile</button>` : "";
          return `<li><a class="pa-carte" href="https://www.google.com/maps?q=${c.lat},${c.lng}" target="_blank" rel="noopener">🗺</a> ${quoi} <b>×${c.n}</b> <span class="pa-dim">(${c.part} % · dernière ${frappe.datetime.str_to_user(c.derniere)})</span>${btn}</li>`;
        }).join("");
        const regle = e.regle ? `<a class="pa-carte" href="https://www.google.com/maps?q=${e.regle[0]},${e.regle[1]}" target="_blank" rel="noopener">🗺 ${e.regle[0].toFixed(4)}, ${e.regle[1].toFixed(4)}</a>${e.ecart_m ? `<div class="pa-dim">à ${e.ecart_m >= 1000 ? (e.ecart_m / 1000).toFixed(1).replace(".", ",") + " km" : e.ecart_m + " m"} du lieu principal</div>` : ""}` : `<span class="pa-dim">aucun (Magasin)</span>`;
        html += `<tr><td><b>${esc(e.employe_nom)}</b><div class="pa-dim">${e.nuits} nuit${e.nuits > 1 ? "s" : ""}</div></td><td><span class="pa-lieu ${vc}">${vt}</span></td><td><ul class="pa-lieux">${lieux}</ul></td><td>${regle}</td></tr>`;
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
        html += `<tr><td><b>${esc(e.employe_nom || e.employe || "?")}</b>${e.immobiles ? `<div class="pa-dim">${e.immobiles} jour(s) sans bouger</div>` : ""}</td><td class="num">${e.jours}</td><td class="num">${e.jours_magasin} <span class="pa-dim">(${e.jours ? Math.round(100 * e.jours_magasin / e.jours) : 0} %)</span></td>
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
        const note = l.immobile ? `<div class="pa-dim">véhicule immobile toute la journée</div>` : l.en_cours ? `<div class="pa-dim">journée en cours</div>` : "";
        html += `<tr><td>${esc(l.employe_nom || "")}${l.employe_nom ? `<div class="pa-dim">${esc(l.vehicule || "")}</div>` : `<b>${esc(l.vehicule || "")}</b><div class="pa-dim">sans employé reconnu</div>`}${note}</td>
          <td>${this._lieu(l.nuit_avant_lieu, l.nuit_avant_lat, l.nuit_avant_lng)}</td><td class="num">${this._h(l.depart_matin)}</td><td class="num">${this._h(l.arrivee_aquaworld)}</td>
          <td class="num">${this._h(l.depart_aquaworld)}</td><td class="num">${this._h(l.retour_soir)}</td><td>${this._lieu(l.nuit_lieu, l.nuit_lat, l.nuit_lng)}</td>
          <td class="num">${l.minutes_aquaworld ? this._min(l.minutes_aquaworld) : "—"}</td><td class="num">${l.km ? String(Math.round(l.km)).replace(".", ",") : ""}</td></tr>`;
      });
      html += `</tbody></table>`;
    }
    $r.find("#pa-liste").html(html);
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
