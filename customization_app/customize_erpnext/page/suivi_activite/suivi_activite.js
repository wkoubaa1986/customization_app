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
  if (wrapper.suivi && wrapper.suivi.ctx) wrapper.suivi.charger();
};

const SA_API = "customization_app.suivi_activite";
const SA_ICONES = { "À faire": "📋", "En cours": "🔧", "En attente": "⏸️", "Terminée": "✅", "Annulée": "🚫" };
const SA_CLASSES = { "À faire": "s-afaire", "En cours": "s-encours", "En attente": "s-attente", "Terminée": "s-terminee", "Annulée": "s-annulee" };
const SA_FERMES = ["Terminée", "Annulée"];
const sa_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const sa_date = (d) => (d ? frappe.datetime.str_to_user(String(d).slice(0, 10)) : "");

/** Date + n jours ouvrés (dimanche chômé), pour la date prévisionnelle proposée par l'IA. */
function sa_ajouter_jours_ouvres(depart, n) {
  let d = frappe.datetime.str_to_obj(depart || frappe.datetime.get_today());
  let reste = Math.max(0, n || 0);
  while (reste > 0) {
    d.setDate(d.getDate() + 1);
    if (d.getDay() !== 0) reste -= 1;
  }
  return frappe.datetime.obj_to_str(d).slice(0, 10);
}

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
    r.find("#sa-titre").on("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.ajouter(); } });
    r.find("#sa-envoi").on("click", () => this.ajouter());
    r.find("#sa-employe, #sa-fermees").on("change", () => this.charger());
    let t = null;
    r.find("#sa-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.charger(), 300); });
    this.charger();
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

  // ── IA : proposition à relire, rien n'est enregistré sans accord ──────────────
  async proposer_ia({ titre, description, employes, name, date_debut }, appliquer) {
    const r = await frappe.call({ method: SA_API + ".ameliorer_ia", args: { titre, description, employes, name },
                                  freeze: true, freeze_message: "✨ L’IA rédige une proposition…" });
    const p = r.message || {};
    const date_prevue = p.duree_jours ? sa_ajouter_jours_ouvres(date_debut, p.duree_jours) : null;
    const d = new frappe.ui.Dialog({
      title: "✨ Proposition de l’IA",
      size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "zone" }],
      primary_action_label: "Appliquer la sélection",
      primary_action: () => {
        const z = d.fields_dict.zone.$wrapper;
        appliquer({
          titre: z.find("#ia-t").is(":checked") ? p.titre : null,
          description: z.find("#ia-d").is(":checked") ? p.description : null,
          etapes: z.find(".ia-e:checked").map((_, el) => p.etapes[Number($(el).attr("data-i"))]).get(),
          date_prevue: z.find("#ia-p").is(":checked") ? date_prevue : null,
        });
        d.hide();
      },
    });
    d.fields_dict.zone.$wrapper.html(`<div class="sa-ia">
      ${p.titre ? `<label style="display:block"><input type="checkbox" id="ia-t" checked> <b>Titre :</b> ${sa_esc(p.titre)}</label>` : ""}
      ${p.description ? `<label style="display:block;margin-top:8px"><input type="checkbox" id="ia-d" checked> <b>Description :</b></label>
        <div class="desc">${sa_esc(p.description)}</div>` : ""}
      ${(p.etapes || []).length ? `<div style="margin-top:8px"><b>Étapes proposées :</b>${p.etapes.map((e, i) => `
        <label style="display:block;font-weight:normal"><input type="checkbox" class="ia-e" data-i="${i}" checked> ${sa_esc(e)}</label>`).join("")}</div>` : ""}
      ${date_prevue ? `<label style="display:block;margin-top:8px"><input type="checkbox" id="ia-p" ${appliquer.date_vide ? "checked" : ""}>
        <b>Date prévisionnelle :</b> ${sa_esc(sa_date(date_prevue))} <span class="text-muted">(≈ ${p.duree_jours} j ouvrés)</span></label>` : ""}
    </div>`);
    d.show();
  }

  // ── Fiche détaillée ──────────────────────────────────────────────────────────
  async ouvrir(name) {
    const ctx = this.ctx;
    let a = (await frappe.call({ method: SA_API + ".get_activite", args: { name } })).message;
    const choisis = new Set(a.employes || []);
    // Un employé affecté qui n'est plus dans le réglage reste visible (et retirable) sur sa fiche.
    const equipe = (ctx.employes || []).slice();
    (a.affectations || []).forEach((r) => { if (!equipe.some((e) => e.name === r.employe)) equipe.push({ name: r.employe, employee_name: r.nom }); });
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
      },
    });
    const $pour = d.fields_dict.pour.$wrapper;
    if (ctx.responsable) {
      $pour.html(`<label class="control-label" style="font-size:12px">Employés</label><div class="sa-chips"></div>`);
      sa_chips($pour.find(".sa-chips"), equipe, choisis);
    } else {
      $pour.html(`<label class="control-label" style="font-size:12px">Employés</label>
        <div>${(a.affectations || []).map((r) => `<span class="sa-pastille">${sa_esc(r.nom)}</span>`).join("")}</div>`);
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
    };
    const appel = async (methode, args) => {
      await frappe.call({ method: SA_API + "." + methode, args: Object.assign({ name: a.name }, args) });
      await recharger();
    };

    const peindre = () => {
      d.fields_dict.statut_zone.$wrapper.html(`<label class="control-label" style="font-size:12px">Statut</label>
        <div class="sa-statuts">${ctx.statuts.map((s) => `<button class="btn btn-xs ${s === a.statut ? "btn-primary" : "btn-default"}"
           data-statut="${sa_esc(s)}">${SA_ICONES[s]} ${sa_esc(s)}</button>`).join("")}</div>
        <div class="small text-muted" style="margin-top:4px">Avancement : <b>${a.avancement || 0} %</b>
          ${a.date_fin ? " · terminée le " + sa_esc(sa_date(a.date_fin)) : ""}${a.ia_ameliore ? " · ✨ améliorée par l’IA" : ""}</div>`);

      const etapes = (a.etapes || []).map((e) => `
        <div class="sa-etape ${e.fait ? "fait" : ""}">
          <input type="checkbox" data-etape="${sa_esc(e.name)}" ${e.fait ? "checked" : ""}>
          <span class="lib">${sa_esc(e.libelle)}</span>
          ${e.fait && e.fait_le ? `<span class="small text-muted">${sa_esc(sa_date(e.fait_le))}</span>` : ""}
          <span class="sup" data-sup-etape="${sa_esc(e.name)}" title="Retirer">✕</span></div>`).join("");
      const photos = (a.fichiers || []).filter((f) => f.photo);
      const docs = (a.fichiers || []).filter((f) => !f.photo);
      const notes = (a.notes || []).slice().reverse();
      d.fields_dict.zone.$wrapper.html(`
        <div><button class="btn btn-default btn-sm sa-btn-ia">✨ Améliorer avec l’IA</button></div>
        <div class="sa-section"><h6>☑️ Étapes</h6>${etapes || `<div class="small text-muted">Aucune étape.</div>`}
          <div class="sa-ajout"><input type="text" class="form-control input-sm sa-nouvelle-etape" placeholder="Nouvelle étape puis Entrée…"></div></div>
        <div class="sa-section"><h6>📷 Photos et documents
            <button class="btn btn-default btn-xs sa-btn-fichier">➕ Ajouter</button></h6>
          ${photos.length ? `<div class="sa-galerie">${photos.map((f) => `<div class="ph">
              <a href="${sa_esc(f.fichier)}" target="_blank" title="${sa_esc(f.description || f.nom)}"><img src="${sa_esc(f.fichier)}" loading="lazy"></a>
              <span class="sup" data-sup-fichier="${sa_esc(f.name)}" title="Retirer">✕</span></div>`).join("")}</div>` : ""}
          ${docs.map((f) => `<div class="sa-fic">📄 <a href="${sa_esc(f.fichier)}" target="_blank">${sa_esc(f.description || f.nom)}</a>
              <span class="small text-muted">${sa_esc(sa_date(f.ajoute_le))}</span>
              <span class="sup" data-sup-fichier="${sa_esc(f.name)}" title="Retirer">✕</span></div>`).join("")}
          ${!photos.length && !docs.length ? `<div class="small text-muted">Aucune pièce.</div>` : ""}</div>
        <div class="sa-section"><h6>📝 Notes</h6>
          <div class="sa-ajout"><textarea class="form-control sa-nouvelle-note" rows="2" placeholder="Ce qui a été fait, ce qui bloque…"></textarea>
            <button class="btn btn-default btn-sm sa-btn-note">Noter</button></div>
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
    $z.on("keydown", ".sa-nouvelle-etape", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const v = $(e.currentTarget).val();
      if (v && v.trim()) appel("etape", { action: "ajouter", libelle: v.trim() });
    });
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
    $z.on("click", ".sa-btn-ia", () => {
      const v = d.get_values(true);
      const appliquer = async (choix) => {
        if (choix.titre) d.set_value("titre", choix.titre);
        if (choix.description) d.set_value("description", choix.description);
        if (choix.date_prevue) d.set_value("date_prevue", choix.date_prevue);
        const data = { name: a.name, ia_ameliore: 1, etapes: choix.etapes || [] };
        if (choix.titre) data.titre = choix.titre;
        if (choix.description) data.description = choix.description;
        if (choix.date_prevue) data.date_prevue = choix.date_prevue;
        await frappe.call({ method: SA_API + ".enregistrer", args: { data } });
        frappe.show_alert({ message: "Proposition appliquée", indicator: "green" });
        await recharger();
      };
      appliquer.date_vide = !v.date_prevue;
      this.proposer_ia({ titre: v.titre, description: v.description, employes: Array.from(choisis), name: a.name,
                         date_debut: v.date_debut }, appliquer);
    });
    d.show();
  }
}
