// Bandeau rouge du MAGASINIER PRINCIPAL (rôle « Magasinier principal ») sur toutes les pages du Desk : tant qu'un BL
// du Magasin attend son approbation, ou qu'une sortie exceptionnelle attend sa vérification, le bandeau reste en haut
// de l'écran (clic → onglet BL de la page Stock). Chaque nouveauté fait un bip et une notification du navigateur.
// Rafraîchi par l'événement temps réel `sm_bl_magasin` (sorties_magasin._prevenir) et toutes les 2 minutes.
// Pour les autres utilisateurs, l'API répond null : rien n'est affiché.
(() => {
  const API = "customization_app.sorties_magasin.compteur";
  let dernier = -1, $b = null, actif = true;
  const style = `<style>
    .sm-bandeau { position: fixed; top: 0; left: 0; right: 0; z-index: 1035; background: #dc2626; color: #fff; height: 42px; box-sizing: border-box; padding: 0 14px;
      font-weight: 700; font-size: 14px; text-align: center; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.25);
      animation: sm-pulse 1.6s ease-in-out infinite; display: flex; justify-content: center; align-items: center; gap: 10px; }
    .sm-bandeau .sm-bt { background: #fff; color: #b91c1c; border-radius: 999px; padding: 3px 12px; font-size: 13px; white-space: nowrap; }
    @keyframes sm-pulse { 0%, 100% { background: #dc2626; } 50% { background: #991b1b; } }
    /* Le Desk ancre tout (barre de boutons du formulaire, onglets) sur --navbar-height : on l'augmente de la hauteur
       du bandeau, et l'en-tête collant (.sticky-top) descend d'autant — sinon les boutons passent sous l'en-tête. */
    body.sm-bandeau-actif { padding-top: 42px; --navbar-height: 90px; }
    body.sm-bandeau-actif .sticky-top { top: 42px; }
  </style>`;
  const texte = (c) => {
    const m = [];
    if (c.a_approuver) m.push(`${c.a_approuver} BL du Magasin à approuver`);
    if (c.a_verifier) m.push(`${c.a_verifier} sortie${c.a_verifier > 1 ? "s" : ""} exceptionnelle${c.a_verifier > 1 ? "s" : ""} à vérifier`);
    if (c.ecarts) m.push(`${c.ecarts} écart${c.ecarts > 1 ? "s" : ""} à régler`);
    return m.join(" · ");
  };
  const ouvrir = () => { frappe.route_options = { onglet: "bl" }; frappe.set_route("stock-entrepots"); };
  const bip = () => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)(), o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square"; o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.25);
    } catch (e) { /* audio non permis avant un geste de l'utilisateur */ }
  };
  const notifier = (txt) => {
    try {
      if (!("Notification" in window)) return;
      if (Notification.permission === "default") Notification.requestPermission();
      if (Notification.permission === "granted") {
        const n = new Notification("📦 Magasin", { body: txt, tag: "sm-bl-magasin", renotify: true });
        n.onclick = () => { window.focus(); ouvrir(); n.close(); };
      }
    } catch (e) { /* ignoré */ }
  };
  const peindre = (c) => {
    const n = c ? (c.a_approuver || 0) + (c.a_verifier || 0) + (c.ecarts || 0) : 0;
    if (!n) { if ($b) { $b.remove(); $b = null; $("body").removeClass("sm-bandeau-actif"); } dernier = 0; return; }
    const txt = texte(c);
    if (!$b) {
      if (!$("#sm-style").length) $("head").append($(style).attr("id", "sm-style"));
      $b = $(`<div class="sm-bandeau" title="Ouvrir l’onglet BL de la page Stock"><span>📦</span><span class="sm-txt"></span><span class="sm-bt">Ouvrir</span></div>`).appendTo("body");
      $("body").addClass("sm-bandeau-actif");
      $b.on("click", ouvrir);
    }
    $b.find(".sm-txt").text(txt);
    if (dernier >= 0 && n > dernier) { bip(); notifier(txt); }
    else if (dernier < 0 && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
    dernier = n;
  };
  const charger = () => {
    if (!actif) return;
    frappe.xcall(API).then((c) => { if (c === null || c === undefined) { actif = false; return; } peindre(c); }).catch(() => {});
  };
  $(document).on("app_ready", () => {
    if (!frappe.session || frappe.session.user === "Guest") return;
    charger();
    setInterval(charger, 120000);
    frappe.realtime.on("sm_bl_magasin", charger);
  });
})();
