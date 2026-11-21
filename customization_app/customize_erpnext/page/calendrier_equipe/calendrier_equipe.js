/**
 * « Calendrier d’équipe » — /app/calendrier-equipe (21/11/2026).
 *
 * Le calendrier de Frappe (vue Calendrier de la liste des tâches, FullCalendar 3) reste en place, intact. Cette page
 * montre LES MÊMES données (customization_app.calendrier_equipe.evenements, qui relit api.get_custom_tache_events)
 * avec EventCalendar (MIT, public/js/lib/event-calendar) :
 *   - vue « Équipe » : la journée, une colonne par employé ; glisser une tâche change son heure, la déposer dans une
 *     autre colonne la confie à cet employé (mêmes refus que « Gérer les interventions », côté serveur) ;
 *   - vues Semaine, Mois et Liste ; filtres par employé et par type en pastilles ;
 *   - tout ce que portait l'ancien calendrier : couleurs par employé, hachures des réservations de l'app, épingles
 *     de l'optimisation, info-bulle, réunions du suivi d'activité, jours fériés, clic = la fiche, sélection d'un
 *     créneau = nouvelle tâche, et les boutons Ma journée / Générer BL / Optimiser / Gérer les interventions /
 *     Réunion — qui ouvrent les MÊMES fenêtres (leurs portes d'entrée globales), rien n'est recopié.
 *
 * ⚠️ Les boutons d'ici n'ont PAS les id de ceux de l'ancien calendrier (btn-generer-bl, btn-ma-journee,
 * btn-optimiser-tournees) : Frappe garde les pages dans le document, et l'ancien calendrier ne pose pas son bouton
 * s'il en trouve déjà un avec son id.
 */
frappe.pages["calendrier-equipe"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: "Calendrier d’équipe", single_column: true });
  wrapper.calendrier = new CalendrierEquipe(wrapper, page);
};
frappe.pages["calendrier-equipe"].on_page_show = function (wrapper) {
  if (wrapper.calendrier) wrapper.calendrier.revenir();
};

const CE_API = "customization_app.calendrier_equipe";
const CE_LIB = "/assets/customization_app/js/lib/event-calendar/event-calendar.min";
const CE_MEMO = "calendrier_equipe_v1";
const CE_VUES = ["resourceTimeGridDay", "timeGridWeek", "dayGridMonth", "listWeek"];
const CE_ANCIEN = "/app/tache-de-travail/view/calendar/Calendrier%20de%20travail";
const ce_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const ce_fmt = (d) => moment(d).format("YYYY-MM-DD HH:mm:ss");
const ce_etroit = () => window.innerWidth < 768;

/** #rrggbb (ou #rgb) -> rgba(r, g, b, a). Fonction pure. */
function ce_rgba(hex, a) {
  let h = String(hex || "").replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (!/^[0-9a-f]{6}$/i.test(h)) return hex || "#9ca3af";
  return `rgba(${parseInt(h.slice(0, 2), 16)}, ${parseInt(h.slice(2, 4), 16)}, ${parseInt(h.slice(4, 6), 16)}, ${a})`;
}

/** La couleur mélangée au blanc : `part` de couleur (0 à 1), le reste de blanc -> #rrggbb opaque. Fonction pure. */
function ce_pastel(hex, part) {
  let h = String(hex || "").replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (!/^[0-9a-f]{6}$/i.test(h)) return hex || "#e5e7eb";
  const c = (i) => Math.round(255 + (parseInt(h.slice(i, i + 2), 16) - 255) * part).toString(16).padStart(2, "0");
  return "#" + c(0) + c(2) + c(4);
}

/** Texte sombre ou blanc selon la couleur réellement affichée (l'alpha est mélangé au blanc de la page). Pure. */
function ce_texte(couleur) {
  let r = 255, g = 255, b = 255, a = 1;
  const m = String(couleur || "").match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/);
  if (m) { r = +m[1]; g = +m[2]; b = +m[3]; a = m[4] === undefined ? 1 : +m[4]; }
  else if (/^#[0-9a-f]{6}$/i.test(couleur || "")) {
    r = parseInt(couleur.slice(1, 3), 16); g = parseInt(couleur.slice(3, 5), 16); b = parseInt(couleur.slice(5, 7), 16);
  }
  const mix = (c) => c * a + 255 * (1 - a);
  return (mix(r) * 299 + mix(g) * 587 + mix(b) * 114) / 1000 >= 128 ? "#111827" : "#ffffff";
}

/** L'épingle de l'optimisation des tournées : [signe, explication] ou null. Pure. */
function ce_epingle(p) {
  const h = cint(p.heure_fixe), e = cint(p.employe_fixe);
  if (h && e) return ["📌", "Heure et employé fixes : l’optimisation des tournées ne la bouge pas"];
  if (h) return ["🕘", "Heure fixe : l’optimisation des tournées garde l’heure (l’employé peut changer)"];
  if (e) return ["👤", "Employé fixe : l’optimisation des tournées garde l’employé (l’heure peut bouger)"];
  return null;
}

/**
 * La fiche d'information d'un événement, montrée au survol (22/11/2026, « améliore la visibilité du tip ») : les
 * mêmes rubriques que l'info-bulle de l'ancien calendrier, mais dans une carte lisible — bandeau de la couleur de la
 * tâche avec l'heure, puis une ligne par rubrique, le libellé à gauche. Rend du HTML déjà échappé. Pure.
 */
function ce_fiche(p, debut, fin, journee) {
  const RAPPORT_VIDE = "Indiquez vos remarques sur l'intervention et le client:";
  const texte = (v) => ce_esc(v).replace(/\n/g, "<br>");
  const annulee = p.statut === "Cancelled", terminee = p.statut === "Completed";
  const plein = p.genre === "ferie" ? "#6b7280" : annulee ? "#9ca3af" : (p.couleur || "#FF6347");
  const lignes = String(p.titre || "").split("\n").filter((l) => l.trim());
  let quand = "";
  if (debut) {
    // le jour en français quelle que soit la langue du compte
    const d = moment(debut), f = fin ? moment(fin) : null;
    const jour = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"][d.day()] + " " + d.format("DD/MM");
    quand = journee ? jour + " · toute la journée" : jour + " · " + d.format("HH:mm") + (f ? " – " + f.format("HH:mm") : "");
  }
  const etat = p.genre === "ferie" ? "Jour férié" : annulee ? "Annulée" : terminee ? "Terminée" : p.genre === "reunion" ? "Réunion" : "";
  const rubriques = [];
  const rubrique = (icone, libelle, valeur, fort) => {
    if (valeur == null || String(valeur).trim() === "") return;
    rubriques.push(`<div class="ce-f-l"><span class="ce-f-n">${icone} ${ce_esc(libelle)}</span><span class="ce-f-v${fort ? " fort" : ""}">${texte(valeur)}</span></div>`);
  };
  if (p.genre !== "ferie") {
    rubrique("👤", "Client", p.nom_client, true);
    rubrique("📞", "Téléphone", p.tel, true);
    rubrique("🛠️", "Type", p.custom_type_dintervention);
    rubrique("👷", p.genre === "reunion" ? "Participants" : "Employé", p["custom_employé"]);
    rubrique("📍", "Adresse", p.details_adresse);
    rubrique("🧭", "Secteur", [p.secteur, p.info_secteur].filter(Boolean).join(" — "));
    rubrique("📝", "Sujet", p.subject);
    rubrique("⛔", "Annulation", p.raison_annulation);
    if (p.rapport_visite && p.rapport_visite.trim() !== RAPPORT_VIDE) rubrique("📋", "Rapport", p.rapport_visite);
    const ep = p.genre === "tache" ? ce_epingle(p) : null;
    if (ep) rubrique(ep[0], "Tournée", ep[1]);
    if (cint(p.reservation_app) === 1) rubrique("📱", "Origine", "Rendez-vous pris par le client dans l’app");
  }
  return `<div class="ce-f-tete" style="background:${ce_esc(plein)};color:${ce_texte(plein)}">
      <div class="ce-f-quand">${ce_esc(quand)}${etat ? `<span class="ce-f-etat">${ce_esc(etat)}</span>` : ""}</div>
      <div class="ce-f-titre">${ce_esc(lignes[0] || "")}</div>
      ${lignes.length > 1 ? `<div class="ce-f-sous">${lignes.slice(1).map(ce_esc).join("<br>")}</div>` : ""}
    </div>${rubriques.length ? `<div class="ce-f-corps">${rubriques.join("")}</div>` : ""}`;
}

/**
 * Un événement du serveur sous la forme d'EventCalendar. Pure.
 * `colonnes` : les id des colonnes affichées (un jour férié s'étend sur toutes) ; `ecrire` : droit de modifier.
 */
function ce_evenement(e, colonnes, ecrire) {
  const out = { id: e.id, start: e.debut, end: e.fin, allDay: !!e.journee, title: e.titre || "", extendedProps: e,
                resourceIds: e.employes || [], editable: false };
  if (e.genre === "fond") {
    return Object.assign(out, { display: "background", resourceIds: colonnes, backgroundColor: "rgba(156, 163, 175, 0.25)" });
  }
  if (e.genre === "ferie") {
    return Object.assign(out, { resourceIds: colonnes, backgroundColor: "#e5e7eb", textColor: "#374151",
      styles: ["--ce-plein:#9ca3af", "border:1px dashed #9ca3af", "font-weight:700", "cursor:default"], classNames: ["ce-e"] });
  }
  // Coloriage (21/11/2026, « améliore le coloriage ») : la couleur reste celle de l'ancien calendrier — l'employé,
  // ou le statut pour une tâche terminée / annulée — mais posée autrement : un fond pastel OPAQUE (le texte se lit,
  // deux tâches qui se touchent ne se mélangent plus) et une barre à gauche dans la couleur pleine.
  const annulee = e.statut === "Cancelled", terminee = e.statut === "Completed";
  const plein = annulee ? "#9ca3af" : (e.couleur || "#FF6347");
  const fond = annulee ? "#f3f4f6" : ce_pastel(plein, terminee ? 0.30 : 0.24);
  const styles = [`--ce-plein:${plein}`, `border:1px solid ${ce_rgba(plein, 0.45)}`, `border-left:5px solid ${plein}`];
  if (cint(e.reservation_app) === 1) {
    // Rendez-vous pris par le client dans l'app : les hachures de l'ancien calendrier, mais en BANDE à droite —
    // posées sur toute la tâche, elles passaient sous le texte des entretiens et le brouillaient (22/11/2026).
    styles.push("background-image:repeating-linear-gradient(45deg, rgba(0,0,0,.28) 0, rgba(0,0,0,.28) 2px, transparent 2px, transparent 6px)",
      "background-size:9px 100%", "background-position:right top", "background-repeat:no-repeat", "padding-right:11px");
  }
  const classes = ["ce-e"];
  if (annulee) classes.push("ce-annulee");
  if (terminee) classes.push("ce-terminee");
  Object.assign(out, { backgroundColor: fond, textColor: annulee ? "#6b7280" : ce_texte(fond), styles, classNames: classes });
  if (e.genre === "reunion") {
    // une réunion prévue se glisse sur un autre jour ou une autre heure ; sa durée et ses participants, non
    out.startEditable = !!ecrire && e.statut !== "Completed";
    out.durationEditable = false;
  } else {
    out.startEditable = !!ecrire;
    out.durationEditable = !!ecrire && !e.journee;
  }
  return out;
}

class CalendrierEquipe {
  constructor(wrapper, page) {
    this.page = page;
    this.ec = null;
    this.cache = null;              // {cle, evenements} : la dernière période lue
    this.colonnes = [];             // toutes les colonnes possibles pour la période
    this.periode = null;
    const memo = this.lire_memo();
    this.filtres = { employes: new Set(memo.employes || []), types: new Set(memo.types || []), annulees: memo.annulees !== false };
    this.vue = CE_VUES.includes(memo.vue) ? memo.vue : (ce_etroit() ? "listWeek" : "resourceTimeGridDay");
    this.poser_css();
    this.$root = $(`<div class="ce-page"><div class="ce-actions"></div><div class="ce-filtres"></div>
      <div class="ce-cal"><div class="text-muted" style="padding:24px">Chargement du calendrier…</div></div></div>`)
      .appendTo($(wrapper).find(".layout-main-section"));
    frappe.require([CE_LIB + ".css", CE_LIB + ".js"], () => {
      frappe.xcall(CE_API + ".contexte").then((ctx) => { this.ctx = ctx; this.monter(); });
    });
  }

  lire_memo() {
    try { return JSON.parse(localStorage.getItem(CE_MEMO) || "{}") || {}; } catch (e) { return {}; }
  }

  sauver_memo() {
    try {
      localStorage.setItem(CE_MEMO, JSON.stringify({ vue: this.vue, employes: [...this.filtres.employes],
        types: [...this.filtres.types], annulees: this.filtres.annulees }));
    } catch (e) { /* stockage refusé : la page marche sans */ }
  }

  poser_css() {
    if (document.getElementById("ce-style")) return;
    const s = document.createElement("style");
    s.id = "ce-style";
    s.textContent = `
      .ce-page { padding: 8px 4px 0; }
      .ce-actions, .ce-filtres { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 8px; }
      .ce-btn { border: none; border-radius: 6px; padding: 6px 12px; font-weight: 700; font-size: 13px; cursor: pointer;
                color: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.2); white-space: nowrap; }
      .ce-btn.clair { background: var(--control-bg, #f3f4f6); color: var(--text-color, #1f2937); box-shadow: none;
                      border: 1px solid var(--border-color, #d1d5db); font-weight: 600; }
      .ce-lien { margin-left: auto; font-size: 12px; }
      .ce-puce { border: 1px solid var(--border-color, #d1d5db); background: var(--card-bg, #fff); border-radius: 999px;
                 padding: 3px 10px; font-size: 12px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
                 color: var(--text-color, #1f2937); }
      .ce-puce.on { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
      .ce-puce i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
      .ce-sep { width: 1px; height: 20px; background: var(--border-color, #d1d5db); margin: 0 4px; }
      .ce-titre-filtre { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted, #6b7280); }
      .ce-cal { height: calc(100vh - 250px); min-height: 460px; }
      .ce-cal .ec { height: 100%; font-size: 12px; }
      .ce-cal .ec-toolbar { flex-wrap: wrap; gap: 6px; }
      .ce-cal .ec-event { border-radius: 6px; cursor: pointer; box-shadow: 0 1px 2px rgba(15,23,42,.10); }
      .ce-cal .ec-event:hover { box-shadow: 0 2px 6px rgba(15,23,42,.22); filter: brightness(.97); }
      .ce-ev { font-size: 10.5px; line-height: 1.2; overflow: hidden; overflow-wrap: anywhere; }
      .ce-cal .ec-title { font-size: 20px; }
      .ce-ev-h { font-weight: 600; opacity: .8; white-space: nowrap; }
      .ce-ev-1 { font-weight: 700; }
      .ce-ev-mois { white-space: nowrap; text-overflow: ellipsis; font-weight: 500; }
      .ce-ev-2 { font-weight: 600; }
      .ce-annulee .ce-ev-1, .ce-annulee .ce-ev-2 { text-decoration: line-through; }
      .ce-annulee { opacity: .85; }
      /* vue Liste : pas de cadre par ligne, la pastille porte la couleur pleine */
      .ce-cal .ec-list .ec-event { border: none !important; border-bottom: 1px solid var(--border-color, #e5e7eb) !important;
                                   border-radius: 0; box-shadow: none; background-image: none !important; }
      .ce-cal .ec-list .ec-event-tag { background-color: var(--ce-plein, #9ca3af) !important; }
      /* la fiche d'information au survol */
      .ce-fiche { position: fixed; z-index: 1040; display: none; width: 340px; max-width: calc(100vw - 16px); pointer-events: none;
                  background: var(--card-bg, #fff); color: var(--text-color, #1f2937); border-radius: 10px; overflow: hidden;
                  border: 1px solid var(--border-color, #d1d5db); box-shadow: 0 10px 30px rgba(15,23,42,.28); font-size: 13px; line-height: 1.4; }
      .ce-f-tete { padding: 9px 12px 10px; }
      .ce-f-quand { font-size: 12px; font-weight: 600; opacity: .9; display: flex; justify-content: space-between; gap: 8px; }
      .ce-f-etat { background: rgba(255,255,255,.85); color: #111827; border-radius: 999px; padding: 0 8px; font-size: 11px; font-weight: 700; }
      .ce-f-titre { font-size: 15px; font-weight: 800; margin-top: 2px; }
      .ce-f-sous { font-size: 12.5px; font-weight: 500; margin-top: 1px; }
      .ce-f-corps { padding: 6px 12px 10px; max-height: 60vh; overflow: hidden; }
      .ce-f-l { display: flex; gap: 10px; padding: 4px 0; border-bottom: 1px solid var(--border-color, #eef0f3); }
      .ce-f-l:last-child { border-bottom: none; }
      .ce-f-n { flex: 0 0 104px; color: var(--text-muted, #6b7280); font-size: 12px; white-space: nowrap; }
      .ce-f-v { flex: 1; min-width: 0; word-break: break-word; }
      .ce-f-v.fort { font-weight: 700; font-size: 14px; }
      .ce-legende { display: inline-flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 11px;
                    color: var(--text-muted, #6b7280); margin-left: auto; }
      .ce-legende span { display: inline-flex; align-items: center; gap: 4px; }
      .ce-legende i { width: 16px; height: 11px; border-radius: 3px; display: inline-block; border: 1px solid rgba(0,0,0,.18); }
      .ce-col { display: inline-flex; align-items: center; gap: 6px; font-weight: 700; }
      .ce-col i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
      .ce-col small { font-weight: 400; color: var(--text-muted, #6b7280); }
      @media (max-width: 767px) {
        .ce-cal { height: 78vh; }
        .ce-cal .ec-title { font-size: 16px; }
        .ce-actions { gap: 5px; }
        .ce-btn { padding: 8px 10px; }
        .ce-lien { margin-left: 0; }
      }`;
    document.head.appendChild(s);
  }

  // ── Boutons : les mêmes fenêtres que l'ancien calendrier ───────────────────
  poser_actions() {
    const d = this.ctx.droits, $a = this.$root.find(".ce-actions").empty();
    const bouton = (libelle, couleur, titre, clic) => $(`<button class="ce-btn" type="button"></button>`)
      .text(libelle).attr("title", titre).css("background", couleur).on("click", clic).appendTo($a);
    if (d.creer) bouton("➕ Nouvelle tâche", "#1d4ed8", "Créer une tâche de travail", () => frappe.new_doc("Tache de travail"));
    if (window.maJournee_ouvrir) {
      bouton("☀️ Ma journée", "#2490ef", "Mes interventions du jour affiché",
        () => window.maJournee_ouvrir(this.vue === "resourceTimeGridDay" && this.periode ? this.periode.from : undefined));
    }
    if (d.generer_bl && typeof _open_generer_bl_dialog === "function") {
      bouton("🚚 Générer BL", "#dc2626", "Générer et imprimer les bons de livraison du jour", () => _open_generer_bl_dialog());
    }
    if (d.optimiser && window.optimiserTournees_ouvrir) {
      bouton("🗺️ Optimiser", "#059669", "Redistribuer et réordonner les tâches du jour entre les employés présents pour rouler moins",
        () => window.optimiserTournees_ouvrir());
    }
    if (d.ecrire && window.customization_app && customization_app.gestion_taches) {
      bouton("🗓️ Gérer les interventions", "#0ea5e9", "Décaler, transférer ou annuler plusieurs interventions, en prévenant les clients",
        () => customization_app.gestion_taches.ouvrir(null, this.periode));
    }
    if (window.customization_app && customization_app.reunion) {
      bouton("👥 Réunion", "#7c3aed", "Créer une réunion du suivi d’activité, avec son lien visio",
        () => customization_app.reunion.ouvrir(() => this.recharger()));
    }
    $(`<button class="ce-btn clair" type="button" title="Relire les tâches">↻</button>`).on("click", () => this.recharger()).appendTo($a);
    $(`<a class="ce-lien text-muted" href="${CE_ANCIEN}">Ancien calendrier ↗</a>`).appendTo($a);
  }

  // ── Filtres en pastilles : rien d'allumé = tout ────────────────────────────
  poser_filtres() {
    const $f = this.$root.find(".ce-filtres").empty(), f = this.filtres;
    // Sur téléphone, les pastilles prendraient la moitié de l'écran : repliées derrière un bouton.
    if (ce_etroit()) {
      const n = f.employes.size + f.types.size + (f.annulees ? 0 : 1);
      $(`<button class="ce-puce${n ? " on" : ""}" type="button"></button>`)
        .text((this.filtres_ouverts ? "▾ " : "▸ ") + "Filtres" + (n ? ` (${n})` : ""))
        .on("click", () => { this.filtres_ouverts = !this.filtres_ouverts; this.poser_filtres(); }).appendTo($f);
      if (!this.filtres_ouverts) return;
    }
    const puce = (libelle, actif, clic, couleur) => {
      const $p = $(`<button class="ce-puce${actif ? " on" : ""}" type="button"></button>`).on("click", clic).appendTo($f);
      if (couleur) $p.append($("<i></i>").css("background", couleur));
      $p.append(document.createTextNode(libelle));
    };
    const basculer = (ensemble, valeur) => () => {
      if (ensemble.has(valeur)) ensemble.delete(valeur); else ensemble.add(valeur);
      this.sauver_memo(); this.poser_filtres(); this.appliquer_filtres();
    };
    $f.append(`<span class="ce-titre-filtre">Employés</span>`);
    this.colonnes.forEach((c) => puce(c.nom + (c.actif || c.id === this.ctx.sans_employe ? "" : " (parti)"),
      f.employes.has(c.id), basculer(f.employes, c.id), c.couleur));
    $f.append(`<span class="ce-sep"></span><span class="ce-titre-filtre">Types</span>`);
    (this.ctx.types || []).forEach((t) => puce(t, f.types.has(t), basculer(f.types, t)));
    $f.append(`<span class="ce-sep"></span>`);
    puce(f.annulees ? "Annulées visibles" : "Annulées masquées", !f.annulees, () => {
      f.annulees = !f.annulees; this.sauver_memo(); this.poser_filtres(); this.appliquer_filtres();
    });
    if (f.employes.size || f.types.size) {
      $(`<a class="text-muted" style="font-size:12px;cursor:pointer">Tout afficher</a>`).on("click", () => {
        f.employes.clear(); f.types.clear(); this.sauver_memo(); this.poser_filtres(); this.appliquer_filtres();
      }).appendTo($f);
    }
    $f.append(`<span class="ce-legende">
      <span><i style="background:${ce_pastel("#32CD32", 0.30)};border-left:4px solid #32CD32"></i>Terminée</span>
      <span><i style="background:#f3f4f6;border-left:4px solid #9ca3af"></i>Annulée</span>
      <span><i style="background:#e5e7eb;background-image:repeating-linear-gradient(45deg, rgba(0,0,0,.3) 0, rgba(0,0,0,.3) 2px, transparent 2px, transparent 5px);background-size:6px 100%;background-position:right;background-repeat:no-repeat"></i>Bande hachurée : prise par le client dans l’app</span>
      <span>📌 🕘 👤 fixe pour l’optimisation</span></span>`);
  }

  /** Les colonnes affichées : celles des employés cochés, sinon toutes. */
  colonnes_affichees() {
    const f = this.filtres.employes;
    const choisies = this.colonnes.filter((c) => f.has(c.id));
    return choisies.length ? choisies : this.colonnes;
  }

  ressources() {
    return this.colonnes_affichees().map((c) => ({ id: c.id, eventBackgroundColor: c.couleur,
      title: { html: `<span class="ce-col"><i style="background:${ce_esc(c.couleur)}"></i>${ce_esc(c.nom)}${
        c.actif || c.id === this.ctx.sans_employe ? "" : " <small>(parti)</small>"}</span>` },
      extendedProps: { nom: c.nom } }));
  }

  /** L'événement passe-t-il les filtres ? Les jours fériés passent toujours. */
  visible(e) {
    const f = this.filtres;
    if (e.genre === "ferie" || e.genre === "fond") return true;
    if (!f.annulees && e.statut === "Cancelled") return false;
    if (f.types.size && !f.types.has(e.custom_type_dintervention)) return false;
    if (f.employes.size) {
      // des employés cochés qui n'ont plus de colonne dans cette période ne masquent pas tout
      const cochees = this.colonnes.filter((c) => f.employes.has(c.id)).map((c) => c.id);
      if (cochees.length && !(e.employes || []).some((x) => cochees.includes(x))) return false;
    }
    return true;
  }

  appliquer_filtres() {
    if (!this.ec) return;
    this.ec.setOption("resources", this.ressources());
    this.ec.refetchEvents();          // servi par le cache : pas d'aller-retour serveur
  }

  // ── Le calendrier ──────────────────────────────────────────────────────────
  monter() {
    this.poser_actions();
    this.colonnes = (this.ctx.employes || []).map((e) => Object.assign({ actif: 1 }, e));
    this.poser_filtres();
    const d = this.ctx.droits, etroit = ce_etroit();
    const $cal = this.$root.find(".ce-cal").empty();
    const textes = (suivant, precedent) => (t) => Object.assign({}, t, { today: "Aujourd’hui", resourceTimeGridDay: "Équipe",
      timeGridWeek: "Semaine", dayGridMonth: "Mois", listWeek: "Liste", next: suivant, prev: precedent, close: "Fermer" });
    this.ec = EventCalendar.create($cal[0], {
      view: this.vue,
      locale: "fr",
      firstDay: 1,
      height: "100%",
      headerToolbar: { start: "prev,next today", center: "title", end: CE_VUES.join(",") },
      buttonText: textes("Suivant", "Précédent"),
      views: {
        resourceTimeGridDay: { buttonText: textes("Jour suivant", "Jour précédent"), columnWidth: etroit ? "170px" : undefined },
        timeGridWeek: { buttonText: textes("Semaine suivante", "Semaine précédente"), columnWidth: etroit ? "130px" : undefined },
        dayGridMonth: { buttonText: textes("Mois suivant", "Mois précédent") },
        listWeek: { buttonText: textes("Semaine suivante", "Semaine précédente") },
      },
      slotMinTime: "07:00:00",
      slotMaxTime: "20:00:00",
      flexibleSlotTimeLimits: true,          // une tâche à 6 h ou à 21 h élargit la grille d'elle-même
      scrollTime: "07:30:00",
      slotDuration: "00:30:00",
      slotHeight: 50,                        // une demi-heure laisse lire l'heure + secteur, puis le client sur deux lignes
      snapDuration: "00:15:00",              // on glisse, on étire et on sélectionne par quart d'heure
      slotEventOverlap: false,               // deux tâches à la même heure : côte à côte, pas l'une sur l'autre
      allDayContent: "Journée",
      slotLabelFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
      eventTimeFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
      nowIndicator: true,
      dayMaxEvents: true,
      pointer: true,
      lazyFetching: false,
      editable: !!d.ecrire,
      selectable: !!d.creer,
      longPressDelay: 500,
      noEventsContent: "Aucune tâche sur cette période",
      moreLinkContent: (info) => "+ " + info.num + " autres",
      resources: this.ressources(),
      eventSources: [{ events: (info) => this.lire(info) }],
      eventContent: (info) => this.contenu(info),
      eventMouseEnter: (info) => this.montrer_fiche(info),
      eventMouseLeave: () => this.cacher_fiche(),
      eventDragStart: () => this.cacher_fiche(),
      eventResizeStart: () => this.cacher_fiche(),
      eventClick: (info) => { this.cacher_fiche(); this.ouvrir(info.event); },
      eventDrop: (info) => this.deposer(info, false),
      eventResize: (info) => this.deposer(info, true),
      select: (info) => this.nouvelle(info),
      dateClick: (info) => {
        // en vue Mois, un clic sur un jour ouvre ce jour en vue Équipe
        if (info.view.type === "dayGridMonth") {
          this.ec.setOption("view", "resourceTimeGridDay");
          this.ec.setOption("date", info.date);
          return;
        }
        // Dans la grille horaire, un simple clic sur un créneau vide crée la tâche, comme l'ancien calendrier :
        // une demi-heure à partir du quart d'heure cliqué (la durée du type d'intervention la remplace à la saisie).
        if (!d.creer || String(info.view.type).startsWith("list")) return;
        const fin = info.allDay ? moment(info.date).add(1, "day") : moment(info.date).add(30, "minutes");
        this.nouvelle({ start: info.date, end: fin.toDate(), allDay: info.allDay, resource: info.resource, view: info.view });
      },
      datesSet: (info) => {
        this.cacher_fiche();
        this.vue = info.view.type;
        const fin = moment(info.view.currentEnd).subtract(1, "day");
        this.periode = { from: moment(info.view.currentStart).format("YYYY-MM-DD"), to: fin.format("YYYY-MM-DD") };
        this.sauver_memo();
      },
    });

    // Une fenêtre se ferme (Ma journée, Gérer les interventions, Optimiser, Générer BL…) : on relit.
    $(document).on("hidden.bs.modal.calendrier_equipe", frappe.utils.debounce(() => { if (this.actif()) this.recharger(); }, 400));
    document.addEventListener("visibilitychange", () => { if (!document.hidden && this.actif()) this.recharger(); });
    // Le bilan des messages envoyés par l'optimisation : l'ancien calendrier l'annonce quand son bouton existe.
    frappe.realtime.on("envoi_taches_termine", (m) => {
      if (!m || !this.actif() || document.getElementById("btn-optimiser-tournees")) return;
      frappe.show_alert({ message: `📨 Messages clients : ${m.sms_envoyes} SMS, ${m.emails_envoyes} e-mail(s)${
        m.echecs ? `, ${m.echecs} échec(s)` : ""}${m.simulation ? " (simulés en dev)" : ""} — détail en commentaire sur chaque tâche`,
        indicator: m.echecs ? "orange" : "green" }, 10);
    });
  }

  actif() {
    return (frappe.get_route() || [])[0] === "calendrier-equipe";
  }

  /** La fiche d'information au survol, posée à côté de la tâche (à droite, sinon à gauche), jamais hors de l'écran. */
  montrer_fiche(info) {
    const p = info.event.extendedProps || {};
    if (p.genre === "fond" || !info.el) return;
    clearTimeout(this._fiche_attente);
    this._fiche_attente = setTimeout(() => {
      if (!document.body.contains(info.el)) return;
      let $f = $("#ce-fiche");
      if (!$f.length) $f = $(`<div id="ce-fiche" class="ce-fiche"></div>`).appendTo(document.body);
      // un jour férié, une réunion : heures de l'événement ; une journée entière : le jour seul
      const fin = info.event.allDay ? null : info.event.end;
      $f.html(ce_fiche(p, info.event.start, fin, info.event.allDay)).css({ left: 0, top: 0, display: "block" });
      const r = info.el.getBoundingClientRect(), l = $f.outerWidth(), h = $f.outerHeight(), marge = 8;
      let x = r.right + marge;
      if (x + l > window.innerWidth - marge) x = r.left - l - marge;
      if (x < marge) x = Math.max(marge, window.innerWidth - l - marge);
      const y = Math.max(marge, Math.min(r.top, window.innerHeight - h - marge));
      $f.css({ left: x + "px", top: y + "px" });
    }, 180);
  }

  cacher_fiche() {
    clearTimeout(this._fiche_attente);
    $("#ce-fiche").hide();
  }

  revenir() {
    if (this.ec) this.recharger();
  }

  recharger() {
    this.cache = null;
    if (this.ec) this.ec.refetchEvents();
  }

  lire(info) {
    const cle = ce_fmt(info.start) + "|" + ce_fmt(info.end);
    const rendre = () => {
      const ids = this.colonnes_affichees().map((c) => c.id);
      const out = [];
      this.cache.evenements.filter((e) => this.visible(e)).forEach((e) => {
        const ev = ce_evenement(e, ids, this.ctx.droits.ecrire);
        out.push(ev);
        // jour férié : la vue Mois ne montre un fond que s'il est « toute la journée » — on donne les deux
        if (e.genre === "fond") out.push(Object.assign({}, ev, { id: ev.id + "|jour", allDay: true }));
      });
      return out;
    };
    if (this.cache && this.cache.cle === cle) return Promise.resolve(rendre());
    return frappe.xcall(CE_API + ".evenements", { start: ce_fmt(info.start), end: ce_fmt(info.end) }).then((r) => {
      r = r || {};
      this.cache = { cle, evenements: r.evenements || [] };
      const avant = JSON.stringify(this.colonnes.map((c) => c.id));
      this.colonnes = r.colonnes || [];
      if (JSON.stringify(this.colonnes.map((c) => c.id)) !== avant) {
        this.poser_filtres();
        this.ec.setOption("resources", this.ressources());
      }
      return rendre();
    });
  }

  /**
   * Ce qui s'écrit sur la tuile : LE TITRE DE LA TÂCHE, tel que l'ancien calendrier (« Secteur 6 / 🔧 Entretien:
   * Client: … / employé ») — demande du 22/11/2026 : « garde le nom de la tâche comme avant mais fais en sorte qu'il
   * apparaisse ». Il était coupé sur une intervention d'une demi-heure : l'heure prenait une ligne à elle seule et
   * la ligne du client débordait. Maintenant l'heure et la première ligne du titre partagent une ligne, le reste
   * passe à la ligne au lieu d'être rogné, et les créneaux sont un peu plus hauts (slotHeight).
   */
  contenu(info) {
    const p = info.event.extendedProps || {};
    if (p.genre === "fond") return { html: "" };
    const ep = p.genre === "tache" ? ce_epingle(p) : null;
    const epingle = ep ? `<span title="${ce_esc(ep[1])}">${ep[0]} </span>` : "";
    const lignes = String(p.titre || "").split("\n").map((l) => l.trim()).filter(Boolean).map(ce_esc);
    if (info.view.type === "dayGridMonth") {
      // vue Mois : une seule ligne par tâche, pour en voir plusieurs dans la case du jour
      const debut = info.event.allDay ? "" : `<b>${ce_esc(moment(info.event.start).format("HH:mm"))}</b> `;
      return { html: `<div class="ce-ev ce-ev-mois">${debut}${epingle}${lignes.join(" · ")}</div>` };
    }
    const heure = !info.event.allDay && info.timeText ? `<span class="ce-ev-h">${ce_esc(info.timeText)}</span> · ` : "";
    return { html: `<div class="ce-ev"><div class="ce-ev-1">${heure}${epingle}${lignes[0] || ""}</div>${
      lignes.length > 1 ? `<div class="ce-ev-2">${lignes.slice(1).join("<br>")}</div>` : ""}</div>` };
  }

  ouvrir(ev) {
    const p = ev.extendedProps || {};
    if (p.genre === "ferie" || p.genre === "fond") return;
    if (p.genre === "reunion") {
      frappe.route_options = { reunion: p.name };
      frappe.set_route("suivi-activite");
      return;
    }
    if (frappe.model.can_read(p.doctype)) frappe.set_route("Form", p.doctype, p.name);
  }

  /** Un créneau sélectionné à la souris : nouvelle tâche à ces heures — et pour cet employé en vue Équipe. */
  nouvelle(info) {
    const unJour = info.end - info.start <= 864e5;
    if (info.view.type === "dayGridMonth" && unJour) { this.ec.unselect(); return; }
    const doc = frappe.model.get_new_doc("Tache de travail");
    doc.starts_on = ce_fmt(info.start);
    doc.ends_on = ce_fmt(info.allDay ? moment(info.end).subtract(1, "second") : info.end);
    if (info.resource && info.resource.id !== this.ctx.sans_employe) doc.custom_choix_du_staff = info.resource.id;
    this.ec.unselect();
    frappe.set_route("Form", "Tache de travail", doc.name);
  }

  /** Une tâche glissée (ou étirée) : le serveur décide, on revient en arrière s'il refuse. */
  deposer(info, etiree) {
    const ev = info.event, p = ev.extendedProps || {}, annuler = () => info.revert();
    const dire = (message) => frappe.show_alert({ message, indicator: "orange" }, 6);
    if (p.genre === "reunion") {
      if (etiree || info.newResource) { dire("Une réunion change de jour ou d’heure ici ; ses participants et sa durée se règlent dans sa page."); return annuler(); }
      if (p.statut === "Completed") { dire("Une réunion tenue ne se déplace plus."); return annuler(); }
      const debut = moment(ev.start);
      frappe.xcall("customization_app.reunion_activite.deplacer",
        { name: p.name, date: debut.format("YYYY-MM-DD"), heure: ev.allDay ? null : debut.format("HH:mm:ss") })
        .then((r) => {
          frappe.show_alert({ message: `Réunion déplacée au ${debut.format("DD/MM")} à ${((r || {}).heure || "").slice(0, 5)}`, indicator: "green" }, 5);
          this.recharger();
        }).catch(annuler);
      return;
    }
    if (p.genre !== "tache") return annuler();
    if (ev.allDay !== info.oldEvent.allDay) {
      dire("Pour passer une tâche en « toute la journée » (ou l’inverse), ouvrez sa fiche.");
      return annuler();
    }
    const args = { name: p.name };
    if (ev.allDay) {
      if (etiree) { dire("La durée d’une tâche « toute la journée » se règle dans sa fiche."); return annuler(); }
      args.jours = Math.round((ev.start - info.oldEvent.start) / 864e5);
    } else {
      args.debut = ce_fmt(ev.start);
      args.fin = ce_fmt(ev.end);
    }
    const envoyer = () => frappe.xcall(CE_API + ".deplacer", args).then((r) => {
      frappe.show_alert({ message: (r || {}).transferee ? "Tâche confiée et enregistrée" : "Tâche déplacée", indicator: "green" }, 4);
      this.recharger();
    }).catch(annuler);
    const vers = info.newResource;
    if (!vers) return envoyer();
    if (vers.id === this.ctx.sans_employe) { dire("Déposez la tâche dans la colonne d’un employé."); return annuler(); }
    args.employe = vers.id;
    const nom = (vers.extendedProps || {}).nom || vers.id;
    const titre = String(p.titre || p.name).split("\n")[0];
    frappe.confirm(`Confier « ${ce_esc(titre)} » à <b>${ce_esc(nom)}</b> ?`, envoyer, annuler);
  }
}
