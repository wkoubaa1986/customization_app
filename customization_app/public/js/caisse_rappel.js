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
    if (window.frameElement) return;   // fiche ouverte en fenêtre (ouvrir_document.js) : l'alerte est déjà dessous
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

/**
 * Caisse — accords de dépense à donner (24/11/2026). Pour les responsables de collecte (Config Caisse : titulaire et
 * co-titulaires) : tant qu'une demande « Accord Depense Caisse » est En attente, une carte bleue en bas à GAUCHE (les
 * pastilles de caisse et d'atelier sont à droite ; au-dessus de la barre de discussion Raven) ; un clic ouvre la liste, « Accorder » ou « Refuser » (motif). Un
 * employé attend souvent devant le fournisseur : notification du navigateur à chaque nouvelle demande. Rafraîchie par
 * l'événement temps réel `caisse_accord` et toutes les 2 minutes. Pour les autres, l'API renvoie [] : rien.
 */
(() => {
  const API = "customization_app.caisse_accord";
  let $carte = null, dernier = -1, liste = [], rendre_ouvert = null;
  const fmt = (v) => (Number(v) || 0).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
  const esc = (s) => frappe.utils.escape_html(s || "");
  const notifier = (txt) => {
    try {
      if (!("Notification" in window)) return;
      if (Notification.permission === "default") Notification.requestPermission();
      if (Notification.permission === "granted") new Notification("💰 Accord de dépense", { body: txt, tag: "caisse-accord", renotify: true });
    } catch (e) { /* ignoré */ }
  };
  const ouvrir = () => {
    const d = new frappe.ui.Dialog({ title: __("Dépenses en espèces à accorder"), size: "large",
                                     fields: [{ fieldtype: "HTML", fieldname: "corps" }] });
    const rendre = () => {
      if (!liste.length) { d.hide(); return; }
      d.fields_dict.corps.$wrapper.html(liste.map((a) => `
        <div style="border:1px solid #dbe3ee;border-radius:10px;padding:10px 12px;margin-bottom:10px" data-name="${esc(a.name)}">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
            <div><div style="font-size:20px;font-weight:800">${fmt(a.montant)}</div>
              <div style="font-size:13.5px">${esc(a.objet)}${a.fournisseur ? " — " + esc(a.fournisseur) : ""}</div>
              <div style="font-size:12px;color:#6b7280">${__("demandé par")} <b>${esc(a.demandeur_nom)}</b> · ${__("caisse de")} ${esc(a.caisse)} · ${esc(String(a.demande_le || "").slice(0, 16))} · ${esc(a.name)}</div></div>
            <div style="display:flex;gap:6px;align-items:center">
              <button class="btn btn-success acd-oui">✅ ${__("Accorder")}</button>
              <button class="btn btn-default acd-non">❌ ${__("Refuser")}</button></div>
          </div>
        </div>`).join(""));
      const decider = (name, accorder, commentaire) => frappe.call({
        method: API + ".decider", args: { name, accorder: accorder ? 1 : 0, commentaire: commentaire || "" }, freeze: true,
        callback: () => {
          frappe.show_alert({ message: accorder ? __("Dépense accordée : l'employé est prévenu.") : __("Dépense refusée : l'employé est prévenu."),
                              indicator: accorder ? "green" : "orange" });
          liste = liste.filter((x) => x.name !== name);
          peindre(); rendre();
        },
      });
      d.fields_dict.corps.$wrapper.find(".acd-oui").on("click", function () { decider($(this).closest("[data-name]").attr("data-name"), true); });
      d.fields_dict.corps.$wrapper.find(".acd-non").on("click", function () {
        const name = $(this).closest("[data-name]").attr("data-name");
        // Le serveur exige 10 caractères : on le dit ici, sans faire tout réécrire (25/11/2026).
        const demander = (texte) => frappe.prompt([{ fieldtype: "Small Text", fieldname: "commentaire", reqd: 1, default: texte || "",
                                                     label: __("Pourquoi ? (l'employé le lira, 10 caractères minimum)") }],
          (v) => {
            const motif = (v.commentaire || "").replace(/\s+/g, " ").trim();
            if (motif.length < 10) {
              frappe.show_alert({ message: __("Motif trop court : 10 caractères minimum."), indicator: "orange" });
              demander(v.commentaire);
              return;
            }
            decider(name, false, motif);
          }, __("Refuser la dépense"), __("Refuser"));
        demander("");
      });
    };
    rendre();
    // La liste ouverte suit les décisions des AUTRES responsables et les nouvelles demandes (25/11/2026).
    rendre_ouvert = rendre;
    d.onhide = () => { rendre_ouvert = null; };
    d.show();
  };
  const peindre = () => {
    const n = liste.length;
    if (!n) { if ($carte) { $carte.remove(); $carte = null; } dernier = 0; return; }
    const a = liste[0];
    const txt = n > 1 ? __("{0} dépenses en espèces attendent votre accord", [n])
      : __("{0} demande {1} : {2}", [a.demandeur_nom, fmt(a.montant), a.objet]);
    if (!$carte) {
      $carte = $(`<div id="acd-carte" title="${__("Voir et décider")}" style="position:fixed;left:18px;bottom:64px;z-index:1030;max-width:min(440px,calc(100vw - 110px));
          background:#1d4ed8;color:#fff;border-radius:12px;padding:10px 14px;box-shadow:0 4px 14px rgba(0,0,0,.25);cursor:pointer;
          display:flex;gap:10px;align-items:center;font-weight:700;font-size:13.5px">
          <span style="font-size:20px">💰</span><span class="acd-txt" style="overflow:hidden;text-overflow:ellipsis"></span>
          <span style="background:#fff;color:#1d4ed8;border-radius:999px;padding:3px 10px;font-size:12.5px;white-space:nowrap">${__("Décider")}</span></div>`).appendTo("body");
      $carte.on("click", ouvrir);
    }
    $carte.find(".acd-txt").text(txt);
    if (dernier >= 0 && n > dernier) notifier(txt);
    dernier = n;
  };
  let actif = true;
  const charger = () => {
    if (!actif) return;
    frappe.xcall(API + ".a_decider").then((r) => {
      if (r === null || r === undefined) { actif = false; return; }      // pas responsable : on n'interroge plus
      liste = r; peindre();
      if (rendre_ouvert) rendre_ouvert();
    }).catch(() => {});
  };
  $(document).on("app_ready", () => {
    if (window.frameElement) return;   // fiche ouverte en fenêtre (ouvrir_document.js) : l'alerte est déjà dessous
    if (!frappe.session || frappe.session.user === "Guest") return;
    charger();
    setInterval(() => { if (document.visibilityState === "visible") charger(); }, 120000);
    frappe.realtime.on("caisse_accord", charger);
  });
})();
