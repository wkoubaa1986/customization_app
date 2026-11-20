/**
 * Écran « Réparation osmoseurs » — l'atelier, dossier par dossier.
 *
 * Toute la décision est CÔTÉ SERVEUR (customization_app.reparation_osmoseur) :
 * la réception se clôture là-bas (photo post-it ou code), l'affectation du
 * lendemain s'y calcule, et c'est la clôture des Taches de travail qui valide
 * le dossier. L'écran ne fait qu'afficher et déclencher.
 */

frappe.pages["reparation-osmoseurs"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Réparation osmoseurs",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("reparation_osmoseurs", {})
  );
  wrapper.ro = new ReparationOsmoseurs(wrapper);
};

// Le lien du rappel du matin (…/reparation-osmoseurs?verifier=1) ouvre directement les machines qui attendent.
frappe.pages["reparation-osmoseurs"].on_page_show = function (wrapper) {
  if (!wrapper.ro) return;
  if (frappe.utils.get_query_params().verifier && !wrapper.ro.verifier) {
    wrapper.ro.verifier = true; wrapper.ro.vue = null;
    wrapper.ro.charger();
  }
};

const RO_API = "customization_app.reparation_osmoseur";
// Trois groupes à l'écran (20/11/2026) ; les sept statuts restent en base et précisent la ligne.
const RO_GROUPES = { "À réparer": "b-reparer", "À rendre": "b-rendre", "Rendue": "b-rendue" };

function ro_date(d) {
  return d ? frappe.datetime.str_to_user(String(d).slice(0, 10)) : "";
}
function ro_datetime(d) {
  if (!d) return "";
  const s = String(d);
  return frappe.datetime.str_to_user(s.slice(0, 10)) + " " + s.slice(11, 16);
}

class ReparationOsmoseurs {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".ro-page");
    this.vue = null;                 // un des trois groupes, ou tout ce qui n'est pas rendu
    this.verifier = !!frappe.utils.get_query_params().verifier;   // seulement ce qui attend depuis trop longtemps
    this.data = null;
    this._bind();
    this.charger();
  }

  _bind() {
    const r = this.$root;
    r.find("#ro-nouvelle").on("click", () => this.nouvelle_reception());
    r.find("#ro-rafraichir").on("click", () => this.charger());
    r.find("#ro-config").on("click", () => frappe.set_route("Form", "Config Reparation Osmoseur"));
    r.find("#ro-guide").on("click", () => this.dialogue_guide());
    r.find("#ro-responsable, #ro-rendues").on("change", () => this.charger());
    let t = null;
    r.find("#ro-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.charger(), 300); });
  }

  async charger() {
    const r = this.$root;
    const res = await frappe.call({
      method: RO_API + ".get_data",
      args: {
        vue: this.vue,
        verifier: this.verifier ? 1 : 0,
        responsable: r.find("#ro-responsable").val() || null,
        recherche: r.find("#ro-recherche").val() || null,
        inclure_rendues: r.find("#ro-rendues").is(":checked") ? 1 : 0,
      },
      freeze: false,
    });
    this.data = res.message || { machines: [], groupes: [], statuts: [] };
    this.rendre();
  }

  rendre() {
    const d = this.data;
    const esc = frappe.utils.escape_html;
    const r = this.$root;

    // Trois tuiles = les trois groupes (clic = filtre), plus ce qui attend depuis trop longtemps.
    const precision = (g) => {
      const x = g.detail || {};
      if (g.nom === "À réparer") return [x["Réceptionnée"] ? `${x["Réceptionnée"]} sans technicien` : "", x["Planifiée"] ? `${x["Planifiée"]} planifiée(s)` : "",
                                          x["Réception en cours"] ? `${x["Réception en cours"]} réception(s) à finir` : ""].filter(Boolean).join(" · ");
      if (g.nom === "À rendre") return [(x["Réparée"] || 0) + (x["Prête au magasin"] || 0) ? `${(x["Réparée"] || 0) + (x["Prête au magasin"] || 0)} chez nous` : "",
                                         x["Livraison planifiée"] ? `${x["Livraison planifiée"]} en livraison` : ""].filter(Boolean).join(" · ");
      return "sorties, dossier fermé";
    };
    r.find("#ro-kpis").html(
      (d.groupes || []).map((g) => `
        <div class="ro-kpi ${this.vue === g.nom && !this.verifier ? "actif" : ""}" data-vue="${esc(g.nom)}">
          <div class="v">${g.n}</div><div class="l">${esc(g.nom)}</div>
          <div class="ro-sub">${esc(precision(g)) || "&nbsp;"}</div>
        </div>`).join("") + `
        <div class="ro-kpi ${d.a_verifier ? "alerte" : ""} ${this.verifier ? "actif" : ""}" data-verifier="1"
             title="Réparées et pas rendues, reçues sans technicien, livraisons pas clôturées — celles du rappel du matin">
          <div class="v">⏳ ${d.a_verifier || 0}</div><div class="l">À vérifier</div>
          <div class="ro-sub">en attente depuis ${d.rappel_jours} j ou plus${d.suivi ? " · rappelées à " + esc(d.suivi) : ""}</div>
        </div>`
    );
    r.find("#ro-kpis .ro-kpi").on("click", (e) => {
      const $k = $(e.currentTarget);
      if ($k.attr("data-verifier")) { this.verifier = !this.verifier; this.vue = null; }
      else { const v = $k.attr("data-vue"); this.vue = this.vue === v && !this.verifier ? null : v; this.verifier = false; }
      this.charger();
    });

    // Liste des responsables (filtre) — valeur conservée
    const sel = r.find("#ro-responsable");
    const courant = sel.val();
    sel.find("option:not(:first)").remove();
    (d.responsables || []).forEach((e) =>
      sel.append(`<option value="${esc(e.name)}">${esc(e.employee_name || e.name)}</option>`));
    sel.val(courant || "");

    r.find("#ro-config").toggle(!!d.peut_configurer);
    const alerte = r.find("#ro-alerte");
    if (!d.config_ok) {
      alerte.html("⚠️ Aucun responsable de réparation configuré : les machines reçues restent sans technicien " +
        "jusqu’à une affectation à la main. " +
        (d.peut_configurer ? `<a href="/app/config-reparation-osmoseur">Configurer les responsables</a>` : "Prévenez un administrateur.")).show();
    } else {
      alerte.hide();
    }

    const corps = r.find("#ro-corps");
    if (!d.machines.length) {
      corps.html(`<tr><td colspan="7" class="ro-vide">${this.verifier ? "Rien n’attend depuis trop longtemps. 👍" : "Aucun dossier" + (this.vue ? " « " + esc(this.vue) + " »" : "") + "."}</td></tr>`);
      return;
    }
    corps.html(d.machines.map((m) => this.ligne(m)).join(""));
    corps.find("[data-action]").on("click", (e) => {
      const b = $(e.currentTarget);
      this.action(b.attr("data-action"), b.attr("data-machine"));
    });
    corps.find("[data-photo]").on("click", (e) => {
      const b = $(e.currentTarget);
      this.galerie(b.attr("data-machine"), Number(b.attr("data-photo")));
    });
    corps.find("[data-detacher]").on("click", (e) => {
      const b = $(e.currentTarget);
      frappe.confirm(`Détacher ${b.attr("data-detacher")} du dossier ${b.attr("data-machine")} ? La tâche reste au calendrier.`, async () => {
        await frappe.call({ method: RO_API + ".detacher_tache", args: { machine: b.attr("data-machine"), tache: b.attr("data-detacher") } });
        this.charger();
      });
    });
  }

  // Toutes les photos du dossier : la première en grand carré, les suivantes en petits carrés. Clic = galerie.
  photos(m) {
    const esc = frappe.utils.escape_html;
    const p = m.photos || [];
    if (!p.length) return `<div class="ro-photo vide" title="Aucune photo">📷</div>`;
    // Sous la grande : trois petits carrés au plus. Au-delà de quatre photos, le dernier dit combien il en reste.
    const visibles = p.length > 4 ? 2 : 3;
    const mini = p.slice(1, 1 + visibles).map((x, i) =>
      `<img class="ro-mini" src="${esc(x.url)}" loading="lazy" title="${esc(x.titre)}" data-photo="${i + 1}" data-machine="${esc(m.name)}">`).join("");
    const reste = p.length > 4 ? `<span class="ro-mini plus" data-photo="3" data-machine="${esc(m.name)}" title="Voir toutes les photos">+${p.length - 3}</span>` : "";
    return `<img class="ro-photo" src="${esc(p[0].url)}" loading="lazy" title="${esc(p[0].titre)}" data-photo="0" data-machine="${esc(m.name)}">
            ${mini || reste ? `<div class="ro-minis">${mini}${reste}</div>` : ""}
            <div class="ro-sub" style="text-align:center">${p.length} photo${p.length > 1 ? "s" : ""}</div>`;
  }

  galerie(machine, index) {
    const esc = frappe.utils.escape_html;
    const m = (this.data.machines || []).find((x) => x.name === machine);
    const p = (m && m.photos) || [];
    if (!p.length) return;
    const d = new frappe.ui.Dialog({ title: `📷 ${machine} — ${esc(m.nom_client || "")}`, size: "extra-large", fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    d.fields_dict.zone.$wrapper.html(`<div class="ro-galerie">${p.map((x, i) => `
      <figure id="ro-g-${i}"><a href="${esc(x.url)}" target="_blank"><img src="${esc(x.url)}"></a>
        <figcaption>${i + 1} / ${p.length} · ${esc(x.titre)}</figcaption></figure>`).join("")}</div>`);
    d.show();
    setTimeout(() => { const el = d.$wrapper.find(`#ro-g-${index}`)[0]; if (el) el.scrollIntoView({ block: "center" }); }, 350);
  }

  // Où en est le règlement : ce que la commande liée a vraiment encaissé (une « Dette non payée » reste due).
  reglement(m) {
    const esc = frappe.utils.escape_html;
    const g = m.reglement;
    const lien = (c) => `<a href="/app/sales-order/${encodeURIComponent(c)}" target="_blank">${esc(c)}</a>`;
    if (m.statut === "Rendue au client") {
      let t = m.reglement_sortie ? `<div class="ro-sub">💰 ${esc(m.reglement_sortie)}</div>` : "";
      if (g && g.etat === "du") t += `<div class="ro-du">reste dû aujourd’hui : ${format_number(g.reste, null, 3)} DT</div>`;
      return t;
    }
    if (m.groupe !== "À rendre") return "";
    if (g) {
      return g.etat === "du" ? `<div class="ro-du">💰 ${lien(g.commande)} : reste dû ${format_number(g.reste, null, 3)} DT</div>`
        : g.etat === "zero" ? `<div class="ro-sub">💰 ${lien(g.commande)} : 0 DT</div>`
        : `<div class="ro-ok">💰 ${lien(g.commande)} : payée</div>`;
    }
    return m.garantie === "Sous garantie" ? `<div class="ro-sub">🆓 garantie${m.commande_garantie ? "" : " — commande d’origine non indiquée"}</div>`
      : `<div class="ro-sub">💰 aucune commande liée</div>`;
  }

  ligne(m) {
    const esc = frappe.utils.escape_html;
    const a = m.attente;
    const taches = (m.taches || []).map((t) => {
      const cls = t.status === "Completed" ? "ok" : t.status === "Cancelled" ? "ko" : "open";
      const st = t.status === "Completed" ? "clôturée" : t.status === "Cancelled" ? "annulée" : "ouverte";
      return `<a class="ro-tache" href="/app/tache-de-travail/${encodeURIComponent(t.name)}" target="_blank"
                 title="${esc(t.rapport || "")}">${t.name === m.tache_livraison ? "🚚" : "🛠️"} ${esc(ro_date(t.starts_on))} · ${esc(t.employe || "")}
                 · <span class="st ${cls}">${st}</span></a>${t.status === "Cancelled" && t.rapport ? `<div class="ro-sub" style="max-width:240px">« ${esc(strip_html(t.rapport).trim().slice(0, 90))} »</div>` : ""}${t.status === "Open" && (m.statut === "Planifiée" || m.statut === "Réceptionnée")
                 ? ` <span class="ro-sub" style="cursor:pointer" title="Détacher cette tâche du dossier (elle reste au calendrier)" data-detacher="${esc(t.name)}" data-machine="${esc(m.name)}">✕</span>` : ""}`;
    }).join("") || `<span class="ro-sub">—</span>`;

    // L'état, en clair : le groupe, puis ce qui se passe et depuis quand.
    const depuis = a ? ` depuis ${a.jours} j` : "";
    let detail = "";
    if (m.statut === "Réception en cours") detail = `<div>📷 réception à terminer (photos)</div>`;
    else if (m.statut === "Réceptionnée") detail = `<div class="${m.a_verifier ? "ro-du" : ""}">sans technicien${depuis}</div>`;
    else if (m.statut === "Planifiée") {
      // Le technicien de la tâche OUVERTE (lu sur la tâche : le nom gardé sur le dossier peut dater).
      const ouverte = (m.taches || []).find((t) => t.status === "Open");
      detail = `<div>chez ${esc((ouverte && ouverte.employe) || m.nom_responsable || "le technicien")}</div><div class="ro-sub">prévue le ${esc(ro_date(m.date_prevue))}</div>`;
    }
    else if (m.statut === "Réparée" || m.statut === "Prête au magasin") {
      detail = `<div class="${m.a_verifier ? "ro-du" : ""}">🏬 chez nous${depuis}</div>
                <div class="ro-sub">réparée le ${esc(ro_datetime(m.date_reparee))}</div>
                <div class="ro-sub">${m.sms_pret_le ? "📲 client prévenu le " + esc(ro_datetime(m.sms_pret_le)) : "📲 client pas encore prévenu"}</div>`;
    } else if (m.statut === "Livraison planifiée") {
      detail = `<div class="${m.a_verifier ? "ro-du" : ""}">🚚 livraison le ${esc(ro_date(m.date_livraison_prevue))}${a ? ` — pas clôturée (${a.jours} j)` : ""}</div>
                <div class="ro-sub">sa clôture ferme le dossier</div>`;
    } else if (m.statut === "Rendue au client") {
      detail = `<div>${m.sans_reparation ? "↪️ sans réparation" : "✔"} le ${esc(ro_datetime(m.date_rendue))}</div>
                <div class="ro-sub">${m.rendu_a ? "à " + esc(m.rendu_a) : ""}${m.rendue_par ? " · par " + esc(m.rendue_par) : ""}${m.mode_restitution === "Livraison" ? " · livraison" : ""}</div>
                ${m.motif_sortie ? `<div class="ro-sub">motif : ${esc(m.motif_sortie)}</div>` : ""}`;
    }
    detail += this.reglement(m);

    const btn = (action, libelle, cls = "btn-default") =>
      `<button class="btn btn-xs ${cls}" data-action="${action}" data-machine="${esc(m.name)}">${libelle}</button>`;
    // UN bouton principal : ce que le dossier attend. Le reste est derrière « Autres actions ».
    const boutons = [];
    if (m.statut === "Réception en cours") boutons.push(btn("photos", "📷 Photos et clôture", "btn-primary"));
    if (m.statut === "Réceptionnée") boutons.push(btn("planifier", "📅 Affecter un technicien", "btn-primary"));
    if (m.statut === "Réparée" || m.statut === "Prête au magasin") boutons.push(btn("rendre", "✅ Rendre au client", "btn-primary"));
    // La réponse au rappel, en un clic, quand le dossier attend depuis trop longtemps.
    if (m.a_verifier && m.statut === "Réceptionnée") boutons.push(btn("sans-reparation", "↪️ Sortie sans réparation"));
    if (m.a_verifier && (m.statut === "Réparée" || m.statut === "Prête au magasin" || m.statut === "Réceptionnée")) boutons.push(btn("toujours-la", "📍 Toujours au magasin"));
    boutons.push(btn("autres", "⋯ Autres actions"));

    return `<tr class="${m.a_verifier ? "retard" : ""}">
      <td class="ro-col-photos">${this.photos(m)}</td>
      <td><div class="ro-dossier">${esc(m.name)}</div>
          <div class="ro-sub">reçue le ${esc(ro_datetime(m.date_reception))}</div>
          ${m.dispense_post_it ? `<div class="ro-sub" title="Clôturée au code superviseur">🔓 sans post-it</div>` : ""}
          ${m.controle_ia_resultat ? `<div class="ro-sub" title="${esc(m.controle_ia_resultat)}">${m.controle_ia_ok ? "🤖 ✅ photos conformes" : "🤖 " + esc(m.controle_ia_resultat.split("\n")[0].slice(0, 60))}</div>` : ""}</td>
      <td><a href="/app/customer/${encodeURIComponent(m.client)}" target="_blank">${esc(m.nom_client || m.client)}</a>
          <div class="ro-sub">${esc(m.tel || "")}</div></td>
      <td>${m.garantie === "Sous garantie" ? `<div><span class="ro-badge b-garantie">🆓 Sous garantie</span>${m.commande_garantie ? ` <a class="ro-sub" href="/app/sales-order/${encodeURIComponent(m.commande_garantie)}" target="_blank">${esc(m.commande_garantie)}</a>` : ""}</div>`
             : m.garantie ? `<div class="ro-sub">Hors garantie</div>` : `<div class="ro-sub" style="color:#c2410c">garantie non décidée</div>`}
          <div class="ro-note">${esc(m.note_reception || "")}</div></td>
      <td class="ro-col-etat"><span class="ro-badge ${RO_GROUPES[m.groupe] || ""}">${esc(m.groupe)}</span>${detail}</td>
      <td>${taches}</td>
      <td><div class="ro-btns">${boutons.join("")}</div></td>
    </tr>`;
  }

  // Ce qui reste possible sur le dossier, hors bouton principal.
  autres_actions(m) {
    const a = [];
    const s = m.statut;
    if (s === "Réparée" || s === "Prête au magasin") {
      a.push(["sms", m.sms_pret_le ? "📲 Renvoyer le SMS « votre appareil est prêt »" : "📲 Prévenir le client par SMS (« votre appareil est prêt »)"]);
      a.push(["livraison", "🚚 Planifier une livraison chez le client"]);
      a.push(["toujours-la", "📍 Toujours au magasin (le rappel repart de zéro)"]);
      a.push(["tache", "➕ Nouvelle tâche de réparation (à reprendre)"]);
    }
    if (s === "Réceptionnée" || s === "Planifiée") {
      a.push(["tache", "➕ Tâche à la main (technicien et date choisis)"]);
      a.push(["rattacher", "🔗 Rattacher une tâche déjà au calendrier"]);
      a.push(["sans-reparation", "↪️ Sortie sans réparation (le client reprend sa machine…)"]);
      a.push(["rouvrir", "↩️ Rouvrir la réception"]);
    }
    if (s === "Livraison planifiée") a.push(["annuler-restitution", "↩️ Annuler la livraison"]);
    if (s === "Rendue au client") a.push(["annuler-sortie", "↩️ Annuler la sortie (clic par erreur)"]);
    a.push(["ouvrir", "📄 Ouvrir la fiche du dossier"]);
    return a;
  }

  async action(action, machine) {
    const m = (this.data.machines || []).find((x) => x.name === machine);
    const apres = () => this.charger();
    if (action === "autres") {
      const d = new frappe.ui.Dialog({ title: `${machine} — autres actions`, fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
      d.fields_dict.zone.$wrapper.html(this.autres_actions(m).map(([act, lib]) =>
        `<button class="btn btn-default" style="display:block;width:100%;text-align:left;margin-bottom:6px;white-space:normal" data-autre="${act}">${lib}</button>`).join(""));
      d.fields_dict.zone.$wrapper.find("[data-autre]").on("click", (e) => { d.hide(); this.action($(e.currentTarget).attr("data-autre"), machine); });
      d.show();
      return;
    }
    if (action === "ouvrir") return frappe.set_route("Form", "Machine Reparation", machine);
    if (action === "photos") return this.dialogue_photos(machine);
    if (action === "tache") return this.dialogue_tache(machine, m);
    if (action === "rattacher") return this.dialogue_rattacher(machine, m);
    if (action === "planifier") {
      const r = await frappe.call({ method: RO_API + ".planifier", args: { machine } });
      frappe.show_alert({ message: (r.message || {}).message || "OK", indicator: r.message && r.message.tache ? "green" : "orange" }, 7);
      return this.charger();
    }
    // La sortie : le même dialogue que partout ailleurs (public/js/reparation_osmoseur_sortie.js).
    if (action === "rendre") return window.ro_sortie.rendre(machine, { apres });
    if (action === "sans-reparation") return window.ro_sortie.sans_reparation(machine, { apres });
    if (action === "livraison") return window.ro_sortie.livraison(machine, m, { apres });
    if (action === "toujours-la") {
      await frappe.call({ method: RO_API + ".toujours_la", args: { machine } });
      frappe.show_alert({ message: `${machine} : toujours au magasin, noté`, indicator: "green" }, 5);
      return this.charger();
    }
    if (action === "sms") {
      frappe.confirm(`Envoyer le SMS « votre appareil est prêt » au ${frappe.utils.escape_html(m.tel || "numéro du dossier")} ?`, async () => {
        const r = m.statut === "Réparée"
          ? ((await frappe.call({ method: RO_API + ".restituer_magasin", args: { machine, sms: 1 } })).message || {}).sms
          : (await frappe.call({ method: RO_API + ".envoyer_sms_pret", args: { machine } })).message;
        const x = r || { numeros: [] };
        frappe.show_alert({ message: x.simule ? `SMS SIMULÉ (dev) → ${x.numeros.join(", ")}` : `SMS envoyé à ${x.numeros.join(", ")}`, indicator: x.simule ? "orange" : "green" }, 6);
        this.charger();
      });
      return;
    }
    if (action === "annuler-restitution") {
      frappe.confirm(`Annuler la livraison de ${machine} ? La tâche de livraison est supprimée et la machine reste à rendre.`, async () => {
        await frappe.call({ method: RO_API + ".annuler_restitution", args: { machine } });
        this.charger();
      });
      return;
    }
    if (action === "annuler-sortie") {
      frappe.confirm(`Annuler la sortie de ${machine} ? Le dossier redevient ouvert, comme si la machine était encore chez nous.`, async () => {
        await frappe.call({ method: RO_API + ".annuler_sortie", args: { machine } });
        this.charger();
      });
      return;
    }
    if (action === "rouvrir") {
      frappe.confirm(`Rouvrir la réception de ${machine} ? Les tâches ouvertes seront supprimées.`, async () => {
        await frappe.call({ method: RO_API + ".rouvrir_reception", args: { machine } });
        this.charger();
      });
    }
  }

  // ── Étape 1 : le dossier naît (il faut un numéro pour le post-it) ──────────
  // À gauche la réception, à droite les commandes du client sur 13 mois : c'est au vu de
  // cette liste (articles, images, appareils garantis 🛡️) que l'accueil décide de la garantie.
  nouvelle_reception() {
    const esc = frappe.utils.escape_html;
    const d = new frappe.ui.Dialog({
      title: "Nouvelle réception — étape 1 / 2",
      size: "extra-large",
      fields: [
        { fieldtype: "Link", fieldname: "client", label: "Client", options: "Customer", reqd: 1,
          onchange: () => {
            const c = d.get_value("client");
            if (!c) return;
            // « Liste Telephone » du client (une ligne par numéro), mobile_no sinon — rempli d'office.
            frappe.db.get_value("Customer", c, ["custom_liste_telephone", "mobile_no"]).then((r) => {
              const v = (r.message || {});
              const nums = String(v.custom_liste_telephone || "").replace(/\\n/g, "\n").split(/\n/).map((x) => x.trim()).filter(Boolean);
              d.set_value("tel", nums.length ? nums.join(" / ") : (v.mobile_no || ""));
            });
            charger_commandes(c);
          } },
        { fieldtype: "Data", fieldname: "tel", label: "Téléphone" },
        { fieldtype: "Small Text", fieldname: "note", label: "Note de réception",
          description: "Panne déclarée, accessoires laissés, remarques." },
        { fieldtype: "Select", fieldname: "garantie", label: "Garantie", reqd: 1,
          options: ["", "Sous garantie", "Hors garantie"],
          description: "Sous garantie = réparation gratuite, notée en tête de la tâche du technicien." },
        { fieldtype: "Link", fieldname: "commande_garantie", label: "Commande justifiant la garantie", options: "Sales Order",
          get_query: () => ({ filters: { customer: d.get_value("client") || "", docstatus: 1 } }) },
        { fieldtype: "Column Break" },
        { fieldtype: "HTML", fieldname: "commandes" },
      ],
      primary_action_label: "Créer le dossier et prendre les photos",
      primary_action: async (v) => {
        const r = await frappe.call({ method: RO_API + ".creer_reception", args: v });
        d.hide();
        const m = r.message || {};
        frappe.show_alert({ message: `Dossier ${m.name} créé`, indicator: "blue" });
        this.charger();
        this.dialogue_photos(m.name);
      },
    });
    const zone = () => d.fields_dict.commandes.$wrapper;
    const charger_commandes = async (client) => {
      zone().html(`<div class="ro-sub">Chargement des commandes…</div>`);
      const r = await frappe.call({ method: RO_API + ".commandes_client", args: { client } });
      const data = r.message || { commandes: [] };
      const depuis = frappe.datetime.str_to_user(data.depuis);
      if (!data.commandes.length) {
        zone().html(`<div class="ro-cmd-titre">Commandes depuis le ${depuis}</div>
                     <div class="ro-sub">Aucune commande validée sur la période : a priori hors garantie.</div>`);
        return;
      }
      zone().html(`<div class="ro-cmd-titre">Commandes depuis le ${depuis} — ${data.commandes.length}
          <span class="ro-sub">(🛡️ = appareil d’un groupe garanti ; cliquez une commande pour la retenir)</span></div>
        <div class="ro-cmd-liste">${data.commandes.map((c) => `
          <div class="ro-cmd ${c.garanti ? "garanti" : ""}" data-cmd="${esc(c.name)}">
            <div class="ro-cmd-tete"><b>${esc(c.name)}</b> · ${esc(frappe.datetime.str_to_user(c.transaction_date))}
              · <span class="ro-sub">${esc(c.status)} · ${format_currency(c.grand_total, "TND")}</span>
              ${c.garanti ? `<span class="ro-badge b-reparee">🛡️ garantie possible</span>` : ""}</div>
            ${c.articles.map((a) => `
              <div class="ro-cmd-art ${a.composant ? "composant" : ""}">
                ${a.image ? `<img src="${esc(a.image)}" loading="lazy">` : `<span class="ro-cmd-noimg">📦</span>`}
                <span><b>${a.qty}×</b> ${esc(a.item_name || a.item_code)} ${a.garanti ? "🛡️" : ""}
                  ${a.composant ? `<span class="ro-sub">(composant)</span>` : ""}</span>
              </div>`).join("")}
          </div>`).join("")}</div>`);
      zone().find(".ro-cmd").on("click", (e) => {
        const nom = $(e.currentTarget).attr("data-cmd");
        zone().find(".ro-cmd").removeClass("choisie"); $(e.currentTarget).addClass("choisie");
        d.set_value("commande_garantie", nom);
        if (!d.get_value("garantie") && $(e.currentTarget).hasClass("garanti")) d.set_value("garantie", "Sous garantie");
      });
    };
    zone().html(`<div class="ro-sub">Choisissez un client pour voir ses commandes des 13 derniers mois.</div>`);
    d.show();
  }

  // ── Étape 2 : photo à l'arrivée + photo post-it (ou code) → clôture ────────
  async dialogue_photos(machine) {
    const esc = frappe.utils.escape_html;
    let etat = (await frappe.call({ method: RO_API + ".etat_reception", args: { machine } })).message;
    const d = new frappe.ui.Dialog({
      title: `Réception ${machine} — étape 2 / 2`,
      fields: [
        { fieldtype: "HTML", fieldname: "zone" },
        { fieldtype: "Section Break", label: "Sans photo post-it ?" },
        { fieldtype: "Password", fieldname: "code", label: "Code superviseur",
          description: "Dispense de la photo avec post-it uniquement. La photo à l’arrivée reste obligatoire." },
      ],
      primary_action_label: "Clôturer la réception",
      primary_action: async (v) => {
        try {
          const r = await frappe.call({ method: RO_API + ".cloturer_reception",
                                        args: { machine, code: v.code || null } });
          d.hide();
          const m = r.message || {};
          let msg = m.tache
            ? `Réception clôturée. ${m.message}${m.employe ? " — " + m.employe : ""}`
            : `Réception clôturée, mais aucune tâche créée : ${m.message || ""}`;
          if (m.avertissement) msg += `<br><br>⚠️ ${frappe.utils.escape_html(m.avertissement)}`;
          frappe.msgprint({ title: machine, message: msg, indicator: m.tache && !m.avertissement ? "green" : "orange" });
          this.charger();
        } catch (e) { /* message déjà affiché par frappe.call */ }
      },
    });
    const rendre = () => {
      const pi = etat.post_it || {};
      const slots = [
        { champ: "arrivee", label: "Machine à l’arrivée", url: etat.photo_arrivee, obligatoire: true },
        { champ: "post_it", label: `Machine avec son post-it`, url: etat.photo_post_it,
          obligatoire: !etat.dispense_post_it,
          aide: `Écrivez sur un post-it collé sur la machine :
                 <div class="ro-postit">${esc(pi.nom || "")}<br>${esc(pi.tel || "")}<br>${esc(pi.ref || machine)}</div>
                 <div class="ro-sub" style="margin-top:4px">À la clôture, l’IA relit le post-it et compare l’appareil à la photo d’arrivée.</div>` },
      ];
      d.fields_dict.zone.$wrapper.html(`
        <div class="ro-sub" style="margin-bottom:6px">Client : <b>${esc(etat.nom_client || "")}</b>
          ${etat.note_reception ? "— " + esc(etat.note_reception) : ""}</div>
        ${slots.map((s, i) => `
          <div class="ro-slot">
            ${s.url ? `<a href="${esc(s.url)}" target="_blank"><img src="${esc(s.url)}"></a>` : `<div class="ro-vignette vide">📷</div>`}
            <div class="lbl"><div>${s.url ? "✅" : "📷"} <b>${s.label}</b>${s.obligatoire ? "" : " (facultatif)"}</div>
              ${s.aide ? `<div class="ro-sub" style="margin-top:4px">${s.aide}</div>` : ""}</div>
            <button class="btn btn-sm ${s.url ? "btn-default" : "btn-primary"}" data-slot="${i}">${s.url ? "Reprendre" : "Prendre la photo"}</button>
          </div>`).join("")}`);
      d.fields_dict.zone.$wrapper.find("[data-slot]").on("click", (e) => {
        const s = slots[Number($(e.currentTarget).attr("data-slot"))];
        new frappe.ui.FileUploader({
          doctype: "Machine Reparation", docname: machine, folder: "Home/Attachments",
          allow_multiple: false, restrictions: { allowed_file_types: ["image/*"] },
          on_success: (file) => {
            frappe.call({ method: RO_API + ".enregistrer_photo",
                          args: { machine, champ: s.champ, file_url: file.file_url } })
              .then((r) => { etat = r.message || etat; rendre(); });
          },
        });
      });
      d.get_primary_btn().toggleClass("btn-success", !!etat.peut_cloturer);
    };
    rendre();
    d.show();
  }

  // ── Guide du processus (lisible sur téléphone) ─────────────────────────────
  dialogue_guide() {
    const d = new frappe.ui.Dialog({ title: "❓ Réparation des osmoseurs — le processus", size: "large", fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    d.fields_dict.zone.$wrapper.html(`<div class="ro-guide">
      <p class="ro-sub">Un dossier passe par trois états. La ligne dit toujours ce qui se passe, depuis quand, et propose <b>un seul bouton</b> : ce qu’il attend.</p>
      <div class="et"><span class="ro-badge b-reparer">1 · À réparer</span><b>Accueil, puis technicien</b>
        <ul><li>« 📷 Nouvelle réception » : client, téléphone, note sur la panne, et la <b>garantie</b> — cliquer la commande qui couvre l’appareil (🛡️) ou choisir « Hors garantie ».</li>
        <li>Post-it <b>nom + téléphone + n° de dossier</b> collé sur la machine, puis « 📷 Photos et clôture » : l’IA relit le post-it et compare l’appareil.</li>
        <li>À la clôture, une tâche de réparation est créée pour le lendemain (responsables dans l’ordre du réglage, 3 machines par jour). Personne de libre → « sans technicien » : « 📅 Affecter un technicien ».</li>
        <li>Le client reprend sa machine sans réparation, devis refusé, réception par erreur → « ↪️ Sortie sans réparation » : le dossier se ferme avec son motif.</li></ul></div>
      <div class="et"><span class="ro-badge b-rendre">2 · À rendre</span><b>Celui qui remet la machine</b>
        <ul><li>Quand le technicien clôture sa tâche, la question lui est posée tout de suite : <b>le client est là</b> (je la rends), <b>elle attend au magasin</b> (SMS au client) ou <b>à livrer</b>.</li>
        <li>« ✅ Rendre au client » : on désigne la commande de la réparation et on voit son règlement. <b>Payée</b> ou <b>reste dû</b>, la machine sort — une dette reste sur le compte du client et s’affiche sur le dossier.</li>
        <li><b>Sous garantie</b> : rien à facturer si la commande d’origine de l’appareil est indiquée ; sinon le code superviseur.</li>
        <li><b>Hors garantie sans aucune vente</b> : un motif et le code superviseur.</li>
        <li>Le même bouton apparaît en bandeau sur la commande et le BL du client.</li>
        <li>Livraison : la clôture de la tâche Livraison ferme le dossier toute seule.</li></ul></div>
      <div class="et"><span class="ro-badge b-rendue">3 · Rendue</span><b>Terminé</b>
        <ul><li>Date, personne, règlement constaté et photos restent sur le dossier. La tuile « Rendue » ou la case « Afficher les machines rendues » les montre.</li></ul></div>
      <div class="et"><span class="ro-badge b-rendre">⏳ À vérifier</span><b>Rappel du matin</b>
        <ul><li>Une machine réparée et pas rendue, reçue sans technicien, ou dont la livraison n’est pas clôturée est rappelée chaque matin à la personne du réglage, puis relancée à une seconde personne.</li>
        <li>La réponse se donne sur la ligne : « ✅ Rendre au client », « 📍 Toujours au magasin » (le rappel repart de zéro), « 📅 Affecter » ou « ↪️ Sortie sans réparation ».</li></ul></div>
      <p class="ro-sub"><b>Erreurs courantes</b> : réception clôturée trop tôt → « Rouvrir la réception » · sortie cliquée par erreur → « Annuler la sortie » · code superviseur : réglage « Config Cloture Tache ».</p>
    </div>`);
    d.show();
  }

  // ── Rattacher une tâche Réparation déjà au calendrier ───────────────────────
  async dialogue_rattacher(machine, m) {
    const esc = frappe.utils.escape_html;
    const cands = (await frappe.call({ method: RO_API + ".taches_candidates", args: { machine } })).message || [];
    const d = new frappe.ui.Dialog({
      title: `🔗 Rattacher une tâche — ${machine}`,
      fields: [
        { fieldtype: "HTML", fieldname: "liste" },
        { fieldtype: "Link", fieldname: "tache", label: "Ou choisir une tâche", options: "Tache de travail",
          get_query: () => ({ filters: { custom_type_dintervention: "Réparation", status: "Open", machine_reparation: ["in", ["", null]] } }) },
      ],
      primary_action_label: "Rattacher",
      primary_action: async (v) => {
        if (!v.tache) { frappe.msgprint("Choisissez une tâche."); return; }
        try {
          const r = await frappe.call({ method: RO_API + ".rattacher_tache", args: { machine, tache: v.tache } });
          d.hide();
          frappe.show_alert({ message: `${v.tache} rattachée — dossier ${(r.message || {}).statut}`, indicator: "green" }, 5);
          this.charger();
        } catch (e) { /* message déjà affiché */ }
      },
    });
    d.fields_dict.liste.$wrapper.html(cands.length
      ? `<div class="ro-sub" style="margin-bottom:6px">Tâches Réparation ouvertes du calendrier, sans dossier${cands.some((c) => c.meme_client) ? " — celles de <b>" + esc(m.nom_client || m.client) + "</b> d’abord" : ""} :</div>
         ${cands.slice(0, 15).map((c) => `<div class="ro-slot" style="cursor:pointer" data-cand="${esc(c.name)}">
            <div class="lbl"><div>${c.meme_client ? "⭐ " : ""}<b>${esc(c.name)}</b> · ${esc(c.date)} · ${esc(c.employe)}</div>
              <div class="ro-sub">${esc(c.client)}${c.sujet ? " — " + esc(c.sujet) : ""}</div></div>
            <button class="btn btn-sm btn-default">Choisir</button></div>`).join("")}`
      : `<div class="ro-sub">Aucune tâche Réparation ouverte sans dossier dans le calendrier (30 derniers jours et à venir).</div>`);
    d.fields_dict.liste.$wrapper.find("[data-cand]").on("click", (e) => {
      d.set_value("tache", $(e.currentTarget).attr("data-cand"));
      d.fields_dict.liste.$wrapper.find("[data-cand]").css("background", "");
      $(e.currentTarget).css("background", "#f0fdf4");
    });
    d.show();
  }

  // ── Tâche à la main (technicien + date choisis) ─────────────────────────────
  dialogue_tache(machine, m) {
    const d = new frappe.ui.Dialog({
      title: `Tâche de réparation — ${machine}`,
      fields: [
        { fieldtype: "Link", fieldname: "employee", label: "Technicien", options: "Employee", reqd: 1,
          default: m && m.responsable, get_query: () => ({ filters: { status: "Active" } }) },
        { fieldtype: "Date", fieldname: "date", label: "Date", reqd: 1, default: frappe.datetime.add_days(frappe.datetime.get_today(), 1) },
        { fieldtype: "Time", fieldname: "heure", label: "Heure", default: "09:00:00" },
      ],
      primary_action_label: "Créer la tâche",
      primary_action: async (v) => {
        const r = await frappe.call({ method: RO_API + ".affecter_tache",
                                      args: { machine, employee: v.employee, date: v.date, heure: String(v.heure || "09:00").slice(0, 5) } });
        d.hide();
        frappe.show_alert({ message: `Tâche ${(r.message || {}).tache} créée`, indicator: "green" });
        this.charger();
      },
    });
    d.show();
  }
}
