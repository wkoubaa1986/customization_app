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

/** Ce qui est réellement parti sur une commande — infobulle de la pastille de relance.
 *  Un envoi peut n'avoir touché personne (ni numéro ni e-mail au dossier) : le dire
 *  vaut mieux que laisser croire que le client a été prévenu. */
function canaux(e) {
  const l = [];
  if (e.sms) l.push("SMS");
  if (e.email) l.push("e-mail");
  return l.length ? l.join(" et ") : "envoi tenté sans destinataire";
}

/** Les articles d'une commande avec leur verdict de stock.
 *  Partagé par la ligne du tableau et la fenêtre « commandes du client » : deux
 *  rendus séparés finiraient par annoncer des verdicts différents pour la même
 *  commande, à quelques centimètres l'un de l'autre. */
function rendre_articles(articles) {
  const esc = frappe.utils.escape_html;
  return (articles || []).map((a) => {
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
}

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
        // Secteurs en CASES À COCHER : on travaille souvent sur deux ou trois
        // secteurs voisins à la fois (une tournée), et une liste déroulante
        // obligeait à repasser l'écran secteur par secteur.
        // « — sans secteur » reste dans le lot : ces adresses sont justement
        // celles qu'on veut pouvoir isoler pour les corriger.
        $("#ct-secteurs").html(
          [{ v: "__vide__", t: "— sans secteur" }]
            .concat((m.secteurs || []).map((s) => ({ v: s, t: s })))
            .map((s) =>
              `<label><input type="checkbox" class="ct-sect"
                 value="${frappe.utils.escape_html(s.v)}" checked>
                 ${frappe.utils.escape_html(s.t)}</label>`).join(""));
        // Tous cochés au départ : le filtre n'enlève rien tant qu'on n'a
        // pas décidé de retirer un type. Plusieurs cases peuvent être cochées
        // en même temps — c'est une multi-sélection.
        $("#ct-groupes").html((m.groupes || []).length
          ? (m.groupes || []).map((g) =>
              `<label><input type="checkbox" class="ct-grp" value="${frappe.utils.escape_html(g.valeur)}" checked>
                 ${frappe.utils.escape_html(g.libelle)} <span class="n">(${g.n})</span></label>`).join("")
          // Un bloc vide sans explication laisserait croire à un bug d'affichage.
          : `<span class="n">Types de clients indisponibles — rechargez la page (Ctrl+Maj+R).</span>`);
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
      secteur: $("#ct-secteurs .ct-sect").length
        ? JSON.stringify($("#ct-secteurs .ct-sect:checked").map((i, e) => e.value).get())
        : "",
      livraison: $("#ct-livraison").val() || "",
      envoi: $("#ct-envoi").val() || "",
      prestation: $("#ct-prestation").val() || "",
      client: $("#ct-client").val() || "",
      // Aucune case affichée (types pas encore chargés) => on n'envoie RIEN,
      // donc aucun filtre : un écran vide par accident serait pire que tout.
      groupes: $("#ct-groupes .ct-grp").length
        ? JSON.stringify($("#ct-groupes .ct-grp:checked").map((i, e) => e.value).get())
        : "",
      tri: $("#ct-tri").val() || "date_asc",
    };
  }

  _bind() {
    let timer = null;
    $("#ct-search").on("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => { this.start = 0; this._load(); }, 400);
    });
    ["#ct-depuis", "#ct-jusqua", "#ct-statut", "#ct-origine", "#ct-dispo",
     "#ct-anomalie", "#ct-tache", "#ct-livraison", "#ct-envoi",
     "#ct-prestation", "#ct-client", "#ct-tri"].forEach((sel) =>
      $(sel).on("change", () => { this.start = 0; this._load(); }));
    // Les cases de secteur sont créées APRÈS ce branchement (elles attendent la
    // liste du serveur) : on écoute donc le conteneur, pas les cases.
    $("#ct-secteurs").on("change", ".ct-sect", () => { this.start = 0; this._load(); });

    $("#ct-clear").on("click", () => {
      $("#ct-search").val("");
      ["#ct-statut", "#ct-origine", "#ct-dispo", "#ct-anomalie", "#ct-tache",
       "#ct-livraison", "#ct-envoi", "#ct-prestation", "#ct-client"]
        .forEach((s) => $(s).val(""));
      $("#ct-groupes .ct-grp, #ct-secteurs .ct-sect").prop("checked", true);
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

    $(this.wrapper).on("change", ".ct-grp", () => { this.start = 0; this._load(); });
    $("#ct-groupes-tous").on("click", () => {
      $("#ct-groupes .ct-grp").prop("checked", true); this.start = 0; this._load();
    });
    $("#ct-secteurs-tous").on("click", () => {
      $("#ct-secteurs .ct-sect").prop("checked", true); this.start = 0; this._load();
    });
    $("#ct-secteurs-aucun").on("click", () => {
      $("#ct-secteurs .ct-sect").prop("checked", false); this.start = 0; this._load();
    });
    $("#ct-groupes-aucun").on("click", () => {
      $("#ct-groupes .ct-grp").prop("checked", false); this.start = 0; this._load();
    });

    $(this.wrapper).on("click", ".ct-multi", (e) =>
      this._dialogue_client($(e.currentTarget).data("client")));

    $(this.wrapper).on("click", ".ct-vider", () => this._vider_selection());

    $("#ct-msg").on("click", () => this._dialogue_message());
    $("#ct-rdv").on("click", () => this._dialogue_message("rdv"));
    $("#ct-livr").on("click", () => this._dialogue_livraison());
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
        // On garde les articles de chaque commande VUE : la sélection survit
        // aux pages, le dialogue doit pouvoir les proposer même après un
        // changement de filtre.
        this._infos_commandes = this._infos_commandes || {};
        (this.data.lignes || []).forEach((c) => {
          this._infos_commandes[c.name] = c.articles || [];
        });
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
      ["Livraison équipe", k.livraison_equipe || 0, ""],
      ["Clients à plusieurs commandes", k.clients_multi || 0, ""],
      ["Anomalies", k.anomalies || 0, k.anomalies ? "alerte" : ""],
      ["Total TTC", format_currency(k.ttc || 0, "TND"), ""],
    ].map(([l, v, cls]) =>
      `<div class="ct-kpi ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`
    ).join(""));

    const lignes = this.data.lignes || [];
    const esc = frappe.utils.escape_html;
    $("#ct-body").html(lignes.length ? lignes.map((c) => {
      const articles = rendre_articles(c.articles);

      // Ce qu'il y a À FAIRE sur la commande, lu des groupes d'articles.
      const prestation = c.a_livraison || c.a_main_oeuvre
        ? `<div style="margin-top:3px">${c.a_livraison ? `<span class="ct-badge b-info">🚚 livraison</span> ` : ""}${
             c.a_main_oeuvre ? `<span class="ct-badge b-info">🔧 main d’œuvre</span>` : ""}</div>`
        : `<div style="margin-top:3px"><span class="ct-badge b-svc">📦 sans intervention</span></div>`;

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
              ${c.web ? `<span class="ct-badge b-web">🌐 web</span>` : ""}
              ${c.livraison_equipe ? `<span class="ct-badge b-ok" title="Le client peut réserver un créneau de livraison en ligne">🚚 livraison équipe</span>` : ""}</div>
            ${c.envoi ? `<div><span class="ct-badge b-svc"
                 title="${esc(canaux(c.envoi))} — dernier envoi le ${esc(c.envoi.dernier)} par ${esc(c.envoi.par)}"
                 >📨 relancé${c.envoi.n > 1 ? ` ×${c.envoi.n}` : ""} · ${esc(String(c.envoi.dernier).slice(0, 10))}</span></div>`
              : ""}</td>
        <td><a href="/app/customer/${encodeURIComponent(c.client)}" target="_blank">${esc(c.client_nom)}</a>
            <div class="ct-sub">${c.telephone
              ? `📞 <a href="tel:${esc(c.telephone)}">${esc(c.telephone)}</a>`
              : "sans numéro"}${c.groupe_client
                 ? ` · ${esc(c.groupe_client)}` : ""}</div>
            ${c.commandes_client > 1
              ? `<div><span class="ct-badge b-warn ct-multi" data-client="${esc(c.client)}"
                     style="cursor:pointer" title="Ouvrir les ${c.commandes_client} commandes de ce client sur la période"
                     >🧾 ${c.commandes_client} commandes</span></div>` : ""}</td>
        <td class="ct-adr">${esc(c.adresse || "—")}
            <div>${c.secteur
              ? `<span class="ct-badge b-info">📍 ${esc(c.secteur)}</span>`
              : `<span class="ct-badge b-warn">📍 sans secteur</span>`}</div></td>
        <td>${articles}${prestation}</td>
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
    if (!n) {
      $("#ct-compte").html("Aucune commande sélectionnée");
      return;
    }
    // ⚠️ La sélection SURVIT aux pages et aux filtres. Une commande cochée puis
    // sortie de l'écran reste retenue : si on ne le dit pas, l'action porte sur
    // des commandes qu'on ne voit plus (« j'en ai coché une, il en traite 3 »).
    const visibles = new Set((this.data ? this.data.lignes : []).map((c) => c.name));
    const caches = Array.from(this.selection).filter((x) => !visibles.has(x)).length;
    $("#ct-compte").html(
      `<b>${n}</b> commande(s) sélectionnée(s)`
      + (caches ? ` <span style="color:#b45309">(dont ${caches} hors de l’écran)</span>` : "")
      + ` <a class="ct-vider" style="cursor:pointer;margin-left:6px">✕ vider</a>`);
  }

  _vider_selection() {
    this.selection.clear();
    $(this.wrapper).find(".ct-sel").prop("checked", false);
    $("#ct-all").prop("checked", false);
    this._maj_compte();
  }

  // ------------------------------------------- les commandes d'un même client

  /** Le badge « N commandes » ouvre la LISTE de ces commandes, au lieu de
   *  filtrer l'écran (demande 01/09/2026).
   *
   *  Pourquoi c'est mieux qu'un filtre : on regarde un client parce qu'on
   *  soupçonne un doublon ou une commande en plusieurs fois — on veut COMPARER,
   *  pas naviguer. Filtrer effaçait la recherche en cours et faisait perdre la
   *  place dans l'arriéré ; il fallait ensuite tout remonter pour reprendre.
   *
   *  Les lignes viennent du serveur, calculées comme celles de la liste : une
   *  fenêtre qui recomposerait le stock à sa façon finirait par contredire la
   *  ligne juste derrière elle.
   */
  _dialogue_client(client) {
    const esc = frappe.utils.escape_html;
    const d = new frappe.ui.Dialog({
      title: __("Commandes du client"),
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "corps" }],
    });
    d.fields_dict.corps.$wrapper.html(
      `<div class="ct-vide">${__("Chargement…")}</div>`);
    d.show();

    frappe.call({
      method: "customization_app.commandes_a_traiter.get_commandes_client",
      args: { client, depuis: $("#ct-depuis").val(), jusqu_a: $("#ct-jusqua").val() },
      callback: (r) => {
        const m = r.message || {};
        const lignes = m.lignes || [];
        d.set_title(__("{0} — {1} commande(s)", [m.client_nom || client, lignes.length]));
        if (!lignes.length) {
          d.fields_dict.corps.$wrapper.html(
            `<div class="ct-vide">${__("Aucune commande sur la période affichée.")}</div>`);
          return;
        }
        const corps = lignes.map((c) => {
          const articles = rendre_articles(c.articles);
          const taches = (c.taches || []).map((t) =>
            `<span class="ct-badge b-info">🛠️ ${esc(t.type || "?")} · ${esc(t.statut || "")}</span>`
          ).join(" ") || `<span class="ct-badge b-warn">aucune tâche</span>`;
          return `<tr>
            <td><a href="/app/sales-order/${encodeURIComponent(c.name)}" target="_blank"
                  class="ct-cde">${esc(c.name)}</a>
                <div class="ct-sub">${esc(c.date)} · ${esc(c.statut)}
                  ${c.web ? `<span class="ct-badge b-web">🌐 web</span>` : ""}</div>
                ${c.envoi ? `<div><span class="ct-badge b-svc"
                     title="${esc(canaux(c.envoi))} — le ${esc(c.envoi.dernier)}"
                     >📨 relancé</span></div>` : ""}</td>
            <td class="ct-adr">${esc(c.adresse || "—")}
                <div>${c.secteur
                  ? `<span class="ct-badge b-info">📍 ${esc(c.secteur)}</span>`
                  : `<span class="ct-badge b-warn">📍 sans secteur</span>`}</div></td>
            <td>${articles}</td>
            <td>${taches}</td>
            <td>${c.anomalie ? `<span class="ct-badge b-ko">${esc(c.anomalie)}</span>` : ""}</td>
            <td class="num">${format_currency(c.ttc, c.devise)}</td>
          </tr>`;
        }).join("");

        d.fields_dict.corps.$wrapper.html(`
          <div style="max-height:60vh;overflow:auto">
            <table class="ct-tbl" style="margin:0">
              <thead><tr>
                <th>${__("Commande")}</th><th>${__("Adresse")}</th>
                <th>${__("Articles & stock")}</th><th>${__("Tâches")}</th>
                <th>${__("Anomalie")}</th><th class="num">${__("TTC")}</th>
              </tr></thead>
              <tbody>${corps}</tbody>
            </table>
          </div>
          <div style="display:flex;align-items:center;gap:10px;margin-top:10px">
            <b>${__("Total")} : ${format_currency(m.total_ttc, m.devise)}</b>
            <button class="btn btn-xs btn-default" id="ct-cli-isoler"
              style="margin-left:auto">🔍 ${__("Isoler dans la liste")}</button>
          </div>`);

        // Le filtrage reste accessible d'un clic pour qui veut ENSUITE agir sur
        // ces commandes : la sélection, les envois et l'annulation vivent dans
        // l'écran, pas dans la fenêtre.
        d.fields_dict.corps.$wrapper.find("#ct-cli-isoler").on("click", () => {
          d.hide();
          $("#ct-search").val(client);
          this.start = 0;
          this._load();
        });
      },
    });
  }

  _selection() {
    if (!this.selection.size) {
      frappe.msgprint(__("Cochez d'abord au moins une commande."));
      return null;
    }
    return Array.from(this.selection);
  }

  // Le rappel de CE QUI EST VISÉ, en tête de chaque dialogue : la sélection
  // peut contenir des commandes qui ne sont plus affichées.
  _rappel_selection(noms) {
    const esc = frappe.utils.escape_html;
    return `<div style="padding:8px 10px;border-radius:9px;background:var(--bg-light-gray,#f6f7f9);
              font-size:12.5px">
              Concerne <b>${noms.length}</b> commande(s) :
              <span style="color:var(--text-muted)">${noms.map(esc).join(", ")}</span>
            </div>`;
  }

  // ---------------------------------------------------------------- actions

  _dialogue_message(preset) {
    const noms = this._selection();
    if (!noms) return;
    let modeles = [];

    const d = new frappe.ui.Dialog({
      title: __("Message aux clients — {0} commande(s)", [noms.length]),
      size: "extra-large",
      fields: [
        { fieldtype: "HTML", fieldname: "visees", options: this._rappel_selection(noms) },
        { fieldtype: "HTML", fieldname: "totaux" },
        {
          fieldtype: "Select", fieldname: "choix_modele",
          label: __("Modèle prédéfini"),
          description: __("Choisir un modèle remplit le message — il reste modifiable."),
        },
        {
          fieldtype: "Small Text", fieldname: "message", reqd: 1,
          label: __("Message (SMS et corps de l'e-mail)"),
          description: __("Balises : {nom_client} {commande} {article} {articles} {total_ttc} {lien_rdv} {remplacements} {signature}"),
        },
        { fieldtype: "Section Break",
          label: __("Article de remplacement commun (applicable à toute la sélection)") },
        {
          fieldtype: "MultiSelectPills", fieldname: "remplacements",
          label: __("Article de remplacement"),
          description: __("Choix commun, ajustable ensuite commande par commande. Tout article actif non marqué « rupture de stock site web »."),
          get_data: (txt) => frappe.call({
            method: "customization_app.commandes_a_traiter.chercher_articles",
            args: { recherche: txt },
          }).then((r) => {
            // On garde le détail (nom, lien boutique) de côté : la pastille ne
            // porte que le code, mais le message doit contenir le vrai nom et
            // le lien vers la fiche du site.
            this._articles_connus = this._articles_connus || {};
            (r.message || []).forEach((a) => { this._articles_connus[a.code] = a; });
            return (r.message || []).map((a) => ({
              value: a.code,
              description: `${a.article} — stock ${a.stock}${a.lien ? " · 🔗 site" : ""}`
                + (a.stock <= 0 ? " · ⚠️ rien en magasin" : ""),
            }));
          }),
        },
        {
          fieldtype: "Button", fieldname: "inserer",
          label: __("➕ Appliquer à toutes les commandes"),
        },
        {
          fieldtype: "Check", fieldname: "annuler", default: 0,
          label: __("❌ Annuler aussi ces commandes"),
          description: __("L'annulation a lieu D'ABORD ; seuls les clients dont la commande a bien été annulée reçoivent le message."),
        },
        {
          fieldtype: "Small Text", fieldname: "motif", depends_on: "annuler",
          label: __("Motif de l'annulation (tracé sur chaque commande)"),
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
        // Pleine largeur : dans la colonne de gauche, les articles de chaque
        // commande se repliaient sur six lignes et devenaient illisibles.
        { fieldtype: "Section Break",
          label: __("Choix par commande — ✏️ pour changer un article") },
        { fieldtype: "HTML", fieldname: "par_commande" },
        { fieldtype: "Section Break", label: __("Aperçu") },
        { fieldtype: "HTML", fieldname: "apercu" },
      ],
      primary_action_label: __("Envoyer"),
      primary_action: (v) => this._envoyer(d, noms, v),
    });

    // Les articles choisis s'écrivent DANS le message : pas de balise cachée,
    // l'utilisateur voit exactement ce que le client recevra.
    // Chaque commande a SES articles : proposer le même remplacement à tout le
    // monde n'a pas de sens dès que les commandes portent des articles
    // différents (demande 30/08). Le sélecteur du haut sert de choix COMMUN,
    // qu'on ajuste ensuite ligne par ligne.
    this._remplacements = {};

    d.fields_dict.inserer.$input.on("click", () => {
      const choisis = d.get_value("remplacements") || [];
      if (!choisis.length) {
        frappe.msgprint(__("Choisissez d'abord un ou plusieurs articles."));
        return;
      }
      noms.forEach((n) => {
        const dedans = (this._infos_commandes || {})[n] || [];
        const souci = dedans.find((a) => a.manque || a.stock_negatif);
        this._remplacements[n] = {
          remplace: (this._remplacements[n] || {}).remplace
                    || (souci ? souci.code : ""),
          par: choisis.slice(),
        };
      });
      this._poser_balise(d);
      this._rendre_par_commande(d, noms);
      this._apercu(d, noms);
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
    // Visible D'EMBLÉE : tant que la table n'apparaissait qu'après « Appliquer
    // à toutes », personne ne pouvait deviner qu'un choix par commande existait.
    this._rendre_par_commande(d, noms);
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

  // La balise {remplacements} est rendue PAR COMMANDE côté serveur : on la
  // pose une fois dans le message, chaque client reçoit ses propres articles.
  _poser_balise(d) {
    const actuel = d.get_value("message") || "";
    if (actuel.includes("{remplacements}")) return;
    const texte = actuel.includes("{signature}")
      ? actuel.replace("{signature}", "{remplacements}\n\n{signature}")
      : actuel.replace(/\s*$/, "") + "\n\n{remplacements}";
    d.fields_dict.message.set_value(texte);
  }

  _rendre_par_commande(d, noms) {
    const esc = frappe.utils.escape_html;
    const connus = this._articles_connus || {};
    const nom_lisible = (code) => (connus[code] || {}).article || code;
    d.fields_dict.par_commande.$wrapper.html(
      `<div style="font-size:11.5px;color:var(--text-muted,#6b7280);margin-bottom:4px">
         ${__("Cliquez ✏️ sur une ligne pour proposer un article différent à ce client.")}
       </div>
       <div style="max-height:280px;overflow:auto;font-size:12px;border:1px solid
            var(--border-color,#e4e8ee);border-radius:8px">
         <table style="width:100%;table-layout:fixed">
           <thead><tr style="background:var(--bg-light-gray,#f6f8fa);
                 font-size:10.5px;text-transform:uppercase;color:#6b7280">
             <th style="padding:5px 8px;width:150px;text-align:left">Commande</th>
             <th style="padding:5px 8px;text-align:left">Articles commandés</th>
             <th style="padding:5px 8px;text-align:left">Remplacement</th>
             <th style="padding:5px 8px;width:110px"></th></tr></thead>${noms.map((n) => {
           const choix = this._remplacements[n] || {};
           const par = choix.par || [];
           const dedans = (this._infos_commandes || {})[n] || [];
           const nom_origine = (code) =>
             (dedans.find((a) => a.code === code) || {}).article || code;
           return `<tr style="border-bottom:1px solid var(--border-color,#eef1f5)">
             <td style="padding:5px 8px;vertical-align:top;white-space:nowrap">
               <b>${esc(n)}</b></td>
             <td style="padding:5px 8px;vertical-align:top;overflow-wrap:anywhere;
                        color:var(--text-muted,#6b7280)">
               ${dedans.length
                  ? dedans.map((a) => `${a.manque || a.stock_negatif ? "⚠️ " : ""}${
                      esc(a.article)}`).join("<br>")
                  : "—"}</td>
             <td style="padding:5px 8px;vertical-align:top;overflow-wrap:anywhere">${par.length
                ? `${choix.remplace
                     ? `<span style="color:#b02a37">${esc(nom_origine(choix.remplace))}</span> → ` : ""}
                   <b>${par.map((c) => esc(nom_lisible(c))).join(", ")}</b>`
                : `<span style="color:#b45309">aucun remplacement</span>`}</td>
             <td style="padding:5px 8px;text-align:right;vertical-align:top">
               <button type="button" class="btn btn-xs btn-default ct-modif-rempl"
                 data-cde="${esc(n)}">✏️ modifier</button></td></tr>`;
         }).join("")}</table></div>`);
    d.fields_dict.par_commande.$wrapper.find(".ct-modif-rempl").on("click", (e) =>
      this._dialogue_articles_commande(d, noms, $(e.currentTarget).data("cde")));
  }

  // Choix des articles pour UNE commande, dans une petite fenêtre dédiée.
  _dialogue_articles_commande(parent, noms, commande) {
    const p = new frappe.ui.Dialog({
      title: __("Articles de remplacement — {0}", [commande]),
      fields: [{
        fieldtype: "Select", fieldname: "remplace",
        label: __("Article à remplacer (dans la commande)"),
        options: [""].concat(((this._infos_commandes || {})[commande] || [])
          .map((a) => a.code)).join("\n"),
        description: __("⚠️ signale un article en rupture ou à stock négatif."),
      }, {
        fieldtype: "MultiSelectPills", fieldname: "articles",
        label: __("Remplacé par"),
        get_data: (txt) => frappe.call({
          method: "customization_app.commandes_a_traiter.chercher_articles",
          args: { recherche: txt },
        }).then((r) => {
          this._articles_connus = this._articles_connus || {};
          (r.message || []).forEach((a) => { this._articles_connus[a.code] = a; });
          return (r.message || []).map((a) => ({
            value: a.code,
            description: `${a.article} — stock ${a.stock}${a.lien ? " · 🔗 site" : ""}`,
          }));
        }),
      }],
      primary_action_label: __("Valider"),
      primary_action: (v) => {
        this._remplacements[commande] = {
          remplace: v.remplace || "", par: v.articles || [],
        };
        p.hide();
        this._poser_balise(parent);
        this._rendre_par_commande(parent, noms);
        this._apercu(parent, noms);
      },
    });
    p.show();
    const actuel = this._remplacements[commande] || {};
    p.set_value("articles", (actuel.par || []).slice());
    // Par défaut, l'article qui pose problème : c'est celui qu'on remplace
    // neuf fois sur dix.
    const dedans = (this._infos_commandes || {})[commande] || [];
    const souci = dedans.find((a) => a.manque || a.stock_negatif);
    p.set_value("remplace", actuel.remplace || (souci ? souci.code : ""));
  }

  _apercu(d, noms, apres) {
    frappe.call({
      method: "customization_app.sms_commandes.apercu",
      args: { noms: JSON.stringify(noms), modele: d.get_value("message") || "",
              remplacements: JSON.stringify(this._remplacements || {}) },
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
    const annule = !!v.annuler;
    if (annule && !(v.motif || "").trim()) {
      frappe.msgprint(__("Écrivez le motif de l'annulation."));
      return;
    }
    frappe.confirm(
      (annule
        ? __("ANNULER {0} commande(s) puis prévenir les clients ?<br>L'annulation est difficile à défaire.",
             [noms.length])
        : __("Envoyer ce message pour {0} commande(s) ?", [noms.length]))
      + __("<br>Les SMS partent vers de VRAIS clients."),
      () => frappe.call({
        method: annule
          ? "customization_app.commandes_a_traiter.annuler_et_informer"
          : "customization_app.sms_commandes.envoyer",
        args: annule
          ? { noms: JSON.stringify(noms), motif: v.motif, modele: v.message,
              sujet: v.sujet, sms: v.sms ? 1 : 0, email: v.email ? 1 : 0,
              remplacements: JSON.stringify(this._remplacements || {}) }
          : { noms: JSON.stringify(noms), modele: v.message, sujet: v.sujet,
              sms: v.sms ? 1 : 0, email: v.email ? 1 : 0,
              remplacements: JSON.stringify(this._remplacements || {}) },
        freeze: true,
        freeze_message: annule ? __("Annulation puis envoi…") : __("Envoi en cours…"),
        callback: (r) => {
          const m = r.message || {};
          d.hide();
          if (annule) {
            const esc = frappe.utils.escape_html;
            frappe.msgprint({
              title: __("Annulation et information"),
              message: (m.annulations || []).map((x) =>
                `<div><b>${esc(x.commande)}</b> — ${esc(x.etat)}</div>`).join("")
                + `<div style="margin-top:8px">✉️ ${(m.informes || []).length} client(s) prévenu(s).</div>`,
            });
            this._vider_selection();
            this._load();
            return;
          }
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

  _dialogue_livraison() {
    const noms = this._selection();
    if (!noms) return;
    const d = new frappe.ui.Dialog({
      title: __("Livraison par notre équipe — {0} commande(s)", [noms.length]),
      fields: [
        { fieldtype: "HTML", fieldname: "visees", options: this._rappel_selection(noms) },
        {
          fieldtype: "HTML", fieldname: "aide",
          options: `<div style="padding:10px 12px;border-radius:9px;background:#e0f2fe;
                      color:#075985;font-size:12.5px">
                      🚚 Autorisée, la commande ouvre au client le créneau
                      <b>« Livraison » (30 min)</b> sur le portail de rendez-vous.
                      Sans cette autorisation, le type ne lui est pas proposé —
                      et le serveur refuse la réservation même si on la force.
                      <div style="margin-top:6px">⚠️ Seules les adresses en
                      <b>secteurs 1 à 7</b> peuvent être autorisées : les
                      secteurs 8 et 9 mobilisent la journée entière d’un
                      technicien, et « Hors Secteur » n’est pas desservi.</div>
                    </div>`,
        },
        {
          fieldtype: "Select", fieldname: "action", reqd: 1,
          label: __("Action"), default: "Autoriser",
          options: ["Autoriser", "Retirer l’autorisation"].join("\n"),
        },
      ],
      primary_action_label: __("Appliquer"),
      primary_action: (v) => frappe.call({
        method: "customization_app.commandes_a_traiter.autoriser_livraison",
        args: { noms: JSON.stringify(noms), autoriser: v.action === "Autoriser" ? 1 : 0 },
        freeze: true,
        callback: (r) => {
          const m = r.message || {};
          const esc = frappe.utils.escape_html;
          d.hide();
          // On montre le détail dès qu'une commande a été refusée : sinon le
          // garde-fou agirait en silence et on croirait la sélection traitée.
          if (m.refuses) {
            frappe.msgprint({
              title: __("Livraison par notre équipe"),
              indicator: "orange",
              message: __("{0} appliquée(s), {1} refusée(s).",
                          [(m.commandes || []).length, m.refuses])
                + `<div style="margin-top:8px;max-height:240px;overflow:auto;font-size:12px">${
                    (m.resultats || []).map((x) =>
                      `<div><b>${esc(x.commande)}</b> — ${esc(x.etat)}</div>`).join("")}</div>`,
            });
            this._vider_selection();
          } else {
            frappe.show_alert({
              message: __("{0} commande(s) — livraison par notre équipe {1}.",
                          [(m.commandes || []).length,
                           m.autorise ? __("autorisée") : __("retirée")]),
              indicator: "green",
            }, 7);
            this._vider_selection();
          }
          this._load();
        },
      }),
    });
    d.show();
  }

  _dialogue_annuler() {
    const noms = this._selection();
    if (!noms) return;
    const d = new frappe.ui.Dialog({
      title: __("Annuler {0} commande(s)", [noms.length]),
      fields: [
        { fieldtype: "HTML", fieldname: "visees", options: this._rappel_selection(noms) },
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
              this._vider_selection();
              this._load();
            },
          })
        );
      },
    });
    d.show();
  }
}
