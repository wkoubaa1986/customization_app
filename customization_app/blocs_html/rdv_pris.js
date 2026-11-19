// Bloc « Rendez-vous pris » (onglet Relances, partenaires et réglages) — customization_app.tableau_rdv.get_tableau.
// Les 30 derniers jours, par canal : calendrier à la main / app par un utilisateur / app par le client.
// `root_element` = la racine du bloc (Frappe l'isole dans un shadow DOM) : tout se cherche à partir d'elle.
const $q = (sel) => root_element.querySelector(sel);
const esc = (s) => frappe.utils.escape_html(String(s == null ? "" : s));
const pct = (v, total) => (total ? Math.round((100 * v) / total) + " %" : "—");
// Le thème sombre se lit sur <html> : un sélecteur CSS ne traverse pas le shadow DOM.
$q(".rp").classList.toggle("sombre", document.documentElement.getAttribute("data-theme") === "dark");

async function charger() {
  let d;
  try {
    d = (await frappe.call({ method: "customization_app.tableau_rdv.get_tableau" })).message;
  } catch (e) {
    $q(".rp-resume").textContent = "Rendez-vous indisponibles.";
    return;
  }
  if (!d) return;
  const t = d.totaux, a = d.avant;
  const jour = (x) => frappe.datetime.str_to_user(x);
  const ecart = t.total - a.total;
  $q(".rp-resume").innerHTML = `Du ${jour(d.du)} au ${jour(d.au)}, comptés le jour où ils sont pris — ` +
    `<b>${t.total}</b> rendez-vous (${ecart > 0 ? "▲ +" + ecart : ecart < 0 ? "▼ −" + Math.abs(ecart) : "="} ` +
    `par rapport aux 30 jours précédents : ${a.total})`;
  $q(".rp-actions").innerHTML = `<button class="rp-btn" data-act="tableau">📊 Ouvrir le tableau complet</button>` +
    `<button class="rp-btn" data-act="maj">↻ Actualiser</button>`;
  $q(".rp-cartes").innerHTML = d.canaux.map((c) => `
    <div class="rp-carte" data-act="tableau">
      <div class="l"><i class="rp-pastille" style="background:var(--rp-${c.cle})"></i>${esc(c.libelle)}</div>
      <div class="v">${t[c.cle]}<small>${pct(t[c.cle], t.total)}</small></div>
      <div class="s">30 jours précédents : ${a[c.cle]}</div>
    </div>`).join("");
  $q(".rp-part").innerHTML = t.total ? d.canaux.filter((c) => t[c.cle]).map((c) =>
    `<div style="flex:${t[c.cle]} 0 0;background:var(--rp-${c.cle})" title="${esc(c.libelle)} : ${t[c.cle]} (${pct(t[c.cle], t.total)})"></div>`).join("") : "";
}

root_element.addEventListener("click", (ev) => {
  const el = ev.target.closest("[data-act]");
  if (!el) return;
  if (el.dataset.act === "maj") charger();
  else if (el.dataset.act === "tableau") frappe.set_route("rendez-vous-pris");
});

charger();
