/**
 * « Stock par entrepôt » — le stock vu du téléphone.
 *
 *  - Solde : le stock d'un entrepôt (ou de tous). L'employé qui a un entrepôt (son véhicule) le voit par défaut.
 *    Toucher un article ouvre ses mouvements (entrées / sorties).
 *  - Entrées / Sorties : sur une période, les mouvements par article, en deux sous-onglets. Chaque sortie dit
 *    sa pièce, sa commande, son client et les tâches de la commande ; chaque entrée dit son origine (transfert
 *    depuis tel entrepôt et qui l’a validé, achat et fournisseur, BL retour et sa commande, rapprochement).
 *  - Transfert (responsable magasin) : d'un entrepôt à un autre, sur les trajets du réglage ; un trajet qui
 *    touche un véhicule est en double validation (l'employé ↔ un responsable magasin, en va-et-vient).
 *  - À zéro (responsable magasin) : ramène un entrepôt à zéro par transferts avec le Magasin, pour que
 *    l'écart ne reste que sur le Magasin.
 *  - Inventaire du Magasin (rôle Inventaire Magasin, bouton du Solde) : tous les articles suivis au Magasin,
 *    une case par emplacement, enregistrement au fil de l'eau, clôture = une écriture Stock Reconciliation.
 * Toute la règle est côté serveur (customization_app.stock_entrepots).
 */

frappe.pages["stock-entrepots"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Stock", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("stock_entrepots", {}));
  wrapper.se = new StockEntrepots(wrapper);
};
frappe.pages["stock-entrepots"].on_page_show = function (wrapper) {
  if (wrapper.se && wrapper.se.pret) wrapper.se.rafraichir();
};

const SE_API = "customization_app.stock_entrepots.";
const SE_INV = "customization_app.inventaire_magasin.";
const SE_PAGE = 80;
const se_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const se_q = (n) => (Math.round((+n || 0) * 1000) / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 3 });
const se_signe = (n) => (n > 0 ? "+" : "") + se_q(n);
const se_dt = (d) => (d ? `${d.slice(8, 10)}/${d.slice(5, 7)}` : "");
const se_court = (wh) => String(wh || "").replace(/ - [^-]+$/, "");
/** Verdict d'enquête d'une ligne en écart : 🤖 Explicable / Probable / Inexpliqué — explication (+ action). */
const se_verdict = (l) => l.verdict ? `<div class="se-note se-verdict ${l.verdict === "Explicable" ? "e" : l.verdict === "Probable" ? "p" : "i"}" style="margin-left:18px"><b>🤖 ${se_esc(l.verdict)}</b>${l.enquete ? ` — ${se_esc(l.enquete)}` : ""}</div>` : "";
/** Bloc « Enquête » d'une fiche terminée : conclusion, dossier (interventions, BL hors stock, brouillons, kits), bouton de (re)lancement. */
const se_enquete = (d, responsable) => {
  const q = d.enquete, f = d.fiche;
  if (f.statut !== "Terminée" || !f.nb_ecarts) return "";
  const bouton = responsable ? `<button type="button" class="btn btn-default btn-xs" data-enqueter="${se_esc(f.name)}">🔎 ${q ? "Relancer l’enquête" : "Enquêter"}</button>` : "";
  if (!q) return `<div class="se-note" style="margin-top:4px"><b>Enquête :</b> pas encore faite (lancée automatiquement à la clôture, ou à la demande). ${bouton}</div>`;
  const inter = (q.interventions || []).filter((t) => t.statut !== "Cancelled");
  const lig = (t) => `<li>${se_esc(t.date || "")} <b>${se_esc(t.type || "")}</b> ${se_esc(t.client || "")}${t.commande ? ` · ${se_lien("Sales Order", t.commande)}` : ""}${t.gps ? ` · ${se_esc(t.gps)}` : ""}${(t.articles || []).length ? ` · ${t.articles.map((a) => `${se_q(a.qty)} ${se_esc(a.item_code)}`).join(", ")}` : ""}</li>`;
  return `<div class="se-enquete"><div class="se-note" style="margin-top:4px"><b>Enquête${q.ia ? " (règles + IA)" : " (règles seules)"} :</b> ${se_esc(q.synthese || "")} · ${se_esc(q.le || "")}${q.par ? ` par ${se_esc(q.par)}` : ""} ${bouton}</div>
    ${q.conclusion ? `<div class="se-conclusion">🤖 ${se_esc(q.conclusion)}</div>` : ""}
    <details class="se-dossier"><summary>Dossier : période ${se_esc((q.periode || {}).depuis || "")} → ${se_esc((q.periode || {}).jusqu_a || "")} · ${inter.length} intervention(s) · ${(q.bls || []).length} BL · ${(q.mauvais_stock || []).length} ligne(s) sortie(s) d’un autre stock · ${(q.brouillons || []).length} brouillon(s) · ${(q.transferts || []).length} transfert(s) · ${(q.kits || []).length} kit(s) compensé(s)</summary>
      ${inter.length ? `<div class="t">Interventions de l’employé</div><ul>${inter.map(lig).join("")}</ul>` : ""}
      ${(q.mauvais_stock || []).length ? `<div class="t">Sorti d’un autre stock que le sien</div><ul>${q.mauvais_stock.map((m) => `<li>${se_lien("Delivery Note", m.bl)} ${se_esc(m.date || "")} ${se_esc(m.client || "")} : ${se_q(m.qty)} ${se_esc(m.item_code)} depuis <b>${se_esc(se_court(m.warehouse))}</b></li>`).join("")}</ul>` : ""}
      ${(q.brouillons || []).length ? `<div class="t">Brouillons jamais validés</div><ul>${q.brouillons.map((b) => `<li>${se_lien(b.type || "Delivery Note", b.name)} ${se_esc(b.date || "")}</li>`).join("")}</ul>` : ""}
      ${(q.kits || []).length ? `<div class="t">Balance des kits</div><ul>${q.kits.map((k) => `<li>${k.n > 0 ? "+" : ""}${k.n} ${se_esc(k.kit)} ⇄ ${Object.entries(k.composants).map(([c, n]) => `${n > 0 ? "+" : ""}${n} ${se_esc(c)}`).join(", ")}</li>`).join("")}</ul>` : ""}
      ${(q.transferts || []).length ? `<div class="t">Transferts</div><ul>${q.transferts.map((t) => `<li>${se_lien("Stock Entry", t.name)} ${se_esc(t.date || "")} : ${t.lignes.map((l) => `${l.qty > 0 ? "+" : ""}${se_q(l.qty)} ${se_esc(l.item_code)}`).join(", ")}</li>`).join("")}</ul>` : ""}
      ${(q.bls || []).length ? `<div class="t">BL de la période</div><ul>${q.bls.map((b) => `<li>${se_lien("Delivery Note", b.name)} ${se_esc(b.date || "")} ${se_esc(b.client || "")}${b.retour ? " (retour)" : ""} : ${b.lignes.map((l) => `${se_q(l.qty)} ${se_esc(l.item_code)}${l.warehouse && !b.retour ? ` <small>(${se_esc(se_court(l.warehouse))})</small>` : ""}`).join(", ")}</li>`).join("")}</ul>` : ""}
    </details></div>`;
};
const se_img = (url) => (url ? `<img class="se-img" src="${se_esc(url)}" loading="lazy">` : `<div class="se-img">📦</div>`);
const se_lien = (doctype, name, texte) => `<a class="se-lien" href="${frappe.utils.get_form_link(doctype, name)}">${se_esc(texte || name)}</a>`;
const se_argent = (v) => `${Math.round(+v || 0).toLocaleString("fr-FR")} DT`;
const SE_STATUTS = { Completed: "✅", Open: "🕒", Cancelled: "✖️" };
const se_hm = (dt) => (dt ? String(dt).slice(11, 16) : "");                 // « 2026-10-27 14:40:12 » → 14:40
const se_dthm = (dt) => (dt ? `${se_dt(dt)} ${se_hm(dt)}` : "");           // → 27/10 14:40
const SE_BL = "customization_app.sorties_magasin.";                            // onglet BL (sorties du Magasin)

class StockEntrepots {
  constructor(wrapper) {
    this.$r = $(wrapper).find(".se-page");
    this.onglet = "solde";
    this.negatifs = false;
    this.reappro = false;
    this.tri = "nom";           // vue Solde : nom | zone | qte | valeur | mvt
    this.triDesc = false;       // 2e clic sur la même puce : sens inversé
    this.selSolde = new Set();
    this.periode = "30j";
    this.sensMvt = "sorties";   // sous-onglet Entrées / Sorties (≠ sens(), le De → Vers du transfert)
    this.panier = [];
    this.jetons = {};
    this.init();
  }

  async init() {
    this.ctx = (await frappe.call(SE_API + "get_context")).message;
    const r = this.$r, ctx = this.ctx;
    this.entrepot = ctx.defaut || "";
    // Ouverture directe sur un entrepôt (panneau d'administration des sous-traitants : route_options.entrepot).
    const voulu_wh = frappe.route_options && frappe.route_options.entrepot;
    if (voulu_wh) { delete frappe.route_options.entrepot; if ((ctx.entrepots || []).some((x) => x.name === voulu_wh)) this.entrepot = voulu_wh; }
    if (!ctx.responsable) r.find('[data-onglet="transfert"], [data-onglet="zero"]').remove();
    if (!ctx.verification) r.find('[data-onglet="verif"]').remove();
    // Sous-traitant : son stock et rien d'autre — pas d'onglet BL (sorties du Magasin), pas de « Tous ».
    if (ctx.sous_traitant) r.find('[data-onglet="bl"]').remove();
    if (!ctx.inventaire) r.find("#se-inv-bouton").remove();
    else r.find("#se-inv-bouton").on("click", () => this.montrer("inventaire"));
    r.find("#se-s-reappro").on("click", (e) => { this.reappro = !this.reappro; $(e.currentTarget).toggleClass("on", this.reappro); this.chargerSolde(); });
    r.find("#se-s-aucun").on("click", () => { this.selSolde.clear(); this.peindreSolde(); });
    r.find("#se-s-tout").on("click", () => { (this.solde ? this.triees() : []).forEach((a) => this.selSolde.add(a.item_code)); this.peindreSolde(); });
    r.find("#se-s-transferer").on("click", () => this.transfererSelection());
    r.find("#se-s-vers-cible").on("click", () => this.ajouterSelectionAuCible());
    r.find("#se-s-cible").on("click", () => this.ouvrirCible());
    r.find(".se-onglet").on("click", (e) => this.montrer($(e.currentTarget).attr("data-onglet")));
    r.find("#se-guide").on("click", () => this.ouvrirGuide());
    this.peindreEntrepots();

    let t1 = null, t2 = null;
    r.find("#se-s-recherche").on("input", () => { clearTimeout(t1); t1 = setTimeout(() => this.chargerSolde(), 300); });
    r.find("#se-s-negatifs").on("click", (e) => { this.negatifs = !this.negatifs; $(e.currentTarget).toggleClass("on", this.negatifs); this.chargerSolde(); });
    r.find("#se-s-tris [data-tri]").on("click", (e) => {
      const t = $(e.currentTarget).attr("data-tri");
      // Même puce : on inverse le sens. Autre puce : sens naturel (nom A→Z, zone dans l’ordre, quantité /
      // valeur du plus grand au plus petit, dernier mouvement du plus récent au plus ancien).
      if (t === this.tri) this.triDesc = !this.triDesc; else { this.tri = t; this.triDesc = false; }
      this.nSolde = SE_PAGE;
      if (this.solde) this.peindreSolde();
    });
    r.find("#se-o-recherche").on("input", () => { clearTimeout(t2); t2 = setTimeout(() => this.chargerSorties(), 300); });
    r.find("#se-periodes .se-chip").on("click", (e) => {
      this.periode = $(e.currentTarget).attr("data-periode");
      r.find("#se-periodes .se-chip").each((_, el) => $(el).toggleClass("on", $(el).attr("data-periode") === this.periode));
      r.find("#se-dates").css("display", this.periode === "dates" ? "flex" : "none");
      if (this.periode === "dates" && !r.find("#se-debut").val()) {
        r.find("#se-debut").val(frappe.datetime.month_start());
        r.find("#se-fin").val(frappe.datetime.get_today());
      }
      this.chargerSorties();
    });
    r.find("#se-debut, #se-fin").on("change", () => this.chargerSorties());
    r.find("#se-sens .se-sous").on("click", (e) => {
      this.sensMvt = $(e.currentTarget).attr("data-sens");
      r.find("#se-sens .se-sous").each((_, el) => $(el).toggleClass("on", $(el).attr("data-sens") === this.sensMvt));
      this.chargerSorties();
    });
    if (ctx.responsable) this.initTransfert();
    r.find("#se-s-attente").on("click", "[data-valider]", (e) => this.ouvrirValidation($(e.currentTarget).attr("data-valider")));
    r.find("#se-s-attente").on("click", "[data-verif]", (e) => { this.comptage = $(e.currentTarget).attr("data-verif"); this.montrer("verif"); });
    frappe.realtime.on("se_transferts_a_valider", () => { this.chargerAttente(); if (this.onglet === "transfert") this.chargerHistorique(); });
    frappe.realtime.on("sm_bl_magasin", () => { if (this.onglet === "bl") this.chargerBL(); else this.majBadgeBL(); });
    this.pret = true;
    // Ouverture directe sur un onglet (bandeau du magasinier : frappe.route_options = { onglet: "bl" }).
    const onglet = frappe.route_options && frappe.route_options.onglet;
    if (onglet) frappe.route_options = null;
    this.montrer(onglet && r.find(`.se-onglet[data-onglet="${onglet}"]`).length ? onglet : "solde");
    await this.chargerAttente();
    this.majBadgeBL();
    const voulu = frappe.route_options && frappe.route_options.valider;
    if (voulu) { frappe.route_options = null; this.ouvrirValidation(voulu); }
  }

  // ── Guide d'utilisation (lisible sur téléphone, selon le rôle) ─────────────
  ouvrirGuide() {
    const resp = !!this.ctx.responsable, mien = this.ctx.mien ? se_esc(this.libelle(this.ctx.mien)) : null;
    const d = new frappe.ui.Dialog({ title: "❓ Stock par entrepôt — guide", size: "large", fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    d.fields_dict.zone.$wrapper.html(`<div class="se-guide">
      <p class="se-note">Le <b>Magasin</b> est la référence ; chaque employé a le stock de son véhicule${mien ? ` (le vôtre : <b>${mien}</b>, affiché en premier)` : ""}. Un transfert qui touche un véhicule (avec le Magasin ou les Articles défectueux) est en <b>double validation</b> : l’employé du véhicule et un responsable magasin doivent être d’accord sur les quantités.</p>
      <div class="et"><div class="t">📦 Solde — ce qu’il y a dans un stock</div><ul>
        <li>Tri : <b>Nom</b> (A→Z), <b>📍 Zone</b> (groupé par emplacement, dans l’ordre des zones du Magasin puis du Hall, « Non rangé » à la fin), <b>Quantité</b> (du plus grand au plus petit), <b>Valeur</b> (responsable), <b>Dernier mouvement</b> (le plus récent d’abord ; sans mouvement en dernier). Un second clic sur la même puce inverse le sens (↑ / ↓).</li>
        <li>Choisissez un stock (pastilles) ou « Tous » ; cherchez un article ; « ⚠️ Négatifs » montre les quantités en dessous de zéro.</li>
        <li>Toucher un article ouvre ses entrées / sorties.</li>
        ${resp ? `<li>Cochez des articles (Maj + clic = plage) → « 🔁 Transférer depuis le Magasin » vers un véhicule, ou « 🎯 Dans le stock cible ».</li>
        <li>« 🎯 Stock cible » : ce que le véhicule doit contenir ; « 🔻 À réappro. » liste ce qui manque ; « Réassort » prépare le transfert en un geste.</li>` : ""}
        <li>Un bandeau en haut vous signale ce qui attend votre geste : un transfert à valider, une vérification à valider ou à accepter.</li></ul></div>
      <div class="et"><div class="t">↕️ Entrées / Sorties — ce qui est arrivé, ce qui est parti</div><ul>
        <li>Par période (7 j, 30 j, mois, dates), deux sous-onglets : 📥 Entrées et 📤 Sorties.</li>
        <li>📤 Chaque sortie dit sa pièce (BL, facture, transfert), la commande et les tâches de cette commande.</li>
        <li>📥 Chaque entrée dit son origine : transfert depuis quel stock (demandé et validé par qui), achat (fournisseur, n° de facture), BL retour d’un client (commande et tâches), rapprochement d’une vérification.</li></ul></div>
      ${resp ? `<div class="et"><div class="t">🔁 Transfert — d’un stock à un autre</div><ul>
        <li>Choisissez De / Vers (⇄ inverse). Cherchez un article, indiquez la quantité, touchez ＋ : il entre dans le panier. Corrigez les quantités, puis « Valider le transfert ».</li>
        <li>« 🎯 Compléter selon le stock cible » remplit le panier avec ce qui manque au véhicule (sa cible, sinon le modèle générique des réglages).</li>
        <li><b>Trajets</b> : seuls ceux du réglage sont permis (Hall ↔ Magasin seulement, jamais un véhicule vers le Hall…). Sous De / Vers, la page dit si le trajet est immédiat, en double validation ou interdit.</li>
        <li><b>Double validation</b> (Magasin ↔ véhicule, véhicule ↔ Articles défectueux) : le stock ne bouge pas tant que l’employé du véhicule et un responsable magasin ne sont pas d’accord. Chacun, à son tour, accepte les quantités de l’autre (→ transfert validé) ou les corrige (→ la demande repart chez l’autre), jusqu’à l’accord. Pour votre propre véhicule, c’est un autre responsable qui valide.</li>
        <li>« Derniers transferts » : ⏳ au tour de qui (rouge après 24 h), « 🔍 Vérifier et valider » quand c’est à vous, « 🔍 Détail », « Annuler ».</li></ul></div>
      <div class="et"><div class="t">0️⃣ À zéro — remettre un véhicule à zéro</div><ul>
        <li>Les quantités négatives sont apportées depuis le Magasin, les positives y retournent : l’écart ne reste que sur le Magasin. Aperçu avant validation, articles cochés seulement.</li></ul></div>` : `<div class="et"><div class="t">🔁 Transfert — à valider</div><ul>
        <li>Quand un transfert touche votre véhicule (chargement depuis le Magasin, retour au Magasin, articles défectueux), un bandeau « Transfert à valider » apparaît (aussi dans Ma journée).</li>
        <li>Vérifiez chaque quantité : d’accord → « Valider », le stock bouge ; pas d’accord → corrigez (0 = article retiré) et renvoyez : le responsable magasin accepte ou corrige à son tour, jusqu’à ce que vous soyez d’accord tous les deux.</li></ul></div>`}
      <div class="et"><div class="t">✅ Vérif. — compter son stock, chaque semaine</div><ul>
        <li>Le jour fixé, une fiche de comptage et deux tâches sont créées (l’employé et le responsable). « Commencer le comptage » photographie le stock système ; saisissez le compté, « 💾 Enregistrer » pour reprendre plus tard.</li>
        <li>« ✅ Terminer le comptage » = votre validation → <b>à valider</b> par un responsable magasin (autre que vous).</li>
        <li>Le responsable vérifie, corrige éventuellement, valide → <b>à accepter</b> par l’employé : d’accord → « ✅ Accepter », un <b>rapprochement de stock</b> aligne le véhicule ; pas d’accord → corrigez la quantité, la fiche repart chez le responsable, qui a le dernier mot.</li>
        <li>Un article qui a bougé entre le comptage et la validation perd son comptage : à recompter (ligne marquée).</li></ul></div>
      ${this.ctx.inventaire ? `<div class="et"><div class="t">📋 Inventaire du Magasin — compter tout le Magasin par emplacement</div><ul>
        <li>Bouton « 📋 Inventaire du Magasin » (Solde). Tous les articles suivis en stock au Magasin, photo et description, <b>une case par emplacement</b> (zones de l’article dans l’espace Magasin — pas le Hall ; « Non rangé » sans zone) : la somme des cases = compté, comparé au système. « ＋ » ajoute un emplacement où l’article a été trouvé : aussitôt ajouté à la fiche article (✕ pour le retirer).</li>
        <li><b>Onglet BL</b> : les BL contenant des articles du Magasin attendent l’approbation du <b>magasinier principal</b> (rôle « Magasinier principal » : lui seul approuve ou rejette ; bandeau rouge sur toutes ses pages tant qu’un BL attend). En son absence, « Sortie exceptionnelle » : l’employé valide lui-même, le stock sort, et la sortie est tracée pour vérification (Conforme / Écart) à son retour.</li>
        <li><b>Inventaire partiel</b> : à l’ouverture, cochez des zones ; seuls leurs articles sont sur la feuille (un article rangé à deux endroits garde toutes ses cases). Plusieurs inventaires partiels peuvent être ouverts en parallèle s’ils ne se recoupent pas ; un inventaire complet exclut les autres. La liste montre, zone par zone, la date du dernier inventaire clôturé.</li>
        <li>Puce « Négatifs » : seuls les articles dont le système est sous zéro (chiffre en rouge). « 📍 Par zone » : les articles groupés par emplacement dans l’ordre des zones, « Non rangé » en dernier — pour faire le tour du magasin rayon par rayon.</li>
        <li>Enregistrement automatique ligne par ligne : plusieurs personnes peuvent compter en même temps, et reprendre plus tard. Filtres : zone, à compter / comptés / écarts.</li>
        <li>« ✅ Clôturer » : aperçu, puis <b>une écriture de stock</b> (Stock Reconciliation) sur le Magasin pour les articles comptés en écart ; un article qui a bougé depuis son comptage reçoit son écart sur le stock du moment. Les non comptés ne bougent pas.</li></ul></div>` : ""}
      <p class="se-note"><b>Erreurs courantes</b> : transfert envoyé par erreur → « Annuler la demande » tant qu’il n’est pas validé · comptage terminé trop tôt → le responsable « Renvoie au comptage » · réglages (Magasin, trajets permis, seuils, cibles, jours de vérification) : <a href="/app/config-stock-entrepot">Config Stock Entrepot</a>.</p>
    </div>`);
    d.show();
  }

  // ── Transferts en double validation (un trajet qui touche un véhicule : l'employé ↔ le Magasin) ──
  /** Les demandes où c'est à MOI de répondre : en bandeau (Solde) et en badge de l'onglet Transfert. */
  async chargerAttente() {
    const liste = (await frappe.call({ method: SE_API + "transferts_a_valider" })).message || [];
    this.attente = liste;
    const verifs = this.ctx.verification ? ((await frappe.call({ method: SE_API + "verifications_a_traiter" })).message || []) : [];
    this.$r.find("#se-v-badge").toggle(!!verifs.length).text(verifs.length);
    const miens = liste.filter((t) => t.a_moi);
    this.$r.find("#se-s-attente").html(verifs.map((v) => `<div class="se-bandeau">
        <div class="t">🧾 Vérification ${se_esc(v.libelle)} — ${v.geste === "valider" ? "à valider" : v.geste === "accepter_pour" ? "employé parti : à accepter ou annuler" : "à accepter"}</div>
        <div class="d">${se_esc(v.texte)}</div>
        <button type="button" class="btn btn-primary btn-sm" data-verif="${se_esc(v.name)}">${v.geste === "valider" ? "🔍 Vérifier et valider" : v.geste === "accepter_pour" ? "🔍 Voir, accepter à sa place ou annuler" : "🔍 Voir et accepter"}</button>
      </div>`).join("") + miens.map((t) => `<div class="se-bandeau">
        <div class="t">🔁 Transfert ${se_esc(t.name)} à valider — ${se_esc(this.libelle(t.de))} → ${se_esc(this.libelle(t.vers))}</div>
        <div class="d">${t.lignes.length} article(s) · ${se_esc(this.dernierTour(t))} · ${se_esc(t.quand)}${t.remarque ? ` · ${se_esc(t.remarque)}` : ""}</div>
        <button type="button" class="btn btn-primary btn-sm" data-valider="${se_esc(t.name)}">🔍 Vérifier les quantités et valider</button>
      </div>`).join(""));
    this.$r.find("#se-t-badge").toggle(!!miens.length).text(miens.length);
  }

  /** « demandé par Hedi » ou « Akram a changé 2 quantité(s) ». */
  dernierTour(t) {
    const h = (t.tours || [])[(t.tours || []).length - 1];
    if (!h) return `demandé par ${t.par}`;
    return h.changes == null ? `demandé par ${h.nom}` : `${h.nom} a changé ${h.changes} quantité(s)`;
  }

  /** Feuille de détail d'un transfert : photo, code, nom, quantité ; état de validation. */
  ouvrirDetail(t) {
    const lignes = (t.lignes || []).map((l) => `<div class="se-z-ligne">${se_img(l.image)}
        <div style="flex:1;min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div><div class="se-code">${se_esc(l.item_code)}</div></div>
        <div class="mvt">${se_q(l.qte)}<small>${se_esc(l.uom || "")}</small></div></div>`).join("");
    const d = new frappe.ui.Dialog({ title: `🔁 ${t.name}`, size: "large", fields: [{ fieldtype: "HTML", fieldname: "corps" }] });
    d.fields_dict.corps.$wrapper.html(`<div class="se-note">${se_esc(this.libelle(t.de))} → <b>${se_esc(this.libelle(t.vers))}</b> · ${se_esc(t.par)} · ${se_esc(t.quand || `${se_dt(t.date)} ${t.heure || ""}`)}</div>
      ${this.etatTransfert(t)}${t.remarque ? `<div class="se-note">${se_esc(t.remarque)}</div>` : ""}
      ${t.attente ? `<div class="se-note">Quantités de la proposition en cours.</div>` : ""}
      <div style="margin-top:8px">${lignes}</div>
      <div class="se-note" style="margin-top:8px">${(t.lignes || []).length} article(s) · ${se_q((t.lignes || []).reduce((a, l) => a + l.qte, 0))} unité(s) · ${se_lien("Stock Entry", t.name, "ouvrir la fiche")}</div>`);
    d.show();
  }

  /** ⏳ au tour de qui / ✅ accord final, pour une carte ou une feuille de l'historique. */
  etatTransfert(t) {
    if (t.attente) {
      const vieux = t.attente.age_h > 24;
      return `<div class="se-attente ${vieux ? "vieux" : ""}">⏳ Au tour de ${se_esc(t.attente.attente_de)}${t.attente.propositions > 1 ? ` · ${t.attente.propositions} propositions` : ""}${vieux ? ` · depuis ${Math.round(t.attente.age_h / 24)} j` : ""}</div>`;
    }
    if (t.validation) {
      return `<div class="se-valide">✅ Accord final : ${se_esc(t.validation.par)} le ${se_esc(t.validation.le)}${t.validation.propositions > 1 ? ` · après ${t.validation.propositions} propositions` : ""}${t.validation.ecart ? `<div class="ecart">⚠️ ${se_esc(t.validation.ecart)}</div>` : ""}</div>`;
    }
    return t.docstatus === 2 ? `<span class="se-badge">annulé</span>` : "";
  }

  /** Double validation, à mon tour : mêmes quantités = accord (le stock bouge) ; une quantité changée = la
   *  demande repart chez l'autre côté, jusqu'à ce que l'un accepte les quantités de l'autre. */
  async ouvrirValidation(name) {
    const liste = (await frappe.call({ method: SE_API + "transferts_a_valider" })).message || [];
    this.attente = liste;
    const t = liste.find((x) => x.name === name);
    if (!t) { frappe.show_alert({ message: `Le transfert ${se_esc(name)} n’est plus en attente.`, indicator: "orange" }, 5); this.chargerAttente(); return; }
    if (!t.a_moi) { frappe.msgprint({ title: `🔁 ${se_esc(t.name)}`, indicator: "orange", message: `⏳ Au tour de <b>${se_esc(t.attente_de)}</b> : vous avez déjà donné vos quantités, ou ce transfert ne vous concerne pas.` }); return; }
    const autre = t.tour === "Employé" ? "un responsable magasin" : t.employe_nom;
    const dernier = t.tours[t.tours.length - 1];
    const lire = () => t.lignes.map((l) => ({ item_code: l.item_code,
      qte: Math.max(0, +String(d.$wrapper.find(`input[data-recu="${CSS.escape(l.item_code)}"]`).val()).replace(",", ".") || 0) }));
    const nChanges = () => lire().filter((l, i) => l.qte !== t.lignes[i].qte).length;
    const d = new frappe.ui.Dialog({ title: `🔁 ${t.name} — à valider`, size: "large", fields: [{ fieldtype: "HTML", fieldname: "corps" }],
      primary_action_label: "✅ D’accord : valider", primary_action: () => {
        const lignes = lire(), n = nChanges();
        const go = async () => {
          d.hide();
          const res = (await frappe.call({ method: SE_API + "valider_transfert", args: { name: t.name, lignes }, freeze: true, freeze_message: "Validation…" })).message;
          if (res.statut === "renvoye") frappe.show_alert({ message: `↩️ ${se_esc(t.name)} renvoyé à ${se_esc(res.attente_de)} avec vos quantités (${res.changes.length} changement(s)) — le stock ne bouge pas encore`, indicator: "orange" }, 8);
          else if (res.statut === "supprime") frappe.show_alert({ message: `Transfert ${se_esc(t.name)} abandonné : toutes les quantités à 0`, indicator: "orange" }, 6);
          else frappe.show_alert({ message: `✅ Accord : ${se_esc(t.name)} validé, ${res.lignes} article(s) transféré(s)${res.ecarts.length ? ` · ${res.ecarts.length} écart(s) avec la demande` : ""}`,
            indicator: res.ecarts.length ? "yellow" : "green" }, 7);
          this.chargerAttente(); this.rafraichir();
        };
        if (n) frappe.confirm(`<b>${n}</b> quantité(s) changée(s) : la demande repart chez <b>${se_esc(autre)}</b>, qui acceptera vos quantités ou les changera à son tour. Le stock ne bouge pas encore. Renvoyer ?`, go);
        else go();
      } });
    const tours = t.tours.length > 1 ? `<ul class="se-tours">${t.tours.map((h) => `<li>${se_esc(h.nom)} (${h.cote === "Employé" ? "employé" : "magasin"}) · ${se_dt((h.le || "").slice(0, 10))} ${se_esc((h.le || "").slice(11, 16))} : ${h.changes == null ? "demande" : `${h.changes} quantité(s) changée(s)`}</li>`).join("")}</ul>` : "";
    d.fields_dict.corps.$wrapper.html(`<div class="se-note">${se_esc(this.libelle(t.de))} → <b>${se_esc(this.libelle(t.vers))}</b> · demandé par ${se_esc(t.par)} · ${se_esc(t.quand)}${t.remarque ? ` · ${se_esc(t.remarque)}` : ""}</div>
      ${tours}
      <div class="se-note">Vérifiez chaque quantité. <b>D’accord</b> → validez : le stock bouge. <b>Pas d’accord</b> → corrigez (0 = article retiré) : la demande repart chez ${se_esc(autre)}, jusqu’à ce que vous soyez d’accord tous les deux.</div>
      <div style="margin-top:8px">${t.lignes.map((l) => {
        const change = l.qte_precedente != null && l.qte !== l.qte_precedente;
        return `<div class="se-cb-ligne ${change ? "change" : ""}">${se_img(l.image)}
        <div class="txt"><div class="se-nom" style="font-size:13.5px">${se_esc(l.item_name || l.item_code)}</div><div class="se-code">${se_esc(l.item_code)} · demandé <b>${se_q(l.qte_initiale)}</b> ${se_esc(l.uom || "")}</div>
          ${change ? `<div class="prev">${se_esc(dernier.nom)} : ${se_q(l.qte)} (avant : ${se_q(l.qte_precedente)})</div>` : ""}</div>
        <input type="number" inputmode="decimal" min="0" step="any" class="form-control c" data-recu="${se_esc(l.item_code)}" value="${se_esc(l.qte)}"></div>`;
      }).join("")}</div>`);
    d.$wrapper.on("input", "input[data-recu]", () => {
      const n = nChanges();
      d.get_primary_btn().text(n ? `↩️ Renvoyer avec mes quantités (${n})` : "✅ D’accord : valider");
    });
    d.show();
  }

  /** Transfert : complète le panier avec ce qui manque au stock cible de la destination (articles ciblés seulement). */
  async reassortCible() {
    const [source, cible] = this.sens();
    // Sans cible propre au véhicule, le modèle générique du réglage sert de cible.
    const d = (await frappe.call({ method: SE_API + "get_stock_cible", args: { entrepot: cible, avec_modele: 1 } })).message;
    if (!(d.lignes || []).length) {
      frappe.msgprint({ title: "Pas de stock cible", indicator: "orange", message: `Ni stock cible pour <b>${se_esc(this.libelle(cible))}</b>, ni modèle générique.<br>Remplissez le <b>stock cible générique</b> dans <a href="/app/config-stock-entrepot">les réglages</a>, ou définissez celui de ce stock depuis l’onglet <b>Solde</b> → <b>🎯 Stock cible</b>.` });
      return;
    }
    const base = d.modele ? "modèle générique (ce stock n’a pas de cible propre)" : "stock cible";
    const manque = (d.lignes || []).filter((l) => l.manque > 0);
    if (!manque.length) { frappe.show_alert({ message: `Rien à compléter : ${se_esc(this.libelle(cible))} est au niveau du ${base}.`, indicator: "green" }, 5); return; }
    let ajoutes = 0;
    manque.forEach((l) => {
      if (this.panier.some((x) => x.item_code === l.item_code)) return;
      this.panier.push({ item_code: l.item_code, item_name: l.item_name, image: l.image, uom: l.uom, qte: l.manque, qte_source: l.qte_source, qte_cible: l.qte });
      ajoutes += 1;
    });
    await this.majQuantitesPanier();
    this.peindreTrouves();
    frappe.show_alert({ message: `🎯 ${ajoutes} article(s) ajouté(s) selon le ${base} de ${se_esc(this.libelle(cible))}${manque.length - ajoutes ? ` (${manque.length - ajoutes} déjà dans le panier)` : ""} — vérifiez puis validez`, indicator: "blue" }, 6);
  }

  majBoutonReassort() {
    const [, cible] = this.sens();
    const w = this.ctx.entrepots.find((x) => x.name === cible);
    // Visible pour tout stock d'employé, cible définie ou non : sans cible, le bouton dit où la définir.
    this.$r.find("#se-t-reassort").toggle(!!(w && !w.magasin)).toggleClass("on", (this.ctx.cibles || []).includes(cible));
  }

  montrer(onglet) {
    this.onglet = onglet;
    if (onglet !== "verif") this.comptage = null;
    if (onglet !== "inventaire") this.feuilleInv = null;
    const r = this.$r;
    r.find(".se-onglet").each((_, el) => $(el).toggleClass("on", $(el).attr("data-onglet") === onglet));
    r.find(".se-vue").each((_, el) => $(el).toggle($(el).attr("data-vue") === onglet));
    r.find("#se-entrepots").toggle(onglet === "solde" || onglet === "sorties");
    this.rafraichir();
  }

  rafraichir() {
    ({ solde: () => this.chargerSolde(), sorties: () => this.chargerSorties(),
       transfert: () => { this.chargerHistorique(); this.majQuantitesPanier(); this.majRegle(); },
       zero: () => (this.zeroEntrepot ? this.ouvrirZero(this.zeroEntrepot) : this.chargerZero()),
       verif: () => (this.comptage ? this.ouvrirComptage(this.comptage) : this.chargerVerif()),
       bl: () => this.chargerBL(),
       inventaire: () => (this.feuilleInv ? this.ouvrirInventaire(this.feuilleInv) : this.chargerInventaire()) })[this.onglet]();
    this.majBarreSolde();
  }

  /** Appel serveur dont seule la DERNIÈRE réponse compte (frappe rapide au clavier, changement d'onglet). */
  async appel(cle, method, args) {
    const jeton = (this.jetons[cle] = (this.jetons[cle] || 0) + 1);
    const r = await frappe.call({ method: SE_API + method, args });
    return jeton === this.jetons[cle] ? r.message : undefined;
  }

  // ── Choix de l'entrepôt (Solde et Sorties) ───────────────────────────────
  peindreEntrepots() {
    const ctx = this.ctx;
    const rang = (w) => (w.name === ctx.mien ? 0 : w.magasin ? 1 : 2);
    const liste = ctx.entrepots.slice().sort((a, b) => rang(a) - rang(b));
    this.$r.find("#se-entrepots").html(liste.map((w) => `<span class="se-chip ${w.name === this.entrepot ? "on" : ""}" data-wh="${se_esc(w.name)}">`
        + `${w.name === ctx.mien ? "👤 " : w.magasin ? "🏬 " : ""}${se_esc(w.libelle)}</span>`).join("")
      + (liste.length > 1 ? `<span class="se-chip ${this.entrepot ? "" : "on"}" data-wh="">Tous</span>` : ""));
    this.$r.find("#se-entrepots .se-chip").on("click", (e) => {
      this.entrepot = $(e.currentTarget).attr("data-wh");
      this.peindreEntrepots();
      this.rafraichir();
    });
  }

  libelle(wh) {
    const w = this.ctx.entrepots.find((x) => x.name === wh);
    return w ? w.libelle : se_court(wh);
  }

  // ── Solde ──────────────────────────────────────────────────────────────
  async chargerSolde() {
    const r = this.$r;
    const seuil = this.entrepot && this.ctx.seuils[this.entrepot];
    const cibleOk = !!(this.ctx.responsable && this.entrepot && this.entrepot !== this.ctx.magasin);
    r.find("#se-s-reappro").toggle(!!((seuil || (this.ctx.cibles || []).includes(this.entrepot)) && this.ctx.responsable));
    r.find("#se-s-cible").toggle(cibleOk).toggleClass("on", (this.ctx.cibles || []).includes(this.entrepot));
    r.find("#se-s-cible-vue").hide(); r.find("#se-s-solde").show();
    if (!seuil) this.reappro = false;
    r.find("#se-s-reappro").toggleClass("on", this.reappro);
    const res = await this.appel("solde", "get_solde", { entrepot: this.entrepot || null,
      recherche: r.find("#se-s-recherche").val() || null, negatifs: this.negatifs ? 1 : 0, a_reappro: this.reappro ? 1 : 0 });
    if (!res) return;
    this.solde = res;
    this.nSolde = SE_PAGE;
    this.selSolde.clear();
    this.peindreSolde();
  }

  /** Les articles du solde dans l’ordre de la puce de tri (copie : la réponse du serveur reste par nom). */
  triees() {
    const res = this.solde, t = this.tri, desc = this.triDesc;
    const nom = (a) => (a.item_name || a.item_code).toLocaleLowerCase("fr");
    const cmpNom = (a, b) => nom(a).localeCompare(nom(b), "fr");
    const cles = {
      nom: cmpNom,
      zone: (a, b) => (a.zone_rang - b.zone_rang) || cmpNom(a, b),
      qte: (a, b) => (b.qte - a.qte) || cmpNom(a, b),
      valeur: (a, b) => ((b.valeur || 0) - (a.valeur || 0)) || cmpNom(a, b),
      // Sans mouvement = le plus ancien : en bas de « récent d’abord », en haut du sens inverse.
      mvt: (a, b) => ((b.dernier_mvt || "") < (a.dernier_mvt || "") ? -1 : (b.dernier_mvt || "") > (a.dernier_mvt || "") ? 1 : 0) || cmpNom(a, b),
    };
    const cmp = cles[t] || cmpNom;
    return res.articles.slice().sort(desc ? (a, b) => cmp(b, a) : cmp);
  }

  peindreTris() {
    const r = this.$r, res = this.solde;
    r.find("#se-s-tris [data-tri]").each((_, el) => {
      const $el = $(el), t = $el.attr("data-tri"), on = t === this.tri;
      $el.toggleClass("on", on).find(".sens").text(on ? (this.triDesc ? " ↑" : " ↓") : "");
    });
    r.find('#se-s-tris [data-tri="valeur"]').toggle(!!(res && res.valeur != null));
  }

  peindreSolde() {
    const r = this.$r, res = this.solde, tous = !this.entrepot;
    const coches = !!(this.ctx.responsable && this.entrepot);
    this.peindreTris();
    const liste = this.triees(), parZone = this.tri === "zone";
    let derniereZone;
    r.find("#se-s-info").html(`${res.articles.length} article(s)`
      + (res.negatifs ? ` · <span class="neg">${res.negatifs} négatif(s)</span>` : "")
      + (res.a_reappro ? ` · <span style="color:#c2410c">${res.a_reappro} à réapprovisionner</span>` : "")
      + (res.valeur != null ? ` · valeur ${se_esc(se_argent(res.valeur))}` : "")
      + ` · ${tous ? "tous les entrepôts" : se_esc(this.libelle(this.entrepot))}`);
    const cartes = liste.slice(0, this.nSolde).map((a) => {
      let tete = "";
      if (parZone) {
        const z = a.zone_cle || "Non rangé";
        if (z !== derniereZone) { tete = `<div class="se-i-groupe">📍 ${se_esc(z)}</div>`; derniereZone = z; }
      }
      return tete + `<div class="se-art ${this.selSolde.has(a.item_code) ? "sel" : ""}" data-item="${se_esc(a.item_code)}">
        ${coches ? `<span class="se-coche ${this.selSolde.has(a.item_code) ? "on" : ""}" data-coche>${this.selSolde.has(a.item_code) ? "✓" : ""}</span>` : ""}
        ${se_img(a.image)}
        <div class="txt"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
          <div class="se-code">${se_esc(a.item_code)}${a.zones ? ` · 📍 ${se_esc(a.zones)}` : ""}</div>
          ${a.a_reappro ? `<span class="se-reappro">🔻 à réapprovisionner · ${a.cible != null ? `cible ${se_q(a.cible)}` : `seuil ${se_q(a.seuil)}`} · manque ${se_q(a.a_transferer)}</span>` : ""}
          ${tous ? `<div class="se-wh">${a.entrepots.map((e) => `<span class="${e.qte < 0 ? "neg" : ""}">${se_esc(e.libelle)} <b>${se_q(e.qte)}</b></span>`).join("")}</div>` : ""}
          ${this.tri === "mvt" ? `<div class="se-code">🕓 dernier mouvement : ${a.dernier_mvt ? se_dt(a.dernier_mvt) + "/" + a.dernier_mvt.slice(0, 4) : "aucun"}</div>` : ""}
          ${this.tri === "valeur" && a.valeur != null ? `<div class="se-code">💰 ${se_esc(se_argent(a.valeur))}</div>` : ""}
        </div>
        <div class="se-qte ${a.qte < 0 ? "neg" : ""}">${se_q(a.qte)}<small>${se_esc(a.uom || "")}</small></div>
      </div>`; }).join("");
    r.find("#se-s-liste").html((cartes || `<div class="se-vide">Aucun stock pour ces filtres.</div>`)
      + (liste.length > this.nSolde ? `<button type="button" class="btn btn-default se-plus">Afficher plus (${liste.length - this.nSolde})</button>` : ""));
    r.find("#se-s-liste .se-plus").on("click", () => { this.nSolde += SE_PAGE; this.peindreSolde(); });
    r.find("#se-s-liste .se-art").on("click", (e) => this.voirSorties($(e.currentTarget).attr("data-item")));
    r.find("#se-s-liste [data-coche]").on("click", (e) => {
      e.stopPropagation();
      const c = $(e.currentTarget).closest(".se-art").attr("data-item");
      const visibles = liste.slice(0, this.nSolde).map((a) => a.item_code);
      const idx = visibles.indexOf(c);
      if (e.shiftKey && this.dernierCoche != null && this.dernierCoche !== idx) {
        // Maj + clic : toute la plage entre la dernière case cliquée et celle-ci prend l'état de celle-ci.
        const coche = !this.selSolde.has(c);
        const [d, f] = [Math.min(this.dernierCoche, idx), Math.max(this.dernierCoche, idx)];
        visibles.slice(d, f + 1).forEach((code) => (coche ? this.selSolde.add(code) : this.selSolde.delete(code)));
        window.getSelection && window.getSelection().removeAllRanges();
        this.dernierCoche = idx;
        this.peindreSolde();
        return;
      }
      this.dernierCoche = idx;
      this.selSolde.has(c) ? this.selSolde.delete(c) : this.selSolde.add(c);
      $(e.currentTarget).toggleClass("on", this.selSolde.has(c)).text(this.selSolde.has(c) ? "✓" : "");
      $(e.currentTarget).closest(".se-art").toggleClass("sel", this.selSolde.has(c));
      this.majBarreSolde();
    });
    this.majBarreSolde();
  }

  majBarreSolde() {
    const n = this.selSolde.size, $b = this.$r.find("#se-s-barre");
    this.$r.find("#se-s-compte").text(`${n} article(s) coché(s)`);
    const feuilleCible = this.$r.find("#se-s-cible-vue").is(":visible");
    n && this.onglet === "solde" && this.ctx.responsable && !feuilleCible ? $b.show() : $b.hide();
  }

  /** Le véhicule visé : celui affiché, ou, depuis le Magasin / « Tous », un choix dans un dialogue. */
  choisirVehicule(titre) {
    if (this.entrepot && this.entrepot !== this.ctx.magasin) return Promise.resolve(this.entrepot);
    const choix = this.ctx.entrepots.filter((w) => w.name !== this.ctx.magasin);
    if (!choix.length) return Promise.resolve(null);
    return new Promise((resolve) => {
      const d = new frappe.ui.Dialog({ title: titre, fields: [{ fieldtype: "Select", fieldname: "vehicule", label: "Vers quel stock ?", reqd: 1,
          options: choix.map((w) => ({ value: w.name, label: w.libelle + (w.employes.length ? ` (${w.employes.join(", ")})` : "") })),
          default: this.ctx.mien && this.ctx.mien !== this.ctx.magasin ? this.ctx.mien : choix[0].name }],
        primary_action_label: "Continuer", primary_action: (v) => { choisi = v.vehicule; d.hide(); } });
      let choisi = null;
      d.onhide = () => resolve(choisi);           // hide() déclenche onhide : le choix est posé AVANT
      d.show();
    });
  }

  async ajouterSelectionAuCible() {
    const items = Array.from(this.selSolde);
    if (!items.length) return;
    const cible = await this.choisirVehicule("🎯 Dans le stock cible de…");
    if (!cible) return;
    const r = (await frappe.call({ method: SE_API + "ajouter_au_stock_cible", args: { entrepot: cible, items }, freeze: true })).message;
    if (!(this.ctx.cibles || []).includes(cible)) (this.ctx.cibles = this.ctx.cibles || []).push(cible);
    frappe.show_alert({ message: `🎯 ${r.ajoutes} article(s) ajouté(s) au stock cible de ${se_esc(this.libelle(cible))} (${r.total} au total) — réglez les quantités`, indicator: "green" }, 5);
    this.selSolde.clear();
    if (this.entrepot !== cible) { this.entrepot = cible; this.peindreEntrepots(); }
    this.ouvrirCible();
  }

  /** Feuille « Stock cible » de l'entrepôt : quantité cible par article, manque, réassort en un geste. */
  async ouvrirCible() {
    const entrepot = this.entrepot, r = this.$r;
    const d = (await frappe.call({ method: SE_API + "get_stock_cible", args: { entrepot } })).message;
    const modele = (await frappe.call({ method: SE_API + "get_modele_stock_cible" })).message || { articles: 0 };
    const $v = r.find("#se-s-cible-vue");
    r.find("#se-s-solde").hide(); $v.show();
    this.selSolde.clear(); this.majBarreSolde();
    const lignes = d.lignes.map((l) => Object.assign({}, l));
    const ligne = (l) => `<div class="se-cb-ligne" data-item="${se_esc(l.item_code)}">${se_img(l.image)}
        <div class="txt"><div class="se-nom" style="font-size:13.5px">${se_esc(l.item_name || l.item_code)}</div>
          <div class="se-code">${se_esc(l.item_code)} · ici <b class="${l.qte < 0 ? "neg" : ""}">${se_q(l.qte)}</b> · ${se_esc(this.libelle(d.source))} ${se_q(l.qte_source)}</div></div>
        <input type="number" inputmode="decimal" min="0" step="any" class="form-control c" value="${se_esc(l.qte_cible)}">
        <div class="m ${l.manque > 0 ? "neg" : "pos"}">${l.manque > 0 ? `manque ${se_q(l.manque)}` : "✓"}</div>
        <span class="x" data-enlever title="Retirer">✕</span></div>`;
    const peindre = () => {
      const n = lignes.filter((l) => l.manque > 0).length, u = lignes.reduce((s, l) => s + l.manque, 0);
      $v.html(`<span class="se-retour" data-retour>← Solde</span>
        <div class="se-card">
          <div class="se-v-stock"><div class="t">🎯 Stock cible — ${se_esc(this.libelle(entrepot))}</div>
            <div class="plan">Ce que ce stock doit contenir. Réassort depuis <b>${se_esc(this.libelle(d.source))}</b> : cible − stock actuel.
              · <a class="se-lien" href="/app/config-stock-entrepot">⚙️ Réglages</a></div>
            ${modele.articles ? `<button type="button" class="btn btn-default btn-sm" data-modele style="margin-top:6px">📋 Appliquer le modèle générique (${modele.articles} articles)</button>`
              : `<div class="se-note">Pas de modèle générique : définissez-le dans les réglages pour l’appliquer à chaque véhicule.</div>`}</div>
          <input type="search" class="form-control se-saisie" id="se-cb-recherche" style="margin-top:8px" placeholder="➕ Ajouter un article (nom ou code)…">
          <div id="se-cb-trouves"></div>
          <div id="se-cb-lignes" style="margin-top:6px">${lignes.map(ligne).join("") || `<div class="se-note" style="padding:10px 0">Aucun article : cherchez ci-dessus, ou cochez des articles dans le solde puis « Dans le stock cible ».</div>`}</div>
          <div class="se-c-barre"><button type="button" class="btn btn-default" data-enregistrer>💾 Enregistrer</button>
            <button type="button" class="btn btn-primary" data-reassort ${n ? "" : "disabled"}>🔁 Réassort (${n} art. · ${se_q(u)} u.)</button></div>
        </div>`);
      let t = null;
      $v.find("#se-cb-recherche").on("input", (e) => { clearTimeout(t); const txt = e.currentTarget.value.trim(); t = setTimeout(async () => {
        const tr = txt.length < 2 ? [] : (await frappe.call({ method: SE_API + "rechercher_articles", args: { txt, source: d.source, cible: entrepot } })).message;
        const dans = new Set(lignes.map((l) => l.item_code));
        $v.find("#se-cb-trouves").html(tr.map((a) => `<div class="se-z-ligne">${se_img(a.image)}<div style="flex:1;min-width:0"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
            <div class="se-code">${se_esc(a.item_code)} · ici ${se_q(a.qte_cible)} · ${se_esc(this.libelle(d.source))} ${se_q(a.qte_source)}</div></div>
            <span class="se-bt ${dans.has(a.item_code) ? "fait" : ""}" data-ajouter="${se_esc(a.item_code)}">${dans.has(a.item_code) ? "✓" : "＋"}</span></div>`).join(""));
        $v.find("[data-ajouter]").on("click", (ev) => {
          const a = tr.find((x) => x.item_code === $(ev.currentTarget).attr("data-ajouter"));
          if (!a || dans.has(a.item_code)) return;
          lignes.push({ item_code: a.item_code, item_name: a.item_name, image: a.image, uom: a.uom, qte_cible: Math.max(a.qte_cible, 1), qte: a.qte_cible, qte_source: a.qte_source, manque: 0 });
          lignes.forEach((l) => { l.manque = Math.max(l.qte_cible - l.qte, 0); });
          peindre(); $v.find("#se-cb-recherche").val(txt);
        });
      }, 250); });
      $v.find("input.c").on("change", (e) => {
        const l = lignes.find((x) => x.item_code === $(e.currentTarget).closest(".se-cb-ligne").attr("data-item"));
        l.qte_cible = Math.max(0, +String(e.currentTarget.value).replace(",", ".") || 0); l.manque = Math.max(l.qte_cible - l.qte, 0); peindre();
      });
      $v.find("[data-enlever]").on("click", (e) => { const c = $(e.currentTarget).closest(".se-cb-ligne").attr("data-item"); lignes.splice(lignes.findIndex((x) => x.item_code === c), 1); peindre(); });
      $v.find("[data-retour]").on("click", () => { $v.hide(); r.find("#se-s-solde").show(); this.chargerSolde(); });
      $v.find("[data-modele]").on("click", () => {
        const dlg = new frappe.ui.Dialog({ title: "📋 Appliquer le modèle générique", fields: [
            { fieldtype: "HTML", options: `<div class="se-note">Les ${modele.articles} articles du modèle sont ajoutés au stock cible de <b>${se_esc(this.libelle(entrepot))}</b>. Les quantités déjà saisies ici (non enregistrées) seront perdues : enregistrez d’abord si besoin.</div>` },
            { fieldtype: "Check", fieldname: "remplacer", label: "Remplacer les quantités déjà définies par celles du modèle" }],
          primary_action_label: "Appliquer", primary_action: async (v) => {
            dlg.hide();
            const res = (await frappe.call({ method: SE_API + "appliquer_modele_cible", args: { entrepot, remplacer: v.remplacer ? 1 : 0 }, freeze: true })).message;
            if (!(this.ctx.cibles || []).includes(entrepot)) (this.ctx.cibles = this.ctx.cibles || []).push(entrepot);
            frappe.show_alert({ message: `📋 Modèle appliqué : ${res.lignes.length} article(s) dans le stock cible`, indicator: "green" }, 4);
            this.ouvrirCible();
          } });
        dlg.show();
      });
      const sauver = async () => {
        const res = (await frappe.call({ method: SE_API + "definir_stock_cible", args: { entrepot, lignes: lignes.map((l) => ({ item_code: l.item_code, qte_cible: l.qte_cible })) }, freeze: true })).message;
        if (!(this.ctx.cibles || []).includes(entrepot) && res.lignes.length) (this.ctx.cibles = this.ctx.cibles || []).push(entrepot);
        return res;
      };
      $v.find("[data-enregistrer]").on("click", async () => { await sauver(); frappe.show_alert({ message: "Stock cible enregistré", indicator: "green" }, 3); });
      $v.find("[data-reassort]").on("click", async () => {
        const res = await sauver();
        const aFaire = res.lignes.filter((l) => l.manque > 0);
        if (!aFaire.length) return;
        this.$r.find("#se-t-de").val(res.source); this.$r.find("#se-t-vers").val(entrepot);
        this.panier = aFaire.map((l) => ({ item_code: l.item_code, item_name: l.item_name, image: l.image, uom: l.uom, qte: l.manque, qte_source: l.qte_source, qte_cible: l.qte }));
        this.trouves = []; this.$r.find("#se-t-recherche").val("");
        $v.hide(); r.find("#se-s-solde").show();
        this.montrer("transfert"); this.peindreTrouves(); this.peindrePanier();
        frappe.show_alert({ message: `🔁 Réassort préparé : ${aFaire.length} article(s) — vérifiez puis validez`, indicator: "blue" }, 5);
      });
    };
    peindre();
  }

  /** Les articles cochés du solde partent dans le panier de l'onglet Transfert : Magasin → cet entrepôt,
   *  quantité proposée = ce qui manque pour revenir à la cible (sinon 1). */
  async transfererSelection() {
    const source = this.ctx.magasin;
    const items = (this.solde ? this.solde.articles : []).filter((a) => this.selSolde.has(a.item_code));
    if (!items.length) return;
    const cible = await this.choisirVehicule("🔁 Transférer depuis le Magasin vers…");
    if (!cible || cible === source) return;
    const qtes = (await frappe.call({ method: SE_API + "get_quantites", args: { items: items.map((a) => a.item_code), source, cible } })).message || {};
    this.$r.find("#se-t-de").val(source);
    this.$r.find("#se-t-vers").val(cible);
    this.panier = items.map((a) => ({ item_code: a.item_code, item_name: a.item_name, image: a.image, uom: a.uom,
      qte: a.a_transferer && a.a_transferer > 0 ? a.a_transferer : 1,
      qte_source: (qtes[a.item_code] || [0, 0])[0], qte_cible: (qtes[a.item_code] || [0, 0])[1] }));
    this.selSolde.clear();
    this.trouves = [];
    this.$r.find("#se-t-recherche").val("");
    this.montrer("transfert");
    this.peindreTrouves();
    this.peindrePanier();
    frappe.show_alert({ message: `${items.length} article(s) dans le panier — vérifiez les quantités puis validez`, indicator: "blue" }, 5);
  }

  /** Depuis le solde : les mouvements de cet article (sous-onglet en cours), détail déjà ouvert. */
  voirSorties(item) {
    this.$r.find("#se-o-recherche").val(item);
    this.ouvrirApres = item;
    this.montrer("sorties");
  }

  // ── Entrées / Sorties ──────────────────────────────────────────────────
  periodeDates() {
    const auj = frappe.datetime.get_today(), debutMois = frappe.datetime.month_start();
    switch (this.periode) {
      case "jour": return [auj, auj];
      case "7j": return [frappe.datetime.add_days(auj, -6), auj];
      case "30j": return [frappe.datetime.add_days(auj, -29), auj];
      case "mois": return [debutMois, auj];
      case "mois_prec": return [frappe.datetime.add_months(debutMois, -1), frappe.datetime.add_days(debutMois, -1)];
      case "dates": return [this.$r.find("#se-debut").val() || auj, this.$r.find("#se-fin").val() || auj];
      default: return [frappe.datetime.add_days(auj, -29), auj];
    }
  }

  async chargerSorties() {
    const r = this.$r, [debut, fin] = this.periodeDates();
    const res = await this.appel("sorties", "get_mouvements", { sens: this.sensMvt, entrepot: this.entrepot || null, debut, fin,
      recherche: r.find("#se-o-recherche").val() || null });
    if (!res) return;
    this.sorties = res;
    this.details = {};
    this.nSorties = SE_PAGE;
    this.peindreSorties();
    const cible = this.ouvrirApres;
    this.ouvrirApres = null;
    if (cible && res.articles.some((a) => a.item_code === cible)) this.basculerSortie(cible);
  }

  peindreSorties() {
    const r = this.$r, res = this.sorties, entree = res.sens === "entrees";
    const mot = entree ? "entrée(s)" : "sortie(s)", signe = entree ? "+" : "−", classe = entree ? "pos" : "neg";
    const qte = res.articles.reduce((s, a) => s + a.qte, 0);
    r.find("#se-o-info").html(`${res.articles.length} article(s) · ${res.mouvements} ${mot} · ${se_q(qte)} unité(s)`
      + ` · du ${se_dt(res.debut)} au ${se_dt(res.fin)} · ${this.entrepot ? se_esc(this.libelle(this.entrepot)) : "tous les entrepôts"}`);
    const cartes = res.articles.slice(0, this.nSorties).map((a) => `<div class="se-sortie" data-item="${se_esc(a.item_code)}">
        <div class="se-art">${se_img(a.image)}
          <div class="txt"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
            <div class="se-code">${se_esc(a.item_code)} · ${a.mouvements} ${mot} · dernière le ${se_dt(a.derniere)}</div></div>
          <div class="se-qte ${classe}">${signe}${se_q(a.qte)}<small>${se_esc(a.uom || "")}</small></div>
          <span class="fleche">▶</span>
        </div>
        <div class="se-lignes" style="display:none"></div>
      </div>`).join("");
    r.find("#se-o-liste").html((cartes || `<div class="se-vide">Aucune ${entree ? "entrée" : "sortie"} sur cette période.</div>`)
      + (res.articles.length > this.nSorties ? `<button type="button" class="btn btn-default se-plus">Afficher plus (${res.articles.length - this.nSorties})</button>` : ""));
    r.find("#se-o-liste .se-plus").on("click", () => { this.nSorties += SE_PAGE; this.peindreSorties(); });
    r.find("#se-o-liste .se-sortie > .se-art").on("click", (e) => this.basculerSortie($(e.currentTarget).closest(".se-sortie").attr("data-item")));
  }

  async basculerSortie(item) {
    const $c = this.$r.find(`#se-o-liste .se-sortie[data-item="${CSS.escape(item)}"]`);
    if (!$c.length) return;
    const $l = $c.find(".se-lignes");
    if ($c.hasClass("ouvert")) { $c.removeClass("ouvert"); $l.hide(); return; }
    $c.addClass("ouvert");
    $l.show().html(`<div class="se-note" style="padding:8px 0">Chargement…</div>`);
    if (!this.details[item]) {
      const [debut, fin, sens] = [this.sorties.debut, this.sorties.fin, this.sorties.sens];
      const res = (await frappe.call({ method: SE_API + "get_mouvements_article",
        args: { item_code: item, sens, entrepot: this.entrepot || null, debut, fin } })).message;
      this.details[item] = res;
    }
    const d = this.details[item];
    $l.html(d.lignes.map((l) => this.ligneSortie(l)).join("")
      + (d.lignes.length >= d.limite ? `<div class="se-note">Les ${d.limite} plus récentes seulement : resserrez la période.</div>` : ""));
  }

  ligneSortie(l) {
    const entree = this.sorties.sens === "entrees";
    const p = [`<div><b>${se_dt(l.date)}</b> ${se_esc(l.heure)} · `
      + (entree ? `<b class="pos">+${se_q(l.qte)}</b>` : `<b class="neg">−${se_q(l.qte)}</b>`)
      + (this.entrepot ? "" : ` <span class="se-badge">${se_esc(this.libelle(l.entrepot))}</span>`) + `</div>`];
    // Une entrée dit d’où elle vient, une sortie où elle va.
    const autre = entree ? (l.de ? ` ← depuis <b>${se_esc(this.libelle(l.de))}</b>` : "") : (l.vers ? ` → <b>${se_esc(this.libelle(l.vers))}</b>` : "");
    p.push(`<div>${se_lien(l.type, l.piece, `${l.libelle} ${l.piece}`)}${autre}</div>`);
    if (l.retour_de) p.push(`<div class="l3">↩️ retour de ${se_lien(l.type, l.retour_de)}</div>`);
    if (l.fournisseur) p.push(`<div>🏭 ${se_esc(l.fournisseur)}${l.facture_fournisseur ? ` <span class="l3">· facture n° ${se_esc(l.facture_fournisseur)}</span>` : ""}</div>`);
    if (l.commande_achat) p.push(`<div class="l3">📋 commande d’achat ${se_lien("Purchase Order", l.commande_achat)}</div>`);
    if (l.verification) p.push(`<div>✅ ${se_lien("Verification Stock", l.verification, `Vérification ${l.verification}`)}</div>`);
    if (l.demande_par || l.valide_par) p.push(`<div class="l3">✍️ ${l.demande_par ? `demandé par ${se_esc(l.demande_par)}` : ""}`
      + `${l.demande_par && l.valide_par ? " · " : ""}${l.valide_par ? `validé par ${se_esc(l.valide_par)}` : ""}</div>`);
    if (l.commande) p.push(`<div>🧾 ${se_lien("Sales Order", l.commande)}${l.client ? ` · ${se_esc(l.client)}` : ""}</div>`);
    else if (l.client) p.push(`<div>👤 ${se_esc(l.client)} <span class="l3">· sans commande</span></div>`);
    if (l.par) p.push(`<div class="l3">🚚 livré par ${se_esc(l.par)}</div>`);
    (l.taches || []).forEach((t) => p.push(`<div class="tache">🛠️ ${se_lien("Tache de travail", t.name, t.type || "Tâche")}`
      + `${t.employe ? ` · ${se_esc(t.employe)}` : ""}${t.date ? ` · ${se_dt(t.date)}` : ""} ${SE_STATUTS[t.statut] || se_esc(t.statut || "")}`
      + ` <span class="l3">${se_esc(t.name)}</span></div>`));
    if (l.commande && !(l.taches || []).length) p.push(`<div class="l3">Aucune tâche liée à cette commande</div>`);
    if (l.remarque && !l.commande) p.push(`<div class="l3">${se_esc(l.remarque)}</div>`);
    return `<div class="se-mvt">${p.join("")}</div>`;
  }

  // ── Transfert (responsable magasin) ──────────────────────────────────────
  initTransfert() {
    const r = this.$r, ctx = this.ctx;
    const options = ctx.entrepots.map((w) => `<option value="${se_esc(w.name)}">${se_esc(w.libelle)}${w.employes.length ? ` (${se_esc(w.employes.join(", "))})` : ""}</option>`).join("");
    r.find("#se-t-de, #se-t-vers").html(options);
    const autre = ctx.entrepots.find((w) => w.name !== ctx.magasin && w.employes.length) || ctx.entrepots.find((w) => w.name !== ctx.magasin);
    r.find("#se-t-de").val(ctx.magasin);
    r.find("#se-t-vers").val(ctx.mien && ctx.mien !== ctx.magasin ? ctx.mien : autre ? autre.name : ctx.magasin);
    r.find("#se-t-de, #se-t-vers").on("change", () => { this.chercherArticles(); this.majQuantitesPanier(); this.majBoutonReassort(); this.majRegle(); });
    r.find("#se-t-reassort").on("click", () => this.reassortCible());
    r.find("#se-t-inverser").on("click", () => {
      const de = r.find("#se-t-de").val();
      r.find("#se-t-de").val(r.find("#se-t-vers").val());
      r.find("#se-t-vers").val(de);
      this.chercherArticles();
      this.majQuantitesPanier();
      this.majBoutonReassort();
      this.majRegle();
    });
    let t = null;
    r.find("#se-t-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.chercherArticles(), 250); });
    r.find("#se-t-recherche").on("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
    r.find("#se-t-trouves").on("click", "[data-ajouter]", (e) => {
      const $b = $(e.currentTarget), $q = $b.closest(".se-z-ligne").find("input[data-qte-trouve]");
      this.ajouterAuPanier($b.attr("data-ajouter"), +String($q.val()).replace(",", ".") || 1);
    });
    r.find("#se-t-trouves").on("keydown", "input[data-qte-trouve]", (e) => {
      if (e.key === "Enter") { e.preventDefault(); $(e.currentTarget).closest(".se-z-ligne").find("[data-ajouter]").trigger("click"); }
    });
    r.find("#se-t-panier").on("click", "[data-pas]", (e) => {
      const $b = $(e.currentTarget), l = this.panier.find((x) => x.item_code === $b.attr("data-item"));
      if (!l) return;
      l.qte = Math.max(1, Math.round(((+l.qte || 0) + (+$b.attr("data-pas"))) * 1000) / 1000);
      this.peindrePanier();
    });
    r.find("#se-t-panier").on("click", "[data-enlever]", (e) => {
      this.panier = this.panier.filter((x) => x.item_code !== $(e.currentTarget).attr("data-enlever"));
      this.peindrePanier();
      this.peindreTrouves();
    });
    r.find("#se-t-panier").on("change", "input[data-qte]", (e) => {
      const l = this.panier.find((x) => x.item_code === $(e.currentTarget).attr("data-qte"));
      if (l) { l.qte = Math.max(0, +String($(e.currentTarget).val()).replace(",", ".") || 0); this.peindrePanier(); }
    });
    r.find("#se-t-valider").on("click", () => this.validerTransfert());
    r.find("#se-t-historique").on("click", "[data-annuler]", (e) => this.annulerTransfert($(e.currentTarget).attr("data-annuler")));
    r.find("#se-t-historique").on("click", "[data-detail]", (e) => {
      const t = (this.historique || []).find((x) => x.name === $(e.currentTarget).attr("data-detail"));
      if (t) this.ouvrirDetail(t);
    });
    r.find("#se-t-historique").on("click", "[data-valider]", (e) => this.ouvrirValidation($(e.currentTarget).attr("data-valider")));
    this.trouves = [];
    this.peindrePanier();
    this.majBoutonReassort();
    this.majRegle();
  }

  /** Ce que le réglage dit du trajet (même règle que le serveur, qui tranche) : permis ? double validation ? */
  regle(de, vers) {
    const cat = (w) => (this.ctx.categories || {})[w] || "Autre entrepôt";
    const c1 = cat(de), c2 = cat(vers), t = this.ctx.trajets;
    if (!t) return { permis: true, double: c1 === "Magasin" && c2 === "Stock véhicule", de: c1, vers: c2 };
    const r = t.find((x) => x[0] === c1 && x[1] === c2);
    return { permis: !!r, double: !!(r && r[2]), de: c1, vers: c2 };
  }

  majRegle() {
    const [de, vers] = this.sens(), $g = this.$r.find("#se-t-regle");
    if (!de || !vers || de === vers) { $g.hide(); return; }
    const g = this.regle(de, vers);
    const veh = this.ctx.entrepots.find((w) => w.name === (g.de === "Stock véhicule" ? de : vers));
    const emp = veh && veh.employes.length ? veh.employes.join(", ") : "l’employé du véhicule";
    $g.removeClass("double direct interdit").addClass(!g.permis ? "interdit" : g.double ? "double" : "direct").show()
      .html(!g.permis ? `⛔ Trajet interdit par le réglage : ${se_esc(g.de)} → ${se_esc(g.vers)}.`
        : g.double ? `🤝 Double validation : ${se_esc(emp)} et un responsable magasin valident les quantités ; le stock bouge à l’accord des deux.`
        : "⚡ Immédiat : le stock bouge dès la validation.");
    this.peindrePanier();
  }

  sens() {
    return [this.$r.find("#se-t-de").val(), this.$r.find("#se-t-vers").val()];
  }

  async chercherArticles() {
    const txt = (this.$r.find("#se-t-recherche").val() || "").trim();
    if (txt.length < 2) { this.trouves = []; this.peindreTrouves(); return; }
    const [source, cible] = this.sens();
    const res = await this.appel("recherche", "rechercher_articles", { txt, source, cible });
    if (!res) return;
    this.trouves = res;
    this.peindreTrouves();
  }

  peindreTrouves() {
    const [de, vers] = this.sens(), dans = new Set(this.panier.map((l) => l.item_code));
    this.$r.find("#se-t-trouves").html(this.trouves.map((a) => `<div class="se-z-ligne">${se_img(a.image)}
        <div class="txt" style="flex:1;min-width:0"><div class="se-nom">${se_esc(a.item_name || a.item_code)}</div>
          <div class="se-code">${se_esc(a.item_code)} · ${se_esc(this.libelle(de))} <b class="${a.qte_source < 0 ? "neg" : ""}">${se_q(a.qte_source)}</b>
            · ${se_esc(this.libelle(vers))} <b class="${a.qte_cible < 0 ? "neg" : ""}">${se_q(a.qte_cible)}</b></div></div>
        <input type="number" inputmode="decimal" min="0" step="any" class="form-control se-qte-trouve" data-qte-trouve value="1" title="Quantité à ajouter">
        <span class="se-bt ${dans.has(a.item_code) ? "fait" : ""}" data-ajouter="${se_esc(a.item_code)}" title="Ajouter">${dans.has(a.item_code) ? "✓" : "＋"}</span>
      </div>`).join("") || ((this.$r.find("#se-t-recherche").val() || "").trim().length >= 2 ? `<div class="se-vide">Aucun article suivi en stock ne correspond.</div>` : ""));
  }

  ajouterAuPanier(item, qte) {
    const a = this.trouves.find((x) => x.item_code === item);
    if (!a) return;
    const n = Math.max(qte > 0 ? qte : 1, 0.001);
    const l = this.panier.find((x) => x.item_code === item);
    if (l) l.qte = (+l.qte || 0) + n;
    else this.panier.push(Object.assign({}, a, { qte: n }));
    this.peindrePanier();
    this.peindreTrouves();
  }

  async majQuantitesPanier() {
    if (!this.panier.length) return;
    const [source, cible] = this.sens();
    const res = await this.appel("qtes", "get_quantites", { items: this.panier.map((l) => l.item_code), source, cible });
    if (!res) return;
    this.panier.forEach((l) => { if (res[l.item_code]) [l.qte_source, l.qte_cible] = res[l.item_code]; });
    this.peindrePanier();
  }

  peindrePanier() {
    const r = this.$r, [de, vers] = this.sens();
    r.find("#se-t-panier").html(this.panier.map((l) => `<div class="se-panier-ligne">
        <div class="haut">${se_img(l.image)}<div style="flex:1;min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div>
          <div class="se-code">${se_esc(l.item_code)} · ${se_esc(this.libelle(de))} ${se_q(l.qte_source)} → ${se_esc(this.libelle(vers))} ${se_q(l.qte_cible)}</div></div></div>
        <div class="se-pas">
          <span class="b" data-pas="-1" data-item="${se_esc(l.item_code)}">−</span>
          <input type="number" inputmode="decimal" min="0" step="any" class="form-control" data-qte="${se_esc(l.item_code)}" value="${se_esc(l.qte)}">
          <span class="b" data-pas="1" data-item="${se_esc(l.item_code)}">＋</span>
          <span style="font-size:12px;color:#64748b">${se_esc(l.uom || "")}</span>
          <span class="b x" data-enlever="${se_esc(l.item_code)}" title="Retirer">✕</span>
        </div>
        ${l.qte > l.qte_source ? `<div class="se-alerte">⚠️ Plus que le stock de ${se_esc(this.libelle(de))} (${se_q(l.qte_source)}) : il passera en négatif.</div>` : ""}
      </div>`).join("") || `<div class="se-note">Cherchez un article ci-dessus puis touchez ＋.</div>`);
    const n = this.panier.filter((l) => l.qte > 0).length, permis = de === vers || this.regle(de, vers).permis;
    r.find("#se-t-valider").prop("disabled", !n || de === vers || !permis)
      .text(de === vers ? "Choisissez deux entrepôts différents" : !permis ? "Trajet interdit (réglage)"
        : n ? `Valider le transfert (${n} article${n > 1 ? "s" : ""})` : "Valider le transfert");
  }

  validerTransfert() {
    const [source, cible] = this.sens();
    const lignes = this.panier.filter((l) => l.qte > 0).map((l) => ({ item_code: l.item_code, qte: l.qte }));
    if (!lignes.length || source === cible) return;
    frappe.confirm(`Transférer <b>${lignes.length}</b> article(s) de <b>${se_esc(this.libelle(source))}</b> vers <b>${se_esc(this.libelle(cible))}</b> ?`, async () => {
      const $b = this.$r.find("#se-t-valider").prop("disabled", true);
      try {
        const res = (await frappe.call({ method: SE_API + "creer_transfert", freeze: true, freeze_message: "Transfert…",
          args: { source, cible, lignes, remarque: this.$r.find("#se-t-remarque").val() || null } })).message;
        frappe.show_alert({ message: res.en_attente ? `⏳ Transfert ${se_esc(res.name)} : ${res.lignes} article(s) — au tour de ${se_esc(res.attente_de)} (le stock bougera à l’accord des deux)`
            : `✅ Transfert ${se_esc(res.name)} : ${res.lignes} article(s)`, indicator: res.en_attente ? "yellow" : "green" }, 8);
        this.panier = [];
        this.chargerAttente();
        this.$r.find("#se-t-remarque").val("");
        this.chercherArticles();
        this.chargerHistorique();
      } finally {
        $b.prop("disabled", false);
        this.peindrePanier();
      }
    });
  }

  async chargerHistorique() {
    const res = await this.appel("historique", "transferts_recents", { limite: 15 });
    if (!res) return;
    this.historique = res;
    this.$r.find("#se-t-historique").html(res.map((t) => `<div class="se-card se-tr ${t.docstatus === 2 ? "annule" : ""} ${t.attente ? `attente ${t.attente.age_h > 24 ? "vieux" : ""}` : ""}">
        <div class="tete"><span>${se_lien("Stock Entry", t.name)} ${t.docstatus === 2 ? `<span class="se-badge">annulé</span>` : ""}</span>
          <span class="se-note">${se_dt(t.date)} ${se_esc(t.heure)} · ${se_esc(t.par)}</span></div>
        <div class="sens">${se_esc(this.libelle(t.de || (t.lignes[0] || {}).de))} → ${se_esc(this.libelle(t.vers || (t.lignes[0] || {}).vers))}</div>
        ${t.docstatus === 2 ? "" : this.etatTransfert(t)}
        <div class="det">${t.lignes.slice(0, 6).map((l) => `${se_esc(l.item_name || l.item_code)} <b>${se_q(l.qte)}</b>`).join(" · ")}${t.lignes.length > 6 ? ` · … +${t.lignes.length - 6}` : ""}</div>
        ${t.remarque ? `<div class="se-note">${se_esc(t.remarque)}</div>` : ""}
        <div class="actions"><button type="button" class="btn btn-xs btn-default" data-detail="${se_esc(t.name)}">🔍 Détail</button>
          ${t.attente && t.attente.a_moi ? `<button type="button" class="btn btn-xs btn-primary" data-valider="${se_esc(t.name)}">🔍 Vérifier et valider</button>` : ""}
          ${t.docstatus === 1 ? `<button type="button" class="btn btn-xs btn-default" data-annuler="${se_esc(t.name)}">Annuler ce transfert</button>` : ""}
          ${t.attente ? `<button type="button" class="btn btn-xs btn-default" data-annuler="${se_esc(t.name)}">Annuler la demande</button>` : ""}</div>
      </div>`).join("") || `<div class="se-vide">Aucun transfert.</div>`);
  }

  annulerTransfert(name) {
    const t = (this.historique || []).find((x) => x.name === name);
    const texte = t && t.attente ? `Retirer la demande <b>${se_esc(name)}</b> (au tour de ${se_esc(t.attente.attente_de)}) ? Rien n’a bougé en stock.`
      : `Annuler le transfert <b>${se_esc(name)}</b> ? Les quantités reviennent à leur place.`;
    frappe.confirm(texte, async () => {
      await frappe.call({ method: SE_API + "annuler_transfert", args: { name }, freeze: true });
      frappe.show_alert({ message: `Transfert ${se_esc(name)} annulé`, indicator: "orange" }, 5);
      this.chargerHistorique();
      this.chargerAttente();
      this.majQuantitesPanier();
    });
  }

  // ── Remise à zéro (responsable magasin) ──────────────────────────────────
  async chargerZero() {
    this.zeroEntrepot = null;
    const res = await this.appel("zero", "entrepots_a_zero", {});
    if (!res) return;
    const $c = this.$r.find("#se-z-contenu");
    $c.html(`<div class="se-note" style="margin:0 2px 10px">L’écart d’inventaire reste sur <b>${se_esc(this.libelle(res.magasin))}</b> :
        les quantités négatives sont apportées depuis lui, les positives y retournent.
        ${res.exclus.length ? `Jamais remis à zéro : ${res.exclus.map((e) => se_esc(this.libelle(e))).join(", ")}.` : ""}
        <a class="se-lien" href="/app/config-stock-entrepot">⚙️ Réglages</a></div>`
      + (res.entrepots.map((w) => `<div class="se-card se-wz">
          <div class="t">🏬 ${se_esc(w.libelle)}</div>
          ${w.employes.length ? `<div class="emp">👤 ${se_esc(w.employes.join(", "))}</div>` : ""}
          ${w.negatifs || w.positifs ? `<div class="stats">
              ${w.negatifs ? `<span class="neg">${w.negatifs} article(s) négatif(s) · ${se_signe(-w.qte_neg)}</span>` : ""}
              ${w.positifs ? `<span class="pos">${w.positifs} article(s) en stock · +${se_q(w.qte_pos)}</span>` : ""}
              <span class="se-note">valeur ${se_esc(se_argent(w.valeur))}</span></div>
            <button type="button" class="btn btn-sm btn-default" data-zero="${se_esc(w.name)}" style="width:100%">Voir et remettre à zéro</button>`
            : `<div class="ok">✓ Déjà à zéro</div>`}
        </div>`).join("") || `<div class="se-vide">Aucun entrepôt à remettre à zéro.</div>`));
    $c.find("[data-zero]").on("click", (e) => this.ouvrirZero($(e.currentTarget).attr("data-zero")));
  }

  async ouvrirZero(entrepot) {
    this.zeroEntrepot = entrepot;
    const res = await this.appel("zero", "apercu_zero", { entrepot });
    if (!res) return;
    this.zero = res;
    this.zeroCoches = new Set(res.lignes.filter((l) => !l.bloque).map((l) => l.item_code));
    this.peindreZero();
    window.scrollTo({ top: 0 });
  }

  peindreZero() {
    const res = this.zero, m = this.libelle(res.magasin), $c = this.$r.find("#se-z-contenu");
    const choisies = res.lignes.filter((l) => !l.bloque && this.zeroCoches.has(l.item_code));
    const apports = choisies.filter((l) => l.sens === "apport"), retours = choisies.filter((l) => l.sens === "retour");
    const ajust = apports.reduce((s, l) => s + (l.ajustement || 0), 0);
    const toutes = res.lignes.filter((l) => !l.bloque);
    $c.html(`<span class="se-retour" data-retour>← Tous les entrepôts</span>
      <div class="se-card">
        <div class="se-wz"><div class="t">🏬 ${se_esc(this.libelle(res.entrepot))}</div></div>
        ${res.lignes.length ? `<div class="se-resume">
            ${apports.length ? `<div>⬅️ <b>${apports.length}</b> apport(s) depuis ${se_esc(m)} : ${se_signe(apports.reduce((s, l) => s - l.qte, 0))} unité(s)</div>` : ""}
            ${retours.length ? `<div>➡️ <b>${retours.length}</b> retour(s) vers ${se_esc(m)} : ${se_q(retours.reduce((s, l) => s + l.qte, 0))} unité(s)</div>` : ""}
            ${Math.abs(ajust) >= 1 ? `<div class="se-note">Ajustement de valorisation estimé : <b>${se_esc(se_argent(ajust))}</b>
              (le stock négatif garde une valeur calculée à ses propres taux ; l’apport entre au taux de ${se_esc(m)}).</div>` : ""}
          </div>
          <div style="margin-top:8px"><span class="se-chip" data-tout>${choisies.length === toutes.length ? "Tout décocher" : "Tout cocher"}</span></div>`
          : `<div class="ok" style="color:#15803d;font-weight:600;margin-top:6px">✓ Cet entrepôt est à zéro.</div>`}
      </div>
      ${res.lignes.length ? `<div class="se-card">${res.lignes.map((l) => `<div class="se-z-ligne ${l.bloque ? "bloque" : ""}" data-item="${se_esc(l.item_code)}">
          ${l.bloque ? `<span class="se-coche" style="cursor:default">–</span>` : `<span class="se-coche ${this.zeroCoches.has(l.item_code) ? "on" : ""}">${this.zeroCoches.has(l.item_code) ? "✓" : ""}</span>`}
          ${se_img(l.image)}
          <div style="flex:1;min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div>
            <div class="se-code">${se_esc(l.item_code)} · stock <b class="${l.qte < 0 ? "neg" : "pos"}">${se_q(l.qte)}</b> ${se_esc(l.uom || "")}</div>
            ${l.bloque ? `<div class="se-alerte">Non transférable : ${se_esc(l.bloque)}</div>` : ""}</div>
          <div class="mvt ${l.sens === "apport" ? "pos" : ""}">${l.sens === "apport" ? `+${se_q(-l.qte)}<small>depuis ${se_esc(m)}</small>` : `−${se_q(l.qte)}<small>vers ${se_esc(m)}</small>`}</div>
        </div>`).join("")}
        <div class="se-barre"><button type="button" class="btn btn-danger" data-valider ${choisies.length ? "" : "disabled"}>
          Remettre à zéro (${choisies.length} article${choisies.length > 1 ? "s" : ""})</button></div></div>` : ""}`);
    $c.find("[data-retour]").on("click", () => this.chargerZero());
    $c.find("[data-tout]").on("click", () => {
      this.zeroCoches = choisies.length === toutes.length ? new Set() : new Set(toutes.map((l) => l.item_code));
      this.peindreZero();
    });
    $c.find(".se-z-ligne:not(.bloque)").on("click", (e) => {
      const c = $(e.currentTarget).attr("data-item");
      this.zeroCoches.has(c) ? this.zeroCoches.delete(c) : this.zeroCoches.add(c);
      this.peindreZero();
    });
    $c.find("[data-valider]").on("click", () => this.validerZero(choisies, apports.length, retours.length));
  }

  // ── Vérification des stocks des employés ─────────────────────────────────
  async chargerVerif() {
    this.comptage = null;
    const res = await this.appel("verif", "verifications_etat", {});
    if (!res) return;
    const $c = this.$r.find("#se-v-contenu");
    const hist = (s) => s.dernieres.map((v) => v.statut === "Annulée" ? `<div class="se-v-h" style="cursor:default">
        <span class="d">${se_dt(v.termine_le)}</span>
        <span class="n">✖️ annulée par ${se_esc(v.annule_par || "?")}${v.motif_annulation ? ` · ${se_esc(v.motif_annulation)}` : ""}</span><span class="v"></span><span class="fleche"></span></div>` : `<div class="se-v-h" data-detail="${se_esc(v.name)}">
        <span class="d">${se_dt(v.termine_le)}</span>
        <span class="n">${v.nb_comptes}/${v.nb_lignes} comptés · <b class="${v.nb_ecarts ? "neg" : "pos"}">${v.nb_ecarts} écart(s)</b>${v.pour_employe ? ` · <span class="se-badge">accepté pour l’employé par ${se_esc(v.valide_employe_par || "?")}</span>` : ""}</span>
        <span class="v ${v.valeur_ecarts < 0 ? "neg" : v.valeur_ecarts > 0 ? "pos" : ""}">${v.nb_ecarts ? `${se_argent(v.valeur_ecarts)}<small class="se-vente">vente ${se_argent(v.valeur_ecarts_vente)}</small>` : "✓"}</span>
        <span class="fleche">▶</span></div><div class="se-v-det" style="display:none" data-det="${se_esc(v.name)}"></div>`).join("");
    $c.html(`<div class="se-note" style="margin:0 2px 10px">Chaque semaine, le jour fixé, une fiche de comptage est ouverte et deux tâches créées :
        l’employé du stock et ${res.responsable_nom ? `<b>${se_esc(res.responsable_nom)}</b>` : "le responsable magasin"}.
        L’employé compte et termine ; un <b>autre responsable magasin</b> vérifie et valide ses quantités ; l’employé les accepte
        (s’il en change une, le responsable revalide — il a le dernier mot) ; un <b>rapprochement de stock</b> aligne alors le véhicule.
        ${res.peut_planifier ? `<a class="se-lien" href="/app/config-stock-entrepot">⚙️ Réglages</a> · <span class="se-lien" data-planifier>▶ Ouvrir les vérifications du jour</span>` : ""}</div>`
      + (() => { const att = res.stocks.filter((s) => s.en_cours && s.en_cours.statut !== "En cours" && s.en_cours.actions.some((a) => ["valider", "confirmer"].includes(a) || (a === "confirmer_pour_employe" && !s.employe_actif)));
          return att.length ? `<div class="se-bandeau"><div class="t">🧾 ${att.length} vérification(s) ${att.some((s) => s.en_cours.actions.includes("valider")) ? "à valider (comptage d’un employé)" : "à accepter (quantités validées par le responsable)"}</div>
            <div class="d">${att.map((s) => se_esc(s.libelle)).join(" · ")}</div></div>` : ""; })()
      + (res.stocks.map((s) => `<div class="se-card se-v-stock">
          <div class="t">🚐 ${se_esc(s.libelle)}</div>
          <div class="plan">👤 ${se_esc(s.employe)}${s.employe_actif === false ? ` <b style="color:#b91c1c">(employé parti)</b>` : ""} · ${s.actif ? `chaque <b>${se_esc(s.jour)}</b> à ${se_esc(s.heure)} · prochaine le ${se_dt(s.prochaine)}` : `<span style="color:#c2410c">pas de jour fixé</span>`}</div>
          ${s.bilan_mois && s.bilan_mois.fiches ? `<div class="se-bilan">📊 Bilan du mois (${s.bilan_mois.fiches} fiche${s.bilan_mois.fiches > 1 ? "s" : ""}, ${s.bilan_mois.ecarts} écart${s.bilan_mois.ecarts > 1 ? "s" : ""}) · prix de revient : net <b class="${s.bilan_mois.revient.net < 0 ? "neg" : "pos"}">${se_argent(s.bilan_mois.revient.net)}</b>, manquants <b class="neg">${se_argent(-s.bilan_mois.revient.manquants)}</b>, surplus <b class="pos">${se_argent(s.bilan_mois.revient.surplus)}</b> · prix de vente : net <b class="${s.bilan_mois.vente.net < 0 ? "neg" : "pos"}">${se_argent(s.bilan_mois.vente.net)}</b>, manquants <b class="neg">${se_argent(-s.bilan_mois.vente.manquants)}</b>, surplus <b class="pos">${se_argent(s.bilan_mois.vente.surplus)}</b></div>` : ""}
          ${s.employe_actif === false ? `<div class="se-alerte">⚠️ Cet employé n’est plus actif : acceptez ou annulez sa fiche ci-dessous${s.actif ? `, et décochez « actif » sur son stock dans <a href="/app/config-stock-entrepot">⚙️ Réglages</a> pour arrêter les vérifications` : ""}.</div>` : ""}
          ${s.en_cours ? this.blocVerif(s) : `<button type="button" class="btn btn-default" data-commencer="${se_esc(s.entrepot)}">Compter maintenant</button>`}
          <div class="se-v-hist">${hist(s) || `<div class="se-note">Aucune vérification terminée.</div>`}</div>
        </div>`).join("") || `<div class="se-vide">Aucun stock d’employé.</div>`));
    $c.find("[data-planifier]").on("click", async () => {
      const r = (await frappe.call({ method: SE_API + "planifier_maintenant", freeze: true })).message || [];
      frappe.show_alert({ message: r.length ? `${r.length} vérification(s) ouverte(s) : ${r.map(se_esc).join(", ")}` : "Rien à ouvrir aujourd’hui (jour non prévu, ou déjà ouverte)", indicator: r.length ? "green" : "orange" }, 6);
      this.chargerVerif();
    });
    $c.find("[data-commencer]").on("click", async (e) => {
      const name = (await frappe.call({ method: SE_API + "commencer_verification", args: { entrepot: $(e.currentTarget).attr("data-commencer") }, freeze: true })).message;
      this.ouvrirComptage(name);
    });
    $c.find("[data-reprendre]").on("click", (e) => this.ouvrirComptage($(e.currentTarget).attr("data-reprendre")));
    $c.find("[data-annuler-carte]").on("click", (e) => {
      const name = $(e.currentTarget).attr("data-annuler-carte");
      frappe.prompt({ fieldtype: "Small Text", fieldname: "motif", label: "Motif (employé parti, fiche en double…)", reqd: 1 }, async (v) => {
        await frappe.call({ method: SE_API + "annuler_verification", args: { name, motif: v.motif }, freeze: true });
        frappe.show_alert({ message: `${name} annulée`, indicator: "orange" }, 4);
        this.chargerVerif(); this.chargerAttente();
      }, `✖️ Annuler ${name}`, "Annuler la fiche");
    });
    $c.on("click", "[data-enqueter]", async (e) => {
      e.stopPropagation();
      const name = $(e.currentTarget).attr("data-enqueter");
      try { await frappe.call({ method: "customization_app.enquete_ecarts.enqueter", args: { name }, freeze: true, freeze_message: "Enquête en cours (actions de l’employé, BL, brouillons, kits, relecture IA)…" }); }
      catch (err) { return; }
      frappe.show_alert({ message: "Enquête terminée", indicator: "green" }, 4);
      $c.find(`[data-det="${CSS.escape(name)}"]`).hide(); $c.find(`[data-detail="${CSS.escape(name)}"]`).trigger("click");
    });
    $c.find("[data-detail]").on("click", async (e) => {
      const name = $(e.currentTarget).attr("data-detail"), $d = $c.find(`[data-det="${CSS.escape(name)}"]`);
      if ($d.is(":visible")) { $d.hide(); return; }
      const d = (await frappe.call({ method: SE_API + "detail_verification", args: { name } })).message;
      const ec = d.lignes.filter((l) => l.ecart != null && Math.abs(l.ecart) > 1e-6);
      $d.html((ec.map((l) => `<div><b class="${l.ecart < 0 ? "neg" : "pos"}">${se_signe(l.ecart)}</b> ${se_esc(l.item_name || l.item_code)}
          <span class="se-note">(système ${se_q(l.qte_systeme)}, compté ${se_q(l.qte_comptee)}, revient ${se_argent(l.valeur_ecart)}, vente ${se_argent(l.valeur_vente)})${l.commentaire ? ` — ${se_esc(l.commentaire)}` : ""}</span>${l.analyse ? `<div class="se-note" style="margin-left:18px">🔎 ${se_esc(l.analyse)}</div>` : ""}${se_verdict(l)}</div>`).join("")
        || `<div class="pos">Aucun écart.</div>`) + (d.fiche.nb_ecarts ? `<div class="se-note" style="margin-top:4px"><b>Valeur des écarts :</b> prix de revient net ${se_esc(se_argent(d.fiche.valeur_ecarts))} (manquants ${se_esc(se_argent(-d.fiche.valeur_manquants))}) · prix de vente net ${se_esc(se_argent(d.fiche.valeur_ecarts_vente))} (manquants ${se_esc(se_argent(-d.fiche.valeur_manquants_vente))})</div>` : "") + (d.fiche.analyse ? `<div class="se-note" style="margin-top:4px"><b>Analyse :</b> ${se_esc(d.fiche.analyse)}</div>` : "") + se_enquete(d, res.peut_planifier) + `<div class="se-note">${se_lien("Verification Stock", d.fiche.name)}${d.fiche.tache_employe ? ` · ${se_lien("Tache de travail", d.fiche.tache_employe, "tâche employé")}` : ""}
          ${d.fiche.valide_employe_par ? ` · comptage ${se_esc(d.fiche.valide_employe_par)}` : ""}${d.fiche.valide_responsable_par ? ` · validé ${se_esc(d.fiche.valide_responsable_par)}` : ""}
          ${d.fiche.rapprochement ? ` · ${se_lien("Stock Reconciliation", d.fiche.rapprochement, "rapprochement " + d.fiche.rapprochement)}` : " · sans rapprochement (aucun écart)"}</div>`).show();
    });
  }

  /** La fiche ouverte d'un stock, selon son statut et ce que l'utilisateur peut y faire. */
  blocVerif(s) {
    const v = s.en_cours, a = v.actions || [];
    if (v.statut === "En cours") return v.photo
      ? `<div class="plan">📝 Comptage en cours du ${se_dt(v.date)} : ${v.nb_comptes}/${v.nb_lignes} comptés${v.renvois ? ` · renvoyé ${v.renvois}×` : ""}${v.nb_a_recompter ? ` · <b class="neg">${v.nb_a_recompter} à recompter</b>` : ""}</div>
         <button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">Reprendre le comptage</button>`
      : `<div class="plan">📅 Vérification ${v.prevue ? "prévue le" : "du"} <b>${se_dt(v.date)}</b>${v.tache_employe ? " · tâches créées" : ""}</div>
         <button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">Commencer le comptage</button>
         ${a.includes("annuler") ? `<button type="button" class="btn btn-default btn-sm" data-annuler-carte="${se_esc(v.name)}" title="Fiche jamais commencée : l’abandonner sans ouvrir le comptage">✖️ Annuler cette vérification</button>` : ""}`;
    if (v.statut === "À valider") return `<div class="plan">📝 Comptage du ${se_dt(v.date)} ${v.renvois ? "revu" : "terminé"} par ${se_esc(v.valide_employe_par)} : <b class="${v.nb_ecarts ? "neg" : "pos"}">${v.nb_ecarts} écart(s)</b>${v.nb_ecarts ? ` · ${se_argent(v.valeur_ecarts)}` : ""}</div>
         ${a.includes("valider") ? `<button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">🔍 Vérifier et valider</button>`
           : `<div class="se-attente">⏳ À valider par un responsable magasin (autre que l’employé du stock)</div>`}`;
    if (v.statut === "À confirmer") return `<div class="plan">📝 Validé par ${se_esc(v.valide_responsable_par)}${v.nb_ajustes ? ` · <b class="neg">${v.nb_ajustes} quantité(s) ajustée(s)</b>` : " · sans changement"}</div>
         ${a.includes("confirmer") ? `<button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">🔍 Voir et accepter</button>`
           : `<div class="se-attente">⏳ À accepter par ${se_esc(s.employe)} (employé du stock)</div>
              ${a.includes("confirmer_pour_employe") ? `<button type="button" class="btn btn-primary" data-reprendre="${se_esc(v.name)}">🔍 Voir et accepter à sa place</button>` : ""}`}`;
    return "";
  }

  /** Feuille d'une vérification : comptage (employé), validation / ajustement (responsable), confirmation (employé). */
  async ouvrirComptage(name) {
    this.comptage = name;
    const d = (await frappe.call({ method: SE_API + "detail_verification", args: { name } })).message;
    const $c = this.$r.find("#se-v-contenu"), statut = d.fiche.statut, a = d.fiche.actions || [];
    const mode = statut === "En cours" ? "comptage" : statut === "À valider" && a.includes("valider") ? "validation"
      : statut === "À confirmer" && a.includes("confirmer") ? "confirmation"
      : statut === "À confirmer" && a.includes("confirmer_pour_employe") ? "confirmation_pour" : "lecture";
    const fini = mode === "lecture" || mode === "confirmation_pour";
    const annuler = a.includes("annuler") ? `<button type="button" class="btn btn-default" data-annuler-verif title="Abandonner cette fiche : rien n’est rapproché">✖️ Annuler la fiche</button>` : "";
    const etat = { "À valider": "⏳ à valider par un responsable", "À confirmer": "⏳ à accepter par l’employé", "Terminée": "terminée" }[statut];
    const saisies = {};
    d.lignes.forEach((l) => { saisies[l.item_code] = { qte_comptee: l.qte_comptee, commentaire: l.commentaire || "" }; });
    const ligne = (l) => `<div class="se-c-ligne ${l.qte_comptee != null ? "fait" : ""}" data-item="${se_esc(l.item_code)}" data-cle="${se_esc(((l.item_name || "") + " " + l.item_code).toLowerCase())}">
        ${se_img(l.image)}
        <div class="txt"><div class="se-nom" style="font-size:13.5px">${se_esc(l.item_name || l.item_code)}</div>
          <div class="se-code">${se_esc(l.item_code)}${l.zones ? ` · 📍 ${se_esc(l.zones)}` : ""}</div>${l.a_recompter ? `<div class="se-alerte">⚠️ à recompter — ${se_esc(l.commentaire || "stock modifié")}</div>` : ""}${fini && l.verdict ? se_verdict(l) : ""}</div>
        <div class="sys">${se_q(l.qte_systeme)}${l.ajuste ? `<small style="display:block;color:#b45309;font-size:10.5px">employé : ${se_q(l.qte_employe)}</small>` : ""}</div>
        <input type="number" inputmode="decimal" step="any" class="form-control q" ${fini ? "disabled" : ""} style="${l.ajuste ? "border-color:#f59e0b;background:#fffbeb" : ""}" value="${l.qte_comptee == null ? "" : se_esc(l.qte_comptee)}" placeholder="?">
        <div class="ec ${l.ecart ? (l.ecart < 0 ? "neg" : "pos") : ""}">${l.ecart == null ? "" : se_signe(l.ecart)}</div>
      </div>`;
    $c.html(`<span class="se-retour" data-retour>← Vérifications</span>
      <div class="se-card">
        <div class="se-v-stock"><div class="t">📝 ${se_esc(this.libelle(d.fiche.entrepot))} — ${se_dt(d.fiche.date)} ${etat ? `<span class="se-badge">${etat}</span>` : ""}</div>
          ${mode === "comptage" ? `<div class="se-note">Quantités système photographiées à la première ouverture de cette feuille.</div>`
            : mode === "validation" ? `<div class="se-note">Comptage de <b>${se_esc(d.fiche.valide_employe_par)}</b> (${se_esc(d.fiche.valide_employe_le)}). Validez tel quel, corrigez une quantité (l’employé devra confirmer), ou renvoyez au comptage.<br>Quantités système actualisées à l’instant : un article qui a bougé depuis le comptage serait à recompter.</div>`
            : mode === "confirmation_pour" ? `<div class="se-note">Quantités validées par <b>${se_esc(d.fiche.valide_responsable_par)}</b>${d.fiche.nb_ajustes ? " (ajustées : surlignées, le comptage de l’employé en dessous)" : ""}. L’employé du stock n’a pas accepté : en tant que responsable magasin vous pouvez <b>accepter à sa place</b> (le rapprochement passe sur son stock, la fiche garde votre nom) ou <b>annuler la fiche</b> (rien n’est rapproché).</div>`
            : mode === "confirmation" ? `<div class="se-note">Quantités validées par <b>${se_esc(d.fiche.valide_responsable_par)}</b>${d.fiche.nb_ajustes ? " (ajustées : surlignées, votre comptage en dessous)" : ""}. D’accord : <b>acceptez</b>. Sinon corrigez la quantité : la fiche repart chez le responsable, qui a le dernier mot.</div>`
            : `<div class="se-note">${d.fiche.valide_employe_par ? `Comptage ${se_esc(d.fiche.valide_employe_par)}` : ""}${d.fiche.valide_responsable_par ? ` · validé par ${se_esc(d.fiche.valide_responsable_par)}` : ""}${d.fiche.rapprochement ? ` · ${se_lien("Stock Reconciliation", d.fiche.rapprochement, "rapprochement " + d.fiche.rapprochement)}` : ""}</div>`}
          ${d.fiche.nb_a_recompter ? `<div class="se-alerte">⚠️ ${d.fiche.nb_a_recompter} article(s) ont bougé depuis le comptage : leur comptage est effacé, à recompter (lignes marquées).</div>` : ""}
          ${d.fiche.note ? `<div class="se-note">${se_esc(d.fiche.note)}</div>` : ""}
          <div class="plan" id="se-c-bilan"></div></div>
        <input type="search" class="form-control se-saisie" id="se-c-filtre" placeholder="🔎 Filtrer un article…" style="margin-top:8px">
        <div class="se-c-tete"><span class="txt">Article</span><span class="sys">Système</span><span class="q">Compté</span><span class="ec">Écart</span></div>
        <div id="se-c-lignes">${d.lignes.map(ligne).join("")}</div>
        ${mode === "comptage" ? `<div class="se-c-barre">${annuler}<button type="button" class="btn btn-default" data-enregistrer>💾 Enregistrer</button>
          <button type="button" class="btn btn-primary" data-terminer>✅ Terminer le comptage</button></div>`
          : mode === "validation" ? `<div class="se-c-barre">${annuler}<button type="button" class="btn btn-default" data-renvoyer>↩ Renvoyer au comptage</button>
          <button type="button" class="btn btn-primary" data-valider>✅ Valider et rapprocher</button></div>`
          : mode === "confirmation" ? `<div class="se-c-barre">${annuler}<button type="button" class="btn btn-default" data-renvoyer>🔁 Tout recompter</button>
          <button type="button" class="btn btn-primary" data-confirmer>✅ Accepter</button></div>`
          : mode === "confirmation_pour" ? `<div class="se-c-barre">${annuler}<button type="button" class="btn btn-primary" data-accepter-pour>✅ Accepter pour l’employé</button></div>`
          : annuler ? `<div class="se-c-barre">${annuler}</div>` : ""}
      </div>`);
    const bilan = () => {
      let n = 0, e = 0, v = 0, pv = 0;
      d.lignes.forEach((l) => { const s = saisies[l.item_code]; if (s.qte_comptee == null || s.qte_comptee === "") return; n++;
        const ec = Math.round((+s.qte_comptee - l.qte_systeme) * 1e6) / 1e6; if (ec) { e++; v += ec * l.taux; pv += ec * (l.prix_vente || 0); } });
      $c.find("#se-c-bilan").html(`${n}/${d.lignes.length} comptés · <b class="${e ? "neg" : "pos"}">${e} écart(s)</b>${e ? ` · revient ${se_argent(v)} · vente ${se_argent(pv)}` : ""}`);
    };
    bilan();
    $c.find("[data-retour]").on("click", () => this.chargerVerif());
    $c.find("#se-c-filtre").on("input", (e) => { const q = (e.currentTarget.value || "").toLowerCase().trim();
      $c.find(".se-c-ligne").each((_, el) => $(el).toggle(!q || ($(el).attr("data-cle") || "").includes(q))); });
    $c.find("input.q").on("input", (e) => {
      const $l = $(e.currentTarget).closest(".se-c-ligne"), code = $l.attr("data-item"), l = d.lignes.find((x) => x.item_code === code);
      const v = String(e.currentTarget.value).replace(",", ".");
      saisies[code].qte_comptee = v === "" ? null : +v;
      const ec = v === "" ? null : Math.round((+v - l.qte_systeme) * 1e6) / 1e6;
      $l.toggleClass("fait", v !== "").find(".ec").text(ec == null ? "" : se_signe(ec)).attr("class", "ec " + (ec ? (ec < 0 ? "neg" : "pos") : ""));
      bilan();
    });
    const comptes = () => JSON.stringify(saisies);
    $c.find("[data-enregistrer]").on("click", async () => {
      await frappe.call({ method: SE_API + "enregistrer_verification", args: { name, comptes: comptes() }, freeze: true });
      frappe.show_alert({ message: "Comptage enregistré — vous pourrez reprendre", indicator: "green" }, 3);
    });
    const retour = () => { this.comptage = null; this.chargerVerif(); this.chargerAttente(); };
    $c.find("[data-terminer]").on("click", () => {
      const n = Object.values(saisies).filter((s) => s.qte_comptee != null && s.qte_comptee !== "").length;
      frappe.confirm(`Terminer le comptage avec <b>${n}</b> article(s) compté(s) sur ${d.lignes.length} ?<br>Les écarts seront figés et soumis à la validation d’un responsable magasin ; le rapprochement de stock se fera à sa validation.`, async () => {
        const r = (await frappe.call({ method: SE_API + "terminer_verification", args: { name, comptes: comptes() }, freeze: true })).message;
        frappe.msgprint({ title: "Comptage terminé — à valider", indicator: r.nb_ecarts ? "orange" : "green",
          message: `${r.nb_comptes} article(s) comptés, <b>${r.nb_ecarts} écart(s)</b>${r.nb_ecarts ? ` · valeur nette ${se_esc(se_argent(r.valeur_ecarts))} · manquants ${se_esc(se_argent(r.valeur_manquants))}` : ""}<br>Un responsable magasin doit maintenant valider.` });
        retour();
      });
    });
    $c.find("[data-valider]").on("click", () => {
      const changes = d.lignes.filter((l) => { const s = saisies[l.item_code]; const q = s.qte_comptee === "" ? null : s.qte_comptee; return (q == null) !== (l.qte_comptee == null) || (q != null && Math.abs(q - l.qte_comptee) > 1e-6); }).length;
      const e = Object.values(saisies).filter((s) => s.qte_comptee != null && s.qte_comptee !== "").length;
      const ecarts = d.lignes.filter((l) => { const s = saisies[l.item_code]; return s.qte_comptee != null && s.qte_comptee !== "" && Math.abs(+s.qte_comptee - l.qte_systeme) > 1e-6; }).length;
      frappe.confirm(`Valider ${changes ? `avec <b>${changes}</b> quantité(s) corrigée(s)` : `le comptage (${e} article(s))`} ?<br>L’employé devra accepter ces quantités ; ${ecarts ? `un <b>rapprochement de stock</b> alignera alors ${ecarts} article(s).` : "aucun écart : pas de rapprochement."}`, async () => {
        const r = (await frappe.call({ method: SE_API + "valider_verification", args: changes ? { name, comptes: comptes() } : { name }, freeze: true })).message;
        if (r.renvoye) { frappe.msgprint({ title: "À recompter", indicator: "orange", message: `${r.a_recompter.length} article(s) ont bougé depuis le comptage : leur comptage est effacé et la fiche repart chez l’employé.<br>${r.a_recompter.map(se_esc).join(", ")}` }); retour(); return; }
        frappe.msgprint({ title: "Validé — à accepter par l’employé", indicator: "green",
          message: `${r.ajustes.length ? r.ajustes.map(se_esc).join("<br>") + "<br>" : "Aucune quantité changée.<br>"}L’employé du stock doit maintenant accepter ces quantités ; le rapprochement sera passé à son accord.` });
        retour();
      });
    });
    $c.find("[data-confirmer]").on("click", () => {
      const changes = d.lignes.filter((l) => { const s = saisies[l.item_code]; const q = s.qte_comptee === "" ? null : s.qte_comptee; return (q == null) !== (l.qte_comptee == null) || (q != null && Math.abs(q - l.qte_comptee) > 1e-6); }).length;
      frappe.confirm(changes
        ? `Vous avez changé <b>${changes}</b> quantité(s) : la fiche repart chez le responsable magasin pour revalidation. Continuer ?`
        : `D’accord avec les quantités validées ? Le rapprochement de stock sera passé sur votre stock.`, async () => {
        const r = (await frappe.call({ method: SE_API + "confirmer_verification", args: changes ? { name, comptes: comptes() } : { name }, freeze: true })).message;
        if (r.renvoye) { frappe.msgprint({ title: "À recompter", indicator: "orange", message: `${r.a_recompter.length} article(s) ont bougé depuis votre comptage : recomptez-les (lignes marquées) puis terminez à nouveau.` }); retour(); return; }
        if (r.statut === "À valider") { frappe.msgprint({ title: "Renvoyé au responsable — à valider", indicator: "orange", message: `${r.changes.map(se_esc).join("<br>")}<br>Le responsable magasin doit revalider, puis vous accepterez.` }); retour(); return; }
        frappe.msgprint({ title: "Vérification terminée", indicator: "green", message: r.rapprochement ? `Validation mutuelle acquise. Rapprochement ${se_lien("Stock Reconciliation", r.rapprochement)} passé.` : "Validation mutuelle acquise. Aucun écart : pas de rapprochement." });
        retour();
      });
    });
    $c.find("[data-accepter-pour]").on("click", () => {
      frappe.confirm(`Accepter les quantités validées <b>à la place de l’employé du stock</b> ?<br>Le rapprochement de stock sera passé sur son stock et la fiche gardera la trace de votre acceptation.`, async () => {
        const r = (await frappe.call({ method: SE_API + "confirmer_verification", args: { name, pour_employe: 1 }, freeze: true })).message;
        if (r.renvoye) { frappe.msgprint({ title: "À recompter", indicator: "orange", message: `${r.a_recompter.length} article(s) ont bougé depuis le comptage : leur comptage est effacé et la fiche repasse en comptage.<br>${r.a_recompter.map(se_esc).join(", ")}` }); retour(); return; }
        frappe.msgprint({ title: "Vérification terminée", indicator: "green", message: r.rapprochement ? `Acceptée pour l’employé. Rapprochement ${se_lien("Stock Reconciliation", r.rapprochement)} passé.` : "Acceptée pour l’employé. Aucun écart : pas de rapprochement." });
        retour();
      });
    });
    $c.find("[data-annuler-verif]").on("click", () => {
      frappe.prompt({ fieldtype: "Small Text", fieldname: "motif", label: "Motif (employé parti, fiche en double…)", reqd: 1 }, async (v) => {
        await frappe.call({ method: SE_API + "annuler_verification", args: { name, motif: v.motif }, freeze: true });
        frappe.show_alert({ message: `${name} annulée`, indicator: "orange" }, 4);
        retour();
      }, `✖️ Annuler ${name}`, "Annuler la fiche");
    });
    $c.find("[data-renvoyer]").on("click", () => {
      frappe.prompt({ fieldtype: "Small Text", fieldname: "motif", label: "Motif (facultatif)" }, async (v) => {
        await frappe.call({ method: SE_API + "renvoyer_verification", args: { name, motif: v.motif || null }, freeze: true });
        frappe.show_alert({ message: "Fiche renvoyée au comptage", indicator: "orange" }, 4);
        retour();
      }, mode === "confirmation" ? "Recompter" : "Renvoyer au comptage", "Confirmer");
    });
  }

  validerZero(choisies, nApports, nRetours) {
    const res = this.zero, m = se_esc(this.libelle(res.magasin)), cible = se_esc(this.libelle(res.entrepot));
    frappe.confirm(`Remettre <b>${cible}</b> à zéro sur ${choisies.length} article(s) ?<br>`
      + (nApports ? `• un transfert ${m} → ${cible} (${nApports} article(s))<br>` : "")
      + (nRetours ? `• un transfert ${cible} → ${m} (${nRetours} article(s))<br>` : "")
      + `L’écart restera sur ${m}.`, async () => {
      const r = (await frappe.call({ method: SE_API + "remettre_a_zero", freeze: true, freeze_message: "Remise à zéro…",
        args: { entrepot: res.entrepot, items: choisies.map((l) => l.item_code) } })).message;
      frappe.msgprint({ title: "Remise à zéro faite", indicator: "green",
        message: `${r.ecritures.map((n) => se_lien("Stock Entry", n)).join("<br>")}`
          + (r.restant.length ? `<br><br>⚠️ Encore non nul (a bougé pendant l’opération ?) : ${r.restant.map(se_esc).join(", ")}` : "") });
      this.ouvrirZero(res.entrepot);
    });
  }

  // ── BL du Magasin : approbation par le magasinier principal, sorties exceptionnelles ───────
  /** Pastille de l'onglet : BL à approuver (+ sorties à vérifier et écarts pour le magasinier principal). */
  async majBadgeBL() {
    if (this.ctx.sous_traitant) return;
    try {
      const e = (await frappe.call({ method: SE_BL + "etat" })).message;
      const n = e.a_approuver.length + e.a_verifier.length + e.ecarts.length;
      this.$r.find("#se-b-badge").toggle(!!n).text(n);
    } catch (err) { /* champs absents (patch non joué) : pas de pastille */ }
  }

  async chargerBL() {
    const jeton = (this.jetons.bl = (this.jetons.bl || 0) + 1);
    let e;
    try { e = (await frappe.call({ method: SE_BL + "etat" })).message; }
    catch (err) { this.$r.find("#se-b-contenu").html(`<div class="se-vide">Onglet indisponible : lancer la migration (patch ensure_sorties_magasin).</div>`); return; }
    if (jeton !== this.jetons.bl || this.onglet !== "bl") return;
    const $c = this.$r.find("#se-b-contenu"), P = e.principal;
    const n = e.a_approuver.length + e.a_verifier.length + e.ecarts.length;
    this.$r.find("#se-b-badge").toggle(!!n).text(n);
    // Stock de l'entrepôt de sortie par article suivi : BL en attente = « stock N → N−q » ; BL validé = « stock après : N ».
    const stock = (b, l) => !l.suivi || l.stock == null ? "" : b.docstatus === 1
      ? `<small class="st ${l.stock < 0 ? "neg" : ""}">stock après : <b>${se_q(l.stock)}</b></small>`
      : `<small class="st ${l.stock - l.qte < 0 ? "neg" : ""}">stock ${se_q(l.stock)} → <b>${se_q(l.stock - l.qte)}</b></small>`;
    const lignes = (b) => b.lignes.map((l) => `<div class="se-b-ligne">${se_img(l.image)}<div style="min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div><div class="se-code">${se_esc(l.item_code)}</div></div>
        <span class="q">${se_q(l.qte)}<small> ${se_esc(l.uom || "")}</small></span><span class="w">${se_esc(se_court(l.entrepot || ""))}${stock(b, l)}</span></div>`).join("");
    // Échange : la pièce reprise RENTRE en stock (BL retour créé à l'approbation) — affichée sous les lignes, en vert.
    const reprises = (b) => (b.reprises || []).map((r) => `<div class="se-b-ligne reprise">${se_img(r.image)}<div style="min-width:0"><div class="se-nom">↩️ ${se_esc(r.item_name || r.item_code)}</div><div class="se-code">${se_esc(r.item_code)} · pièce reprise, rentre en stock</div></div>
        <span class="q">+${se_q(r.qte)}<small> ${se_esc(r.uom || "")}</small></span><span class="w">${se_esc(se_court(r.entrepot || ""))}${r.suivi && r.stock != null ? `<small class="st">stock ${se_q(r.stock)} → <b>${se_q(r.stock + r.qte)}</b></small>` : ""}</span></div>`).join("");
    const entete = (b, extra) => `<div class="t">🧾 ${se_lien("Delivery Note", b.name)} — ${se_esc(b.client)}</div>
        <div class="d">${b.commande ? `${se_lien("Sales Order", b.commande, "commande " + b.commande)} · ` : ""}créé par ${se_esc(b.par || "")} le ${se_esc(b.creation || "")}${b.livre_par ? ` · livré par ${se_esc(b.livre_par)}` : ""}${b.remarque ? ` · ${se_esc(b.remarque)}` : ""}${extra || ""}</div>`;
    const tag = (b) => b.verification === "À vérifier" ? `<span class="se-b-tag v">⏳ à vérifier</span>` : b.verification === "Conforme" ? `<span class="se-b-tag c">✅ conforme</span>` : b.verification === "Écart" ? `<span class="se-b-tag e">⚠️ écart</span>` : "";
    const vieux = (b) => (b.age_h != null && b.age_h > 24 ? ` · <b class="neg">depuis ${Math.round(b.age_h / 24)} j</b>` : "");
    // Jaune = la commande a encore une tâche ouverte à venir (pas urgent) ; rouge = rien de prévu ou tâche passée.
    const futur = (b) => b.tache_future ? ` · <b style="color:#b45309">📅 tâche ${se_esc(b.tache_future.type || "")} le ${se_esc(b.tache_future.quand)}${b.tache_future.employe ? ` (${se_esc(b.tache_future.employe)})` : ""}</b>` : "";
    const approuver = e.a_approuver.map((b) => `<div class="se-b-carte attente ${b.tache_future ? "futur" : ""}" data-bl="${se_esc(b.name)}">${entete(b, (b.tache_future ? futur(b) : vieux(b)))}${lignes(b)}${reprises(b)}
        <div class="se-b-actions">${P ? `<button type="button" class="btn btn-primary btn-sm" data-approuver="${se_esc(b.name)}">✅ Approuver la sortie</button>
          <button type="button" class="btn btn-default btn-sm" data-rejeter="${se_esc(b.name)}">✖️ Rejeter</button>`
          : `<button type="button" class="btn btn-warning btn-sm" data-exceptionnelle="${se_esc(b.name)}">⚠️ Sortie exceptionnelle (magasinier absent)</button>`}</div></div>`).join("");
    const verifier = e.a_verifier.map((b) => `<div class="se-b-carte verif">${entete(b, ` · <b>sorti par ${se_esc(b.sorti_par || "")} le ${se_esc(b.sorti_le || "")}</b>${vieux(b)}${b.note ? `<br>📝 ${se_esc(b.note)}` : ""}`)}${lignes(b)}
        <div class="se-b-actions"><button type="button" class="btn btn-primary btn-sm" data-conforme="${se_esc(b.name)}">✅ Conforme</button>
          <button type="button" class="btn btn-danger btn-sm" data-ecart="${se_esc(b.name)}">⚠️ Écart constaté</button></div></div>`).join("");
    const ecarts = e.ecarts.map((b) => `<div class="se-b-carte ecart">${entete(b, ` · sorti par ${se_esc(b.sorti_par || "")} le ${se_esc(b.sorti_le || "")} · écart relevé par ${se_esc(b.verifie_par || "")} le ${se_esc(b.verifie_le || "")}<br>📝 ${se_esc(b.note || "")}`)}${lignes(b)}
        <div class="se-b-actions"><button type="button" class="btn btn-default btn-sm" data-regle="${se_esc(b.name)}">✅ Écart réglé</button></div></div>`).join("");
    const histo = (liste) => liste.map((b) => `<div class="se-v-h" style="cursor:default"><span class="d">${se_esc(b.sorti_le || b.creation || "")}</span>
        <span class="n">${tag(b) || `<span class="se-b-tag c">✅ approuvé</span>`} ${se_lien("Delivery Note", b.name)} · ${se_esc(b.client)} · ${b.nb} art.${b.sorti_par ? ` · ${se_esc(b.sorti_par)}` : ""}${b.verifie_par ? ` · vérifié par ${se_esc(b.verifie_par)}` : ""}${b.note ? `<div class="se-note">📝 ${se_esc(b.note)}</div>` : ""}</span></div>`).join("");
    $c.html(`<div class="se-card se-v-stock"><div class="t">🧾 BL du Magasin</div>
        <div class="plan">${P ? `Vous êtes <b>magasinier principal</b> : vous seul approuvez les sorties du Magasin. En votre absence, un employé peut faire une <b>sortie exceptionnelle</b> : elle est tracée ici pour votre vérification.`
          : `Les BL contenant des articles du Magasin attendent l’approbation du magasinier principal (${se_esc((e.principaux || []).join(", "))}). S’il est absent, vous pouvez faire une <b>sortie exceptionnelle</b> : le stock sort, et la sortie sera vérifiée à son retour.`}</div></div>
      <div class="se-titre">📥 À approuver (${e.a_approuver.length})</div>
      ${approuver || `<div class="se-vide">Aucun BL en attente.</div>`}
      ${P ? `<div class="se-titre">⚠️ Sorties exceptionnelles à vérifier (${e.a_verifier.length})</div>${verifier || `<div class="se-vide">Rien à vérifier.</div>`}
        ${e.ecarts.length ? `<div class="se-titre">❗ Écarts à régler (${e.ecarts.length})</div>${ecarts}` : ""}
        <div class="se-titre">🕘 Dernières sorties</div><div class="se-card">${histo(e.historique) || `<div class="se-note">Aucune.</div>`}</div>`
        : `<div class="se-titre">🕘 Mes sorties exceptionnelles</div><div class="se-card">${histo(e.miennes) || `<div class="se-note">Aucune.</div>`}</div>`}`);
    const geste = async (method, args, confirmation, apres) => {
      const faire = async () => { try { await frappe.call({ method: SE_BL + method, args, freeze: true }); frappe.show_alert({ message: apres, indicator: "green" }, 4); } finally { this.chargerBL(); } };
      confirmation ? frappe.confirm(confirmation, faire) : faire();
    };
    $c.find("[data-approuver]").on("click", (ev) => { const name = $(ev.currentTarget).attr("data-approuver"); const b = e.a_approuver.find((x) => x.name === name);
      if (b && (b.reprises || []).length) return this.confirmerEchange(b, (choix) => geste("approuver", { name, confirme_echange: 1, ...choix }, null, `${name} approuvé — pièce reprise en stock`));
      geste("approuver", { name }, `Approuver la sortie du Magasin pour <b>${se_esc(name)}</b> ? Le stock sort.`, `${name} approuvé`); });
    $c.find("[data-rejeter]").on("click", (ev) => { const name = $(ev.currentTarget).attr("data-rejeter");
      frappe.prompt({ fieldtype: "Small Text", fieldname: "motif", label: "Motif du rejet", reqd: 1 }, (v) => geste("rejeter", { name, motif: v.motif }, null, `${name} rejeté`), `Rejeter ${name}`, "Rejeter"); });
    $c.find("[data-exceptionnelle]").on("click", (ev) => { const name = $(ev.currentTarget).attr("data-exceptionnelle");
      frappe.prompt({ fieldtype: "Small Text", fieldname: "motif", label: "Pourquoi sans le magasinier ? (facultatif)" },
        (v) => geste("sortie_exceptionnelle", { name, motif: v.motif || null }, null, `${name} validé — sortie à vérifier par le magasinier`),
        `⚠️ Sortie exceptionnelle ${name}`, "Valider et faire sortir le stock"); });
    $c.find("[data-conforme]").on("click", (ev) => { const name = $(ev.currentTarget).attr("data-conforme"); geste("verifier", { name, resultat: "Conforme" }, null, `${name} : conforme`); });
    $c.find("[data-ecart]").on("click", (ev) => { const name = $(ev.currentTarget).attr("data-ecart");
      frappe.prompt({ fieldtype: "Small Text", fieldname: "note", label: "Écart constaté (quantités, articles…)", reqd: 1 }, (v) => geste("verifier", { name, resultat: "Écart", note: v.note }, null, `${name} : écart consigné, l’employé est prévenu`), `⚠️ Écart sur ${name}`, "Consigner l’écart"); });
    $c.find("[data-regle]").on("click", (ev) => { const name = $(ev.currentTarget).attr("data-regle");
      frappe.prompt({ fieldtype: "Small Text", fieldname: "note", label: "Comment l’écart a été réglé" }, (v) => geste("verifier", { name, resultat: "Conforme", note: v.note || null }, null, `${name} : écart réglé`), `Écart réglé — ${name}`, "Marquer réglé"); });
  }

  /** Échange : avant d'approuver, le magasinier voit la pièce qui SORT (et de quel entrepôt) et la pièce
   *  REPRISE (et dans quel entrepôt elle rentre, par le BL retour créé automatiquement), et confirme les deux.
   *  Sans les deux cases, pas d'approbation — le serveur refuse aussi sans `confirme_echange`. */
  async confirmerEchange(b, faire) {
    const sorties = (b.lignes || []).filter((l) => l.suivi), echanges = (b.lignes || []).filter((l) => !l.suivi), reprises = b.reprises || [];
    // Stock par entrepôt des pièces concernées : le magasinier choisit où ça sort et où ça rentre, le stock suit.
    let stocks = {};
    try { stocks = (await frappe.call({ method: SE_BL + "stocks_par_entrepot", args: { codes: [...sorties, ...reprises].map((l) => l.item_code) } })).message || {}; } catch (err) { /* sans stock, le choix reste possible */ }
    const entrepots = this.ctx.entrepots || [];
    const options = (choisi) => entrepots.map((w) => `<option value="${se_esc(w.name)}" ${w.name === choisi ? "selected" : ""}>${se_esc(w.libelle || w.name)}${w.employes && w.employes.length ? ` (${se_esc(w.employes.join(", "))})` : ""}</option>`).join("");
    const defSortie = (sorties[0] || {}).entrepot || this.ctx.magasin, defReprise = (reprises[0] || {}).entrepot || this.ctx.magasin;
    const ligne = (l, signe, wh) => { const st = (stocks[l.item_code] || {})[wh]; const av = st == null ? 0 : st;
      return `<div class="se-b-ligne">${se_img(l.image)}<div style="min-width:0"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div><div class="se-code">${se_esc(l.item_code)}</div></div>
        <span class="q">${signe}${se_q(l.qte)}<small> ${se_esc(l.uom || "")}</small></span><span class="w">${se_esc(se_court(wh || ""))}${l.suivi ? `<small class="st ${(signe === "+" ? av + l.qte : av - l.qte) < 0 ? "neg" : ""}">stock ${se_q(av)} → <b>${se_q(signe === "+" ? av + l.qte : av - l.qte)}</b></small>` : ""}</span></div>`; };
    const d = new frappe.ui.Dialog({ title: `🔁 Échange — ${b.name} — ${b.client}`, size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "corps" },
        { fieldtype: "Check", fieldname: "ok_sortie", label: "Je remets la pièce au client : elle SORT de l’entrepôt choisi" },
        { fieldtype: "Check", fieldname: "ok_reprise", label: "J’ai récupéré la pièce reprise : elle RENTRE dans l’entrepôt choisi" }],
      primary_action_label: "✅ Approuver l’échange", primary_action: (v) => {
        if (!v.ok_sortie || !v.ok_reprise) { frappe.msgprint({ title: "Confirmation incomplète", indicator: "orange", message: "Cochez la pièce sortie ET la pièce reprise." }); return; }
        const entrepot_sortie = $w.find("[data-sortie]").val(), entrepot_reprise = $w.find("[data-reprise]").val();
        d.hide(); faire({ entrepot_sortie, entrepot_reprise }); } });
    const $w = d.fields_dict.corps.$wrapper;
    $w.html(`<div class="se-ech">
      ${echanges.length ? `<div class="se-note">Article(s) d’échange : ${echanges.map((l) => `<b>${se_esc(l.item_name || l.item_code)}</b> (${se_esc(l.item_code)})`).join(", ")} — ne bouge pas le stock, il porte la différence de prix.</div>` : ""}
      <div class="bloc sortie"><div class="t">📤 Sort du stock <small>— pièce remise au client</small><span class="choix">Sort de <select class="form-control" data-sortie>${options(defSortie)}</select></span></div><div data-l-sortie></div></div>
      <div class="bloc entree"><div class="t">📥 Rentre en stock <small>— pièce reprise, par un BL retour créé automatiquement</small><span class="choix">Rentre dans <select class="form-control" data-reprise>${options(defReprise)}</select></span></div><div data-l-reprise></div></div>
      <div class="se-note">Vérifiez l’entrepôt de chaque mouvement avant de cocher (une pièce reprise usagée va aux Articles défectueux). Une fois approuvé, le BL d’échange est validé et le BL retour est créé et validé dans la foulée ; le choix des entrepôts est tracé sur le BL.</div></div>`);
    const rendre = () => { const ws = $w.find("[data-sortie]").val(), wr = $w.find("[data-reprise]").val();
      $w.find("[data-l-sortie]").html(sorties.map((l) => ligne(l, "−", ws)).join("") || `<div class="se-note">Aucune pièce suivie en stock ne sort.</div>`);
      $w.find("[data-l-reprise]").html(reprises.map((r) => ligne(r, "+", wr)).join("")); };
    $w.find("[data-sortie], [data-reprise]").on("change", rendre); rendre();
    const btn = d.get_primary_btn().prop("disabled", true);
    const maj = () => btn.prop("disabled", !(d.get_value("ok_sortie") && d.get_value("ok_reprise")));
    ["ok_sortie", "ok_reprise"].forEach((f) => d.fields_dict[f].$input.on("change", maj));
    d.show();
  }

  // ── Inventaire du Magasin (rôle Inventaire Magasin) ───────────────────────
  /** L'inventaire ouvert et les derniers terminés. */
  async chargerInventaire() {
    this.feuilleInv = null;
    const jeton = (this.jetons.inv = (this.jetons.inv || 0) + 1);
    const res = (await frappe.call({ method: SE_INV + "inventaires_etat" })).message;
    if (jeton !== this.jetons.inv || this.onglet !== "inventaire") return;
    const $c = this.$r.find("#se-i-contenu"), ouverts = res.ouverts || [], SANS = res.sans_zone;
    const etat = (v) => (v.statut === "Terminé" ? "✅" : v.statut === "Annulé" ? "✖️" : "📝");
    const peri = (v) => `<span class="se-badge">📍 ${se_esc(v.perimetre_libelle || "Tout le Magasin")}</span>`;
    // Historique par zone : en cours / dernier inventaire clôturé / jamais inventoriée.
    const zh = (res.zones_historique || []).map((z) => `<span class="se-zh ${z.en_cours ? "ec" : z.dernier ? "" : "jamais"}" title="${z.en_cours ? `En cours : ${se_esc(z.en_cours)}` : z.dernier ? `Dernier inventaire clôturé ${se_esc(se_dt(z.dernier))} (${se_esc(z.dernier_inventaire)})` : "Jamais inventoriée"}">${se_esc(z.libelle)}<small>${z.en_cours ? "en cours" : z.dernier ? se_dt(z.dernier) : "jamais"}</small></span>`).join("");
    const hist = res.dernieres.map((v) => `<div class="se-v-h" style="cursor:default">
        <span class="d">${se_dt(v.termine_le || v.date)}</span>
        <span class="n">${etat(v)} ${peri(v)} ${v.nb_comptes}/${v.nb_lignes} comptés · <b class="${v.nb_ecarts ? "neg" : "pos"}">${v.nb_ecarts} écart(s)</b>${v.termine_par ? ` · ${se_esc(v.termine_par)}` : ""}
          <span class="se-note">${se_lien("Inventaire Magasin", v.name)}${v.rapprochement ? ` · ${se_lien("Stock Reconciliation", v.rapprochement, "écriture " + v.rapprochement)}` : v.statut === "Terminé" ? " · sans écriture (aucun écart)" : ""}</span></span>
        <span class="v ${v.valeur_ecarts < 0 ? "neg" : v.valeur_ecarts > 0 ? "pos" : ""}">${v.nb_ecarts ? se_argent(v.valeur_ecarts) : v.statut === "Terminé" ? "✓" : ""}</span>
        <span class="se-lien" data-voir="${se_esc(v.name)}" style="cursor:pointer">🔍</span></div>`).join("");
    $c.html(`<span class="se-retour" data-retour>← Solde</span>
      <div class="se-card se-v-stock"><div class="t">📋 Inventaire du ${se_esc(res.libelle)}</div>
        <div class="plan">Tous les articles suivis en stock au Magasin, avec photo et description, <b>une case par emplacement</b> (zones de l’article) : la somme des cases est la quantité comptée, comparée au stock système. Le comptage s’enregistre au fil de l’eau ; à la clôture, <b>une écriture de stock</b> (rapprochement) aligne le Magasin sur le compté. Les articles non comptés ne bougent pas.</div>
        <div class="plan">Inventaire <b>complet</b> ou <b>partiel</b> (quelques zones à la fois, plusieurs en parallèle tant qu’ils ne se recoupent pas) : seuls les articles rangés dans les zones choisies sont sur la feuille.</div>
        ${ouverts.map((ec) => `<div class="plan">📝 ${peri(ec)} en cours depuis le <b>${se_dt(ec.date)}</b>${ec.ouvert_par ? ` (${se_esc(ec.ouvert_par)})` : ""} : <b>${ec.nb_comptes}/${ec.nb_lignes}</b> comptés · <b class="${ec.nb_ecarts ? "neg" : "pos"}">${ec.nb_ecarts} écart(s)</b>${ec.nb_ecarts ? ` · ${se_argent(ec.valeur_ecarts)}` : ""}
            <button type="button" class="btn btn-primary btn-sm" data-reprendre="${se_esc(ec.name)}" style="margin-left:8px">Reprendre</button></div>`).join("")}
        ${ouverts.some((v) => !(v.perimetre || []).length) ? "" : `<button type="button" class="btn btn-primary" data-ouvrir>▶ Ouvrir un inventaire${ouverts.length ? " partiel" : ""}</button>`}
      </div>
      <div class="se-titre">📍 Zones : dernier inventaire</div>
      <div class="se-card"><div class="se-zh-grille">${zh || `<span class="se-note">Aucune zone dans l’espace du Magasin.</span>`}</div></div>
      <div class="se-titre">🕘 Derniers inventaires</div>
      <div class="se-card">${hist || `<div class="se-note">Aucun inventaire terminé.</div>`}</div>`);
    $c.find("[data-retour]").on("click", () => this.montrer("solde"));
    $c.find("[data-ouvrir]").on("click", () => {
      // Périmètre : zones cochées = inventaire partiel ; rien de coché = tout le Magasin (refusé si un partiel est ouvert).
      const prises = {}; ouverts.forEach((v) => (v.perimetre || []).forEach((z) => { prises[z] = v.name; }));
      const opts = (res.zones || []).filter((z) => !z.est_espace && !prises[z.name]).map((z) => ({ label: z.code, value: z.name }));
      if (!prises[SANS]) opts.push({ label: "Non rangé (sans zone)", value: SANS });
      const dlg = new frappe.ui.Dialog({ title: "▶ Ouvrir un inventaire", fields: [
          { fieldtype: "HTML", fieldname: "aide", options: `<div class="se-note" style="margin-bottom:8px">Cochez les zones à inventorier (<b>inventaire partiel</b>) ; rien de coché = <b>tout le Magasin</b>.${Object.keys(prises).length ? `<br>Déjà en cours (non proposées) : ${Object.keys(prises).map((z) => se_esc(z === SANS ? "Non rangé" : ((res.zones || []).find((x) => x.name === z) || { code: z }).code)).join(", ")}.` : ""}</div>` },
          { fieldtype: "MultiCheck", fieldname: "zones", label: "Zones", options: opts, columns: 3 }],
        primary_action_label: "Ouvrir",
        primary_action: async (v) => {
          const zones = v.zones || [];
          if (!zones.length && ouverts.length) { frappe.msgprint("Un inventaire partiel est en cours : clôturez-le avant d’ouvrir un inventaire de tout le Magasin, ou cochez des zones."); return; }
          dlg.hide();
          const name = (await frappe.call({ method: SE_INV + "ouvrir_inventaire", args: { zones: JSON.stringify(zones) }, freeze: true, freeze_message: "Photographie du stock du Magasin…" })).message;
          this.ouvrirInventaire(name);
        } });
      dlg.show();
    });
    $c.find("[data-reprendre], [data-voir]").on("click", (e) => this.ouvrirInventaire($(e.currentTarget).attr("data-reprendre") || $(e.currentTarget).attr("data-voir")));
  }

  /** Feuille d'inventaire : une ligne par article, une case par emplacement ; enregistrement au fil de l'eau. */
  async ouvrirInventaire(name) {
    this.feuilleInv = name;
    const d = (await frappe.call({ method: SE_INV + "detail_inventaire", args: { name } })).message;
    const $c = this.$r.find("#se-i-contenu"), fini = d.fiche.statut !== "En cours", SANS = d.sans_zone;
    const zinfo = {}; d.zones.forEach((z) => { zinfo[z.name] = z; });
    const libZone = (z) => (z === SANS ? "Non rangé" : !zinfo[z] ? z : zinfo[z].est_espace ? z : zinfo[z].espace === d.espace ? zinfo[z].code : `${zinfo[z].espace} ${zinfo[z].code}`);
    const r6 = (n) => Math.round(n * 1e6) / 1e6;
    // État de saisie par article : colonnes (zones de l'article, puis celles ajoutées) et valeur de chaque case.
    const etat = {};
    d.lignes.forEach((l) => {
      const colonnes = l.zones.slice();
      Object.keys(l.cases).forEach((z) => { if (!colonnes.includes(z)) colonnes.push(z); });
      if (!colonnes.length) colonnes.push(SANS);
      const cases = {}; colonnes.forEach((z) => { cases[z] = l.cases[z] == null ? null : l.cases[z]; });
      etat[l.item_code] = { colonnes, cases, commentaire: l.commentaire || "", ajoutees: l.zones_ajoutees || [] };
    });
    const totalDe = (e) => { let t = null; Object.values(e.cases).forEach((v) => { if (v != null && v !== "") t = (t || 0) + (+v || 0); }); return t == null ? null : r6(t); };
    const ligne = (l) => {
      const e = etat[l.item_code], total = totalDe(e), compte = total != null, ecart = compte ? r6(total - l.qte_systeme) : null;
      return `<div class="se-i-ligne ${compte ? "fait" : ""}" data-item="${se_esc(l.item_code)}" data-cle="${se_esc(((l.item_name || "") + " " + l.item_code + " " + (l.description || "")).toLowerCase())}">
        <div class="haut">${se_img(l.image)}
          <div class="txt"><div class="se-nom">${se_esc(l.item_name || l.item_code)}</div>
            <div class="se-code">${se_esc(l.item_code)}${l.groupe ? ` · ${se_esc(l.groupe)}` : ""}${l.uom ? ` · ${se_esc(l.uom)}` : ""}</div>
            ${l.description ? `<div class="se-desc" title="${se_esc(l.description)}">${se_esc(l.description)}</div>` : ""}</div>
          <div class="sys ${l.qte_systeme < 0 ? "negatif" : ""}">système<b>${se_q(l.qte_systeme)}</b>${l.bouge ? `<small class="neg">maintenant ${se_q(l.qte_actuelle)}</small>` : ""}</div></div>
        <div class="cases">${e.colonnes.map((z) => `<label class="case ${e.ajoutees.includes(z) ? "ajoutee" : ""}"><span class="z">${se_esc(libZone(z))}${e.ajoutees.includes(z) && !fini ? `<span class="x" data-retirer-zone="${se_esc(z)}" title="Retirer cet emplacement de l’article">✕</span>` : ""}</span>
            <input type="number" inputmode="decimal" min="0" step="any" class="form-control" data-zone="${se_esc(z)}" value="${e.cases[z] == null ? "" : se_esc(e.cases[z])}" ${fini ? "disabled" : ""} placeholder="–"></label>`).join("")}
          ${fini ? "" : `<span class="se-bt" data-ajouter-zone title="Ajouter un emplacement">＋</span>`}
          <div class="total">= <b class="tot">${compte ? se_q(total) : "?"}</b><span class="ec ${ecart ? (ecart < 0 ? "neg" : "pos") : ""}">${ecart == null ? "" : se_signe(ecart)}</span></div></div>
        ${l.compte_par ? `<div class="se-note qui">compté par ${se_esc(l.compte_par)} le ${se_dthm(l.compte_le)}${l.qte_rapprochee != null && fini ? ` · rapproché à <b>${se_q(l.qte_rapprochee)}</b>` : ""}${l.sans_taux ? ` · <span class="neg">sans taux : exclu</span>` : ""}${(l.zones_ajoutees || []).length ? ` · 📍 ajouté à l’article : ${se_esc(l.zones_ajoutees.join(", "))}` : ""}</div>` : ""}
      </div>`;
    };
    const optZones = () => { const g = {}; d.zones.forEach((z) => { (g[z.est_espace ? z.name : z.espace] = g[z.est_espace ? z.name : z.espace] || []).push(z); });
      return `<option value="">📍 Toutes les zones</option><option value="${SANS}">Non rangé (sans zone)</option>` + Object.keys(g).map((esp) => `<optgroup label="${se_esc(esp)}">${g[esp].map((z) => `<option value="${se_esc(z.name)}">${se_esc(z.est_espace ? `${z.name} (sans rayon)` : z.name)}</option>`).join("")}</optgroup>`).join(""); };
    $c.html(`<span class="se-retour" data-retour>← Inventaires</span>
      <div class="se-card">
        <div class="se-v-stock"><div class="t">📋 Inventaire ${se_esc(d.fiche.name)} — ${se_dt(d.fiche.date)} <span class="se-badge">📍 ${se_esc(d.fiche.perimetre_libelle || "Tout le Magasin")}</span> ${fini ? `<span class="se-badge">${se_esc(d.fiche.statut)}</span>` : ""}</div>
          ${fini ? `<div class="se-note">${d.fiche.termine_par ? `${d.fiche.statut === "Terminé" ? "Clôturé" : "Annulé"} par ${se_esc(d.fiche.termine_par)} le ${se_dthm(d.fiche.termine_le)}` : ""}${d.fiche.rapprochement ? ` · ${se_lien("Stock Reconciliation", d.fiche.rapprochement, "écriture " + d.fiche.rapprochement)}` : ""}${d.fiche.note ? `<br>${se_esc(d.fiche.note)}` : ""}</div>`
            : `<div class="se-note">Une case par emplacement : tapez ce que vous trouvez à chaque endroit, la somme fait le compté. « ＋ » ajoute un emplacement où l’article a été trouvé : il est <b>aussitôt ajouté aux emplacements de l’article</b> (✕ pour le retirer). Enregistrement automatique.</div>`}
          <div class="plan" id="se-i-bilan"></div><div class="se-i-etat" id="se-i-etat"></div></div>
        <div class="se-i-filtres"><input type="search" class="form-control se-saisie" id="se-i-filtre" placeholder="🔎 Article (nom, code, description)…">
          <select class="form-control" id="se-i-zone">${optZones()}</select></div>
        <div class="se-chips" id="se-i-vues" style="margin-top:6px"><span class="se-chip on" data-vue="tous">Tous</span><span class="se-chip" data-vue="non">À compter</span><span class="se-chip" data-vue="faits">Comptés</span><span class="se-chip" data-vue="ecarts">Écarts</span><span class="se-chip alerte" data-vue="negatifs">Négatifs</span><span class="se-chip tri" data-tri title="Grouper les articles par emplacement, dans l’ordre des zones">📍 Par zone</span></div>
        <div id="se-i-lignes"></div>
        <button type="button" class="btn btn-default se-plus" id="se-i-plus" style="display:none">Afficher plus</button>
        ${fini ? "" : `<div class="se-c-barre"><button type="button" class="btn btn-default" data-annuler>✖️ Annuler l’inventaire</button>
          <button type="button" class="btn btn-primary" data-cloturer>✅ Clôturer et passer l’écriture</button></div>`}
      </div>`);
    // ── Filtres et rendu paresseux ──
    let vue = "tous", zone = "", texte = "", nAff = 100, parZone = false;
    // Tri par zone : rang = ordre des zones (espace, puis code) ; « Non rangé » en dernier. Un article rangé à
    // plusieurs endroits est classé sous sa première zone (ou sous la zone filtrée).
    const rangZone = {}; d.zones.forEach((z, i) => { rangZone[z.name] = i; });
    const rang = (z) => (z === SANS || rangZone[z] == null ? 1e9 : rangZone[z]);
    const cleZone = (l) => { const e = etat[l.item_code]; if (zone && e.colonnes.includes(zone)) return zone; return e.colonnes.slice().sort((a, b) => rang(a) - rang(b))[0] || SANS; };
    const visibles = () => {
      const v = d.lignes.filter((l) => {
        const e = etat[l.item_code], t = totalDe(e);
        if (vue === "non" && t != null) return false;
        if (vue === "faits" && t == null) return false;
        if (vue === "ecarts" && (t == null || Math.abs(t - l.qte_systeme) < 1e-6)) return false;
        if (vue === "negatifs" && l.qte_systeme >= 0) return false;
        if (zone && !e.colonnes.includes(zone)) return false;
        if (texte && !((l.item_name || "") + " " + l.item_code + " " + (l.description || "")).toLowerCase().includes(texte)) return false;
        return true;
      });
      if (!parZone) return v;
      const nom = (l) => ((l.item_name || l.item_code) + "").toLowerCase();
      return v.map((l) => ({ l, r: rang(cleZone(l)), n: nom(l) })).sort((a, b) => a.r - b.r || (a.n < b.n ? -1 : a.n > b.n ? 1 : 0)).map((x) => x.l);
    };
    const peindre = () => {
      const v = visibles();
      let derniere = null;
      const html = v.slice(0, nAff).map((l) => {
        if (!parZone) return ligne(l);
        const z = cleZone(l), tete = z !== derniere ? `<div class="se-i-groupe">📍 ${se_esc(libZone(z))}</div>` : "";
        derniere = z; return tete + ligne(l);
      }).join("");
      $c.find("#se-i-lignes").html(html || `<div class="se-vide">Aucun article pour ce filtre.</div>`);
      $c.find("#se-i-plus").toggle(v.length > nAff).text(`Afficher plus (${Math.max(v.length - nAff, 0)} restants)`);
    };
    const bilan = () => {
      let n = 0, e = 0, val = 0;
      d.lignes.forEach((l) => { const t = totalDe(etat[l.item_code]); if (t == null) return; n++; const ec = r6(t - l.qte_systeme); if (ec) { e++; val += ec * l.taux; } });
      const neg = d.lignes.filter((l) => l.qte_systeme < 0).length;
      $c.find("#se-i-bilan").html(`<b>${n}/${d.lignes.length}</b> comptés · <b class="${e ? "neg" : "pos"}">${e} écart(s)</b>${e && d.voir_valeur ? ` · ${se_argent(val)}` : ""}${neg ? ` · <span class="neg">${neg} en stock négatif</span>` : ""}`);
    };
    // ── Enregistrement au fil de l'eau : seules les lignes modifiées partent, regroupées ──
    let sale = {}, minuterie = null, enCours = null;
    const marquer = (code) => { sale[code] = { cases: etat[code].cases, commentaire: etat[code].commentaire }; $c.find("#se-i-etat").text("✏️ modifications non enregistrées…"); clearTimeout(minuterie); minuterie = setTimeout(envoyer, 1500); };
    const envoyer = async () => {
      if (enCours) return enCours;
      if (!Object.keys(sale).length) return;
      const envoi = sale; sale = {};
      $c.find("#se-i-etat").text("⏳ enregistrement…");
      enCours = frappe.call({ method: SE_INV + "enregistrer_inventaire", args: { name, comptes: JSON.stringify(envoi) } })
        .then((r) => { $c.find("#se-i-etat").text(`💾 enregistré à ${se_hm((r.message || {}).enregistre_le)}`); })
        .fail(() => { Object.keys(envoi).forEach((k) => { if (!sale[k]) sale[k] = envoi[k]; }); $c.find("#se-i-etat").html(`<span class="neg">⚠️ non enregistré — réessai dans 5 s</span>`); clearTimeout(minuterie); minuterie = setTimeout(envoyer, 5000); })
        .always(() => { enCours = null; if (Object.keys(sale).length && !minuterie) minuterie = setTimeout(envoyer, 1500); });
      return enCours;
    };
    const vider = async () => { clearTimeout(minuterie); minuterie = null; await envoyer(); if (Object.keys(sale).length) await envoyer(); };
    window.onbeforeunload = () => (Object.keys(sale).length || enCours ? "Comptage non enregistré" : undefined);
    // Gestionnaires DÉLÉGUÉS (posés une fois : les lignes sont repeintes à chaque filtre).
    const brancher = () => {
      $c.find("#se-i-lignes").on("input", ".se-i-ligne input[data-zone]", (ev) => {
        const $l = $(ev.currentTarget).closest(".se-i-ligne"), code = $l.attr("data-item"), e = etat[code], l = d.lignes.find((x) => x.item_code === code);
        const v = String(ev.currentTarget.value).replace(",", ".");
        e.cases[$(ev.currentTarget).attr("data-zone")] = v === "" ? null : Math.max(+v || 0, 0);
        const t = totalDe(e), ec = t == null ? null : r6(t - l.qte_systeme);
        $l.toggleClass("fait", t != null).find(".tot").text(t == null ? "?" : se_q(t));
        $l.find(".ec").text(ec == null ? "" : se_signe(ec)).attr("class", "ec " + (ec ? (ec < 0 ? "neg" : "pos") : ""));
        bilan(); marquer(code);
      });
      $c.find("#se-i-lignes").on("keydown", ".se-i-ligne input[data-zone]", (ev) => {   // Entrée = case suivante
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        const tous = $c.find(".se-i-ligne input[data-zone]:enabled").toArray(), i = tous.indexOf(ev.currentTarget);
        if (i >= 0 && i + 1 < tous.length) { tous[i + 1].focus(); tous[i + 1].select(); }
      });
      $c.find("#se-i-lignes").on("click", "[data-ajouter-zone]", (ev) => {
        const $l = $(ev.currentTarget).closest(".se-i-ligne"), code = $l.attr("data-item"), e = etat[code];
        const dlg = new frappe.ui.Dialog({ title: `📍 ${se_esc(code)} — trouvé à…`, fields: [{ fieldtype: "HTML", fieldname: "z" }],
          primary_action_label: "Ajouter l’emplacement", primary_action: async () => {
            const z = dlg.fields_dict.z.$wrapper.find("[data-choix]").val();
            dlg.hide();
            if (!z || e.colonnes.includes(z)) return;
            const r = (await frappe.call({ method: SE_INV + "ajouter_emplacement", args: { name, item_code: code, zone: z }, freeze: true })).message;
            const l = d.lignes.find((x) => x.item_code === code);
            l.zones = r.zones; l.zones_ajoutees = r.zones_ajoutees; e.ajoutees = r.zones_ajoutees;
            if (e.colonnes.length === 1 && e.colonnes[0] === SANS && e.cases[SANS] == null) { e.colonnes = []; delete e.cases[SANS]; }
            e.colonnes.push(z); e.cases[z] = null;
            frappe.show_alert({ message: `📍 ${se_esc(libZone(z))} ajouté aux emplacements de ${se_esc(code)}`, indicator: "green" }, 3);
            $c.find(`.se-i-ligne[data-item="${CSS.escape(code)}"]`).replaceWith(ligne(l));
            const inp = $c.find(`.se-i-ligne[data-item="${CSS.escape(code)}"] input[data-zone="${CSS.escape(z)}"]`)[0];
            if (inp) inp.focus();
          } });
        dlg.fields_dict.z.$wrapper.html(`<div class="se-note" style="margin-bottom:8px">Choisissez l’emplacement où vous avez trouvé cet article : il est <b>aussitôt ajouté aux emplacements de l’article</b> et une case s’ajoute pour y compter. Un ✕ sur la case permet de le retirer en cas d’erreur.</div>
          <select class="form-control" style="height:40px" data-choix>${optZones().replace(/<option value="">[^<]*<\/option>/, `<option value="">— choisir —</option>`)}</select>`);
        dlg.show();
      });
      $c.find("#se-i-lignes").on("click", "[data-retirer-zone]", (ev) => {
        ev.preventDefault();
        const $l = $(ev.currentTarget).closest(".se-i-ligne"), code = $l.attr("data-item"), e = etat[code], z = $(ev.currentTarget).attr("data-retirer-zone");
        const q = e.cases[z];
        frappe.confirm(`Retirer <b>${se_esc(libZone(z))}</b> des emplacements de <b>${se_esc(code)}</b> ?${q != null && q !== "" ? `<br>La quantité ${se_esc(q)} comptée dans cette case sera effacée.` : ""}`, async () => {
          await vider();
          const r = (await frappe.call({ method: SE_INV + "retirer_emplacement", args: { name, item_code: code, zone: z }, freeze: true })).message;
          const l = d.lignes.find((x) => x.item_code === code);
          l.zones = r.zones; l.zones_ajoutees = r.zones_ajoutees; e.ajoutees = r.zones_ajoutees;
          e.colonnes = e.colonnes.filter((c) => c !== z); delete e.cases[z];
          if (!e.colonnes.length) { e.colonnes = [SANS]; e.cases[SANS] = null; }
          $c.find(`.se-i-ligne[data-item="${CSS.escape(code)}"]`).replaceWith(ligne(l));
          bilan();
        });
      });
    };
    brancher(); bilan(); peindre();
    let t = null;
    $c.find("#se-i-filtre").on("input", (ev) => { clearTimeout(t); t = setTimeout(() => { texte = (ev.target.value || "").toLowerCase().trim(); nAff = 100; peindre(); }, 250); });
    $c.find("#se-i-zone").on("change", (ev) => { zone = ev.currentTarget.value; nAff = 100; peindre(); });
    $c.find("#se-i-vues .se-chip[data-vue]").on("click", (ev) => { vue = $(ev.currentTarget).attr("data-vue"); $c.find("#se-i-vues .se-chip[data-vue]").each((_, el) => $(el).toggleClass("on", $(el).attr("data-vue") === vue)); nAff = 100; peindre(); });
    $c.find("#se-i-vues [data-tri]").on("click", (ev) => { parZone = !parZone; $(ev.currentTarget).toggleClass("on", parZone); nAff = 100; peindre(); });
    $c.find("#se-i-plus").on("click", () => { nAff += 100; peindre(); });
    $c.find("[data-retour]").on("click", async () => { await vider(); window.onbeforeunload = null; this.chargerInventaire(); });
    $c.find("[data-annuler]").on("click", () => {
      frappe.prompt({ fieldtype: "Small Text", fieldname: "motif", label: "Motif (facultatif)" }, async (v) => {
        await vider();
        await frappe.call({ method: SE_INV + "annuler_inventaire", args: { name, motif: v.motif || null }, freeze: true });
        window.onbeforeunload = null;
        frappe.show_alert({ message: "Inventaire annulé — aucun mouvement de stock", indicator: "orange" }, 4);
        this.chargerInventaire();
      }, "Annuler l’inventaire (le comptage est conservé, rien ne bouge)", "Confirmer");
    });
    $c.find("[data-cloturer]").on("click", async () => {
      await vider();
      let b = (await frappe.call({ method: SE_INV + "bilan_cloture", args: { name }, freeze: true })).message;
      const libelle = (b) => b.a_rapprocher.length ? `Passer l’écriture (${b.a_rapprocher.length} article${b.a_rapprocher.length > 1 ? "s" : ""})` : "Clôturer sans écriture";
      const dlg = new frappe.ui.Dialog({ title: "✅ Clôturer l’inventaire", size: "large", fields: [{ fieldtype: "HTML", fieldname: "b" }, { fieldtype: "Small Text", fieldname: "note", label: "Note (facultatif)" }],
        primary_action_label: libelle(b),
        primary_action: async (v) => {
          dlg.hide();
          const r = (await frappe.call({ method: SE_INV + "cloturer_inventaire", args: { name, note: v.note || null }, freeze: true, freeze_message: "Rapprochement du Magasin…" })).message;
          window.onbeforeunload = null;
          frappe.msgprint({ title: "Inventaire clôturé", indicator: r.nb_ecarts ? "orange" : "green",
            message: `${r.nb_comptes} article(s) comptés, ${r.nb_non_comptes} non comptés (inchangés), <b>${r.nb_ecarts} écart(s)</b>${r.nb_ecarts ? ` · valeur nette ${se_esc(se_argent(r.valeur_ecarts))} · manquants ${se_esc(se_argent(r.valeur_manquants))}` : ""}<br>`
              + (r.rapprochement ? `Écriture de stock : ${se_lien("Stock Reconciliation", r.rapprochement)} (${r.nb_rapproches} article(s)).` : "Aucune écriture : rien à rapprocher.")
              + (r.nb_zones_ajoutees ? `<br>📍 ${r.nb_zones_ajoutees} emplacement(s) ajouté(s) aux articles pendant l’inventaire.` : "") + (r.nb_sans_taux ? `<br>⚠️ ${r.nb_sans_taux} article(s) sans taux de valorisation : exclus, à régler sur la fiche article puis à rapprocher à la main.` : "") });
          this.chargerInventaire();
        } });
      const li = (x, txt) => `<li>${se_esc(x.item_name || x.item_code)} <span class="se-note">(${se_esc(x.item_code)})</span> ${txt}</li>`;
      // Taux de valorisation : affiché et modifiable par les responsables (ceux qui voient les valeurs). Un taux saisi
      // prime sur l'automatique et sort l'article de « sans taux » ; vide = retour à l'automatique.
      const taux = (x) => !d.voir_valeur ? "" : `<label class="se-i-taux"><span>taux</span><input type="number" inputmode="decimal" min="0" step="any" class="form-control" data-taux-item="${se_esc(x.item_code)}" value="${x.taux_saisi ? se_esc(x.taux_saisi) : ""}" placeholder="${x.taux ? se_esc(se_q(x.taux)) : "à saisir"}" title="Taux de valorisation (DT) : vide = automatique"></label>${x.taux_saisi ? `<span class="se-note">✏️ saisi</span>` : ""}`;
      const rendre = (b) => dlg.fields_dict.b.$wrapper.html(`<div class="se-resume">
        <div><b>${b.nb_comptes}</b> article(s) comptés sur ${b.nb_lignes} · <b>${b.nb_non_comptes}</b> non comptés : <b>ils ne bougent pas</b>.</div>
        <div><b class="${b.nb_ecarts ? "neg" : "pos"}">${b.nb_ecarts} écart(s)</b>${b.nb_ecarts && d.voir_valeur ? ` · valeur nette ${se_esc(se_argent(b.valeur_ecarts))} · manquants ${se_esc(se_argent(b.valeur_manquants))}` : ""}</div>
        <div>${b.a_rapprocher.length ? `Une écriture <b>Stock Reconciliation</b> sur le Magasin va rapprocher <b>${b.a_rapprocher.length}</b> article(s) :` : "Aucun article à rapprocher : la clôture ne passe pas d’écriture."}</div>
        ${b.a_rapprocher.length ? `<ul class="se-tours" style="max-height:220px;overflow:auto">${b.a_rapprocher.map((x) => li(x, `${se_q(x.qte_actuelle)} → <b>${se_q(x.qte_rapprochee)}</b> <span class="${x.ecart < 0 ? "neg" : "pos"}">(${se_signe(x.ecart)})</span>${d.voir_valeur ? ` · ${se_argent(x.valeur_ecart)}` : ""} ${taux(x)}`)).join("")}</ul>` : ""}
        ${b.bouges.length ? `<div class="se-alerte" style="margin-top:6px">⚠️ ${b.bouges.length} article(s) ont bougé entre leur comptage et maintenant : l’écart mesuré au comptage est appliqué au stock du moment (système actuel + écart).</div>
          <ul class="se-tours" style="max-height:160px;overflow:auto">${b.bouges.map((x) => li(x, `compté ${se_q(x.qte_comptee)} quand le système disait ${se_q(x.qte_systeme)} ; maintenant ${se_q(x.qte_actuelle)} → <b>${se_q(x.qte_rapprochee)}</b>`)).join("")}</ul>` : ""}
        ${b.sans_taux.length ? `<div class="se-alerte">⚠️ ${b.sans_taux.length} article(s) sans taux de valorisation : <b>exclus</b> de l’écriture${d.voir_valeur ? " — saisissez un taux pour les inclure" : " (à régler sur la fiche article)"}.</div><ul class="se-tours">${b.sans_taux.map((x) => li(x, `→ <b>${se_q(x.qte_rapprochee)}</b> ${taux(x)}`)).join("")}</ul>` : ""}
        ${b.zones_nouvelles.length ? `<div class="se-note" style="margin-top:6px">📍 ${b.zones_nouvelles.length} article(s) ont reçu un nouvel emplacement pendant cet inventaire (déjà sur la fiche article) : ${b.zones_nouvelles.slice(0, 5).map((x) => `${se_esc(x.item_code)} → ${se_esc(x.zones.join(", "))}`).join(" · ")}${b.zones_nouvelles.length > 5 ? " …" : ""}.</div>` : ""}
      </div>`);
      rendre(b);
      // Saisie d'un taux : enregistrée au changement, puis l'aperçu est recalculé (listes, valeurs, bouton).
      // Les saisies sont SÉRIALISÉES : deux « change » rapprochés feraient deux sauvegardes concurrentes de la
      // même fiche (TimestampMismatch 417 constaté sous Playwright).
      let file = Promise.resolve();
      dlg.fields_dict.b.$wrapper.on("change", "[data-taux-item]", (ev) => {
        const code = $(ev.currentTarget).attr("data-taux-item"), val = ev.currentTarget.value;
        file = file.then(async () => {
          try {
            await frappe.call({ method: SE_INV + "definir_taux", args: { name, item_code: code, taux: val === "" ? null : +val }, freeze: true });
            b = (await frappe.call({ method: SE_INV + "bilan_cloture", args: { name } })).message;
            rendre(b); dlg.get_primary_btn().text(libelle(b));
          } catch (e) { frappe.show_alert({ message: "Taux non enregistré", indicator: "red" }); }
        });
      });
      dlg.show();
    });
    window.scrollTo({ top: 0 });
  }
}
