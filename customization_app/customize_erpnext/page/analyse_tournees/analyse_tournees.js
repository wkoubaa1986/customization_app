frappe.pages["analyse-tournees"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Analyse Tournées Commerciales",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("analyse_tournees", {}));
  new AnalyseTournees(wrapper);
};

// Rendu pur : tous les agrégats viennent de customization_app.tournee.get_analytics.
const AT_RESULT_CLASS = {
  "Visite réalisée": "res-green",
  "Demande de devis": "res-blue",
  "Commande possible": "res-teal",
  "Nouveau besoin": "res-violet",
  "Relance nécessaire": "res-orange",
  "Client absent": "res-gray",
  "Aucun intérêt": "res-red",
  "Sans résultat": "res-gray",
};
const AT_RESULT_VARS = {
  "res-green": "--at-gain", "res-blue": "--at-accent", "res-teal": "--at-teal",
  "res-violet": "--at-violet", "res-orange": "--at-warn", "res-red": "--at-loss",
  "res-gray": "--at-gray",
};

class AnalyseTournees {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".at-page");
    this._data = null;
    this._init_defaults();
    this._bind();
    this._fetch();
  }

  _init_defaults() {
    this.$root.find("#at-from").val(frappe.datetime.month_start());
    this.$root.find("#at-to").val(frappe.datetime.month_end());
  }

  _args() {
    return {
      from_date: this.$root.find("#at-from").val() || null,
      to_date: this.$root.find("#at-to").val() || null,
      commercial: this.$root.find("#at-commercial").val() || null,
    };
  }

  _bind() {
    this.$root.find("[data-action='refresh']").on("click", () => this._fetch());
    this.$root.find("#at-from, #at-to, #at-commercial").on("change", () => this._fetch());
    this.$root.find("[data-action='print']").on("click", () => window.print());
    this.$root.find("[data-action='excel']").on("click", () => {
      const a = this._args();
      window.open(
        `/api/method/customization_app.tournee.download_excel?from_date=${encodeURIComponent(a.from_date || "")}` +
        `&to_date=${encodeURIComponent(a.to_date || "")}&commercial=${encodeURIComponent(a.commercial || "")}`
      );
    });
    this.$root.on("click", ".at-tournee-head", (e) => {
      if ($(e.target).closest("a").length) return;
      $(e.currentTarget).closest(".at-tournee").toggleClass("open");
    });
    this.$root.on("click", ".at-proofthumb", (e) => {
      this._photo_popup($(e.currentTarget).attr("data-src"), $(e.currentTarget).attr("data-title"));
    });
    this.$root.on("click", "[data-action='reprogrammer']", (e) => {
      const $el = $(e.currentTarget);
      this._dialog_reprogrammer($el.attr("data-visite"), $el.attr("data-client"), $el.attr("data-commercial"));
    });
  }

  async _fetch() {
    const r = await frappe.call({
      method: "customization_app.tournee.get_analytics",
      args: this._args(),
      freeze: true,
      freeze_message: __("Analyse en cours…"),
    });
    this._data = r.message;
    this._render();
  }

  _fmt(v) {
    return format_currency(v || 0, this._data.currency);
  }

  _render() {
    const esc = frappe.utils.escape_html;
    const d = this._data;
    const k = d.kpis;

    // filtre commercial (préserver la sélection)
    const $sel = this.$root.find("#at-commercial");
    const current = $sel.val();
    $sel.find("option:not(:first)").remove();
    (d.commerciaux || []).forEach((c) => {
      if (c) $sel.append(`<option value="${esc(c)}">${esc(c)}</option>`);
    });
    $sel.val(current || "");

    const kpi = (lbl, val, cls = "", sub = "") =>
      `<div class="at-kpi ${cls}"><div class="lbl">${lbl}</div><div class="val">${val}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
    this.$root.find("[data-role='kpis']").html([
      kpi("Visites prévues", k.visites_prevues),
      kpi("Visites réalisées", k.visites_realisees, "gain", `${k.taux_realisation} % de réalisation`),
      kpi("Nouveaux prospects", k.nouveaux_prospects),
      kpi("Contacts ajoutés", k.contacts_ajoutes),
      kpi("Demandes de devis", k.demandes_devis),
      kpi("Opportunités créées", k.opportunites),
      kpi("Montant potentiel", this._fmt(k.montant_potentiel), "gain"),
      kpi("Taux de conversion", `${k.taux_conversion} %`),
      kpi("Visites avec photo", `${k.avec_photo}/${k.visites_realisees}`),
      kpi("Validées GPS", `${k.avec_gps}/${k.visites_realisees}`),
      kpi("Relances en retard", k.relances_en_retard, k.relances_en_retard > 0 ? "loss" : "gain"),
    ].join(""));

    // répartition des résultats
    const total = Object.values(d.repartition).reduce((s, n) => s + n, 0) || 1;
    const entries = Object.entries(d.repartition).sort((a, b) => b[1] - a[1]);
    this.$root.find("[data-role='resultbar']").html(
      entries.map(([res, n]) => {
        const cls = AT_RESULT_CLASS[res] || "res-gray";
        return `<div class="seg" style="flex:${(n / total).toFixed(4)};background:var(${AT_RESULT_VARS[cls]})"></div>`;
      }).join("")
    );
    this.$root.find("[data-role='resultlegend']").html(
      entries.length
        ? entries.map(([res, n]) => {
            const cls = AT_RESULT_CLASS[res] || "res-gray";
            return `<span><span class="at-dot" style="background:var(${AT_RESULT_VARS[cls]})"></span>${esc(res)} <b>${n}</b></span>`;
          }).join("")
        : `<span class="at-empty">Aucune visite réalisée sur la période.</span>`
    );

    // par commercial
    this.$root.find("[data-role='commerciaux']").html(
      d.par_commercial.length ? `
      <table class="at-tbl">
        <thead><tr><th>Commercial</th><th class="num">Visites</th><th class="num">Réalisées</th>
        <th class="num">Devis / Cmd possibles</th><th class="num">Montant potentiel</th><th class="num">Conversion</th></tr></thead>
        <tbody>${d.par_commercial.map((s) => `
          <tr>
            <td>${esc(s.commercial_nom)}</td>
            <td class="num">${s.visites}</td>
            <td class="num">${s.realisees}</td>
            <td class="num">${s.devis}</td>
            <td class="num">${this._fmt(s.montant)}</td>
            <td class="num">${s.realisees ? Math.round((s.devis / s.realisees) * 100) : 0} %</td>
          </tr>`).join("")}
        </tbody>
      </table>` : `<div class="at-empty">Aucune donnée.</div>`
    );

    // relances / actions futures
    const late = d.relances.filter((r) => r.en_retard).length;
    this.$root.find("[data-role='late-count']").html(
      late ? `<span class="at-badge res-red" style="margin-left:6px">${late} en retard</span>` : ""
    );
    this.$root.find("[data-role='relances']").html(
      d.relances.length ? `
      <table class="at-tbl">
        <thead><tr><th>Client</th><th>Commercial</th><th>Date relance</th><th>Prochaine action</th>
        <th>Tâche</th><th>Statut</th><th></th></tr></thead>
        <tbody>${d.relances.map((r) => `
          <tr class="${r.en_retard ? "at-late" : ""}">
            <td>${esc(r.client_nom || r.client)}</td>
            <td>${esc(r.commercial_nom || "")}</td>
            <td>${r.date_relance ? frappe.datetime.str_to_user(r.date_relance) : "—"}${r.en_retard ? " ⚠" : ""}</td>
            <td>${esc(r.prochaine_action || "")}</td>
            <td>${r.tache_relance ? `<a href="/app/tache-de-travail/${encodeURIComponent(r.tache_relance)}">${esc(r.tache_relance)}</a>` : "—"}</td>
            <td>${r.faite ? `<span class="at-badge res-green">Faite</span>` : r.tache_relance ? `<span class="at-badge res-blue">Planifiée</span>` : `<span class="at-badge res-orange">À planifier</span>`}</td>
            <td>${r.faite ? "" : `<button class="btn btn-default btn-xs" data-action="reprogrammer"
                 data-visite="${esc(r.visite)}" data-client="${esc(r.client_nom || r.client)}"
                 data-commercial="${esc(r.commercial || "")}">📅 Reprogrammer</button>`}</td>
          </tr>`).join("")}
        </tbody>
      </table>` : `<div class="at-empty">Aucune relance sur la période.</div>`
    );

    // tournées
    this.$root.find("[data-role='tournees']").html(
      d.tournees.length ? d.tournees.map((t) => this._tournee(t)).join("")
        : `<div class="at-empty">Aucune tournée sur la période.</div>`
    );
  }

  _tournee(t) {
    const esc = frappe.utils.escape_html;
    const realisees = t.visites.filter((v) => v.statut === "Réalisée").length;
    return `
    <div class="at-tournee">
      <button class="at-tournee-head" type="button">
        <span class="at-caret">▶</span>
        <a class="tc" href="/app/tournee-commerciale/${encodeURIComponent(t.name)}">${esc(t.name)}</a>
        <span class="meta">${t.date_tournee ? frappe.datetime.str_to_user(t.date_tournee) : "—"} · ${esc(t.commercial_nom || "")}
          · ${realisees}/${t.visites.length} visite(s)</span>
        <span class="at-badge ${t.statut === "Terminée" ? "res-green" : t.statut === "Annulée" ? "res-gray" : "res-blue"}">${esc(t.statut || "")}</span>
      </button>
      <div class="at-tournee-body">
        <div class="at-scroll">
          <table class="at-tbl">
            <thead><tr><th>Client</th><th>Résultat</th><th>Intérêt</th><th class="num">Montant pot.</th>
            <th>📷</th><th>📍</th><th>Relance</th><th>Opportunité</th></tr></thead>
            <tbody>${t.visites.map((v) => `
              <tr>
                <td><a href="/app/visite-commerciale/${encodeURIComponent(v.name)}">${esc(v.client_nom || v.client)}</a>${v.nouveau_prospect ? " 🆕" : ""}</td>
                <td>${v.resultat ? `<span class="at-badge ${AT_RESULT_CLASS[v.resultat] || "res-gray"}">${esc(v.resultat)}</span>` : `<span class="at-badge res-gray">${esc(v.statut)}</span>`}</td>
                <td>${esc(v.niveau_interet || "")}</td>
                <td class="num">${v.montant_potentiel ? this._fmt(v.montant_potentiel) : ""}</td>
                <td>${v.photo_visite ? `<img class="at-proofthumb" src="${esc(v.photo_visite)}" data-src="${esc(v.photo_visite)}" data-title="${esc(v.client_nom || v.client)}">` : "—"}</td>
                <td>${v.gps_lat && v.gps_lng ? `<a href="${esc(v.lien_google_maps || `https://maps.google.com/?q=${v.gps_lat},${v.gps_lng}`)}" target="_blank">📍</a>` : "—"}</td>
                <td>${v.date_relance ? frappe.datetime.str_to_user(v.date_relance) : "—"}</td>
                <td>${v.opportunite ? `<a href="/app/opportunity/${encodeURIComponent(v.opportunite)}">${esc(v.opportunite)}</a>` : "—"}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </div>
    </div>`;
  }

  _photo_popup(src, title) {
    const dialog = new frappe.ui.Dialog({
      title: title || __("Photo de visite"),
      size: "extra-large",
      secondary_action_label: __("Ouvrir dans un onglet"),
      secondary_action: () => window.open(src),
    });
    dialog.$body.html(`<img class="at-file-img" src="${frappe.utils.escape_html(src)}">`);
    dialog.$wrapper.find(".modal-dialog").css({ "max-width": "94vw", width: "94vw" });
    dialog.show();
  }

  _dialog_reprogrammer(visite, client, commercial) {
    const d = new frappe.ui.Dialog({
      title: __("Reprogrammer la relance — {0}", [client]),
      fields: [
        { fieldname: "date_relance", label: __("Nouvelle date"), fieldtype: "Date", reqd: 1,
          default: frappe.datetime.add_days(frappe.datetime.get_today(), 7) },
        { fieldname: "commercial", label: __("Commercial"), fieldtype: "Link", options: "Employee",
          default: commercial || "" },
      ],
      primary_action_label: __("Planifier"),
      primary_action: async (values) => {
        d.hide();
        const r = await frappe.call({
          method: "customization_app.tournee.reprogrammer_relance",
          args: { visite, date_relance: values.date_relance, commercial: values.commercial || null },
          freeze: true,
        });
        frappe.show_alert({
          message: __("Relance planifiée : {0}", [r.message.tache]),
          indicator: "green",
        });
        this._fetch();
      },
    });
    d.show();
  }
}
