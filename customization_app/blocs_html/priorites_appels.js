// Bloc « Mes appels par priorité » (onglet Appels) — customization_app.tableau_appels.get_priorites.
// `root_element` = la racine du bloc (Frappe l'isole dans un shadow DOM) : tout se cherche à partir d'elle.
const $q = (sel) => root_element.querySelector(sel);
const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));
const nb = (v) => (v == null ? "—" : v);

function ouvrir(f) {
  frappe.route_options = Object.assign({}, f.options || {});
  frappe.set_route(...f.route);
}

async function charger() {
  let p;
  try {
    p = (await frappe.call({ method: "customization_app.tableau_appels.get_priorites" })).message;
  } catch (e) {
    $q(".pa-resume").textContent = "Priorités indisponibles.";
    return;
  }
  if (!p) return;
  const suivante = p.files.find((f) => f.suivante);
  $q(".pa-resume").innerHTML =
    `Aujourd’hui : <b>${p.total}</b> action(s) · <b class="rdv">📅 ${p.rdv} rendez-vous pris</b>` +
    (p.rdv_hors ? ` (dont ${p.rdv_hors} hors files)` : "") +
    (suivante ? ` — 👉 à faire maintenant : <b>${esc(suivante.libelle)}</b>` : " — ✅ toutes les files sont vides");
  $q(".pa-actions").innerHTML =
    (p.tableau ? `<button class="pa-btn" data-act="tableau">📊 Tableau des appels</button>` : "") +
    `<button class="pa-btn" data-act="maj">↻ Actualiser</button>`;
  $q(".pa-files").innerHTML = p.files.map((f, i) => `
    <div class="pa-file ${f.suivante ? "suivante" : ""} ${f.reste === 0 ? "vide" : ""}" data-i="${i}">
      <div class="pa-rang">${f.rang}</div>
      <div><div class="pa-nom">${esc(f.libelle)}</div>
        <div class="pa-sous">${f.suivante ? "👉 à faire maintenant" : f.reste === 0 ? "✓ rien en attente" : ""}</div></div>
      <div class="pa-chiffre"><b>${nb(f.reste)}</b><span>reste à faire</span></div>
      <div class="pa-chiffre pa-fait"><b>${f.fait}</b><span>fait aujourd’hui</span></div>
      <div class="pa-chiffre pa-rdv ${f.rdv ? "" : "zero"}"><b>${f.rdv}</b><span>RDV pris</span></div>
      <div class="pa-ouvrir"><button class="pa-btn" data-ouvrir="${i}">Ouvrir ›</button></div>
    </div>`).join("");
  const autres = p.autres.map((a) => `${esc(a.libelle)} <b>${a.fait}</b>`)
    .concat(p.rdv_hors ? [`Rendez-vous hors files <b>${p.rdv_hors}</b>`] : []);
  $q(".pa-autres").innerHTML = autres.length ? "Autres suivis aujourd’hui : " + autres.join(" · ") : "";
  root_element.__files = p.files;
}

root_element.addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.ouvrir !== undefined) ouvrir(root_element.__files[+b.dataset.ouvrir]);
  else if (b.dataset.act === "maj") charger();
  else if (b.dataset.act === "tableau") frappe.set_route("tableau-appels");
});

charger();
// Actualisation toutes les 2 minutes tant que le bloc est affiché.
const minuterie = setInterval(() => {
  if (!root_element.host || !root_element.host.isConnected) return clearInterval(minuterie);
  if (document.visibilityState === "visible") charger();
}, 120000);
