// Réglage de l'optimisation des tournées : état du géocodage des adresses et bouton pour le lancer.
frappe.ui.form.on("Config Optimisation Tournees", {
  refresh(frm) {
    frm.add_custom_button("🧪 Comparer les géocodeurs", () => ouvrir_essais_geocodage(frm));
    frm.add_custom_button("📍 Géocoder les adresses", () => {
      frappe.confirm(
        "Contrôler les adresses à localiser avec Google et, si configuré, votre serveur Nominatim privé ? " +
        "Les positions connues sont conservées. Les résultats incertains peuvent être utilisés comme estimations pour les tournées et corrigés sur les fiches Adresse. Le plafond quotidien est partagé avec les essais.",
        async () => {
          const r = (await frappe.call({ method: "customization_app.tournee_optimisation.lancer_geocodage", args: { limite: 200 }, freeze: true })).message;
          frappe.show_alert({ message: `Géocodage lancé en tâche de fond — ${r.geocodees}/${r.total} adresses positionnées pour l’instant`, indicator: "blue" }, 6);
        }
      );
    });
    frappe.call({ method: "customization_app.tournee_optimisation.etat_geocodage" }).then((r) => {
      const e = r.message || {};
      frm.dashboard.set_headline(
        `📍 Adresses positionnées : <b>${e.geocodees}</b> / ${e.total} (avec lien Google Maps : ${e.avec_lien} · ` +
        `liens morts : ${e.liens_morts} · position approchée par le texte : ${e.approchees}). ` +
        `Les positions se contrôlent depuis la fiche Adresse → Localisation. La recherche externe nécessite l’activation explicite ci-dessous.`
      );
    });
  },
});

async function ouvrir_essais_geocodage(frm) {
  if (frm.is_dirty()) {
    frappe.msgprint("Enregistrez les réglages et les clés avant de lancer un essai.");
    return;
  }
  const api = "customization_app.geocodage_essais.";
  const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
  const etat = (await frappe.call({ method: api + "etat_services" })).message;
  const noms = Object.fromEntries(etat.services.map((s) => [s.service, s.nom]));
  let liste = [], ferme = false, actif = false, minuterie = null, suivi = null, generation = 0;
  const d = new frappe.ui.Dialog({
    title: "Comparer la recherche d’adresse", size: "extra-large",
    fields: [
      { fieldtype: "HTML", options: `<p>Les services cochés recevront le <b>texte des adresses affichées ci-dessous</b>.
        Les champs client, téléphone et position de référence ne leur sont pas transmis.</p>
        <p>Les positions existantes restent inchangées. Les références issues de liens Google Maps servent à mesurer un écart,
        mais ne sont pas toutes vérifiées sur le terrain. Résultats disponibles pendant 24 heures.</p>
        <p>${etat.services.map((s) => `<b>${esc(s.nom)}</b> : ${s.disponible ? (s.champ ? "clé enregistrée, accès à tester" : "sans clé") : "clé à renseigner"}
          · <a href="${esc(s.url)}" target="_blank" rel="noopener">${s.service === "nominatim" ? "conditions" : "compte / activation"}</a>`).join("<br>")}</p>
        <p class="text-muted">Nominatim public : petit essai ponctuel uniquement, au plus une requête par seconde, sans autocomplétion.
        ${etat.appels}/${etat.plafond} appels d’essai utilisés aujourd’hui (ce compteur ne couvre pas les autres usages des comptes fournisseurs).</p>` },
      ...etat.services.map((s) => ({ fieldtype: "Check", fieldname: s.service, label: s.nom,
        default: s.disponible ? 1 : 0, read_only: !s.disponible })),
      { fieldtype: "Section Break", label: "Adresses à comparer" },
      { fieldtype: "Int", fieldname: "limite", label: "Nombre d’adresses (1 à 50)", default: 12,
        onchange: () => setTimeout(() => { if (!ferme) charger(); }, 0) },
      { fieldtype: "HTML", fieldname: "echantillon" },
      { fieldtype: "Section Break", label: "Résultats" },
      { fieldtype: "HTML", fieldname: "resultats" },
    ],
    primary_action_label: "Lancer la comparaison",
    primary_action: async (v) => {
      const services = etat.services.filter((s) => v[s.service]).map((s) => s.service);
      if (!services.length || !liste.length) {
        frappe.msgprint("Choisissez au moins un service et attendez le chargement des adresses.");
        return;
      }
      actif = true;
      d.get_primary_btn().prop("disabled", true);
      try {
        const r = (await frappe.call({ method: api + "lancer", args: { adresses: liste.map((a) => a.adresse), services } })).message;
        suivre(r.id);
      } catch (e) {
        actif = false;
        d.get_primary_btn().prop("disabled", false);
      }
    },
  });
  const distance = (m) => m == null ? "—" : m < 1000 ? `${m} m` : `${(m / 1000).toFixed(1)} km`;
  const precisions = { ROOFTOP: "bâtiment", RANGE_INTERPOLATED: "numéro interpolé", GEOMETRIC_CENTER: "centre géométrique",
    APPROXIMATE: "approximative", building: "bâtiment", house: "bâtiment", street: "rue", road: "rue",
    city: "ville", town: "ville", village: "localité", suburb: "quartier", district: "quartier",
    primary: "voie principale", secondary: "voie secondaire", tertiary: "voie", residential: "rue",
    square: "place", tourism: "lieu touristique" };
  const candidat = (c) => `<div>${esc(c.libelle)}<br><small class="text-muted">Précision annoncée : ${esc(precisions[c.precision] || c.precision)}
    ${c.partiel ? " · correspondance partielle" : ""}${c.confiance == null ? "" : ` · indice fournisseur : ${esc(c.confiance)}`}
    · <a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(c.lat + "," + c.lng)}" target="_blank" rel="noopener">Voir le point</a></small></div>`;
  const cellule = (r) => {
    if (!r) return '<span class="text-muted">En attente</span>';
    if (r.statut === "indisponible") return `<span class="text-danger">${esc(r.message)}</span>`;
    if (r.statut === "introuvable") return '<span class="text-muted">Adresse introuvable</span>';
    return `${candidat(r.candidats[0])}<div><b>Écart : ${distance(r.ecart_m)}</b>${r.cache ? " · résultat en cache" : ""}</div>
      ${r.candidats.length > 1 ? `<details><summary>${r.candidats.length - 1} autre(s) résultat(s)</summary>${r.candidats.slice(1).map(candidat).join("<hr>")}</details>` : ""}`;
  };
  const rendre = (r) => {
    const statut = { en_attente: "En attente du traitement", en_cours: "Comparaison en cours", termine: "Comparaison terminée", erreur: "Essai interrompu" };
    d.fields_dict.resultats.$wrapper.html(`<p><b>${esc(statut[r.statut] || r.statut)}</b> · ${r.fait}/${r.total}
      ${r.message ? ` · ${esc(r.message)}` : ""}</p>
      <div class="table-responsive"><table class="table table-bordered table-condensed"><thead><tr><th>Service</th><th>Adresses trouvées</th><th>Références comparées</th><th>Écart médian</th><th>≤ 200 m</th><th>&gt; 1 km</th></tr></thead>
      <tbody>${(r.bilan || []).map((b) => `<tr><td>${esc(b.nom)}</td><td>${b.trouves}/${b.traites}</td><td>${b.references_comparees}</td><td>${distance(b.ecart_median_m)}</td><td>${b.moins_200m}</td><td>${b.plus_1000m}</td></tr>`).join("")}</tbody></table></div>
      <p class="text-muted">L’écart compare le premier résultat à la position enregistrée issue d’un lien.
      « Trouvée » ne signifie pas « bâtiment exact ». Les indices des fournisseurs ne sont pas directement comparables.</p>
      <div class="table-responsive"><table class="table table-bordered"><thead><tr><th>Adresse recherchée</th>${r.services.map((s) => `<th>${esc(noms[s] || s)}</th>`).join("")}</tr></thead>
      <tbody>${r.lignes.map((l) => `<tr><td style="min-width:180px">${esc(l.texte)}<br><small>${l.reference ? "Référence issue d’un lien" : "Sans référence précise"}</small></td>
        ${r.services.map((s) => `<td style="min-width:210px">${cellule(l.resultats[s])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="text-muted">Données : © OpenStreetMap contributors, Geoapify, <a href="https://locationiq.com" target="_blank" rel="noopener">Search by LocationIQ.com</a>, Google selon les services sélectionnés.</p>`);
  };
  const suivre = async (id) => {
    suivi = id;
    clearTimeout(minuterie);
    try {
      const r = (await frappe.call({ method: api + "resultat", args: { identifiant: id } })).message;
      if (ferme || suivi !== id) return;
      rendre(r);
      actif = ["en_attente", "en_cours"].includes(r.statut);
      d.get_primary_btn().prop("disabled", actif || !liste.length);
      if (actif) minuterie = setTimeout(() => suivre(id), 2000);
    } catch (e) {
      actif = false;
      if (!ferme) d.get_primary_btn().prop("disabled", !liste.length);
    }
  };
  const charger = async () => {
    const courant = ++generation;
    liste = [];
    d.get_primary_btn().prop("disabled", true);
    try {
      const r = (await frappe.call({ method: api + "echantillon", args: { limite: d.get_value("limite") || 12 } })).message;
      if (ferme || courant !== generation) return;
      liste = r;
      d.fields_dict.echantillon.$wrapper.html(`<details open><summary>${liste.length} adresse(s), dont ${liste.filter((a) => a.reference).length} avec une référence issue d’un lien</summary>
        <ol>${liste.map((a) => `<li><a href="/app/address/${encodeURIComponent(a.adresse)}" target="_blank" rel="noopener">${esc(a.texte)}</a>
          <small class="text-muted"> — ${a.reference ? "référence disponible" : "à localiser"}</small></li>`).join("")}</ol></details>`);
      d.get_primary_btn().prop("disabled", actif || !liste.length);
    } catch (e) {
      d.fields_dict.echantillon.$wrapper.text("Impossible de lire l’échantillon. Vérifiez vos permissions sur les adresses.");
    }
  };
  d.$wrapper.on("hidden.bs.modal", () => { ferme = true; clearTimeout(minuterie); });
  if (etat.dernier) {
    d.set_secondary_action_label("Dernier essai");
    d.set_secondary_action(() => suivre(etat.dernier));
  }
  d.show();
  // Appliquer explicitement les cases : Dialog ne reprend pas toujours leur défaut.
  etat.services.forEach((s) => d.set_value(s.service, s.disponible ? 1 : 0));
  await charger();
}
