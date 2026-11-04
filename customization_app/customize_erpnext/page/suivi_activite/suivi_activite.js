/**
 * « Suivi d'activité » — une seule liste pour l'équipe.
 *
 * On écrit l'activité en tête de liste (Entrée) et on l'affecte à un ou plusieurs employés :
 * c'est UNE fiche partagée, que chaque employé affecté voit dans sa liste. Le rond en début de
 * ligne la marque terminée. Les règles (qui voit quoi, qui affecte) sont côté serveur
 * (customization_app.suivi_activite + permissions du DocType) : l'écran n'est qu'un client.
 */

frappe.pages["suivi-activite"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Suivi d’activité", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("suivi_activite", {}));
  wrapper.suivi = new SuiviActivite(wrapper);
};
frappe.pages["suivi-activite"].on_page_show = function (wrapper) {
  // Venu du Calendrier de travail (clic sur une réunion) : frappe.route_options.reunion = REU-…
  const ro = frappe.route_options || {};
  if (ro.reunion) { wrapper.suivi.reunion_a_ouvrir = ro.reunion; frappe.route_options = null; }
  if (wrapper.suivi && wrapper.suivi.ctx) { wrapper.suivi.charger(); wrapper.suivi.ouvrir_reunion_demandee(); }
};

const SA_API = "customization_app.suivi_activite";
const RR_API = "customization_app.reunion_activite";
const sa_jour = (d) => { const o = frappe.datetime.str_to_obj(String(d).slice(0, 10)); return ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."][o.getDay()]; };
/** Libellé court d'une date d'étape : « ven. 24/10 », avec le retard en jours si elle est passée et non faite. */
function sa_date_etape(e) {
  if (!e.date_prevue) return { txt: "sans date", cls: "vide" };
  const diff = frappe.datetime.get_day_diff(frappe.datetime.get_today(), e.date_prevue);
  const base = sa_jour(e.date_prevue) + " " + sa_date(e.date_prevue).slice(0, 5);
  if (e.fait) return { txt: base, cls: "" };
  if (diff > 0) return { txt: `⏰ ${base} (+${diff} j)`, cls: "retard" };
  if (diff === 0) return { txt: `aujourd’hui`, cls: "" };
  return { txt: base, cls: "" };
}
const SA_ICONES = { "À faire": "📋", "En cours": "🔧", "En attente": "⏸️", "Terminée": "✅", "Annulée": "🚫" };
const SA_CLASSES = { "À faire": "s-afaire", "En cours": "s-encours", "En attente": "s-attente", "Terminée": "s-terminee", "Annulée": "s-annulee" };
const SA_FERMES = ["Terminée", "Annulée"];
const sa_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const sa_date = (d) => (d ? frappe.datetime.str_to_user(String(d).slice(0, 10)) : "");

/** Pastilles d'employés à cocher (une ou plusieurs). `choisis` est un Set modifié sur place. */
function sa_chips($zone, employes, choisis, on_change) {
  $zone.html(employes.map((e) => `<span class="sa-chip ${choisis.has(e.name) ? "on" : ""}" data-emp="${sa_esc(e.name)}">${sa_esc(e.employee_name)}</span>`).join(""));
  $zone.off("click.sa").on("click.sa", ".sa-chip", (ev) => {
    const emp = $(ev.currentTarget).attr("data-emp");
    choisis.has(emp) ? choisis.delete(emp) : choisis.add(emp);
    $(ev.currentTarget).toggleClass("on", choisis.has(emp));
    on_change && on_change();
  });
}

// Même sélecteur à cases à cocher dans une étape et dans la proposition IA.
frappe.suivi_responsables_html = (equipe, responsables, options = {}) => {
  const choisis = new Set(responsables || []);
  const noms = equipe.filter((e) => choisis.has(e.name)).map((e) => e.employee_name);
  return `<details class="sa-responsables" ${options.ligne ? `data-ligne="${sa_esc(options.ligne)}"` : ""}>
    <summary><span class="sa-responsables-label">👥 ${sa_esc(noms.join(", ") || "Responsables…")}</span></summary>
    <div class="sa-responsables-choix">${equipe.map((e) => `<label><input type="checkbox" value="${sa_esc(e.name)}" data-nom="${sa_esc(e.employee_name)}" ${choisis.has(e.name) ? "checked" : ""}> ${sa_esc(e.employee_name)}</label>`).join("") || '<span class="text-muted small">Aucun employé actif affecté à l’activité.</span>'}
      ${options.appliquer ? '<button type="button" class="btn btn-primary btn-xs sa-appliquer-responsables">Appliquer</button>' : ""}</div></details>`;
};
$(document).off("change.sa-responsables").on("change.sa-responsables", ".sa-responsables input[type=checkbox]", (e) => {
  const $d = $(e.target).closest(".sa-responsables");
  const noms = $d.find("input:checked").map((_, el) => $(el).attr("data-nom")).get();
  $d.find(".sa-responsables-label").text("👥 " + (noms.join(", ") || "Responsables…"));
});

class SuiviActivite {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".sa-page");
    this.filtre = null;          // statut, « En retard » ou null
    this.pour = new Set();       // employés choisis pour la prochaine activité écrite
    this.init();
  }

  async init() {
    try {
      this.ctx = (await frappe.call({ method: SA_API + ".get_context" })).message;
    } catch (e) {
      this.$root.html(`<div class="sa-card">Le suivi d’activité n’est pas ouvert à votre compte.</div>`);
      return;
    }
    const r = this.$root, ctx = this.ctx;
    r.find("#sa-prio").html(ctx.priorites.map((p) => `<option ${p === "Normale" ? "selected" : ""}>${sa_esc(p)}</option>`).join(""));
    if (ctx.responsable) {
      r.find("#sa-pour").show();
      sa_chips(r.find("#sa-chips"), ctx.employes || [], this.pour);
      r.find("#sa-employe").show();
      (ctx.employes || []).forEach((e) =>
        r.find("#sa-employe").append(`<option value="${sa_esc(e.name)}">${sa_esc(e.employee_name)}</option>`));
      r.find("#sa-aide").text("Choisissez un ou plusieurs employés, écrivez l’activité, puis Entrée. Chacun la verra dans sa liste.");
    } else {
      r.find("#sa-aide").text("Écrivez une activité puis Entrée : elle s’ajoute à votre liste. Cliquez une activité pour l’ouvrir.");
    }
    r.find("#sa-config").toggle(!!ctx.peut_configurer).on("click", () => frappe.set_route("Form", "Config Suivi Activite"));
    r.find(".sa-onglet").on("click", (e) => this.onglet($(e.currentTarget).attr("data-onglet")));
    this.reunions = new SuiviReunions(this);
    this.ouvrir_reunion_demandee();
    r.find("#sa-titre").on("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.ajouter(); } });
    r.find("#sa-envoi").on("click", () => this.ajouter());
    r.find("#sa-formaliser").on("click", () => this.formaliser({
      texte: r.find("#sa-titre").val(), employes: Array.from(this.pour),
      date_prevue: r.find("#sa-date").val(), priorite: r.find("#sa-prio").val(),
      on_success: async () => { r.find("#sa-titre").val(""); await this.charger(); },
    }));
    r.find("#sa-employe, #sa-fermees").on("change", () => this.charger());
    let t = null;
    r.find("#sa-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.charger(), 300); });
    this.charger();
  }

  ouvrir_reunion_demandee() {
    const name = this.reunion_a_ouvrir;
    if (!name) return;
    this.reunion_a_ouvrir = null;
    this.onglet("reunions");
    this.reunions.ouvrir(name);
  }

  async formaliser(options) {
    await frappe.require("/assets/customization_app/js/suivi_activite_ia.js");
    return window.sa_formaliser_ia({ ctx: this.ctx, ...options });
  }

  onglet(nom) {
    const r = this.$root;
    r.find(".sa-onglet").removeClass("actif").filter(`[data-onglet="${nom}"]`).addClass("actif");
    r.find("#sa-vue-activites").toggle(nom === "activites");
    r.find("#sa-vue-reunions").toggle(nom === "reunions");
    if (nom === "reunions") this.reunions.charger();
  }

  async ajouter() {
    const r = this.$root;
    const titre = (r.find("#sa-titre").val() || "").trim();
    if (!titre) return r.find("#sa-titre").focus();
    const data = { titre, date_prevue: r.find("#sa-date").val() || null, priorite: r.find("#sa-prio").val() };
    if (this.ctx.responsable) {
      if (!this.pour.size) {
        frappe.show_alert({ message: "Choisissez au moins un employé (pastilles « Pour »).", indicator: "orange" }, 5);
        return;
      }
      data.employes = Array.from(this.pour);
    }
    await frappe.call({ method: SA_API + ".enregistrer", args: { data } });
    // On garde les employés et la date : on enchaîne souvent plusieurs activités pour les mêmes.
    r.find("#sa-titre").val("").focus();
    frappe.show_alert({ message: "Activité ajoutée", indicator: "green" }, 3);
    this.charger();
  }

  async charger() {
    const r = this.$root;
    const res = await frappe.call({
      method: SA_API + ".get_activites",
      args: { employe: r.find("#sa-employe").val() || null, recherche: r.find("#sa-recherche").val() || null,
              inclure_fermees: r.find("#sa-fermees").is(":checked") ? 1 : 0 },
      freeze: false,
    });
    this.data = res.message || { activites: [], kpis: {} };
    this.rendre();
    if (this.reunions) this.reunions.compteur();
  }

  rendre() {
    const { activites, kpis } = this.data;
    const r = this.$root;
    r.find("#sa-kpis").html(["À faire", "En cours", "En attente", "En retard", "Terminée"].map((k) => `
      <div class="sa-kpi ${k === "En retard" ? "retard" : ""} ${this.filtre === k ? "actif" : ""}" data-filtre="${sa_esc(k)}">
        <div class="v">${kpis[k] || 0}</div><div class="l">${sa_esc(k === "Terminée" ? "Terminées (30 j)" : k)}</div></div>`).join(""));
    r.find("#sa-kpis .sa-kpi").on("click", (e) => {
      const k = $(e.currentTarget).attr("data-filtre");
      this.filtre = this.filtre === k ? null : k;
      this.rendre();
    });

    let liste = activites;
    if (this.filtre === "En retard") liste = liste.filter((a) => a.en_retard);
    else if (this.filtre) liste = liste.filter((a) => a.statut === this.filtre);
    const ouvertes = liste.filter((a) => !SA_FERMES.includes(a.statut));
    const fermees = liste.filter((a) => SA_FERMES.includes(a.statut));
    const $l = r.find("#sa-liste");
    if (!liste.length) {
      $l.html(`<div class="sa-vide">${this.filtre ? "Rien dans ce filtre." : "Aucune activité pour l’instant. Écrivez la première ci-dessus."}</div>`);
      return;
    }
    $l.html((ouvertes.length ? `<div class="sa-groupe">À faire et en cours — ${ouvertes.length}</div>${ouvertes.map((a) => this.ligne(a)).join("")}` : "")
      + (fermees.length ? `<div class="sa-groupe">Terminées et annulées — ${fermees.length}</div>${fermees.map((a) => this.ligne(a)).join("")}` : ""));
    $l.find(".sa-coche").on("click", (e) => {
      e.stopPropagation();
      const name = $(e.currentTarget).closest(".sa-ligne").attr("data-name");
      const a = activites.find((x) => x.name === name);
      this.basculer_terminee(a);
    });
    $l.find(".sa-ligne").on("click", (e) => this.ouvrir($(e.currentTarget).attr("data-name")));
  }

  async basculer_terminee(a) {
    const statut = a.statut === "Terminée" ? "En cours" : "Terminée";
    await frappe.call({ method: SA_API + ".changer_statut", args: { name: a.name, statut } });
    frappe.show_alert({ message: statut === "Terminée" ? `✅ « ${a.titre} » terminée` : "Activité rouverte", indicator: "green" }, 3);
    this.charger();
  }

  ligne(a) {
    const [faites, total] = a.etapes || [0, 0];
    const meta = [];
    if (total) meta.push(`☑️ ${faites}/${total}`);
    if (a.etapes_retard) meta.push(`<b style="color:#b91c1c">⏰ ${a.etapes_retard} étape${a.etapes_retard > 1 ? "s" : ""} en retard</b>`);
    else if (a.prochaine && !SA_FERMES.includes(a.statut)) meta.push(`→ ${sa_esc(a.prochaine.libelle)}${a.prochaine.responsable_nom ? " · 👤 " + sa_esc(a.prochaine.responsable_nom) : ""}${a.prochaine.date_prevue ? " · " + sa_esc(sa_jour(a.prochaine.date_prevue) + " " + sa_date(a.prochaine.date_prevue).slice(0, 5)) : ""}`);
    if (a.nb_fichiers) meta.push(`📎 ${a.nb_fichiers}`);
    if (a.nb_notes) meta.push(`📝 ${a.nb_notes}`);
    if (a.ia_ameliore) meta.push(`✨`);
    if (a.priorite === "Urgente" || a.priorite === "Haute") meta.push(`<b>${sa_esc(a.priorite)}</b>`);
    const retard = a.en_retard ? frappe.datetime.get_day_diff(frappe.datetime.get_today(), a.date_prevue) : 0;
    const date = a.statut === "Terminée" && a.date_fin ? `<span class="sa-date">✅ ${sa_esc(sa_date(a.date_fin))}</span>`
      : a.date_prevue ? `<span class="sa-date ${a.en_retard ? "retard" : ""}">${a.en_retard ? "⏰ " + retard + " j de retard" : "📅 " + sa_esc(sa_date(a.date_prevue))}</span>`
      : `<span class="sa-date" style="color:#cbd5e1">—</span>`;
    return `<div class="sa-ligne p-${sa_esc(a.priorite)} ${SA_FERMES.includes(a.statut) ? "ferme" : ""}" data-name="${sa_esc(a.name)}">
      <span class="sa-coche ${a.statut === "Terminée" ? "ok" : ""}" title="${a.statut === "Terminée" ? "Rouvrir" : "Marquer terminée"}">${a.statut === "Terminée" ? "✓" : ""}</span>
      <div><div class="t">${sa_esc(a.titre)}</div>${meta.length ? `<div class="m">${meta.map((m) => `<span>${m}</span>`).join("")}</div>` : ""}</div>
      <div class="col">${(a.employes || []).map((e) => `<span class="sa-pastille">${sa_esc(e.nom)}</span>`).join("")}</div>
      <div class="col">${date}</div>
      <div class="col"><span class="sa-badge ${SA_CLASSES[a.statut] || ""}">${SA_ICONES[a.statut] || ""} ${sa_esc(a.statut)}</span></div>
      <div class="col"><div class="sa-prog"><div style="width:${Math.max(0, Math.min(100, a.avancement || 0))}%"></div></div>
        <div class="sa-pct">${a.avancement || 0} %</div></div>
    </div>`;
  }

  // ── Fiche détaillée ──────────────────────────────────────────────────────────
  async ouvrir(name, apres_modification) {
    const ctx = this.ctx;
    let a = (await frappe.call({ method: SA_API + ".get_activite", args: { name } })).message;
    const choisis = new Set(a.employes || []);
    const equipe = (a.employes_disponibles || ctx.employes || []).slice();
    (a.affectations || []).filter((r) => r.actif !== false).forEach((r) => { if (!equipe.some((e) => e.name === r.employe)) equipe.push({ name: r.employe, employee_name: r.nom }); });
    const d = new frappe.ui.Dialog({
      title: `${a.name}`,
      size: "extra-large",
      fields: [
        { fieldtype: "Data", fieldname: "titre", label: "Activité", reqd: 1, default: a.titre },
        { fieldtype: "HTML", fieldname: "pour" },
        { fieldtype: "Select", fieldname: "priorite", label: "Priorité", options: ctx.priorites, default: a.priorite },
        { fieldtype: "Column Break" },
        { fieldtype: "Date", fieldname: "date_debut", label: "Date de début", default: a.date_debut },
        { fieldtype: "Date", fieldname: "date_prevue", label: "Date prévisionnelle", default: a.date_prevue },
        { fieldtype: "HTML", fieldname: "statut_zone" },
        { fieldtype: "Section Break" },
        { fieldtype: "Small Text", fieldname: "description", label: "Description", default: a.description },
        { fieldtype: "HTML", fieldname: "zone" },
      ],
      primary_action_label: "Enregistrer",
      primary_action: async (v) => {
        const data = Object.assign({ name: a.name }, v);
        if (ctx.responsable) {
          if (!choisis.size) return frappe.msgprint("Affectez l’activité à au moins un employé.");
          data.employes = Array.from(choisis);
        }
        await frappe.call({ method: SA_API + ".enregistrer", args: { data } });
        frappe.show_alert({ message: "Enregistré", indicator: "green" });
        d.hide();
        this.charger();
        if (apres_modification) await apres_modification();
      },
    });
    const $pour = d.fields_dict.pour.$wrapper;
    if (ctx.responsable) {
      $pour.html(`<label class="control-label" style="font-size:12px">Employés</label><div class="sa-chips"></div>`);
      sa_chips($pour.find(".sa-chips"), equipe, choisis);
    } else {
      $pour.html(`<label class="control-label" style="font-size:12px">Employés</label>
        <div>${(a.affectations || []).filter((r) => r.actif !== false).map((r) => `<span class="sa-pastille">${sa_esc(r.nom)}</span>`).join("")}</div>`);
    }
    if (a.peut_supprimer) {
      d.set_secondary_action_label("🗑️ Supprimer");
      d.set_secondary_action(() => frappe.confirm(`Supprimer définitivement « ${sa_esc(a.titre)} » ?`, async () => {
        await frappe.call({ method: SA_API + ".supprimer", args: { name: a.name } });
        d.hide(); this.charger();
      }));
    }

    const recharger = async () => {
      a = (await frappe.call({ method: SA_API + ".get_activite", args: { name } })).message;
      peindre();
      this.charger();
      if (apres_modification) await apres_modification();
    };
    const appel = async (methode, args) => {
      await frappe.call({ method: SA_API + "." + methode, args: Object.assign({ name: a.name }, args) });
      await recharger();
    };

    const peindre = () => {
      d.fields_dict.statut_zone.$wrapper.html(`<label class="control-label" style="font-size:12px">Statut</label>
        <div class="sa-statuts">${ctx.statuts.map((s) => `<button type="button" class="btn btn-xs ${s === a.statut ? "btn-primary" : "btn-default"}"
           data-statut="${sa_esc(s)}">${SA_ICONES[s]} ${sa_esc(s)}</button>`).join("")}</div>
        <div class="small text-muted" style="margin-top:4px">Avancement : <b>${a.avancement || 0} %</b>
          ${a.date_fin ? " · terminée le " + sa_esc(sa_date(a.date_fin)) : ""}${a.ia_ameliore ? " · ✨ améliorée par l’IA" : ""}</div>`);

      const equipeAct = (a.affectations || []).filter((r) => r.actif !== false).map((r) => ({ name: r.employe, employee_name: r.nom }));
      const selResp = (e) => frappe.suivi_responsables_html(equipeAct, e.responsables || (e.responsable ? [e.responsable] : []), { ligne: e.name, appliquer: true });
      const n = (a.etapes || []).length;
      const etapes = (a.etapes || []).map((e, i) => {
        const dt = sa_date_etape(e);
        return `
        <div class="sa-etape ${e.fait ? "fait" : ""}">
          <span class="ord"><span data-ordre="monter" data-ligne="${sa_esc(e.name)}" class="${i === 0 ? "off" : ""}" title="Monter">▲</span><span data-ordre="descendre" data-ligne="${sa_esc(e.name)}" class="${i === n - 1 ? "off" : ""}" title="Descendre">▼</span></span>
          <input type="checkbox" data-etape="${sa_esc(e.name)}" ${e.fait ? "checked" : ""}>
          <span class="lib">${sa_esc(e.libelle)}</span>
          ${selResp(e)}
          <span class="dt ${dt.cls}" data-dater="${sa_esc(e.name)}" data-val="${sa_esc(e.date_prevue || "")}" title="Changer la date">📅 ${sa_esc(dt.txt)}</span>
          ${e.fait && e.fait_le ? `<span class="small text-muted">✓ ${sa_esc(sa_date(e.fait_le))}</span>` : ""}
          <span class="sup" data-sup-etape="${sa_esc(e.name)}" title="Retirer">✕</span></div>`; }).join("");
      const photos = (a.fichiers || []).filter((f) => f.photo);
      const docs = (a.fichiers || []).filter((f) => !f.photo);
      const notes = (a.notes || []).slice().reverse();
      d.fields_dict.zone.$wrapper.html(`
        <div style="display:flex;flex-wrap:wrap;gap:6px"><button type="button" class="btn btn-default btn-sm sa-btn-ia" data-mode="Clarifier ma tâche">✨ Clarifier</button>
          <button type="button" class="btn btn-default btn-sm sa-btn-ia" data-mode="Répartir le travail">Répartir le travail</button>
          <button type="button" class="btn btn-default btn-sm sa-btn-ia" data-mode="Vérifier ma tâche">Vérifier</button></div>
        <div class="sa-section"><h6>☑️ Étapes</h6>${etapes || `<div class="small text-muted">Aucune étape.</div>`}
          <div class="sa-ajout"><input type="text" class="form-control input-sm sa-nouvelle-etape" placeholder="Nouvelle étape puis Entrée…">
            <div class="sa-etape-resps">${frappe.suivi_responsables_html(equipeAct, [])}</div>
            <input type="date" class="form-control input-sm sa-etape-date" title="Prévue le"><button type="button" class="btn btn-default btn-sm sa-ajouter-etape">Ajouter</button></div></div>
        <div class="sa-section"><h6>📷 Photos et documents
            <button type="button" class="btn btn-default btn-xs sa-btn-fichier">➕ Ajouter</button></h6>
          ${photos.length ? `<div class="sa-galerie">${photos.map((f) => `<div class="ph">
              <a href="${sa_esc(f.fichier)}" target="_blank" title="${sa_esc(f.description || f.nom)}"><img src="${sa_esc(f.fichier)}" loading="lazy"></a>
              <span class="sup" data-sup-fichier="${sa_esc(f.name)}" title="Retirer">✕</span></div>`).join("")}</div>` : ""}
          ${docs.map((f) => `<div class="sa-fic">📄 <a href="${sa_esc(f.fichier)}" target="_blank">${sa_esc(f.description || f.nom)}</a>
              <span class="small text-muted">${sa_esc(sa_date(f.ajoute_le))}</span>
              <span class="sup" data-sup-fichier="${sa_esc(f.name)}" title="Retirer">✕</span></div>`).join("")}
          ${!photos.length && !docs.length ? `<div class="small text-muted">Aucune pièce.</div>` : ""}</div>
        <div class="sa-section"><h6>📝 Notes</h6>
          <div class="sa-ajout"><textarea class="form-control sa-nouvelle-note" rows="2" placeholder="Ce qui a été fait, ce qui bloque…"></textarea>
            <button type="button" class="btn btn-default btn-sm sa-btn-note">Noter</button></div>
          <div style="margin-top:8px">${notes.map((n) => `<div class="sa-note">
              <div class="qui">${sa_esc(n.auteur_nom || "")} · ${sa_esc(sa_date(n.date))} ${sa_esc(String(n.date || "").slice(11, 16))}</div>
              <div class="txt">${sa_esc(n.texte)}</div></div>`).join("") || `<div class="small text-muted">Aucune note.</div>`}</div></div>`);
    };
    peindre();

    const $z = d.$wrapper;
    $z.on("click", "[data-statut]", (e) => appel("changer_statut", { statut: $(e.currentTarget).attr("data-statut") }));
    $z.on("change", "[data-etape]", async (e) => {
      await appel("etape", { action: "basculer", ligne: $(e.currentTarget).attr("data-etape") });
      // Dernière étape cochée : on propose de clore, sans le faire d'office (une étape oubliée
      // dans la liste ne doit pas fermer l'activité dans le dos de l'employé).
      if ((a.etapes || []).length && a.avancement >= 100 && !SA_FERMES.includes(a.statut)) {
        frappe.confirm("Toutes les étapes sont faites. Marquer l’activité comme <b>terminée</b> ?",
          () => appel("changer_statut", { statut: "Terminée" }));
      }
    });
    $z.on("click", "[data-sup-etape]", (e) => appel("etape", { action: "supprimer", ligne: $(e.currentTarget).attr("data-sup-etape") }));
    // Date d'une étape : clic sur la date → champ date en place, Entrée/changement = enregistré, Échap = abandon.
    $z.on("click", "[data-dater]", (e) => {
      const $s = $(e.currentTarget), ligne = $s.attr("data-dater");
      const $i = $(`<input type="date" class="form-control dt-edit" value="${sa_esc($s.attr("data-val"))}">`);
      $s.replaceWith($i); $i.focus();
      let fini = false;
      const valider = () => { if (fini) return; fini = true; appel("etape", { action: "dater", ligne, date: $i.val() || null }); };
      // Entrée sans preventDefault = soumission implicite du formulaire du dialogue : le 1er bouton (✨ IA) serait cliqué.
      $i.on("change", valider).on("keydown", (k) => { if (k.key === "Escape") { fini = true; peindre(); } if (k.key === "Enter") { k.preventDefault(); valider(); } })
        .on("blur", () => { if (!fini) { fini = true; if ($i.val() !== $s.attr("data-val")) appel("etape", { action: "dater", ligne, date: $i.val() || null }); else peindre(); } });
    });
    const ajouter_etape = () => {
      const v = $z.find(".sa-nouvelle-etape").val();
      if (v && v.trim()) return appel("etape", { action: "ajouter", libelle: v.trim(), date: $z.find(".sa-etape-date").val() || null,
        employes: $z.find(".sa-etape-resps input:checked").map((_, el) => el.value).get() });
    };
    $z.on("click", ".sa-ajouter-etape", ajouter_etape);
    $z.on("keydown", ".sa-nouvelle-etape", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      ajouter_etape();
    });
    $z.on("click", ".sa-appliquer-responsables", async (e) => {
      const $bouton = $(e.currentTarget), $d = $bouton.closest(".sa-responsables");
      $bouton.prop("disabled", true);
      try { await appel("etape", { action: "affecter", ligne: $d.attr("data-ligne"), employes: $d.find("input:checked").map((_, el) => el.value).get() }); }
      finally { $bouton.prop("disabled", false); }
    });
    $z.on("click", "[data-ordre]", (e) => { const $o = $(e.currentTarget); if (!$o.hasClass("off")) appel("etape", { action: $o.attr("data-ordre"), ligne: $o.attr("data-ligne") }); });
    $z.on("click", ".sa-btn-note", () => {
      const v = $z.find(".sa-nouvelle-note").val();
      if (v && v.trim()) appel("ajouter_note", { texte: v.trim() });
    });
    $z.on("click", "[data-sup-fichier]", (e) => {
      const ligne = $(e.currentTarget).attr("data-sup-fichier");
      frappe.confirm("Retirer cette pièce de l’activité ?", () => appel("supprimer_fichier", { ligne }));
    });
    $z.on("click", ".sa-btn-fichier", () => {
      new frappe.ui.FileUploader({
        doctype: "Activite Employe", docname: a.name, folder: "Home/Attachments", allow_multiple: true,
        on_success: (file) => {
          const fichiers = Array.isArray(file) ? file : [file];
          Promise.all(fichiers.map((f) => frappe.call({ method: SA_API + ".ajouter_fichier",
            args: { name: a.name, file_url: f.file_url, description: f.file_name } }))).then(recharger);
        },
      });
    });
    $z.on("click", ".sa-btn-ia", (e) => {
      const v = d.get_values(true);
      this.formaliser({ ...v, name: a.name, employes: Array.from(choisis), mode: $(e.currentTarget).attr("data-mode"),
        on_success: async () => {
          await recharger();
          await d.set_values({ titre: a.titre, description: a.description, date_prevue: a.date_prevue });
        },
      });
    });
    d.show();
  }
}

// ── Réunions d'équipe : point d'avancement toutes les N semaines ──────────────
const RR_STATUT = { "Prévue": "🗓️ Prévue", "En cours": "▶ En cours", "Terminée": "✅ Terminée", "Tenue": "✅ Terminée", "Annulée": "🚫 Annulée" };
const rr_etat = (r) => r.statut_affiche || (r.statut === "Tenue" ? "Terminée" : r.statut);
const rr_ouverte = (r) => ["Prévue", "En cours"].includes(rr_etat(r));
const rr_badge = (r) => `<span class="sa-badge ${rr_etat(r) === "Terminée" ? "s-terminee" : rr_etat(r) === "Annulée" ? "s-annulee" : "s-encours"}">${RR_STATUT[rr_etat(r)] || sa_esc(rr_etat(r))}</span>`;
const rr_heure = (h) => { const [hh, mm] = String(h || "").split(":"); return hh === undefined || mm === undefined ? "" : hh.padStart(2, "0") + ":" + mm.padStart(2, "0"); };
const rr_quand = (r) => `${sa_jour(r.date)} ${sa_date(r.date)} · ${rr_heure(r.heure)}`;
const rr_tag = (a) => a.statut === "Terminée" ? `<span class="rr-tag done">Terminée ${a.date_fin ? sa_esc(sa_date(a.date_fin).slice(0, 5)) : ""}</span>`
  : a.retard_jours ? `<span class="rr-tag late">Retard ${a.retard_jours} j</span>`
  : a.etapes_retard ? `<span class="rr-tag late">${a.etapes_retard} étape${a.etapes_retard > 1 ? "s" : ""} en retard</span>`
  : a.statut === "En cours" ? `<span class="rr-tag run">En cours ${a.avancement || 0} %</span>`
  : a.statut === "En attente" ? `<span class="rr-tag run">En attente</span>` : `<span class="rr-tag todo">À faire</span>`;

/** Cases à cocher des activités ouvertes des employés choisis ; `choisies` (Set) vide = toutes leurs activités. */
async function rr_picker_activites($zone, employes, choisies, toutes) {
  if (!employes.length && !toutes) { $zone.html(`<div class="small text-muted">Choisissez d’abord des participants.</div>`); return; }
  const r = await frappe.call({ method: RR_API + ".activites_de", args: { employes, toutes: toutes ? 1 : 0 }, freeze: false });
  const acts = r.message || [];
  Array.from(choisies).forEach((n) => { if (!acts.some((a) => a.name === n)) choisies.delete(n); });
  let sep = false;
  $zone.html(acts.length ? acts.map((a) => {
      const titre = (toutes && !a.des_participants && !sep) ? (sep = true, `<div class="small text-muted" style="margin:6px 0 2px;border-top:1px solid #e4e8ee;padding-top:4px">Autres employés</div>`) : "";
      return titre + `<label style="display:block;font-weight:normal;margin:2px 0">
      <input type="checkbox" class="rr-act" value="${sa_esc(a.name)}" ${choisies.has(a.name) ? "checked" : ""}>
      ${sa_esc(a.titre)} <span class="text-muted small">· ${sa_esc(a.noms_employes || "")} · ${sa_esc(a.statut)}${a.avancement ? " " + a.avancement + " %" : ""}${a.date_prevue ? " · prévue " + sa_esc(sa_date(a.date_prevue)) : ""}</span></label>`; }).join("")
    : `<div class="small text-muted">Aucune activité ouverte${toutes ? "" : " pour ces participants"}.</div>`);
  $zone.off("change.rr").on("change.rr", ".rr-act", (e) => { const v = e.currentTarget.value; e.currentTarget.checked ? choisies.add(v) : choisies.delete(v); });
}
const RR_REPETE = ["Non", "Chaque semaine", "Toutes les 2 semaines", "Toutes les 3 semaines", "Toutes les 4 semaines"];
const rr_repete_n = (v) => { const i = RR_REPETE.indexOf(v); return i <= 0 ? 0 : i; };

class SuiviReunions {
  constructor(parent) {
    this.parent = parent;
    this.$root = parent.$root;
    this.data = null;
    this.actualisation = setInterval(() => {
      if (document.visibilityState === "visible" && frappe.get_route()[0] === "suivi-activite") {
        this.charger(!this.$root.find("#sa-vue-reunions").is(":visible")).catch(() => {});
      }
    }, 60000);
  }

  async compteur() {
    // Pastille de l'onglet : nombre de réunions prévues (chargé une fois, sans ouvrir l'onglet).
    if (this.data) return;
    try { await this.charger(true); } catch (e) { /* onglet non accessible */ }
  }

  async charger(silencieux) {
    const res = await frappe.call({ method: RR_API + ".get_reunions", freeze: false });
    this.data = res.message || { a_venir: [], passees: [] };
    const n = (this.data.a_venir || []).length;
    this.$root.find("#sa-reu-n").text(n).toggle(n > 0);
    if (this.actualiser_dialogue) this.actualiser_dialogue(this.data);
    if (!silencieux) this.rendre();
  }

  rendre() {
    const d = this.data, r = this.$root, c = d.config || {};
    const regl = [];
    if (c.active) regl.push(`🔁 Toutes les ${c.toutes_les} semaine${c.toutes_les > 1 ? "s" : ""}, le ${sa_esc(String(c.jour || "").toLowerCase())} à ${sa_esc(rr_heure(c.heure))}, ${sa_esc(c.duree)} min · ${sa_esc(c.lieu || "")}${c.visio ? " + visio" : ""}${c.rappel_sms ? " · rappel SMS" : ""}`);
    else regl.push(`Réunions créées à la main (le réglage peut les créer toutes les N semaines).`);
    if (d.prochaine_date) regl.push(`Prochaine occurrence : <b>${sa_esc(sa_jour(d.prochaine_date))} ${sa_esc(sa_date(d.prochaine_date))}</b>`);
    r.find("#sa-reu-regl").html(regl.map((x) => `<span>${x}</span>`).join("")
      + (d.peut_gerer ? `<span style="margin-left:auto;display:flex;gap:6px"><button type="button" class="btn btn-default btn-xs" id="rr-nouvelle">➕ Nouvelle réunion</button>
         ${this.parent.ctx.peut_configurer ? `<button type="button" class="btn btn-default btn-xs" id="rr-regl">⚙️ Réglage</button>` : ""}</span>` : ""));
    r.find("#rr-nouvelle").on("click", () => this.nouvelle());
    r.find("#rr-regl").on("click", () => frappe.set_route("Form", "Config Suivi Activite"));

    const ligne = (x, cls) => `<div class="sa-reu ${cls}" data-name="${sa_esc(x.name)}">
      <div class="d">${sa_esc(rr_quand(x))}<small>${sa_esc(x.duree_min || 45)} min · ${sa_esc(x.lieu || "")}</small></div>
      <div class="t">${sa_esc(x.titre)}<small>${x.statut === "Prévue" ? `${(x.participants || []).length} participant(s)` : `${x.nb_presents} présent(s)`}
        ${x.toutes_les_semaines ? ` · 🔁 toutes les ${x.toutes_les_semaines} sem.` : ""}${(x.suivies || []).length ? ` · suit ${x.suivies.length} activité(s)` : ""}
        ${x.nb_decisions ? ` · ${x.nb_decisions} décision(s), ${x.nb_decisions_faites} faite(s)` : ""}</small></div>
      <div class="visio">${x.lien_visio && rr_ouverte(x) ? `<a href="${sa_esc(x.lien_visio)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🎥 Rejoindre</a>` : ""}</div>
      <div>${rr_badge(x)}</div></div>`;
    const av = d.a_venir || [], pa = d.passees || [];
    const historique_ouvert = r.find("#rr-historique").prop("open");
    r.find("#sa-reu-liste").html(
      `<div class="sa-groupe">${av.some((x) => rr_etat(x) === "En cours") ? "À venir et en cours" : "À venir"} — ${av.length}</div>` + (av.length ? av.map((x, i) => ligne(x, i === 0 ? "prochaine" : "")).join("")
        : `<div class="sa-reu-vide">Aucune réunion prévue.${d.peut_gerer ? " Cliquez « Nouvelle réunion », ou activez la création automatique dans le réglage." : ""}</div>`)
      + (pa.length ? `<details id="rr-historique" ${historique_ouvert ? "open" : ""}><summary class="sa-groupe" style="cursor:pointer">Réunions passées et annulées — ${pa.length}${pa.length === 20 ? " dernières" : ""}</summary>` + pa.map((x) => ligne(x, "tenue")).join("") + "</details>" : ""));
    r.find("#sa-reu-liste .sa-reu").on("click", (e) => this.ouvrir($(e.currentTarget).attr("data-name")));
  }

  nouvelle() {
    const d = this.data || {}, c = d.config || {};
    const equipe = this.parent.ctx.employes || [];
    const choisis = new Set(equipe.map((e) => e.name));          // toute l'équipe par défaut ; on décoche, ou on n'en garde qu'un
    const dlg = new frappe.ui.Dialog({
      title: "Nouvelle réunion",
      fields: [{ fieldtype: "Date", fieldname: "date", label: "Date", reqd: 1, default: d.prochaine_date || frappe.datetime.get_today() },
               { fieldtype: "Time", fieldname: "heure", label: "Heure", default: c.heure || "09:00:00" },
               { fieldtype: "Column Break" },
               { fieldtype: "Data", fieldname: "titre", label: "Titre", description: "Vide = « Réunion d’équipe · point du … », ou « Point avec <employé> » s’il n’y en a qu’un" },
               { fieldtype: "Data", fieldname: "lieu", label: "Lieu", default: c.lieu || "" },
               { fieldtype: "Section Break", label: "Répétition" },
               { fieldtype: "Select", fieldname: "repete", label: "Répéter", options: RR_REPETE.join("\n"), default: "Non",
                 description: "Une réunion par occurrence, chacune avec son lien visio, de la date ci-dessus jusqu’à la date de fin." },
               { fieldtype: "Column Break" },
               { fieldtype: "Date", fieldname: "jusquau", label: "Jusqu’au", depends_on: "eval:doc.repete && doc.repete !== 'Non'" },
               { fieldtype: "Section Break" },
               { fieldtype: "HTML", fieldname: "parts" },
               { fieldtype: "Section Break", label: "Activités suivies" },
               { fieldtype: "HTML", fieldname: "acts" }],
      primary_action_label: "Créer",
      primary_action: async (v) => {
        if (!choisis.size) return frappe.msgprint("Choisissez au moins un participant.");
        const n = rr_repete_n(v.repete);
        if (n && !v.jusquau) return frappe.msgprint("Indiquez la date de fin de la répétition.");
        const args = { date: v.date, heure: v.heure, titre: v.titre, lieu: v.lieu, employes: Array.from(choisis),
                       activites: Array.from(actsChoisies), toutes_les: n, jusquau: n ? v.jusquau : null };
        const r = await frappe.call({ method: RR_API + ".creer", args });
        dlg.hide(); await this.charger();
        const noms = (r.message || {}).noms || [];
        if (noms.length > 1) frappe.show_alert({ message: `${noms.length} réunions créées (série)`, indicator: "green" }, 5);
        this.ouvrir(r.message.name);
      },
    });
    const actsChoisies = new Set();
    const $a = dlg.fields_dict.acts.$wrapper;
    const peindreActs = () => rr_picker_activites($a.find(".rr-acts"), Array.from(choisis), actsChoisies);
    $a.html(`<div class="small text-muted" style="margin-bottom:4px">Rien de coché = l’ordre du jour parle de <b>toutes</b> les activités des participants. Cochez-en pour que cette réunion (ou la série) n’en suive que certaines.</div><div class="rr-acts"></div>`);
    const $p = dlg.fields_dict.parts.$wrapper;
    $p.html(`<label class="control-label" style="font-size:12px">Participants <span class="text-muted">— cliquez pour cocher / décocher ; un seul employé = point individuel</span></label>
      <div class="sa-chips"></div>
      <div style="margin-top:6px;display:flex;gap:6px"><button type="button" class="btn btn-default btn-xs" data-tous="1">Tous</button><button type="button" class="btn btn-default btn-xs" data-tous="0">Aucun</button></div>
      <div class="small text-muted" style="margin-top:6px">L’ordre du jour ne montrera que les activités des participants cochés.</div>`);
    const peindre = () => sa_chips($p.find(".sa-chips"), equipe, choisis, peindreActs);
    peindre(); peindreActs();
    $p.on("click", "[data-tous]", (e) => { choisis.clear(); if ($(e.currentTarget).attr("data-tous") === "1") equipe.forEach((x) => choisis.add(x.name)); peindre(); peindreActs(); });
    dlg.show();
  }

  async ouvrir(name) {
    let r = (await frappe.call({ method: RR_API + ".get_reunion", args: { name } })).message;
    const pour = new Set();
    const d = new frappe.ui.Dialog({ title: `${r.name}`, size: "extra-large", fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    const $z = d.fields_dict.zone.$wrapper;
    const actualiser_dialogue = (data) => {
      if (!d.is_visible) return;
      const actuelle = [...(data.a_venir || []), ...(data.passees || [])].find((x) => x.name === r.name);
      if (!actuelle || actuelle.statut_affiche === r.statut_affiche) return;
      r.statut_affiche = actuelle.statut_affiche;
      $z.find(".rr-etat-horaire").html(rr_badge(r));
      $z.find(".rr-visio a, .rr-visio code, #rr-copier").toggle(rr_ouverte(r));
      if (r.peut_gerer && r.statut === "Prévue") d.get_primary_btn().text(rr_etat(r) === "Terminée" ? "Valider le compte rendu" : "✅ Clôturer la réunion");
    };
    this.actualiser_dialogue = actualiser_dialogue;
    d.$wrapper.on("hidden.bs.modal", () => { if (this.actualiser_dialogue === actualiser_dialogue) this.actualiser_dialogue = null; });
    const brouillon = { texte: "", date: "", synthese: r.compte_rendu || "" };
    let synthese_enregistree = brouillon.synthese, sauvegarde = Promise.resolve(), minuteur;
    const retenir = () => {
      if ($z.find("#rr-txt").length) brouillon.texte = $z.find("#rr-txt").val();
      if ($z.find("#rr-date").length) brouillon.date = $z.find("#rr-date").val();
      if ($z.find("#rr-synth").length) brouillon.synthese = $z.find("#rr-synth").val();
    };
    const sauver_synthese = () => {
      retenir(); clearTimeout(minuteur);
      sauvegarde = sauvegarde.catch(() => {}).then(async () => {
        if (!r.peut_gerer || brouillon.synthese === synthese_enregistree) return;
        const texte = brouillon.synthese;
        $z.find("#rr-sauvegarde").text("Enregistrement…");
        try {
          await frappe.call({ method: RR_API + ".enregistrer_synthese", args: { name: r.name, texte, precedente: synthese_enregistree } });
          synthese_enregistree = texte;
          $z.find("#rr-sauvegarde").text(brouillon.synthese === texte ? "Synthèse enregistrée" : "Modifications en attente");
        } catch (err) { $z.find("#rr-sauvegarde").text("Synthèse non enregistrée — conservez votre brouillon"); throw err; }
      });
      return sauvegarde;
    };
    const recharger_reunion = async (texte_enregistre) => {
      const synthese_avant = synthese_enregistree;
      const nouvelle = (await frappe.call({ method: RR_API + ".get_reunion", args: { name } })).message;
      retenir(); // Conserve aussi ce qui a été tapé pendant la requête réseau.
      if (texte_enregistre !== undefined && brouillon.texte.trim() === texte_enregistre.trim()) brouillon.texte = "";
      r = nouvelle;
      if (synthese_avant === synthese_enregistree && brouillon.synthese === synthese_enregistree) brouillon.synthese = synthese_enregistree = r.compte_rendu || "";
      peindre(); this.charger(true);
    };
    const appel = async (methode, args) => {
      await sauver_synthese();
      const resultat = await frappe.call({ method: RR_API + "." + methode, args: Object.assign({ name: r.name }, args) });
      await recharger_reunion(methode === "noter" ? args.texte : undefined);
      return resultat.message;
    };

    const peindre = () => {
      const odj = r.ordre_du_jour || { employes: [], kpis: {}, decisions_precedente: [] };
      const k = odj.kpis || {};
      const prevue = r.statut === "Prévue";
      const ligne_act = (a) => `<div class="rr-it" data-act="${sa_esc(a.name)}">${rr_tag(a)}
        <span class="tt">${sa_esc(a.titre)} <em>${a.noms_employes && a.noms_employes.includes(",") ? "· partagée" : ""}${a.priorite === "Urgente" || a.priorite === "Haute" ? " · " + sa_esc(a.priorite.toLowerCase()) : ""}${a.date_prevue && a.statut !== "Terminée" ? " · prévue " + sa_esc(sa_date(a.date_prevue).slice(0, 5)) : ""}${a.etapes && a.etapes[1] ? ` · ${a.etapes[0]}/${a.etapes[1]} étapes` : ""}${a.prochaine ? " · → " + sa_esc(a.prochaine.libelle) : ""}</em></span>
        <span class="ref">${sa_esc(a.name)}</span></div>`;
      const notes = (r.notes || []).map((n) => `<div class="rr-note"><time>${sa_esc(String(n.date || "").slice(11, 16))}</time>
        ${n.activite ? `<div class="dec"><div>${sa_esc(n.texte)}</div><div class="to">Pour ${sa_esc(n.pour || "")} · <span class="ok" data-act="${sa_esc(n.activite)}">✓ activité ${sa_esc(n.activite)}${n.activite_statut ? " · " + sa_esc(n.activite_statut) + (n.activite_avancement ? " " + n.activite_avancement + " %" : "") : ""}</span></div></div>`
          : `<div>${sa_esc(n.texte)} <span class="small text-muted">· ${sa_esc(n.auteur_nom || "")}</span></div>`}
        <span>${r.peut_gerer ? `<button type="button" class="btn btn-default btn-xs" data-formaliser-note="${sa_esc(n.name)}">✨ Formaliser</button> ` : ""}<span class="sup" data-sup-note="${sa_esc(n.name)}" title="Retirer la note">✕</span></span></div>`).join("");
      $z.html(`
        <div class="rr-head">
          <div><div style="font-size:16px;font-weight:700">${sa_esc(r.titre)}</div>
            <div class="text-muted">${sa_esc(sa_jour(r.date))} ${sa_esc(sa_date(r.date))} à ${sa_esc(rr_heure(r.heure))} · ${sa_esc(r.duree_min || 45)} min · ${sa_esc(r.lieu || "")}
              <span class="rr-etat-horaire">${rr_badge(r)}</span></div></div>
          <div class="rr-visio" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            ${r.lien_visio && rr_ouverte(r) ? `<a class="btn btn-sm" href="${sa_esc(r.lien_visio)}" target="_blank" rel="noopener">🎥 Rejoindre la visio</a>
              <code>${sa_esc(r.lien_visio.replace("https://", ""))}</code><button type="button" class="btn btn-default btn-xs" id="rr-copier">Copier</button>` : ""}
            ${r.peut_gerer ? `<button type="button" class="btn btn-default btn-xs" id="rr-modifier">✏️ Modifier</button>` : ""}</div>
        </div>
        ${r.serie || (r.activites_suivies || []).length ? `<div class="small" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center">
          ${r.serie ? `<span>🔁 Série : toutes les ${sa_esc(r.toutes_les_semaines)} sem., ${(r.serie_reunions || []).length} réunion(s) : ${(r.serie_reunions || []).map((x) => `<span class="${x.name === r.name ? "" : "text-muted"}" style="${x.statut === "Annulée" ? "text-decoration:line-through" : ""}">${sa_esc(sa_date(x.date).slice(0, 5))}</span>`).join(" · ")}</span>` : ""}
          ${(r.activites_suivies || []).length ? `<span>${r.seulement_suivies ? "🎯 Ne parle que de :" : "➕ Ajoutées à l’ordre du jour :"} ${r.activites_suivies.map((x) => `<span class="sa-pastille" data-act="${sa_esc(x.activite)}" style="cursor:pointer">${sa_esc(x.titre)}${x.avancement ? " · " + x.avancement + " %" : ""}${x.statut === "Terminée" ? " ✅" : ""}</span>`).join("")}</span>` : ""}
        </div>` : ""}
        <div class="sa-section"><h6>👥 Participants</h6>
          <div>${(r.participants || []).map((p) => `<span class="rr-chip ${p.present === "Présent" ? "on" : p.present === "Absent" ? "off" : ""}" data-emp="${sa_esc(p.employe)}" data-present="${sa_esc(p.present || "")}"><i class="dot"></i>${sa_esc(p.nom)}</span>`).join("")}</div>
          <div class="small text-muted">Un clic : présent → absent → non renseigné.${r.participants && r.participants.some((p) => p.rappel_envoye) ? " Rappel envoyé le " + sa_esc(sa_date(r.participants.find((p) => p.rappel_envoye).rappel_envoye)) + "." : ""}</div></div>
        <div class="sa-section"><h6>📋 Ordre du jour <span class="small text-muted" style="font-weight:normal;text-transform:none">assemblé depuis les activités, depuis le ${sa_esc(sa_date(odj.depuis))}</span>
          ${r.peut_gerer ? `<button type="button" class="btn btn-default btn-xs" id="rr-ajouter-act" style="margin-left:auto">➕ Ajouter une activité</button>` : ""}</h6>
          <div class="rr-kpis"><div class="rr-kpi late"><b>${k.retard || 0}</b><span>en retard</span></div><div class="rr-kpi run"><b>${k.en_cours || 0}</b><span>en cours</span></div><div class="rr-kpi done"><b>${k.terminees || 0}</b><span>terminées depuis</span></div></div>
          ${(odj.decisions_precedente || []).length ? `<div class="rr-emp"><div class="who">Décisions du ${sa_esc(sa_date(r.precedente_date))}<small>${odj.decisions_precedente.length} à relire</small></div><div>
            ${odj.decisions_precedente.map((x) => `<div class="rr-it" data-act="${sa_esc(x.activite)}">${rr_tag({ statut: x.statut, retard_jours: x.en_retard ? frappe.datetime.get_day_diff(frappe.datetime.get_today(), x.date_prevue) : 0, avancement: x.avancement })}<span class="tt">${sa_esc(x.texte)} <em>· ${sa_esc(x.pour || "")}</em></span><span class="ref">${sa_esc(x.activite)}</span></div>`).join("")}</div></div>` : ""}
          ${(odj.employes || []).map((e) => `<div class="rr-emp"><div class="who">${sa_esc(e.nom)}<small>${e.present === "Absent" ? `<span class="abs">absent</span> · ` : ""}${e.nb_retard ? `${e.nb_retard} en retard · ` : ""}${e.nb_en_cours} en cours · ${e.nb_terminees} terminée${e.nb_terminees > 1 ? "s" : ""}</small></div>
            <div>${e.ouvertes.map(ligne_act).join("")}${e.terminees.map(ligne_act).join("")}${!e.ouvertes.length && !e.terminees.length ? `<span class="small text-muted">Rien d’ouvert.</span>` : ""}</div></div>`).join("")}
        </div>
        <div class="sa-section"><h6>📝 Compte-rendu</h6>
          ${notes || `<div class="small text-muted">Aucune note pour l’instant.</div>`}
          ${prevue || r.peut_gerer ? `<div class="rr-write"><textarea class="form-control input-sm" rows="3" id="rr-txt" placeholder="Une note ou une décision de la réunion… Ctrl+Entrée pour ajouter">${sa_esc(brouillon.texte)}</textarea><button type="button" class="btn btn-default btn-sm" id="rr-add">${pour.size ? "Créer une activité" : "Ajouter une note"}</button></div>
          ${r.peut_gerer ? `<div class="rr-write-opts"><span>Décision pour :</span><span class="sa-chips" id="rr-pour"></span><span>prévue le</span><input type="date" class="form-control" id="rr-date"></div>` : ""}` : ""}
          ${r.peut_gerer ? `<button type="button" class="btn btn-default btn-sm" id="rr-formaliser" style="margin-top:8px">✨ Formaliser une décision avec l’IA</button>
            <div style="margin-top:10px"><label class="control-label small">Synthèse (facultative)</label><textarea class="form-control" id="rr-synth" rows="3">${sa_esc(brouillon.synthese)}</textarea><div class="small text-muted" id="rr-sauvegarde">Sauvegarde automatique de la synthèse</div></div>` : (r.compte_rendu ? `<div class="sa-note" style="margin-top:8px"><div class="qui">Synthèse</div><div class="txt">${sa_esc(r.compte_rendu)}</div></div>` : "")}
        </div>`);
      if (r.peut_gerer) sa_chips($z.find("#rr-pour"), r.employes || [], pour, () => $z.find("#rr-add").text(pour.size ? "Créer une activité" : "Ajouter une note"));
      $z.find("#rr-date").val(brouillon.date);
      d.set_primary_action(r.peut_gerer ? (prevue ? (rr_etat(r) === "Terminée" ? "Valider le compte rendu" : "✅ Clôturer la réunion") : "Enregistrer") : "Fermer", async () => {
        if (!r.peut_gerer) return d.hide();
        if (prevue) {
          frappe.confirm("Clôturer la réunion ? Elle passe en « Tenue » ; les décisions restent des activités.", async () => {
            await appel("changer_statut", { statut: "Tenue" }); d.hide(); this.charger();
          });
        } else { await sauver_synthese(); d.hide(); this.charger(); }
      });
      if (r.peut_gerer) {
        d.set_secondary_action_label(prevue ? "🚫 Annuler la réunion" : (r.statut === "Annulée" ? "↩️ Remettre en prévue" : "🗑️ Supprimer"));
        d.set_secondary_action(() => {
          if (prevue && r.serie) {
            const m = new frappe.ui.Dialog({ title: "Annuler", fields: [{ fieldtype: "HTML", fieldname: "h", options: `<p>Cette réunion fait partie d’une série.</p>` }],
              primary_action_label: "Cette réunion seulement", primary_action: async () => { m.hide(); await appel("changer_statut", { statut: "Annulée" }); d.hide(); this.charger(); } });
            m.set_secondary_action_label("Celle-ci et toutes les suivantes");
            m.set_secondary_action(async () => { m.hide(); await frappe.call({ method: RR_API + ".annuler_serie_suivantes", args: { name: r.name } }); d.hide(); this.charger(); });
            m.show(); return;
          }
          if (prevue) frappe.confirm("Annuler cette réunion ?", async () => { await appel("changer_statut", { statut: "Annulée" }); d.hide(); this.charger(); });
          else if (r.statut === "Annulée") appel("changer_statut", { statut: "Prévue" });
          else frappe.confirm(`Supprimer définitivement ${sa_esc(r.name)} ? Les activités créées par ses décisions restent.`, async () => {
            await frappe.call({ method: RR_API + ".supprimer", args: { name: r.name } }); d.hide(); this.charger(); });
        });
      }
    };
    peindre();

    $z.on("click", ".rr-chip", (e) => {
      const $c = $(e.currentTarget), cur = $c.attr("data-present");
      const suivant = cur === "Présent" ? "Absent" : cur === "Absent" ? "" : "Présent";
      appel("presence", { employe: $c.attr("data-emp"), present: suivant });
    });
    $z.on("click", "#rr-copier", () => {
      frappe.utils.copy_to_clipboard(r.lien_visio);
    });
    $z.on("input", "#rr-synth", () => {
      retenir(); clearTimeout(minuteur);
      $z.find("#rr-sauvegarde").text("Modifications en attente…");
      minuteur = setTimeout(() => sauver_synthese().catch(() => {}), 800);
    });
    d.$wrapper.on("hide.bs.modal", (e) => { if (e.target === d.$wrapper[0]) sauver_synthese().catch(() => {}); });
    $z.on("click", "[data-act]", async (e) => {
      e.stopPropagation(); await sauver_synthese();
      this.parent.ouvrir($(e.currentTarget).attr("data-act"), async () => { retenir(); await recharger_reunion(); });
    });
    const formaliser = async (note) => {
      await sauver_synthese();
      const texte = note ? note.texte : brouillon.texte;
      await this.parent.formaliser({ reunion: r.name, note: note && note.name, name: note && note.activite,
        note_liee: !!(note && note.activite), texte, employes: Array.from(pour), date_prevue: brouillon.date,
        on_success: async () => {
          await recharger_reunion(note ? undefined : texte); await this.parent.charger();
        },
      });
    };
    $z.on("click", "#rr-formaliser", () => formaliser());
    $z.on("click", "[data-formaliser-note]", (e) => formaliser((r.notes || []).find((n) => n.name === $(e.currentTarget).attr("data-formaliser-note"))));
    const ajouter = async () => {
      const v = ($z.find("#rr-txt").val() || "").trim();
      if (!v) return;
      const args = { texte: v };
      if (pour.size) { args.employes = Array.from(pour); args.date_prevue = $z.find("#rr-date").val() || null; }
      await appel("noter", args);
      if (args.employes) frappe.show_alert({ message: `Décision confiée à ${args.employes.length} employé(s) : activité créée`, indicator: "green" }, 4);
      this.parent.charger();
    };
    $z.on("click", "#rr-add", ajouter);
    $z.on("keydown", "#rr-txt", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ajouter(); } });
    $z.on("click", "[data-sup-note]", (e) => {
      const ligne = $(e.currentTarget).attr("data-sup-note");
      frappe.confirm("Retirer cette note du compte-rendu ?", () => appel("supprimer_note", { ligne }));
    });
    // ➕ Activité à l'ordre du jour : n'importe quelle activité ouverte (celles des participants d'abord), ou restreindre.
    $z.on("click", "#rr-ajouter-act", () => {
      const actsChoisies = new Set((r.activites_suivies || []).map((x) => x.activite));
      const m = new frappe.ui.Dialog({
        title: "Activités à l’ordre du jour",
        size: "large",
        fields: [{ fieldtype: "Check", fieldname: "seulement", label: "Ne parler que des activités cochées (sinon elles s’ajoutent à celles des participants)", default: r.seulement_suivies ? 1 : 0 },
                 { fieldtype: "HTML", fieldname: "acts" }],
        primary_action_label: "Enregistrer",
        primary_action: async (v) => { await appel("modifier", { data: { activites: Array.from(actsChoisies), seulement_suivies: v.seulement ? 1 : 0 } }); m.hide(); },
      });
      m.fields_dict.acts.$wrapper.html(`<div class="small text-muted" style="margin-bottom:4px">Pour une activité qui n’existe pas encore : écrivez-la dans le compte-rendu avec « Décision pour », elle sera créée et ajoutée.</div><div class="rr-acts"></div>`);
      rr_picker_activites(m.fields_dict.acts.$wrapper.find(".rr-acts"), (r.participants || []).map((p) => p.employe), actsChoisies, true);
      m.show();
    });
    $z.on("click", "#rr-modifier", async () => {
      await sauver_synthese();
      await recharger_reunion();
      const choisis = new Set((r.participants || []).filter((p) => p.actif !== false || !rr_ouverte(r)).map((p) => p.employe));
      const suivantes = "Celle-ci et les suivantes";
      const enSerie = "eval:doc.portee === 'Celle-ci et les suivantes'";
      const m = new frappe.ui.Dialog({
        title: "Modifier la réunion",
        size: "large",
        fields: [
          { fieldtype: "Select", fieldname: "portee", label: "Appliquer les modifications à", options: ["Cette réunion seulement", suivantes], default: "Cette réunion seulement", hidden: !r.peut_modifier_serie },
          { fieldtype: "Int", fieldname: "toutes_les_semaines", label: "Répéter toutes les (semaines)", default: r.toutes_les_semaines || 1, depends_on: enSerie },
          { fieldtype: "HTML", fieldname: "portee_info", depends_on: enSerie,
            options: `<div class="small text-muted" style="margin-bottom:12px">${sa_esc(r.nb_serie_modifiables || 0)} réunion(s) à venir seront modifiées. Le nombre de réunions est conservé. Si la fréquence change, leurs dates seront recalculées à partir de la date ci-dessous. Les réunions passées ou annulées restent inchangées ; chaque réunion garde son compte rendu et son lien visio.</div>` },
          { fieldtype: "Section Break" },
          { fieldtype: "Data", fieldname: "titre", label: "Titre", default: r.titre, reqd: 1 },
          { fieldtype: "Date", fieldname: "date", label: "Date", default: r.date, reqd: 1 },
          { fieldtype: "Time", fieldname: "heure", label: "Heure", default: r.heure },
          { fieldtype: "Column Break" },
          { fieldtype: "Int", fieldname: "duree_min", label: "Durée (min)", default: r.duree_min },
          { fieldtype: "Data", fieldname: "lieu", label: "Lieu", default: r.lieu },
          { fieldtype: "Data", fieldname: "lien_visio", label: "Lien visio", default: r.lien_visio, depends_on: "eval:doc.portee !== 'Celle-ci et les suivantes'" },
          { fieldtype: "Section Break" },
          { fieldtype: "HTML", fieldname: "parts" },
          { fieldtype: "Section Break", label: "Activités à l’ordre du jour" },
          { fieldtype: "Check", fieldname: "seulement_suivies", label: "Ne parler que des activités cochées", default: r.seulement_suivies ? 1 : 0 },
          { fieldtype: "HTML", fieldname: "acts" },
        ],
        primary_action_label: "Enregistrer",
        primary_action: async (v) => {
          if (!choisis.size) return frappe.msgprint("Choisissez au moins un participant actif.");
          const portee = v.portee === suivantes ? "suivantes" : "cette_reunion";
          if (portee === "suivantes" && (!Number.isInteger(v.toutes_les_semaines) || v.toutes_les_semaines < 1 || v.toutes_les_semaines > 52)) return frappe.msgprint("La fréquence doit être un nombre entier de 1 à 52 semaines.");
          const data = { ...v, employes: Array.from(choisis), activites: Array.from(actsChoisies), seulement_suivies: v.seulement_suivies ? 1 : 0 };
          delete data.portee;
          if (portee === "suivantes") delete data.lien_visio;
          const resultat = await appel("modifier", { data, portee, modified: r.modified });
          m.hide();
          frappe.show_alert({ message: `${(resultat.modifiees || []).length || 1} réunion(s) mise(s) à jour`, indicator: "green" });
        },
      });
      const actsChoisies = new Set((r.activites_suivies || []).map((x) => x.activite));
      const $a = m.fields_dict.acts.$wrapper;
      $a.html(`<div class="small text-muted" style="margin-bottom:4px">Rien de coché = toutes les activités des participants. Les activités d’autres employés peuvent être ajoutées.</div><div class="rr-acts"></div>`);
      const peindreActs = () => rr_picker_activites($a.find(".rr-acts"), Array.from(choisis), actsChoisies, true);
      m.fields_dict.parts.$wrapper.html(`<label class="control-label" style="font-size:12px">Participants</label><div class="sa-chips"></div>`);
      const equipe = (r.employes || []).slice();
      (r.participants || []).filter((p) => p.actif !== false).forEach((p) => { if (!equipe.some((e) => e.name === p.employe)) equipe.push({ name: p.employe, employee_name: p.nom }); });
      sa_chips(m.fields_dict.parts.$wrapper.find(".sa-chips"), equipe, choisis, peindreActs);
      peindreActs();
      m.show();
    });
    d.show();
  }
}
