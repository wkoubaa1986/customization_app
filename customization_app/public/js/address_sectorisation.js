// Fiche Adresse : gouvernorat, ville et secteur.
//
// Remplace le Client Script « Sectorisation Adresse » qui vivait en base (désactivé par le patch
// aligner_gouvernorat_ville_adresses). Ses trois défauts, constatés le 20/11/2026 :
//   - il ne chargeait les listes que lorsque le pays CHANGEAIT : sur une adresse existante, « Gouvernorats Tunisie »
//     et « Villes Tunisie » n'avaient aucun choix et s'affichaient vides, même remplis en base ;
//   - à l'enregistrement il recopiait le champ visible dans Ville même quand il était vide : « City/Town is
//     required », l'adresse ne s'enregistrait plus (2 216 adresses créées par la saisie rapide du client) ;
//   - il forçait le pays à Tunisia à chaque ouverture : une adresse étrangère basculait sans qu'on le demande.
//
// Ici : les listes sont chargées à CHAQUE ouverture depuis la table du serveur (adresse_sectorisation.referentiel,
// la même que la saisie rapide et le portail) ; les champs de la fiche vides sont repris depuis Gouvernorat / Ville
// sans marquer la fiche comme modifiée ; une valeur hors liste reste affichée ; le pays n'est jamais forcé ; on ne
// vide jamais Ville. Le serveur (adresse_sectorisation.valider) garde de toute façon les deux jeux de champs alignés.
(() => {
  const TUNISIE = "Tunisia";
  let table = null, attente = null;
  const charger = () => attente || (attente = frappe.xcall("customization_app.adresse_sectorisation.referentiel")
    .then(r => { table = r || {}; return table; })
    .catch(() => { attente = null; table = table || {}; return table; }));

  const norme = s => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const officiel = (liste, nom) => liste.find(x => norme(x) === norme(nom)) || null;
  const tunisie = frm => frm.doc.country === TUNISIE;
  const gouvernorats = () => Object.keys(table || {});
  const villes = g => Object.keys((table || {})[g] || {});
  // la liste, précédée du choix vide, et la valeur enregistrée même si la table ne la connaît pas
  const avec = (liste, valeur) => [""].concat(liste, valeur && !liste.includes(valeur) ? [valeur] : []);
  const CHAMPS = ["custom_state_s", "custom_villes_s", "custom_state_d", "custom_villes_d"];

  // Les champs de la fiche vides alors que Gouvernorat / Ville sont remplis (adresse créée par la saisie rapide,
  // la tournée, un import) : on les montre. Écrit dans le document sans passer par set_value : la fiche ne devient
  // pas « non enregistrée » pour autant, et rien ne part au serveur tant que l'utilisateur ne modifie rien.
  // Seulement sur une fiche telle qu'elle vient du serveur : dès que l'utilisateur a touché quelque chose, un champ
  // vide est SON choix. Sans cette garde, après un enregistrement refusé (gouvernorat changé, ville pas encore
  // rechoisie) la fiche se rafraîchit et reprenait l'ancienne ville sous le nouveau gouvernorat.
  function reprendre(frm) {
    const d = frm.doc;
    if (frm.is_new() || frm.is_dirty()) return;
    if (tunisie(frm)) {
      if (!d.custom_state_s && d.state) d.custom_state_s = officiel(gouvernorats(), d.state) || d.state;
      if (!d.custom_villes_s && d.city) d.custom_villes_s = officiel(villes(d.custom_state_s), d.city) || d.city;
    } else {
      if (!d.custom_state_d && d.state) d.custom_state_d = d.state;
      if (!d.custom_villes_d && d.city) d.custom_villes_d = d.city;
    }
  }

  function listes(frm) {
    frm.set_df_property("custom_state_s", "options", avec(gouvernorats(), frm.doc.custom_state_s));
    frm.set_df_property("custom_villes_s", "options", avec(villes(frm.doc.custom_state_s), frm.doc.custom_villes_s));
  }

  function affichage(frm) {
    const tn = tunisie(frm);
    frm.toggle_display(["custom_state_s", "custom_villes_s"], tn);
    frm.toggle_display(["custom_state_d", "custom_villes_d"], !tn);
    frm.toggle_reqd("custom_state_s", tn);
    frm.toggle_reqd("custom_villes_s", tn);
    frm.toggle_reqd("custom_villes_d", !tn);
  }

  // Gouvernorat / Ville (champs standard, en lecture seule sur la fiche) suivent le champ visible — jamais vidés.
  function recopier(frm) {
    const d = frm.doc, tn = tunisie(frm);
    const g = tn ? d.custom_state_s : d.custom_state_d, v = tn ? d.custom_villes_s : d.custom_villes_d;
    if (g) d.state = g;
    if (v) d.city = v;
    frm.refresh_field("state");
    frm.refresh_field("city");
  }

  const secteur_de = frm => ((table || {})[frm.doc.custom_state_s] || {})[frm.doc.custom_villes_s] || "";

  async function installer(frm) {
    await charger();
    reprendre(frm);
    listes(frm);
    affichage(frm);
    CHAMPS.forEach(c => frm.refresh_field(c));
  }

  frappe.ui.form.on("Address", {
    async refresh(frm) {
      if (frm.is_new() && !frm.doc.country) await frm.set_value("country", TUNISIE);
      await installer(frm);
    },

    async country(frm) {
      await charger();
      listes(frm);
      affichage(frm);
      CHAMPS.forEach(c => frm.refresh_field(c));
      frm.set_value("custom_secteur", tunisie(frm) ? secteur_de(frm) : "Hors Secteur");
    },

    async custom_state_s(frm) {
      await charger();
      // la ville d'un autre gouvernorat ne vaut plus : on la retire AVANT de refaire la liste
      if (frm.doc.custom_villes_s && !villes(frm.doc.custom_state_s).includes(frm.doc.custom_villes_s)) {
        await frm.set_value("custom_villes_s", "");
      }
      listes(frm);
      frm.refresh_field("custom_villes_s");
      recopier(frm);
    },

    async custom_villes_s(frm) {
      await charger();
      const ville = frm.doc.custom_villes_s, secteur = secteur_de(frm);
      if (secteur) frm.set_value("custom_secteur", secteur);
      else if (!ville) frm.set_value("custom_secteur", "");
      recopier(frm);
    },

    custom_state_d: recopier,
    custom_villes_d: recopier,

    validate: recopier,
  });
})();
