// Barre « réunion qui approche » (demande du 24/11/2026), sur toutes les pages du Desk de chaque PARTICIPANT d'une
// réunion du Suivi d'activité : elle apparaît 1 h avant le début, JAUNE, devient ROUGE dans le dernier quart d'heure
// (et le reste pendant la réunion), et disparaît quand il entre dans la réunion (« Rejoindre »), quand la réunion est
// close ou annulée, ou quand l'heure de fin est passée. Réunion déplacée : tout suit le nouvel horaire.
// Données : customization_app.reunion_activite.barre_reunions ; rafraîchie par l'événement temps réel `reunion_barre`
// (création, déplacement, clôture…), toutes les 5 min, et recalculée toutes les 15 s à l'heure du serveur.
//
// Empilement : ce script range aussi les autres barres d'alerte (BL du Magasin, congés à valider) l'une sous l'autre
// et fait descendre le Desk de leur hauteur totale (.main-section, --navbar-height, .sticky-top).
(() => {
  const API = "customization_app.reunion_activite";
  const ORDRE = [".rb-bandeau", ".sm-bandeau", ".cg-bandeau", ".bm-bandeau"];   // .bm : bordereaux manquants (bank_retenue_sync)
  let reunions = [], ecart = 0, avant_min = 60, rouge_min = 15, $b = null;

  const style = `<style id="rb-style">
    .rb-bandeau { position: fixed; top: 0; left: 0; right: 0; z-index: 1036; height: 40px; box-sizing: border-box; padding: 0 14px;
      font-weight: 700; font-size: 14px; display: flex; justify-content: center; align-items: center; gap: 10px;
      white-space: nowrap; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
    .rb-bandeau.jaune { background: #facc15; color: #422006; }
    .rb-bandeau.rouge { background: #dc2626; color: #fff; animation: rb-pulse 1.6s ease-in-out infinite; }
    @keyframes rb-pulse { 0%, 100% { background: #dc2626; } 50% { background: #991b1b; } }
    .rb-bandeau .rb-txt { overflow: hidden; text-overflow: ellipsis; }
    .rb-bandeau .rb-bt { border: none; border-radius: 999px; padding: 3px 14px; font-size: 13px; font-weight: 700; cursor: pointer; }
    .rb-bandeau.jaune .rb-bt { background: #422006; color: #fde68a; }
    .rb-bandeau.rouge .rb-bt { background: #fff; color: #b91c1c; }
    @media (max-width: 768px) { .rb-bandeau { font-size: 12.5px; } .rb-bandeau .rb-lieu { display: none; } }
  </style>`;

  // ── Empilement des barres d'alerte ────────────────────────────────────────────────────────────────────────────────
  let base_nav = null;
  const empiler = () => {
    if (base_nav === null) base_nav = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--navbar-height"), 10) || 48;
    let y = 0;
    ORDRE.forEach((sel) => document.querySelectorAll(sel).forEach((el) => { el.style.top = y + "px"; y += el.offsetHeight || 40; }));
    const main = document.querySelector(".main-section");
    if (main) main.style.paddingTop = y ? y + "px" : "";
    if (y) document.body.style.setProperty("--navbar-height", base_nav + y + "px");
    else document.body.style.removeProperty("--navbar-height");
    document.querySelectorAll(".sticky-top").forEach((el) => { el.style.top = y ? y + "px" : ""; });
  };

  // ── Heure du serveur ──────────────────────────────────────────────────────────────────────────────────────────────
  const ms = (s) => { const m = String(s).match(/(\d+)-(\d+)-(\d+)[ T](\d+):(\d+)(?::(\d+))?/); return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : NaN; };
  const maintenant = () => Date.now() + ecart;          // en « heure du serveur » (UTC fictif, comme ms())
  const hm = (s) => String(s).slice(11, 16);
  const duree = (min) => (min >= 60 ? `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")}` : `${min} min`);

  const rejoindre = (r) => {
    frappe.xcall(`${API}.entrer`, { name: r.name }).then((x) => {
      reunions = reunions.filter((y) => y.name !== r.name);
      peindre();
      if (x && x.lien_visio) window.open(x.lien_visio, "_blank", "noopener");
      else { frappe.route_options = { reunion: r.name }; frappe.set_route("suivi-activite"); }
    }).catch(() => {});
  };

  const peindre = () => {
    const t = maintenant();
    const r = reunions.find((x) => t >= ms(x.debut) - avant_min * 60000 && t < ms(x.fin));
    if (!r) {
      if ($b) { $b.remove(); $b = null; empiler(); }
      return;
    }
    if (!document.getElementById("rb-style")) $("head").append(style);
    if (!$b) {
      $b = $(`<div class="rb-bandeau"><span>👥</span><span class="rb-txt"></span><button type="button" class="rb-bt">Rejoindre</button></div>`).appendTo("body");
    }
    const reste = Math.round((ms(r.debut) - t) / 60000);
    const rouge = t >= ms(r.debut) - rouge_min * 60000;
    $b.toggleClass("rouge", rouge).toggleClass("jaune", !rouge);
    const quand = reste > 0 ? `à ${hm(r.debut)} (dans ${duree(reste)})` : reste === 0 ? `maintenant (${hm(r.debut)})` : `a commencé à ${hm(r.debut)} (il y a ${duree(-reste)})`;
    $b.find(".rb-txt").html(`${frappe.utils.escape_html(r.titre)} ${quand}${r.lieu ? `<span class="rb-lieu"> · ${frappe.utils.escape_html(r.lieu)}</span>` : ""}${r.visio ? '<span class="rb-lieu"> · visio</span>' : ""}`);
    $b.find(".rb-bt").off("click").on("click", () => rejoindre(r));
    empiler();
  };

  const charger = () => {
    frappe.xcall(`${API}.barre_reunions`).then((c) => {
      if (!c) return;
      ecart = ms(c.maintenant) - Date.now();
      avant_min = c.avant_min || 60; rouge_min = c.rouge_min || 15;
      reunions = c.reunions || [];
      peindre();
    }).catch(() => {});
  };

  $(document).on("app_ready", () => {
    if (!frappe.session || frappe.session.user === "Guest") return;
    // Les autres barres (magasin, congés) apparaissent et disparaissent seules : on ré-empile à chaque changement.
    new MutationObserver(empiler).observe(document.body, { childList: true });
    charger();
    setInterval(peindre, 15000);
    setInterval(charger, 300000);
    frappe.realtime.on("reunion_barre", charger);
  });
})();
