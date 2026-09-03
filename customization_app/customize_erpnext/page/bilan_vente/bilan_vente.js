frappe.pages["bilan-vente"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Bilan Vente Economiq Aqua Solution",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("bilan_vente", {})
  );
  new BilanVente(wrapper);
};

// Rendu pur : toutes les valeurs (bénéfices, totaux, soldes, classification
// des paiements) viennent du backend customization_app.bilan_vente.get_data.

const BV_DOCTYPE_LABELS = {
  "Sales Order": "Commande client",
  "Payment Entry": "Écriture de paiement",
  "Tache de travail": "Tâche de travail",
  "Delivery Note": "Bon de livraison",
};

// Champs affichés dans l'aperçu pop-up, par doctype (affichage uniquement).
const BV_PREVIEW_FIELDS = {
  "Sales Order": [
    ["status", "Statut", "badge"],
    ["customer", "Client"],
    ["transaction_date", "Date commande", "date"],
    ["delivery_date", "Date livraison", "date"],
    ["grand_total", "Total TTC", "currency"],
    ["delivery_status", "Statut livraison"],
    ["owner", "Créée par"],
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
  "Delivery Note": [
    ["status", "Statut", "badge"],
    ["customer_name", "Client"],
    ["posting_date", "Date", "date"],
    ["grand_total", "Total TTC", "currency"],
    ["custom_reconciliation_stock", "Réconciliation stock"],
    ["set_warehouse", "Magasin"],
    ["owner", "Créé par"],
  ],
  "Tache de travail": [
    ["status", "Statut", "badge"],
    ["custom_type_dintervention", "Type d'intervention"],
    ["custom_employé", "Effectuée par"],
    ["custom_client", "Client"],
    ["starts_on", "Début", "datetime"],
    ["ends_on", "Fin", "datetime"],
    ["commande_client", "Commande client"],
  ],
};

// Les statuts d'un bon de livraison. « Cancelled » est posé par le backend depuis le docstatus :
// une pièce annulée conserve sinon le statut qu'elle avait avant, et s'afficherait « Completed ».
const BV_DN_STATUS_CLASS = {
  Completed: "st-green",
  Closed: "st-green",
  "To Bill": "st-orange",
  Draft: "st-orange",
  "Return Issued": "st-red",
  Cancelled: "st-gray",
};

const BV_TASK_STATUS_CLASS = {
  Completed: "st-green",
  Closed: "st-green",
  Open: "st-red",
  Cancelled: "st-gray",
};

class BilanVente {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".bv-page");
    this._data = null;
    this._bind();
    this._fetch();
  }

  // Les trois filtres partent ensemble : le mois, la base de comptage et l'exclusion des
  // tâches ouvertes. Les lire au moment du fetch (et non les mémoriser) évite qu'un export
  // Excel reparte sur un périmètre différent de celui affiché.
  _filtres() {
    return {
      month: this.$root.find("#bv-month").val() || (this._data && this._data.month) || "",
      base: this.$root.find("#bv-base").val() || "livraison",
      exclure_ouvertes: this.$root.find("#bv-ouvertes").is(":checked") ? 1 : 0,
    };
  }

  // Les deux cases sont une RÈGLE ENREGISTRÉE, pas une vue : l'onglet « Partenaire Economiq »
  // la lit aussi pour bâtir l'écriture de bilan. On l'écrit donc avant de relire, sinon les
  // deux écrans annonceraient deux bénéfices différents sur le même mois.
  async _enregistrer_regle() {
    const f = this._filtres();
    await frappe.call({
      method: "customization_app.bilan_vente.set_regle",
      args: { base: f.base, exclure_ouvertes: f.exclure_ouvertes },
    });
  }

  _bind() {
    const relire = () => this._fetch();
    this.$root.find("#bv-month").on("change", relire);
    this.$root.find("#bv-base, #bv-ouvertes").on("change", async () => {
      await this._enregistrer_regle();
      this._fetch();
    });
    this.$root.find("[data-action='refresh']").on("click", relire);
    this.$root.find("[data-action='print']").on("click", () => window.print());
    this.$root.find("[data-action='excel']").on("click", () => {
      const f = this._filtres();
      const q = $.param({ month: f.month, base: f.base,
                          exclure_ouvertes: f.exclure_ouvertes });
      window.open(`/api/method/customization_app.bilan_vente.download_excel?${q}`);
      // L'export part des cases AFFICHÉES, qui sont déjà la règle : ce que tu vois est
      // exactement ce que tu télécharges.
    });
    this.$root.find("[data-action='toggle-all']").on("click", (e) => {
      const $orders = this.$root.find(".bv-order");
      const any_closed = $orders.not(".open").length > 0;
      $orders.toggleClass("open", any_closed);
      $(e.currentTarget).text(any_closed ? "Tout replier" : "Tout déplier");
    });
    this.$root.on("click", ".bv-order-head", (e) => {
      if ($(e.target).closest("a").length) return;
      $(e.currentTarget).closest(".bv-order").toggleClass("open");
    });
    // Tous les liens de documents s'ouvrent en pop-up
    this.$root.on("click", "a[data-preview]", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const $el = $(e.currentTarget);
      this._preview($el.attr("data-doctype"), $el.attr("data-name"));
    });
  }

  async _fetch() {
    const f = this._filtres();
    const r = await frappe.call({
      method: "customization_app.bilan_vente.get_data",
      args: { month: f.month || null, base: f.base,
              exclure_ouvertes: f.exclure_ouvertes },
      freeze: true,
      freeze_message: __("Chargement du bilan…"),
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

  async _preview(doctype, name) {
    let doc;
    try {
      doc = await frappe.db.get_doc(doctype, name);
    } catch (e) {
      frappe.msgprint(__("Impossible de charger {0} {1} (droits insuffisants ?)", [doctype, name]));
      return;
    }
    const esc = frappe.utils.escape_html;
    const rows = (BV_PREVIEW_FIELDS[doctype] || [])
      .filter(([field]) => doc[field] !== undefined && doc[field] !== null && doc[field] !== "")
      .map(([field, label, type]) => {
        let val = doc[field];
        if (type === "currency") val = this._fmt(val);
        else if (type === "date" || type === "datetime") val = frappe.datetime.str_to_user(val);
        else val = esc(String(val));
        if (type === "badge") val = `<span class="bv-badge st-neutral">${val}</span>`;
        return `<tr><td class="bv-pv-label">${label}</td><td>${val}</td></tr>`;
      })
      .join("");

    const dialog = new frappe.ui.Dialog({
      title: `${BV_DOCTYPE_LABELS[doctype] || doctype} — ${name}`,
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
    dialog.$body.html(`<table class="bv-pv-table">${rows || `<tr><td>${__("Aucun détail disponible")}</td></tr>`}</table>`);
    dialog.show();
  }

  // Ouvre le formulaire Desk complet (n'importe quel doctype) dans un grand
  // pop-up, sans quitter le rapport. Le chrome du desk (navbar) est masqué
  // dans l'iframe ; la fiche reste 100 % fonctionnelle (édition incluse).
  _open_form_popup(doctype, name) {
    const route = `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
    const dialog = new frappe.ui.Dialog({
      title: `${BV_DOCTYPE_LABELS[doctype] || doctype} — ${name}`,
      size: "extra-large",
      secondary_action_label: __("Ouvrir dans un onglet"),
      secondary_action: () => window.open(route),
    });
    dialog.$body.html(
      `<iframe class="bv-form-frame" src="${route}" title="${frappe.utils.escape_html(name)}"></iframe>`
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
      } catch (e) { /* cross-origin improbable (même site) — on laisse tel quel */ }
    });
    dialog.show();
  }

  _render() {
    const d = this._data;
    const esc = frappe.utils.escape_html;

    const $month = this.$root.find("#bv-month");
    $month.empty();
    (d.months || []).forEach((m) => {
      $month.append(`<option value="${esc(m.value)}">${esc(m.label)}</option>`);
    });
    $month.val(d.month);
    // Le serveur a le dernier mot sur les filtres retenus : il normalise une base inconnue.
    this.$root.find("#bv-base").val(d.base || "livraison");
    this.$root.find("#bv-ouvertes").prop("checked", !!d.exclure_ouvertes);
    // Qui ne peut pas régler avec le partenaire ne change pas la règle du règlement.
    const fige = !d.peut_regler;
    this.$root.find("#bv-base, #bv-ouvertes").prop("disabled", fige).attr(
      "title",
      fige ? "Reserve aux profils comptables : cette regle sert aussi a l onglet Partenaire Economiq"
           : "Regle partagee avec l onglet Partenaire Economiq"
    );
    const ecartees = d.ecartees || [];
    this.$root.find("[data-role='ecartees']").text(
      ecartees.length
        ? `${ecartees.length} commande(s) écartée(s) : tâche encore ouverte`
        : ""
    ).attr("title", ecartees.join(", "));
    this.$root.find("[data-role='period']").text(
      `du ${frappe.datetime.str_to_user(d.period.from)} au ${frappe.datetime.str_to_user(d.period.to)}`
    );

    // KPI par société (totaux calculés par le backend, par section)
    this.$root.find("[data-role='kpis']").html(
      (d.sections || []).map((s) => this._kpigroup(s)).join("")
    );

    // barre globale des encaissements
    const k = d.kpis;
    const total = (k.especes + k.cheques + k.autres) || 1;
    const seg = (cls, v) => (v > 0 ? `<div class="seg ${cls}" style="flex:${(v / total).toFixed(4)}"></div>` : "");
    this.$root.find("[data-role='cashbar']").html(
      seg("especes", k.especes) + seg("cheque", k.cheques) + seg("autre", k.autres)
    );
    const leg = (cls, lbl, v) =>
      `<span><span class="bv-dot" style="background:var(--bv-${cls})"></span>${lbl} <b>${this._fmt(v)}</b></span>`;
    this.$root.find("[data-role='cashlegend']").html(
      leg("especes", "Espèces", k.especes) + leg("cheque", "Chèques", k.cheques) + leg("autre", "Autres modes", k.autres)
    );

    this.$root.find("[data-role='sections']").html(
      (d.sections || []).map((s) => this._section(s)).join("")
    );
    this.$root.find("[data-action='toggle-all']").text("Tout déplier");
  }

  _kpigroup(s) {
    const esc = frappe.utils.escape_html;
    const t = s.totals;
    const tile = (lbl, v, cls = "") =>
      `<div class="bv-kpi ${cls}"><div class="lbl">${lbl}</div><div class="val">${this._fmt(v)}</div></div>`;
    return `
    <div class="bv-kpigroup" data-owner="${esc(s.key)}">
      <div class="head">
        <span class="eyebrow">Travaux effectués par</span>
        <span class="tag">${esc(s.company)}</span>
        ${s.margin_note ? `<span class="note">${esc(s.margin_note)}</span>` : ""}
      </div>
      <div class="tiles">
        ${tile("Ventes TTC", t.vente)}
        ${tile("Achats TTC", t.achat)}
        ${tile("Bénéfice", t.benefice, t.benefice >= 0 ? "gain" : "loss")}
        ${tile("Espèces", t.especes)}
        ${tile("Chèques", t.cheques)}
        ${tile("Restant dû", t.reste, t.reste > 0.001 ? "loss" : "")}
      </div>
    </div>`;
  }

  _section(s) {
    const esc = frappe.utils.escape_html;
    const t = s.totals;
    const n = s.orders.length;
    const cheques = (s.cheques || [])
      .map((c) => `${esc(c.reference_no || "—")} : ${this._fmt(c.amount)}`)
      .join(" · ");
    return `
    <div class="bv-section" data-owner="${esc(s.key)}">
      <div class="bv-section-head">
        <div class="bar"></div>
        <h2>Travaux effectués par</h2>
        <span class="tag">${esc(s.company)}</span>
        ${s.margin_note ? `<span class="note">${esc(s.margin_note)}</span>` : ""}
        <span class="count">${n} commande${n > 1 ? "s" : ""}</span>
      </div>
      ${n ? s.orders.map((o) => this._order(o)).join("") : `<div class="bv-empty">Aucune commande sur la période.</div>`}
      <div class="bv-bilan">
        <h5>Bilan — ${esc(s.company)}</h5>
        <div class="bv-bilan-grid">
          ${this._cell("Ventes TTC", t.vente)}
          ${this._cell("Achats TTC", t.achat)}
          ${this._cell("Bénéfice", t.benefice)}
          ${this._cell("Espèces", t.especes)}
          ${this._cell("Chèques", t.cheques)}
          ${this._cell("Autres modes", t.autres)}
        </div>
        <div class="bv-solde">
          <span>${esc(s.solde.due_by)}</span>
          <span class="amount">${this._fmt(s.solde.amount)}</span>
        </div>
        ${cheques ? `<div class="bv-cheques"><b>Chèques :</b> ${cheques}</div>` : ""}
        ${this._payments_detail(s)}
      </div>
    </div>`;
  }

  _payments_detail(s) {
    const esc = frappe.utils.escape_html;
    const rows = s.payments_detail || [];
    if (!rows.length) return "";
    return `
    <details class="bv-paydetail">
      <summary>Détail des paiements (${rows.length})</summary>
      <div class="bv-scroll">
        <table class="bv-tbl">
          <thead><tr>
            <th>Date</th><th>Écriture</th><th>Commande</th><th>Client</th>
            <th>Mode</th><th>Compte encaissé</th><th>Référence</th><th class="bv-num">Montant</th>
          </tr></thead>
          <tbody>
            ${rows.map((p) => `
              <tr>
                <td>${p.posting_date ? frappe.datetime.str_to_user(p.posting_date) : "—"}</td>
                <td>${this._doclink("Payment Entry", p.payment_entry)}</td>
                <td>${this._doclink("Sales Order", p.order)}</td>
                <td>${esc(p.customer || "")}</td>
                <td><span class="bv-badge ${esc(p.kind)}">${esc(p.mode || "—")}</span></td>
                <td>${esc(p.paid_to || "—")}</td>
                <td>${esc(p.reference_no || "")}</td>
                <td class="bv-num">${this._fmt(p.amount)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </details>`;
  }

  _cell(k, v) {
    return `<div><div class="k">${k}</div><div class="v">${this._fmt(v)}</div></div>`;
  }

  /** Les bons de livraison de la commande, avec leur état et un lien en pop-up.
   *
   * L'éligibilité au bilan repose sur la livraison (« Fully Delivered », ou un bon validé avec
   * réconciliation de stock) sans jamais montrer QUELLE pièce la porte : on lisait une commande
   * au bilan sans pouvoir vérifier ce qui l'y avait fait entrer.
   */
  _bons(o) {
    const esc = frappe.utils.escape_html;
    if (!o.bons || !o.bons.length) {
      return `<div class="bv-tasks"><h6>Bons de livraison</h6>
        <div class="bv-task"><span class="meta">Aucun bon de livraison rattaché.</span></div></div>`;
    }
    return `
    <div class="bv-tasks">
      <h6>Bons de livraison</h6>
      ${o.bons.map((b) => `
        <div class="bv-task">
          ${this._doclink("Delivery Note", b.name, b.name, "bv-task-link")}
          <span class="bv-badge ${BV_DN_STATUS_CLASS[b.status] || "st-orange"}">${esc(b.status || "—")}</span>
          ${b.posting_date ? `<span class="meta">${frappe.datetime.str_to_user(b.posting_date)}</span>` : ""}
          <span class="meta">${this._fmt(b.total)}</span>
          ${b.reconciliation ? `<span class="meta">réconc. ${esc(b.reconciliation)}</span>` : ""}
        </div>`).join("")}
    </div>`;
  }

  _tasks(o) {
    const esc = frappe.utils.escape_html;
    if (!o.tasks || !o.tasks.length) return "";
    return `
    <div class="bv-tasks">
      <h6>Tâches de travail</h6>
      ${o.tasks.map((tk) => `
        <div class="bv-task">
          ${this._doclink("Tache de travail", tk.name, tk.name, "bv-task-link")}
          <span class="bv-badge ${BV_TASK_STATUS_CLASS[tk.status] || "st-orange"}">${esc(tk.status || "—")}</span>
          ${tk.intervention_type ? `<span class="meta">${esc(tk.intervention_type)}</span>` : ""}
          ${tk.employee ? `<span class="meta">par <b>${esc(tk.employee)}</b></span>` : ""}
          ${tk.starts_on ? `<span class="meta">début : ${frappe.datetime.str_to_user(tk.starts_on)}</span>` : ""}
        </div>`).join("")}
    </div>`;
  }

  _order(o) {
    const esc = frappe.utils.escape_html;
    const t = o.totals;
    const soldee = t.reste <= 0.001;
    const ben_cls = t.benefice >= 0 ? "pos" : "neg";
    return `
    <div class="bv-order">
      <button class="bv-order-head" type="button">
        <span class="bv-caret">▶</span>
        <span>${this._doclink("Sales Order", o.name, o.name, "bv-so")}</span>
        <span class="bv-cust">${esc(o.customer)} · ${frappe.datetime.str_to_user(o.delivery_date)}</span>
        <span class="bv-num bv-ttc">${this._fmt(o.total_ttc)}</span>
        <span class="bv-num bv-paystate-cell"><span class="bv-paystate ${soldee ? "ok" : "due"}">${soldee ? "Soldée" : "Reste " + this._fmt(t.reste)}</span></span>
        <span class="bv-num bv-chip-cell"><span class="bv-chip ${ben_cls}">${t.benefice >= 0 ? "+" : "−"}${this._fmt(Math.abs(t.benefice))}</span></span>
      </button>
      <div class="bv-order-body">
        <h6>Articles</h6>
        <div class="bv-scroll">
          <table class="bv-tbl">
            <thead><tr>
              <th>Article</th><th class="bv-num">Qté</th><th class="bv-num">PV TTC</th>
              <th class="bv-num">PA TTC</th><th class="bv-num">Bénéfice</th>
            </tr></thead>
            <tbody>
              ${o.items.map((it) => `
                <tr>
                  <td>${esc(it.item_name)}</td>
                  <td class="bv-num">${it.qty}</td>
                  <td class="bv-num">${this._fmt(it.pv)}</td>
                  <td class="bv-num">${this._fmt(it.pa)}</td>
                  <td class="bv-num ${it.benefice > 0.0005 ? "bv-gain" : it.benefice < -0.0005 ? "bv-loss" : ""}">${this._fmt(it.benefice)}</td>
                </tr>`).join("")}
            </tbody>
            <tfoot><tr>
              <td>Total</td><td class="bv-num"></td>
              <td class="bv-num">${this._fmt(t.vente)}</td>
              <td class="bv-num">${this._fmt(t.achat)}</td>
              <td class="bv-num ${t.benefice >= 0 ? "bv-gain" : "bv-loss"}">${this._fmt(t.benefice)}</td>
            </tr></tfoot>
          </table>
        </div>
        <div class="bv-paycols">
          <div>
            <h6>Paiements</h6>
            <div class="bv-paylist">
              ${o.payments.length ? o.payments.map((p) => `
                <div class="bv-payrow">
                  <span class="bv-badge ${esc(p.kind)}">${esc(p.mode || "—")}</span>
                  ${this._doclink("Payment Entry", p.payment_entry, p.payment_entry, "ref")}
                  ${p.posting_date ? `<span class="ref">${frappe.datetime.str_to_user(p.posting_date)}</span>` : ""}
                  ${p.reference_no ? `<span class="ref">${esc(p.reference_no)}</span>` : ""}
                  <span class="amt">${this._fmt(p.amount)}</span>
                </div>`).join("") : `<span class="bv-empty">Aucun paiement enregistré</span>`}
            </div>
          </div>
          <div>
            <h6>Règlement</h6>
            <div class="bv-paylist">
              <div class="bv-payrow"><span>Total TTC</span><span class="amt">${this._fmt(o.total_ttc)}</span></div>
              <div class="bv-payrow"><span>Encaissé</span><span class="amt">${this._fmt(t.paid)}</span></div>
              <div class="bv-payrow"><span>Reste dû</span><span class="amt" style="color:var(--bv-${soldee ? "gain" : "loss"})">${this._fmt(t.reste)}</span></div>
            </div>
          </div>
        </div>
        ${this._bons(o)}
        ${this._tasks(o)}
      </div>
    </div>`;
  }
}
