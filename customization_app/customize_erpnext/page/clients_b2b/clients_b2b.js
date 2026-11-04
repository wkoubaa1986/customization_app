/**
 * « Clients B2B » — le tableau de bord du commercial : qui achète quoi, à quel rythme, qui se sert de sa fiche client,
 * et qui voir cette semaine. Tout est calculé côté serveur (customization_app.clients_b2b) ; ici on filtre, on trie, on
 * ouvre la fiche d'un client, on note une visite, on rattache un commercial.
 */
frappe.pages["clients-b2b"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Clients B2B", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("clients_b2b", {}));
  wrapper.b2b = new ClientsB2B(wrapper);
};
frappe.pages["clients-b2b"].on_page_show = function (wrapper) {
  if (wrapper.b2b && wrapper.b2b.pret) wrapper.b2b.charger();
};

const B2B_API = "customization_app.clients_b2b";
const b2b_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const b2b_m = (v) => (v == null ? "—" : format_currency(v, "TND", 0));
const b2b_j = (n) => (n == null ? "—" : n === 0 ? "aujourd’hui" : n === 1 ? "hier" : `il y a ${n} j`);
const b2b_date = (d) => (d ? frappe.datetime.str_to_user(String(d).slice(0, 10)) : "—");
const B2B_SANS_SECTEUR = "Sans secteur";
const b2b_secteur = (l) => ((l.geo || {}).secteur || B2B_SANS_SECTEUR);
/** Distance à vol d'oiseau en km (haversine) — assez précise pour « autour de ce client ». */
const b2b_km = (a, b) => {
  if (a.lat == null || b.lat == null) return null;
  const r = Math.PI / 180, R = 6371;
  const dLat = (b.lat - a.lat) * r, dLng = (b.lng - a.lng) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};
const B2B_ACTIONS = {
  connexion: "connexion", recherche: "recherche", photo: "photo", apercu_site: "article vu", liste_prix: "liste de prix",
  pdf_liste: "PDF", rapport_ristourne: "rapport ristourne", familles: "familles",
};

class ClientsB2B {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".b2b-page");
    this.groupes = [];          // groupes cochés ([] = tous)
    this.alerte = null;         // filtre sur une alerte
    this.tri = { cle: "score", asc: false };
    this.donnees = null;
    this.pret = true;
    this.$root.find("#b2b-rafraichir").on("click", () => this.charger());
    this.$root.find("#b2b-commercial, #b2b-periode").on("change", () => this.charger());
    this.$root.find("#b2b-recherche").on("input", () => this.rendre_liste());
    this.$root.on("click", ".b2b-chip.groupe", (e) => {
      const g = $(e.currentTarget).data("groupe");
      const i = this.groupes.indexOf(g);
      i >= 0 ? this.groupes.splice(i, 1) : this.groupes.push(g);
      this.rendre();
    });
    this.$root.on("click", ".b2b-kpi", (e) => {
      const g = $(e.currentTarget).data("groupe");
      this.groupes = this.groupes.length === 1 && this.groupes[0] === g ? [] : [g];
      this.rendre();
    });
    this.$root.on("click", ".b2b-chip.alerte", (e) => {
      const a = $(e.currentTarget).data("alerte");
      this.alerte = this.alerte === a ? null : a;
      this.rendre();
    });
    this.$root.on("click", ".b2b-table th[data-cle]", (e) => {
      const cle = $(e.currentTarget).data("cle");
      this.tri = { cle, asc: this.tri.cle === cle ? !this.tri.asc : false };
      this.rendre_liste();
    });
    this.$root.on("click", "tr.b2b-ligne", (e) => {
      if ($(e.target).closest("button, a").length) return;
      this.ouvrir_fiche($(e.currentTarget).data("client"));
    });
    this.$root.on("click", ".b2b-visite", (e) => { e.stopPropagation(); this.visite($(e.currentTarget).data("client")); });
    this.$root.on("click", ".b2b-commercial-btn", (e) => { e.stopPropagation(); this.rattacher($(e.currentTarget).data("client")); });
    this.$root.on("click", ".b2b-historique", (e) => {
      e.stopPropagation();
      if (window.ouvrir_historique_client) window.ouvrir_historique_client($(e.currentTarget).data("client"));
    });
    // Sélection pour la tournée : les cases cochées survivent au tri et aux filtres (this.selection).
    this.selection = new Set();
    this.$root.on("click", "td.coche, th.coche", (e) => e.stopPropagation());
    this.$root.on("change", "input.b2b-coche", (e) => {
      const c = e.currentTarget.dataset.client;
      e.currentTarget.checked ? this.selection.add(c) : this.selection.delete(c);
      this.maj_selection();
    });
    this.$root.on("change", "#b2b-coche-tout", (e) => {
      this.lignes_visibles().forEach((l) => (e.currentTarget.checked ? this.selection.add(l.client) : this.selection.delete(l.client)));
      this.rendre_liste();
    });
    this.$root.find("#b2b-planifier").on("click", () => this.planifier([...this.selection]));
    // Zone : secteurs cochés, ville, regroupement, rayon autour d'un client.
    this.secteurs = [];
    this.autour = null;           // { client, nom, km, lat, lng }
    this.$root.on("click", ".b2b-chip.secteur", (e) => {
      const s = $(e.currentTarget).data("secteur");
      const i = this.secteurs.indexOf(s);
      i >= 0 ? this.secteurs.splice(i, 1) : this.secteurs.push(s);
      this.rendre();
    });
    this.$root.find("#b2b-ville").on("change", () => this.rendre_liste());
    this.$root.find("#b2b-grouper").on("change", () => this.rendre_liste());
    this.$root.on("click", ".b2b-autour-off", () => { this.autour = null; this.rendre(); });
    this.$root.on("change", "input.b2b-coche-secteur", (e) => {
      const s = e.currentTarget.dataset.secteur;
      this.lignes_visibles().filter((l) => b2b_secteur(l) === s)
        .forEach((l) => (e.currentTarget.checked ? this.selection.add(l.client) : this.selection.delete(l.client)));
      this.rendre_liste();
    });
    this.charger();
  }

  maj_selection() {
    const n = this.selection.size;
    this.$root.find("#b2b-planifier").prop("disabled", !n).text(n ? `🧭 Mettre en tournée (${n})` : "🧭 Mettre en tournée");
    this.$root.find("#b2b-selection").text(n ? "" : "cochez des clients pour les mettre dans une tournée");
  }

  async charger() {
    this.$root.find("#b2b-liste").html('<div class="b2b-vide">Chargement…</div>');
    try {
      this.donnees = (await frappe.call({
        method: B2B_API + ".tableau",
        args: { commercial: this.$root.find("#b2b-commercial").val() || null, periode: this.$root.find("#b2b-periode").val() },
      })).message || {};
    } catch (e) {
      this.$root.find("#b2b-liste").html(`<div class="b2b-vide">Erreur : ${b2b_esc(e.message || e)}</div>`);
      return;
    }
    const $c = this.$root.find("#b2b-commercial");
    const val = $c.val();
    $c.find("option:not(:first)").remove();
    $c.append('<option value="(aucun)">Sans commercial</option>');
    (this.donnees.commerciaux || []).forEach((n) => $c.append(`<option value="${b2b_esc(n)}">${b2b_esc(n)}</option>`));
    $c.val(val || "");
    this.rendre();
  }

  lignes_visibles() {
    const d = this.donnees || {};
    const rech = (this.$root.find("#b2b-recherche").val() || "").trim().toLowerCase();
    const ville = this.$root.find("#b2b-ville").val() || "";
    return (d.lignes || []).filter((l) =>
      (!this.groupes.length || this.groupes.includes(l.groupe)) &&
      (!this.alerte || (l.alertes || []).includes(this.alerte)) &&
      (!this.secteurs.length || this.secteurs.includes(b2b_secteur(l))) &&
      (!ville || (l.geo || {}).ville === ville) &&
      (!this.autour || (() => { const k = b2b_km(this.autour, l.geo || {}); l._km = k; return k != null && k <= this.autour.km; })()) &&
      (!rech || [l.nom, l.client, l.telephone, (l.geo || {}).ville].some((x) => (x || "").toLowerCase().includes(rech))));
  }

  rendre() {
    const d = this.donnees || {};
    const periode = d.periode === "annee" ? "depuis le 1er janvier" : "sur les 12 derniers mois";
    this.$root.find("#b2b-intro").html(
      `Ventes = <b>bons de livraison validés</b> (HT, retours déduits), ${periode}. Le <b>rythme</b> est le nombre de jours entre deux ` +
      `achats du client ; un client est <b>en retard</b> quand son silence dépasse 1,5 × son rythme. La colonne <b>fiche client</b> lit ` +
      `les connexions et les actions sur /fiche_client. Cliquer sur une ligne ouvre le détail.`);

    this.$root.find("#b2b-roles").text((d.roles || []).join(" ou "));
    this.$root.find("#b2b-acces").html((d.acces || []).length
      ? d.acces.map((a) => `<b>${b2b_esc(a.nom)}</b> <span class="b2b-muted">(${b2b_esc(a.roles)})</span>`).join(", ")
      : "personne pour l’instant");

    this.$root.find("#b2b-groupes").html((d.groupes || []).map((g) => {
      const k = (d.kpis || {})[g] || {};
      return `<span class="b2b-chip groupe ${this.groupes.includes(g) ? "actif" : ""}" data-groupe="${b2b_esc(g)}">${b2b_esc(g)}<span class="n">${k.clients || 0}</span></span>`;
    }).join(" "));

    this.$root.find("#b2b-kpis").html((d.groupes || []).map((g) => {
      const k = (d.kpis || {})[g];
      if (!k) return "";
      return `<div class="b2b-kpi ${this.groupes.length === 1 && this.groupes[0] === g ? "actif" : ""}" data-groupe="${b2b_esc(g)}">
        <div class="t">${b2b_esc(g)} · ${k.actifs}/${k.clients} actifs</div>
        <div class="v">${b2b_m(k.ca_ht)} <span class="b2b-muted">HT</span></div>
        <div class="d">panier <b>${b2b_m(k.panier)}</b> · rythme <b>${k.rythme ? k.rythme + " j" : "—"}</b><br>
          connectés <b>${k.connectes}</b> · silencieux <b>${k.silencieux}</b> · impayé <b>${b2b_m(k.impaye)}</b><br>
          <span class="avoir">${k.a_voir} à voir</span></div></div>`;
    }).join(""));

    // Zone : pastilles de secteurs (comptées sur les groupes cochés), villes, rayon.
    const base_zone = (d.lignes || []).filter((l) => !this.groupes.length || this.groupes.includes(l.groupe));
    const par_secteur = {};
    base_zone.forEach((l) => { const s = b2b_secteur(l); par_secteur[s] = (par_secteur[s] || 0) + 1; });
    const desc = {};
    (d.secteurs || []).forEach((s) => { desc[s.nom] = s.description; });
    const ordre = (d.secteurs || []).map((s) => s.nom).concat(Object.keys(par_secteur).filter((s) => !(s in desc)).sort());
    this.$root.find("#b2b-secteurs").html(ordre.filter((s) => par_secteur[s]).map((s) =>
      `<span class="b2b-chip secteur ${this.secteurs.includes(s) ? "actif" : ""}" data-secteur="${b2b_esc(s)}" title="${b2b_esc(desc[s] || "")}">${b2b_esc(s)}<span class="n">${par_secteur[s]}</span></span>`).join(" "));
    const $v = this.$root.find("#b2b-ville");
    const ville = $v.val() || "";
    const villes = {};
    base_zone.filter((l) => !this.secteurs.length || this.secteurs.includes(b2b_secteur(l)))
      .forEach((l) => { const x = (l.geo || {}).ville; if (x) villes[x] = (villes[x] || 0) + 1; });
    $v.find("option:not(:first)").remove();
    Object.keys(villes).sort((a, b) => a.localeCompare(b)).forEach((x) => $v.append(`<option value="${b2b_esc(x)}">${b2b_esc(x)} (${villes[x]})</option>`));
    $v.val(villes[ville] ? ville : "");
    this.$root.find("#b2b-autour").html(this.autour
      ? `<span class="b2b-chip autour">📍 à moins de ${this.autour.km} km de ${b2b_esc(this.autour.nom)} <a class="b2b-autour-off" style="margin-left:6px;cursor:pointer;">✕</a></span>` : "");

    const visibles_sans_alerte = base_zone;
    const comptes = {};
    visibles_sans_alerte.forEach((l) => (l.alertes || []).forEach((a) => { comptes[a] = (comptes[a] || 0) + 1; }));
    this.$root.find("#b2b-alertes").html(Object.entries(d.alertes || {})
      .sort((a, b) => b[1].poids - a[1].poids)
      .map(([code, a]) => `<span class="b2b-chip alerte ${this.alerte === code ? "actif" : ""}" data-alerte="${code}"
          title="${b2b_esc(a.explication)}">${b2b_esc(a.libelle)}<span class="n">${comptes[code] || 0}</span></span>`).join(" "));

    this.rendre_liste();
  }

  rendre_liste() {
    const d = this.donnees || {};
    const lignes = this.lignes_visibles();
    const { cle, asc } = this.tri;
    const val = (l) => {
      if (cle === "nom") return (l.nom || "").toLowerCase();
      if (cle === "tendance") return (l.tendance || {}).variation;
      if (cle === "connexion") return l.jours_depuis_connexion;
      if (cle === "visite") return l.jours_depuis_visite;
      if (cle === "zone") return ((l.geo || {}).ville || "").toLowerCase();
      if (cle === "km") return l._km;
      return l[cle];
    };
    lignes.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      const c = typeof x === "string" ? x.localeCompare(y) : x - y;
      return asc ? c : -c;
    });
    const th = (label, k, cls = "") => `<th data-cle="${k}" class="${cls} ${cle === k ? "tri" + (asc ? " asc" : "") : ""}">${label}</th>`;
    const alertes = d.alertes || {};
    const grouper = this.$root.find("#b2b-grouper").is(":checked");
    if (grouper) {
      // Une section par secteur (ordre des secteurs), puis par ville, puis par urgence.
      const ordre = (d.secteurs || []).map((s) => s.nom);
      const rang = (l) => { const i = ordre.indexOf(b2b_secteur(l)); return i < 0 ? 999 : i; };
      lignes.sort((a, b) => rang(a) - rang(b) || ((a.geo || {}).ville || "").localeCompare((b.geo || {}).ville || "") || b.score - a.score);
    }
    const desc = {};
    (d.secteurs || []).forEach((s) => { desc[s.nom] = s.description; });
    let secteur_courant = null;
    const ligne_html = (l) => {
      const t = l.tendance || {};
      const tend = t.variation == null ? '<span class="b2b-muted">—</span>'
        : `<span class="b2b-tend ${t.variation >= 0 ? "up" : "down"}">${t.variation >= 0 ? "▲" : "▼"} ${Math.round(Math.abs(t.variation) * 100)} %</span>`;
      const acts = Object.entries(l.actions_90 || {}).map(([a, n]) => `${B2B_ACTIONS[a] || a} ×${n}`).join(", ");
      const fiche = l.derniere_connexion
        ? `${b2b_j(l.jours_depuis_connexion)} <span class="b2b-muted">· ${l.connexions_90} / 90 j</span>` +
          (acts ? `<br><span class="b2b-muted">${b2b_esc(acts)}</span>` : "")
        : `<span class="b2b-muted">${l.acces_fiche ? "jamais" : "accès désactivé"}</span>`;
      const r = l.ristourne || {};
      const p = r.palier;
      const rist = p ? `${p.taux} %` + (p.prochain ? `<br><span class="b2b-muted">+${b2b_m(p.prochain.manque)} → ${p.prochain.taux} %</span>` : "")
        : '<span class="b2b-muted">—</span>';
      const badges = (l.alertes || []).map((a) => `<span class="b2b-badge a${(alertes[a] || {}).poids || 1}" title="${b2b_esc((alertes[a] || {}).explication)}">${b2b_esc((alertes[a] || {}).libelle || a)}</span>`).join(" ")
        || '<span class="b2b-badge ok">RAS</span>';
      const v = l.visite || {};
      let visite = l.derniere_visite ? b2b_j(l.jours_depuis_visite) : '<span class="b2b-muted">jamais</span>';
      if (v.derniere) {
        visite += `<br><span class="b2b-visite-res">${b2b_esc(v.derniere.resultat || "réalisée")}${v.derniere.date_relance ? ` · relance ${b2b_date(v.derniere.date_relance)}` : ""}</span>`;
      }
      if (v.prochaine) {
        visite += `<br><span class="b2b-badge ok" title="Tournée ${b2b_esc(v.prochaine.tournee)}">🧭 ${b2b_date(v.prochaine.date)}</span>`;
      }
      const g = l.geo || {};
      const zone = g.ville || g.gouvernorat
        ? `${b2b_esc(g.ville)}${g.gouvernorat && g.gouvernorat !== g.ville ? ` <span class="b2b-muted">(${b2b_esc(g.gouvernorat)})</span>` : ""}`
        : '<span class="b2b-muted">—</span>';
      const km = this.autour && l._km != null ? `<br><span class="b2b-muted">${l._km < 1 ? "< 1" : Math.round(l._km)} km</span>` : "";
      return `<tr class="b2b-ligne" data-client="${b2b_esc(l.client)}">
        <td class="coche"><input type="checkbox" class="b2b-coche" data-client="${b2b_esc(l.client)}" ${this.selection.has(l.client) ? "checked" : ""}></td>
        <td><div class="b2b-nom">${b2b_esc(l.nom)}</div>
            <span class="b2b-badge groupe">${b2b_esc(l.groupe)}</span>
            <span class="b2b-muted">${b2b_esc(l.commercial || "sans commercial")}${l.telephone ? " · " + b2b_esc(l.telephone) : ""}</span></td>
        <td>${zone}${!grouper ? `<br><span class="b2b-muted">${b2b_esc(b2b_secteur(l))}</span>` : ""}${g.lien ? ` <a href="${b2b_esc(g.lien)}" target="_blank" title="Google Maps">🗺</a>` : ""}${km}</td>
        <td class="num"><b>${b2b_m(l.ca_ht)}</b></td>
        <td class="num">${l.nb_bl}</td>
        <td class="num">${b2b_m(l.panier)}</td>
        <td class="num">${l.rythme ? l.rythme + " j" : '<span class="b2b-muted">—</span>'}</td>
        <td>${b2b_j(l.jours_depuis_bl)}<br><span class="b2b-muted">${b2b_date(l.derniere_bl)}</span></td>
        <td>${tend}</td>
        <td class="num">${l.impaye > 1 ? `<b style="color:#b91c1c">${b2b_m(l.impaye)}</b>` : '<span class="b2b-muted">—</span>'}</td>
        <td>${fiche}</td>
        <td>${rist}</td>
        <td>${visite}</td>
        <td>${badges}</td>
        <td class="b2b-actions" style="white-space:nowrap;">
          <button class="btn btn-xs btn-default b2b-historique" data-client="${b2b_esc(l.client)}" title="Historique client">📜</button>
          <button class="btn btn-xs btn-default b2b-visite" data-client="${b2b_esc(l.client)}" title="Noter une visite">🤝</button>
          <button class="btn btn-xs btn-default b2b-commercial-btn" data-client="${b2b_esc(l.client)}" title="Rattacher un commercial">👤</button>
          <a class="btn btn-xs btn-default" href="/app/customer/${encodeURIComponent(l.client)}" target="_blank" title="Ouvrir la fiche">↗</a>
        </td></tr>`;
    };
    const html = lignes.map((l) => {
      let entete = "";
      if (grouper && b2b_secteur(l) !== secteur_courant) {
        secteur_courant = b2b_secteur(l);
        const du_secteur = lignes.filter((x) => b2b_secteur(x) === secteur_courant);
        const tous_coches = du_secteur.every((x) => this.selection.has(x.client));
        entete = `<tr class="b2b-secteur"><td class="coche"><input type="checkbox" class="b2b-coche-secteur" data-secteur="${b2b_esc(secteur_courant)}" ${tous_coches ? "checked" : ""} title="Tout cocher ce secteur"></td>
          <td colspan="14">${b2b_esc(secteur_courant)} <span class="b2b-muted">· ${du_secteur.length} client(s)</span><span class="desc">${b2b_esc(desc[secteur_courant] || "")}</span></td></tr>`;
      }
      return entete + ligne_html(l);
    }).join("");
    const tous = lignes.length && lignes.every((l) => this.selection.has(l.client));
    this.$root.find("#b2b-liste").html(lignes.length ? `<table class="b2b-table"><thead><tr>
        <th class="coche"><input type="checkbox" id="b2b-coche-tout" ${tous ? "checked" : ""} title="Tout cocher"></th>
        ${th("Client", "nom")}${th(this.autour ? "Zone · km" : "Zone", this.autour ? "km" : "zone")}${th("CA HT", "ca_ht", "num")}${th("BL", "nb_bl", "num")}${th("Panier", "panier", "num")}
        ${th("Rythme", "rythme", "num")}${th("Dernier BL", "jours_depuis_bl")}${th("Tendance 6 m", "tendance")}
        ${th("Impayé", "impaye", "num")}${th("Fiche client", "connexion")}${th("Ristourne", "ca_ht_annee")}
        ${th("Visite", "visite")}${th("Alertes", "score")}<th></th></tr></thead><tbody>${html}</tbody></table>
        <div class="b2b-muted" style="padding:6px 8px;">${lignes.length} client(s)</div>`
      : '<div class="b2b-vide">Aucun client pour ces filtres.</div>');
    this.maj_selection();
  }

  // ---------------------------------------------------------------- tournée commerciale

  planifier(clients) {
    if (!clients.length) return;
    const d = this.donnees || {};
    const tournees = d.tournees || [];
    const opt_t = [""].concat(tournees.map((t) => t.name));
    const libelle_t = (n) => { const t = tournees.find((x) => x.name === n); return t ? `${t.name} — ${b2b_date(t.date)} · ${t.commercial || "?"} · ${t.nb_visites} visite(s)` : n; };
    const employes = d.commerciaux_employes || [];
    const dlg = new frappe.ui.Dialog({
      title: `Mettre ${clients.length} client(s) en tournée`,
      fields: [
        { fieldname: "mode", fieldtype: "Select", label: "Tournée", options: ["Nouvelle tournée"].concat(tournees.length ? ["Tournée existante"] : []).join("\n"), default: "Nouvelle tournée", reqd: 1 },
        { fieldname: "tournee", fieldtype: "Select", label: "Tournée existante (en cours)", options: opt_t.join("\n"),
          depends_on: "eval:doc.mode=='Tournée existante'", description: tournees.map((t) => libelle_t(t.name)).join("<br>") },
        { fieldname: "commercial", fieldtype: "Select", label: "Commercial", options: [""].concat(employes.map((e) => e.employee)).join("\n"),
          depends_on: "eval:doc.mode=='Nouvelle tournée'", description: employes.map((e) => `${e.employee} = ${e.nom}`).join(" · ") },
        { fieldname: "date_tournee", fieldtype: "Date", label: "Date de la tournée", default: frappe.datetime.add_days(frappe.datetime.get_today(), 1), depends_on: "eval:doc.mode=='Nouvelle tournée'" },
        { fieldname: "objectif", fieldtype: "Small Text", label: "Objectif (facultatif)", depends_on: "eval:doc.mode=='Nouvelle tournée'" },
        { fieldname: "liste", fieldtype: "HTML", options: `<div class="b2b-muted" style="font-size:12px">${clients.map((c) => b2b_esc(((d.lignes || []).find((l) => l.client === c) || {}).nom || c)).join(" · ")}</div>` },
      ],
      primary_action_label: "Planifier",
      primary_action: async (v) => {
        const existante = v.mode === "Tournée existante";
        if (existante && !v.tournee) { frappe.msgprint("Choisissez la tournée."); return; }
        if (!existante && !v.commercial) { frappe.msgprint("Choisissez le commercial."); return; }
        dlg.hide();
        const r = (await frappe.call({ method: B2B_API + ".planifier_visites", args: {
          clients, tournee: existante ? v.tournee : null, commercial: existante ? null : v.commercial,
          date_tournee: existante ? null : v.date_tournee, objectif: existante ? null : v.objectif || "" } })).message || {};
        this.selection.clear();
        frappe.msgprint({
          title: "Tournée planifiée", indicator: "green",
          message: `Tournée <b>${b2b_esc(r.tournee)}</b> du ${b2b_date(r.date)} : ${(r.ajoutes || []).length} client(s) ajouté(s)` +
            ((r.deja || []).length ? `, ${r.deja.length} déjà dedans` : "") +
            `.<br>${frappe.utils.get_form_link("Tache de travail", r.tache, true)} · ` +
            `<a href="/app/ma-tournee?tache=${encodeURIComponent(r.tache)}">🧭 Ouvrir Ma Tournée</a>`,
        });
        this.charger();
      },
    });
    dlg.show();
  }

  // ---------------------------------------------------------------- fiche d'un client

  async ouvrir_fiche(client) {
    let f;
    try {
      f = (await frappe.call({ method: B2B_API + ".fiche", args: { client } })).message || {};
    } catch (e) { return; }
    const l = f.ligne || {};
    const dlg = new frappe.ui.Dialog({ title: `${l.nom || client} — ${l.groupe || ""}`, size: "extra-large" });
    const maxCa = Math.max(1, ...(f.mois || []).map((m) => m.ca_ht));
    const maxConn = Math.max(1, ...(f.mois || []).map((m) => m.connexions + m.actions));
    const mois = (f.mois || []).map((m) => `<tr><td>${b2b_esc(m.mois)}</td>
      <td class="num">${b2b_m(m.ca_ht)}</td><td><span class="b2b-barre-mois" style="width:${Math.round(120 * m.ca_ht / maxCa)}px"></span></td>
      <td class="num">${m.nb_bl}</td>
      <td class="num">${m.connexions}${m.actions ? ` <span class="b2b-muted">+${m.actions} actions</span>` : ""}</td>
      <td><span class="b2b-barre-mois conn" style="width:${Math.round(80 * (m.connexions + m.actions) / maxConn)}px"></span></td></tr>`).join("");
    const familles = (f.familles || []).map((x) => `<tr><td>${b2b_esc(x.famille)}</td><td class="num">${b2b_m(x.ht)}</td><td class="num">${x.qte}</td></tr>`).join("")
      || '<tr><td colspan="3" class="b2b-muted">Aucune livraison sur 12 mois.</td></tr>';
    const articles = (f.articles || []).map((x) => `<tr><td>${b2b_esc(x.item_name || x.item_code)}</td><td class="num">${b2b_m(x.ht)}</td><td class="num">${x.qte}</td><td>${b2b_date(x.dernier)}</td></tr>`).join("");
    const bls = (f.bls || []).map((b) => `<tr><td>${frappe.utils.get_form_link("Delivery Note", b.name, true)}</td><td>${b2b_date(b.posting_date)}</td>
      <td class="num">${b2b_m(b.base_net_total)}</td><td>${b.is_return ? '<span class="b2b-badge a2">retour</span>' : b2b_esc(b.status)}</td></tr>`).join("")
      || '<tr><td colspan="4" class="b2b-muted">Aucun bon de livraison.</td></tr>';
    const actions = (f.actions || []).map((a) => `<tr><td>${frappe.datetime.str_to_user(String(a.logging_time).slice(0, 16))}</td>
      <td>${b2b_esc(B2B_ACTIONS[a.action || "connexion"] || a.action)}</td><td>${b2b_esc(a.detail || "")}</td></tr>`).join("")
      || '<tr><td colspan="3" class="b2b-muted">Jamais connecté à sa fiche client.</td></tr>';
    const listes = (f.listes || []).map((x) => `<tr><td>${frappe.utils.get_form_link("Liste prix documents", x.name, true)}</td>
      <td>${frappe.datetime.str_to_user(String(x.creation).slice(0, 16))}</td><td>${b2b_esc(x.liste_des_prix)}</td></tr>`).join("");
    const comm = (f.commentaires || []).map((c) => `<tr><td style="white-space:nowrap">${frappe.datetime.str_to_user(String(c.creation).slice(0, 10))}</td>
      <td>${b2b_esc(c.comment_by || c.comment_email)}</td><td>${b2b_esc(c.content)}</td></tr>`).join("");
    const visites = (f.visites || []).map((v) => `<tr><td style="white-space:nowrap">${b2b_date(v.date_tournee)}</td>
      <td>${b2b_esc(v.commercial || "")}</td><td><span class="b2b-badge ${v.statut === "Réalisée" ? "ok" : v.statut === "Annulée" ? "a1" : "groupe"}">${b2b_esc(v.statut)}</span></td>
      <td>${b2b_esc(v.resultat || "")}${v.niveau_interet ? ` <span class="b2b-muted">(intérêt ${b2b_esc(v.niveau_interet)})</span>` : ""}
        ${v.montant_potentiel ? `<br><span class="b2b-muted">potentiel ${b2b_m(v.montant_potentiel)}</span>` : ""}</td>
      <td>${b2b_esc(v.resume_discussion || "")}${v.prochaine_action ? `<br><b>→ ${b2b_esc(v.prochaine_action)}</b>` : ""}
        ${v.date_relance ? `<br><span class="b2b-muted">relance ${b2b_date(v.date_relance)}${v.tache_relance ? " · " + frappe.utils.get_form_link("Tache de travail", v.tache_relance, true) : ""}</span>` : ""}
        ${v.opportunite ? `<br>${frappe.utils.get_form_link("Opportunity", v.opportunite, true)}` : ""}</td>
      <td><a href="/app/ma-tournee?tache=${encodeURIComponent(v.tache_de_travail || "")}" title="Ouvrir dans Ma Tournée">🧭</a></td></tr>`).join("");
    const r = l.ristourne || {};
    const p = r.palier;
    const alertes = (l.alertes || []).map((a) => `<div>• <b>${b2b_esc((f.alertes[a] || {}).libelle || a)}</b> — ${b2b_esc((f.alertes[a] || {}).explication || "")}</div>`).join("");
    const t = l.tendance || {};
    dlg.$body.html(`<div class="b2b-fiche">
      ${alertes ? `<div class="b2b-note">${alertes}</div>` : ""}
      <div class="grille">
        <div class="case"><div class="t">CA HT 12 mois</div><div class="v">${b2b_m(l.ca_ht)}</div><div class="b2b-muted">${l.nb_bl} BL · panier ${b2b_m(l.panier)}</div></div>
        <div class="case"><div class="t">Rythme</div><div class="v">${l.rythme ? l.rythme + " j" : "—"}</div><div class="b2b-muted">dernier BL ${b2b_j(l.jours_depuis_bl)}</div></div>
        <div class="case"><div class="t">Tendance 6 m</div><div class="v">${t.variation == null ? "—" : (t.variation >= 0 ? "▲ " : "▼ ") + Math.round(Math.abs(t.variation) * 100) + " %"}</div><div class="b2b-muted">${b2b_m(t.avant)} → ${b2b_m(t.recent)}</div></div>
        <div class="case"><div class="t">Impayé</div><div class="v">${b2b_m(l.impaye)}</div></div>
        <div class="case"><div class="t">Fiche client</div><div class="v">${l.derniere_connexion ? b2b_j(l.jours_depuis_connexion) : "jamais"}</div><div class="b2b-muted">${l.connexions_90} connexions / 90 j</div></div>
        <div class="case"><div class="t">Ristourne ${new Date().getFullYear()}</div><div class="v">${p ? p.taux + " %" : "—"}</div>
          <div class="b2b-muted">CA année ${b2b_m(l.ca_ht_annee)}${p && p.prochain ? ` · +${b2b_m(p.prochain.manque)} pour ${p.prochain.taux} %` : ""}${r.acquise != null ? ` · acquise ${b2b_m(r.acquise)}, utilisée ${b2b_m(r.utilisee)}` : ""}</div></div>
        <div class="case"><div class="t">Commercial</div><div class="v" style="font-size:13px">${b2b_esc(l.commercial || "—")}</div><div class="b2b-muted">visite ${l.derniere_visite ? b2b_date(l.derniere_visite) : "jamais"}</div></div>
        <div class="case"><div class="t">Zone</div><div class="v" style="font-size:13px">${b2b_esc(b2b_secteur(l))}</div>
          <div class="b2b-muted">${b2b_esc((l.geo || {}).ville || "")}${(l.geo || {}).gouvernorat ? ` (${b2b_esc(l.geo.gouvernorat)})` : ""}${(l.geo || {}).ligne ? `<br>${b2b_esc(l.geo.ligne)}` : ""}${(l.geo || {}).lien ? ` <a href="${b2b_esc(l.geo.lien)}" target="_blank">🗺 carte</a>` : ""}</div>
          ${(l.geo || {}).lat != null ? `<div style="margin-top:4px;">${[5, 10, 20].map((k) => `<button class="btn btn-xs btn-default b2b-autour-btn" data-km="${k}">📍 Autour ${k} km</button>`).join(" ")}</div>` : '<div class="b2b-muted">pas de position GPS</div>'}</div>
      </div>
      <h5>Mois par mois</h5>
      <table><thead><tr><th>Mois</th><th class="num">CA HT</th><th></th><th class="num">BL</th><th class="num">Connexions</th><th></th></tr></thead><tbody>${mois}</tbody></table>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div><h5>Familles achetées (12 mois)</h5><table><thead><tr><th>Famille</th><th class="num">HT</th><th class="num">Qté</th></tr></thead><tbody>${familles}</tbody></table></div>
        <div><h5>Articles les plus achetés</h5><table><thead><tr><th>Article</th><th class="num">HT</th><th class="num">Qté</th><th>Dernier</th></tr></thead><tbody>${articles || '<tr><td colspan="4" class="b2b-muted">—</td></tr>'}</tbody></table></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div><h5>Dernières livraisons</h5><table><tbody>${bls}</tbody></table></div>
        <div><h5>Sur sa fiche client</h5><table><tbody>${actions}</tbody></table>
          ${listes ? `<h5>Listes de prix</h5><table><tbody>${listes}</tbody></table>` : ""}</div>
      </div>
      <h5>Visites commerciales (tournées)</h5>
      ${visites ? `<table><thead><tr><th>Date</th><th>Commercial</th><th>Statut</th><th>Résultat</th><th>Compte rendu · suite</th><th></th></tr></thead><tbody>${visites}</tbody></table>`
        : '<div class="b2b-muted">Aucune visite dans une tournée commerciale. « 🧭 Mettre en tournée » pour en planifier une.</div>'}
      ${comm ? `<h5>Commentaires</h5><table><tbody>${comm}</tbody></table>` : ""}
    </div>`);
    dlg.$body.on("click", ".b2b-autour-btn", (e) => {
      const g = l.geo || {};
      this.autour = { client, nom: l.nom || client, km: Number(e.currentTarget.dataset.km), lat: g.lat, lng: g.lng };
      this.secteurs = [];
      this.$root.find("#b2b-ville").val("");
      dlg.hide();
      this.rendre();
    });
    dlg.set_primary_action("🧭 Mettre en tournée", () => { dlg.hide(); this.planifier([client]); });
    dlg.set_secondary_action_label("🤝 Visite faite (hors tournée)");
    dlg.set_secondary_action(() => { dlg.hide(); this.visite(client); });
    dlg.show();
  }

  // ---------------------------------------------------------------- actions

  visite(client) {
    const d = new frappe.ui.Dialog({
      title: "Visite commerciale",
      fields: [{ fieldname: "note", fieldtype: "Small Text", label: "Ce qui s’est dit (facultatif)" }],
      primary_action_label: "Enregistrer la visite d’aujourd’hui",
      primary_action: async (v) => {
        d.hide();
        await frappe.call({ method: B2B_API + ".visite_faite", args: { client, note: v.note || "" } });
        frappe.show_alert({ message: "Visite enregistrée sur la fiche client.", indicator: "green" });
        this.charger();
      },
    });
    d.show();
  }

  rattacher(client) {
    const options = [""].concat((this.donnees || {}).commerciaux || []);
    const d = new frappe.ui.Dialog({
      title: "Commercial du client",
      fields: [{ fieldname: "sales_person", fieldtype: "Select", label: "Commercial", options: options.join("\n") }],
      primary_action_label: "Rattacher",
      primary_action: async (v) => {
        d.hide();
        await frappe.call({ method: B2B_API + ".rattacher_commercial", args: { client, sales_person: v.sales_person || null } });
        frappe.show_alert({ message: v.sales_person ? `Rattaché à ${v.sales_person}.` : "Plus de commercial rattaché.", indicator: "green" });
        this.charger();
      },
    });
    d.show();
  }
}
