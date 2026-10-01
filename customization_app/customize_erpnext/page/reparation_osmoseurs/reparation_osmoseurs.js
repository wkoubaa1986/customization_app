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
  "Rendue au client": "b-rendue",
};
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
          <div class="v">${d.kpis[s] || 0}</div><div class="l">${esc(s)}</div>
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
  }

  ligne(m) {
    const esc = frappe.utils.escape_html;
    const photo = m.photo_arrivee
      ? `<a href="${esc(m.photo_arrivee)}" target="_blank"><img class="ro-vignette" src="${esc(m.photo_arrivee)}"></a>`
      : `<div class="ro-vignette vide">📷</div>`;
    const retard = m.statut === "Réparée" && m.age_jours > RO_RETARD_JOURS;
    const taches = (m.taches || []).map((t) => {
      const cls = t.status === "Completed" ? "ok" : t.status === "Cancelled" ? "ko" : "open";
      const st = t.status === "Completed" ? "clôturée" : t.status === "Cancelled" ? "annulée" : "ouverte";
      return `<a class="ro-tache" href="/app/tache-de-travail/${encodeURIComponent(t.name)}" target="_blank"
                 title="${esc(t.rapport || "")}">🛠️ ${esc(ro_date(t.starts_on))} · ${esc(t.employe || "")}
                 · <span class="st ${cls}">${st}</span></a>`;
    }).join("") || `<span class="ro-sub">—</span>`;

    let reparation = "";
    if (m.statut === "Planifiée" || m.statut === "Réparée") {
      reparation = `<div>${esc(m.nom_responsable || m.responsable || "")}</div>
                    <div class="ro-sub">prévue le ${esc(ro_date(m.date_prevue))}</div>`;
      if (m.date_reparee) reparation += `<div class="ro-sub">réparée le ${esc(ro_datetime(m.date_reparee))}</div>`;
    } else if (m.statut === "Réceptionnée") {
      reparation = `<span class="ro-sub">à affecter</span>`;
    } else if (m.statut === "Rendue au client") {
      reparation = `<div class="ro-sub">rendue le ${esc(ro_datetime(m.date_rendue))}</div>`;
    }
    if (retard) reparation += `<div style="color:#c2410c;font-weight:600">⏳ prête depuis ${m.age_jours} j</div>`;

    const btn = (action, libelle, cls = "btn-default") =>
      `<button class="btn btn-xs ${cls}" data-action="${action}" data-machine="${esc(m.name)}">${libelle}</button>`;
    const boutons = [];
    if (m.statut === "Réception en cours") boutons.push(btn("photos", "📷 Photos et clôture", "btn-primary"));
    if (m.statut === "Réceptionnée") boutons.push(btn("planifier", "📅 Affecter (auto)", "btn-primary"));
    if (m.statut !== "Réception en cours" && m.statut !== "Rendue au client") boutons.push(btn("tache", "➕ Tâche à la main"));
    if (m.statut === "Réparée") boutons.push(btn("rendre", "✅ Rendue au client", "btn-success"));
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
      <td><span class="ro-badge ${RO_CLASSES[m.statut] || ""}">${esc(m.statut)}</span></td>
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
    if (action === "planifier") {
      const r = await frappe.call({ method: RO_API + ".planifier", args: { machine } });
      frappe.show_alert({ message: (r.message || {}).message || "OK", indicator: r.message && r.message.tache ? "green" : "orange" }, 7);
      return this.charger();
    }
    if (action === "rendre") {
      frappe.confirm(`Marquer ${machine} comme rendue au client ?`, async () => {
        await frappe.call({ method: RO_API + ".rendre", args: { machine } });
        frappe.show_alert({ message: "Machine rendue au client", indicator: "green" });
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
