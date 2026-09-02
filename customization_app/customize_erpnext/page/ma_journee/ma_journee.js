/**
 * Écran « Ma journée » — le technicien mène ses interventions au bout.
 *
 * Toute la décision est CÔTÉ SERVEUR (customization_app.planning_employe) :
 * l'écran n'invente ni les droits, ni les règles de clôture, ni la validité
 * d'un bordereau. Il collecte, il affiche, il demande.
 *
 * Les règles de clôture viennent de `cloture_tache.exigences` — les mêmes que
 * la fiche. Ce qui manque est affiché EN CLAIR plutôt que de laisser le bouton
 * « Terminer » échouer sur un message d'erreur.
 */

frappe.pages["ma-journee"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Ma journée",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("ma_journee", {}));
  wrapper.ma_journee = new MaJournee(wrapper);
};

frappe.pages["ma-journee"].on_page_show = function (wrapper) {
  if (wrapper.ma_journee) wrapper.ma_journee.charger();
};

const MJ_ICONES = {
  "Entretien": "🔧", "Réparation": "🧰", "Installation": "🔨",
  "Livraison": "🚚", "Visite": "👋", "Autre": "☕",
};

class MaJournee {
  constructor(wrapper) {
    this.wrapper = wrapper;
    this.data = null;
    $("#mj-jour").val(frappe.datetime.get_today());
    this._bind();
    this.charger();
  }

  _bind() {
    $("#mj-jour, #mj-employe").on("change", () => this.charger());
    $("#mj-refresh").on("click", () => this.charger());
    $("#mj-aujourdhui").on("click", () => {
      $("#mj-jour").val(frappe.datetime.get_today());
      this.charger();
    });
    $("#mj-hier").on("click", () => this._decaler(-1));
    $("#mj-demain").on("click", () => this._decaler(1));

    const w = $(this.wrapper);
    w.on("click", "[data-appel]", (e) => this._appeler(e.currentTarget));
    w.on("click", "[data-photo]", (e) => this._photo(e.currentTarget));
    w.on("click", "[data-aramex]", (e) => this._aramex(e.currentTarget));
    w.on("click", "[data-terminer]", (e) => this._terminer(e.currentTarget));
    w.on("click", "[data-rapport]", (e) => this._rapport(e.currentTarget));
  }

  _decaler(jours) {
    $("#mj-jour").val(frappe.datetime.add_days($("#mj-jour").val()
      || frappe.datetime.get_today(), jours));
    this.charger();
  }

  charger() {
    $("#mj-liste").html(`<div class="mj-vide">${__("Chargement…")}</div>`);
    frappe.call({
      method: "customization_app.planning_employe.ma_journee",
      args: { date: $("#mj-jour").val(), employe: $("#mj-employe").val() || null },
      callback: (r) => {
        this.data = r.message || {};
        this._rendre_selecteur();
        this._rendre();
      },
      error: () => $("#mj-liste").html(
        `<div class="mj-vide">${__("Journée indisponible — rechargez la page.")}</div>`),
    });
  }

  _rendre_selecteur() {
    const m = this.data;
    if (!m.supervise || !(m.employes || []).length) return;
    $("#mj-employe-bloc").show();
    if ($("#mj-employe option").length) return;      // déjà rempli
    $("#mj-employe").html((m.employes || []).map((e) =>
      `<option value="${frappe.utils.escape_html(e.nom)}"${e.nom === m.employe ? " selected" : ""}>${
        frappe.utils.escape_html(e.libelle || e.nom)}</option>`).join(""));
  }

  // ------------------------------------------------------------------ rendu

  _rendre() {
    const esc = frappe.utils.escape_html;
    const m = this.data;
    const lignes = m.lignes || [];
    const restant = lignes.filter((l) => l.statut === "Open").length;
    $("#mj-compte").html(
      `<b>${esc(m.employe_nom || "")}</b> · ${lignes.length} intervention(s), ${restant} à faire`);

    if (!lignes.length) {
      $("#mj-liste").html(`<div class="mj-vide">${__("Aucune intervention ce jour-là.")}</div>`);
      return;
    }
    $("#mj-liste").html(lignes.map((l) => this._carte(l, esc)).join(""));
  }

  _carte(l, esc) {
    const faite = l.statut !== "Open";
    const icone = MJ_ICONES[l.type] || "📌";
    const statut = { "Open": ["b-att", "à faire"], "Completed": ["b-ok", "terminée"],
                     "Cancelled": ["b-ko", "annulée"] }[l.statut] || ["b-gris", l.statut];
    return `<div class="mj-carte ${faite ? "faite" : ""}" data-tache="${esc(l.tache)}">
      <div class="mj-tete">
        <span class="mj-heure">${esc(l.debut)}</span>
        <span class="mj-badge b-inf">${icone} ${esc(l.type || "?")}</span>
        <span class="mj-client">${esc(l.client || "")}</span>
        ${l.secteur ? `<span class="mj-badge b-gris">📍 ${esc(l.secteur)}</span>` : ""}
        <span class="mj-badge ${statut[0]}" style="margin-left:auto">${esc(statut[1])}</span>
      </div>
      <div class="mj-corps">
        ${this._ligne_telephones(l, esc)}
        ${this._ligne_adresse(l, esc)}
        ${this._ligne_articles(l, esc)}
        ${l.note ? `<div class="mj-l"><span class="k">Note</span><span>${esc(l.note)}</span></div>` : ""}
        ${this._ligne_aramex(l, esc)}
        ${this._ligne_cloture(l, esc)}
      </div>
      ${faite ? "" : this._actions(l, esc)}
    </div>`;
  }

  _ligne_telephones(l, esc) {
    if (!(l.telephones || []).length) {
      return `<div class="mj-l"><span class="k">Client</span>
        <span class="mj-manque">${__("aucun numéro au dossier")}</span></div>`;
    }
    // `tel:` ouvre le composeur du téléphone. Le résultat est demandé APRÈS :
    // savoir qu'on a appelé sans savoir si ça a répondu ne sert à rien.
    const liens = (l.telephones || []).map((t) =>
      `<span class="mj-tel"><a href="tel:${esc(t)}" data-appel="${esc(t)}"
         data-tache="${esc(l.tache)}">📞 ${esc(t)}</a></span>`).join("");
    const passes = (l.appels || []).length
      ? `<div class="ct-sub" style="font-size:11px;color:#6b7280">${
           (l.appels || []).slice(0, 3).map((a) =>
             `${esc(a.quand)} — ${esc(a.texte.replace("📞 Appel ", ""))}`).join("<br>")}</div>`
      : "";
    return `<div class="mj-l"><span class="k">Appeler</span><span>${liens}${passes}</span></div>`;
  }

  _ligne_adresse(l, esc) {
    if (!l.adresse && !l.google_map) return "";
    const maps = l.google_map
      ? `<a href="${esc(l.google_map)}" target="_blank" rel="noopener">🗺️ ${__("Ouvrir dans Maps")}</a>`
      // Sans lien enregistré, on en fabrique un depuis l'adresse : mieux vaut
      // une recherche Maps que rien du tout devant une porte cochère.
      : (l.adresse ? `<a href="https://www.google.com/maps/search/?api=1&query=${
            encodeURIComponent(l.adresse)}" target="_blank" rel="noopener">🗺️ ${
            __("Chercher dans Maps")}</a>` : "");
    return `<div class="mj-l"><span class="k">Adresse</span>
      <span>${esc(l.adresse || "")} ${maps}</span></div>`;
  }

  _ligne_articles(l, esc) {
    if (!(l.articles || []).length) return "";
    return `<div class="mj-l"><span class="k">À poser</span><span>${
      (l.articles || []).map((a) =>
        `<div class="mj-art"><b>${a.qte}×</b> ${esc(a.article)}</div>`).join("")}
      ${l.commande ? `<a href="/app/sales-order/${encodeURIComponent(l.commande)}"
         target="_blank" style="font-size:11px">${esc(l.commande)} ↗</a>` : ""}</span></div>`;
  }

  _ligne_aramex(l, esc) {
    if (!l.aramex) return "";
    return `<div class="mj-l"><span class="k">Aramex</span><span>
      ${l.bordereau
        ? `<span class="mj-badge b-ok">📦 ${esc(l.bordereau)}</span>`
        : `<span class="mj-badge b-att">📦 ${__("bordereau non saisi")}</span>`}
      ${l.statut === "Open" ? `<div style="margin-top:5px;display:flex;gap:5px">
          <input type="text" class="form-control" style="width:180px;height:28px;font-size:12px"
                 placeholder="${__("N° de bordereau")}" data-champ-aramex="${esc(l.tache)}"
                 value="${esc(l.bordereau || "")}">
          <button class="btn btn-xs btn-default" data-aramex="${esc(l.tache)}"
            >${__("Vérifier sur la photo")}</button></div>` : ""}
      </span></div>`;
  }

  /** Ce qu'il reste à faire pour pouvoir clôturer — dit AVANT d'essayer. */
  _ligne_cloture(l, esc) {
    const e = l.exigences || {};
    if (l.statut !== "Open") return "";
    const manque = [];
    if (!e.dispense && e.concerne) {
      const min = e.minima || {}, ph = e.photos || {};
      if ((min.avant || 0) > (ph.avant || 0))
        manque.push(__("{0} photo(s) avant", [(min.avant || 0) - (ph.avant || 0)]));
      if ((min.apres || 0) > (ph.apres || 0))
        manque.push(__("{0} photo(s) après", [(min.apres || 0) - (ph.apres || 0)]));
      if (e.gmap_requis && !e.gmap) manque.push(__("position GPS"));
    }
    if (e.commande_requise && !e.commande) manque.push(__("commande liée"));
    if (e.rapport_requis && !e.rapport) manque.push(__("compte rendu"));
    if (!manque.length) {
      return `<div class="mj-l"><span class="k">Clôture</span>
        <span class="mj-badge b-ok">${__("prête")}</span></div>`;
    }
    return `<div class="mj-l"><span class="k">Clôture</span>
      <span class="mj-manque">${__("Il manque")} : ${manque.map(esc).join(" · ")}</span></div>`;
  }

  _actions(l, esc) {
    const e = l.exigences || {};
    return `<div class="mj-actions">
      <button class="btn btn-xs btn-default" data-photo="avant" data-tache="${esc(l.tache)}"
        >📷 ${__("Photo avant")}</button>
      <button class="btn btn-xs btn-default" data-photo="apres" data-tache="${esc(l.tache)}"
        >📷 ${__("Photo après")}</button>
      ${e.rapport_requis ? `<button class="btn btn-xs btn-default"
         data-rapport="${esc(l.tache)}">📝 ${__("Compte rendu")}</button>` : ""}
      <a class="btn btn-xs btn-default" href="/app/tache-de-travail/${
         encodeURIComponent(l.tache)}" target="_blank">${__("Ouvrir la fiche")}</a>
      <button class="btn btn-xs btn-primary" data-terminer="${esc(l.tache)}"
        style="margin-left:auto">✅ ${__("Terminer")}</button>
    </div>`;
  }

  // ---------------------------------------------------------------- actions

  _appeler(el) {
    const tache = $(el).data("tache");
    const numero = String($(el).data("appel"));
    // Le lien `tel:` suit son cours ; on demande le résultat juste après.
    setTimeout(() => {
      const d = new frappe.ui.Dialog({
        title: __("Appel au {0}", [numero]),
        fields: [{ fieldtype: "Select", fieldname: "resultat", reqd: 1,
                   label: __("Résultat"), default: "Répondu",
                   options: (this.data.resultats_appel || []).join("\n") }],
        primary_action_label: __("Enregistrer"),
        primary_action: (v) => frappe.call({
          method: "customization_app.planning_employe.tracer_appel",
          args: { tache, numero, resultat: v.resultat },
          callback: () => { d.hide(); this.charger(); },
        }),
      });
      d.show();
    }, 600);
  }

  _photo(el) {
    const tache = $(el).data("tache");
    const champ = $(el).data("photo") === "avant"
      ? "liste_photos_avant" : "liste_photos_apres";
    new frappe.ui.FileUploader({
      doctype: "Tache de travail", docname: tache, folder: "Home/Attachments",
      allow_multiple: true, restrictions: { allowed_file_types: ["image/*"] },
      on_success: (file) => frappe.call({
        method: "customization_app.cloture_tache.enregistrer_photo",
        args: { tache, champ, file_url: file.file_url },
        callback: () => {
          frappe.show_alert({ message: __("Photo enregistrée"), indicator: "green" }, 3);
          this.charger();
        },
      }),
    });
  }

  _rapport(el) {
    const tache = $(el).data("rapport");
    const ligne = (this.data.lignes || []).find((x) => x.tache === tache) || {};
    const d = new frappe.ui.Dialog({
      title: __("Compte rendu"),
      fields: [{ fieldtype: "Small Text", fieldname: "rapport", reqd: 1,
                 label: __("Ce qui a été fait"), default: ligne.rapport || "" }],
      primary_action_label: __("Enregistrer"),
      primary_action: (v) => frappe.call({
        method: "customization_app.cloture_tache.completer_champs",
        args: { tache, rapport: v.rapport },
        callback: () => { d.hide(); this.charger(); },
      }),
    });
    d.show();
  }

  _aramex(el) {
    const tache = $(el).data("aramex");
    const numero = $(`[data-champ-aramex="${tache}"]`).val();
    frappe.call({
      method: "customization_app.planning_employe.verifier_bordereau",
      args: { tache, numero },
      freeze: true,
      freeze_message: __("Lecture de la photo du bordereau…"),
      callback: (r) => {
        const m = r.message || {};
        if (m.avertissement) {
          frappe.msgprint({ title: __("Bordereau enregistré"), indicator: "orange",
                            message: m.avertissement });
        } else {
          frappe.show_alert({ message: __("Bordereau {0} vérifié sur la photo",
                                          [m.bordereau]), indicator: "green" }, 5);
        }
        this.charger();
      },
    });
  }

  _terminer(el) {
    const tache = $(el).data("terminer");
    const ligne = (this.data.lignes || []).find((x) => x.tache === tache) || {};
    const e = ligne.exigences || {};
    const d = new frappe.ui.Dialog({
      title: __("Terminer l intervention"),
      fields: [
        { fieldtype: "HTML", fieldname: "quoi",
          options: `<div style="padding:9px 11px;border-radius:9px;background:#e0f2fe;
                      color:#075985;font-size:12.5px">${frappe.utils.escape_html(
                        ligne.client || "")} — ${frappe.utils.escape_html(ligne.type || "")}
                      ${e.commande ? `<br>${frappe.utils.escape_html(e.commande)}` : ""}</div>` },
        { fieldtype: "Small Text", fieldname: "rapport_visite",
          label: __("Compte rendu"), default: ligne.rapport || "",
          reqd: e.rapport_requis ? 1 : 0 },
      ],
      primary_action_label: __("Terminer"),
      primary_action: (v) => frappe.call({
        method: "customization_app.planning_employe.cloturer",
        args: { tache, rapport_visite: v.rapport_visite },
        freeze: true,
        callback: () => {
          d.hide();
          frappe.show_alert({ message: __("Intervention terminée"), indicator: "green" }, 4);
          this.charger();
        },
      }),
    });
    d.show();
  }
}
