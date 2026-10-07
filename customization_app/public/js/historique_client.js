// Historique client — panneau commun aux écrans d'appel (demande du 07/10/2026).
//
// Utilisé par « Liste Appelle Entretien », « Liste Appels Rattrapage » et « Commandes à traiter » :
//   - window.historique_bouton_html(client)      → bouton 📜 ; un seul gestionnaire de clic global ouvre le panneau ;
//   - window.historique_pieces_html(client)      → emplacement de la ligne « 🔧 À proposer » d'une carte ;
//   - window.historique_remplir_pieces($racine)  → remplit en UN appel serveur tous les emplacements d'un écran.
// Le serveur (customization_app.historique_client) ne fait que lire, sauf la note d'appel.
(function () {
  if (window.ouvrir_historique_client) return;
  const M = "customization_app.historique_client.";
  const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
  const jour = (d) => (d ? frappe.datetime.str_to_user(String(d).slice(0, 10)) : "");
  const heure = (d) => (d && String(d).length > 10 ? " " + String(d).slice(11, 16) : "");
  const lien = (doctype, nom) => nom
    ? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(nom)}" target="_blank">${esc(nom)}</a>` : "";
  // Le partenaire (rôle « Partenaire » seul) ne voit pas l'historique de nos clients.
  const ROLES_INTERNES = ["Sales User", "Sales Manager", "Maintenance Manager", "System Manager"];
  const partenaire_seul = () => frappe.user.has_role("Partenaire") && !ROLES_INTERNES.some((r) => frappe.user.has_role(r));
  const STATUT = { retard: ["hc-r", "En retard"], bientot: ["hc-b", "Bientôt"], ok: ["hc-ok", "À jour"],
                   ancien: ["hc-a", "Ancien"] };

  function style() {
    if (document.getElementById("hc-style")) return;
    const css = `
      .hc-btn{font-size:11px;padding:1px 7px;border-radius:10px;border:1px solid var(--border-color);
        background:var(--control-bg);color:var(--text-color);cursor:pointer;white-space:nowrap}
      .hc-btn:hover{background:var(--fg-hover-color,rgba(0,0,0,.05))}
      .hc-pieces{font-size:11.5px;margin-top:3px;line-height:1.45}
      .hc-pieces:empty{display:none}
      .hc-pieces .hc-tag{display:inline-block;padding:0 6px;border-radius:9px;margin:1px 3px 1px 0}
      .hc-r{background:rgba(192,57,43,.13);color:#a93226}.hc-b{background:rgba(230,126,34,.15);color:#a35a00}
      .hc-ok{background:rgba(40,167,69,.13);color:#1e7e34}.hc-a{background:rgba(154,160,166,.18);color:#5f6368}
      .hc-wrap{font-size:12.5px}
      .hc-wrap h5{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);
        margin:16px 0 6px;font-weight:600}
      .hc-wrap table{width:100%;border-collapse:collapse}
      .hc-wrap td,.hc-wrap th{padding:4px 7px;border-bottom:1px solid var(--border-color);vertical-align:top;text-align:left}
      .hc-wrap th{font-size:10.5px;color:var(--text-muted);text-transform:uppercase;font-weight:600;white-space:nowrap}
      .hc-wrap .m{color:var(--text-muted)}.hc-wrap .nw{white-space:nowrap}
      .hc-wrap .hc-vide{color:var(--text-muted);font-style:italic;padding:4px 0}
      .hc-wrap .hc-tete{display:flex;flex-wrap:wrap;gap:8px 18px;padding:8px 10px;border:1px solid var(--border-color);
        border-radius:8px;background:var(--control-bg)}
      .hc-wrap .hc-note{display:flex;gap:6px;margin-top:6px}
      .hc-wrap .hc-note textarea{flex:1;min-height:38px;font-size:12.5px;border:1px solid var(--border-color);
        border-radius:6px;padding:4px 7px;background:var(--control-bg);color:var(--text-color)}
      .hc-wrap .hc-pill{display:inline-block;padding:0 6px;border-radius:9px;font-size:11px;white-space:nowrap}
      .hc-wrap .hc-scroll{max-height:320px;overflow:auto;border:1px solid var(--border-color);border-radius:6px}
      .hc-wrap details summary{cursor:pointer;color:var(--text-muted);font-size:11.5px;margin:4px 0}`;
    $("<style id='hc-style'>").text(css).appendTo("head");
  }

  // ── Bouton et ligne « pièces » sur les cartes ───────────────────────────────
  window.historique_bouton_html = function (client) {
    style();
    return client && !partenaire_seul() ? `<button type="button" class="hc-btn" data-hc-client="${esc(client)}"
      title="Historique du client : appels, commentaires, interventions, échéancier, pièces à proposer">📜 Historique</button>` : "";
  };
  window.historique_pieces_html = function (client) {
    style();
    return client && !partenaire_seul() ? `<div class="hc-pieces" data-hc-pieces="${esc(client)}"></div>` : "";
  };
  window.historique_remplir_pieces = async function ($racine) {
    const $places = $($racine || document).find("[data-hc-pieces]").filter((_, el) => !el.dataset.hcFait);
    const clients = [...new Set($places.map((_, el) => el.dataset.hcPieces).get())];
    if (!clients.length) return;
    $places.each((_, el) => { el.dataset.hcFait = "1"; });
    let res = {};
    try {
      res = (await frappe.call({ method: M + "get_pieces_lot", args: { clients: JSON.stringify(clients) } })).message || {};
    } catch (e) { return; }
    $places.each((_, el) => {
      const r = res[el.dataset.hcPieces];
      if (!r || r.revendeur || !(r.pieces || []).length) return;
      el.innerHTML = "🔧 <b>À proposer :</b> " + etiquettes(r.pieces);
    });
  };
  function etiquettes(pieces) {
    return pieces.map((p) => {
      const [cls] = STATUT[p.statut] || STATUT.ok;
      const quand = p.statut === "retard"
        ? (p.retard_jours >= 30 ? `retard ${Math.floor(p.retard_jours / 30)} mois` : "à changer")
        : `le ${jour(p.echeance)}`;
      return `<span class="hc-tag ${cls}" title="${esc(p.message || "")}">${esc(p.libelle)} · ${esc(quand)}</span>`;
    }).join("");
  }

  // ── Fiche « Tache de travail » : pour le technicien chez le client (demande du 07/10/2026) ─────
  // Bouton dans la barre + bandeau sous l'en-tête. Le conseil (« test de dureté »…) est écrit en clair :
  // sur téléphone, une infobulle ne s'affiche pas.
  function bandeau_tache(frm) {
    $(frm.layout && frm.layout.wrapper).find(".hc-tache").remove();
    const client = frm.doc.custom_client;
    if (!client || frm.is_new() || partenaire_seul()) return;
    style();
    frm.add_custom_button("📜 Historique client", () => window.ouvrir_historique_client(client));
    const nom = frm.doc.name;
    frappe.call({ method: M + "get_pieces_lot", args: { clients: JSON.stringify([client]) } }).then((r) => {
      // Le Desk est une SPA : si on a changé de fiche pendant l'appel, on ne peint rien.
      if (frm.doc.name !== nom || frm.doc.custom_client !== client) return;
      const x = (r.message || {})[client];
      if (!x || x.revendeur || !(x.pieces || []).length) return;
      const conseils = [...new Set(x.pieces.map((p) => p.message).filter(Boolean))];
      const $b = $(`<div class="hc-tache hc-pieces" style="margin:8px 0 10px;padding:8px 12px;border:1px solid
          rgba(230,126,34,.45);border-radius:8px;background:rgba(230,126,34,.07);font-size:12.5px">
        🔧 <b>À proposer au client :</b> ${etiquettes(x.pieces)}
        ${conseils.map((c) => `<div class="m" style="margin-top:3px;font-size:11.5px">💡 ${esc(c)}</div>`).join("")}
      </div>`);
      const $w = $(frm.layout.wrapper);
      const $tabs = $w.find(".form-tabs-list").first();
      if ($tabs.length) $b.insertBefore($tabs); else $w.prepend($b);
    });
  }
  frappe.ui.form.on("Tache de travail", {
    refresh: bandeau_tache,
    custom_client: bandeau_tache,
  });

  $(document).on("click", "[data-hc-client]", (e) => {
    e.preventDefault();
    e.stopPropagation();
    window.ouvrir_historique_client(e.currentTarget.dataset.hcClient);
  });

  // ── Le panneau ──────────────────────────────────────────────────────────────
  window.ouvrir_historique_client = async function (client) {
    style();
    const d = new frappe.ui.Dialog({ title: `📜 Historique — ${client}`, size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "corps" }] });
    const $c = d.get_field("corps").$wrapper;
    $c.html('<div class="hc-wrap"><div class="hc-vide">Chargement…</div></div>');
    d.show();
    let h;
    try {
      h = (await frappe.call({ method: M + "get_historique", args: { client } })).message || {};
    } catch (e) {
      $c.html(`<div class="hc-wrap"><div class="hc-vide">Chargement impossible.</div></div>`);
      return;
    }
    $c.html(`<div class="hc-wrap">${tete(h)}${pieces(h)}${a_venir(h)}${appels(h)}${interventions(h)}${echeancier(h)}${achats(h)}</div>`);
    $c.find(".hc-note button").on("click", async () => {
      const $t = $c.find(".hc-note textarea");
      const note = ($t.val() || "").trim();
      if (note.length < 2) return;
      try {
        const r = (await frappe.call({ method: M + "ajouter_note", type: "POST", args: { client, note }, freeze: true })).message;
        $t.val("");
        $c.find("[data-hc-appels] tbody").prepend(`<tr><td class="nw">${jour(r.date)}${heure(r.date)}</td>
          <td>${esc(r.par)}</td><td>Note d’appel</td><td>${esc(note)}</td><td></td></tr>`);
        frappe.show_alert({ message: "Note enregistrée sur la fiche client", indicator: "green" });
      } catch (e) { /* message serveur déjà affiché */ }
    });
  };

  function tete(h) {
    const c = h.client || {};
    const bouts = [`<span><b>${lien("Customer", c.name)}</b> ${c.nom && c.nom !== c.name ? esc(c.nom) : ""}</span>`];
    if (c.telephone) bouts.push(`<span>📞 ${esc(c.telephone)}</span>`);
    if (c.groupe) bouts.push(`<span class="m">${esc(c.groupe)}</span>`);
    if (c.statut_relance) bouts.push(`<span class="hc-pill hc-a">${esc(c.statut_relance)}</span>`);
    if (c.partenaire) bouts.push(`<span class="hc-pill hc-b">Géré par le partenaire</span>`);
    return `<div class="hc-tete">${bouts.join("")}</div>`;
  }

  function pieces(h) {
    const p = h.pieces || {};
    if (p.revendeur) return `<h5>🔧 Pièces à proposer</h5><div class="hc-vide">Revendeur (${esc(p.groupe || "")}) : pas de calcul,
      il achète pour son stock.</div>`;
    const lignes = p.pieces || [];
    if (!lignes.length) return `<h5>🔧 Pièces à proposer</h5><div class="hc-vide">Aucune machine ni pièce connue chez ce client.</div>`;
    return `<h5>🔧 Pièces à proposer</h5><table><thead><tr><th>Pièce</th><th>État</th><th>Dernier changement</th>
      <th>Échéance</th><th>Rythme</th><th></th></tr></thead><tbody>${lignes.map((x) => {
        const [cls, lib] = STATUT[x.statut] || STATUT.ok;
        const etat = x.statut === "retard" && x.retard_jours >= 30 ? `${lib} · ${Math.floor(x.retard_jours / 30)} mois` : lib;
        return `<tr><td><b>${esc(x.libelle)}</b></td><td><span class="hc-pill ${cls}">${esc(etat)}</span></td>
          <td class="nw">${jour(x.depuis)} <span class="m">${x.origine === "pose" ? "posée avec" : "achat"}
            ${esc(x.article_depart || "")}</span></td>
          <td class="nw">${jour(x.echeance)}</td>
          <td class="nw m">${x.intervalle_mois} mois${x.appris ? " (son rythme)" : ""}</td>
          <td class="m" style="font-size:11.5px">${esc(x.message || "")}</td></tr>`;
      }).join("")}</tbody></table>`;
  }

  function a_venir(h) {
    const l = h.a_venir || [];
    return `<h5>📅 Interventions à venir</h5>` + (l.length
      ? `<table><tbody>${l.map((t) => `<tr><td class="nw">${jour(t.date)}${heure(t.date)}</td><td>${esc(t.type)}</td>
          <td>${esc(t.employe)}</td><td>${lien("Tache de travail", t.name)}</td><td>${lien("Sales Order", t.commande)}</td>
          <td class="m">${esc(t.sujet)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="hc-vide">Aucune intervention planifiée.</div>`);
  }

  function appels(h) {
    const l = h.appels || [];
    const corps = l.map((a) => {
      // Sans doctype : une ligne de liste d'appels. Les commentaires du client lui-même n'ont pas de source.
      const src = a.source ? lien(a.doctype || "Liste Appelle Entretien", a.source) : "";
      return `<tr><td class="nw">${jour(a.date)}${heure(a.date)}</td><td class="nw">${esc(a.par)}</td>
        <td>${esc(a.titre)}${a.detail ? ` <span class="m">· ${esc(a.detail)}</span>` : ""}</td>
        <td>${esc(a.texte)}</td><td class="m">${src}</td></tr>`;
    }).join("");
    return `<h5>📞 Appels et commentaires</h5>
      <div class="hc-note"><textarea placeholder="Note d’appel (enregistrée sur la fiche client)…"></textarea>
        <button class="btn btn-sm btn-primary">Enregistrer</button></div>
      <div class="hc-scroll" style="margin-top:6px"><table data-hc-appels><thead><tr><th>Quand</th><th>Qui</th>
        <th>Quoi</th><th>Réponse / commentaire</th><th>Source</th></tr></thead><tbody>${corps}</tbody></table>
        ${l.length ? "" : '<div class="hc-vide" style="padding:6px">Aucun appel ni commentaire.</div>'}</div>`;
  }

  function interventions(h) {
    const l = h.interventions || [];
    if (!l.length) return `<h5>🛠 Interventions passées</h5><div class="hc-vide">Aucune.</div>`;
    const pill = (s) => `<span class="hc-pill ${s === "Completed" ? "hc-ok" : s === "Cancelled" ? "hc-a" : "hc-r"}">${
      esc(s === "Completed" ? "Faite" : s === "Cancelled" ? "Annulée" : s)}</span>`;
    return `<h5>🛠 Interventions passées</h5><div class="hc-scroll"><table><thead><tr><th>Date</th><th>Type</th>
      <th>Statut</th><th>Technicien</th><th>Rapport / raison</th><th>Tâche</th><th>Commande</th></tr></thead>
      <tbody>${l.map((t) => `<tr><td class="nw">${jour(t.date)}</td><td>${esc(t.type)}</td><td>${pill(t.status)}</td>
        <td class="nw">${esc(t.employe)}</td><td>${esc(t.rapport || t.raison_annulation || "")}</td>
        <td class="nw">${lien("Tache de travail", t.name)}</td><td class="nw">${lien("Sales Order", t.commande)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function echeancier(h) {
    const e = h.echeancier || {};
    const ms = e.echeanciers || [];
    if (!ms.length) return `<h5>🗓 Échéancier d’entretien</h5><div class="hc-vide">Aucun échéancier.</div>`;
    const sms = (d, s) => d ? `${jour(d)} <span class="hc-pill ${s === "Success" ? "hc-ok" : "hc-r"}">${s === "Success" ? "✓" : "✗"}</span>` : "";
    const etat = (v) => `<span class="hc-pill ${v.etat === "réalisée" ? "hc-ok" : v.etat === "à venir" ? "hc-a" : "hc-r"}">${esc(v.etat)}</span>`;
    const blocs = ms.map((m) => {
      const machines = (m.machines || []).map((x) => `${esc(x.item_code)} <span class="m">(${jour(x.depuis)})</span>`).join(", ");
      const vis = m.visites || [];
      const recentes = vis.slice(-6);
      const lignes = (arr) => arr.map((v) => `<tr><td class="nw">${jour(v.prevue)}</td><td class="nw">${esc(v.item_code)}</td>
        <td>${etat(v)}</td><td class="nw">${sms(v.sms1, v.sms1_statut)}</td><td class="nw">${sms(v.sms2, v.sms2_statut)}</td>
        <td class="nw">${jour(v.premier_appel)}</td><td class="nw">${jour(v.appele)}</td>
        <td class="nw">${v.faite_le ? jour(v.faite_le) + " " : ""}${lien("Sales Order", v.commande)}</td></tr>`).join("");
      const tete_t = `<thead><tr><th>Prévue</th><th>Machine</th><th>État</th><th>SMS 1</th><th>SMS 2</th><th>1er appel</th>
        <th>Appelé</th><th>Réalisée</th></tr></thead>`;
      const plus = vis.length > recentes.length
        ? `<details><summary>${vis.length - recentes.length} visite(s) plus ancienne(s)</summary><table>${tete_t}<tbody>${
            lignes(vis.slice(0, vis.length - recentes.length))}</tbody></table></details>` : "";
      return `<div style="margin:6px 0 10px"><div>${lien("Maintenance Schedule", m.name)} <span class="m">· ${machines}</span></div>
        ${plus}<table>${tete_t}<tbody>${lignes(recentes)}</tbody></table></div>`;
    }).join("");
    const journal = (e.journal || []).length
      ? `<details><summary>Journal de l’échéancier (${e.journal.length})</summary><table><tbody>${e.journal.map((j) =>
          `<tr><td class="nw">${jour(j.date)}</td><td>${esc(j.texte)}</td></tr>`).join("")}</tbody></table></details>` : "";
    return `<h5>🗓 Échéancier d’entretien</h5>${blocs}${journal}`;
  }

  function achats(h) {
    const l = h.achats || [];
    if (!l.length) return `<h5>🧾 Achats</h5><div class="hc-vide">Aucune commande.</div>`;
    return `<h5>🧾 Achats</h5><div class="hc-scroll"><table><thead><tr><th>Date</th><th>Commande</th><th>Statut</th>
      <th>Articles</th><th style="text-align:right">TTC</th></tr></thead><tbody>${l.map((c) => `<tr>
        <td class="nw">${jour(c.date)}</td><td class="nw">${lien("Sales Order", c.name)}</td><td class="m nw">${esc(c.status)}</td>
        <td>${c.articles.map((a) => a.machine ? `<b>🔧 ${esc(a.code)}</b>` : a.main_doeuvre
          ? `<span class="m">${esc(a.code)}</span>` : esc(a.code) + (a.qte > 1 ? ` ×${a.qte}` : "")).join(", ")}</td>
        <td class="nw" style="text-align:right">${format_currency(c.total)}</td></tr>`).join("")}</tbody></table></div>`;
  }
})();
