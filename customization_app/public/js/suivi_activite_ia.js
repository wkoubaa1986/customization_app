/* Formalisation partagée par les activités et les réunions. Aucune écriture avant la relecture. */
(() => {
  const API = "customization_app.formalisation_activite";
  const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
  const modes = { "Clarifier ma tâche": "clarifier", "Répartir le travail": "repartir", "Vérifier ma tâche": "verifier" };
  const description = (p) => [["Objectif", p.objectif], ["Résultat attendu", p.resultat_attendu], ["Critère de fin", p.critere_fin]]
    .filter(([, v]) => v).map(([k, v]) => `${k} :\n${v}`).join("\n\n");

  function equipe_picker($zone, equipe, choisis, fixes = []) {
    $zone.html(`<label class="control-label">Employés concernés</label><div class="sa-ia-equipe" style="display:flex;flex-wrap:wrap;gap:8px 14px">${equipe.map((e) =>
      `<label style="font-weight:normal"><input type="checkbox" value="${esc(e.name)}" ${choisis.has(e.name) ? "checked" : ""} ${fixes.includes(e.name) ? "disabled" : ""}> ${esc(e.employee_name)}</label>`).join("") || '<span class="text-muted">Aucun employé disponible dans le réglage du suivi.</span>'}</div>`);
    $zone.off("change.saia").on("change.saia", "input", (e) => {
      e.target.checked ? choisis.add(e.target.value) : choisis.delete(e.target.value);
    });
  }

  window.sa_formaliser_ia = async function (opts) {
    const ctx = opts.ctx;
    let cible = opts.name || null, sequence = 0, chargement = false, en_cours = false;
    let equipe = (ctx.employes || []).slice(), fixes = [], choisies = new Set((opts.employes || []).filter((n) => equipe.some((e) => e.name === n)));
    if (!ctx.responsable && ctx.moi) choisies.add(ctx.moi);
    const choix_destination = !!opts.reunion && !opts.note_liee;
    const d = new frappe.ui.Dialog({
      title: "Formaliser avec l’IA", size: "large",
      fields: [
        { fieldtype: "Select", fieldname: "mode", label: "Comment l’IA peut vous aider", options: Object.keys(modes), default: opts.mode || "Clarifier ma tâche" },
        { fieldtype: "Small Text", fieldname: "texte", label: opts.reunion ? "Décision ou extrait de la minute" : "Besoin et consignes",
          default: opts.texte || opts.description || opts.titre || "", reqd: 1,
          description: "Décrivez le résultat souhaité et ce qui est déjà convenu. Une décision à la fois ; les informations manquantes seront signalées." },
        { fieldtype: "Select", fieldname: "destination", label: "Destination", options: ["Créer une activité", "Compléter une activité"],
          default: cible ? "Compléter une activité" : "Créer une activité", hidden: !choix_destination,
          onchange: () => actualiser_destination() },
        { fieldtype: "Link", fieldname: "cible", label: "Activité à compléter", options: "Activite Employe", default: cible,
          hidden: !choix_destination || !cible, read_only: !choix_destination,
          get_query: () => ({ filters: { statut: ["not in", ["Terminée", "Annulée"]] } }),
          onchange: () => charger_cible(d.get_value("cible")) },
        { fieldtype: "HTML", fieldname: "equipe" },
        { fieldtype: "Date", fieldname: "date_prevue", label: "Échéance convenue (facultative)", default: opts.date_prevue },
        { fieldtype: "Select", fieldname: "priorite", label: "Priorité de la nouvelle activité", options: ctx.priorites, default: opts.priorite || "Normale", hidden: !!cible },
      ],
      primary_action_label: "Préparer la proposition",
      primary_action: async (v) => {
        if (en_cours || chargement) return;
        if (choix_destination && v.destination === "Compléter une activité" && !cible) {
          return frappe.msgprint("Choisissez l’activité à compléter.");
        }
        en_cours = true;
        d.get_primary_btn().prop("disabled", true);
        try {
          const args = { texte: v.texte, mode: modes[v.mode], name: cible, employes: Array.from(choisies),
            reunion: opts.reunion, note: opts.note, date_prevue: v.date_prevue || null };
          if (!choix_destination && cible === opts.name) {
            args.titre = opts.titre; args.description = opts.description; args.date_debut = opts.date_debut;
          }
          const { message: p } = await frappe.call({ method: API + ".proposer", args, freeze: true, freeze_message: "L’IA prépare votre proposition…" });
          relire(p, { ...opts, priorite: v.priorite }, d, equipe, choisies, fixes);
        } finally { en_cours = false; d.get_primary_btn().prop("disabled", false); }
      },
    });
    const peindre_equipe = () => equipe_picker(d.fields_dict.equipe.$wrapper, equipe, choisies, ctx.responsable ? fixes : Array.from(choisies));
    const charger_cible = async (name) => {
      const tour = ++sequence;
      chargement = true;
      cible = name || null;
      d.get_primary_btn().prop("disabled", true);
      try {
        fixes = [];
        if (cible) {
          const { message: a } = await frappe.call({ method: "customization_app.suivi_activite.get_activite", args: { name: cible } });
          if (tour !== sequence) return;
          equipe = (a.employes_disponibles || equipe).slice();
          fixes = (a.affectations || []).filter((r) => r.actif !== false).map((r) => r.employe);
          (a.affectations || []).filter((r) => r.actif !== false).forEach((r) => { if (!equipe.some((e) => e.name === r.employe)) equipe.push({ name: r.employe, employee_name: r.nom }); });
          choisies = new Set([...fixes, ...(opts.employes || []).filter((n) => equipe.some((e) => e.name === n))]);
          await d.set_value("date_prevue", opts.date_prevue || a.date_prevue || "");
        } else {
          choisies = new Set((opts.employes || []).filter((n) => equipe.some((e) => e.name === n)));
          if (!ctx.responsable && ctx.moi) choisies.add(ctx.moi);
          await d.set_value("date_prevue", opts.date_prevue || "");
        }
        peindre_equipe();
        d.set_df_property("priorite", "hidden", !!cible);
      } finally { if (tour === sequence) { chargement = false; d.get_primary_btn().prop("disabled", false); } }
    };
    const actualiser_destination = () => {
      const existante = d.get_value("destination") === "Compléter une activité";
      d.set_df_property("cible", "hidden", !existante);
      charger_cible(existante ? d.get_value("cible") : null);
    };
    peindre_equipe();
    d.show();
    if (cible) await charger_cible(cible);
  };

  function relire(p, opts, configuration, equipe, selection, fixes) {
    const verification = p.mode === "verifier", choisis = new Set([...selection, ...p.equipe.map((e) => e.name)]);
    const etapes = (p.etapes || []).map((e) => ({ ...e }));
    const remarques = [...new Set([...(p.points_a_preciser || []), ...(p.alertes || [])])];
    const actuels = p.name ? `<details style="margin:8px 0"><summary>Voir l’activité actuelle : ${esc(p.titre_actuel)}</summary><div style="white-space:pre-wrap;margin-top:8px">${esc(p.description_actuelle || "Sans description")}</div></details>` : "";
    let en_cours = false, appliquee = false;
    const d = new frappe.ui.Dialog({
      title: verification ? "Vérification de la tâche" : "Relire et ajuster la proposition", size: "extra-large",
      fields: [
        { fieldtype: "HTML", fieldname: "diagnostic", options: `<div class="sa-ia-diagnostic">${remarques.length ?
          `<b>Points à relire ou à préciser</b><ul>${remarques.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` :
          "Aucun point supplémentaire signalé par l’IA. Vérifiez les consignes avant de continuer."}${actuels}</div>` },
        { fieldtype: "Check", fieldname: "appliquer_titre", label: "Appliquer le titre", default: p.name && opts.reunion ? 0 : 1, hidden: verification || !p.name },
        { fieldtype: "Data", fieldname: "titre", label: "Titre de l’activité", default: p.titre || p.titre_actuel, hidden: verification },
        { fieldtype: "Check", fieldname: "appliquer_description", label: opts.reunion ? "Ajouter cette décision à la description" : "Appliquer la description", default: 1, hidden: verification || !p.name },
        { fieldtype: "Small Text", fieldname: "description", label: "Objectif, résultat attendu et critère de fin", default: description(p), hidden: verification },
        { fieldtype: "HTML", fieldname: "equipe", hidden: verification },
        { fieldtype: "Check", fieldname: "appliquer_date", label: "Modifier l’échéance de l’activité", default: p.name && p.date_prevue !== p.date_prevue_actuelle ? 1 : 0, hidden: verification || !p.name },
        { fieldtype: "Date", fieldname: "date_prevue", label: "Échéance (facultative)", default: p.date_prevue, hidden: verification },
        { fieldtype: "HTML", fieldname: "etapes", hidden: verification },
      ],
      primary_action_label: verification ? "Fermer" : p.name ? "Appliquer les modifications sélectionnées" : "Créer l’activité",
      primary_action: async (v) => {
        if (verification) return d.hide();
        if (en_cours) return;
        if (!choisis.size) return frappe.msgprint("Sélectionnez au moins un employé.");
        const lignes = lire_etapes();
        if ((!p.name || v.appliquer_titre) && !(v.titre || "").trim()) return frappe.msgprint("Donnez un titre à l’activité.");
        if (lignes.some((e) => !e.libelle)) return frappe.msgprint("Renseignez le libellé de chaque étape sélectionnée.");
        if (lignes.some((e) => e.responsables.some((n) => !choisis.has(n)))) return frappe.msgprint("Un responsable d’étape n’est plus dans l’équipe sélectionnée.");
        en_cours = true;
        d.get_primary_btn().prop("disabled", true);
        try {
          const data = { ...v, name: p.name, modified: p.modified, operation_id: p.operation_id, mode: p.mode,
            reunion: p.reunion, note: p.note, note_texte: p.note_texte, texte_source: p.texte_source,
            employes: Array.from(choisis), etapes: lignes, date_debut: p.date_debut, priorite: opts.priorite };
          const { message: resultat } = await frappe.call({ method: API + ".appliquer", args: { data }, freeze: true, freeze_message: "Enregistrement de l’activité…" });
          appliquee = true;
          d.hide(); configuration.hide();
          frappe.show_alert({ message: p.name ? "Activité mise à jour" : "Activité créée", indicator: "green" });
          if (opts.on_success) await opts.on_success(resultat);
        } finally { en_cours = false; d.get_primary_btn().prop("disabled", false); }
      },
    });
    if (!verification) equipe_picker(d.fields_dict.equipe.$wrapper, equipe, choisis, opts.ctx.responsable ? fixes : Array.from(choisis));
    const $etapes = d.fields_dict.etapes.$wrapper;
    const lire_etapes = () => $etapes.find(".sa-ia-ligne").map((i, el) => {
      const $r = $(el);
      if (!$r.find(".sa-ia-garder").is(":checked")) return null;
      return { source: etapes[i].source || null, libelle: ($r.find(".sa-ia-libelle").val() || "").trim(),
        responsables: $r.find(".sa-responsables input:checked").map((_, el) => el.value).get(), date_prevue: $r.find(".sa-ia-date").val() || null };
    }).get();
    const peindre_etapes = () => {
      $etapes.html(`<style>.sa-ia-ligne{display:grid;grid-template-columns:24px minmax(160px,1fr) 185px 145px;gap:8px;align-items:start;margin:8px 0}.sa-ia-ligne .form-control{font-size:12px}.sa-ia-ligne textarea.sa-ia-libelle{height:62px;min-height:62px}.sa-ia-ligne small{display:block;color:var(--text-muted)}@media(max-width:650px){.sa-ia-ligne{grid-template-columns:24px 1fr}.sa-ia-ligne>.sa-responsables,.sa-ia-ligne input[type=date]{grid-column:2;width:100%}}</style>
        <label class="control-label">Étapes proposées</label><p class="small text-muted">Cochez les propositions à appliquer. Les étapes existantes non sélectionnées et les étapes déjà faites sont conservées.</p>
        ${etapes.map((e) => `<div class="sa-ia-ligne"><input class="sa-ia-garder" type="checkbox" ${e.garder === false ? "" : "checked"} aria-label="Appliquer cette étape">
          <div><textarea class="form-control sa-ia-libelle" rows="2" maxlength="140" aria-label="Libellé de l’étape">${esc(e.libelle)}</textarea><small>${e.source ? "Modifier une étape existante" : "Nouvelle étape"}</small></div>
          ${frappe.suivi_responsables_html(equipe, e.responsables || (e.responsable ? [e.responsable] : []))}
          <input class="form-control sa-ia-date" type="date" value="${esc(e.date_prevue || "")}" aria-label="Échéance de l’étape"></div>`).join("")}
        ${etapes.length < 12 ? '<button type="button" class="btn btn-default btn-xs sa-ia-ajouter">Ajouter une étape</button>' : ""}`);
    };
    if (!verification) peindre_etapes();
    $etapes.on("click", ".sa-ia-ajouter", () => {
      $etapes.find(".sa-ia-ligne").each((i, el) => {
        const $r = $(el);
        Object.assign(etapes[i], { garder: $r.find(".sa-ia-garder").is(":checked"), libelle: $r.find(".sa-ia-libelle").val(),
          responsables: $r.find(".sa-responsables input:checked").map((_, el) => el.value).get(), date_prevue: $r.find(".sa-ia-date").val() });
      });
      etapes.push({ libelle: "", source: null }); peindre_etapes();
      $etapes.find(".sa-ia-libelle").last().focus();
    });
    d.$wrapper.one("hidden.bs.modal", () => { if (!appliquee) configuration.show(); });
    configuration.$wrapper.one("hidden.bs.modal", () => d.show());
    configuration.hide();
  }
})();
