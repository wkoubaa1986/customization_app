// Journaux des relances : dernier passage, résumé du fichier journal et erreurs récentes de chaque cron de l’entretien.
frappe.pages["journaux-relances"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Journaux des relances", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("journaux_relances", {}));
  wrapper.jr_charger = async () => {
    const r = (await frappe.call({ method: "customization_app.journaux_relances.resume", args: { jours: 2 } })).message || {};
    const esc = frappe.utils.escape_html;
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
        : `<div class="jr-vide" style="margin-top:6px">${c.journal ? `Journal ${esc(c.journal)}.log vide pour l’instant (il se remplit au prochain passage).` : "Ce cron laisse ses traces en commentaires sur les tâches (📲 Rappel SMS)."}</div>`}
      ${c.erreurs && c.erreurs.length ? `<div class="jr-err">${c.erreurs.map((e) => `<div><a href="/app/error-log/${encodeURIComponent(e.name)}">${esc(e.method)}</a> <span class="text-muted">${esc(String(e.creation).slice(0, 16))}</span></div>`).join("")}</div>` : ""}
      ${c.passages && c.passages.length > 1 ? `<div class="text-muted" style="font-size:11px;margin-top:6px">Passages précédents : ${c.passages.slice(1).map((p) => `${esc(p.status)} ${esc(String(p.creation).slice(0, 16))}`).join(" · ")}</div>` : ""}
    </div>`).join(""));
  };
  wrapper.jr_charger();
};
frappe.pages["journaux-relances"].on_page_show = function (wrapper) { if (wrapper.jr_charger) wrapper.jr_charger(); };
