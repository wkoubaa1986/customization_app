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
const CE_VUES = ["resourceTimeGridDay", "timeGridDay", "timeGridWeek", "dayGridMonth", "listWeek"];
const CE_ANCIEN = "/app/tache-de-travail/view/calendar/Calendrier%20de%20travail";
const ce_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const ce_fmt = (d) => moment(d).format("YYYY-MM-DD HH:mm:ss");
const ce_etroit = () => window.innerWidth < 768;
// MODE CRÉNEAU (22/11/2026 : « les boutons de prendre rdv doivent utiliser le nouveau calendrier aussi »). La prise de
// rendez-vous (commande client, compte partenaire, liste d'appels) ouvre le calendrier dans un cadre plein écran pour
// y CHOISIR un créneau ; la page du dessus ouvre ensuite son propre dialogue de rendez-vous. Avec l'ancien calendrier,
// ces fenêtres bricolaient FullCalendar de l'extérieur. Ici la page le fait elle-même quand son cadre porte l'un de
// ces noms : elle envoie le créneau choisi au-dessus (message « rdv_new_doc », le même qu'avant) et ne propose ni
// boutons d'action ni fenêtre de tâche. « -appels » : la liste d'appels, qui attend toujours une heure de fin et a un
// mode « modifier » (la tâche à déplacer, notée dans le localStorage par la liste).
const CE_CADRES_CRENEAU = ["calendrier-equipe-creneau", "calendrier-equipe-creneau-appels"];
const ce_mode_creneau = () => window.parent !== window && CE_CADRES_CRENEAU.includes(window.name);
const ce_tache_a_deplacer = () => { try { return localStorage.getItem("rdv_edit_task") || ""; } catch (e) { return ""; } };
// Fuseau : la base garde les heures dans le fuseau du SYSTÈME, Frappe les montre dans celui de l'UTILISATEUR (fiche,
// ancien calendrier). On fait de même, pour que le calendrier et la fiche disent la même heure à un compte réglé sur
// un autre fuseau (Administrator). Pour tous les comptes de l'entreprise les deux fuseaux sont égaux : rien ne bouge.
const ce_decale = () => { const z = frappe.boot.time_zone || {}; return !!(z.system && z.user && z.system !== z.user); };
const ce_vers_utilisateur = (texte) => (texte && ce_decale() ? frappe.datetime.convert_to_user_tz(texte, true) : texte);
const ce_vers_systeme = (d) => (ce_decale() ? frappe.datetime.convert_to_system_tz(ce_fmt(d), true) : ce_fmt(d));

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

/**
 * Les lignes du titre d'une tâche, sans le mot « Client: » : « 🔧 Entretien: Client: X » -> « 🔧 Entretien: X »,
 * « ☕ Autre: Client: » (sans client) -> « ☕ Autre » (22/11/2026). Le titre enregistré, lui, ne change pas. Pure.
 */
function ce_titre(titre) {
  return String(titre || "").split("\n").map((l) => l.replace(/:\s*Client:\s*/, ": ").replace(/:\s*$/, "").trim()).filter(Boolean);
}

/**
 * Le signe de l'optimisation des tournées : [signe, explication, classe, libellé court] ou null. Pure.
 * 🕘 heure fixe, 👤 employé fixe, 📌 les deux.
 */
function ce_epingle(p) {
  const h = cint(p.heure_fixe), e = cint(p.employe_fixe);
  if (h && e) return ["📌", "Heure et employé fixes : l’optimisation des tournées ne la bouge pas", "t", "Heure et employé fixes"];
  if (h) return ["🕘", "Heure fixe : l’optimisation des tournées garde l’heure (l’employé peut changer)", "h", "Heure fixe"];
  if (e) return ["👤", "Employé fixe : l’optimisation des tournées garde l’employé (l’heure peut bouger)", "e", "Employé fixe"];
  return null;
}

/**
 * Le signe dans une pastille blanche cerclée (22/11/2026 : « le sigle d'employé fixe ou temps fixe n'apparaît pas,
 * il n'y a que lorsque les deux sont fixes ») : 🕘 et 👤 sont de petits pictogrammes sombres, illisibles posés à même
 * le fond coloré d'une tuile — seule l'épingle rouge se voyait. `coin` : posée en haut à droite de la tuile. Pure.
 */
function ce_pastille(ep) {
  return ep ? `<span class="ce-fixe ${ep[2]}" title="${ce_esc(ep[1])}">${ep[0]}</span>` : "";
}

/** Le signe d'une livraison confiée à Aramex (22/11/2026 : « ajoute un tampon aussi pour les livraisons Aramex »). */
const CE_ARAMEX = ["📦", "Livraison Aramex : le colis part par transporteur", "a", "Livraison Aramex"];

/**
 * Machine reprise à l'atelier (23/11/2026) : le technicien n'a pas pu finir et est reparti avec la machine. La tâche
 * est fermée, mais ce n'est pas une annulation ordinaire : orange, pas barrée, avec l'état du dossier de réparation.
 */
const CE_REPRISE = "#ea580c";
const ce_reprise = (p) => (p && p.statut === "Cancelled" && p.reprise ? p.reprise : null);
/** « 🔧 Reprise atelier · OSM-2026-0015 · en route vers le magasin ». Pure, déjà échappé. */
const ce_reprise_texte = (r) => `🔧 Reprise atelier · ${ce_esc(r.dossier)}${r.etat ? " · " + ce_esc(r.etat) : ""}`;

/** Les pastilles d'une tâche : optimisation des tournées (heure / employé fixes), puis Aramex. Pure. */
function ce_pastilles(p) {
  const out = [];
  if (p.genre === "tache") {
    const ep = ce_epingle(p);
    if (ep) out.push(ep);
    if (cint(p.aramex)) out.push(CE_ARAMEX);
  }
  return out;
}

/**
 * La fiche d'information d'un événement, montrée au survol (22/11/2026, « améliore la visibilité du tip ») : les
 * mêmes rubriques que l'info-bulle de l'ancien calendrier, mais dans une carte lisible — bandeau de la couleur de la
 * tâche avec l'heure, puis une ligne par rubrique, le libellé à gauche. Rend du HTML déjà échappé. Pure.
 */
function ce_fiche(p, debut, fin, journee) {
  const RAPPORT_VIDE = "Indiquez vos remarques sur l'intervention et le client:";
  const texte = (v) => ce_esc(v).replace(/\n/g, "<br>");
  const reprise = ce_reprise(p);
  const annulee = p.statut === "Cancelled" && !reprise, terminee = p.statut === "Completed";
  const plein = p.genre === "ferie" ? "#6b7280" : reprise ? CE_REPRISE : annulee ? "#9ca3af" : (p.couleur || "#FF6347");
  const lignes = ce_titre(p.titre);
  let quand = "";
  if (debut) {
    // le jour en français quelle que soit la langue du compte
    const d = moment(debut), f = fin ? moment(fin) : null;
    const jour = ["Dimanche", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"][d.day()] + " " + d.format("DD/MM");
    quand = journee ? jour + " · toute la journée" : jour + " · " + d.format("HH:mm") + (f ? " – " + f.format("HH:mm") : "");
  }
  const etat = p.genre === "ferie" ? "Jour férié" : reprise ? "Reprise atelier" : annulee ? "Annulée" : terminee ? "Terminée"
    : p.genre === "reunion" ? "Réunion" : p.verification ? "Vérification en cours" : "";
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
    if (reprise) rubrique("🔧", "Machine reprise", `Dossier ${reprise.dossier} — ${reprise.etat || ""}\n(un clic ouvre le dossier de réparation)`, true);
    else rubrique("⛔", "Annulation", p.raison_annulation);
    // Vérification de stock : la tâche ne devient verte qu'à l'accord de l'employé et du responsable.
    if (p.verification) rubrique("🧾", "Vérification", `${p.verification.verification} — ${p.verification.etape}\nTerminée (verte) quand les deux ont accepté les quantités`, true);
    if (p.rapport_visite && p.rapport_visite.trim() !== RAPPORT_VIDE) rubrique("📋", "Rapport", p.rapport_visite);
    const ep = p.genre === "tache" ? ce_epingle(p) : null;
    if (ep) rubrique(ep[0], ep[3], ep[1].split(" : ")[1]);
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
  const horaire = !e.journee && (e.genre === "tache" || e.genre === "reunion");      // jours entiers et fériés : pas de fuseau
  const out = { id: e.id, start: horaire ? ce_vers_utilisateur(e.debut) : e.debut, end: horaire ? ce_vers_utilisateur(e.fin) : e.fin,
                allDay: !!e.journee, title: e.titre || "", extendedProps: e,
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
  const reprise = ce_reprise(e);
  const annulee = e.statut === "Cancelled" && !reprise, terminee = e.statut === "Completed";
  const plein = reprise ? CE_REPRISE : annulee ? "#9ca3af" : (e.couleur || "#FF6347");
  const fond = annulee ? "#f3f4f6" : ce_pastel(plein, terminee ? 0.30 : 0.24);
  const styles = [`--ce-plein:${plein}`, `border:1px solid ${ce_rgba(plein, 0.45)}`, `border-left:5px solid ${plein}`];
  if (cint(e.reservation_app) === 1) {
    // Rendez-vous pris par le client dans l'app : les hachures de l'ancien calendrier, sur toute la tuile, mais
    // LÉGÈRES (22/11/2026 : « garde le barré, mais barre plus légèrement dans le texte ») — traits fins et pâles,
    // bien espacés : on voit que la tuile est hachurée, le texte reste net.
    styles.push("background-image:repeating-linear-gradient(45deg, rgba(0,0,0,.075) 0, rgba(0,0,0,.075) 1px, transparent 1px, transparent 8px)");
  }
  const classes = ["ce-e"];
  if (annulee) classes.push("ce-annulee");
  if (terminee) classes.push("ce-terminee");
  if (reprise) classes.push("ce-reprise");
  Object.assign(out, { backgroundColor: fond, textColor: annulee ? "#6b7280" : ce_texte(fond), styles, classNames: classes });
  if (e.genre === "reunion") {
    // une réunion prévue se glisse sur un autre jour ou une autre heure ; sa durée et ses participants, non
    out.startEditable = !!ecrire && e.statut !== "Completed";
    out.durationEditable = false;
  } else {
    // une intervention fermée par une reprise de machine ne se déplace pas : elle dit ce qui s'est passé ce jour-là
    out.startEditable = !!ecrire && !reprise;
    out.durationEditable = !!ecrire && !e.journee && !reprise;
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
    this.creneau = ce_mode_creneau();
    this.creneau_appels = this.creneau && window.name === "calendrier-equipe-creneau-appels";
    const memo = this.creneau ? {} : this.lire_memo();      // le mode créneau ne lit ni n'écrit les préférences
    this.filtres = { employes: new Set(memo.employes || []), types: new Set(memo.types || []), annulees: memo.annulees !== false };
    this.vue = this.creneau ? "timeGridWeek"                // comme avant : la semaine, pour voir où il reste de la place
      : CE_VUES.includes(memo.vue) ? memo.vue : (ce_etroit() ? "listWeek" : "resourceTimeGridDay");
    if (this.creneau) document.body.classList.add("ce-mode-creneau");
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
    if (this.creneau) return;
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
      /* mode créneau : le calendrier seul, sans la barre du Desk ni le titre de la page */
      body.ce-mode-creneau .navbar, body.ce-mode-creneau .page-head, body.ce-mode-creneau .raven-chat { display: none !important; }
      body.ce-mode-creneau { --navbar-height: 0px; padding-top: 0 !important; }
      body.ce-mode-creneau .ce-cal { height: calc(100vh - 150px); }
      .ce-consigne { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e3a8a; border-radius: 8px; padding: 7px 12px; font-size: 13px; font-weight: 600; flex: 1; }
      .ce-consigne.modifier { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
      .ce-cal .ec-event.ce-a-deplacer { background-color: #fecaca !important; outline: 3px solid #dc2626; outline-offset: -1px; z-index: 3; }
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
      .ce-saut { flex-basis: 100%; height: 0; }
      .ce-titre-filtre { min-width: 68px; }
      .ce-titre-filtre { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted, #6b7280); }
      .ce-cal { height: calc(100vh - 250px); min-height: 460px; }
      .ce-cal .ec { height: 100%; font-size: 12px; }
      /* Vue Mois : la hauteur de la fenêtre ne suffit pas — sur un écran ordinaire les cases faisaient 75 à 100 px et
         toutes les tâches se repliaient derrière « + N autres » (22/11/2026). Le mois prend la place qu'il lui faut
         (au moins 165 px par semaine, 5 à 6 tâches visibles par jour) et c'est la page qui défile. */
      .ce-cal.ce-mois { height: max(calc(100vh - 250px), 1040px); }
      .ce-cal .ec-toolbar { flex-wrap: wrap; gap: 6px; }
      .ce-cal .ec-event { border-radius: 6px; cursor: pointer; box-shadow: 0 1px 2px rgba(15,23,42,.10); }
      .ce-cal .ec-event:hover { box-shadow: 0 2px 6px rgba(15,23,42,.22); filter: brightness(.97); }
      .ce-ev { font-size: 10.5px; line-height: 1.2; overflow: hidden; overflow-wrap: anywhere; }
      .ce-cal .ec-title { font-size: 20px; }
      .ce-ev-h { font-weight: 600; opacity: .8; white-space: nowrap; }
      .ce-ev-1 { font-weight: 800; font-size: 11px; }
      .ce-ev-s, .ce-ev-c { font-weight: 800; font-size: 11px; }
      .ce-ev-mois { white-space: nowrap; text-overflow: ellipsis; font-weight: 500; }
      .ce-ev-2 { font-weight: 500; }
      .ce-annulee .ce-ev-1, .ce-annulee .ce-ev-2 { text-decoration: line-through; }
      .ce-annulee { opacity: .85; }
      .ce-ev-r { font-weight: 700; }
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
      /* les signes de l'optimisation : pastille blanche cerclée — orange = heure fixe, violet = employé fixe, rouge = les deux */
      .ce-fixe { display: inline-flex; align-items: center; justify-content: center; width: 17px; height: 17px; border-radius: 50%;
                 background: #fff; border: 2px solid #f59e0b; font-size: 10px; line-height: 1; vertical-align: middle;
                 box-shadow: 0 1px 2px rgba(0,0,0,.28); text-decoration: none; }
      .ce-fixe.e { border-color: #7c3aed; }
      .ce-fixe.t { border-color: #dc2626; }
      .ce-fixe.a { border-color: #c2410c; }
      .ce-coin { position: absolute; top: 2px; right: 3px; z-index: 1; display: inline-flex; gap: 2px; }
      .ce-legende { display: inline-flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 11px;
                    color: var(--text-muted, #6b7280); margin-left: auto; }
      .ce-legende span { display: inline-flex; align-items: center; gap: 4px; }
      .ce-legende i { width: 16px; height: 11px; border-radius: 3px; display: inline-block; border: 1px solid rgba(0,0,0,.18); }
      .ce-col { display: inline-flex; align-items: center; gap: 6px; font-weight: 700; }
      .ce-col i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
      .ce-col small { font-weight: 400; color: var(--text-muted, #6b7280); }
      @media (max-width: 767px) {
        .ce-cal { height: 78vh; }
        .ce-cal.ce-mois { height: max(78vh, 900px); }
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
    if (this.creneau) {
      const modifier = !!ce_tache_a_deplacer();
      $(`<div class="ce-consigne${modifier ? " modifier" : ""}"></div>`).text(modifier
        ? "✏️ La tâche encadrée en rouge est celle à déplacer : glissez-la, ou cliquez sur son nouveau créneau."
        : "📅 Cliquez sur un créneau pour y placer le rendez-vous — ou glissez sur plusieurs créneaux pour fixer sa durée. La vue « Équipe » permet de choisir aussi l’employé.").appendTo($a);
      $(`<button class="ce-btn clair" type="button" title="Relire les tâches">↻</button>`).on("click", () => this.recharger()).appendTo($a);
      return;
    }
    const bouton = (libelle, couleur, titre, clic) => $(`<button class="ce-btn" type="button"></button>`)
      .text(libelle).attr("title", titre).css("background", couleur).on("click", clic).appendTo($a);
    if (d.creer) bouton("➕ Nouvelle tâche", "#1d4ed8", "Créer une tâche de travail", () => this.fenetre_nouvelle({}));
    if (window.maJournee_ouvrir) {
      bouton("☀️ Ma journée", "#2490ef", "Mes interventions du jour affiché",
        () => window.maJournee_ouvrir(["resourceTimeGridDay", "timeGridDay"].includes(this.vue) && this.periode ? this.periode.from : undefined));
    }
    if (d.generer_bl && typeof _open_generer_bl_dialog === "function") {
      bouton("🚚 Générer BL", "#dc2626", "Générer et imprimer les bons de livraison du jour", () => _open_generer_bl_dialog());
    }
    if (d.optimiser && window.optimiserTournees_ouvrir) {
      bouton("🗺️ Optimiser", "#059669", "Redistribuer et réordonner les tâches du jour entre les employés présents pour rouler moins",
        () => window.optimiserTournees_ouvrir());
    }
    if (d.ecrire && window.customization_app && customization_app.gestion_taches) {
      // « Décaler / annuler » : le libellé du bouton de l'ancien calendrier — c'est le même écran (Gérer les interventions)
      bouton("🗓️ Décaler / annuler", "#0ea5e9", "Gérer les interventions : décaler, transférer ou annuler plusieurs tâches, en prévenant les clients",
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
    // « Types » sur sa propre ligne (22/11/2026) : employés en haut, types dessous, on s'y retrouve d'un coup d'œil
    $f.append(`<span class="ce-saut"></span><span class="ce-titre-filtre">Types</span>`);
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
      <span title="Le technicien n’a pas pu finir et est reparti avec la machine : elle suit son dossier dans Réparation osmoseurs"><i style="background:${ce_pastel(CE_REPRISE, 0.24)};border-left:4px solid ${CE_REPRISE}"></i>🔧 Machine reprise à l’atelier</span>
      <span><i style="background:#eef2f7;background-image:repeating-linear-gradient(45deg, rgba(0,0,0,.22) 0, rgba(0,0,0,.22) 1px, transparent 1px, transparent 4px)"></i>Hachures : prise par le client dans l’app</span>
      <span title="L’optimisation des tournées peut changer l’employé, pas l’heure">${ce_pastille(ce_epingle({ heure_fixe: 1 }))} Heure fixe</span>
      <span title="L’optimisation des tournées peut changer l’heure, pas l’employé">${ce_pastille(ce_epingle({ employe_fixe: 1 }))} Employé fixe</span>
      <span title="L’optimisation des tournées ne touche pas à cette tâche">${ce_pastille(ce_epingle({ heure_fixe: 1, employe_fixe: 1 }))} Heure et employé fixes</span>
      <span>— l’optimisation des tournées les respecte</span>
      <span title="${ce_esc(CE_ARAMEX[1])}">${ce_pastille(CE_ARAMEX)} Livraison Aramex</span></span>`);
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

  /**
   * La grille va de 07:00 à 20:00 ; une tâche plus tôt ou plus tard l'élargit, par HEURES ENTIÈRES. (L'option de la
   * bibliothèque qui le fait seule calait la grille sur la minute de la tâche — une tâche à 02:05 donnait une
   * échelle « 02:27, 03:27… » — d'où ce calcul à la main, le 22/11/2026.) Fonction sans effet si rien ne change.
   */
  caler_heures(evenements) {
    let debut = 7, fin = 20;
    (evenements || []).forEach((ev) => {
      if (ev.allDay || ev.display === "background" || !ev.start) return;
      const d = moment(ev.start), f = moment(ev.end || ev.start);
      debut = Math.min(debut, d.hour());
      fin = Math.max(fin, f.isSame(d, "day") ? f.hour() + (f.minute() ? 1 : 0) : 24);
    });
    const cle = debut + "-" + fin;
    if (!this.ec || cle === this._heures) return;
    this._heures = cle;
    const hh = (n) => String(n).padStart(2, "0") + ":00:00";
    this.ec.setOption("slotMinTime", hh(debut));
    this.ec.setOption("slotMaxTime", hh(Math.min(24, fin)));
  }

  /** L'événement passe-t-il les filtres ? Les jours fériés passent toujours. */
  visible(e) {
    const f = this.filtres;
    if (e.genre === "ferie" || e.genre === "fond") return true;
    if (!f.annulees && e.statut === "Cancelled" && !ce_reprise(e)) return false;
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
      timeGridDay: "Jour", timeGridWeek: "Semaine", dayGridMonth: "Mois", listWeek: "Liste", next: suivant, prev: precedent, close: "Fermer" });
    this.ec = EventCalendar.create($cal[0], {
      view: this.vue,
      locale: "fr",
      firstDay: 1,
      height: "100%",
      headerToolbar: { start: "prev,next today", center: "title", end: CE_VUES.join(",") },
      buttonText: textes("Suivant", "Précédent"),
      views: {
        resourceTimeGridDay: { buttonText: textes("Jour suivant", "Jour précédent"), columnWidth: etroit ? "170px" : undefined },
        timeGridDay: { buttonText: textes("Jour suivant", "Jour précédent") },
        timeGridWeek: { buttonText: textes("Semaine suivante", "Semaine précédente"), columnWidth: etroit ? "130px" : undefined },
        dayGridMonth: { buttonText: textes("Mois suivant", "Mois précédent") },
        listWeek: { buttonText: textes("Semaine suivante", "Semaine précédente") },
      },
      slotMinTime: "07:00:00",
      slotMaxTime: "20:00:00",
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
      editable: this.creneau ? !!ce_tache_a_deplacer() : !!d.ecrire,      // mode créneau : on ne déplace que « la tâche à modifier »
      selectable: this.creneau ? true : !!d.creer,
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
      eventClick: (info) => {
        this.cacher_fiche();
        if (this.creneau) {
          // mode « modifier » de la liste d'appels : cliquer la tâche garde ses heures actuelles
          if (ce_tache_a_deplacer() && !info.event.allDay) this.envoyer_creneau(info.event.start, info.event.end || moment(info.event.start).add(1, "hour").toDate());
          return;
        }
        this.ouvrir(info.event);
      },
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
        if (String(info.view.type).startsWith("list")) return;
        if (this.creneau) {
          // Un clic n'est pas une durée voulue : sans heure de fin, le dialogue de rendez-vous la calcule d'après le
          // type d'intervention (la liste d'appels, elle, attend toujours une fin : une demi-heure).
          if (!info.allDay) this.envoyer_creneau(info.date, this.creneau_appels ? moment(info.date).add(30, "minutes").toDate() : null, info.resource);
          return;
        }
        if (!d.creer) return;
        const fin = info.allDay ? moment(info.date).add(1, "day") : moment(info.date).add(30, "minutes");
        this.nouvelle({ start: info.date, end: fin.toDate(), allDay: info.allDay, resource: info.resource, view: info.view });
      },
      datesSet: (info) => {
        this.cacher_fiche();
        this.vue = info.view.type;
        this.$root.find(".ce-cal").toggleClass("ce-mois", this.vue === "dayGridMonth");
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
      const a_deplacer = this.creneau ? ce_tache_a_deplacer() : "";
      const out = [];
      this.cache.evenements.filter((e) => this.visible(e)).forEach((e) => {
        const ev = ce_evenement(e, ids, this.creneau ? !!a_deplacer : this.ctx.droits.ecrire);
        if (a_deplacer && e.name === a_deplacer) ev.classNames = (ev.classNames || []).concat("ce-a-deplacer");
        out.push(ev);
        // jour férié : la vue Mois ne montre un fond que s'il est « toute la journée » — on donne les deux
        if (e.genre === "fond") out.push(Object.assign({}, ev, { id: ev.id + "|jour", allDay: true }));
      });
      this.caler_heures(out);
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
   * Ce qui s'écrit sur la tuile (22/11/2026). Disposition choisie APRÈS MESURE sur les 141 tâches à client de deux
   * semaines réelles, colonnes de 165 px, interventions d'une demi-heure (3 lignes utiles) — part des tuiles où le
   * nom du client se lit en entier / l'employé :
   *   titre d'avant « Secteur / 🔧 Entretien: Client: X / employé », heure longue ........ 65–72 % /  1–2 %
   *   le même sans « Client: », heure compacte .......................................... 69–78 % / 35–38 %
   *   heure · type / secteur · client / employé (heure longue : la 1re ligne déborde) .... 40–44 % / 12–13 %
   *   la même avec l'heure compacte ..................................................... 73–83 % / 21–23 %
   *   heure type: / CLIENT / secteur · employé .......................................... 82–92 % / 61–63 %
   * SA DÉCISION, le même jour, après avoir vu la dernière : « le secteur en gras après le temps est très important,
   * sinon le reste est bien — temps, secteur, après intervention, nom du client ». D'où la disposition en place :
   *   heure SECTEUR / type: CLIENT / employé   (la 2e du tableau, 69–78 % : il la préfère en connaissance de cause)
   * Ce qui fait la différence : « 09:30 – 10:00 » avec ses espaces ne laisse pas la place au type sur la même
   * ligne, et un client qui partage sa ligne avec le secteur ou le type passe à la ligne dès que son nom est long.
   * On garde donc l'heure compacte « 09:30–10:00 », suivie du secteur en gras ; puis « 🔧 Entretien: nom du client »
   * (sans le mot « Client: »), le nom en gras, qui passe à la ligne s'il est long ; puis l'employé.
   * Terminée = ✓, annulée = ✕ devant le secteur (la couleur et la fiche au survol le disent aussi). En vue Équipe la
   * colonne nomme déjà l'employé : pas de 3e ligne. Sans client (congé, réunion, vérification de stock, jour
   * férié) : le titre de l'ancien calendrier, heure compacte sur sa première ligne.
   */
  contenu(info) {
    const p = info.event.extendedProps || {};
    if (p.genre === "fond") return { html: "" };
    const signes = ce_pastilles(p).map((x) => ce_pastille(x)).join("");
    const lignes = ce_titre(p.titre).map(ce_esc);
    const mois = info.view.type === "dayGridMonth";
    // vues Mois et Liste : la pastille dans la ligne ; grilles horaires : dans le coin de la tuile, la 1re ligne lui laisse la place
    const enLigne = mois || String(info.view.type || "").startsWith("list");
    const epingle = enLigne && signes ? signes + " " : "";
    const coin = !enLigne && signes ? `<span class="ce-coin">${signes}</span>` : "";
    const place = coin ? ` style="padding-right:${19 * ce_pastilles(p).length}px"` : "";
    const debut = info.event.allDay ? "" : moment(info.event.start).format("HH:mm");
    const heure = info.event.allDay ? "" : `<span class="ce-ev-h">${debut}–${moment(info.event.end).format("HH:mm")}</span> `;
    if (p.genre === "tache" && p.client) {
      const type = p.custom_type_dintervention || "", icone = (this.ctx.icones || {})[type] || "";
      const genre = ce_esc((icone + " " + type).trim());
      const reprise = ce_reprise(p);
      const etat = p.statut === "Completed" ? "✓ " : reprise ? "🔧 " : p.statut === "Cancelled" ? "✕ " : "";
      if (mois) {
        return { html: `<div class="ce-ev ce-ev-mois">${debut ? `<b>${debut}</b> ` : ""}${epingle}${etat}${genre}${genre ? ": " : ""}<b>${ce_esc(p.client)}</b>${reprise ? " · " + ce_esc(reprise.dossier) : ""}</div>` };
      }
      const aramex = lignes.filter((l) => l.startsWith("📦"));
      const employe = info.view.type !== "resourceTimeGridDay" && p["custom_employé"] ? ce_esc(p["custom_employé"]) : "";
      return { html: `<div class="ce-ev">${coin}<div class="ce-ev-2"${place}>${heure}${epingle}<b class="ce-ev-s">${etat}${ce_esc(p.secteur || "")}</b></div>
        <div class="ce-ev-2">${genre}${genre ? ": " : ""}<b class="ce-ev-c">${ce_esc(p.client)}</b></div>
        ${reprise ? `<div class="ce-ev-2 ce-ev-r">${ce_reprise_texte(reprise)}</div>` : ""}${aramex.length ? `<div class="ce-ev-2">${aramex.join("<br>")}</div>` : ""}${employe ? `<div class="ce-ev-2">${employe}</div>` : ""}</div>` };
    }
    // Vérification de stock encore ouverte : l'étape, et qui doit faire le geste suivant (verte à l'accord des deux).
    const etape = p.genre === "tache" && p.verification && p.verification.etape ? "⏳ " + ce_esc(p.verification.etape) : "";
    if (mois) {
      // vue Mois : une seule ligne par tâche, pour en voir plusieurs dans la case du jour
      return { html: `<div class="ce-ev ce-ev-mois">${debut ? `<b>${debut}</b> ` : ""}${epingle}${lignes.concat(etape ? [etape] : []).join(" · ")}</div>` };
    }
    return { html: `<div class="ce-ev">${coin}<div class="ce-ev-1"${place}>${heure}${epingle}${lignes[0] || ""}</div>${
      etape ? `<div class="ce-ev-2 ce-ev-r">${etape}</div>` : ""}${
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
    // Machine reprise à l'atelier : la suite se passe dans Réparation osmoseurs, on y ouvre le dossier.
    const reprise = ce_reprise(p);
    if (reprise && frappe.model.can_read("Machine Reparation")) {
      window.open("/app/reparation-osmoseurs?dossier=" + encodeURIComponent(reprise.dossier), "_blank");
      return;
    }
    // La fiche complète de la tâche, dans une fenêtre au-dessus du calendrier (22/11/2026).
    if (frappe.model.can_read(p.doctype)) this.fenetre_tache(p.name);
  }

  /**
   * Mode créneau : le créneau choisi part à la page du dessus, qui ouvre son dialogue de rendez-vous. Le message est
   * celui des anciennes fenêtres — `rdv_new_doc`, avec le préremplissage laissé dans le localStorage (liste
   * d'appels) et les heures dans le fuseau du système. En vue Équipe, l'employé de la colonne l'accompagne.
   */
  envoyer_creneau(debut, fin, colonne) {
    let depose = {};
    try { depose = JSON.parse(localStorage.getItem("rdv_prefill") || "{}") || {}; } catch (e) { depose = {}; }
    const creneau = { starts_on: ce_vers_systeme(debut) };
    if (fin) creneau.ends_on = ce_vers_systeme(fin);
    if (colonne && colonne.id && colonne.id !== this.ctx.sans_employe) creneau.custom_choix_du_staff = colonne.id;
    window.parent.postMessage({ type: "rdv_new_doc", doc: Object.assign({}, depose, creneau) }, window.location.origin);
  }

  /** Un créneau cliqué ou sélectionné : nouvelle tâche à ces heures — et pour cet employé en vue Équipe. */
  nouvelle(info) {
    if (this.creneau) {
      this.ec.unselect();
      if (info.allDay || info.view.type === "dayGridMonth") return;
      // une sélection de plus d'une demi-heure est une durée voulue ; en dessous, c'est un clic qui a glissé
      const voulue = info.end - info.start > 30 * 60000;
      this.envoyer_creneau(info.start, voulue || this.creneau_appels ? info.end : null, info.resource);
      return;
    }
    const unJour = info.end - info.start <= 864e5;
    if (info.view.type === "dayGridMonth" && unJour) { this.ec.unselect(); return; }
    const valeurs = info.allDay ? { starts_on: ce_fmt(info.start), ends_on: ce_fmt(moment(info.end).subtract(1, "second")) }
      : { starts_on: ce_vers_systeme(info.start), ends_on: ce_vers_systeme(info.end) };
    if (info.resource && info.resource.id !== this.ctx.sans_employe) valeurs.custom_choix_du_staff = info.resource.id;
    this.ec.unselect();
    this.fenetre_nouvelle(valeurs);
  }

  /**
   * La fiche d'une tâche dans une fenêtre au-dessus du calendrier (22/11/2026 : « je veux que la tâche s'ouvre en
   * popup dans le calendrier », la nouvelle comme l'existante, « avec tout le doctype »). C'est la VRAIE fiche, dans
   * un cadre : ses listes, ses boutons, ses contrôles et le titre qu'elle compose restent ceux du formulaire (même
   * procédé que la tâche ouverte depuis l'optimisation des tournées). À la fermeture, le calendrier est relu.
   * ⚠️ Le cadre porte un NOM : le Client Script « Tache de travail RDV Prefill » renvoie toute nouvelle tâche ouverte
   * dans un cadre à la page du dessus (prise de rendez-vous depuis la liste d'appels) puis revient en arrière — sans
   * ce nom, qu'il reconnaît, la fiche se vidait et le calendrier avec.
   * `apres_chargement(cadre)` : appelé à chaque seconde tant que la fenêtre est ouverte (fermeture automatique).
   */
  fenetre(titre, adresse, surveiller) {
    const p = new frappe.ui.Dialog({ title: titre, size: "extra-large", fields: [{ fieldtype: "HTML", fieldname: "fiche" }] });
    p.fields_dict.fiche.$wrapper.html(`<div class="text-muted" data-chargement style="padding:12px">Ouverture de la fiche…</div>
      <iframe name="calendrier-equipe-tache" src="${ce_esc(adresse)}" style="width:100%;height:80vh;border:0;display:block;visibility:hidden"></iframe>`);
    p.$wrapper.find(".modal-dialog").css({ "max-width": "min(1400px, 96vw)", width: "96vw" });
    const $if = p.fields_dict.fiche.$wrapper.find("iframe");
    $if.on("load", () => {
      try {
        // sans la barre du Desk ni le fil Raven : la fiche seule
        const doc = $if[0].contentDocument, st = doc.createElement("style");
        st.textContent = ":root { --navbar-height: 0px !important; } header.navbar, .navbar { display: none !important; }"
          + " .raven-chat { display: none !important; } body { padding-top: 0 !important; } .page-head { top: 0 !important; }";
        doc.head.appendChild(st);
      } catch (e) { /* fiche affichée telle quelle */ }
      p.fields_dict.fiche.$wrapper.find("[data-chargement]").remove();
      $if.css("visibility", "visible");
    });
    const veille = surveiller ? setInterval(() => {
      try { surveiller($if[0].contentWindow, p); } catch (e) { /* cadre pas encore prêt */ }
    }, 700) : null;
    p.onhide = () => {
      if (veille) clearInterval(veille);
      // une fenêtre Frappe fermée reste dans le document : on retire le cadre, sinon la fiche continue de vivre derrière
      setTimeout(() => p.$wrapper.remove(), 400);
      this.recharger();
    };
    p.show();
    return p;
  }

  /**
   * Une tâche existante : sa fiche complète. Dès qu'elle est enregistrée (sa date de modification a changé, plus rien
   * en attente, aucun message ouvert dans la fiche), la fenêtre se referme seule (22/11/2026). Le Client Script
   * « Calender View Tache de travail » renvoyait après chaque enregistrement à l'ANCIEN calendrier — dans le cadre,
   * on se retrouvait avec l'ancien calendrier dans la fenêtre ; il reconnaît le nom du cadre et ne le fait plus ici.
   * Si la fiche part ailleurs (un bouton qui ouvre la commande, par exemple), la fenêtre reste ouverte.
   */
  fenetre_tache(nom) {
    let depart = null, vue_enregistree = 0;
    this.fenetre("🗂️ " + nom, "/app/tache-de-travail/" + encodeURIComponent(nom), (w, p) => {
      const f = w && w.cur_frm;
      if (!f || f.doctype !== "Tache de travail" || f.doc.name !== nom) { vue_enregistree = 0; return; }
      if (depart === null) { if (!f.is_dirty()) depart = f.doc.modified; return; }
      const enregistree = f.doc.modified !== depart && !f.is_dirty() && !w.document.querySelector(".modal.show");
      vue_enregistree = enregistree ? vue_enregistree + 1 : 0;
      if (vue_enregistree >= 2) {
        p.hide();
        frappe.show_alert({ message: "Tâche enregistrée : " + nom, indicator: "green" }, 5);
      }
    });
  }

  /**
   * Une NOUVELLE tâche. `valeurs` préremplit la fiche par l'adresse (/app/tache-de-travail/new?champ=valeur). Une
   * fois la tâche enregistrée (la fiche n'est plus « nouvelle », rien en attente, aucun message ouvert dedans), la
   * fenêtre se ferme seule.
   */
  fenetre_nouvelle(valeurs) {
    const adresse = "/app/tache-de-travail/new?" + Object.keys(valeurs || {}).map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(valeurs[k])).join("&");
    let vue_enregistree = 0;
    this.fenetre("➕ Nouvelle tâche", adresse, (w, p) => {
      const f = w && w.cur_frm;
      const prete = f && f.doctype === "Tache de travail" && !f.is_new() && !f.is_dirty() && !w.document.querySelector(".modal.show");
      vue_enregistree = prete ? vue_enregistree + 1 : 0;
      if (vue_enregistree >= 2) {
        const nom = f.doc.name;
        p.hide();
        frappe.show_alert({ message: "Tâche créée : " + nom, indicator: "green" }, 5);
      }
    });
  }

  /** Une tâche glissée (ou étirée) : le serveur décide, on revient en arrière s'il refuse. */
  deposer(info, etiree) {
    const ev = info.event, p = ev.extendedProps || {}, annuler = () => info.revert();
    if (this.creneau) {
      // mode « modifier » : la tâche glissée donne le nouveau créneau, rien n'est enregistré ici
      const debut = ev.start, fin = ev.end || moment(ev.start).add(1, "hour").toDate();
      annuler();
      if (!etiree && ce_tache_a_deplacer() && !ev.allDay) this.envoyer_creneau(debut, fin, info.newResource);
      return;
    }
    const dire = (message) => frappe.show_alert({ message, indicator: "orange" }, 6);
    if (p.genre === "reunion") {
      if (etiree || info.newResource) { dire("Une réunion change de jour ou d’heure ici ; ses participants et sa durée se règlent dans sa page."); return annuler(); }
      if (p.statut === "Completed") { dire("Une réunion tenue ne se déplace plus."); return annuler(); }
      const debut = moment(ev.allDay ? ce_fmt(ev.start) : ce_vers_systeme(ev.start));
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
      args.debut = ce_vers_systeme(ev.start);
      args.fin = ce_vers_systeme(ev.end);
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
