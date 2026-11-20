/**
 * Caisse — « comptez vos caisses non comptées » : le rappel qui S'IMPOSE à l'écran (20/11/2026).
 *
 * Demande : « je vois des utilisateurs qui n'ont pas compté leur caisse, je veux un message intrusif dans leur
 * session ». Pour chacun, sur SA caisse, tant qu'un jour PASSÉ où elle a bougé reste sans comptage (la même
 * règle que le ❌ « Pas comptée » de l'état des caisses ; aujourd'hui n'est pas un retard) :
 *   - une FENÊTRE devant tout, qui NE SE FERME PAS (consigne du 20/11/2026 : « le message intrusif ne doit pas
 *     s'arrêter tant qu'il y a une caisse à compter ») : ni « Plus tard », ni croix, ni Échap, ni clic à côté.
 *     Sa seule sortie est « Compter », qui ouvre le comptage du jour choisi ; elle revient à chaque changement
 *     d'écran tant qu'il reste un jour, et disparaît d'elle-même quand tout est compté ;
 *   - une PASTILLE rouge en bas à droite, sur la caisse journalière (le seul écran où la fenêtre ne s'ouvre pas :
 *     c'est là qu'on compte).
 * Elle n'écrase jamais une autre fenêtre déjà ouverte (une saisie en cours) : elle attend qu'elle se ferme.
 * Pour qui n'a rien à compter
 * (pas de caisse, partenaire, sous-traitant), l'API répond null : rien n'est affiché, on ne redemande plus.
 */
(() => {
  const API = "customization_app.caisse_collecte.mes_caisses_non_comptees";
  const BIP_ENTRE_DEUX = 10 * 60 * 1000;      // le bip, lui, pas plus d'une fois toutes les dix minutes
  const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
  let actif = true, $pastille = null, fenetre = null, dernier_appel = 0, dernier_bip = 0, connu = null;
  const sur_la_caisse = () => (frappe.get_route() || [])[0] === "caisse-journaliere";
  const compter = (r, date) => {
    frappe.route_options = { compter_caisse: r.caisse, compter_date: date };
    // Déjà sur la caisse journalière (c'est là que la pastille se clique) : aller sur la même page ne déclenche
    // rien — on demande directement à la page de se placer sur ce jour et d'ouvrir le comptage (20/11/2026 : « le
    // bouton rouge ne m'envoie pas vers la caisse »).
    const page = frappe.pages["caisse-journaliere"];
    if (sur_la_caisse() && page && page.rcj) { page.rcj._depuis_rappel(); return; }
    frappe.set_route("caisse-journaliere");
  };
  const jour_en_clair = (d) => {
    const noms = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"];
    return `${noms[frappe.datetime.str_to_obj(d).getDay()]} ${frappe.datetime.str_to_user(d)}`;
  };
  const bip = () => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)(), o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square"; o.frequency.value = 520; g.gain.value = 0.07; o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.3);
    } catch (e) { /* audio non permis avant un geste de l'utilisateur */ }
  };

  const pastille = (r) => {
    if (!r || !r.n) { if ($pastille) { $pastille.remove(); $pastille = null; } return; }
    if (!$pastille) {
      $pastille = $(`<div id="caisse-pastille" title="Jours où votre caisse n’a pas été comptée" style="position:fixed;right:18px;z-index:1030;
        background:#dc2626;color:#fff;border-radius:999px;padding:9px 16px;font-weight:700;font-size:13.5px;cursor:pointer;
        box-shadow:0 4px 14px rgba(0,0,0,.28);display:flex;align-items:center;gap:8px"></div>`).appendTo("body");
    }
    // Au-dessus de la pastille de l'atelier quand les deux sont là.
    $pastille.css("bottom", $("#ro-pastille").length ? "64px" : "18px");
    $pastille.off("click").on("click", () => compter(r, r.jours[0].date));
    $pastille.html(`🧮 Caisse : <span>${r.n} jour${r.n > 1 ? "s" : ""} à compter</span><span style="background:#fff;color:#b91c1c;border-radius:999px;padding:1px 10px;font-size:12.5px">Compter</span>`);
  };

  const montrer = (r) => {
    if (fenetre) { fenetre.hide(); fenetre = null; }
    const premier = r.jours[0].date;
    const d = new frappe.ui.Dialog({
      title: `🧮 Votre caisse n’est pas comptée — ${r.n} jour${r.n > 1 ? "s" : ""}`, static: true,
      fields: [{ fieldtype: "HTML", fieldname: "zone" }],
      primary_action_label: `Compter ma caisse du ${frappe.datetime.str_to_user(premier)}`,
      primary_action: () => { fermer(d); compter(r, premier); },
    });
    // Elle ne se ferme pas : pas de croix, et tout ce qui la cacherait (Échap, autre script) la fait revenir —
    // sauf quand c'est nous qui la fermons (bouton « Compter », ou plus rien à compter).
    d.$wrapper.find(".modal-header .btn-modal-close, .modal-header .close, .modal-header .btn-modal-minimize").remove();
    d.$wrapper.on("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); } });
    d.fields_dict.zone.$wrapper.html(`
      <div style="background:#fef2f2;border:1px solid #fca5a5;color:#991b1b;border-radius:10px;padding:10px 12px;margin-bottom:10px;font-size:13.5px">
        <b>Comptez votre caisse</b> pour chaque jour ci-dessous, en commençant par le plus ancien :
        le solde d’un jour part du comptage de la veille.</div>
      <div style="max-height:46vh;overflow:auto">${r.jours.map((j) => `
        <div style="display:flex;gap:10px;align-items:center;padding:8px 2px;border-bottom:1px solid var(--border-color,#eef1f5)">
          <span style="flex:1"><b>${esc(jour_en_clair(j.date))}</b>
            <span style="display:block;font-size:12px;color:var(--text-muted)">${esc(j.resume)}</span></span>
          <button class="btn btn-xs btn-default" data-jour="${esc(j.date)}">Compter</button>
        </div>`).join("")}</div>
      <div style="font-size:12px;color:var(--text-muted);margin-top:8px">Caisse : ${esc(r.caisse)}</div>`);
    d.fields_dict.zone.$wrapper.find("[data-jour]").on("click", (e) => { fermer(d); compter(r, $(e.currentTarget).attr("data-jour")); });
    d.$wrapper.on("hidden.bs.modal", () => {
      const voulu = d.__fermee_par_nous;
      if (fenetre === d) fenetre = null;
      if (!voulu) setTimeout(() => traiter(connu), 200);        // fermée autrement : elle revient
    });
    fenetre = d;
    d.show();
    if (Date.now() - dernier_bip > BIP_ENTRE_DEUX) { bip(); dernier_bip = Date.now(); }
  };
  const fermer = (d) => { d.__fermee_par_nous = true; d.hide(); };

  const traiter = (r) => {
    connu = r;
    pastille(r);
    if (!r || !r.n) { if (fenetre) fermer(fenetre); return; }
    if (sur_la_caisse()) { if (fenetre) fermer(fenetre); return; }          // c'est là qu'on compte
    if (fenetre) return;
    // Jamais par-dessus une autre fenêtre (une saisie en cours) : on réessaie dès qu'elle se ferme.
    if ($(".modal.show").length) { $(document).one("hidden.bs.modal", () => setTimeout(() => traiter(connu), 300)); return; }
    montrer(r);                                                             // tant qu'il reste un jour à compter
  };

  const charger = () => {
    if (!actif) return;
    dernier_appel = Date.now();
    frappe.xcall(API).then((r) => { if (r === null || r === undefined) { actif = false; pastille(null); return; } traiter(r); }).catch(() => {});
  };
  window.caisse_rappel_actualiser = charger;

  $(document).on("app_ready", () => {
    if (!frappe.session || frappe.session.user === "Guest") return;
    setTimeout(charger, 2500);
    // Tant qu'il reste un jour : toutes les 2 minutes (la fenêtre disparaît dès que le dernier est compté) ;
    // sinon toutes les 10 minutes.
    setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if ((connu && connu.n) || Date.now() - dernier_appel > 590000) charger();
    }, 120000);
    // À chaque changement d'écran : s'il reste un jour, on revérifie tout de suite et la fenêtre revient (ou
    // disparaît si le comptage vient d'être fait) ; sinon au plus une fois par minute.
    frappe.router.on("change", () => { if ((connu && connu.n) || Date.now() - dernier_appel > 60000) charger(); });
  });
})();
