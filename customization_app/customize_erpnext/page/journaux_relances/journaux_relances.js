// Journaux des relances : dernier passage, résumé du fichier journal et erreurs récentes de chaque cron de l’entretien.
// 25/11/2026 : en tête, l’« Entonnoir du jour » (entonnoir_relances.vue_page) — signaux rouges, photo du jour, tendance
// des 14 derniers jours — pour voir une baisse des listes d’appel en un jour au lieu de deux mois.
frappe.pages["journaux-relances"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Journaux des relances", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("journaux_relances", {}));
  const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));
  const nb = (n) => (n === null || n === undefined || n === "") ? "—" : Number(n).toLocaleString("fr-FR");
  const jm = (iso) => iso ? `${String(iso).slice(8, 10)}/${String(iso).slice(5, 7)}` : "";

  const charger_crons = async () => {
    const r = (await frappe.call({ method: "customization_app.journaux_relances.resume", args: { jours: 2 } })).message || {};
    $(wrapper).find("#jr-intro").html(
      `<span>Alertes par e-mail : <b>${esc(r.email_alertes || "aucune adresse")}</b></span>` +
      `<a href="${r.liens.jobs}">📋 Journal des tâches planifiées</a><a href="${r.liens.erreurs}">⚠️ Journal des erreurs</a>` +
      `<a href="${r.liens.listes}">📞 Listes d’appels</a><a href="${r.liens.reglage}">⚙️ Réglage des relances</a>` +
      `<button class="btn btn-xs btn-default" id="jr-refresh">↻ Actualiser</button>`);
    $(wrapper).find("#jr-refresh").on("click", wrapper.jr_charger);
    const badge = (d) => !d ? `<span class="jr-badge jr-na">jamais vu (40 j)</span>`
      : `<span class="jr-badge ${d.status === "Complete" ? "jr-ok" : d.status === "Start" ? "jr-na" : "jr-ko"}">${esc(d.status)} · ${frappe.datetime.prettyDate(d.creation)} (${esc(String(d.creation).slice(0, 16))})</span>`;
    $(wrapper).find("#jr-liste").html((r.crons || []).map((c) => `<div class="jr-card">
      <div class="jr-head"><div><b>${esc(c.titre)}</b> <span class="jr-quand">· ${esc(c.quand)} · ${esc(c.cle)}</span></div>${badge(c.dernier)}
        ${c.nb_erreurs ? `<span class="jr-badge jr-ko">${c.nb_erreurs} erreur(s) depuis ${esc(r.depuis)}</span>` : `<span class="jr-badge jr-ok">0 erreur depuis ${esc(r.depuis)}</span>`}</div>
      ${c.lignes && c.lignes.length ? `<div class="jr-pre">${c.lignes.map(esc).join("\n")}</div>`
        : `<div class="jr-vide" style="margin-top:6px">${c.journal ? `Journal ${esc(c.journal)}.log vide pour l’instant (il se remplit au prochain passage).` : esc(c.trace || "Ce cron laisse ses traces en commentaires sur les tâches (📲 Rappel SMS).")}</div>`}
      ${c.erreurs && c.erreurs.length ? `<div class="jr-err">${c.erreurs.map((e) => `<div><a href="/app/error-log/${encodeURIComponent(e.name)}">${esc(e.method)}</a> <span class="text-muted">${esc(String(e.creation).slice(0, 16))}</span></div>`).join("")}</div>` : ""}
      ${c.passages && c.passages.length > 1 ? `<div class="text-muted" style="font-size:11px;margin-top:6px">Passages précédents : ${c.passages.slice(1).map((p) => `${esc(p.status)} ${esc(String(p.creation).slice(0, 16))}`).join(" · ")}</div>` : ""}
    </div>`).join(""));
  };

  // ── Entonnoir du jour ────────────────────────────────────────────────────
  const ligne = (libelle, valeur, retrait) => `<div class="jr-ligne${retrait ? " jr-retrait" : ""}"><span>${libelle}</span><span>${valeur}</span></div>`;
  const tuile = (titre, corps) => `<div class="jr-tuile"><h4>${titre}</h4>${corps}</div>`;
  const ETATS = { ok: ["jr-ok", "✓"], a_venir: ["jr-na", "à venir"], en_cours: ["jr-na", "en cours"], manquant: ["jr-ko", "manquant"],
    echec: ["jr-ko", "échec"], interrompu: ["jr-ko", "interrompu"], plantage: ["jr-ko", "planté"] };

  const t_visites = (v, statuts) => !v ? "" : tuile("Visites échues non faites",
    `<div class="jr-chiffre">${nb(v.lignes)}</div><div class="jr-note" style="margin-top:0">${nb(v.clients)} client(s), dont ${nb(v.plus_6_mois)} visite(s) de plus de 6 mois</div>` +
    (statuts || []).map((s) => ligne(esc(s), `${nb((v.par_statut[s] || {}).lignes)} <span class="text-muted">· ${nb((v.par_statut[s] || {}).clients)} cl.</span>`, true)).join(""));

  const t_appel = (a) => {
    if (!a) return tuile("Listes d’appel : l’entonnoir", `<div class="jr-vide">Calculé seulement pour aujourd’hui.</div>`);
    const motifs = Object.entries((a.exclus || {}).motifs || {}).map(([m, n]) => `${esc(m)} ${nb(n)}`).join(", ");
    return tuile("Listes d’appel : l’entonnoir",
      (a.avant ? ligne("Visites SMS 2 envoyé, non appelées", `${nb(a.avant.visites)} <span class="text-muted">· ${nb(a.avant.clients)} cl.</span>`)
        + ligne(`Écartés : intervention de moins de ${nb(a.jours_apres_intervention)} j`, nb(a.avant.intervention_recente), true) : "")
      + ligne("<b>Candidats</b>", `<b>${nb(a.candidats)}</b>`)
      + ligne("Exclus de la relance", nb((a.exclus || {}).total), true)
      + (motifs ? `<div class="jr-note jr-retrait" style="margin-top:0">${motifs}</div>` : "")
      + ligne("Rendez-vous proche", nb(a.rdv), true)
      + ligne(`Appelés il y a moins de ${nb(a.jours_entre_deux_appels)} j`, nb(a.appel_recent), true)
      + (a.autres_ecartes ? ligne("Autres écarts", nb(a.autres_ecartes), true) : "")
      + ligne("<b>À appeler</b>", `<b>${nb((a.a_appeler || []).reduce((s, x) => s + (x.clients || 0), 0))}</b>`)
      + (a.a_appeler || []).map((x) => ligne(esc(x.type), x.clients ? `${nb(x.clients)} <span class="text-muted">· ${nb(x.dans_un_brouillon)} en liste, </span>${x.en_attente ? `<span style="color:#b91c1c">${nb(x.en_attente)} en attente</span>` : "0 en attente"}` : "0", true)).join("")
      + ((a.requalification_a_lister || a.rappels_echus) ? `<div class="jr-note">À requalifier jamais rappelés : ${nb(a.requalification_a_lister)} · rappels « plus tard » échus : ${nb(a.rappels_echus)}</div>` : ""));
  };

  const listes_txt = (l) => Object.keys(l || {}).length ? Object.entries(l).map(([t, x]) => `${esc(t)} ${nb(x.lignes)}`).join(", ") : "aucune";
  // 26/11/2026 : les RDV pris au calendrier (« Prendre RDV ») ou dans l’app cochent la ligne sans laisser de Version ;
  // ils sont ajoutés au jour de leur tâche (73 lignes « Rendez-vous pris » sur 108 depuis le 01/10 sur la copie).
  const t_listes = (fj, fv) => tuile("Listes et appels",
    ligne("Lignes créées aujourd’hui", nb((fj || {}).lignes)) + `<div class="jr-note jr-retrait" style="margin-top:0">${listes_txt((fj || {}).listes)}</div>`
    + ligne("Lignes créées hier", nb((fv || {}).lignes)) + `<div class="jr-note jr-retrait" style="margin-top:0">${listes_txt((fv || {}).listes)}</div>`
    + ligne("Lignes cochées « appelé » hier / aujourd’hui", `${nb((fv || {}).appelees)} / ${nb((fj || {}).appelees)}`)
    + (((fv || {}).appelees_rdv || (fj || {}).appelees_rdv) ? ligne("dont RDV pris au calendrier ou dans l’app", `${nb((fv || {}).appelees_rdv)} / ${nb((fj || {}).appelees_rdv)}`, true) : "")
    + ligne("Listes validées hier / aujourd’hui", `${nb((fv || {}).validees)} / ${nb((fj || {}).validees)}`));

  // 26/11/2026 : un SMS compte d’après son statut. De décembre 2025 à avril 2026, tous les envois de la copie sont
  // « Failed » (crédit ou API WinSMS) : compter les dates seules montrait des journées normales.
  const sms_ligne = (f) => {
    f = f || {};
    const ko = (f.sms1_echec || 0) + (f.sms2_echec || 0), inv = (f.sms1_invalide || 0) + (f.sms2_invalide || 0);
    return `${nb(f.sms1)} / ${nb(f.sms2)} <span class="text-muted">· ${nb(f.sms_clients)} cl.</span>`
      + (ko ? ` <span class="jr-badge jr-ko">${nb(ko)} en échec</span>` : "") + (inv ? ` <span class="jr-badge jr-na">${nb(inv)} sans numéro</span>` : "");
  };
  const t_sms = (fj, fv) => tuile("SMS de relance réussis",
    ligne("Hier : SMS 1 / SMS 2", sms_ligne(fv))
    + ligne("Aujourd’hui : SMS 1 / SMS 2", sms_ligne(fj))
    + `<div class="jr-note">Visites marquées « Success ». Les échecs (« Failed ») et les fiches sans numéro sont comptés à part. Dates portées par les visites : des minimums (un décalage en efface quelques-unes).</div>`);

  const t_machines = (fj, fv) => {
    const m = (fv || {}).machines || {}, n = (fj || {}).nes || {};
    return tuile("Machines livrées → échéanciers",
      ligne("Lignes machine livrées hier (BL)", nb(m.lignes))
      + ligne("B2B (sans échéancier, voulu) / clients « Non » ou comptoir", `${nb(m.b2b)} / ${nb(m.non)}`, true)
      + ligne("Machines suivies : couvertes / attendues (les « Non » comptent depuis le 26/11/2026)", `${nb(m.suivies)} / ${nb(m.attendues)}`, true)
      + ((m.sans || []).length ? `<div class="jr-note jr-retrait" style="color:#b91c1c;margin-top:0">Sans échéancier : ${(m.sans || []).map((d) => `<a href="/app/delivery-note/${encodeURIComponent(d)}">${esc(d)}</a>`).join(", ")}</div>` : "")
      + ligne("Échéanciers nés aujourd’hui", nb(n.total))
      + ligne("machine vendue / deviné (achat de consommables)", `${nb(n.machine)} / ${nb(n.devine)}`, true));
  };

  // 26/11/2026 : le chiffre est celui que la nuit doit ranimer ; les articles laissés gelés exprès (jumeau / porte-filtre)
  // sont comptés à part, sinon le chiffre ne tomberait jamais à 0 (41 muets, dont 5 gelés, sur la copie du 25/11).
  const chips_ms = (noms) => (noms || []).length ? `<div class="jr-chips">${noms.map((n) => `<a class="jr-chip" href="/app/maintenance-schedule/${encodeURIComponent(n)}">${esc(n)}</a>`).join("")}</div>` : "";
  const t_sans_visite = (s) => {
    if (!s) return "";
    const a = s.a_prolonger !== undefined ? s.a_prolonger : s.echeanciers;
    return tuile("Échéanciers sans visite à venir",
      `<div class="jr-chiffre" style="color:${a ? "#b91c1c" : "#166534"}">${nb(a)}</div>`
      + `<div class="jr-note" style="margin-top:0">à ranimer par la prolongation de nuit (doit valoir 0), ${nb(s.relancables)} client(s) relançable(s).</div>`
      + chips_ms(s.exemples)
      + ligne("Articles sans visite à venir, tous", `${nb(s.articles)} <span class="text-muted">· ${nb(s.echeanciers)} échéancier(s), ${nb(s.clients)} cl.</span>`)
      + (s.geles ? ligne("dont laissés gelés exprès (jumeau / porte-filtre)", nb(s.geles), true) + chips_ms(s.exemples_geles) : ""));
  };

  // 26/11/2026 : rouge = type bloquant immobile depuis le seuil de l’alerte de 07:00 ; orange = ouvert depuis le seuil
  // mais encore travaillé, ou Urgence (ce type ne bloque aucune liste).
  const t_brouillons = (bs, seuil, bloquants) => tuile(`Brouillons de listes (${nb((bs || []).length)})`, !(bs || []).length ? `<div class="jr-vide">Aucun brouillon.</div>`
    : (bs || []).map((b) => {
      const immobile = (b.inactif_jo !== null && b.inactif_jo !== undefined) ? b.inactif_jo : b.age_jo;
      const bloque = (bloquants || []).includes(b.type);
      const couleur = seuil > 0 && bloque && immobile >= seuil ? "#b91c1c" : (seuil > 0 && (immobile >= seuil || b.age_jo >= seuil) ? "#9a3412" : "");
      return ligne(`<a href="/app/liste-appelle-entretien/${encodeURIComponent(b.name)}">${esc(b.type)}</a> <span class="text-muted">du ${jm(b.cree_le)}${bloque ? "" : " · ne bloque rien"}</span>`,
        `<span style="${couleur ? "color:" + couleur : ""}">${nb(b.appelees)}/${nb(b.lignes)} appelées · ${nb(b.age_jo)} j.o.${b.inactif_jo !== null && b.inactif_jo !== undefined ? ` · immobile ${nb(b.inactif_jo)} j.o.` : ""}</span>`);
    }).join("") + `<div class="jr-note">Jours ouvrés pleins (lundi–samedi), même seuil que l’alerte de 07:00 : ${nb(seuil)} j.o.</div>`);

  // 26/11/2026 : un cron arrêté (« Stopped ») ou absent du planificateur reste affiché, avec son état.
  const t_passages = (fj, inactif) => tuile("Passages du jour",
    (inactif ? `<div class="jr-sig jr-sig-rouge" style="margin-top:0">Planificateur en pause sur ce site</div>` : "")
    + ((fj || {}).passages || []).map((p) => ligne(esc(p.titre),
      p.arrete ? `<span class="jr-badge jr-ko">arrêté</span>`
        : p.absent ? `<span class="jr-badge jr-ko">absent du planificateur</span>`
        : (p.prevus || []).length ? p.prevus.map((h) => `<span class="jr-badge ${(ETATS[h.etat] || ETATS.a_venir)[0]}">${esc(h.heure)} ${(ETATS[h.etat] || [0, esc(h.etat)])[1]}</span>`).join(" ")
        + (p.erreurs_elements ? ` <span class="jr-badge jr-warn">${nb(p.erreurs_elements)} err.</span>` : "") : `<span class="text-muted">pas de passage prévu</span>`)).join(""));

  const valeurs = (obj, cles, prefixe) => Object.entries(obj || {}).flatMap(([k, v]) => {
    const lib = (prefixe ? prefixe + " › " : "") + (cles[k] || k.replace(/_/g, " "));
    if (v && typeof v === "object" && !Array.isArray(v)) return valeurs(v, cles, lib);
    if (v === 0 || v === "" || v === null || (Array.isArray(v) && !v.length)) return [];
    return [`<span class="jr-chip">${esc(lib)} : <b>${esc(Array.isArray(v) ? v.join(", ") : (typeof v === "number" ? nb(v) : v))}</b></span>`];
  });
  const t_sources = (sources, r) => {
    const noms = Object.keys(sources || {});
    return `<div class="jr-tuile" style="grid-column:1/-1"><h4>Compteurs notés par les crons aujourd’hui</h4>` + (!noms.length
      ? `<div class="jr-vide">${r.table_prete ? "Aucun cron n’a encore noté ses compteurs aujourd’hui." : "La table « Entonnoir Relances » n’existe pas encore (migration à faire) : les crons ne peuvent pas encore noter leurs compteurs."}</div>`
      : noms.map((n) => {
        const s = sources[n] || {};
        const affiche = (s.passages || 0) > 1 ? s.dernier_passage : s.compteurs;
        const chips = valeurs(affiche, r.libelles_cles || {});
        return `<div style="margin-top:6px"><b style="font-size:12px">${esc((r.libelles_sources || {})[n] || n)}</b>
          <span class="jr-quand">· ${nb(s.passages)} passage(s), ${esc(s.premier || "")}${s.passages > 1 ? ` → ${esc(s.dernier || "")} (dernier passage ci-dessous)` : ""}</span>
          <div class="jr-chips">${chips.length ? chips.join("") : `<span class="jr-vide">tous les compteurs à 0</span>`}</div>
          ${(s.passages || 0) > 1 ? `<details><summary>Cumul des ${nb(s.passages)} passages</summary><div class="jr-chips">${valeurs(s.compteurs, r.libelles_cles || {}).join("")}</div></details>` : ""}</div>`;
      }).join("")) + `<div class="jr-note">Valeurs nulles masquées.</div></div>`;
  };

  const tableau = (rows) => {
    const cell = (v, rouge) => `<td class="${rouge ? "jr-rouge" : ""}">${nb(v)}</td>`;
    const stock = (s, k, rouge_si) => !s ? "<td>—</td>" : cell(s[k], rouge_si && s[k] > 0);
    return `<div class="jr-table-wrap"><table class="jr-table"><thead><tr>
      <th>Jour</th><th>Lignes<br>créées</th><th>dont<br>Normal+Urg.</th><th>Cochées<br>appelé</th><th>Listes<br>validées</th>
      <th>SMS 1<br>réussis</th><th>SMS 2<br>réussis</th><th>SMS en<br>échec</th><th>Machines<br>BL veille</th><th>Échéanciers<br>nés (devinés)</th>
      <th>Visites<br>échues*</th><th>En<br>attente*</th><th>Sans visite<br>à venir*</th><th>Brouillons<br>bloqués*</th><th>Passages<br>KO</th><th>Erreurs</th>
    </tr></thead><tbody>${(rows || []).map((t) => `<tr class="${t.dimanche ? "jr-dim" : ""}">
      <td>${esc(t.libelle)}</td>${cell(t.lignes)}${cell(t.lignes_flux)}${cell(t.appelees)}${cell(t.validees)}
      ${cell(t.sms1, t.sms_ko)}${cell(t.sms2, t.sms_ko)}${cell(t.sms_echecs, t.sms_echecs > 0)}
      ${cell(t.machines_veille)}<td>${nb((t.nes || {}).total)} <span class="text-muted">(${nb((t.nes || {}).devine)})</span></td>
      ${stock(t.stock, "visites_echues")}${stock(t.stock, "en_attente")}${stock(t.stock, "echeanciers_sans_visite", true)}${stock(t.stock, "brouillons_anciens", true)}
      ${cell(t.passages_ko, t.passages_ko > 0)}${cell(t.erreurs, t.erreurs > 0)}</tr>`).join("")}</tbody></table></div>`;
  };

  const charger_entonnoir = async () => {
    const $z = $(wrapper).find("#jr-entonnoir");
    let r;
    try {
      r = (await frappe.call({ method: "customization_app.journaux_relances.entonnoir" })).message || {};
    } catch (e) {
      $z.html(`<div class="jr-card"><span class="jr-badge jr-ko">Entonnoir indisponible</span> <span class="jr-vide">Voir le Journal des erreurs.</span></div>`);
      return;
    }
    const p = r.photo || {}, fl = p.flux || {}, fj = fl.jour || {}, fv = fl.veille || {};
    const sig = r.signaux || [];
    const rouges = sig.filter((s) => s.niveau === "rouge").length;
    const seuil = (r.reglages || {}).seuil_brouillon_jo, bloquants = (r.reglages || {}).types_bloquants;
    const entete = `<div class="jr-head"><div><b>Entonnoir du jour</b> <span class="jr-quand">· ${esc(jm(p.jour))} · calculé à ${esc(String(p.calcule_le || "").slice(11, 16))}`
      + `${r.photo_enregistree ? ` · photo enregistrée à ${esc(String(r.photo_enregistree).slice(11, 16))}` : (r.table_prete ? " · pas encore de photo enregistrée aujourd’hui" : "")}`
      + `${r.table_prete ? ` · <a href="/app/entonnoir-relances">📈 jour par jour</a>` : ""}</span></div>`
      + `${rouges ? `<span class="jr-badge jr-ko">${rouges} signal(aux) rouge(s)</span>` : `<span class="jr-badge jr-ok">Aucun signal rouge</span>`}</div>`;
    const signaux = sig.map((s) => `<div class="jr-sig jr-sig-${esc(s.niveau)}"><span>${s.niveau === "rouge" ? "🔴" : "🟠"}</span><span>${esc(s.texte)}${s.lien ? ` · <a href="${esc(s.lien)}">ouvrir</a>` : ""}</span></div>`).join("");
    const erreurs = (p.erreurs_calcul || []).length ? `<div class="jr-note" style="color:#b91c1c">Parties non calculées : ${(p.erreurs_calcul || []).map(esc).join(" · ")}</div>` : "";
    $z.html(`<div class="jr-card">${entete}${signaux}${erreurs}<div class="jr-tuiles">
        ${t_visites(p.visites_echues, r.statuts)}${t_appel(p.appel)}${t_listes(fj, fv)}${t_sms(fj, fv)}
        ${t_machines(fj, fv)}${t_sans_visite(p.sans_visite_a_venir)}${t_brouillons(p.brouillons, seuil, bloquants)}${t_passages(fj, p.ordonnanceur_inactif)}
        ${t_sources(r.sources, r)}</div></div>
      <div class="jr-card"><div class="jr-head"><b>Tendance des ${nb((r.tendance || []).length)} derniers jours</b>
        <span class="jr-quand">le plus récent en haut · dimanche grisé</span></div>${tableau(r.tendance)}
        <div class="jr-note">Flux relus en base : une liste purgée ou un SMS effacé par un décalage n’y sont plus (des minimums).
        Cochées appelé : traces des enregistrements, plus les RDV pris au calendrier ou dans l’app. SMS : réussis en rouge quand aucun n’est parti un jour de passage.
        * Stocks de la photo enregistrée ce jour-là (cron de 07:30 et 10:30) : « — » avant la première photo.
        Machines : BL de la veille, échéanciers nés la nuit suivante.</div></div>`);
  };

  wrapper.jr_charger = async () => {
    if (wrapper.jr_en_cours) return;
    wrapper.jr_en_cours = true;
    try {
      await Promise.all([charger_crons(), charger_entonnoir()]);
    } finally {
      wrapper.jr_en_cours = false;
    }
  };
  wrapper.jr_charger();
};
frappe.pages["journaux-relances"].on_page_show = function (wrapper) { if (wrapper.jr_charger) wrapper.jr_charger(); };
