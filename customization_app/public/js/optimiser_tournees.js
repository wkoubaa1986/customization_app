/**
 * « 🗺️ Optimiser la journée » — calendrier des tâches (demande du 02/10/2026).
 *
 * Pour le jour affiché, redistribue et réordonne les tâches de terrain entre les employés qui travaillent
 * ce jour-là, pour que chacun roule moins (règle et calcul : customization_app.tournee_optimisation).
 * La proposition se lit d'abord — par employé, avant / après, tâches déplacées ou décalées, itinéraire
 * Google Maps — et ne s'écrit qu'au bouton « Appliquer ».
 *
 * Même injection que « Ma journée » : un vrai bouton dans la barre du calendrier (les boutons Frappe se
 * replient dans un menu sur téléphone).
 */
(() => {
  const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
  const ROLES = ["System Manager", "Responsable magasin"];
  const API = "customization_app.tournee_optimisation.";

  function dateAffichee() {
    try {
      const cal = window.cur_list && cur_list.calendar;
      if (cal && cal.getDate) return frappe.datetime.obj_to_str(cal.getDate());
      const $cal = window.cur_list && cur_list.$cal;
      if ($cal && $cal.fullCalendar) return $cal.fullCalendar("getDate").format("YYYY-MM-DD");
    } catch (e) { /* calendrier pas prêt */ }
    return frappe.datetime.get_today();
  }

  function itineraire(depot, arrets) {
    const pts = arrets.filter((a) => a.lat && a.lng).map((a) => `${a.lat},${a.lng}`);
    if (!pts.length) return "";
    const d = `${depot[0]},${depot[1]}`;
    return `https://www.google.com/maps/dir/?api=1&travelmode=driving&origin=${d}&destination=${d}&waypoints=${encodeURIComponent(pts.join("|"))}`;
  }

  function rendre(p) {
    if (!p.employes.length) return `<div class="text-muted" style="padding:16px">${esc(p.message || "Rien à optimiser.")}</div>`;
    const t = p.total, gain = t.avant_min - t.apres_min;
    const ligneAvant = (a) => `<div style="font-size:12.5px;padding:2px 0;${a.fixe ? "color:#64748b" : ""}">${esc(a.debut)}–${esc(a.fin)} · ${esc(a.client)} <span class="text-muted">${esc(a.type)}</span>${a.fixe ? " 📌" : ""}</div>`;
    const ligneApres = (a) => `<div style="font-size:12.5px;padding:2px 0;${a.deplace ? "background:#fef3c7;border-radius:4px" : a.decale ? "background:#eff6ff;border-radius:4px" : ""}${a.fixe ? ";color:#64748b" : ""}">
        ${esc(a.debut)}–${esc(a.fin)} · <b>${esc(a.client)}</b> <span class="text-muted">${esc(a.type)}</span>${a.fixe ? " 📌" : ""}
        ${a.deplace ? `<span style="color:#b45309;font-size:11px"> ↔ venait de ${esc(a.de_nom)}</span>` : a.decale ? `<span style="color:#1d4ed8;font-size:11px"> ⏱ heure changée</span>` : a.non_place ? `<span style="color:#b91c1c;font-size:11px"> ⚠️ hors tournée (chevauchement ou hors journée), inchangée</span>` : ""}
        ${a.position === "secteur" ? `<span style="color:#b91c1c;font-size:11px"> ≈ secteur</span>` : ""}</div>`;
    const cartes = p.employes.map((e) => `<div style="border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px">
          <b>👤 ${esc(e.nom)}</b> <span class="text-muted" style="font-size:11.5px">départ ${esc(e.depart)}</span>
          <span style="font-size:12.5px">${e.avant.km} km · ${e.avant.minutes} min → <b>${e.apres.km} km · ${e.apres.minutes} min</b>
            ${itineraire(e.depart_point || p.depot, e.apres.arrets) ? ` · <a href="${itineraire(e.depart_point || p.depot, e.apres.arrets)}" target="_blank" rel="noopener">🗺️ itinéraire</a>` : ""}</span>
        </div>
        <div class="row" style="margin-top:6px">
          <div class="col-sm-6"><div class="text-muted" style="font-size:11px;text-transform:uppercase">Actuel (${e.avant.arrets.length})</div>${e.avant.arrets.map(ligneAvant).join("") || `<div class="text-muted" style="font-size:12px">—</div>`}</div>
          <div class="col-sm-6"><div class="text-muted" style="font-size:11px;text-transform:uppercase">Proposé (${e.apres.arrets.length})</div>${e.apres.arrets.map(ligneApres).join("") || `<div class="text-muted" style="font-size:12px">—</div>`}</div>
        </div></div>`).join("");
    return `<div style="font-size:13px;margin-bottom:8px">
        <b>${t.avant_km} km · ${t.avant_min} min</b> de route aujourd’hui → <b>${t.apres_km} km · ${t.apres_min} min</b>
        <span style="color:${gain > 0 ? "#15803d" : "#b45309"};font-weight:700"> (${gain > 0 ? "−" : "+"}${Math.abs(gain)} min)</span>
        · ${p.deplacees} tâche(s) changent d’employé · ${p.decalees} changent d’heure · départ ${esc(p.journee[0])}, visites dès ${esc(p.premiere)}, retour ${esc(p.journee[1])} (<a href="/app/config-optimisation-tournees">réglages</a>)
        · distances : ${esc(p.source)}</div>
      ${p.non_places.length ? `<div style="color:#b91c1c;font-size:12.5px;margin-bottom:6px">⚠️ ${p.non_places.length} tâche(s) ne tiennent pas dans la tournée (deux rendez-vous à la même heure, ou hors journée) : elles restent telles quelles, marquées dans la liste.</div>` : ""}
      ${p.avertissements.length ? `<div style="color:#b45309;font-size:12px;margin-bottom:6px">${p.avertissements.map(esc).join("<br>")}</div>` : ""}
      <div class="text-muted" style="font-size:11.5px;margin-bottom:8px">📌 fixe (type non déplaçable, épinglée, réparation au local, terminée) · 🟨 change d’employé · 🟦 change d’heure · ≈ position approchée</div>
      ${cartes}`;
  }

  function ouvrir() {
    let proposition = null;
    const d = new frappe.ui.Dialog({
      title: "🗺️ Optimiser la journée",
      size: "extra-large",
      fields: [
        { fieldtype: "Date", fieldname: "date", label: "Jour", default: dateAffichee(), reqd: 1 },
        { fieldtype: "Column Break" },
        { fieldtype: "HTML", fieldname: "aide", options: `<div class="text-muted" style="font-size:12px;margin-top:26px">Seuls les employés qui ont des tâches ce jour-là sont utilisés. Une tâche cochée « Heure et employé fixes » ne bouge pas. Rien n’est écrit avant « Appliquer ».</div>` },
        { fieldtype: "Section Break" },
        { fieldtype: "HTML", fieldname: "resultat" },
      ],
      primary_action_label: "Calculer",
      primary_action: async (v) => {
        d.fields_dict.resultat.$wrapper.html(`<div class="text-muted" style="padding:16px">Calcul des distances et des tournées…</div>`);
        d.set_secondary_action_label("");
        proposition = (await frappe.call({ method: API + "proposer", args: { date: v.date }, freeze: true, freeze_message: "Optimisation…" })).message;
        d.fields_dict.resultat.$wrapper.html(rendre(proposition));
        const n = proposition.deplacees + proposition.decalees;
        if (n) {
          d.set_secondary_action_label(`✅ Appliquer (${n} tâche(s))`);
          d.set_secondary_action(() => {
            const plan = [];
            proposition.employes.forEach((e) => e.apres.arrets.forEach((a) => { if (a.deplace || a.decale) plan.push({ tache: a.tache, employe: a.employe, starts_on: a.starts_on, ends_on: a.ends_on }); }));
            frappe.confirm(`Appliquer la proposition : <b>${proposition.deplacees}</b> tâche(s) changent d’employé, <b>${proposition.decalees}</b> changent d’heure ?<br>Un commentaire sera posé sur chaque tâche modifiée.`, async () => {
              const r = (await frappe.call({ method: API + "appliquer", args: { date: proposition.date, plan }, freeze: true, freeze_message: "Application…" })).message;
              d.hide();
              frappe.show_alert({ message: `🗺️ ${r.modifiees.length} tâche(s) mise(s) à jour`, indicator: "green" }, 6);
              if (window.cur_list && cur_list.refresh) cur_list.refresh();
            });
          });
        }
      },
    });
    d.show();
  }

  window.optimiserTournees_ouvrir = ouvrir;

  function poser_bouton(essais) {
    if (document.getElementById("btn-optimiser-tournees")) return;
    if (!frappe.user.has_role(ROLES)) return;
    const barre = document.querySelector(".fc-header-toolbar .fc-left, .fc-toolbar .fc-left, .fc-toolbar-chunk") || document.querySelector(".fc-toolbar");
    if (!barre) { if ((essais || 0) < 12) setTimeout(() => poser_bouton((essais || 0) + 1), 600); return; }
    const btn = document.createElement("button");
    btn.id = "btn-optimiser-tournees";
    btn.innerHTML = "🗺️ Optimiser";
    btn.title = "Redistribuer et réordonner les tâches du jour entre les employés présents pour rouler moins";
    btn.style.cssText = "background:#059669;color:#fff;border:none;border-radius:6px;padding:6px 14px;font-weight:700;font-size:13px;cursor:pointer;margin-left:10px;box-shadow:0 1px 3px rgba(0,0,0,.2)";
    btn.onclick = ouvrir;
    const ancre = document.getElementById("btn-ma-journee") || document.getElementById("btn-generer-bl") || document.querySelector(".fc-today-button");
    if (ancre && ancre.parentNode) ancre.parentNode.insertBefore(btn, ancre.nextSibling); else barre.appendChild(btn);
  }

  const _cal = frappe.views.CalendarView && frappe.views.CalendarView.prototype;
  if (_cal && _cal.render) {
    const _render = _cal.render;
    _cal.render = function () {
      const out = _render.apply(this, arguments);
      try { if (this.doctype === "Tache de travail") setTimeout(() => poser_bouton(0), 500); } catch (e) { /* jamais bloquant */ }
      return out;
    };
  }
})();
