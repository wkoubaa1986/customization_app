/**
 * « 💵 Où est l'argent liquide » (demande du 24/11/2026) — pour les responsables de collecte et la direction.
 * À une date : ce que tient chaque employé (fond gardé, compté pas encore remis, pas encore compté), ce que chaque
 * collecteur a reçu et pas encore versé, le compte Sabah Espèces, les versements en banque et les flux jour par jour.
 * Tout vient de customization_app.argent_liquide.situation (la méthode est expliquée en bas de page).
 */
frappe.pages["argent-liquide"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: __("Où est l’argent liquide"), single_column: true });
  wrapper.al = new ArgentLiquide(wrapper);
};
frappe.pages["argent-liquide"].on_page_show = function (wrapper) {
  if (wrapper.al) wrapper.al.charger();
};

class ArgentLiquide {
  constructor(wrapper) {
    this.page = wrapper.page;
    this.$c = $(wrapper).find(".layout-main-section");
    this.poser_css();
    this.champ_date = this.page.add_field({
      fieldtype: "Date", fieldname: "date", label: __("Au"), default: frappe.datetime.get_today(),
      change: () => this.charger(),
    });
    this.page.set_primary_action(__("Actualiser"), () => this.charger(), "refresh");
  }

  poser_css() {
    if (document.getElementById("al-css")) return;
    $("head").append(`<style id="al-css">
      .al { max-width: 1100px; margin: 0 auto; padding-bottom: 30px; }
      .al-tuiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 6px 0 12px; }
      .al-tuile { border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 14px; background: var(--card-bg); }
      .al-tuile .t { font-size: 12px; color: var(--text-muted); }
      .al-tuile .v { font-size: 22px; font-weight: 800; white-space: nowrap; }
      .al-tuile.total { background: #eff6ff; border-color: #bfdbfe; }
      .al-alerte { background: #fef2f2; border: 1px solid #fca5a5; color: #991b1b; border-radius: 10px; padding: 9px 12px; margin-bottom: 8px; font-weight: 600; }
      .al h4 { margin: 18px 0 6px; font-size: 15px; font-weight: 800; }
      .al table { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--card-bg); }
      .al th { text-align: left; font-size: 11.5px; color: var(--text-muted); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--border-color); }
      .al td { padding: 6px 8px; border-bottom: 1px solid var(--border-color); vertical-align: top; }
      .al .n { text-align: right; white-space: nowrap; }
      .al tr.pied td { font-weight: 800; border-top: 2px solid var(--border-color); }
      .al .muet { color: var(--text-muted); font-size: 12px; }
      .al .zero { color: var(--text-light); }
      .al-coll { border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 14px; margin-bottom: 10px; background: var(--card-bg); }
      .al-coll .calc { font-size: 13px; margin: 4px 0 8px; }
      .al-defile { overflow-x: auto; }
      .al-note { font-size: 12px; color: var(--text-muted); margin-top: 18px; line-height: 1.5; }
      @media (max-width: 768px) { .al-tuiles { grid-template-columns: repeat(2, 1fr); } .al-tuile .v { font-size: 18px; } }
    </style>`);
  }

  charger() {
    const date = this.champ_date.get_value() || frappe.datetime.get_today();
    this.$c.html(`<div class="al muet" style="padding:20px">${__("Calcul…")}</div>`);
    frappe.call({ method: "customization_app.argent_liquide.situation", args: { date },
                  callback: (r) => this.rendre(r.message || {}) });
  }

  rendre(s) {
    const esc = frappe.utils.escape_html;
    const dt = (v, zero) => {
      const n = Number(v) || 0;
      if (!n && zero !== false) return `<span class="zero">—</span>`;
      return n.toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
    };
    const jour = (iso) => (iso ? frappe.datetime.str_to_user(iso) : "");
    const t = s.totaux || {};
    const tuile = (titre, v, cls) => `<div class="al-tuile ${cls || ""}"><div class="t">${titre}</div><div class="v">${dt(v, false)}</div></div>`;

    // Qui tient l'argent : une ligne par caisse d'employé ; le collecté va sur la ligne de la caisse du collecteur.
    const lignes = (s.employes || []).map((e) => Object.assign({ collecte: 0 }, e));
    (s.collecteurs || []).forEach((x) => {
      const l = lignes.find((e) => e.caisse === x.caisse);
      if (l) { l.collecte = x.detient; l.total += x.detient; }
      else lignes.push({ caisse: x.nom, fond: 0, a_remettre: 0, non_compte: 0, collecte: x.detient, total: x.detient });
    });
    lignes.sort((a, b) => b.total - a.total);
    const somme = (k) => lignes.reduce((acc, l) => acc + (Number(l[k]) || 0), 0);
    const tableau_personnes = `
      <div class="al-defile"><table>
        <tr><th>${__("Personne")}</th><th class="n">${__("Fond gardé")}</th><th class="n">${__("Compté, pas encore remis")}</th>
            <th class="n">${__("Pas encore compté")}</th><th class="n">${__("Collecté, pas versé")}</th><th class="n">${__("Total tenu")}</th></tr>
        ${lignes.map((l) => `<tr>
          <td><b>${esc(l.caisse)}</b>${l.derniere ? `<div class="muet">${__("dernier comptage")} <a href="/app/cloture-caisse/${encodeURIComponent(l.derniere.name)}">${jour(l.derniere.date)}</a> · ${esc(l.derniere.statut)}</div>` : ""}</td>
          <td class="n">${dt(l.fond)}</td><td class="n">${dt(l.a_remettre)}</td>
          <td class="n">${dt(l.non_compte)}${l.jours_non_comptes > 1 ? `<div class="muet">${__("sur {0} jours", [l.jours_non_comptes])}</div>` : ""}</td>
          <td class="n">${dt(l.collecte)}</td><td class="n"><b>${dt(l.total)}</b></td></tr>`).join("")}
        <tr class="pied"><td>${__("Total")}</td><td class="n">${dt(somme("fond"))}</td><td class="n">${dt(somme("a_remettre"))}</td>
          <td class="n">${dt(somme("non_compte"))}</td><td class="n">${dt(somme("collecte"))}</td><td class="n">${dt(somme("total"))}</td></tr>
      </table></div>`;

    const collecteurs = (s.collecteurs || []).map((x) => {
      const sorties = Object.keys(x.sorties || {}).map((k) => `${esc(k)} ${dt(x.sorties[k], false)}`).join(" · ");
      return `<div class="al-coll">
        <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
          <b style="font-size:15px">${esc(x.nom)}</b>
          <span><b>${dt(x.detient, false)}</b> ${__("collectés, pas encore versés")}${x.depuis ? ` · ${__("depuis le {0} ({1} j)", [jour(x.depuis), x.age_jours])}` : ""}</span></div>
        <div class="calc muet">${__("Reçu aux collectes")} ${dt(x.recu, false)}${x.passations ? ` + ${__("passations")} ${dt(x.passations, false)}` : ""}${
          x.entrees_autres ? ` + ${__("retours au compte Espèces")} ${dt(x.entrees_autres, false)}` : ""} − ${__("sorties")} ${dt(x.sorties_total, false)}${sorties ? ` (${sorties})` : ""}</div>
        ${(x.detail_sorties || []).length ? `<div class="al-defile"><table>
          <tr><th>${__("Date")}</th><th>${__("Vers / depuis")}</th><th>${__("Référence")}</th><th>${__("Saisie par")}</th><th class="n">${__("Sortie")}</th></tr>
          ${x.detail_sorties.map((m) => `<tr><td>${jour(m.date)}</td><td>${esc((m.vers || "").replace(/ - A&S/g, ""))}</td>
            <td><a href="/app/journal-entry/${encodeURIComponent(m.name)}">${esc(m.reference || m.name)}</a></td><td>${esc(m.saisie_par || "")}</td>
            <td class="n">${m.montant < 0 ? "+ " + dt(-m.montant, false) : dt(m.montant, false)}</td></tr>`).join("")}
        </table><div class="muet">${__("15 derniers mouvements ; « + » = argent revenu au compte Espèces.")}</div></div>` : ""}
      </div>`;
    }).join("");

    const versements = (s.versements || []).length ? `<div class="al-defile"><table>
        <tr><th>${__("Date")}</th><th>${__("Depuis")}</th><th>${__("Banque")}</th><th>${__("Référence du bordereau")}</th><th>${__("Saisi par")}</th><th class="n">${__("Montant")}</th></tr>
        ${s.versements.map((v) => `<tr><td>${jour(v.date)}</td><td>${esc(v.depuis)}</td><td>${esc(v.banque)}</td>
          <td><a href="/app/journal-entry/${encodeURIComponent(v.name)}">${esc(v.reference || v.name)}</a></td><td>${esc(v.saisie_par || "")}</td><td class="n">${dt(v.montant, false)}</td></tr>`).join("")}
      </table></div>` : `<div class="muet">${__("Aucun versement en banque sur les 30 derniers jours.")}</div>`;

    const jours = `<div class="al-defile"><table>
        <tr><th>${__("Jour")}</th><th class="n">${__("Encaissé en espèces")}</th><th class="n">${__("Dépensé en espèces")}</th>
            <th class="n">${__("Collecté")}</th><th class="n">${__("→ Sabah Espèces")}</th><th class="n">${__("→ Banque")}</th><th class="n">${__("Autres sorties")}</th></tr>
        ${(s.jours || []).map((j) => `<tr><td>${jour(j.date)}</td><td class="n">${dt(j.encaisse)}</td><td class="n">${dt(j.depense)}</td>
          <td class="n">${dt(j.collecte)}</td><td class="n">${dt(j.sabah)}</td><td class="n">${dt(j.banque)}</td><td class="n">${dt(j.autres)}</td></tr>`).join("")}
      </table></div>`;

    const alertes = (s.collecteurs || []).flatMap((x) => (x.alertes || []).map((a) => `<div class="al-alerte">⚠️ ${esc(x.nom)} : ${esc(a)}</div>`)).join("");

    this.$c.html(`<div class="al">
      <div class="al-tuiles">
        ${tuile(__("Chez les employés"), t.employes)}
        ${tuile(__("Chez les collecteurs (collecté, pas versé)"), t.collecteurs)}
        ${tuile(__("Compte Sabah Espèces"), t.sabah)}
        ${tuile(__("Total de l’argent liquide"), t.total, "total")}
      </div>
      ${alertes}
      <h4>👥 ${__("Qui tient l’argent au {0}", [jour(s.date)])}</h4>${tableau_personnes}
      <h4>📥 ${__("Collecteurs")}</h4>${collecteurs || `<div class="muet">${__("Rien de collecté depuis la date de départ.")}</div>`}
      <h4>🏦 ${__("Versements en banque (30 derniers jours)")}</h4>${versements}
      <h4>📅 ${__("Jour par jour (14 derniers jours)")}</h4>${jours}
      <div class="al-note">${__("Méthode : toutes les caisses partagent un seul compte comptable « Espèces », qui ne dit pas qui tient les billets (son solde au {0} : {1}). La page reconstruit donc qui détient quoi à partir des comptages, des collectes et des écritures depuis la date de départ des caisses ({2}). Une écriture de sortie saisie par quelqu’un qui n’est pas collecteur est comptée chez le responsable titulaire. Alerte au-delà de {3} ou après {4} jours sans versement (Config Caisse).",
        [jour(s.date), dt(t.comptable_especes, false), jour(s.date_depart), dt(s.plafond, false), s.jours_max])}</div>
    </div>`);
  }
}
