frappe.pages["livraison-aramex"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Livraisons Aramex — Suivi",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("livraison_aramex", {}));
  // Le manifeste de fin de journée : la feuille que signe le coursier (page imprimable).
  wrapper.page.add_inner_button(__("🧾 Manifeste du jour"), () =>
    window.open("/manifeste-aramex", "_blank")
  );
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
    // Filtre local : les colis de la période sont déjà tous là, inutile de rappeler le serveur.
    this.$root.on("input", "[data-role='recherche']", () => this._render());
    this.$root.on("change", "[data-role='statut']", () => this._render());
    this.$root.on("change", "[data-role='sms-auto']", (e) => this._basculer_sms(e.currentTarget));
    this.$root.on("click", "[data-act='un-suivi']", (e) =>
      this._suivre([$(e.currentTarget).data("ref")], true)
    );
    this.$root.on("click", "[data-act='ouvrir']", (e) => {
      const $p = $(e.currentTarget);
      this._ouvrir($p.data("doctype"), $p.data("name"));
    });
    this.$root.on("click", "[data-act='echange-recu']", (e) => this._echange_recu($(e.currentTarget).data("echange")));
    this.$root.on("click", "[data-act='echange-annuler']", (e) => this._echange_annuler($(e.currentTarget).data("echange")));
    this.$root.on("click", "[data-act='retour']", (e) =>
      this._retour($(e.currentTarget).data("pe"))
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
        this._options_statut();
        this._render();
      });
  }

  // Le statut « tel que l'écran l'affiche » : la pastille du suivi, ou l'un des trois états
  // sans pastille (sans bordereau, suivi jamais demandé, suivi indisponible).
  _statut_de(c) {
    if (!c.reference) return __("sans bordereau");
    const s = c.suivi;
    if (!s) return __("suivi jamais demandé");
    if (s.erreur) return __("suivi indisponible");
    return s.statut || "—";
  }

  // La liste déroulante ne propose que les statuts réellement présents sur la période, avec
  // leur nombre — et garde le choix courant s'il existe encore.
  _options_statut() {
    const $sel = this.$root.find("[data-role='statut']");
    const courant = $sel.val() || "";
    const comptes = {};
    (this._data.colis || []).forEach((c) => {
      const st = this._statut_de(c);
      comptes[st] = (comptes[st] || 0) + 1;
    });
    const esc = frappe.utils.escape_html;
    const options = Object.keys(comptes)
      .sort((a, b) => a.localeCompare(b, "fr"))
      .map((st) => `<option value="${esc(st)}">${esc(st)} (${comptes[st]})</option>`);
    $sel.html(`<option value="">${__("Tous les statuts")}</option>${options.join("")}`);
    $sel.val(comptes[courant] ? courant : "");
  }

  _normaliser(texte) {
    return String(texte || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  // Recherche plein texte sur ce que l'œil cherche : le numéro de bordereau (aussi tel que saisi
  // sur le paiement), le client, son téléphone / e-mail, les pièces (commande, facture, BL), le
  // paiement, la ville de destination. Les espaces des numéros ne comptent pas.
  _filtrer(colis) {
    const q = this._normaliser(this.$root.find("[data-role='recherche']").val()).trim();
    const statut = this.$root.find("[data-role='statut']").val() || "";
    const qc = q.replace(/\s+/g, "");
    return colis.filter((c) => {
      if (statut && this._statut_de(c) !== statut) return false;
      if (!q) return true;
      const s = c.suivi || {};
      const champs = [
        c.reference, c.reference_brute, c.customer_name, c.customer, c.telephone, c.email,
        c.payment_entry, (s.destination || {}).ville, s.statut,
        ...(c.pieces || []).map((p) => p.name),
      ];
      const texte = this._normaliser(champs.filter(Boolean).join(" "));
      return texte.includes(q) || texte.replace(/\s+/g, "").includes(qc);
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

    this._rendre_echanges();
    this._suivre_echanges();
    this.$root.find("[data-role='sms-auto']").prop("checked", !!d.sms_auto);

    const tous = d.colis || [];
    let colis = this._filtrer(tous);
    if (this.$root.find("[data-role='alertes-seules']").is(":checked")) {
      colis = colis.filter((c) => c.alerte);
    }
    const filtre = colis.length !== tous.length;
    this.$root
      .find("[data-role='compte']")
      .text(filtre ? __("{0} / {1} colis", [colis.length, tous.length]) : "");
    this.$root
      .find("[data-role='colis']")
      .html(
        colis.length
          ? `<div class="ala-liste">${colis.map((c) => this._ligne(c)).join("")}</div>`
          : `<div class="ala-liste"><div class="ala-vide">${
              filtre ? __("Aucun colis ne correspond au filtre.") : __("Aucun colis sur la période.")
            }</div></div>`
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
          ${this._pieces(c)}
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

  // Les trois pièces du colis : la commande, la facture et le BON DE LIVRAISON. C'est ce dernier
  // qui dit ce qui est réellement parti chez le client — la seule pièce à confronter au suivi du
  // transporteur — et il n'était nulle part sur cet écran.
  _pieces(c) {
    const esc = frappe.utils.escape_html;
    const pieces = c.pieces || [];
    if (!pieces.length) return "";
    const court = { "Sales Order": __("Cde"), "Sales Invoice": __("Fac"), "Delivery Note": __("BL") };
    return (
      pieces
        .map(
          (p) =>
            `<span class="ala-piece ${p.principale ? "payee" : ""}" data-act="ouvrir"
                   data-doctype="${esc(p.doctype)}" data-name="${esc(p.name)}"
                   title="${esc(`${p.doctype} ${p.name} · ${p.statut || ""} · ${p.date || ""}`)}">
               <b>${court[p.doctype] || esc(p.doctype)}</b> ${esc(p.name)}
               <span class="etat">${esc(p.statut || "")}</span>
             </span>`
        )
        .join("") + "<br>"
    );
  }

  // ⚠️ UNE FENÊTRE, PAS UNE NAVIGATION. Ouvrir la pièce dans l'onglet ferait quitter l'écran :
  // l'utilisateur perd sa période, son filtre et sa place dans la liste, et doit tout refaire au
  // retour. Le formulaire complet vit donc dans un cadre, avec le droit d'enregistrer et de valider
  // comme partout ailleurs — c'est le desk, pas une copie appauvrie.
  //
  // La fenêtre elle-même est PARTAGÉE avec les autres tableaux de bord : sa mise au point a déjà
  // coûté deux corrections (la barre qui porte Enregistrer, puis le bandeau Raven), et deux copies
  // du même code, c'est la garantie de n'en corriger qu'une la prochaine fois.
  _ouvrir(doctype, nom) {
    frappe
      .require("/assets/customization_app/js/ouvrir_document.js")
      .then(() =>
        customization_app.ouvrir_document(doctype, nom, {
          // La relecture ne coûte aucun appel au transporteur : le suivi vient de la base.
          a_la_fermeture: () => this.refresh(),
        })
      );
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
      ${this._historique(s)}
      <div class="ala-menu">
        ${s.url ? `<a href="${esc(s.url)}" target="_blank">${esc(c.reference)} ↗</a>` : esc(c.reference)}
        · ${bouton}
      </div>
      ${this._retour_chip(c)}`;
  }

  // L'historique complet, quand le suivi vient de l'API Aramex (le scraping ne
  // rendait que le dernier événement). Replié : la ligne reste aussi courte qu'avant.
  _historique(s) {
    const esc = frappe.utils.escape_html;
    const ev = s.evenements || [];
    if (!ev.length) return "";
    const lignes = ev
      .map(
        (e) =>
          `<div style="display:flex;gap:6px;font-size:11px;line-height:1.35">
            <span class="ala-menu" style="white-space:nowrap">${esc(e.date || "")}</span>
            <span>${esc(e.description || e.code || "")}${
              e.probleme ? ` <b style="color:var(--ala-loss)">${esc(e.probleme)}</b>` : ""
            }${e.lieu ? ` <span class="ala-menu">· ${esc(e.lieu)}</span>` : ""}</span>
          </div>`
      )
      .join("");
    return `<details style="margin-top:2px">
      <summary class="ala-menu" style="cursor:pointer">${__("{0} événements", [ev.length])}</summary>
      <div style="margin-top:4px">${lignes}</div>
    </details>`;
  }

  // Le retour physique : un colis « Returned » porte le bouton tant que le
  // retour n'est pas constaté ; ensuite, la trace remplace le bouton.
  _retour_chip(c) {
    const esc = frappe.utils.escape_html;
    const r = c.retour || {};
    if (r.constate) {
      return `<div class="ala-menu" style="color:var(--ala-gain);font-weight:600">
        📦 ${__("Retour constaté le {0}", [frappe.datetime.str_to_user(r.constate.split(" ")[0])])}
        ${r.bl ? ` · ${esc(r.bl)}` : ""}</div>`;
    }
    if (r.possible) {
      return `<button class="btn btn-xs btn-danger" style="margin-top:4px"
        data-act="retour" data-pe="${esc(c.payment_entry)}">📦 ${__("Retour reçu")}</button>
        ${(r.bordereaux || []).length ? `<div class="ala-menu" style="color:#dc2626;font-weight:700">${__("Bordereau de retour : {0}", [esc(r.bordereaux.join(", "))])}</div>` : ""}`;
    }
    return "";
  }

  // Photo obligatoire -> BL de retour (stock rentre) + suppression du paiement
  // dette + commande fermée et marquée « Retour colis ». Une facture validée
  // BLOQUE : l'avoir se traite à la main.
  //
  // LA PHOTO EST VÉRIFIÉE DÈS QU'ELLE EST PRISE (19/11/2026) : l'IA lit l'étiquette, l'API Aramex fait la
  // correspondance avec le bordereau. Le 18/11, un retour avait été constaté avec la photo du colis d'un autre
  // client. Tant que le verdict n'est pas bon, le bouton reste éteint — et le serveur refuserait de toute façon.
  _retour(pe) {
    const c = (this._data.colis || []).find((x) => x.payment_entry === pe) || {};
    const esc = frappe.utils.escape_html;
    let zone = null;
    const d = new frappe.ui.Dialog({
      title: __("Retour reçu — {0} ({1})", [
        esc(c.customer_name || ""), format_currency(c.montant || 0, "TND")]),
      fields: [
        { fieldtype: "HTML", fieldname: "info" },
        { fieldtype: "HTML", fieldname: "photo_zone" },
        { fieldtype: "Small Text", fieldname: "note", label: __("Note (facultatif)") },
      ],
      primary_action_label: __("Constater le retour"),
      primary_action: (v) => {
        if (!zone.photo()) {
          frappe.msgprint(__("Prenez la photo du colis retourné : c'est la preuve du retour."));
          return;
        }
        const forcer = zone.forcer();
        if (forcer && !(v.note || "").trim()) {
          frappe.msgprint(__("Écrivez dans la note pourquoi vous constatez ce retour malgré la vérification."));
          return;
        }
        frappe.call({
          method: "customization_app.retour_aramex.constater_retour",
          args: { payment_entry: pe, photo: zone.photo(), photo_nom: zone.nom(), note: v.note, forcer },
          freeze: true, freeze_message: __("BL de retour, paiement dette, commande…"),
          callback: (r) => {
            d.hide();
            const m = r.message || {};
            frappe.msgprint({
              title: __("Retour constaté"), indicator: "green",
              message: __("Stock ré-entré ({0}), paiement dette {1} supprimé ({2}), commande {3} fermée et marquée « Retour colis ».",
                [(m.bls_retour || []).join(", "), esc(m.pe_supprimee || ""),
                 format_currency(m.montant || 0, "TND"),
                 `<a href="/app/sales-order/${encodeURIComponent(m.commande)}">${esc(m.commande)}</a>`]),
            });
            this.refresh();
          },
        });
      },
    });
    d.fields_dict.info.$wrapper.html(`
      <div style="font-size:12px;margin-bottom:6px">
        ${__("Bordereau")} <b>${esc(c.reference || "")}</b>
        ${((c.retour || {}).bordereaux || []).length ? ` · <span style="color:#dc2626;font-weight:700">${__("bordereau de retour")} ${esc(c.retour.bordereaux.join(", "))}</span>` : ""} ·
        ${(c.pieces || []).map((p) => esc(`${p.doctype === "Sales Order" ? "Cde" : p.doctype === "Delivery Note" ? "BL" : "Fac"} ${p.name}`)).join(" · ")}
      </div>
      <div class="text-muted" style="font-size:11.5px;margin-bottom:8px">
        ${__("Le stock rentre par un BL de retour, le paiement dette Aramex est supprimé (avec trace sur la commande), la commande est fermée. Une facture validée bloque : avoir à traiter à la main.")}
      </div>
      <div class="text-muted" style="font-size:11.5px;margin-bottom:8px">
        📷 ${__("Photographiez l’étiquette Aramex du colis, entière et nette : elle est lue et comparée à ce bordereau avant d’enregistrer.")}
      </div>`);
    zone = this._zone_photo(d, {
      bouton: __("Photo du colis retourné"), qui: esc(c.customer_name || __("ce client")), quoi: __("le colis"),
      reference: c.reference || "",
      methode: "customization_app.retour_aramex.verifier_photo", args: { payment_entry: pe },
    });
    d.show();
  }

  // ── La photo, vérifiée dès qu'elle est prise ────────────────────────────────────────────────────────────
  // Commun au « Retour reçu » d'un colis et à l'« Échange reçu » d'une pochette : un bouton photo, un verdict
  // dit en clair, et le bouton principal du dialogue éteint tant que le verdict n'est pas bon.
  // -> { photo(), nom(), forcer() }
  _zone_photo(d, o) {
    const esc = frappe.utils.escape_html;
    let photo = null, photo_nom = null, tour = 0;
    const $z = d.fields_dict.photo_zone.$wrapper;
    $z.html(`
      <button type="button" class="btn btn-default btn-sm ala-photo-btn">📷 ${o.bouton}</button>
      <span class="ala-photo-nom text-muted" style="margin-left:8px"></span>
      <input type="file" accept="image/*" capture="environment" style="display:none">
      <div class="ala-verdict" style="margin-top:10px"></div>`);
    const $input = $z.find("input[type=file]"), $verdict = $z.find(".ala-verdict");
    const $ok = d.get_primary_btn();
    const boite = (couleur, fond, html) => $verdict.html(
      `<div style="border:1px solid ${couleur};background:${fond};border-radius:8px;padding:9px 11px;font-size:12.5px;line-height:1.5">${html}</div>`);
    const qui = o.qui;

    // Un seul verdict, dit en clair ; le détail (ce qui a été lu) reste en petit dessous.
    const afficher = (v) => {
      const lu = v.lu || {}, at = v.attendu || {}, autre = v.autre || {};
      // Ce qu'Aramex annonce (le bordereau de retour, imprimé sur l'étiquette du colis qui revient) à côté de ce
      // que la photo montre : l'employé voit lui-même que les deux numéros sont les mêmes — ou pas.
      const attendu_retour = (at.retours || []).join(", ");
      const lecture = [attendu_retour && __("bordereau de retour annoncé par Aramex : <b>{0}</b>", [esc(attendu_retour)]),
                       lu.bordereau && __("lu sur la photo : <b>{0}</b>", [esc(lu.bordereau)]),
                       lu.reference_expediteur && __("réf. expéditeur lue : {0}", [esc(lu.reference_expediteur)])]
        .filter(Boolean).join(" · ");
      const petit = lecture ? `<div class="text-muted" style="font-size:11px;margin-top:4px">${lecture}</div>` : "";
      const aramex = at.retour_livre === true ? __("Selon Aramex, le retour de {0} a bien été livré chez vous.", [qui])
        : at.retour_livre === false ? __("Selon Aramex, le retour de {0} n’est PAS encore arrivé chez vous ({1}).", [qui, esc(at.retour_statut || __("en route"))])
        : "";
      let autorise = false;
      if (v.verdict === "ok") {
        autorise = true;
        boite("var(--green-500)", "var(--green-50, #f0fdf4)", `✅ <b>${__("C’est bien {0} de {1}", [o.quoi, qui])}</b> — ${__("bordereau {0}", [esc(o.reference)])}.${petit}`);
      } else if (v.verdict === "sans_bordereau") {
        autorise = true;
        boite("var(--border-color)", "var(--subtle-fg)", `ℹ️ ${__("Cet échange n’a pas de bordereau Aramex : la photo est gardée comme preuve, sans comparaison.")}`);
      } else if (v.verdict === "autre") {
        const a_qui = autre.destinataire || autre.client;
        boite("var(--red-500)", "var(--red-50, #fef2f2)", `❌ <b>${__("Ce n’est pas {0} de {1}.", [o.quoi, qui])}</b><br>` +
          (a_qui ? __(autre.sur === false ? "L’étiquette semble être celle de <b>{0}</b> — commande {1}, bordereau {2} ({3})."
                                          : "L’étiquette est celle de <b>{0}</b> — commande {1}, bordereau {2} ({3}).",
                      [esc(a_qui), esc(autre.commande || "?"), esc(autre.reference || ""), esc(autre.statut || "")])
                 : __("L’étiquette porte un autre bordereau, inconnu de nos envois.")) +
          (autre.etiquette_date ? "<br>" + __("Ce colis-là vous a été remis le {0}.", [esc(autre.etiquette_date)]) : "") +
          // La pochette d'un échange d'articles a sa propre ligne, en haut de l'écran : on y renvoie.
          (autre.echange ? "<br>🔁 " + __("C’est la pochette de l’<b>échange {0}</b> : fermez ce dialogue et utilisez « Échange reçu » en haut de l’écran.", [esc(autre.echange)]) : "") +
          (aramex ? "<br>" + aramex : "") +
          `<br><b>${__("Rien n’est enregistré.")}${autre.echange ? "" : " " + __("Reprenez la photo du bon colis.")}</b>${petit}`);
      } else if (v.verdict === "illisible") {
        boite("var(--orange-500)", "var(--orange-50, #fff7ed)", `⚠️ <b>${__("Je ne lis pas l’étiquette Aramex.")}</b> ${__("Reprenez la photo : étiquette entière, nette, sans reflet.")}${petit}`);
      } else {
        autorise = at.retour_livre === true;
        boite("var(--orange-500)", "var(--orange-50, #fff7ed)", `⚠️ <b>${__("La lecture automatique de l’étiquette ne répond pas.")}</b><br>` +
          (autorise ? aramex + " " + __("Vous pouvez continuer.")
                    : (aramex || __("Aramex n’annonce pas ce retour comme livré.")) + " " + __("Réessayez dans un instant.")));
      }
      // Un responsable peut passer outre (colis sans étiquette…) : c'est écrit sur la commande, avec sa note.
      if (!autorise && v.peut_forcer) {
        $verdict.append(`<label style="display:block;margin-top:8px;font-size:12px;font-weight:500">
          <input type="checkbox" class="ala-forcer"> ${__("Enregistrer quand même (responsable) — la note devient obligatoire")}</label>`);
        $verdict.find(".ala-forcer").on("change", function () { $ok.prop("disabled", !this.checked); });
      }
      $ok.prop("disabled", !autorise);
    };

    const verifier = () => {
      const moi = ++tour;
      $ok.prop("disabled", true);
      boite("var(--border-color)", "var(--subtle-fg)", `🔎 ${__("Lecture de l’étiquette et vérification chez Aramex… (quelques secondes)")}`);
      frappe.call({ method: o.methode, args: Object.assign({}, o.args, { photo }) })
        .then((r) => { if (moi === tour) afficher(r.message || {}); })
        .catch(() => { if (moi === tour) boite("var(--orange-500)", "var(--orange-50, #fff7ed)",
          `⚠️ ${__("La vérification n’a pas abouti : reprenez la photo ou réessayez.")}`); });
    };

    $ok.prop("disabled", true);
    $z.find(".ala-photo-btn").on("click", () => $input.trigger("click"));
    $input.on("change", function () {
      const f = this.files && this.files[0];
      if (!f) return;
      const lecteur = new FileReader();
      lecteur.onload = () => {
        photo = lecteur.result;
        photo_nom = f.name;
        $z.find(".ala-photo-nom").text("✓ " + f.name);
        $z.find(".ala-photo-btn").text("📷 " + __("Reprendre la photo"));
        verifier();
      };
      lecteur.readAsDataURL(f);
      this.value = "";       // la même photo peut être rechoisie après une reprise
    });
    return { photo: () => photo, nom: () => photo_nom,
             forcer: () => (d.$wrapper.find(".ala-forcer").is(":checked") ? 1 : 0) };
  }

  // ── Échanges d'articles : la pochette attendue ──────────────────────────────────────────────────────────
  // En tête de l'écran tant qu'elle n'est pas revenue : c'est ICI qu'on clique quand elle arrive.
  _rendre_echanges() {
    const esc = frappe.utils.escape_html;
    const liste = (this._data || {}).echanges || [];
    let $bloc = this.$root.find("[data-role='echanges']");
    if (!$bloc.length) $bloc = $(`<div data-role="echanges"></div>`).insertBefore(this.$root.find("[data-role='kpis']"));
    if (!liste.length) { $bloc.empty(); return; }
    $bloc.html(`<div class="ala-liste" style="margin-bottom:16px;border-color:var(--ala-accent)">
      <div style="padding:9px 14px;font-weight:700;font-size:13px;border-bottom:1px solid var(--border-color)">
        🔁 ${__("Échanges d’articles — pochette attendue")} (${liste.length})</div>
      ${liste.map((e) => `<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--border-color);font-size:12.5px">
        <div style="flex:1;min-width:230px">
          <b>${esc(e.client || "")}</b>
          <span class="ala-menu"> · <a href="/app/sales-order/${encodeURIComponent(e.commande)}">${esc(e.commande)}</a>
            · <a href="/app/echange-aramex/${encodeURIComponent(e.name)}">${esc(e.name)}</a></span>
          <div>${__("Envoyé : <b>{0}</b> — attendu en retour : <b>{1}</b>", [esc(e.envoyes), esc(e.attendus)])}</div>
          <div class="ala-menu">${e.bordereau ? __("Bordereau d’envoi {0}", [esc(e.bordereau)]) + (e.suivi ? " · " + esc(e.suivi) : "")
                                              : __("Pas encore de bordereau Aramex")}</div>
          <div class="ala-menu">${e.bordereau_retour
              ? `<span style="color:#dc2626;font-weight:700">📮 ${__("Bordereau de retour (pochette) : {0}", [esc(e.bordereau_retour)])}</span>${e.retour_suivi ? " — " + esc(e.retour_suivi) : ""}`
              : (!e.bordereau ? "" : (this._data || {})._retours_recus ? __("Bordereau de retour : pas encore annoncé par Aramex")
                                                                      : "🔎 " + __("Bordereau de retour : demande à Aramex…"))}
            ${e.retard ? ` · <span style="color:var(--ala-loss);font-weight:600">⏰ ${__("livré au client, pochette en retard de {0} j", [e.retard])}</span>` : ""}</div>
        </div>
        <button class="btn btn-xs btn-danger" data-act="echange-recu" data-echange="${esc(e.name)}">📦 ${__("Échange reçu")}</button>
        <button class="btn btn-xs btn-default" data-act="echange-annuler" data-echange="${esc(e.name)}">${__("Annuler l’échange")}</button>
      </div>`).join("")}</div>`);
  }

  // Le bordereau de la pochette se demande à Aramex APRÈS l'affichage : l'écran s'ouvre sans attendre le
  // transporteur, la ligne se complète quelques secondes plus tard. Une fois par chargement des données.
  _suivre_echanges() {
    const d = this._data || {};
    if (d._retours_demandes || !(d.echanges || []).some((e) => e.bordereau)) return;
    d._retours_demandes = true;
    frappe.call({ method: "customization_app.echange_aramex.suivre_retours" }).then((r) => {
      if (this._data !== d || !r.message) return;
      d.echanges = r.message;
      d._retours_recus = true;
      this._rendre_echanges();
    });
  }

  _echange_recu(nom) {
    const e = ((this._data || {}).echanges || []).find((x) => x.name === nom) || {};
    const esc = frappe.utils.escape_html;
    let zone = null;
    const d = new frappe.ui.Dialog({
      title: __("Échange reçu — {0}", [esc(e.client || "")]),
      fields: [
        { fieldtype: "HTML", fieldname: "info" },
        { fieldtype: "HTML", fieldname: "photo_zone" },
        // Où ranger ce qui revient : l'entrepôt de départ d'office, mais c'est ici qu'on choisit.
        { fieldtype: "Link", fieldname: "entrepot", label: __("Entrepôt où rentrent les articles"), options: "Warehouse",
          default: e.entrepot || "", reqd: 1, get_query: () => ({ filters: { is_group: 0, disabled: 0 } }) },
        { fieldtype: "Small Text", fieldname: "note", label: __("Note (facultatif)") },
      ],
      primary_action_label: __("Rentrer en stock"),
      primary_action: (v) => {
        if (!zone.photo()) { frappe.msgprint(__("Prenez la photo de la pochette revenue.")); return; }
        const forcer = zone.forcer();
        if (forcer && !(v.note || "").trim()) {
          frappe.msgprint(__("Écrivez dans la note pourquoi vous recevez cet échange malgré la vérification."));
          return;
        }
        frappe.call({
          method: "customization_app.echange_aramex.recevoir", type: "POST",
          args: { echange: nom, photo: zone.photo(), photo_nom: zone.nom(), note: v.note, forcer, entrepot: v.entrepot },
          freeze: true, freeze_message: __("Entrée en stock…"),
          callback: (r) => {
            d.hide();
            const m = r.message || {};
            frappe.msgprint({ title: __("Échange reçu"), indicator: "green",
              message: __("{0} rentré(s) dans {1} (écriture {2}). L’échange {3} est terminé.",
                [esc(m.articles || ""), esc(m.entrepot || ""), esc(m.retour_stock || ""), esc(m.echange || "")]) });
            this.refresh();
          },
        });
      },
    });
    d.fields_dict.info.$wrapper.html(`
      <div style="font-size:12.5px;margin-bottom:6px">
        ${__("Le client renvoie : <b>{0}</b>", [esc(e.attendus || "")])}<br>
        <span class="text-muted">${__("Envoyé le {0} : {1}", [frappe.datetime.str_to_user(e.declare_le || ""), esc(e.envoyes || "")])}
          ${e.bordereau ? " · " + __("bordereau {0}", [esc(e.bordereau)]) : ""}</span>
      </div>
      <div class="text-muted" style="font-size:11.5px;margin-bottom:8px">
        📷 ${__("Photographiez l’étiquette Aramex de la pochette : elle est comparée au bordereau de cet échange, puis les articles rentrent en stock.")}
      </div>`);
    d.fields_dict.info.$wrapper.append(e.bordereau_retour
      ? `<div style="font-size:12.5px;margin-bottom:8px"><span style="color:#dc2626;font-weight:700">📮 ${__("Bordereau de retour attendu sur l’étiquette : {0}", [esc(e.bordereau_retour)])}</span>${e.retour_suivi ? ` <span class="text-muted">— ${esc(e.retour_suivi)}</span>` : ""}</div>` : "");
    zone = this._zone_photo(d, {
      bouton: __("Photo de la pochette revenue"), qui: esc(e.client || __("ce client")), quoi: __("la pochette"),
      reference: e.bordereau || "",
      methode: "customization_app.echange_aramex.verifier_photo", args: { echange: nom },
    });
    d.show();
  }

  _echange_annuler(nom) {
    frappe.prompt(
      [{ fieldtype: "Small Text", fieldname: "motif", reqd: 1, label: __("Pourquoi l’échange est-il annulé ?") }],
      (v) => frappe.call({
        method: "customization_app.echange_aramex.annuler", type: "POST", args: { echange: nom, motif: v.motif },
        freeze: true,
        callback: () => { frappe.show_alert({ message: __("Échange annulé : les articles envoyés sont rentrés en stock."), indicator: "green" }); this.refresh(); },
      }),
      __("Annuler l’échange {0}", [nom]), __("Annuler l’échange"));
  }
}
