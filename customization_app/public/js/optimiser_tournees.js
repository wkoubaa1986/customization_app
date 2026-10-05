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

  // Le trajet dans Google Maps. Google liste les étapes DANS L'ORDRE (ligne 1 = départ, ligne 2 = 1er client…) et les
  // nomme lui-même d'après le lieu le plus proche — on ne peut pas lui donner les noms des clients (un nom serait CHERCHÉ
  // comme un lieu). Chaque client de la page porte donc son NUMÉRO d'ordre : 1 = 1er client après le départ. Un lien
  // accepte 9 étapes au plus : au-delà, plusieurs liens qui s'enchaînent (le suivant part du dernier client du précédent).
  // → { liens: [{url, de, a}], numeros: { tache: n } }.
  const ETAPES_MAX = 9;
  function itineraires(depot, arrets) {
    const pts = arrets.filter((a) => a.lat && a.lng);
    if (!pts.length) return { liens: [], numeros: {} };
    const numeros = {};
    pts.forEach((x, i) => { if (!(x.tache in numeros)) numeros[x.tache] = i + 1; });
    const suite = [{ lat: depot[0], lng: depot[1], n: 0 }, ...pts.map((x, i) => ({ lat: x.lat, lng: x.lng, n: i + 1 })), { lat: depot[0], lng: depot[1], n: 0 }];
    const liens = [];
    for (let i = 0; i < suite.length - 1; i += ETAPES_MAX + 1) {
      const m = suite.slice(i, i + ETAPES_MAX + 2), o = m[0], dst = m[m.length - 1];
      const etapes = m.slice(1, -1).map((x) => `${x.lat},${x.lng}`);
      const clients = m.filter((x) => x.n).map((x) => x.n);
      liens.push({ de: Math.min(...clients), a: Math.max(...clients),
        url: `https://www.google.com/maps/dir/?api=1&travelmode=driving&origin=${o.lat},${o.lng}&destination=${dst.lat},${dst.lng}`
          + (etapes.length ? `&waypoints=${encodeURIComponent(etapes.join("|"))}` : "") });
    }
    return { liens, numeros };
  }

  const pastille = (lettre, couleur) => lettre ? `<span title="${esc(lettre)}e client de l’itinéraire Google Maps (ligne ${esc(+lettre + 1)} de sa liste)" style="display:inline-block;min-width:18px;padding:0 4px;border-radius:9px;background:${couleur};color:#fff;font-size:10.5px;font-weight:700;text-align:center;margin-right:4px">${esc(lettre)}</span>` : "";
  const COULEURS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#be185d", "#4d7c0f"];

  // ── La carte de la journée (OpenStreetMap, Leaflet déjà chargé par le Desk) ─────────────────────────────
  // Une couleur par employé ; chaque étape porte sa lettre Google et le nom du client ; ≈ = position approchée
  // (contour en pointillés) ; un clic ouvre la tâche. Les traits sont droits : le vrai chemin est dans le lien Google.
  let carte = null;                          // la carte du résultat de l'optimisation
  const nomEtape = (a) => a.client || `${a.type || "Tâche"} · ${a.tache}`;      // une tâche sans client : son type
  function dessinerCarte(p, el, ancienne) {
    if (ancienne) { try { ancienne.remove(); } catch (e) { /* ancienne carte déjà retirée de la page */ } }
    let carte = null;
    if (!document.getElementById("opt-carte-style")) {
      const st = document.createElement("style");
      st.id = "opt-carte-style";
      st.textContent = ".opt-etiquette { font-size: 11px; padding: 1px 5px; border-radius: 6px; box-shadow: none; }";
      document.head.appendChild(st);
    }
    if (!window.L) { el.innerHTML = `<div class="text-muted" style="padding:12px">Carte indisponible (bibliothèque Leaflet absente).</div>`; return null; }
    carte = L.map(el, { scrollWheelZoom: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(carte);
    const bornes = [];
    const point = (lat, lng) => { bornes.push([lat, lng]); return [lat, lng]; };
    const depots = new Set();
    p.employes.forEach((e, i) => {
      const c = COULEURS[i % COULEURS.length], dep = e.depart_point || p.depot, it = itineraires(dep, e.apres.arrets);
      const cle = `${dep[0]},${dep[1]}`;
      if (!depots.has(cle)) {
        depots.add(cle);
        L.marker(point(dep[0], dep[1]), { icon: L.divIcon({ className: "", html: `<div style="font-size:20px;line-height:20px">${e.depart === "domicile" ? "🏠" : "🏬"}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }) })
          .bindTooltip(`${e.depart === "domicile" ? "Domicile de " + esc(e.nom) : "Magasin"}`, { direction: "top" }).addTo(carte);
      }
      const trace = [[dep[0], dep[1]]];
      e.apres.arrets.forEach((a) => {
        if (!(a.lat && a.lng)) return;
        trace.push(point(a.lat, a.lng));
        const approx = a.position === "secteur", lettre = it.numeros[a.tache] || "";
        L.marker([a.lat, a.lng], { icon: L.divIcon({ className: "", iconSize: [24, 24], iconAnchor: [12, 12],
          html: `<div style="width:24px;height:24px;border-radius:12px;background:${c};color:#fff;font-weight:700;font-size:11px;display:flex;align-items:center;justify-content:center;border:2px ${approx ? "dashed" : "solid"} #fff;box-shadow:0 0 0 1px ${c}">${esc(lettre)}</div>` }) })
          .bindTooltip(`${esc(lettre)} · ${esc(nomEtape(a))} <span style="color:#64748b">${esc(a.debut)}</span>${approx ? " ≈" : ""}`, { permanent: true, direction: "right", offset: [12, 0], className: "opt-etiquette" })
          .bindPopup(`<b>${esc(lettre)} · ${esc(nomEtape(a))}</b><br>${esc(e.nom)} · ${esc(a.debut)}–${esc(a.fin)} · ${esc(a.type)}${approx ? "<br>≈ position approchée" : ""}<br>`
            + `<a href="/app/tache-de-travail/${encodeURIComponent(a.tache)}" data-ouvrir-tache="${esc(a.tache)}">ouvrir la tâche</a>`)
          .addTo(carte);
      });
      trace.push([dep[0], dep[1]]);
      L.polyline(trace, { color: c, weight: 3, opacity: 0.75, dashArray: "6 6" }).addTo(carte);
    });
    if (bornes.length) carte.fitBounds(bornes, { padding: [30, 30] });
    setTimeout(() => carte && carte.invalidateSize(), 50);
    return carte;
  }

  // « +1 h 30 » / « −45 min » : de combien l'heure d'une tâche bouge par rapport à l'actuel.
  const decalage = (mn) => {
    const a = Math.abs(mn), h = Math.floor(a / 60), m = a % 60;
    return (mn < 0 ? "−" : "+") + (h ? `${h} h${m ? " " + String(m).padStart(2, "0") : ""}` : `${m} min`);
  };

  // « 📨 Clients à prévenir » : qui recevra quoi si on applique — le texte exact, avec la plage horaire.
  function blocNotifications(p) {
    const pv = p.prevenir || {}, actif = pv.sms || pv.email, n = (p.notifications || []).length;
    const canaux = [pv.sms ? "SMS" : "", pv.email ? "e-mail" : ""].filter(Boolean).join(" + ");
    if (!actif) return `<div class="text-muted" style="font-size:12px;margin-bottom:8px;padding:6px 10px;background:#f8fafc;border-radius:6px">📨 Les clients ne sont pas prévenus des changements d’heure (à activer dans <a href="/app/config-optimisation-tournees" target="_blank">⚙️ Réglages → Prévenir les clients</a>).</div>`;
    if (!n) return `<div class="text-muted" style="font-size:12px;margin-bottom:8px">📨 Aucun client à prévenir (aucune heure ne bouge de ${pv.seuil} min ou plus).</div>`;
    const sansNum = pv.sms ? p.notifications.filter((x) => !x.numeros.length).length : 0, sansMail = pv.email ? p.notifications.filter((x) => !x.emails.length).length : 0;
    const sans = [sansNum ? `${sansNum} sans numéro` : "", sansMail ? `${sansMail} sans e-mail` : ""].filter(Boolean).join(", ");
    return `<div style="border:1px solid #bfdbfe;background:#eff6ff;border-radius:8px;padding:8px 12px;margin-bottom:10px;font-size:12.5px">
      <label style="margin:0;display:flex;align-items:center;gap:8px"><input type="checkbox" id="opt-prevenir" checked> <b>📨 Prévenir ${n} client(s) par ${canaux}</b>
        <span class="text-muted" style="font-size:11.5px">plage de ${pv.plage} min centrée sur la nouvelle heure · envoyé à l’application, trace sur chaque tâche${sans ? ` · ⚠️ ${sans}` : ""}</span>
        <a href="#" id="opt-voir-messages" style="margin-left:auto;font-size:11.5px">▸ voir les messages</a></label>
      <div id="opt-messages" style="display:none;margin-top:8px">${p.notifications.map((x) => `
        <div style="border-top:1px solid #dbeafe;padding:6px 0">
          <div><b>${esc(x.client)}</b> · ${esc(x.ancienne_heure)} → ${esc(x.heure)} (${esc(x.demi)} entre ${esc(x.plage)})
            <span class="text-muted" style="font-size:11.5px"> · 📱 ${x.numeros.length ? esc(x.numeros.join(", ")) : "<span style=\"color:#b91c1c\">aucun numéro</span>"} · ✉️ ${x.emails.length ? esc(x.emails.join(", ")) : "<span style=\"color:#b91c1c\">aucun e-mail</span>"}</span></div>
          <pre style="white-space:pre-wrap;font-family:inherit;font-size:12px;margin:4px 0 0;color:#334155;background:#fff;border-radius:6px;padding:6px 8px">${esc(x.sms)}</pre>
          ${pv.email && x.email !== x.sms ? `<pre style="white-space:pre-wrap;font-family:inherit;font-size:12px;margin:4px 0 0;color:#334155;background:#fff;border-radius:6px;padding:6px 8px">✉️ ${esc(x.email)}</pre>` : ""}
        </div>`).join("")}</div></div>`;
  }

  // D'où viennent les heures proposées : Google (trafic habituel, trajet par trajet) ou, à défaut, OSRM corrigé.
  function blocRecalage(p) {
    const r = p.recalage || {};
    const appels = `${r.nouveaux_appels || 0} nouvel(s) appel(s) Google · ${r.appels_du_jour || 0}/${r.plafond || 0} aujourd’hui`;
    if (r.source === "google") return `<span style="font-size:12px;color:#15803d">🚗 <b>Heures calculées avec Google</b> (trafic habituel à l’heure de chaque trajet) · ${r.trajets_google} trajets · ${appels} · ordre choisi sur OSRM × ${p.coef}</span>`;
    if (r.source === "mixte") return `<span style="font-size:12px;color:#b45309">🚗 Heures calculées avec Google pour ${r.trajets_google} trajet(s), OSRM corrigé (≈) pour ${r.trajets_osrm} : ${esc(r.raison || "")} · ${appels}</span>`;
    return `<span style="font-size:12px;color:#b45309">≈ <b>Heures calculées sans Google</b> (${esc(r.raison || "")}) : OSRM × ${p.coef} et heures de pointe.</span>`;
  }

  function rendre(p) {
    if (!p.employes.length) return `<div class="text-muted" style="padding:16px">${esc(p.message || "Rien à optimiser.")}</div>`;
    const t = p.total, gain = t.avant_min - t.apres_min;
    const ligneAvant = (a) => `<div style="font-size:12.5px;padding:2px 0;${a.fixe ? "color:#64748b" : ""}">${esc(a.debut)}–${esc(a.fin)} · ${lienTache(a.tache, esc(a.client))} <span class="text-muted">${esc(a.type)}</span>${a.fixe ? " 📌" : ""}</div>`;
    // Le trajet qui MÈNE à l'arrêt (🚗 Google, ≈ OSRM corrigé), puis ce qui cloche : retard sur une heure fixe, attente.
    const trajetAvant = (a) => a.trajet == null ? "" : `<span class="text-muted" style="font-size:11px">${a.trajet_source === "sur place" ? "📍 sur place" : `${a.trajet_source === "google" ? "🚗" : "≈"} ${duree(a.trajet)}${a.trajet_km ? ` · ${a.trajet_km} km` : ""}`} → </span>`;
    const marques = (a) => (a.retard ? `<span style="color:#b91c1c;font-size:11px;font-weight:600"> ⚠️ arrivée ${a.retard} min après l’heure fixe</span>` : "")
      + (a.hors_fenetre ? `<span style="color:#b91c1c;font-size:11px;font-weight:600"> ⚠️ hors de sa fenêtre</span>` : "")
      + (a.attente >= 15 ? `<span class="text-muted" style="font-size:11px"> ⏳ ${duree(a.attente)} d’attente avant</span>` : "");
    const ligneApres = (a, lettre = "") => `<div style="font-size:12.5px;padding:2px 0;${a.deplace ? "background:#fef3c7;border-radius:4px" : a.decale ? "background:#eff6ff;border-radius:4px" : ""}${a.fixe ? ";color:#64748b" : ""}">
        ${lettre}${trajetAvant(a)}${esc(a.debut)}–${esc(a.fin)} · ${lienTache(a.tache, `<b>${esc(a.client)}</b>`)} <span class="text-muted">${esc(a.type)}</span>${a.fixe ? " 📌" : ""}${marques(a)}
        ${a.deplace ? `<span style="color:#b45309;font-size:11px"> ↔ venait de ${esc(a.de_nom)}</span>` : ""}${a.decale ? `<span style="color:#1d4ed8;font-size:11px"> ⏱ était à ${esc(a.ancien_debut)} (${decalage(a.ecart_min)})</span>` : ""}${a.non_place ? `<span style="color:#b91c1c;font-size:11px"> ⚠️ hors tournée (chevauchement ou hors journée), inchangée</span>` : ""}
        ${a.position === "secteur" ? `<span style="color:#b91c1c;font-size:11px"> ≈ secteur</span>` : ""}</div>`;
    const cartes = p.employes.map((e, i) => { const it = itineraires(e.depart_point || p.depot, e.apres.arrets), coul = COULEURS[i % COULEURS.length]; return `<div style="border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px">
          <b>👤 ${esc(e.nom)}</b> <span class="text-muted" style="font-size:11.5px">départ ${esc(e.depart)} · journée ${esc((e.journee || [])[0] || "")}–${esc((e.journee || [])[1] || "")}</span>
          <span style="font-size:12.5px">${e.avant.km} km · ${e.avant.minutes} min → <b>${e.apres.km} km · ${e.apres.minutes} min</b>
            ${it.liens.map((l, n) => ` · <a href="${l.url}" target="_blank" rel="noopener" title="Dans Google Maps, les clients apparaissent dans l’ordre des numéros de la page">🗺️ itinéraire${it.liens.length > 1 ? ` ${n + 1}/${it.liens.length} (clients ${l.de} à ${l.a})` : ""}</a>`).join("")}</span>
          ${e.avant.fin ? `<span class="text-muted" style="font-size:11.5px;flex-basis:100%">⏱ interventions ${e.avant.interventions} min · fin ${esc(e.avant.fin)} → <b>interventions ${e.apres.interventions} min · fin ${esc(e.apres.fin)}</b> <span style="font-size:11px">(l’équilibrage rapproche les fins de journée)</span></span>` : ""}
        </div>
        ${e.horaires && e.horaires.depart ? `<div style="font-size:12.5px;margin-top:6px;padding:6px 10px;border-radius:8px;background:${e.horaires.depasse || e.horaires.retards ? "#fef2f2" : "#f0fdf4"}">
          🕘 <b>Départ ${esc(e.depart)} ${esc(e.horaires.depart)}</b> → <b>retour ${esc(e.horaires.retour)}</b>${e.horaires.depasse ? ` <b style="color:#b91c1c">(après la fin de journée ${esc(e.horaires.fin_journee)})</b>` : ""}
          · trajets ${duree(e.horaires.trajets)} · ${e.horaires.km} km${e.horaires.retour_trajet != null ? ` <span class="text-muted">(dont retour ${duree(e.horaires.retour_trajet)})</span>` : ""}
          ${e.horaires.retards ? ` · <b style="color:#b91c1c">⚠️ ${e.horaires.retards} arrivée(s) en retard ou hors fenêtre</b>` : ""}</div>` : ""}
        <div class="row" style="margin-top:6px">
          <div class="col-sm-6"><div class="text-muted" style="font-size:11px;text-transform:uppercase">Actuel (${e.avant.arrets.length})</div>${e.avant.arrets.map(ligneAvant).join("") || `<div class="text-muted" style="font-size:12px">—</div>`}</div>
          <div class="col-sm-6"><div class="text-muted" style="font-size:11px;text-transform:uppercase">Proposé (${e.apres.arrets.length})</div>${e.apres.arrets.map((a) => ligneApres(a, pastille(it.numeros[a.tache], coul))).join("") || `<div class="text-muted" style="font-size:12px">—</div>`}</div>
        </div></div>`; }).join("");
    return `<div style="font-size:13px;margin-bottom:8px">
        <b>${t.avant_km} km · ${t.avant_min} min</b> de route aujourd’hui → <b>${t.apres_km} km · ${t.apres_min} min</b>
        <span style="color:${gain > 0 ? "#15803d" : "#b45309"};font-weight:700"> (${gain > 0 ? "−" : "+"}${Math.abs(gain)} min)</span>
        · ${p.deplacees} tâche(s) changent d’employé · ${p.decalees} changent d’heure · départ ${esc(p.journee[0])}, visites dès ${esc(p.premiere)}, retour ${esc(p.journee[1])}${p.pause ? `, pause ${esc(p.pause[0])}–${esc(p.pause[1])}` : ""} · stationnement ${p.marge} min + rangement ${p.rangement} min par arrêt${(p.pointes || []).length ? ` · pointes ${p.pointes.map(esc).join(", ")}` : ""} · ${p.fenetre ? `heures gardées à ± ${p.fenetre} min` : "heures libres"} (<a href="/app/config-optimisation-tournees">réglages</a>)
        · distances : ${esc(p.source)}</div>
      <div style="margin-bottom:8px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        ${blocRecalage(p)}
        <button type="button" class="btn btn-xs btn-default" id="opt-carte-btn" title="Les tournées proposées sur une carte : une couleur par employé, chaque étape avec sa lettre Google et le nom du client">🗺️ Carte de la journée</button></div>
      <div id="opt-carte" style="display:none;height:460px;border-radius:10px;margin:0 0 10px;border:1px solid #e2e8f0"></div>
      ${p.non_places.length ? `<div style="color:#b91c1c;font-size:12.5px;margin-bottom:6px">⚠️ ${p.non_places.length} tâche(s) ne tiennent pas dans la tournée : elles restent à leur heure et leur employé, marquées dans la liste, et leur créneau est réservé (rien d’autre dessus).${p.fenetre ? ` Souvent dû à la fenêtre ± ${p.fenetre} min : élargissez-la ou décochez « Garder les heures proches » pour recalculer.` : " Deux rendez-vous à la même heure chez le même employé, ou journée trop chargée."}</div>` : ""}
      ${p.avertissements.length ? `<div style="color:#b45309;font-size:12px;margin-bottom:6px">${p.avertissements.map(esc).join("<br>")}</div>` : ""}
      <div class="text-muted" style="font-size:11.5px;margin-bottom:8px">📌 fixe (type non déplaçable, épinglée, réparation au local, terminée) · 🟨 change d’employé · 🟦 change d’heure · ≈ position approchée</div>
      ${blocNotifications(p)}
      ${cartes}`;
  }

  // Lien vers une tâche : un clic l'ouvre PAR-DESSUS l'optimisation (ouvrirTache), Ctrl/Cmd + clic dans un onglet.
  const lienTache = (tache, html) => tache
    ? `<a href="/app/tache-de-travail/${encodeURIComponent(tache)}" data-ouvrir-tache="${esc(tache)}" style="color:inherit">${html}</a>` : html;

  // La fiche complète de la tâche (modifiable, avec ses boutons) dans une fenêtre au-dessus de l'optimisation, sans la
  // barre du Desk (demande du 05/10/2026). `siModifiee` est appelé à la fermeture si la tâche a été enregistrée.
  async function ouvrirTache(tache, siModifiee) {
    const avant = (await frappe.db.get_value("Tache de travail", tache, "modified")).message?.modified;
    const p = new frappe.ui.Dialog({ title: `🗂️ ${tache}`, size: "extra-large", fields: [{ fieldtype: "HTML", fieldname: "fiche" }] });
    p.fields_dict.fiche.$wrapper.html(`<div class="text-muted" data-chargement style="padding:12px">Chargement de la tâche…</div>
      <iframe src="/app/tache-de-travail/${encodeURIComponent(tache)}" style="width:100%;height:78vh;border:0;display:block;visibility:hidden"></iframe>`);
    const $if = p.fields_dict.fiche.$wrapper.find("iframe");
    $if.on("load", () => {
      try {
        const doc = $if[0].contentDocument, st = doc.createElement("style");
        st.textContent = ":root { --navbar-height: 0px !important; } header.navbar, .navbar { display: none !important; }"
          + " .layout-side-section, .raven-chat { display: none !important; } body { padding-top: 0 !important; } .page-head { top: 0 !important; }";
        doc.head.appendChild(st);
      } catch (e) { /* fiche affichée telle quelle */ }
      p.fields_dict.fiche.$wrapper.find("[data-chargement]").remove();
      $if.css("visibility", "visible");
    });
    p.onhide = async () => {
      const apres = (await frappe.db.get_value("Tache de travail", tache, "modified")).message?.modified;
      if (apres && apres !== avant && siModifiee) siModifiee();
    };
    p.show();
  }

  // « 1 h 52 » / « 45 min ».
  const duree = (mn) => (mn >= 60 ? `${Math.floor(mn / 60)} h ${String(mn % 60).padStart(2, "0")}` : `${mn} min`);

  function ouvrir() {
    let proposition = null;
    const d = new frappe.ui.Dialog({
      title: "🗺️ Optimiser la journée",
      size: "extra-large",
      fields: [
        { fieldtype: "Date", fieldname: "date", label: "Jour", default: dateAffichee(), reqd: 1 },
        { fieldtype: "Check", fieldname: "proche", label: "Garder les heures proches de l’actuel", default: 1,
          description: "Chaque tâche déplaçable reste à ± la fenêtre ci-dessous autour de son heure actuelle" },
        { fieldtype: "Int", fieldname: "fenetre", label: "Fenêtre (± minutes)", default: 60 },
        { fieldtype: "Column Break" },
        { fieldtype: "HTML", fieldname: "aide", options: `<div style="margin-top:26px;display:flex;gap:12px;align-items:flex-start">
          <div class="text-muted" style="font-size:12px;flex:1">Seuls les employés qui ont des tâches ce jour-là sont utilisés. Une tâche cochée « Heure et employé fixes » ne bouge pas. Rien n’est écrit avant « Appliquer ».</div>
          <a class="btn btn-default btn-xs" href="/app/config-optimisation-tournees" target="_blank" title="Magasin, heures, pause, stationnement, heures de pointe, types, exclus, domiciles" style="white-space:nowrap">⚙️ Réglages</a></div>` },
        { fieldtype: "Section Break", label: "Employés du jour" },
        { fieldtype: "HTML", fieldname: "employes" },
        { fieldtype: "Section Break" },
        { fieldtype: "HTML", fieldname: "resultat" },
      ],
      primary_action_label: "Calculer",
      primary_action: async (v) => {
        const choix = [];
        d.fields_dict.employes.$wrapper.find("[data-emp]").each((_, el) => {
          const $e = $(el);
          // Les heures ne sont envoyées que si vous les avez touchées : saisies, elles sont VOULUES (la journée
          // s'arrête là, le reste va ailleurs) ; sinon la journée s'étend au travail déjà planifié.
          if ($e.find("input[type=checkbox]").is(":checked")) choix.push({ employe: $e.attr("data-emp"), depart: $e.find("select").val(),
            debut: $e.find("input[data-debut]").attr("data-modifie") ? $e.find("input[data-debut]").val() || null : null,
            fin: $e.find("input[data-fin]").attr("data-modifie") ? $e.find("input[data-fin]").val() || null : null });
        });
        if (!choix.length) { frappe.msgprint("Cochez au moins un employé."); return; }
        d.fields_dict.resultat.$wrapper.html(`<div class="text-muted" style="padding:16px">Calcul des distances et des tournées…</div>`);
        d.set_secondary_action_label("");
        proposition = (await frappe.call({ method: API + "proposer", args: { date: v.date, fenetre: v.proche ? (v.fenetre || 60) : 0, employes: choix }, freeze: true, freeze_message: "Optimisation, puis heures calculées avec Google…" })).message;
        if ((proposition.sans_domicile || []).length) frappe.show_alert({ message: `Pas de domicile réglé pour ${esc(proposition.sans_domicile.join(", "))} : départ du Magasin. (Réglages → Points de départ particuliers)`, indicator: "orange" }, 8);
        d.fields_dict.resultat.$wrapper.html(rendre(proposition));
        d.fields_dict.resultat.$wrapper.find("#opt-carte-btn").on("click", (ev) => {
          const el = d.fields_dict.resultat.$wrapper.find("#opt-carte")[0], ouvrir = !$(el).is(":visible");
          $(el).toggle(ouvrir);
          $(ev.currentTarget).text(ouvrir ? "🗺️ Masquer la carte" : "🗺️ Carte de la journée");
          if (ouvrir) carte = dessinerCarte(proposition, el, carte);
        });
        d.fields_dict.resultat.$wrapper.find("#opt-voir-messages").on("click", (ev) => {
          ev.preventDefault();
          const $m = d.fields_dict.resultat.$wrapper.find("#opt-messages"); $m.toggle();
          $(ev.currentTarget).text($m.is(":visible") ? "▾ masquer les messages" : "▸ voir les messages");
        });
        const n = proposition.deplacees + proposition.decalees;
        if (n) {
          d.set_secondary_action_label(`✅ Appliquer (${n} tâche(s))`);
          d.set_secondary_action(() => {
            const plan = [], notifs = new Set((proposition.notifications || []).map((x) => x.tache));
            const prevenir = d.fields_dict.resultat.$wrapper.find("#opt-prevenir").is(":checked") ? 1 : 0;
            proposition.employes.forEach((e) => e.apres.arrets.forEach((a) => { if (a.deplace || a.decale) plan.push({ tache: a.tache, employe: a.employe, starts_on: a.starts_on, ends_on: a.ends_on, prevenir: notifs.has(a.tache) ? 1 : 0 }); }));
            const nb = prevenir ? notifs.size : 0;
            frappe.confirm(`Appliquer la proposition : <b>${proposition.deplacees}</b> tâche(s) changent d’employé, <b>${proposition.decalees}</b> changent d’heure ?<br>Un commentaire sera posé sur chaque tâche modifiée.${nb ? `<br><b>📨 ${nb} client(s)</b> recevront le message de changement d’horaire.` : ""}`, async () => {
              const r = (await frappe.call({ method: API + "appliquer", args: { date: proposition.date, plan, prevenir }, freeze: true, freeze_message: "Application…" })).message;
              d.hide();
              frappe.show_alert({ message: `🗺️ ${r.modifiees.length} tâche(s) mise(s) à jour${r.prevenus ? ` · 📨 ${r.prevenus} client(s) prévenus en arrière-plan` : ""}`, indicator: "green" }, 6);
              if (window.cur_list && cur_list.refresh) cur_list.refresh();
            });
          });
        }
      },
    });
    // Les tâches d'un employé sous sa ligne : heure, type, client, adresse ; grisée si elle ne peut pas bouger (et pourquoi).
    const ligneTaches = (liste) => liste.length ? `<table style="width:100%;font-size:12px;border-collapse:collapse">${liste.map((t) => `
        <tr style="${t.mobile ? "" : "color:#94a3b8"}">
          <td style="padding:1px 8px 1px 0;white-space:nowrap">${esc(t.debut)}${t.fin ? "–" + esc(t.fin) : ""}</td>
          <td style="padding:1px 8px 1px 0;white-space:nowrap">${lienTache(t.tache, esc(t.type))}</td>
          <td style="padding:1px 8px 1px 0">${lienTache(t.tache, `<b>${esc(t.client)}</b>`)}</td>
          <td style="padding:1px 8px 1px 0">${esc(t.adresse)}${t.secteur ? ` <span class="text-muted">· ${esc(t.secteur)}</span>` : ""}${t.position === "secteur" ? ' <span title="Position approchée par le centre du secteur">≈</span>' : ""}</td>
          <td style="padding:1px 0;white-space:nowrap;text-align:right">${t.mobile ? "🔀 déplaçable" : esc(t.motif)}</td>
        </tr>`).join("")}</table>` : `<span class="text-muted">Aucune tâche.</span>`;
    const chargerEmployes = async () => {
      const date = d.get_value("date");
      if (!date) return;
      // L'ancienne liste ne doit pas rester affichée pendant le chargement du nouveau jour.
      d.fields_dict.employes.$wrapper.html(`<div class="text-muted" style="font-size:12px;padding:6px 0">⏳ Chargement des tâches du ${esc(frappe.datetime.str_to_user(date))}…</div>`);
      const liste = (await frappe.call({ method: API + "employes_du_jour", args: { date } })).message || [];
      if (d.get_value("date") !== date) return;      // une autre date a été choisie entre-temps : sa réponse fera foi
      d.fields_dict.employes.$wrapper.html(liste.length ? `<div class="text-muted" style="font-size:12px;margin-bottom:4px">Cochez qui entre dans l’optimisation et d’où chacun part et revient. Un employé décoché garde ses tâches telles quelles.</div>
        ${liste.map((e) => `<div data-emp="${esc(e.employe)}" style="display:flex;align-items:center;gap:10px;padding:4px 0;border-bottom:1px solid #f1f5f9">
          <label style="margin:0;display:flex;align-items:center;gap:6px;min-width:220px"><input type="checkbox" ${e.exclu ? "" : "checked"}> <b>${esc(e.nom)}</b> <span class="text-muted" style="font-size:11.5px">${e.taches} tâche(s), ${e.mobiles} déplaçable(s)${e.exclu ? " · exclu par le réglage" : ""}</span></label>
          <select class="form-control input-xs" style="width:auto;height:26px;padding:0 6px;font-size:12px">
            <option value="magasin">🏬 Départ et retour : Magasin</option>
            <option value="domicile" ${e.domicile ? "" : "disabled"}>🏠 Départ et retour : domicile${e.domicile ? "" : " (non réglé)"}</option>
          </select>
          <span style="font-size:11.5px;white-space:nowrap" title="Heures de ce calcul. Si vous les modifiez, la journée s’arrête à l’heure saisie et ce qui ne tient pas va à un autre employé ; sinon elle s’étend au travail déjà planifié.">🕘 <input type="time" data-debut value="${esc(e.debut)}" style="height:26px;font-size:12px;width:92px"> → <input type="time" data-fin value="${esc(e.fin)}" style="height:26px;font-size:12px;width:92px"></span>
          <a href="#" data-memoriser="${esc(e.employe)}" data-nom="${esc(e.nom)}" title="Enregistrer ces heures comme horaires habituels de cet employé" style="font-size:11.5px;white-space:nowrap">${e.horaire_propre ? "💾 horaires mémorisés" : "💾 mémoriser"}</a>
          <a href="#" data-regler="${esc(e.employe)}" data-nom="${esc(e.nom)}" style="font-size:11.5px;white-space:nowrap">${e.domicile ? "📍 changer le domicile" : "📍 régler le domicile"}</a>
          <a href="#" data-plier style="font-size:11.5px;white-space:nowrap;margin-left:auto" title="Voir les tâches de la journée">▸ tâches</a></div>
          <div data-taches style="display:none;padding:2px 0 6px 28px;border-bottom:1px solid #f1f5f9">${ligneTaches(e.liste || [])}</div>`).join("")}
        <div class="text-muted" style="font-size:11.5px;margin-top:4px">🕘 Début et fin de journée de chacun pour ce calcul (« 💾 mémoriser » les garde pour les jours suivants). Le domicile se règle une fois (lien Google Maps). <a href="/app/config-optimisation-tournees" target="_blank">⚙️ Tous les réglages</a>.</div>` : `<div class="text-muted" style="font-size:12px">Aucun employé n’a de tâche ce jour-là.</div>`);
      d.fields_dict.employes.$wrapper.find("[data-plier]").on("click", (ev) => {
        ev.preventDefault();
        const $a = $(ev.currentTarget), $bloc = $a.closest("[data-emp]").next("[data-taches]");
        $bloc.toggle();
        $a.text($bloc.is(":visible") ? "▾ tâches" : "▸ tâches");
      });
      d.fields_dict.employes.$wrapper.find("input[data-debut], input[data-fin]").on("change", (ev) => $(ev.currentTarget).attr("data-modifie", "1"));
      d.fields_dict.employes.$wrapper.find("[data-memoriser]").on("click", async (ev) => {
        ev.preventDefault();
        const $row = $(ev.currentTarget).closest("[data-emp]"), emp = $row.attr("data-emp"), nom = $(ev.currentTarget).attr("data-nom");
        const debut = $row.find("input[data-debut]").val(), fin = $row.find("input[data-fin]").val();
        if (!debut || !fin) { frappe.msgprint("Renseignez le début et la fin."); return; }
        await frappe.call({ method: API + "definir_horaires", args: { employe: emp, debut, fin }, freeze: true });
        frappe.show_alert({ message: `Horaires de ${esc(nom)} mémorisés : ${debut} → ${fin}`, indicator: "green" }, 4);
        $(ev.currentTarget).text("💾 horaires mémorisés");
      });
      d.fields_dict.employes.$wrapper.find("[data-regler]").on("click", (ev) => {
        ev.preventDefault();
        const emp = $(ev.currentTarget).attr("data-regler"), nom = $(ev.currentTarget).attr("data-nom");
        frappe.prompt([
          { fieldtype: "HTML", options: `<div class="text-muted" style="font-size:12px;margin-bottom:6px">Ouvrez Google Maps sur le domicile de <b>${esc(nom)}</b> → Partager → copiez le lien, collez-le ici. Vide = il repart du Magasin.</div>` },
          { fieldtype: "Data", fieldname: "lien", label: "Lien Google Maps du domicile" },
        ], async (v) => {
          const r = (await frappe.call({ method: API + "definir_depart", args: { employe: emp, lien: v.lien || "" }, freeze: true })).message;
          frappe.show_alert({ message: r.domicile ? `Domicile de ${esc(nom)} enregistré` : `${esc(nom)} repart du Magasin`, indicator: "green" }, 4);
          await chargerEmployes();
          if (r.domicile) d.fields_dict.employes.$wrapper.find(`[data-emp="${CSS.escape(emp)}"] select`).val("domicile");
        }, `📍 Domicile de ${nom}`, "Enregistrer");
      });
    };
    d.fields_dict.date.$input.on("change", () => setTimeout(chargerEmployes, 200));
    d.$wrapper.on("click", "[data-ouvrir-tache]", (ev) => {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button === 1) return;        // nouvel onglet, comme avant
      // Le routeur de Frappe (router.js, sur body) suit sinon le lien /app/… et quitte la page.
      ev.preventDefault();
      ev.stopPropagation();
      ouvrirTache($(ev.currentTarget).attr("data-ouvrir-tache"), () => {
        chargerEmployes();
        if (proposition) frappe.show_alert({ message: "Tâche modifiée : relancez « Calculer » pour une proposition à jour.", indicator: "orange" }, 6);
      });
    });
    d.show();
    d.set_value("proche", 1);        // le `default` d'une case à cocher n'est pas appliqué par le dialogue
    chargerEmployes();
  }

  window.optimiserTournees_ouvrir = ouvrir;

  // ── « 🗺️ Itinéraire » d'une journée TELLE QU'ELLE EST PLANIFIÉE (Ma journée, Générer les BL) ─────────────
  // Même calcul que la fin de l'optimisation (customization_app.tournee_itineraire) : trajets mesurés par Google à leur
  // heure, départ et retour, km, temps de route, retards ; numéros = ordre dans le lien Google Maps ; carte.
  const resumeJournee = (e) => {
    const h = (e && e.horaires) || {};
    if (!h.depart) return "";
    return `🕘 Départ ${esc(e.depart)} <b>${esc(h.depart)}</b> → retour <b>${esc(h.retour)}</b> · route <b>${duree(h.trajets)}</b> · <b>${h.km} km</b>`
      + (h.retards ? ` · <b style="color:#b91c1c">⚠️ ${h.retards} arrivée(s) en retard</b>` : "")
      + (h.depasse ? ` <b style="color:#b91c1c">(après la fin de journée ${esc(h.fin_journee)})</b>` : "");
  };
  const sourceTemps = (r) => {
    const x = (r && r.recalage) || {};
    return x.source === "google" ? "🚗 trajets mesurés par Google (trafic habituel à l’heure de chaque trajet)"
      : x.source === "mixte" ? "🚗 trajets mesurés par Google, ≈ OSRM corrigé pour les trajets déjà passés ou au-delà du plafond"
      : `≈ trajets estimés sans Google (${esc(x.raison || "")}) : OSRM × ${esc(r.coef)}`;
  };

  async function itineraireJournee(date, employe) {
    const r = (await frappe.call({ method: "customization_app.tournee_itineraire.itineraire_employe", args: { date, employe },
      freeze: true, freeze_message: "Itinéraire : trajets mesurés par Google…" })).message || {};
    const e = (r.employes || [])[0];
    const d = new frappe.ui.Dialog({ title: `🗺️ Itinéraire${e ? " — " + e.nom : ""} — ${frappe.datetime.str_to_user(date)}`, size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "corps" }] });
    let carteIti = null;
    if (!e) {
      d.fields_dict.corps.$wrapper.html(`<div class="text-muted" style="padding:12px">Aucune tâche à conduire ce jour-là.</div>`);
      d.show();
      return;
    }
    const it = itineraires(e.depart_point || r.depot, e.apres.arrets), h = e.horaires || {}, coul = COULEURS[0];
    const lignes = e.apres.arrets.map((a) => `<div style="font-size:13px;padding:5px 0;border-bottom:1px solid #f1f5f9">
        ${pastille(it.numeros[a.tache], coul)}${a.trajet != null ? `<span class="text-muted" style="font-size:11.5px">${a.trajet_source === "sur place" ? "📍 sur place" : `${a.trajet_source === "google" ? "🚗" : "≈"} ${duree(a.trajet)}${a.trajet_km ? ` · ${a.trajet_km} km` : ""}`} → </span>` : ""}
        <b>${esc(a.debut)}</b>–${esc(a.fin)} · ${lienTache(a.tache, `<b>${esc(nomEtape(a))}</b>`)} <span class="text-muted">${esc(a.type)}</span>${a.position === "secteur" ? ` <span style="color:#b91c1c;font-size:11px">≈ position approchée</span>` : ""}
        ${a.retard ? `<div style="color:#b91c1c;font-size:11.5px;font-weight:600;margin-left:24px">⚠️ arrivée prévue ${a.retard} min après l’heure du rendez-vous</div>` : ""}</div>`).join("");
    d.fields_dict.corps.$wrapper.html(`
      <div style="font-size:13px;padding:8px 12px;border-radius:8px;background:${h.retards || h.depasse ? "#fef2f2" : "#f0fdf4"}">${resumeJournee(e)}</div>
      <div class="text-muted" style="font-size:11.5px;margin:4px 2px 8px">${sourceTemps(r)} · stationnement ${esc(r.marge)} min + rangement ${esc(r.rangement)} min par arrêt</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px">${it.liens.map((l, n) => `<a class="btn btn-sm btn-primary" href="${l.url}" target="_blank" rel="noopener">🗺️ Ouvrir dans Google Maps${it.liens.length > 1 ? ` ${n + 1}/${it.liens.length} (clients ${l.de} à ${l.a})` : ""}</a>`).join("")}</div>
      <div class="text-muted" style="font-size:11.5px;margin-bottom:6px">Dans Google Maps, les clients sont dans l’ordre des numéros ci-dessous (Google les nomme d’après le lieu le plus proche).</div>
      ${lignes}
      ${h.retour ? `<div style="font-size:13px;padding:5px 0"><span class="text-muted" style="font-size:11.5px">${h.retour_source === "google" ? "🚗" : "≈"} ${duree(h.retour_trajet || 0)} → </span><b>${esc(h.retour)}</b> · retour ${esc(e.depart)}</div>` : ""}
      ${(e.hors_itineraire || []).length ? `<div class="text-muted" style="font-size:12px;margin-top:6px">Hors itinéraire : ${e.hors_itineraire.map((x) => `${esc(x.client || x.tache)} (${esc(x.motif)})`).join(" · ")}</div>` : ""}
      <div data-carte-iti style="height:320px;border-radius:10px;margin-top:10px;border:1px solid #e2e8f0"></div>`);
    d.$wrapper.on("click", "[data-ouvrir-tache]", (ev) => {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button === 1) return;
      ev.preventDefault();
      ev.stopPropagation();          // sinon le routeur de Frappe suit le lien /app/… et quitte la page
      ouvrirTache($(ev.currentTarget).attr("data-ouvrir-tache"));
    });
    d.onhide = () => { if (carteIti) { try { carteIti.remove(); } catch (x) { /* déjà retirée */ } carteIti = null; } };
    d.show();
    setTimeout(() => { carteIti = dessinerCarte({ employes: [e], depot: r.depot }, d.$wrapper.find("[data-carte-iti]")[0]); }, 250);
  }

  window.tournees_itineraire = itineraireJournee;
  window.tournees_resume = resumeJournee;

  frappe.realtime.on("envoi_taches_termine", (m) => {
    if (!m || !document.getElementById("btn-optimiser-tournees")) return;
    frappe.show_alert({ message: `📨 Messages clients : ${m.sms_envoyes} SMS, ${m.emails_envoyes} e-mail(s)${m.echecs ? `, ${m.echecs} échec(s)` : ""}${m.simulation ? " (simulés en dev)" : ""} — détail en commentaire sur chaque tâche`, indicator: m.echecs ? "orange" : "green" }, 10);
  });

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
