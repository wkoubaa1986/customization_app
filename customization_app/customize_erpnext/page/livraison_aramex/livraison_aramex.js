frappe.pages["livraison-aramex"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Livraisons Aramex — Suivi",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("livraison_aramex", {}));
  new LivraisonAramex(wrapper);
};

// Rendu pur : la liste, les coordonnées et le suivi viennent de
// customization_app.livraison_aramex. L'écran ne calcule rien.
class LivraisonAramex {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".ala-page");
    this._bind();
    this._dates_par_defaut();
    this.refresh();
  }

  _dates_par_defaut() {
    // L'année civile entière : un colis de janvier doit rester visible en décembre.
    const annee = frappe.datetime.str_to_obj(frappe.datetime.get_today()).getFullYear();
    this.$root.find("#ala-from").val(`${annee}-01-01`);
    this.$root.find("#ala-to").val(`${annee}-12-31`);
  }

  _bind() {
    this.$root.on("click", "[data-action='refresh']", () => this.refresh());
    this.$root.on("click", "[data-action='suivre']", () => this._suivre());
    this.$root.on("change", "[data-role='alertes-seules']", () => this._render());
    this.$root.on("change", "[data-role='sms-auto']", (e) => this._basculer_sms(e.currentTarget));
    this.$root.on("click", "[data-act='un-suivi']", (e) =>
      this._suivre([$(e.currentTarget).data("ref")], true)
    );
  }

  refresh() {
    return frappe
      .call({
        method: "customization_app.livraison_aramex.get_data",
        args: {
          from_date: this.$root.find("#ala-from").val() || null,
          to_date: this.$root.find("#ala-to").val() || null,
        },
        freeze: true,
        freeze_message: __("Lecture des livraisons…"),
      })
      .then((r) => {
        this._data = r.message || {};
        this._render();
      });
  }

  // Le suivi frais se demande, il ne s'impose pas : 1,9 seconde par colis, et un colis livré ne
  // bougera plus jamais — c'est le backend qui saute ceux-là.
  _suivre(references, tout) {
    const refs = references || (this._data.colis || []).map((c) => c.reference).filter(Boolean);
    if (!refs.length) {
      frappe.show_alert({ message: __("Aucun bordereau à interroger"), indicator: "orange" }, 5);
      return;
    }
    return frappe
      .call({
        method: "customization_app.livraison_aramex.rafraichir",
        args: { references: JSON.stringify(refs), tout: tout ? 1 : 0 },
        freeze: true,
        freeze_message: __("Interrogation d'Aramex ({0} colis)…", [refs.length]),
      })
      .then((r) => {
        const m = r.message || {};
        // Le plafond ne doit pas être silencieux : « 25 interrogés » sur 31 colis se lirait
        // comme « tout est à jour », et les six derniers resteraient invisibles.
        frappe.show_alert(
          {
            message:
              __("{0} colis interrogé(s), {1} en erreur", [m.interroges || 0, m.erreurs || 0]) +
              (m.ignores ? " · " + __("{0} non traité(s) : relancez pour la suite", [m.ignores]) : ""),
            indicator: m.erreurs || m.ignores ? "orange" : "green",
          },
          7
        );
        this.refresh();
      });
  }

  // Activer cet interrupteur fait partir de vrais SMS à de vrais clients, chaque soir, sans que
  // personne ne relise. On le confirme donc — et on le décoche visuellement si l'utilisateur
  // renonce, pour que la case ne mente jamais sur l'état réel du réglage.
  _basculer_sms(cible) {
    const actif = $(cible).is(":checked");
    const appliquer = () =>
      frappe
        .call({
          method: "customization_app.livraison_aramex.basculer_sms",
          args: { actif: actif ? 1 : 0 },
        })
        .then((r) => {
          const etat = (r.message || {}).sms_auto;
          $(cible).prop("checked", !!etat);
          frappe.show_alert(
            {
              message: etat
                ? __("Envoi automatique activé : un SMS par bordereau, après la synchro de 16h")
                : __("Envoi automatique désactivé"),
              indicator: etat ? "green" : "orange",
            },
            6
          );
        });
    if (!actif) return appliquer();
    frappe.confirm(
      __(
        "Chaque soir après la synchronisation de 16h, un SMS partira vers le client de chaque colis <b>non livré</b> — une seule fois par bordereau. Activer ?"
      ),
      appliquer,
      () => $(cible).prop("checked", false)
    );
  }

  _render() {
    const d = this._data || {};
    const k = d.kpis || {};
    const dt = (v) => format_currency(v, "TND");
    const tuile = (lbl, val, sub, classe) =>
      `<div class="ala-kpi ${classe || ""}"><div class="lbl">${lbl}</div>
         <div class="val">${val}${sub ? ` <span class="sub">${sub}</span>` : ""}</div></div>`;

    this.$root.find("[data-role='kpis']").html(
      [
        tuile(__("Colis"), k.total || 0, dt(k.montant_total)),
        // Le seul chiffre qui parle d'argent : ce qu'Aramex détient pour nous.
        tuile(__("Chez Aramex"), dt(k.chez_aramex), "", k.chez_aramex ? "warn" : ""),
        tuile(__("En route"), k.en_route || 0, dt(k.montant_en_route)),
        tuile(__("Livrés"), k.livres || 0, dt(k.montant_livres)),
        tuile(__("À traiter"), k.alertes || 0, dt(k.montant_alertes), k.alertes ? "loss" : ""),
        tuile(__("Sans bordereau"), k.sans_reference || 0, "", k.sans_reference ? "warn" : ""),
        tuile(__("Suivi jamais demandé"), k.suivi_inconnu || 0),
      ].join("")
    );

    this.$root.find("[data-role='sms-auto']").prop("checked", !!d.sms_auto);

    let colis = d.colis || [];
    if (this.$root.find("[data-role='alertes-seules']").is(":checked")) {
      colis = colis.filter((c) => c.alerte);
    }
    this.$root
      .find("[data-role='colis']")
      .html(
        colis.length
          ? `<div class="ala-liste">${colis.map((c) => this._ligne(c)).join("")}</div>`
          : `<div class="ala-liste"><div class="ala-vide">${__("Aucun colis sur la période.")}</div></div>`
      );
  }

  _ligne(c) {
    const esc = frappe.utils.escape_html;
    const dt = (v) => format_currency(v, "TND");
    const lien = (doctype, nom) =>
      nom
        ? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(nom)}">${esc(nom)}</a>`
        : "—";
    return `<div class="ala-colis ${c.alerte ? "alerte" : ""}">
      <div>
        <div class="nom">${esc(c.customer_name || c.customer || "")}</div>
        <div class="ala-menu">
          ${c.telephone ? `📱 ${esc(c.telephone)}<br>` : ""}
          ${c.email ? `✉️ ${esc(c.email)}<br>` : ""}
          ${c.piece ? `${lien(c.piece_doctype, c.piece)} · ${esc(c.piece_statut || "")}<br>` : ""}
          ${this._data.peut_voir_paiement ? lien("Payment Entry", c.payment_entry) : __("encaissement")} ·
          ${dt(c.montant)} · ${frappe.datetime.str_to_user(c.posting_date)}
        </div>
      </div>
      <div class="ala-adresse">${esc(c.adresse || __("adresse non renseignée"))}
        ${c.adresse_de_facturation ? `<div style="color:var(--ala-warn)">${__("adresse de facturation — pas d'adresse de livraison sur la pièce")}</div>` : ""}
      </div>
      <div>${this._suivi(c)}</div>
    </div>`;
  }

  // Le suivi dit trois choses distinctes, et l'écran ne doit pas les confondre : ce qu'on sait,
  // ce qu'on n'a jamais demandé, et ce qu'on n'a pas pu obtenir.
  _suivi(c) {
    const esc = frappe.utils.escape_html;
    if (!c.reference) {
      return `<span class="ala-chip" style="color:var(--ala-warn);background:var(--ala-warn-bg)">${__(
        "sans bordereau"
      )}</span>
        <div class="ala-menu">${esc(c.reference_brute || "")}</div>`;
    }
    const bouton = `<button class="btn btn-xs btn-default" data-act="un-suivi" data-ref="${esc(
      c.reference
    )}">${__("Interroger")}</button>`;
    const s = c.suivi;
    if (!s) {
      return `<div class="ala-menu">${esc(c.reference)}</div>${bouton}`;
    }
    if (s.erreur) {
      return `<span class="ala-chip" style="color:var(--ala-loss);background:var(--ala-loss-bg)">${__(
        "suivi indisponible"
      )}</span>
        <div class="ala-menu">${esc(s.erreur)}</div>${bouton}`;
    }
    const couleur = s.livre
      ? ["var(--ala-gain)", "var(--ala-gain-bg)"]
      : c.alerte
        ? ["var(--ala-loss)", "var(--ala-loss-bg)"]
        : ["var(--ala-warn)", "var(--ala-warn-bg)"];
    const etapes = (s.etapes || [])
      .map(
        (e) =>
          `<div class="ala-etape ${e.franchie ? "franchie" : ""} ${e.courante ? "courante" : ""}"
                title="${esc(e.libelle || "")}"></div>`
      )
      .join("");
    const maj = s.derniere_maj || {};
    return `<span class="ala-chip" style="color:${couleur[0]};background:${couleur[1]}">${esc(
      s.statut || ""
    )}</span>
      ${s.destination && s.destination.ville ? `<span class="ala-menu"> → ${esc(s.destination.ville)}</span>` : ""}
      <div class="ala-etapes">${etapes}</div>
      <div class="ala-maj" style="${c.alerte ? "color:var(--ala-loss);font-weight:600" : ""}">
        ${esc(maj.description || "")}${maj.date ? ` <span class="ala-menu">— ${esc(maj.date)}</span>` : ""}
      </div>
      <div class="ala-menu">
        ${s.url ? `<a href="${esc(s.url)}" target="_blank">${esc(c.reference)} ↗</a>` : esc(c.reference)}
        · ${bouton}
      </div>`;
  }
}
