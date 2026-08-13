frappe.pages["retenue-source-vente"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Retenue à la source — Ventes",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("retenue_source_vente", {})
  );
  new RetenueSourceVente(wrapper);
};

// Rendu pur : montants, taux et agrégats viennent du backend
// customization_app.retenue_source.get_data.

const RSV_DOCTYPE_LABELS = {
  "Sales Invoice": "Facture de vente",
  "Payment Entry": "Écriture de paiement",
  Customer: "Client",
};

const RSV_PREVIEW_FIELDS = {
  "Sales Invoice": [
    ["status", "Statut", "badge"],
    ["customer_name", "Client"],
    ["posting_date", "Date", "date"],
    ["due_date", "Échéance", "date"],
    ["grand_total", "Total TTC", "currency"],
    ["outstanding_amount", "Restant dû", "currency"],
  ],
  "Payment Entry": [
    ["status", "Statut", "badge"],
    ["party_name", "Client"],
    ["posting_date", "Date", "date"],
    ["mode_of_payment", "Mode de paiement"],
    ["paid_amount", "Montant", "currency"],
    ["paid_to", "Compte encaissé"],
    ["reference_no", "Référence"],
    ["reference_date", "Date référence", "date"],
  ],
  Customer: [
    ["customer_name", "Nom"],
    ["tax_id", "Matricule fiscale"],
    ["mobile_no", "Mobile"],
    ["email_id", "E-mail"],
    ["customer_group", "Groupe"],
  ],
};

const RSV_SI_STATUS_CLASS = {
  Paid: "st-green",
  "Partly Paid": "st-orange",
  Unpaid: "st-red",
  Overdue: "st-red",
  Return: "st-gray",
  "Credit Note Issued": "st-gray",
  Cancelled: "st-gray",
};

const RSV_IMG_EXT = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"];

class RetenueSourceVente {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".rsv-page");
    this._data = null;
    this._init_defaults();
    this._bind();
    this._fetch();
  }

  _init_defaults() {
    // Par défaut : l'année civile en cours complète.
    const year = frappe.datetime.get_today().slice(0, 4);
    this.$root.find("#rsv-from").val(`${year}-01-01`);
    this.$root.find("#rsv-to").val(`${year}-12-31`);
  }

  _dates() {
    return {
      from_date: this.$root.find("#rsv-from").val() || null,
      to_date: this.$root.find("#rsv-to").val() || null,
    };
  }

  _switch_tab(tab) {
    this.$root.find(".rsv-tab").removeClass("active");
    this.$root.find(`.rsv-tab[data-tab="${tab}"]`).addClass("active");
    this.$root.find("[data-pane]").hide();
    const $pane = this.$root.find(`[data-pane="${tab}"]`).show();
    // La barre de relance ne concerne que les factures : l'afficher au-dessus des certificats
    // laisserait croire qu'elle relance les declarants.
    this.$root.find("[data-action='relance'], [data-action='toggle-all'], [data-action='excel']")
      .toggle(tab === "factures");
    if (tab !== "certificats") return;
    if (this._certs) return this._certs.refresh();
    // ⚠️ PAS `frappe.require` AVEC UN « ?v= ». Son `assets.extn()` prend ce qui suit le « ? » :
    // « …certificats_ras.js?v=123 » lui donne l'extension « v=123 », `_handlers[...]` vaut alors
    // undefined et l'exception tue le callback — la classe n'est jamais définie et l'onglet reste
    // figé, sans message. Constaté en réel.
    //
    // `$.getScript` ajoute lui-même un paramètre anti-cache : ce fichier est servi par symlink,
    // sans passer par `bench build`, donc sans version dans l'URL — le navigateur gardait sa copie
    // et l'onglet affichait un ancien écran des heures après la correction.
    $.getScript("/assets/bank_retenue_sync/js/certificats_ras.js")
      .done(() => {
        this._certs = new window.CertificatsRAS($pane);
      })
      .fail(() => {
        // Un onglet qui ne se charge pas doit le DIRE : c'est le silence qui a coûté le plus cher.
        $pane.html(
          `<div class="rsv-inv" style="padding:18px;text-align:center;color:var(--rsv-loss)">${__(
            "Impossible de charger l'onglet Certificats TEJ. Rechargez la page (Ctrl+Maj+R)."
          )}</div>`
        );
      });
  }

  _bind() {
    this.$root.find("[data-action='refresh']").on("click", () => this._fetch());
    this.$root.find("#rsv-from, #rsv-to").on("change", () => this._fetch());
    this.$root.find("[data-action='print']").on("click", () => window.print());
    this.$root.find("[data-action='excel']").on("click", () => {
      const d = this._dates();
      window.open(
        `/api/method/customization_app.retenue_source.download_excel?from_date=${encodeURIComponent(d.from_date || "")}&to_date=${encodeURIComponent(d.to_date || "")}`
      );
    });
    this.$root.find("[data-action='toggle-all']").on("click", (e) => {
      const $invs = this.$root.find(".rsv-inv");
      const any_closed = $invs.not(".open").length > 0;
      $invs.toggleClass("open", any_closed);
      $(e.currentTarget).text(any_closed ? "Tout replier" : "Tout déplier");
    });
    this.$root.on("click", ".rsv-inv-head", (e) => {
      if ($(e.target).closest("a").length) return;
      $(e.currentTarget).closest(".rsv-inv").toggleClass("open");
    });
    this.$root.on("click", "a[data-preview]", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const $el = $(e.currentTarget);
      this._preview($el.attr("data-doctype"), $el.attr("data-name"));
    });
    this.$root.on("click", ".rsv-att", (e) => {
      const $el = $(e.currentTarget);
      this._open_attachment($el.attr("data-url"), $el.attr("data-fname"), $el.attr("data-ext"));
    });
    this.$root.find("[data-action='relance']").on("click", () => this._open_relance_dialog(null));
    // Onglet « Certificats TEJ » : servi par bank_retenue_sync, charge a la premiere ouverture
    // seulement — il interroge le portail, inutile de le payer quand on vient lire les factures.
    this.$root.on("click", ".rsv-tab", (e) => this._switch_tab($(e.currentTarget).data("tab")));
    this.$root.on("click", ".rsv-relance-one", (e) =>
      this._open_relance_dialog([$(e.currentTarget).attr("data-customer")]));
    this.$root.on("click", ".rsv-tel", (e) =>
      this._relance_telephone($(e.currentTarget).attr("data-customer"),
                              $(e.currentTarget).attr("data-customer-name")));
    this.$root.on("click", ".rsv-histo", (e) =>
      this._show_historique($(e.currentTarget).attr("data-customer"),
                            $(e.currentTarget).attr("data-customer-name")));
  }

  // ---------------------------------------------------------------
  //  Relances certificats RAS (même mécanique que Relance Paiements)
  // ---------------------------------------------------------------
  _open_relance_dialog(customers) {
    const args = this._dates();
    if (customers) args.customers = JSON.stringify(customers);
    frappe.call({
      method: "customization_app.retenue_source.preparer_relances",
      args,
      freeze: true,
      freeze_message: __("Préparation des messages…"),
      callback: (r) => {
        const msgs = r.message || [];
        if (!msgs.length) {
          frappe.msgprint(__("Aucun certificat manquant à relancer sur la période."));
          return;
        }
        this._show_relance_dialog(msgs);
      },
    });
  }

  _show_relance_dialog(msgs) {
    const esc = frappe.utils.escape_html;
    const d = new frappe.ui.Dialog({
      title: __("Relance certificats — {0} client(s)", [msgs.length]),
      size: "large",
      fields: [
        {
          fieldname: "channel",
          label: __("Canal"),
          fieldtype: "Select",
          options: [
            { value: "sms", label: __("SMS") },
            { value: "email", label: __("Email") },
            { value: "both", label: __("SMS + Email") },
          ],
          default: "both",
          reqd: 1,
        },
        { fieldname: "cards", fieldtype: "HTML" },
      ],
      primary_action_label: __("Envoyer"),
      primary_action: () => this._send_relance(d, msgs),
    });

    const $wrap = d.fields_dict.cards.$wrapper;
    const contactBadge = (m, channel) => {
      const sms = m.telephone
        ? `📱 ${esc(m.telephone)}`
        : `<span style="color:var(--rsv-loss,#dc2626)">📱 téléphone manquant</span>`;
      const email = m.email
        ? `✉️ ${esc(m.email)}`
        : `<span style="color:var(--rsv-loss,#dc2626)">✉️ email manquant</span>`;
      if (channel === "sms") return sms;
      if (channel === "email") return email;
      return `${sms} &nbsp; ${email}`;
    };
    const renderCards = () => {
      const channel = d.get_value("channel");
      $wrap.html(msgs.map((m, i) => `
        <div class="rsv-msg-card">
          <div class="hdr">
            <span>${esc(m.customer_name)}</span>
            <span>${m.invoices.length} facture(s) · ${this._fmt(m.total_ras)}</span>
          </div>
          <textarea rows="4" data-i="${i}">${esc(m.message)}</textarea>
          <div class="meta">${contactBadge(m, channel)}</div>
        </div>`).join(""));
    };
    renderCards();
    d.fields_dict.channel.$input.on("change", renderCards);
    d.show();
  }

  _send_relance(d, msgs) {
    const channel = d.get_value("channel");
    const esc = frappe.utils.escape_html;
    const payload = msgs.map((m, i) => {
      const $ta = d.fields_dict.cards.$wrapper.find(`textarea[data-i="${i}"]`);
      return {
        customer: m.customer,
        telephone: m.telephone,
        email: m.email,
        invoices: m.invoices,
        message: $ta.length ? $ta.val() : m.message,
      };
    });

    const missing = payload.filter((p) => {
      if (channel === "sms") return !p.telephone;
      if (channel === "email") return !p.email;
      return !p.telephone && !p.email;
    });
    const proceed = () => {
      frappe.call({
        method: "customization_app.retenue_source.envoyer_relance",
        args: { payload: JSON.stringify(payload), channel },
        freeze: true,
        freeze_message: __("Envoi en cours…"),
        callback: (r) => {
          const res = r.message || { sent: [], failed: [] };
          d.hide();
          frappe.msgprint({
            title: __("Résultat de la relance"),
            indicator: res.failed.length ? "orange" : "green",
            message:
              `<b>${res.sent.length}</b> ${__("envoyé(s)")}<br>` +
              (res.failed.length
                ? `<b>${res.failed.length}</b> ${__("échec(s)")}:<br>` +
                  res.failed.map((f) => `• ${esc(f.customer)} — ${esc(f.reason)}`).join("<br>")
                : ""),
          });
        },
      });
    };

    if (missing.length) {
      frappe.confirm(
        __("{0} client(s) sans coordonnée pour ce canal seront ignorés. Continuer ?", [missing.length]),
        proceed
      );
    } else {
      proceed();
    }
  }

  _relance_telephone(customer, customer_name) {
    const d = new frappe.ui.Dialog({
      title: __("Relance téléphonique — {0}", [customer_name || customer]),
      fields: [
        { fieldname: "commentaire", label: __("Commentaire"), fieldtype: "Small Text",
          description: __("Ex : client appelé, enverra le certificat cette semaine.") },
      ],
      primary_action_label: __("Enregistrer"),
      primary_action: (values) => {
        frappe.call({
          method: "customization_app.retenue_source.enregistrer_relance_telephone",
          args: { customer, commentaire: values.commentaire || "" },
          freeze: true,
          callback: () => {
            d.hide();
            frappe.show_alert({ message: __("Relance téléphonique enregistrée."), indicator: "green" });
          },
        });
      },
    });
    d.show();
  }

  _show_historique(customer, customer_name) {
    const esc = frappe.utils.escape_html;
    frappe.call({
      method: "customization_app.retenue_source.get_historique_relances",
      args: { customer },
      freeze: true,
      callback: (r) => {
        const rows = r.message || [];
        const d = new frappe.ui.Dialog({
          title: __("Historique des relances — {0}", [customer_name || customer]),
        });
        d.$body.html(rows.length ? `
          <div class="rsv-histo">
            ${rows.map((h) => `
              <div class="rsv-histo-item">
                <span class="rsv-badge st-neutral">${esc(h.type)}</span>
                <span class="when">${esc(h.date)}</span>
                <span class="who">${esc(h.user)}</span>
                ${h.note ? `<div class="note">${esc(h.note)}</div>` : ""}
              </div>`).join("")}
          </div>` : `<div class="rsv-empty">${__("Aucune relance enregistrée pour ce client.")}</div>`);
        d.show();
      },
    });
  }

  async _fetch() {
    const r = await frappe.call({
      method: "customization_app.retenue_source.get_data",
      args: this._dates(),
      freeze: true,
      freeze_message: __("Chargement…"),
    });
    this._data = r.message;
    this._render();
  }

  _fmt(v) {
    return format_currency(v || 0, this._data.currency);
  }

  _doclink(doctype, name, text, cls = "") {
    const esc = frappe.utils.escape_html;
    if (!name) return "";
    const route = `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
    return `<a class="${cls}" href="${route}" data-preview data-doctype="${esc(doctype)}" data-name="${esc(name)}">${esc(text || name)}</a>`;
  }

  // ---- pièces jointes : visionneuse en pop-up (PDF / images), sinon onglet
  _open_attachment(url, fname, ext) {
    const esc = frappe.utils.escape_html;
    let body = null;
    if (ext === "pdf") {
      body = `<iframe class="rsv-file-frame" src="${esc(url)}" title="${esc(fname)}"></iframe>`;
    } else if (RSV_IMG_EXT.includes(ext)) {
      body = `<img class="rsv-file-img" src="${esc(url)}" alt="${esc(fname)}">`;
    }
    if (!body) {
      window.open(url); // format non prévisualisable → onglet / téléchargement
      return;
    }
    const dialog = new frappe.ui.Dialog({
      title: fname,
      size: "extra-large",
      secondary_action_label: __("Ouvrir dans un onglet"),
      secondary_action: () => window.open(url),
    });
    dialog.$body.html(body);
    dialog.$wrapper.find(".modal-dialog").css({ "max-width": "94vw", width: "94vw" });
    dialog.show();
  }

  async _preview(doctype, name) {
    let doc;
    try {
      doc = await frappe.db.get_doc(doctype, name);
    } catch (e) {
      frappe.msgprint(__("Impossible de charger {0} {1} (droits insuffisants ?)", [doctype, name]));
      return;
    }
    const esc = frappe.utils.escape_html;
    const rows = (RSV_PREVIEW_FIELDS[doctype] || [])
      .filter(([field]) => doc[field] !== undefined && doc[field] !== null && doc[field] !== "")
      .map(([field, label, type]) => {
        let val = doc[field];
        if (type === "currency") val = this._fmt(val);
        else if (type === "date" || type === "datetime") val = frappe.datetime.str_to_user(val);
        else val = esc(String(val));
        if (type === "badge") val = `<span class="rsv-badge st-neutral">${val}</span>`;
        return `<tr><td class="rsv-pv-label">${label}</td><td>${val}</td></tr>`;
      })
      .join("");

    const dialog = new frappe.ui.Dialog({
      title: `${RSV_DOCTYPE_LABELS[doctype] || doctype} — ${name}`,
      primary_action_label: __("Ouvrir la fiche complète"),
      primary_action: () => {
        dialog.hide();
        this._open_form_popup(doctype, name);
      },
      secondary_action_label: __("Ouvrir dans un onglet"),
      secondary_action: () => {
        window.open(`/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`);
      },
    });
    dialog.$body.html(`<table class="rsv-pv-table">${rows || `<tr><td>${__("Aucun détail disponible")}</td></tr>`}</table>`);
    dialog.show();
  }

  _open_form_popup(doctype, name) {
    const route = `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
    const dialog = new frappe.ui.Dialog({
      title: `${RSV_DOCTYPE_LABELS[doctype] || doctype} — ${name}`,
      size: "extra-large",
      secondary_action_label: __("Ouvrir dans un onglet"),
      secondary_action: () => window.open(route),
    });
    dialog.$body.html(
      `<iframe class="rsv-form-frame" src="${route}" title="${frappe.utils.escape_html(name)}"></iframe>`
    );
    dialog.$wrapper.find(".modal-dialog").css({ "max-width": "94vw", width: "94vw" });
    const frame = dialog.$body.find("iframe")[0];
    frame.addEventListener("load", () => {
      try {
        const idoc = frame.contentDocument;
        idoc.documentElement.style.setProperty("--navbar-height", "0px");
        const style = idoc.createElement("style");
        style.textContent = ".navbar { display: none !important; }";
        idoc.head.appendChild(style);
      } catch (e) { /* même origine attendue */ }
    });
    dialog.show();
  }

  _render() {
    const d = this._data;
    const k = d.kpis;
    const kpi = (lbl, val, cls = "") =>
      `<div class="rsv-kpi ${cls}"><div class="lbl">${lbl}</div><div class="val">${val}</div></div>`;
    this.$root.find("[data-role='kpis']").html([
      kpi("Factures", k.invoice_count),
      kpi("Total facturé TTC", this._fmt(k.total_invoiced)),
      kpi("Total retenue", this._fmt(k.total_ras)),
      kpi("Taux moyen", `${k.avg_rate} %`),
      kpi("Factures sans justificatif", k.missing_proof, k.missing_proof > 0 ? "warn" : ""),
      kpi("Retenue sans justificatif", this._fmt(k.missing_proof_amount), k.missing_proof_amount > 0.001 ? "warn" : ""),
    ].join(""));

    this.$root.find("[data-role='invoices']").html(
      d.invoices.length
        ? d.invoices.map((inv) => this._invoice(inv)).join("")
        : `<div class="rsv-empty-state">Aucune facture avec retenue à la source sur la période.</div>`
    );
    this.$root.find("[data-action='toggle-all']").text("Tout déplier");
  }

  _invoice(inv) {
    const esc = frappe.utils.escape_html;
    const nb = inv.attachments.length;
    return `
    <div class="rsv-inv">
      <button class="rsv-inv-head" type="button">
        <span class="rsv-caret">▶</span>
        <span>${this._doclink("Sales Invoice", inv.name, inv.name, "rsv-si")}</span>
        <span class="rsv-cust">
          ${this._doclink("Customer", inv.customer, inv.customer_name)}
          ${inv.tax_id ? ` · MF ${esc(inv.tax_id)}` : ""}
          <span class="rsv-date-cell"> · ${frappe.datetime.str_to_user(inv.posting_date)}</span>
        </span>
        <span><span class="rsv-badge ${RSV_SI_STATUS_CLASS[inv.status] || "st-neutral"}">${esc(inv.status || "—")}</span></span>
        <span class="rsv-num">${this._fmt(inv.grand_total)}</span>
        <span class="rsv-num"><span class="rsv-chip">${this._fmt(inv.ras_total)}</span></span>
        <span class="rsv-num rsv-rate">${inv.ras_rate} %
          <span class="rsv-att-count ${nb ? "" : "none"}">· ${nb ? `📎 ${nb}` : "sans justif."}</span>
        </span>
      </button>
      <div class="rsv-inv-body">
        <div class="rsv-cols">
          <div>
            <h6>Écritures de retenue</h6>
            <div class="rsv-scroll">
              <table class="rsv-tbl">
                <thead><tr>
                  <th>Écriture</th><th>Date</th><th>Référence</th><th class="rsv-num">Montant alloué</th>
                </tr></thead>
                <tbody>
                  ${inv.payments.map((p) => `
                    <tr>
                      <td>${this._doclink("Payment Entry", p.payment_entry)}</td>
                      <td>${p.posting_date ? frappe.datetime.str_to_user(p.posting_date) : "—"}</td>
                      <td>${esc(p.reference_no || "")}${p.reference_date ? ` (${frappe.datetime.str_to_user(p.reference_date)})` : ""}</td>
                      <td class="rsv-num">${this._fmt(p.amount)}</td>
                    </tr>`).join("")}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <h6>Pièces jointes (${nb})</h6>
            <div class="rsv-atts">
              ${nb ? inv.attachments.map((a) => `
                <button class="rsv-att" type="button"
                        data-url="${esc(a.file_url)}" data-fname="${esc(a.file_name)}" data-ext="${esc(a.extension)}">
                  <span class="icon">${a.extension === "pdf" ? "📄" : RSV_IMG_EXT.includes(a.extension) ? "🖼️" : "📎"}</span>
                  <span class="fname">${esc(a.file_name)}</span>
                  <span class="src">${esc(a.source)} · ${esc(a.source_name)}</span>
                </button>`).join("") : `<span class="rsv-empty">Aucun justificatif attaché — pensez à joindre le certificat de retenue.</span>`}
            </div>
          </div>
        </div>
        <div class="rsv-actions">
          ${nb ? "" : `<button class="btn btn-default btn-xs rsv-relance-one" data-customer="${esc(inv.customer)}">✉️ Relancer ce client</button>`}
          <button class="btn btn-default btn-xs rsv-tel" data-customer="${esc(inv.customer)}" data-customer-name="${esc(inv.customer_name)}">📞 Relance téléphone</button>
          <button class="btn btn-default btn-xs rsv-histo" data-customer="${esc(inv.customer)}" data-customer-name="${esc(inv.customer_name)}">🗂 Historique relances</button>
        </div>
      </div>
    </div>`;
  }
}
