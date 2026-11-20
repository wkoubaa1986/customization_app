/**
 * Réparation osmoseurs — le rappel qui S'IMPOSE à l'écran (20/11/2026).
 *
 * Pour la personne du « Suivi des restitutions » (et celle de la relance), sur toutes les pages du Desk, tant que
 * des machines de l'atelier attendent une réponse :
 *   - une FENÊTRE qui se met devant tout (on ne la ferme pas en cliquant à côté) : à la première ouverture du jour,
 *     puis toutes les 3 heures, et tout de suite quand une machine s'ajoute ;
 *   - une PASTILLE orange qui reste en bas à droite entre deux fenêtres (clic → les machines à vérifier).
 * La fenêtre ne s'ouvre pas pendant qu'on est déjà sur l'écran Réparation osmoseurs : on y travaille.
 * Pour tous les autres, l'API répond null : rien n'est affiché, et on ne redemande plus.
 */
(() => {
  const API = "customization_app.reparation_osmoseur.mon_rappel";
  const CLE = "ro_rappel_vu";
  const ENTRE_DEUX = 3 * 3600 * 1000;       // la fenêtre revient toutes les 3 heures tant qu'il reste des machines
  const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
  let actif = true, $pastille = null, fenetre = null;

  const vu = () => { try { return JSON.parse(localStorage.getItem(CLE) || "{}") || {}; } catch (e) { return {}; } };
  const noter = (n) => {
    try { localStorage.setItem(CLE, JSON.stringify({ user: frappe.session.user, jour: frappe.datetime.get_today(), le: Date.now(), n })); } catch (e) { /* sans effet */ }
  };
  const ouvrir = () => { frappe.route_options = { verifier: 1 }; frappe.set_route("reparation-osmoseurs"); };
  const sur_l_ecran = () => (frappe.get_route() || [])[0] === "reparation-osmoseurs";
  const bip = () => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)(), o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square"; o.frequency.value = 660; g.gain.value = 0.07; o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.3);
    } catch (e) { /* audio non permis avant un geste de l'utilisateur */ }
  };

  const pastille = (r) => {
    if (!r || !r.n) { if ($pastille) { $pastille.remove(); $pastille = null; } return; }
    if (!$pastille) {
      $pastille = $(`<div id="ro-pastille" title="Machines de l’atelier qui attendent une réponse" style="position:fixed;right:18px;bottom:18px;z-index:1030;
        background:#ea580c;color:#fff;border-radius:999px;padding:9px 16px;font-weight:700;font-size:13.5px;cursor:pointer;
        box-shadow:0 4px 14px rgba(0,0,0,.28);display:flex;align-items:center;gap:8px"></div>`).appendTo("body");
      $pastille.on("click", ouvrir);
    }
    $pastille.html(`⏳ Atelier : <span>${r.n} machine${r.n > 1 ? "s" : ""}</span><span style="background:#fff;color:#c2410c;border-radius:999px;padding:1px 10px;font-size:12.5px">Ouvrir</span>`);
  };

  const montrer = (r) => {
    if (fenetre) { fenetre.hide(); fenetre = null; }
    const titre = r.role === "relance"
      ? `🔁 Atelier — ${r.n} machine${r.n > 1 ? "s" : ""} sans réponse${r.suivi ? " de " + r.suivi : ""}`
      : `⏳ Atelier — ${r.n} machine${r.n > 1 ? "s attendent" : " attend"} votre réponse`;
    const d = new frappe.ui.Dialog({
      title: titre, static: true, fields: [{ fieldtype: "HTML", fieldname: "zone" }],
      primary_action_label: "Ouvrir les machines à vérifier",
      primary_action: () => { d.hide(); ouvrir(); },
      secondary_action_label: "Plus tard",
      secondary_action: () => d.hide(),
    });
    d.fields_dict.zone.$wrapper.html(`
      <div style="background:#fff7ed;border:1px solid #fdba74;color:#9a3412;border-radius:10px;padding:10px 12px;margin-bottom:10px;font-size:13px">
        Chaque machine attend une réponse : <b>la rendre</b>, confirmer qu’elle est <b>toujours au magasin</b>,
        <b>affecter un technicien</b> ou la <b>sortir sans réparation</b>.</div>
      <div style="max-height:46vh;overflow:auto">${r.machines.map((m) => `
        <div style="display:flex;gap:10px;align-items:baseline;padding:7px 2px;border-bottom:1px solid var(--border-color,#eef1f5)">
          <b style="white-space:nowrap">${esc(m.name)}</b>
          <span style="flex:1">${esc(m.client)}<span style="display:block;font-size:12px;color:${m.jours >= 14 ? "#c2410c" : "var(--text-muted)"}">${esc(m.texte)}</span></span>
        </div>`).join("")}</div>`);
    d.$wrapper.on("hidden.bs.modal", () => { if (fenetre === d) fenetre = null; });
    fenetre = d;
    d.show();
    bip();
    noter(r.n);
  };

  const traiter = (r) => {
    pastille(r);
    if (!r || !r.n) { if (fenetre) { fenetre.hide(); fenetre = null; } return; }
    if (sur_l_ecran() || fenetre) return;
    const v = vu();
    const meme = v.user === frappe.session.user && v.jour === frappe.datetime.get_today();
    // Première fois du jour, une machine de plus qu'à la dernière fenêtre, ou trois heures passées : elle revient.
    if (!meme || r.n > (v.n || 0) || Date.now() - (v.le || 0) >= ENTRE_DEUX) montrer(r);
  };

  const charger = () => {
    if (!actif) return;
    frappe.xcall(API).then((r) => { if (r === null || r === undefined) { actif = false; pastille(null); return; } traiter(r); }).catch(() => {});
  };
  // Pour l'écran Réparation osmoseurs : la pastille suit dès qu'une réponse est donnée.
  window.ro_rappel_actualiser = charger;

  $(document).on("app_ready", () => {
    if (!frappe.session || frappe.session.user === "Guest") return;
    setTimeout(charger, 1500);                       // après le premier rendu du Desk
    setInterval(() => { if (document.visibilityState === "visible") charger(); }, 300000);
  });
})();
