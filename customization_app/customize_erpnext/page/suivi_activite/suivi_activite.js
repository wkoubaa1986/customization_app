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
    r.find(".sa-onglet").on("click", (e) => this.onglet($(e.currentTarget).attr("data-onglet")));
    this.reunions = new SuiviReunions(this);
    this.ouvrir_reunion_demandee();
    r.find("#sa-titre").on("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.ajouter(); } });
    r.find("#sa-envoi").on("click", () => this.ajouter());
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
    else if (a.prochaine && !SA_FERMES.includes(a.statut)) meta.push(`→ ${sa_esc(a.prochaine.libelle)}${a.prochaine.date_prevue ? " · " + sa_esc(sa_jour(a.prochaine.date_prevue) + " " + sa_date(a.prochaine.date_prevue).slice(0, 5)) : ""}`);
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
    const r = await frappe.call({ method: SA_API + ".ameliorer_ia", args: { titre, description, employes, name, date_debut },
                                  freeze: true, freeze_message: "✨ L’IA rédige une proposition…" });
    const p = r.message || {};
    const date_prevue = p.date_prevue || (p.duree_jours ? sa_ajouter_jours_ouvres(date_debut, p.duree_jours) : null);
    const d = new frappe.ui.Dialog({
      title: "✨ Proposition de l’IA",
      size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "zone" }],
      primary_action_label: "Appliquer la sélection",
      primary_action: () => {
        const z = d.fields_dict.zone.$wrapper;
        const etapes = z.find(".ia-e:checked").map((_, el) => {
          const i = Number($(el).attr("data-i"));
          return { libelle: p.etapes[i].libelle, date_prevue: z.find(`.ia-date[data-i="${i}"]`).val() || null };
        }).get();
        // La date prévisionnelle suit la dernière étape gardée, si l'utilisateur a retouché les dates.
        const derniere = etapes.map((e) => e.date_prevue).filter(Boolean).sort().pop();
        appliquer({
          titre: z.find("#ia-t").is(":checked") ? p.titre : null,
          description: z.find("#ia-d").is(":checked") ? p.description : null,
          etapes,
          date_prevue: z.find("#ia-p").is(":checked") ? (derniere || date_prevue) : null,
        });
        d.hide();
      },
    });
    d.fields_dict.zone.$wrapper.html(`<div class="sa-ia">
      ${p.titre ? `<label style="display:block"><input type="checkbox" id="ia-t" checked> <b>Titre :</b> ${sa_esc(p.titre)}</label>` : ""}
      ${p.description ? `<label style="display:block;margin-top:8px"><input type="checkbox" id="ia-d" checked> <b>Description :</b></label>
        <div class="desc">${sa_esc(p.description)}</div>` : ""}
      ${(p.etapes || []).length ? `<div style="margin-top:8px"><b>Étapes proposées</b> <span class="text-muted small">(à partir du ${sa_esc(sa_date(p.date_debut))} ; retouchez les dates si besoin)</span>${p.etapes.map((e, i) => `
        <label class="etp"><input type="checkbox" class="ia-e" data-i="${i}" checked> <span>${sa_esc(e.libelle)}</span>
          <input type="date" class="form-control ia-date" data-i="${i}" value="${sa_esc(e.date_prevue || "")}" title="Prévue le"></label>`).join("")}</div>` : ""}
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
        <div class="sa-statuts">${ctx.statuts.map((s) => `<button type="button" class="btn btn-xs ${s === a.statut ? "btn-primary" : "btn-default"}"
           data-statut="${sa_esc(s)}">${SA_ICONES[s]} ${sa_esc(s)}</button>`).join("")}</div>
        <div class="small text-muted" style="margin-top:4px">Avancement : <b>${a.avancement || 0} %</b>
          ${a.date_fin ? " · terminée le " + sa_esc(sa_date(a.date_fin)) : ""}${a.ia_ameliore ? " · ✨ améliorée par l’IA" : ""}</div>`);

      const etapes = (a.etapes || []).map((e) => {
        const dt = sa_date_etape(e);
        return `
        <div class="sa-etape ${e.fait ? "fait" : ""}">
          <input type="checkbox" data-etape="${sa_esc(e.name)}" ${e.fait ? "checked" : ""}>
          <span class="lib">${sa_esc(e.libelle)}</span>
          <span class="dt ${dt.cls}" data-dater="${sa_esc(e.name)}" data-val="${sa_esc(e.date_prevue || "")}" title="Changer la date">📅 ${sa_esc(dt.txt)}</span>
          ${e.fait && e.fait_le ? `<span class="small text-muted">✓ ${sa_esc(sa_date(e.fait_le))}</span>` : ""}
          <span class="sup" data-sup-etape="${sa_esc(e.name)}" title="Retirer">✕</span></div>`; }).join("");
      const photos = (a.fichiers || []).filter((f) => f.photo);
      const docs = (a.fichiers || []).filter((f) => !f.photo);
      const notes = (a.notes || []).slice().reverse();
      d.fields_dict.zone.$wrapper.html(`
        <div><button type="button" class="btn btn-default btn-sm sa-btn-ia">✨ Améliorer avec l’IA</button></div>
        <div class="sa-section"><h6>☑️ Étapes</h6>${etapes || `<div class="small text-muted">Aucune étape.</div>`}
          <div class="sa-ajout"><input type="text" class="form-control input-sm sa-nouvelle-etape" placeholder="Nouvelle étape puis Entrée…">
            <input type="date" class="form-control input-sm sa-etape-date" title="Prévue le"></div></div>
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
    $z.on("keydown", ".sa-nouvelle-etape", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const v = $(e.currentTarget).val();
      if (v && v.trim()) appel("etape", { action: "ajouter", libelle: v.trim(), date: $z.find(".sa-etape-date").val() || null });
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

// ── Réunions d'équipe : point d'avancement toutes les N semaines ──────────────
const RR_STATUT = { "Prévue": "🗓️ Prévue", "Tenue": "✅ Tenue", "Annulée": "🚫 Annulée" };
const rr_heure = (h) => { const [hh, mm] = String(h || "").split(":"); return hh === undefined || mm === undefined ? "" : hh.padStart(2, "0") + ":" + mm.padStart(2, "0"); };
const rr_quand = (r) => `${sa_jour(r.date)} ${sa_date(r.date)} · ${rr_heure(r.heure)}`;
const rr_tag = (a) => a.statut === "Terminée" ? `<span class="rr-tag done">Terminée ${a.date_fin ? sa_esc(sa_date(a.date_fin).slice(0, 5)) : ""}</span>`
  : a.retard_jours ? `<span class="rr-tag late">Retard ${a.retard_jours} j</span>`
  : a.etapes_retard ? `<span class="rr-tag late">${a.etapes_retard} étape${a.etapes_retard > 1 ? "s" : ""} en retard</span>`
  : a.statut === "En cours" ? `<span class="rr-tag run">En cours ${a.avancement || 0} %</span>`
  : a.statut === "En attente" ? `<span class="rr-tag run">En attente</span>` : `<span class="rr-tag todo">À faire</span>`;

class SuiviReunions {
  constructor(parent) {
    this.parent = parent;
    this.$root = parent.$root;
    this.data = null;
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
        ${x.nb_decisions ? ` · ${x.nb_decisions} décision(s), ${x.nb_decisions_faites} faite(s)` : ""}</small></div>
      <div class="visio">${x.lien_visio && x.statut === "Prévue" ? `<a href="${sa_esc(x.lien_visio)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🎥 Rejoindre</a>` : ""}</div>
      <div><span class="sa-badge ${x.statut === "Tenue" ? "s-terminee" : x.statut === "Annulée" ? "s-annulee" : "s-encours"}">${RR_STATUT[x.statut] || sa_esc(x.statut)}</span></div></div>`;
    const av = d.a_venir || [], pa = d.passees || [];
    r.find("#sa-reu-liste").html(
      `<div class="sa-groupe">À venir — ${av.length}</div>` + (av.length ? av.map((x, i) => ligne(x, i === 0 ? "prochaine" : "")).join("")
        : `<div class="sa-reu-vide">Aucune réunion prévue.${d.peut_gerer ? " Cliquez « Nouvelle réunion », ou activez la création automatique dans le réglage." : ""}</div>`)
      + (pa.length ? `<div class="sa-groupe">Tenues et annulées — ${pa.length}</div>` + pa.map((x) => ligne(x, "tenue")).join("") : ""));
    r.find("#sa-reu-liste .sa-reu").on("click", (e) => this.ouvrir($(e.currentTarget).attr("data-name")));
  }

  nouvelle() {
    const d = this.data || {}, c = d.config || {};
    const dlg = new frappe.ui.Dialog({
      title: "Nouvelle réunion d’équipe",
      fields: [{ fieldtype: "Date", fieldname: "date", label: "Date", reqd: 1, default: d.prochaine_date || frappe.datetime.get_today() },
               { fieldtype: "Time", fieldname: "heure", label: "Heure", default: c.heure || "09:00:00" }],
      primary_action_label: "Créer",
      primary_action: async (v) => {
        const r = await frappe.call({ method: RR_API + ".creer", args: v });
        dlg.hide(); await this.charger(); this.ouvrir(r.message.name);
      },
    });
    dlg.show();
  }

  async ouvrir(name) {
    let r = (await frappe.call({ method: RR_API + ".get_reunion", args: { name } })).message;
    const pour = new Set();
    const d = new frappe.ui.Dialog({ title: `${r.name}`, size: "extra-large", fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    const $z = d.fields_dict.zone.$wrapper;
    const appel = async (methode, args) => {
      await frappe.call({ method: RR_API + "." + methode, args: Object.assign({ name: r.name }, args) });
      r = (await frappe.call({ method: RR_API + ".get_reunion", args: { name } })).message;
      peindre(); this.charger(true);
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
        <span class="sup" data-sup-note="${sa_esc(n.name)}" title="Retirer la note">✕</span></div>`).join("");
      $z.html(`
        <div class="rr-head">
          <div><div style="font-size:16px;font-weight:700">${sa_esc(r.titre)}</div>
            <div class="text-muted">${sa_esc(sa_jour(r.date))} ${sa_esc(sa_date(r.date))} à ${sa_esc(rr_heure(r.heure))} · ${sa_esc(r.duree_min || 45)} min · ${sa_esc(r.lieu || "")}
              <span class="sa-badge ${r.statut === "Tenue" ? "s-terminee" : r.statut === "Annulée" ? "s-annulee" : "s-encours"}">${RR_STATUT[r.statut]}</span></div></div>
          <div class="rr-visio" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            ${r.lien_visio ? `<a class="btn btn-sm" href="${sa_esc(r.lien_visio)}" target="_blank" rel="noopener">🎥 Rejoindre la visio</a>
              <code>${sa_esc(r.lien_visio.replace("https://", ""))}</code><button type="button" class="btn btn-default btn-xs" id="rr-copier">Copier</button>` : ""}
            ${r.peut_gerer ? `<button type="button" class="btn btn-default btn-xs" id="rr-modifier">✏️ Modifier</button>` : ""}</div>
        </div>
        <div class="sa-section"><h6>👥 Participants</h6>
          <div>${(r.participants || []).map((p) => `<span class="rr-chip ${p.present === "Présent" ? "on" : p.present === "Absent" ? "off" : ""}" data-emp="${sa_esc(p.employe)}" data-present="${sa_esc(p.present || "")}"><i class="dot"></i>${sa_esc(p.nom)}</span>`).join("")}</div>
          <div class="small text-muted">Un clic : présent → absent → non renseigné.${r.participants && r.participants.some((p) => p.rappel_envoye) ? " Rappel envoyé le " + sa_esc(sa_date(r.participants.find((p) => p.rappel_envoye).rappel_envoye)) + "." : ""}</div></div>
        <div class="sa-section"><h6>📋 Ordre du jour <span class="small text-muted" style="font-weight:normal;text-transform:none">assemblé depuis les activités, depuis le ${sa_esc(sa_date(odj.depuis))}</span></h6>
          <div class="rr-kpis"><div class="rr-kpi late"><b>${k.retard || 0}</b><span>en retard</span></div><div class="rr-kpi run"><b>${k.en_cours || 0}</b><span>en cours</span></div><div class="rr-kpi done"><b>${k.terminees || 0}</b><span>terminées depuis</span></div></div>
          ${(odj.decisions_precedente || []).length ? `<div class="rr-emp"><div class="who">Décisions du ${sa_esc(sa_date(r.precedente_date))}<small>${odj.decisions_precedente.length} à relire</small></div><div>
            ${odj.decisions_precedente.map((x) => `<div class="rr-it" data-act="${sa_esc(x.activite)}">${rr_tag({ statut: x.statut, retard_jours: x.en_retard ? frappe.datetime.get_day_diff(frappe.datetime.get_today(), x.date_prevue) : 0, avancement: x.avancement })}<span class="tt">${sa_esc(x.texte)} <em>· ${sa_esc(x.pour || "")}</em></span><span class="ref">${sa_esc(x.activite)}</span></div>`).join("")}</div></div>` : ""}
          ${(odj.employes || []).map((e) => `<div class="rr-emp"><div class="who">${sa_esc(e.nom)}<small>${e.present === "Absent" ? `<span class="abs">absent</span> · ` : ""}${e.nb_retard ? `${e.nb_retard} en retard · ` : ""}${e.nb_en_cours} en cours · ${e.nb_terminees} terminée${e.nb_terminees > 1 ? "s" : ""}</small></div>
            <div>${e.ouvertes.map(ligne_act).join("")}${e.terminees.map(ligne_act).join("")}${!e.ouvertes.length && !e.terminees.length ? `<span class="small text-muted">Rien d’ouvert.</span>` : ""}</div></div>`).join("")}
        </div>
        <div class="sa-section"><h6>📝 Compte-rendu</h6>
          ${notes || `<div class="small text-muted">Aucune note pour l’instant.</div>`}
          ${prevue || r.peut_gerer ? `<div class="rr-write"><input type="text" class="form-control input-sm" id="rr-txt" placeholder="Une note… ou une décision : choisissez à qui elle est confiée ci-dessous, puis Entrée"><button type="button" class="btn btn-default btn-sm" id="rr-add">Ajouter</button></div>
          ${r.peut_gerer ? `<div class="rr-write-opts"><span>Décision pour :</span><span class="sa-chips" id="rr-pour"></span><span>prévue le</span><input type="date" class="form-control" id="rr-date"></div>` : ""}` : ""}
          ${r.peut_gerer ? `<div style="margin-top:10px"><label class="control-label small">Synthèse (facultative)</label><textarea class="form-control" id="rr-synth" rows="2">${sa_esc(r.compte_rendu || "")}</textarea></div>` : (r.compte_rendu ? `<div class="sa-note" style="margin-top:8px"><div class="qui">Synthèse</div><div class="txt">${sa_esc(r.compte_rendu)}</div></div>` : "")}
        </div>`);
      if (r.peut_gerer) sa_chips($z.find("#rr-pour"), r.employes || [], pour);
      d.set_primary_action(r.peut_gerer ? (prevue ? "✅ Clôturer la réunion" : "Enregistrer") : "Fermer", async () => {
        if (!r.peut_gerer) return d.hide();
        if (prevue) {
          frappe.confirm("Clôturer la réunion ? Elle passe en « Tenue » ; les décisions restent des activités.", async () => {
            await appel("changer_statut", { statut: "Tenue", compte_rendu: $z.find("#rr-synth").val() }); d.hide(); this.charger();
          });
        } else { await appel("modifier", { data: { compte_rendu: $z.find("#rr-synth").val() } }); d.hide(); this.charger(); }
      });
      if (r.peut_gerer) {
        d.set_secondary_action_label(prevue ? "🚫 Annuler la réunion" : (r.statut === "Annulée" ? "↩️ Remettre en prévue" : "🗑️ Supprimer"));
        d.set_secondary_action(() => {
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
    $z.on("click", "[data-act]", (e) => { e.stopPropagation(); this.parent.ouvrir($(e.currentTarget).attr("data-act")); });
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
    $z.on("keydown", "#rr-txt", (e) => { if (e.key === "Enter") { e.preventDefault(); ajouter(); } });
    $z.on("click", "[data-sup-note]", (e) => {
      const ligne = $(e.currentTarget).attr("data-sup-note");
      frappe.confirm("Retirer cette note du compte-rendu ?", () => appel("supprimer_note", { ligne }));
    });
    $z.on("click", "#rr-modifier", () => {
      const choisis = new Set((r.participants || []).map((p) => p.employe));
      const m = new frappe.ui.Dialog({
        title: "Modifier la réunion",
        fields: [
          { fieldtype: "Data", fieldname: "titre", label: "Titre", default: r.titre, reqd: 1 },
          { fieldtype: "Date", fieldname: "date", label: "Date", default: r.date, reqd: 1 },
          { fieldtype: "Time", fieldname: "heure", label: "Heure", default: r.heure },
          { fieldtype: "Column Break" },
          { fieldtype: "Int", fieldname: "duree_min", label: "Durée (min)", default: r.duree_min },
          { fieldtype: "Data", fieldname: "lieu", label: "Lieu", default: r.lieu },
          { fieldtype: "Data", fieldname: "lien_visio", label: "Lien visio", default: r.lien_visio },
          { fieldtype: "Section Break" },
          { fieldtype: "HTML", fieldname: "parts" },
        ],
        primary_action_label: "Enregistrer",
        primary_action: async (v) => { await appel("modifier", { data: Object.assign(v, { employes: Array.from(choisis) }) }); m.hide(); },
      });
      m.fields_dict.parts.$wrapper.html(`<label class="control-label" style="font-size:12px">Participants</label><div class="sa-chips"></div>`);
      const equipe = (r.employes || []).slice();
      (r.participants || []).forEach((p) => { if (!equipe.some((e) => e.name === p.employe)) equipe.push({ name: p.employe, employee_name: p.nom }); });
      sa_chips(m.fields_dict.parts.$wrapper.find(".sa-chips"), equipe, choisis);
      m.show();
    });
    d.show();
  }
}
