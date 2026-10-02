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
  new ReparationOsmoseurs(wrapper);
};

const RO_API = "customization_app.reparation_osmoseur";
const RO_CLASSES = {
  "Réception en cours": "b-reception",
  "Réceptionnée": "b-receptionnee",
  "Planifiée": "b-planifiee",
  "Réparée": "b-reparee",
  "Prête au magasin": "b-prete",
  "Livraison planifiée": "b-livraison",
  "Rendue au client": "b-rendue",
};
// Libellés d'écran : « Réparée » est l'étape À RENDRE.
const RO_LIBELLES = { "Réparée": "Réparée — à rendre" };
const RO_RETARD_JOURS = 7; // réparée mais toujours pas rendue

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
    this.statut = null;
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
        statut: this.statut,
        responsable: r.find("#ro-responsable").val() || null,
        recherche: r.find("#ro-recherche").val() || null,
        inclure_rendues: r.find("#ro-rendues").is(":checked") ? 1 : 0,
      },
      freeze: false,
    });
    this.data = res.message || { machines: [], kpis: {}, statuts: [] };
    this.rendre();
  }

  rendre() {
    const d = this.data;
    const esc = frappe.utils.escape_html;
    const r = this.$root;

    // KPI cliquables = filtre par statut
    r.find("#ro-kpis").html(
      d.statuts.map((s) => `
        <div class="ro-kpi ${this.statut === s ? "actif" : ""}" data-statut="${esc(s)}">
          <div class="v">${d.kpis[s] || 0}</div><div class="l">${esc(RO_LIBELLES[s] || s)}</div>
        </div>`).join("")
    );
    r.find("#ro-kpis .ro-kpi").on("click", (e) => {
      const s = $(e.currentTarget).attr("data-statut");
      this.statut = this.statut === s ? null : s;
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
      alerte.html("⚠️ Aucun responsable de réparation configuré : les réceptions clôturées restent « Réceptionnée » " +
        "jusqu’à une affectation à la main. " +
        (d.peut_configurer ? `<a href="/app/config-reparation-osmoseur">Configurer les responsables</a>` : "Prévenez un administrateur.")).show();
    } else {
      alerte.hide();
    }

    const corps = r.find("#ro-corps");
    if (!d.machines.length) {
      corps.html(`<tr><td colspan="8" class="ro-vide">Aucun dossier${this.statut ? " « " + esc(this.statut) + " »" : ""}.</td></tr>`);
      return;
    }
    corps.html(d.machines.map((m) => this.ligne(m)).join(""));
    corps.find("[data-action]").on("click", (e) => {
      const b = $(e.currentTarget);
      this.action(b.attr("data-action"), b.attr("data-machine"));
    });
    corps.find("[data-detacher]").on("click", (e) => {
      const b = $(e.currentTarget);
      frappe.confirm(`Détacher ${b.attr("data-detacher")} du dossier ${b.attr("data-machine")} ? La tâche reste au calendrier.`, async () => {
        await frappe.call({ method: RO_API + ".detacher_tache", args: { machine: b.attr("data-machine"), tache: b.attr("data-detacher") } });
        this.charger();
      });
    });
  }

  ligne(m) {
    const esc = frappe.utils.escape_html;
    const photo = m.photo_arrivee
      ? `<a href="${esc(m.photo_arrivee)}" target="_blank"><img class="ro-vignette" src="${esc(m.photo_arrivee)}"></a>`
      : `<div class="ro-vignette vide">📷</div>`;
    const retard = (m.statut === "Réparée" || m.statut === "Prête au magasin") && m.age_jours > RO_RETARD_JOURS;
    const taches = (m.taches || []).map((t) => {
      const cls = t.status === "Completed" ? "ok" : t.status === "Cancelled" ? "ko" : "open";
      const st = t.status === "Completed" ? "clôturée" : t.status === "Cancelled" ? "annulée" : "ouverte";
      return `<a class="ro-tache" href="/app/tache-de-travail/${encodeURIComponent(t.name)}" target="_blank"
                 title="${esc(t.rapport || "")}">🛠️ ${esc(ro_date(t.starts_on))} · ${esc(t.employe || "")}
                 · <span class="st ${cls}">${st}</span></a>${t.status === "Open" && (m.statut === "Planifiée" || m.statut === "Réceptionnée")
                 ? ` <span class="ro-sub" style="cursor:pointer" title="Détacher cette tâche du dossier (elle reste au calendrier)" data-detacher="${esc(t.name)}" data-machine="${esc(m.name)}">✕</span>` : ""}`;
    }).join("") || `<span class="ro-sub">—</span>`;

    let reparation = "";
    if (m.statut === "Planifiée" || m.statut === "Réparée") {
      reparation = `<div>${esc(m.nom_responsable || m.responsable || "")}</div>
                    <div class="ro-sub">prévue le ${esc(ro_date(m.date_prevue))}</div>`;
      if (m.date_reparee) reparation += `<div class="ro-sub">réparée le ${esc(ro_datetime(m.date_reparee))}</div>`;
    } else if (m.statut === "Réceptionnée") {
      reparation = `<span class="ro-sub">à affecter</span>`;
    } else if (m.statut === "Prête au magasin") {
      reparation = `<div>🏬 Au magasin, le client vient la chercher</div>
                    <div class="ro-sub">réparée le ${esc(ro_datetime(m.date_reparee))}</div>
                    <div class="ro-sub">${m.sms_pret_le ? "📲 SMS « prête » envoyé le " + esc(ro_datetime(m.sms_pret_le)) : "📲 SMS « prête » non envoyé"}</div>`;
    } else if (m.statut === "Livraison planifiée") {
      reparation = `<div>🚚 Livraison le ${esc(ro_date(m.date_livraison_prevue))}</div>
                    <div class="ro-sub">${m.tache_livraison ? `<a href="/app/tache-de-travail/${encodeURIComponent(m.tache_livraison)}" target="_blank">${esc(m.tache_livraison)}</a> · ` : ""}la clôture de la livraison rend la machine</div>`;
    } else if (m.statut === "Rendue au client") {
      reparation = `<div class="ro-sub">rendue le ${esc(ro_datetime(m.date_rendue))}${m.rendu_a ? " — " + esc(m.rendu_a) : ""}</div>
                    <div class="ro-sub">${esc(m.mode_restitution || "")}${m.photo_remise ? ` · <a href="${esc(m.photo_remise)}" target="_blank">📷 remise</a>` : ""}</div>`;
    }
    if (retard) reparation += `<div style="color:#c2410c;font-weight:600">⏳ prête depuis ${m.age_jours} j</div>`;

    const btn = (action, libelle, cls = "btn-default") =>
      `<button class="btn btn-xs ${cls}" data-action="${action}" data-machine="${esc(m.name)}">${libelle}</button>`;
    const boutons = [];
    if (m.statut === "Réception en cours") boutons.push(btn("photos", "📷 Photos et clôture", "btn-primary"));
    if (m.statut === "Réceptionnée") boutons.push(btn("planifier", "📅 Affecter (auto)", "btn-primary"));
    if (m.statut !== "Réception en cours" && m.statut !== "Rendue au client") boutons.push(btn("tache", "➕ Tâche à la main"));
    if (m.statut === "Réceptionnée" || m.statut === "Planifiée") boutons.push(btn("rattacher", "🔗 Rattacher une tâche"));
    if (m.statut === "Réparée") boutons.push(btn("restitution", "📦 Restitution", "btn-primary"));
    if (m.statut === "Prête au magasin") {
      boutons.push(btn("rendre", "✅ Rendue au client", "btn-success"));
      if (!m.sms_pret_le) boutons.push(btn("sms", "📲 SMS « prête »"));
      boutons.push(btn("livraison", "🚚 Planifier une livraison"));
      boutons.push(btn("annuler-restitution", "↩️ Annuler la restitution"));
    }
    if (m.statut === "Livraison planifiée") boutons.push(btn("annuler-restitution", "↩️ Annuler la livraison"));
    if (m.statut === "Réceptionnée" || m.statut === "Planifiée") boutons.push(btn("rouvrir", "↩️ Rouvrir la réception"));
    boutons.push(btn("ouvrir", "Ouvrir"));

    return `<tr class="${retard ? "retard" : ""}">
      <td>${photo}</td>
      <td><div class="ro-dossier">${esc(m.name)}</div>
          <div class="ro-sub">reçue le ${esc(ro_datetime(m.date_reception))}</div>
          ${m.dispense_post_it ? `<div class="ro-sub" title="Clôturée au code superviseur">🔓 sans post-it</div>` : ""}
          ${m.controle_ia_resultat ? `<div class="ro-sub" title="${esc(m.controle_ia_resultat)}">${m.controle_ia_ok ? "🤖 ✅ photos conformes" : "🤖 " + esc(m.controle_ia_resultat.split("\n")[0].slice(0, 60))}</div>` : ""}</td>
      <td><a href="/app/customer/${encodeURIComponent(m.client)}" target="_blank">${esc(m.nom_client || m.client)}</a>
          <div class="ro-sub">${esc(m.tel || "")}</div></td>
      <td>${m.garantie === "Sous garantie" ? `<div><span class="ro-badge b-reparee">🆓 Sous garantie</span>${m.commande_garantie ? ` <a class="ro-sub" href="/app/sales-order/${encodeURIComponent(m.commande_garantie)}" target="_blank">${esc(m.commande_garantie)}</a>` : ""}</div>`
             : m.garantie ? `<div class="ro-sub">Hors garantie</div>` : `<div class="ro-sub" style="color:#c2410c">garantie non décidée</div>`}
          <div class="ro-note">${esc(m.note_reception || "")}</div></td>
      <td><span class="ro-badge ${RO_CLASSES[m.statut] || ""}">${esc(RO_LIBELLES[m.statut] || m.statut)}</span></td>
      <td>${reparation}</td>
      <td>${taches}</td>
      <td><div class="ro-btns">${boutons.join("")}</div></td>
    </tr>`;
  }

  async action(action, machine) {
    const m = (this.data.machines || []).find((x) => x.name === machine);
    if (action === "ouvrir") return frappe.set_route("Form", "Machine Reparation", machine);
    if (action === "photos") return this.dialogue_photos(machine);
    if (action === "tache") return this.dialogue_tache(machine, m);
    if (action === "rattacher") return this.dialogue_rattacher(machine, m);
    if (action === "planifier") {
      const r = await frappe.call({ method: RO_API + ".planifier", args: { machine } });
      frappe.show_alert({ message: (r.message || {}).message || "OK", indicator: r.message && r.message.tache ? "green" : "orange" }, 7);
      return this.charger();
    }
    if (action === "restitution") return this.dialogue_restitution(machine, m);
    if (action === "livraison") return this.dialogue_livraison(machine, m);
    if (action === "rendre") return this.dialogue_rendre(machine, m);
    if (action === "sms") {
      frappe.confirm(`Envoyer le SMS « machine prête » au ${frappe.utils.escape_html(m.tel || "numéro du dossier")} ?`, async () => {
        const r = await frappe.call({ method: RO_API + ".envoyer_sms_pret", args: { machine } });
        const x = r.message || {};
        frappe.show_alert({ message: x.simule ? `SMS SIMULÉ (dev) → ${x.numeros.join(", ")}` : `SMS envoyé à ${x.numeros.join(", ")}`, indicator: x.simule ? "orange" : "green" }, 6);
        this.charger();
      });
      return;
    }
    if (action === "annuler-restitution") {
      frappe.confirm(`Annuler la restitution de ${machine} ? Le dossier revient à « Réparée — à rendre »${m.tache_livraison ? " et la tâche de livraison est supprimée" : ""}.`, async () => {
        await frappe.call({ method: RO_API + ".annuler_restitution", args: { machine } });
        this.charger();
      });
      return;
    }
    if (action === "rouvrir") {
      frappe.confirm(`Rouvrir la réception de ${machine} ? Les tâches ouvertes seront annulées.`, async () => {
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

  // ── Restitution : le client vient, ou on livre ─────────────────────────────
  dialogue_restitution(machine, m) {
    const esc = frappe.utils.escape_html;
    const d = new frappe.ui.Dialog({
      title: `📦 Restitution — ${machine}`,
      fields: [{ fieldtype: "HTML", fieldname: "zone" }],
    });
    d.fields_dict.zone.$wrapper.html(`
      <div class="ro-sub" style="margin-bottom:10px">Client : <b>${esc(m.nom_client || m.client)}</b> · ${esc(m.tel || "")}
        ${m.garantie === "Hors garantie" ? `<div style="color:#b45309;margin-top:4px">💰 Hors garantie : la réparation est à encaisser à la remise.</div>` : `<div style="color:#166534;margin-top:4px">🆓 Sous garantie : rien à facturer.</div>`}</div>
      <div class="ro-choix">
        <div class="ro-choix-carte">
          <div class="t">🏬 Le client vient la chercher</div>
          <div class="ro-sub">La machine attend au magasin ; vous marquerez « Rendue au client » à la remise.</div>
          <label style="display:block;margin:8px 0;font-weight:500"><input type="checkbox" data-sms checked> Prévenir le client par SMS (« votre appareil est réparé… »)</label>
          <button class="btn btn-primary btn-sm" data-magasin>Mettre en attente au magasin</button>
        </div>
        <div class="ro-choix-carte">
          <div class="t">🚚 Planifier une livraison</div>
          <div class="ro-sub">Une tâche Livraison au calendrier, à l’adresse du client ; sa clôture rend la machine. SMS au client proposé.</div>
          <button class="btn btn-default btn-sm" data-livrer style="margin-top:8px">Choisir la date et le livreur</button>
        </div>
      </div>`);
    d.fields_dict.zone.$wrapper.find("[data-magasin]").on("click", async () => {
      const sms = d.fields_dict.zone.$wrapper.find("[data-sms]").is(":checked") ? 1 : 0;
      try {
        const r = await frappe.call({ method: RO_API + ".restituer_magasin", args: { machine, sms } });
        d.hide();
        const x = (r.message || {}).sms;
        frappe.show_alert({ message: x ? (x.simule ? `Au magasin · SMS SIMULÉ (dev) → ${x.numeros.join(", ")}` : `Au magasin · SMS envoyé à ${x.numeros.join(", ")}`) : "Au magasin, en attente du client", indicator: "green" }, 6);
        this.charger();
      } catch (e) { /* message déjà affiché */ }
    });
    d.fields_dict.zone.$wrapper.find("[data-livrer]").on("click", () => { d.hide(); this.dialogue_livraison(machine, m); });
    d.show();
  }

  async dialogue_livraison(machine, m) {
    const adresses = (await frappe.call({ method: RO_API + ".adresses_client", args: { client: m.client } })).message || [];
    const d = new frappe.ui.Dialog({
      title: `🚚 Livraison — ${machine}`,
      fields: [
        { fieldtype: "Link", fieldname: "employee", label: "Livreur", options: "Employee", reqd: 1,
          default: m && m.responsable, get_query: () => ({ filters: { status: "Active" } }) },
        { fieldtype: "Date", fieldname: "date", label: "Date", reqd: 1, default: frappe.datetime.add_days(frappe.datetime.get_today(), 1) },
        { fieldtype: "Time", fieldname: "heure", label: "Heure", default: "09:00:00" },
        { fieldtype: "Select", fieldname: "adresse", label: "Adresse de livraison", reqd: adresses.length ? 1 : 0,
          options: adresses.map((a) => a.name).join("\n"), default: adresses.length ? adresses[0].name : "",
          description: adresses.length ? "" : "Ce client n’a pas d’adresse enregistrée : la tâche sera créée sans adresse." },
        { fieldtype: "Small Text", fieldname: "note", label: "Note pour le livreur" },
        { fieldtype: "Check", fieldname: "sms", label: "Prévenir le client par SMS (« nous vous le livrons le … »)", default: 1 },
      ],
      primary_action_label: "Créer la livraison",
      primary_action: async (v) => {
        try {
          const r = await frappe.call({ method: RO_API + ".planifier_livraison",
                                        args: { machine, employee: v.employee, date: v.date, heure: String(v.heure || "09:00").slice(0, 5), adresse: v.adresse || null, note: v.note || null, sms: v.sms ? 1 : 0 } });
          d.hide();
          const x = r.message || {};
          const sms = x.sms ? (x.sms.simule ? ` · SMS SIMULÉ (dev) → ${x.sms.numeros.join(", ")}` : ` · SMS envoyé à ${x.sms.numeros.join(", ")}`) : "";
          frappe.show_alert({ message: `Livraison ${x.tache} créée le ${x.date} — ${x.employe}${x.adresse ? " · " + x.adresse : ""}${sms}`, indicator: "green" }, 8);
          this.charger();
        } catch (e) { /* message déjà affiché */ }
      },
    });
    // libellés lisibles pour les adresses (le Select Frappe n'affiche que la valeur)
    const sel = d.fields_dict.adresse && d.fields_dict.adresse.$input;
    if (sel) adresses.forEach((a) => sel.find(`option[value="${CSS.escape(a.name)}"]`).text(`${a.libelle || a.name}${a.custom_secteur ? " · " + a.custom_secteur : ""}`));
    d.show();
  }

  dialogue_rendre(machine, m) {
    const esc = frappe.utils.escape_html;
    let photo = m.photo_remise || "";
    const d = new frappe.ui.Dialog({
      title: `✅ Rendue au client — ${machine}`,
      fields: [
        { fieldtype: "Data", fieldname: "rendu_a", label: "Remise à (personne)", reqd: 1, default: m.nom_client || "" },
        { fieldtype: "Small Text", fieldname: "remarque", label: "Remarque (facultatif)" },
        { fieldtype: "HTML", fieldname: "zone" },
      ],
      primary_action_label: "Confirmer la remise",
      primary_action: async (v) => {
        try {
          await frappe.call({ method: RO_API + ".rendre", args: { machine, rendu_a: v.rendu_a, remarque: v.remarque || null } });
          d.hide();
          frappe.show_alert({ message: "Machine rendue au client", indicator: "green" });
          this.charger();
        } catch (e) { /* message déjà affiché */ }
      },
    });
    const rendre = () => {
      d.fields_dict.zone.$wrapper.html(`
        ${m.garantie === "Hors garantie" ? `<div style="color:#b45309;margin-bottom:8px">💰 Hors garantie : encaisser la réparation à la remise (caisse).</div>` : ""}
        <div class="ro-slot">
          ${photo ? `<a href="${esc(photo)}" target="_blank"><img src="${esc(photo)}"></a>` : `<div class="ro-vignette vide">📷</div>`}
          <div class="lbl"><div>${photo ? "✅" : "📷"} <b>Photo de la remise</b> (facultatif)</div>
            <div class="ro-sub">La machine avec la personne qui la récupère, ou le reçu signé.</div></div>
          <button class="btn btn-sm btn-default" data-photo>${photo ? "Reprendre" : "Prendre la photo"}</button>
        </div>`);
      d.fields_dict.zone.$wrapper.find("[data-photo]").on("click", () => {
        new frappe.ui.FileUploader({
          doctype: "Machine Reparation", docname: machine, folder: "Home/Attachments",
          allow_multiple: false, restrictions: { allowed_file_types: ["image/*"] },
          on_success: (file) => {
            frappe.call({ method: RO_API + ".enregistrer_photo", args: { machine, champ: "remise", file_url: file.file_url } })
              .then(() => { photo = file.file_url; rendre(); });
          },
        });
      });
    };
    rendre();
    d.show();
  }

  // ── Guide du processus (lisible sur téléphone) ─────────────────────────────
  dialogue_guide() {
    const d = new frappe.ui.Dialog({ title: "❓ Réparation des osmoseurs — le processus", size: "large", fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    d.fields_dict.zone.$wrapper.html(`<div class="ro-guide">
      <p class="ro-sub">Chaque dossier avance de haut en bas. Le statut dit où il en est et <b>qui doit agir</b>.</p>
      <div class="et"><span class="ro-badge b-reception">1 · Réception en cours</span><b>Accueil</b>
        <ul><li>« 📷 Nouvelle réception » : client, téléphone (rempli d’office), note sur la panne.</li>
        <li><b>Garantie à décider</b> : la liste des commandes du client sur 13 mois s’affiche ; cliquer la commande qui couvre l’appareil (🛡️ = garanti) ou choisir « Hors garantie ».</li>
        <li>Écrire sur un post-it <b>nom + téléphone + n° de dossier</b>, le coller sur la machine.</li>
        <li>« 📷 Photos et clôture » : photo à l’arrivée, puis photo avec le post-it (ou le code superviseur). L’IA relit le post-it et compare l’appareil ; si ça ne correspond pas, la clôture est refusée.</li></ul></div>
      <div class="et"><span class="ro-badge b-planifiee">2 · Planifiée</span><b>Automatique</b>
        <ul><li>À la clôture, une tâche <b>Réparation</b> est créée pour le lendemain : responsables dans l’ordre du réglage, un seul opérateur par jour, 3 machines par jour maxi, 1 h 15 chacune.</li>
        <li>Personne de libre ou réglage vide → « Réceptionnée » : « 📅 Affecter » ou « ➕ Tâche à la main ».</li>
        <li>Une réparation déjà au calendrier pour ce client ? « 🔗 Rattacher une tâche du calendrier » la relie au dossier (✕ pour la détacher).</li></ul></div>
      <div class="et"><span class="ro-badge b-reparee">3 · Réparée — à rendre</span><b>Technicien</b>
        <ul><li>Le technicien clôture sa tâche comme n’importe quelle intervention (photos obligatoires, rapport). Quand la dernière tâche est clôturée, le dossier passe ici tout seul.</li>
        <li>Une machine sous garantie porte « 🆓 GARANTIE » : rien à facturer.</li></ul></div>
      <div class="et"><span class="ro-badge b-prete">4a · Prête au magasin</span><b>Accueil — « 📦 Restitution » → le client vient</b>
        <ul><li>Un SMS « votre appareil est réparé » part si la case est cochée (ré-envoyable ensuite).</li>
        <li>À la remise : « ✅ Rendue au client » — nom de la personne, photo facultative. Hors garantie : encaisser à la caisse.</li>
        <li>Au-delà de 7 jours au magasin, la ligne passe en orange.</li></ul></div>
      <div class="et"><span class="ro-badge b-livraison">4b · Livraison planifiée</span><b>Accueil — « 📦 Restitution » → livrer</b>
        <ul><li>Choisir livreur, date, adresse : une tâche <b>Livraison</b> apparaît au calendrier et dans « Ma journée » du livreur ; un SMS « nous vous le livrons le … » part si la case est cochée.</li>
        <li>Quand le livreur clôture sa tâche (photos), le dossier passe « Rendue au client » tout seul. Tâche supprimée → retour à « Réparée — à rendre ».</li></ul></div>
      <div class="et"><span class="ro-badge b-rendue">5 · Rendue au client</span><b>Terminé</b>
        <ul><li>Date, personne, mode (magasin ou livraison) et photo restent sur le dossier. Cocher « Afficher les machines rendues » pour les revoir.</li></ul></div>
      <p class="ro-sub"><b>Erreurs courantes</b> : réception clôturée trop tôt → « ↩️ Rouvrir la réception » (tant qu’aucune réparation n’est clôturée) · restitution lancée par erreur → « ↩️ Annuler la restitution » · code superviseur : réglage « Config Cloture Tache ».</p>
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
