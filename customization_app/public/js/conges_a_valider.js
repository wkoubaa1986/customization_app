// Barre orange des demandes de congé à valider (demande du 24/11/2026), sur toutes les pages du Desk de ceux qui
// valident les congés (rôle « Congés RH » : lui et Nejib Koubaa, le gérant). Tant qu'une demande est « Open », la barre
// reste en haut de l'écran ; un clic ouvre « Congés & récupérations », section « Demandes à valider ».
// Rafraîchie par l'événement temps réel `conges_a_valider` (conges_recuperations.signaler_changement) et toutes les
// 2 minutes. Pour les autres utilisateurs, l'API répond null : rien n'est affiché.
// Se place SOUS la barre rouge du magasin (bl_magasin_alerte.js) si les deux sont là.
(() => {
  const API = "customization_app.conges_recuperations.compteur_demandes";
  let dernier = -1, $b = null, actif = true;
  const style = `<style>
    .cg-bandeau { position: fixed; top: 0; left: 0; right: 0; z-index: 1034; background: #d97706; color: #fff; height: 40px;
      box-sizing: border-box; padding: 0 14px; font-weight: 700; font-size: 14px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.2);
      display: flex; justify-content: center; align-items: center; gap: 10px; white-space: nowrap; overflow: hidden; }
    .cg-bandeau .cg-txt { overflow: hidden; text-overflow: ellipsis; }
    .cg-bandeau .cg-bt { background: #fff; color: #b45309; border-radius: 999px; padding: 3px 12px; font-size: 13px; }
    /* Le contenu descend de la hauteur de la barre : marge sur .main-section (celle du <body> n'a aucun effet dans le
       Desk — l'en-tête de page recouvrait les filtres de la liste des commandes, 24/11/2026) ; les éléments collants
       du Desk (en-tête, onglets d'un formulaire) se calent sur --navbar-height. */
    body.cg-bandeau-actif { --navbar-height: 88px; }
    body.cg-bandeau-actif .main-section { padding-top: 40px; }
    body.cg-bandeau-actif .sticky-top { top: 40px; }
    body.sm-bandeau-actif .cg-bandeau { top: 42px; }
    body.sm-bandeau-actif.cg-bandeau-actif { --navbar-height: 130px; }
    body.sm-bandeau-actif.cg-bandeau-actif .main-section { padding-top: 82px; }
    body.sm-bandeau-actif.cg-bandeau-actif .sticky-top { top: 82px; }
    @media (max-width: 768px) { .cg-bandeau { font-size: 13px; } .cg-bandeau .cg-detail { display: none; } }
  </style>`;
  const jj = (iso) => { const p = String(iso).split("-"); return p[2] + "/" + p[1]; };
  const texte = (c) => {
    const a = c.apercu[0];
    const detail = a ? ` — ${a.nom} : ${a.type}, ${jj(a.du)}${a.au !== a.du ? " → " + jj(a.au) : ""}${c.n > 1 ? " …" : ""}` : "";
    return { court: `${c.n} demande${c.n > 1 ? "s" : ""} de congé à valider`, detail };
  };
  const ouvrir = () => { frappe.route_options = { demandes: 1 }; frappe.set_route("conges-recuperations"); };
  const notifier = (txt) => {
    try {
      if (!("Notification" in window)) return;
      if (Notification.permission === "default") Notification.requestPermission();
      if (Notification.permission === "granted") {
        const n = new Notification("🏖️ Congés", { body: txt, tag: "cg-conges", renotify: true });
        n.onclick = () => { window.focus(); ouvrir(); n.close(); };
      }
    } catch (e) { /* ignoré */ }
  };
  const peindre = (c) => {
    const n = c ? c.n || 0 : 0;
    if (!n) { if ($b) { $b.remove(); $b = null; $("body").removeClass("cg-bandeau-actif"); } dernier = 0; return; }
    const t = texte(c);
    if (!$b) {
      if (!$("#cg-style").length) $("head").append($(style).attr("id", "cg-style"));
      $b = $(`<div class="cg-bandeau" title="Ouvrir Congés & récupérations"><span>🏖️</span><span class="cg-txt"><span class="cg-court"></span><span class="cg-detail"></span></span><span class="cg-bt">Voir et valider</span></div>`).appendTo("body");
      $("body").addClass("cg-bandeau-actif");
      $b.on("click", ouvrir);
    }
    $b.find(".cg-court").text(t.court);
    $b.find(".cg-detail").text(t.detail);
    if (dernier >= 0 && n > dernier) notifier(t.court + t.detail);
    dernier = n;
  };
  const charger = () => {
    if (!actif) return;
    frappe.xcall(API).then((c) => { if (c === null || c === undefined) { actif = false; return; } peindre(c); }).catch(() => {});
  };
  $(document).on("app_ready", () => {
    if (window.frameElement) return;   // fiche ouverte en fenêtre (ouvrir_document.js) : l'alerte est déjà dessous
    if (!frappe.session || frappe.session.user === "Guest") return;
    charger();
    setInterval(charger, 120000);
    frappe.realtime.on("conges_a_valider", charger);
  });
})();
