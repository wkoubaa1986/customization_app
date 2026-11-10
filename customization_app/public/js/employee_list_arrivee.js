// Liste des employés : « Nouveau technicien » (arrivee_employe.py, 10/11/2026).
// Un seul dialogue : utilisateur avec le profil Technicien, fiche, entrepôt + vérification + seuil, réglages,
// caisse, récupération, écran simplifié du téléphone.
frappe.listview_settings["Employee"] = frappe.listview_settings["Employee"] || {};
(function () {
  const precedent = frappe.listview_settings["Employee"].onload;
  frappe.listview_settings["Employee"].onload = function (listview) {
    if (precedent) precedent(listview);
    const droit = frappe.user.has_role("System Manager") || frappe.user.has_role("HR Manager") || frappe.user.has_role("Congés RH");
    if (!droit) return;
    listview.page.add_inner_button(__("➕ Nouveau technicien"), () => rcj_nouveau_technicien(listview));
  };
})();

function rcj_nouveau_technicien(listview) {
  frappe.call({ method: "customization_app.arrivee_employe.options_arrivee", callback: (r) => rcj_dialogue_technicien(listview, r.message || {}) });
}

function rcj_dialogue_technicien(listview, opts) {
  const JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
  const approbateurs = opts.approbateurs || [];
  const d = new frappe.ui.Dialog({
    title: __("Nouveau technicien"),
    fields: [
      { fieldtype: "Data", fieldname: "nom", label: __("Prénom et nom"), reqd: 1 },
      { fieldtype: "Data", fieldname: "email", label: __("E-mail (identifiant)"), options: "Email", reqd: 1 },
      { fieldtype: "Password", fieldname: "mot_de_passe", label: __("Mot de passe"), reqd: 1 },
      { fieldtype: "Data", fieldname: "telephone", label: __("Téléphone"), options: "Phone" },
      { fieldtype: "Column Break" },
      { fieldtype: "Date", fieldname: "date_entree", label: __("Date d'entrée"), default: frappe.datetime.get_today(), reqd: 1 },
      { fieldtype: "Select", fieldname: "genre", label: __("Genre"), options: "Male\nFemale", default: "Male" },
      { fieldtype: "Date", fieldname: "date_naissance", label: __("Date de naissance") },
      { fieldtype: "Check", fieldname: "ecran_simplifie", label: __("Écran simplifié du téléphone (Ma journée, agenda, mon stock, ma caisse, clôture mobile)"), default: 1 },
      { fieldtype: "Color", fieldname: "couleur", label: __("Sa couleur dans le calendrier"), default: opts.couleur_proposee || "",
        description: __("Proposée : une couleur que personne n'a. Modifiable ensuite sur sa fiche.") },
      { fieldtype: "Section Break", label: __("RH et calendrier") },
      { fieldtype: "Select", fieldname: "leave_approver", label: __("Approbateur de ses congés"), reqd: 1,
        options: approbateurs.map((a) => a.value).join("\n"), default: approbateurs.length ? approbateurs[0].value : "",
        description: approbateurs.map((a) => `${a.label} (${a.value})`).join(" · ") },
      { fieldtype: "Column Break" },
      { fieldtype: "MultiCheck", fieldname: "calendrier_visible_par", label: __("Son calendrier est visible par"), columns: 2,
        options: (opts.employes || []).map((e) => ({ label: e.label, value: e.value, checked: false })),
        description: __("Les collègues qui verront ses tâches. La direction voit tout ; lui-même toujours.") },
      { fieldtype: "MultiCheck", fieldname: "voit_calendrier_de", label: __("Il voit le calendrier de"), columns: 2,
        options: (opts.employes || []).map((e) => ({ label: e.label, value: e.value, checked: false })),
        description: __("Ne cochez personne : il ne voit que ses propres tâches.") },
      { fieldtype: "Section Break", label: __("Stock et tournées") },
      { fieldtype: "Select", fieldname: "jour_verification", label: __("Vérification du stock, le"), options: JOURS.join("\n"), default: "Lundi" },
      { fieldtype: "Select", fieldname: "periodicite", label: __("Périodicité"), options: "Chaque semaine\nToutes les deux semaines", default: "Chaque semaine" },
      { fieldtype: "Column Break" },
      { fieldtype: "Data", fieldname: "domicile_lien", label: __("Domicile (lien Google Maps, point de départ des tournées)"),
        description: __("Facultatif : collez un lien Google Maps ouvert sur le lieu.") },
      { fieldtype: "Data", fieldname: "vehicule", label: __("Véhicule GPS (immatriculation et modèle)"), description: __("Facultatif") },
      { fieldtype: "Data", fieldname: "cbox", label: __("Boîtier GPS (cbox)") },
      { fieldtype: "Section Break", label: __("Congés et récupération") },
      { fieldtype: "Float", fieldname: "conges_payes", label: __("Congés payés jusqu'au 31/12 (jours)"),
        description: __("Vide : 1,5 jour par mois restant, mois d'entrée compris. 0 : rien pour l'instant.") },
      { fieldtype: "Column Break" },
      { fieldtype: "Date", fieldname: "premier_jour_recup", label: __("Premier jour de récupération (puis toutes les deux semaines)"),
        description: __("Facultatif : laissez vide pour régler la règle plus tard dans Congés & récupérations.") },
    ],
    primary_action_label: __("Créer le technicien"),
    primary_action(v) {
      if (v.vehicule && !v.cbox) { frappe.msgprint(__("Indiquez le boîtier GPS du véhicule, ou laissez les deux vides.")); return; }
      frappe.call({
        method: "customization_app.arrivee_employe.creer_technicien",
        args: { nom: v.nom, email: v.email, mot_de_passe: v.mot_de_passe, telephone: v.telephone, date_entree: v.date_entree,
                genre: v.genre, date_naissance: v.date_naissance, domicile_lien: v.domicile_lien, vehicule: v.vehicule, cbox: v.cbox,
                jour_verification: v.jour_verification, periodicite: v.periodicite, premier_jour_recup: v.premier_jour_recup,
                ecran_simplifie: v.ecran_simplifie ? 1 : 0, leave_approver: v.leave_approver || null,
                calendrier_visible_par: JSON.stringify(d.get_value("calendrier_visible_par") || []),
                voit_calendrier_de: JSON.stringify(d.get_value("voit_calendrier_de") || []),
                conges_payes: (v.conges_payes === undefined || v.conges_payes === null || v.conges_payes === "") ? null : v.conges_payes,
                couleur: v.couleur || null },
        freeze: true, freeze_message: __("Création en cours…"),
        callback: (r) => {
          d.hide();
          const m = r.message || {};
          const esc = frappe.utils.escape_html;
          frappe.msgprint({
            title: __("Technicien créé"), indicator: "green",
            message: `<div><b>${esc(m.employe || "")}</b> — ${esc(m.utilisateur || "")} — ${esc(m.entrepot || "")}</div>
                      <ul>${(m.journal || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`,
            primary_action: { label: __("Ouvrir la fiche"), action: () => frappe.set_route("Form", "Employee", m.employe) },
          });
          if (listview && listview.refresh) listview.refresh();
        },
      });
    },
  });
  d.show();
  // Le navigateur remplissait e-mail et mot de passe avec CEUX DE L'UTILISATEUR CONNECTÉ (vu le 10/11/2026) :
  // ce sont les identifiants du NOUVEAU technicien, jamais à pré-remplir.
  ["email", "mot_de_passe"].forEach((f) => {
    const $i = d.fields_dict[f] && d.fields_dict[f].$input;
    if ($i) { $i.attr("autocomplete", "new-password").val(""); d.set_value(f, ""); }
  });
}
