/**
 * Écran « Commandes à traiter » — l'arriéré, article par article.
 *
 * Toute la décision est CÔTÉ SERVEUR (customization_app.commandes_a_traiter) :
 * l'écran n'invente ni le stock, ni les anomalies, ni les envois. Les messages
 * repassent par `sms_commandes` (mêmes balises, mêmes traces sur la commande)
 * pour qu'il n'existe qu'un seul chemin d'envoi dans l'app.
 */

frappe.pages["commandes-a-traiter"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Commandes à traiter",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("commandes_a_traiter", {})
  );
  new CommandesATraiter(wrapper);
};

const CT_PAGE_LENGTH = 50;

class CommandesATraiter {
  constructor(wrapper) {
    this.wrapper = wrapper;
    this.start = 0;
    this.data = null;
    // La sélection SURVIT aux changements de page et de filtre : on coche au
    // fil de l'analyse, on agit à la fin.
    this.selection = new Set();
    this.modeles = [];
    this._bind();
    this._init_filtres();
  }

  // ---------------------------------------------------------------- filtres

  _init_filtres() {
    frappe.call({
      method: "customization_app.commandes_a_traiter.get_filtres",
      callback: (r) => {
        const m = r.message || {};
        $("#ct-statut").append(
          (m.statuts || []).map((s) => `<option value="${s}">${s}</option>`).join(""));
        $("#ct-depuis").val(m.depuis_defaut || "2026-07-01");
        $("#ct-jusqua").val(frappe.datetime.get_today());
        this._load();
      },
    });
  }

  _filtres() {
    return {
      depuis: $("#ct-depuis").val(),
      jusqu_a: $("#ct-jusqua").val(),
      recherche: $("#ct-search").val() || "",
      statut: $("#ct-statut").val() || "",
      origine: $("#ct-origine").val() || "",
      dispo: $("#ct-dispo").val() || "",
      anomalie: $("#ct-anomalie").val() || "",
      tache: $("#ct-tache").val() || "",
    };
  }

  _bind() {
    let timer = null;
    $("#ct-search").on("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => { this.start = 0; this._load(); }, 400);
    });
    ["#ct-depuis", "#ct-jusqua", "#ct-statut", "#ct-origine", "#ct-dispo",
     "#ct-anomalie", "#ct-tache"].forEach((sel) =>
      $(sel).on("change", () => { this.start = 0; this._load(); }));

    $("#ct-clear").on("click", () => {
      $("#ct-search").val("");
      ["#ct-statut", "#ct-origine", "#ct-dispo", "#ct-anomalie", "#ct-tache"]
        .forEach((s) => $(s).val(""));
      this.start = 0;
      this._load();
    });

    $("#ct-prev").on("click", () => {
      if (this.start > 0) { this.start = Math.max(0, this.start - CT_PAGE_LENGTH); this._load(); }
    });
    $("#ct-next").on("click", () => {
      if (this.data && this.start + CT_PAGE_LENGTH < this.data.total) {
        this.start += CT_PAGE_LENGTH; this._load();
      }
    });

    $(this.wrapper).on("change", ".ct-sel", (e) => {
      const nom = $(e.currentTarget).data("cde");
      if (e.currentTarget.checked) this.selection.add(nom);
      else this.selection.delete(nom);
      this._maj_compte();
    });
    $("#ct-all").on("change", (e) => {
      const coche = e.currentTarget.checked;
      (this.data ? this.data.lignes : []).forEach((c) => {
        if (coche) this.selection.add(c.name); else this.selection.delete(c.name);
      });
      $(this.wrapper).find(".ct-sel").prop("checked", coche);
      this._maj_compte();
    });

    $("#ct-msg").on("click", () => this._dialogue_message());
    $("#ct-rdv").on("click", () => this._dialogue_message("rdv"));
    $("#ct-annuler").on("click", () => this._dialogue_annuler());
  }

  // ---------------------------------------------------------------- données

  _load() {
    $("#ct-body").html(`<tr><td colspan="8" class="ct-vide">Chargement…</td></tr>`);
    frappe.call({
      method: "customization_app.commandes_a_traiter.get_commandes",
      args: Object.assign(this._filtres(), {
        start: this.start, page_length: CT_PAGE_LENGTH,
      }),
      callback: (r) => {
        this.data = r.message || { lignes: [], total: 0, kpis: {} };
        this._rendre();
      },
    });
  }

  _rendre() {
    const k = this.data.kpis || {};
    $("#ct-kpis").html([
      ["Commandes", k.commandes || 0, ""],
      ["Rupture réelle", k.manque_reel || 0, k.manque_reel ? "alerte" : ""],
      ["Stock négatif à corriger", k.stock_negatif || 0, ""],
      ["Sans tâche", k.sans_tache || 0, ""],
      ["Anomalies", k.anomalies || 0, k.anomalies ? "alerte" : ""],
      ["Total TTC", format_currency(k.ttc || 0, "TND"), ""],
    ].map(([l, v, cls]) =>
      `<div class="ct-kpi ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`
    ).join(""));

    const lignes = this.data.lignes || [];
    const esc = frappe.utils.escape_html;
    $("#ct-body").html(lignes.length ? lignes.map((c) => {
      const articles = (c.articles || []).map((a) => {
        // Un stock NÉGATIF n'est pas une rupture : c'est un inventaire faux.
        // Le dire autrement évite d'annuler une commande dont on a l'article.
        const badge = !a.stocke
          ? `<span class="ct-badge b-svc">service</span>`
          : a.stock_negatif
            ? `<span class="ct-badge b-warn" title="Le stock enregistré est négatif : l'inventaire de cet article est faux, à corriger avant de décider">🩺 stock négatif (${a.stock})</span>`
            : a.manque
              ? `<span class="ct-badge b-ko">manque · dispo ${a.dispo}</span>`
              : `<span class="ct-badge b-ok">stock ${a.dispo}</span>`;
        return `<div class="ct-art"><b>${a.qte}×</b>
            <span class="nom" title="${esc(a.code)} — ${esc(a.article)}">${esc(a.article)}</span>
            ${badge}</div>`;
      }).join("") || `<span class="ct-sub">—</span>`;

      const taches = (c.taches || []).map((t) =>
        `<div><a href="/app/tache-de-travail/${encodeURIComponent(t.tache)}"
             class="ct-badge b-info" target="_blank">🛠️ ${esc(t.type || "?")} · ${esc(t.statut || "")}</a>
           ${t.date ? `<span class="ct-sub">${esc(String(t.date).slice(0, 16))}</span>` : ""}</div>`
      ).join("") || `<span class="ct-badge b-warn">aucune tâche</span>`;

      return `<tr class="${c.manques_reels ? "manque" : ""}">
        <td><input type="checkbox" class="ct-sel" data-cde="${esc(c.name)}"
              ${this.selection.has(c.name) ? "checked" : ""}></td>
        <td><a href="/app/sales-order/${encodeURIComponent(c.name)}" target="_blank"
              class="ct-cde">${esc(c.name)}</a>
            <div class="ct-sub">${esc(c.date)} · ${esc(c.statut)}
              ${c.web ? `<span class="ct-badge b-web">🌐 web</span>` : ""}</div></td>
        <td><a href="/app/customer/${encodeURIComponent(c.client)}" target="_blank">${esc(c.client_nom)}</a>
            <div class="ct-sub">${c.telephone
              ? `📞 <a href="tel:${esc(c.telephone)}">${esc(c.telephone)}</a>`
              : "sans numéro"}</div></td>
        <td class="ct-adr">${esc(c.adresse || "—")}</td>
        <td>${articles}</td>
        <td>${taches}</td>
        <td>${c.anomalie ? `<span class="ct-badge b-ko">${esc(c.anomalie)}</span>` : ""}
            ${c.bordereau ? `<div class="ct-sub">🚚 ${esc(c.bordereau)}</div>` : ""}</td>
        <td class="num">${format_currency(c.ttc, c.devise)}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="8" class="ct-vide">Aucune commande ne correspond aux filtres.</td></tr>`);

    const fin = Math.min(this.start + CT_PAGE_LENGTH, this.data.total);
    $("#ct-range").text(this.data.total
      ? `${this.start + 1}–${fin} sur ${this.data.total}` : "—");
    $("#ct-all").prop("checked", false);
    this._maj_compte();
  }

  _maj_compte() {
    const n = this.selection.size;
    $("#ct-compte").text(n
      ? `${n} commande(s) sélectionnée(s)`
      : "Aucune commande sélectionnée");
  }

  _selection() {
    if (!this.selection.size) {
      frappe.msgprint(__("Cochez d'abord au moins une commande."));
      return null;
    }
    return Array.from(this.selection);
  }

  // ---------------------------------------------------------------- actions

  _dialogue_message(preset) {
    const noms = this._selection();
    if (!noms) return;
    let modeles = [];

    const d = new frappe.ui.Dialog({
      title: __("Message aux clients — {0} commande(s)", [noms.length]),
      size: "large",
      fields: [
        { fieldtype: "HTML", fieldname: "totaux" },
        {
          fieldtype: "Select", fieldname: "choix_modele",
          label: __("Modèle prédéfini"),
          description: __("Choisir un modèle remplit le message — il reste modifiable."),
        },
        {
          fieldtype: "Small Text", fieldname: "message", reqd: 1,
          label: __("Message (SMS et corps de l'e-mail)"),
          description: __("Balises : {nom_client} {commande} {article} {articles} {total_ttc} {lien_rdv} {signature}"),
        },
        { fieldtype: "Section Break", label: __("Articles de remplacement à proposer") },
        {
          fieldtype: "MultiSelectPills", fieldname: "remplacements",
          label: __("Articles en stock"),
          description: __("Choix manuel — seuls les articles ayant du stock sont proposés."),
          get_data: (txt) => frappe.call({
            method: "customization_app.commandes_a_traiter.chercher_articles",
            args: { recherche: txt, en_stock: 1 },
          }).then((r) => (r.message || []).map((a) => ({
            value: a.code, description: `${a.article} — stock ${a.stock}`,
          }))),
        },
        {
          fieldtype: "Button", fieldname: "inserer",
          label: __("➕ Insérer les articles dans le message"),
        },
        { fieldtype: "Column Break" },
        {
          fieldtype: "Check", fieldname: "sms", default: 1,
          label: __("Envoyer par SMS (Liste Telephone du client)"),
        },
        {
          fieldtype: "Check", fieldname: "email", default: 1,
          label: __("Envoyer par e-mail (contacts du client)"),
        },
        {
          fieldtype: "Data", fieldname: "sujet", label: __("Objet de l'e-mail"),
          depends_on: "email",
        },
        { fieldtype: "Section Break", label: __("Aperçu") },
        { fieldtype: "HTML", fieldname: "apercu" },
      ],
      primary_action_label: __("Envoyer"),
      primary_action: (v) => this._envoyer(d, noms, v),
    });

    // Les articles choisis s'écrivent DANS le message : pas de balise cachée,
    // l'utilisateur voit exactement ce que le client recevra.
    d.fields_dict.inserer.$input.on("click", () => {
      const choisis = d.get_value("remplacements") || [];
      if (!choisis.length) {
        frappe.msgprint(__("Choisissez d'abord un ou plusieurs articles."));
        return;
      }
      const texte = (d.get_value("message") || "").replace(/\s*$/, "")
        + "\n\nNous vous proposons en remplacement : " + choisis.join(", ") + ".";
      Promise.resolve(d.fields_dict.message.set_value(texte))
        .then(() => this._apercu(d, noms));
    });

    d.fields_dict.choix_modele.$input.on("change", function () {
      const m = modeles.find((x) => x.libelle === $(this).val());
      if (!m) return;
      Promise.resolve(d.fields_dict.message.set_value(m.texte))
        .then(() => d.__rafraichir());
    });
    d.fields_dict.message.$input.on("input", frappe.utils.debounce(
      () => this._apercu(d, noms), 400));
    d.__rafraichir = () => this._apercu(d, noms);

    d.show();
    this._apercu(d, noms, (m) => {
      modeles = m.modeles || [];
      this.modeles = modeles;
      d.fields_dict.choix_modele.df.options =
        [""].concat(modeles.map((x) => x.libelle)).join("\n");
      d.fields_dict.choix_modele.refresh();
      // Bouton « Lien de rendez-vous » : on part du modèle qui porte {lien_rdv}.
      if (preset === "rdv") {
        const rdv = modeles.find((x) => (x.texte || "").includes("{lien_rdv}"));
        if (rdv) {
          d.set_value("choix_modele", rdv.libelle);
          Promise.resolve(d.fields_dict.message.set_value(rdv.texte))
            .then(() => this._apercu(d, noms));
        }
      }
    });
  }

  _apercu(d, noms, apres) {
    frappe.call({
      method: "customization_app.sms_commandes.apercu",
      args: { noms: JSON.stringify(noms), modele: d.get_value("message") || "" },
      callback: (r) => {
        const m = r.message || {};
        const t = m.totaux || {};
        const esc = frappe.utils.escape_html;
        d.fields_dict.totaux.$wrapper.html(
          `<div style="padding:8px 10px;border-radius:8px;background:var(--bg-light-gray,#f6f7f9);
                font-size:12.5px">
             <b>${t.commandes || 0}</b> commande(s) · <b>${t.clients || 0}</b> client(s) ·
             📱 <b>${t.numeros || 0}</b> numéro(s) · ✉️ <b>${t.emails || 0}</b> e-mail(s)
             ${t.sans_numero ? ` · <span style="color:#b45309">${t.sans_numero} sans numéro</span>` : ""}
             ${t.sans_email ? ` · <span style="color:#b45309">${t.sans_email} sans e-mail</span>` : ""}
           </div>`);
        d.fields_dict.apercu.$wrapper.html(
          `<div style="max-height:220px;overflow:auto;font-size:12px">${
            (m.lignes || []).map((l) => `<div style="padding:6px 0;
                 border-bottom:1px solid var(--border-color,#eee)">
               <b>${esc(l.nom_client)}</b> · ${esc(l.commande)}<br>
               <span style="color:var(--text-muted)">📱 ${l.numeros.length ? esc(l.numeros.join(", ")) : "—"}
                 · ✉️ ${l.emails.length ? esc(l.emails.join(", ")) : "—"}</span>
               ${l.message ? `<div style="margin-top:3px;white-space:pre-wrap">${esc(l.message)}</div>` : ""}
             </div>`).join("")
          }</div>`);
        apres && apres(m);
      },
    });
  }

  _envoyer(d, noms, v) {
    if (!(v.message || "").trim()) {
      frappe.msgprint(__("Écrivez le message à envoyer."));
      return;
    }
    if (!v.sms && !v.email) {
      frappe.msgprint(__("Choisissez au moins un canal : SMS ou e-mail."));
      return;
    }
    frappe.confirm(
      __("Envoyer ce message pour {0} commande(s) ?<br>Les SMS partent vers de VRAIS clients.",
         [noms.length]),
      () => frappe.call({
        method: "customization_app.sms_commandes.envoyer",
        args: {
          noms: JSON.stringify(noms), modele: v.message, sujet: v.sujet,
          sms: v.sms ? 1 : 0, email: v.email ? 1 : 0,
        },
        freeze: true,
        freeze_message: __("Envoi en cours…"),
        callback: (r) => {
          const m = r.message || {};
          d.hide();
          if (m.differe) {
            frappe.show_alert({
              message: __("Envoi lancé pour {0} commande(s) — la progression s'affiche ici.",
                          [m.commandes]), indicator: "blue" }, 10);
            return;
          }
          frappe.msgprint({
            title: __("Envoi terminé"),
            indicator: m.echecs ? "orange" : "green",
            message: __("📱 {0} SMS · ✉️ {1} e-mail(s) · {2} échec(s)",
                        [m.sms_envoyes || 0, m.emails_envoyes || 0, m.echecs || 0]),
          });
        },
      })
    );
  }

  _dialogue_annuler() {
    const noms = this._selection();
    if (!noms) return;
    const d = new frappe.ui.Dialog({
      title: __("Annuler {0} commande(s)", [noms.length]),
      fields: [
        {
          fieldtype: "HTML", fieldname: "avert",
          options: `<div style="padding:10px 12px;border-radius:9px;background:#fef3c7;
                      color:#92400e;font-size:12.5px">
                      ⚠️ L'annulation déclenche la cascade habituelle : bons de livraison,
                      échéancier et tâches liées. Un <b>brouillon</b> n'est pas annulé
                      (il se supprime, il ne s'annule pas) — il sera signalé.
                    </div>`,
        },
        {
          fieldtype: "Small Text", fieldname: "motif", reqd: 1,
          label: __("Motif (tracé en commentaire sur chaque commande)"),
        },
      ],
      primary_action_label: __("Annuler ces commandes"),
      primary_action: (v) => {
        frappe.confirm(
          __("Confirmer l'annulation de {0} commande(s) ? Cette action est difficile à défaire.",
             [noms.length]),
          () => frappe.call({
            method: "customization_app.commandes_a_traiter.annuler",
            args: { noms: JSON.stringify(noms), motif: v.motif },
            freeze: true, freeze_message: __("Annulation en cours…"),
            callback: (r) => {
              d.hide();
              const esc = frappe.utils.escape_html;
              frappe.msgprint({
                title: __("Résultat de l'annulation"),
                message: (r.message || []).map((x) =>
                  `<div><b>${esc(x.commande)}</b> — ${esc(x.etat)}</div>`).join(""),
              });
              this.selection.clear();
              this._load();
            },
          })
        );
      },
    });
    d.show();
  }
}
