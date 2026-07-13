frappe.pages["ma-tournee"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Ma Tournée",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("ma_tournee", {}));
  wrapper.ma_tournee = new MaTournee(wrapper);
};

frappe.pages["ma-tournee"].on_page_show = function (wrapper) {
  if (wrapper.ma_tournee) wrapper.ma_tournee.handle_route();
};

// Rendu pur : toute la logique métier (complétude, automatisations, horodatages
// de preuve) vit dans customization_app.tournee. Le front collecte et affiche.
class MaTournee {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".mt-page");
    this.data = null;          // tournée courante (réponse serveur)
    this.refs_cache = {};      // contacts/adresses par client
    this.open_visite = null;   // carte dépliée
    this._bind();
    this.handle_route();
  }

  _bind() {
    this.$root.find("[data-action='add-visite']").on("click", () => this._dialog_add_visite());
    this.$root.find("[data-action='terminer']").on("click", () => this._terminer());
    this.$root.on("click", ".mt-visite-head", (e) => {
      const name = $(e.currentTarget).closest(".mt-visite").attr("data-name");
      this.open_visite = this.open_visite === name ? null : name;
      this.render();
    });
  }

  handle_route() {
    const tache = frappe.route_options && frappe.route_options.tache;
    frappe.route_options = null;
    if (tache) {
      this.load(tache);
    } else if (!this.data) {
      this._show_task_chooser();
    }
  }

  async load(tache) {
    await frappe.model.with_doctype("Visite Commerciale"); // options des Selects
    const r = await frappe.call({
      method: "customization_app.tournee.get_tournee",
      args: { tache },
      freeze: true,
      freeze_message: __("Chargement de la tournée…"),
    });
    this.data = r.message;
    if (this.data.visites.length && !this.open_visite) {
      const encours = this.data.visites.find((v) => !["Réalisée", "Annulée"].includes(v.statut));
      this.open_visite = encours ? encours.name : null;
    }
    this.render();
  }

  _options(fieldname) {
    const df = frappe.meta.get_docfield("Visite Commerciale", fieldname);
    return ((df && df.options) || "").split("\n").filter(Boolean);
  }

  // ---------------------------------------------------------------- rendu
  render() {
    const esc = frappe.utils.escape_html;
    const d = this.data;
    if (!d) return;
    const actives = d.visites.filter((v) => v.statut !== "Annulée");
    const realisees = actives.filter((v) => v.statut === "Réalisée");
    const pct = actives.length ? Math.round((realisees.length / actives.length) * 100) : 0;
    const statut_cls = { "En cours": "st-blue", "Terminée": "st-green", "Annulée": "st-gray" };

    this.$root.find("[data-role='content']").html(`
      <div class="mt-card">
        <div class="mt-head">
          <h2>🧭 Tournée du ${d.date_tournee ? frappe.datetime.str_to_user(d.date_tournee) : "—"}</h2>
          <span class="mt-badge ${statut_cls[d.statut] || "st-gray"}">${esc(d.statut)}</span>
          <div class="meta">
            ${esc(d.commercial_nom || d.commercial || "")} · ${realisees.length}/${actives.length} visite(s) réalisée(s)
            · <a href="/app/tache-de-travail/${encodeURIComponent(d.tache)}">${esc(d.tache)}</a>
          </div>
        </div>
        <div class="mt-progress-track"><div class="mt-progress-fill" style="width:${pct}%"></div></div>
      </div>
      ${d.visites.map((v) => this._visite_card(v)).join("")}
      ${!d.visites.length ? `<div class="mt-empty">Aucune visite pour l'instant.<br>Ajoutez votre première visite ⬇</div>` : ""}
    `);

    const incompletes = actives.filter((v) => v.statut !== "Réalisée");
    this.$root.find("[data-role='footer']").toggle(d.statut !== "Terminée");
    this.$root.find("[data-action='terminer']").prop("disabled", !actives.length || incompletes.length > 0);
    this.$root.find("[data-role='missing']").text(
      !actives.length ? __("Ajoutez au moins une visite.")
        : incompletes.length ? __("{0} visite(s) à clôturer avant de terminer.", [incompletes.length]) : ""
    );

    this._bind_cards();
  }

  _icons(v) {
    return `${v.photo_visite ? "📷" : "◻"}${v.gps_lat && v.gps_lng ? "📍" : "◻"}${v.resume_discussion ? "📝" : "◻"}`;
  }

  _visite_card(v) {
    const esc = frappe.utils.escape_html;
    const open = this.open_visite === v.name;
    const st_cls = { "Réalisée": "st-green", "En cours": "st-blue", "Annulée": "st-gray", "Planifiée": "st-orange" };
    return `
    <div class="mt-card mt-visite ${open ? "open" : ""}" data-name="${esc(v.name)}">
      <button class="mt-visite-head" type="button">
        <span class="mt-caret">▶</span>
        <span class="who">${esc(v.client_nom)}${v.nouveau_prospect ? " 🆕" : ""}</span>
        <span class="icons">${this._icons(v)}</span>
        <span class="mt-badge ${st_cls[v.statut] || "st-gray"}">${esc(v.statut)}</span>
      </button>
      <div class="mt-visite-body">${open ? this._visite_body(v) : ""}</div>
    </div>`;
  }

  _visite_body(v) {
    const esc = frappe.utils.escape_html;
    const done = v.statut === "Réalisée";
    const dis = done ? "disabled" : "";
    const step = (num, title, ok, inner) => `
      <div class="mt-step">
        <div class="mt-step-title"><span class="num">${num}</span> ${title}
          ${ok ? `<span class="done">✔</span>` : ""}</div>
        ${inner}
      </div>`;

    const contact_inner = `
      <div class="mt-chips" data-role="contact-chips">${v.contact ? `<button class="mt-chip selected">${esc(v.contact)}</button>` : `<span class="text-muted" style="font-size:12.5px">Chargement des contacts…</span>`}</div>
      <div class="mt-field"><label>Personne rencontrée</label>
        <input data-field="personne_rencontree" value="${esc(v.personne_rencontree || "")}" ${dis}></div>
      <button class="mt-btn ghost" data-action="add-contact" ${dis}>＋ Ajouter un contact</button>`;

    const adresse_inner = `
      <div class="mt-chips" data-role="adresse-chips"></div>
      <div class="mt-proof" data-role="adresse-maps">
        ${v.lien_google_maps ? `<a href="${esc(v.lien_google_maps)}" target="_blank">🗺 Ouvrir dans Google Maps</a>` : ""}
      </div>
      <button class="mt-btn ghost" data-action="add-adresse" ${dis}>＋ Nouvelle adresse</button>`;

    const gps_inner = `
      <button class="mt-btn primary" data-action="gps" ${dis}>📍 Utiliser ma position actuelle</button>
      <div class="mt-proof">
        ${v.gps_lat && v.gps_lng
          ? `<a href="https://maps.google.com/?q=${v.gps_lat},${v.gps_lng}" target="_blank">📍 ${v.gps_lat}, ${v.gps_lng}</a>
             <span>· ${v.gps_horodatage ? frappe.datetime.str_to_user(v.gps_horodatage) : ""}</span>`
          : `<span>Position non enregistrée</span>`}
      </div>
      <div class="mt-field" style="margin-top:8px"><label>Lien Google Maps * (repris de l'adresse, ou collez-le)</label>
        <input data-field="lien_google_maps" value="${esc(v.lien_google_maps || "")}" ${dis}></div>`;

    const photo_inner = `
      <button class="mt-btn primary" data-action="photo" ${dis}>📷 Prendre une photo</button>
      <input type="file" accept="image/*" capture="environment" data-role="photo-input" style="display:none">
      <div class="mt-proof">
        ${v.photo_visite
          ? `<img src="${esc(v.photo_visite)}" alt="preuve"><span>prise le ${v.photo_horodatage ? frappe.datetime.str_to_user(v.photo_horodatage) : "—"}</span>`
          : `<span>Aucune photo</span>`}
      </div>`;

    const sel = (field, label, current) => `
      <div class="mt-field"><label>${label}</label>
        <select data-field="${field}" ${dis}>
          ${this._options(field).map((o) => `<option value="${esc(o)}" ${o === current ? "selected" : ""}>${esc(o)}</option>`).join("")}
          ${!current ? `<option value="" selected></option>` : ""}
        </select></div>`;

    const cr_inner = `
      <div class="mt-field"><label>Résumé de la discussion *</label>
        <textarea data-field="resume_discussion" ${dis}>${esc(v.resume_discussion || "")}</textarea></div>
      ${done ? "" : `<button class="mt-btn" data-action="ai-resume">✨ Organiser le résumé avec l'IA</button>`}
      <div class="mt-field"><label>Besoin du client</label>
        <input data-field="besoin_client" value="${esc(v.besoin_client || "")}" ${dis}></div>
      <div class="mt-grid-2">
        ${sel("niveau_interet", "Niveau d'intérêt", v.niveau_interet)}
        ${sel("resultat", "Résultat *", v.resultat)}
      </div>
      <div class="mt-grid-2">
        <div class="mt-field"><label>Montant potentiel</label>
          <input type="number" step="0.001" data-field="montant_potentiel" value="${v.montant_potentiel || ""}" ${dis}></div>
        <div class="mt-field"><label>Date de relance</label>
          <input type="date" data-field="date_relance" value="${esc(v.date_relance || "")}" ${dis}></div>
      </div>
      <div class="mt-field"><label>Prochaine action</label>
        <input data-field="prochaine_action" value="${esc(v.prochaine_action || "")}" ${dis}></div>`;

    const actions = done
      ? `<div class="mt-proof" style="justify-content:center">✅ Visite clôturée${v.opportunite ? ` · Opportunité <a href="/app/opportunity/${encodeURIComponent(v.opportunite)}">${esc(v.opportunite)}</a>` : ""}${v.tache_relance ? ` · Relance <a href="/app/tache-de-travail/${encodeURIComponent(v.tache_relance)}">${esc(v.tache_relance)}</a>` : ""}</div>`
      : `<div class="mt-btn-row" style="margin-top:6px">
           <button class="mt-btn" data-action="save">💾 Enregistrer</button>
           <button class="mt-btn success" data-action="cloturer">✔ Clôturer la visite</button>
         </div>
         <button class="mt-btn ghost" data-action="annuler" style="margin-top:2px">Annuler cette visite</button>`;

    return `
      <button class="mt-btn ghost" data-action="fiche-b2b">📤 Envoyer la fiche B2B aux contacts du client</button>
      ${step(1, "Contact rencontré", !!(v.contact || v.personne_rencontree), contact_inner)}
      ${step(2, "Adresse visitée", !!v.adresse, adresse_inner)}
      ${step(3, "Position GPS", !!(v.gps_lat && v.gps_lng), gps_inner)}
      ${step(4, "Photo de la visite", !!v.photo_visite, photo_inner)}
      ${step(5, "Compte rendu", !!(v.resume_discussion && v.resultat), cr_inner)}
      ${actions}`;
  }

  _bind_cards() {
    const $card = this.$root.find(".mt-visite.open");
    if (!$card.length) return;
    const name = $card.attr("data-name");
    const v = this.data.visites.find((x) => x.name === name);
    if (!v) return;

    if (v.statut !== "Réalisée") this._load_refs($card, v);

    $card.find("[data-action='gps']").on("click", () => this._capture_gps(name, $card));
    $card.find("[data-action='photo']").on("click", () => $card.find("[data-role='photo-input']").trigger("click"));
    $card.find("[data-role='photo-input']").on("change", (e) => this._upload_photo(name, $card, e.target.files[0]));
    $card.find("[data-action='save']").on("click", () => this._save(name, $card));
    $card.find("[data-action='cloturer']").on("click", () => this._save(name, $card, { statut: "Réalisée" }));
    $card.find("[data-action='annuler']").on("click", () => {
      frappe.confirm(__("Annuler cette visite ?"), async () => {
        await frappe.call({ method: "customization_app.tournee.annuler_visite", args: { name }, freeze: true });
        this.load(this.data.tache);
      });
    });
    $card.find("[data-action='add-contact']").on("click", () => this._dialog_contact(v));
    $card.find("[data-action='add-adresse']").on("click", () => this._dialog_adresse(v));
    $card.find("[data-action='ai-resume']").on("click", () => this._ai_resume($card));
    $card.find("[data-action='fiche-b2b']").on("click", () => this._envoyer_fiche_b2b(v));
  }

  // ✨ Réorganise le texte du résumé en puces via le backend (OpenAI / AI Settings).
  // Le résultat remplace le texte dans la zone SANS enregistrer : le commercial
  // relit, corrige, puis « Enregistrer ».
  async _ai_resume($card) {
    const $ta = $card.find("textarea[data-field='resume_discussion']");
    const texte = ($ta.val() || "").trim();
    if (texte.length < 10) {
      frappe.msgprint(__("Écrivez d'abord le résumé, même en vrac — l'IA l'organisera."));
      return;
    }
    const r = await frappe.call({
      method: "customization_app.tournee.ameliorer_resume",
      args: { texte },
      freeze: true,
      freeze_message: __("✨ Organisation du résumé…"),
    });
    $ta.val(r.message.resume);
    frappe.show_alert({ message: __("Résumé réorganisé — relisez puis Enregistrer."), indicator: "green" });
  }

  _envoyer_fiche_b2b(v) {
    frappe.confirm(
      __("Envoyer le lien de la fiche B2B de {0} par SMS et email à tous ses contacts ?", [v.client_nom]),
      async () => {
        const r = await frappe.call({
          method: "customization_app.tournee.envoyer_fiche_b2b",
          args: { client: v.client },
          freeze: true,
          freeze_message: __("Envoi en cours…"),
        });
        const res = r.message;
        frappe.msgprint({
          title: __("Fiche B2B envoyée"),
          indicator: res.failed.length ? "orange" : "green",
          message: `<b>${res.sent.length}</b> ${__("envoi(s)")} — ` +
            res.sent.map((s) => `${s.channel === "sms" ? "📱" : "✉️"} ${frappe.utils.escape_html(s.to)}`).join(", ") +
            (res.failed.length ? `<br><b>${res.failed.length}</b> ${__("échec(s)")}` : "") +
            `<br><a href="${frappe.utils.escape_html(res.lien)}" target="_blank">${frappe.utils.escape_html(res.lien)}</a>`,
        });
      }
    );
  }

  async _load_refs($card, v) {
    const esc = frappe.utils.escape_html;
    if (!this.refs_cache[v.client]) {
      const r = await frappe.call({ method: "customization_app.tournee.get_client_refs", args: { client: v.client } });
      this.refs_cache[v.client] = r.message;
    }
    const refs = this.refs_cache[v.client];
    const chip = (val, label, selected, kind) =>
      `<button class="mt-chip ${selected ? "selected" : ""}" data-kind="${kind}" data-val="${esc(val)}">${esc(label)}</button>`;
    $card.find("[data-role='contact-chips']").html(
      refs.contacts.length
        ? refs.contacts.map((c) => chip(c.name, `${c.nom_complet || c.name}${c.designation ? " · " + c.designation : ""}`, v.contact === c.name, "contact")).join("")
        : `<span class="text-muted" style="font-size:12.5px">Aucun contact enregistré</span>`
    );
    $card.find("[data-role='adresse-chips']").html(
      refs.adresses.length
        ? refs.adresses.map((a) => chip(a.name, `${a.address_line1 || a.name}${a.city ? ", " + a.city : ""}${a.google_map ? " 🗺" : ""}`, v.adresse === a.name, "adresse")).join("")
        : `<span class="text-muted" style="font-size:12.5px">Aucune adresse enregistrée</span>`
    );
    $card.find(".mt-chip").on("click", async (e) => {
      const $c = $(e.currentTarget);
      const values = this._collect($card);
      values[$c.attr("data-kind") === "contact" ? "contact" : "adresse"] = $c.attr("data-val");
      await this._push(v.name, values);
    });
  }

  _collect($card) {
    const values = {};
    $card.find("[data-field]").each(function () {
      values[$(this).attr("data-field")] = $(this).val();
    });
    return values;
  }

  async _push(name, values) {
    const r = await frappe.call({
      method: "customization_app.tournee.save_visite",
      args: { name, values: JSON.stringify(values) },
      freeze: true,
    });
    const idx = this.data.visites.findIndex((x) => x.name === name);
    this.data.visites[idx] = r.message;
    this.render();
    return r.message;
  }

  async _save(name, $card, extra = {}) {
    const values = Object.assign(this._collect($card), extra);
    const visite = await this._push(name, values);
    if (extra.statut === "Réalisée") {
      frappe.show_alert({ message: __("Visite clôturée ✔"), indicator: "green" });
      this.open_visite = null;
      this.load(this.data.tache);
    }
    return visite;
  }

  _capture_gps(name, $card) {
    if (!navigator.geolocation) {
      frappe.msgprint(__("La géolocalisation n'est pas disponible sur cet appareil."));
      return;
    }
    frappe.dom.freeze(__("Récupération de la position…"));
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        frappe.dom.unfreeze();
        const values = this._collect($card);
        values.gps_lat = pos.coords.latitude.toFixed(6);
        values.gps_lng = pos.coords.longitude.toFixed(6);
        values.lien_google_maps = "";
        await this._push(name, values);
        frappe.show_alert({ message: __("Position enregistrée 📍"), indicator: "green" });
      },
      (err) => {
        frappe.dom.unfreeze();
        frappe.msgprint(__("Position indisponible : {0}. Vérifiez l'autorisation GPS du navigateur.", [err.message]));
      },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  }

  async _upload_photo(name, $card, file) {
    if (!file) return;
    await this._push(name, this._collect($card)); // ne pas perdre la saisie en cours
    frappe.dom.freeze(__("Envoi de la photo…"));
    try {
      const fd = new FormData();
      fd.append("file", file, file.name || "visite.jpg");
      fd.append("is_private", "1");
      fd.append("doctype", "Visite Commerciale");
      fd.append("docname", name);
      const res = await fetch("/api/method/upload_file", {
        method: "POST",
        headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
        body: fd,
      });
      const out = await res.json();
      if (!res.ok || !out.message) throw new Error(out._server_messages || res.statusText);
      const r = await frappe.call({
        method: "customization_app.tournee.set_photo",
        args: { name, file_url: out.message.file_url },
      });
      const idx = this.data.visites.findIndex((x) => x.name === name);
      this.data.visites[idx] = r.message;
      this.render();
      frappe.show_alert({ message: __("Photo enregistrée 📷"), indicator: "green" });
    } catch (e) {
      frappe.msgprint(__("Échec de l'envoi de la photo : {0}", [e.message || e]));
    } finally {
      frappe.dom.unfreeze();
    }
  }

  // ---------------------------------------------------------------- dialogs
  _dialog_add_visite() {
    const d = new frappe.ui.Dialog({
      title: __("Ajouter une visite"),
      fields: [
        { fieldname: "client", label: __("Client existant"), fieldtype: "Link", options: "Customer" },
        { fieldname: "hint", fieldtype: "HTML",
          options: `<div class="text-muted" style="font-size:12.5px;margin-top:4px">${__("Ou créez un nouveau prospect ⬇")}</div>` },
      ],
      primary_action_label: __("Ajouter"),
      primary_action: async (values) => {
        if (!values.client) return frappe.msgprint(__("Choisissez un client ou créez un prospect."));
        d.hide();
        await this._add_visite(values.client, 0);
      },
      secondary_action_label: __("🆕 Nouveau client / prospect"),
      secondary_action: () => { d.hide(); this._quick_entry_prospect(); },
    });
    d.show();
  }

  // Quick Entry standard de Customer (utilise le CustomerQuickEntryForm
  // personnalisé de l'app) ; la visite est ajoutée après la création.
  _quick_entry_prospect() {
    frappe.ui.form.make_quick_entry("Customer", (doc) => {
      frappe.show_alert({ message: __("Client {0} créé", [doc.customer_name || doc.name]), indicator: "green" });
      this._add_visite(doc.name, 1);
    });
  }

  async _add_visite(client, nouveau_prospect) {
    const r = await frappe.call({
      method: "customization_app.tournee.add_visite",
      args: { tournee: this.data.name, client, nouveau_prospect },
      freeze: true,
    });
    this.open_visite = r.message.name;
    this.load(this.data.tache);
  }

  _dialog_contact(v) {
    const d = new frappe.ui.Dialog({
      title: __("Nouveau contact — {0}", [v.client_nom]),
      fields: [
        { fieldname: "nom", label: __("Nom"), fieldtype: "Data", reqd: 1 },
        { fieldname: "fonction", label: __("Fonction"), fieldtype: "Data" },
        { fieldname: "telephone", label: __("Téléphone"), fieldtype: "Data" },
        { fieldname: "email", label: __("E-mail"), fieldtype: "Data", options: "Email" },
        { fieldname: "photo", label: __("Photo du contact"), fieldtype: "Attach Image" },
      ],
      primary_action_label: __("Créer"),
      primary_action: async (values) => {
        d.hide();
        const r = await frappe.call({
          method: "customization_app.tournee.quick_create_contact",
          args: Object.assign({ client: v.client, visite: v.name }, values),
          freeze: true,
        });
        delete this.refs_cache[v.client];
        const card = this.$root.find(`.mt-visite[data-name="${v.name}"]`);
        const vals = this._collect(card);
        vals.contact = r.message.name;
        if (!vals.personne_rencontree) vals.personne_rencontree = values.nom;
        await this._push(v.name, vals);
      },
    });
    d.show();
  }

  _dialog_adresse(v) {
    const d = new frappe.ui.Dialog({
      title: __("Nouvelle adresse — {0}", [v.client_nom]),
      fields: [
        { fieldname: "adresse", label: __("Adresse"), fieldtype: "Data", reqd: 1 },
        { fieldname: "gouvernorat", label: __("Gouvernorat"), fieldtype: "Data" },
        { fieldname: "ville", label: __("Ville / Délégation"), fieldtype: "Data" },
        { fieldname: "lien_google_maps", label: __("Lien Google Maps"), fieldtype: "Data" },
      ],
      primary_action_label: __("Créer"),
      primary_action: async (values) => {
        d.hide();
        const r = await frappe.call({
          method: "customization_app.tournee.quick_create_adresse",
          args: Object.assign({ client: v.client }, values),
          freeze: true,
        });
        delete this.refs_cache[v.client];
        const card = this.$root.find(`.mt-visite[data-name="${v.name}"]`);
        const vals = this._collect(card);
        vals.adresse = r.message.name;
        await this._push(v.name, vals);
      },
    });
    d.show();
  }

  async _terminer() {
    frappe.confirm(__("Terminer la tournée et clôturer la tâche de travail ?"), async () => {
      try {
        await frappe.call({
          method: "customization_app.tournee.terminer_tournee",
          args: { tournee: this.data.name },
          freeze: true,
        });
        frappe.show_alert({ message: __("Tournée terminée 🏁"), indicator: "green" });
        this.load(this.data.tache);
      } catch (e) { /* message d'erreur serveur déjà affiché */ }
    });
  }

  // ---------------------------------------------------------------- choix de tâche
  async _show_task_chooser() {
    const esc = frappe.utils.escape_html;
    const taches = await frappe.db.get_list("Tache de travail", {
      filters: { custom_type_dintervention: "Tournée commerciale" },
      fields: ["name", "starts_on", "status", "custom_employé", "subject"],
      order_by: "starts_on desc",
      limit: 20,
    });
    this.$root.find("[data-role='footer']").hide();
    this.$root.find("[data-role='content']").html(`
      <div class="mt-card">
        <div class="mt-head"><h2>🧭 Mes tournées</h2>
        <div class="meta">Choisissez une tournée, ou créez une Tâche de travail de type « Tournée commerciale ».</div></div>
      </div>
      <div class="mt-select-list">
        ${taches.length ? taches.map((t) => `
          <button class="mt-btn" data-tache="${esc(t.name)}">
            <span>${t.starts_on ? frappe.datetime.str_to_user(t.starts_on.split(" ")[0]) : "—"} · ${esc(t.custom_employé || "")}</span>
            <span class="mt-badge ${t.status === "Completed" ? "st-green" : t.status === "Open" ? "st-blue" : "st-gray"}">${esc(t.status)}</span>
          </button>`).join("")
        : `<div class="mt-empty">Aucune tâche « Tournée commerciale » trouvée.</div>`}
      </div>`);
    this.$root.find("[data-tache]").on("click", (e) => this.load($(e.currentTarget).attr("data-tache")));
  }
}
