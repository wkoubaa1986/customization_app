// Ouvrir n'importe quel document dans une FENÊTRE, sans quitter l'écran courant.
//
// POURQUOI CE FICHIER EXISTE, ET POURQUOI IL EST PARTAGÉ
// -------------------------------------------------------
// Les tableaux de bord (Livraisons Aramex, Relance paiements) listent des pièces — commande,
// facture, bon de livraison, écriture de paiement — qu'on veut ouvrir et corriger sans perdre sa
// période, ses filtres et sa place dans la liste. Naviguer vers la pièce fait tout reperdre.
//
// La mise au point de cette fenêtre a déjà coûté deux corrections après coup : la barre qui porte
// le bouton ENREGISTRER, puis le bandeau Raven. Chaque copie de ce code est une occasion de n'en
// corriger qu'une la prochaine fois — d'où un seul exemplaire, chargé à la demande par
// `frappe.require("/assets/customization_app/js/ouvrir_document.js")`.

window.customization_app = window.customization_app || {};

customization_app.ouvrir_document = function (doctype, nom, options) {
  const opts = options || {};
  const titre = opts.titre || `${doctype} · ${nom}`;
  const d = new frappe.ui.Dialog({ title: titre, size: "extra-large" });

  // opts.url : ouvrir un NOUVEAU document pré-rempli — /app/<slug>/new?champ=valeur
  // (frappe verse les paramètres de l'URL dans route_options du formulaire neuf).
  const src = opts.url || `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(nom)}`;
  d.$body.html(
    `<iframe class="ca-cadre-document"
             src="${frappe.utils.escape_html(src)}"
             title="${frappe.utils.escape_html(titre)}"
             style="width:100%;height:72vh;border:0"></iframe>`
  );

  d.$body.find("iframe").on("load", function () {
    try {
      const doc = this.contentDocument;
      const style = doc.createElement("style");
      // ⚠️ ON NE MASQUE JAMAIS `.page-head`. Cette barre porte ENREGISTRER, Valider et le menu du
      // formulaire : la cacher laisse une fiche qu'on peut modifier et jamais sauver — le clic ne
      // rate pas, il n'y a simplement plus rien à cliquer. Elle est collante sous la barre de
      // navigation, qui, elle, disparaît : on la recale en haut, sinon elle flotte dans le vide.
      //
      // ⚠️ RAVEN SE DÉFEND DE DEUX FAÇONS. Le bandeau de discussion se greffe sur `.main-section`
      // et recouvre le bas du formulaire. Il se monte APRÈS ce style (chargement asynchrone sur
      // `app_ready`), mais une règle CSS vaut aussi pour ce qui naît plus tard : le masquer par
      // classe suffit. Sa marge de 60 px, elle, est posée en style EN LIGNE — seul un `!important`
      // en vient à bout.
      style.textContent =
        ".navbar, footer, .layout-side-section, .raven-chat { display: none !important; }" +
        ".page-head { top: 0 !important; }" +
        ".page-container, .main-section { padding-top: 0 !important;" +
        " padding-bottom: 0 !important; }";
      doc.head.appendChild(style);
    } catch (e) {
      // Rien à faire : le formulaire s'affiche simplement avec toutes ses barres.
    }
    // opts.au_chargement(fenêtre) : agir DANS le formulaire ouvert (ex. poser
    // une valeur qu'une cascade de remplissage a écrasée après les paramètres
    // d'URL — cas de delivery_date sur une nouvelle commande).
    try {
      opts.au_chargement && opts.au_chargement(this.contentWindow);
    } catch (e) {
      // Fenêtre inaccessible : tant pis, le champ restera à remplir à la main.
    }
  });

  // ⚠️ ON RELIT À LA FERMETURE SANS CHERCHER À SAVOIR SI QUELQUE CHOSE A BOUGÉ. Un statut, une
  // adresse, un montant corrigé dans la fenêtre changent ce que la liste affiche, et parier sur
  // « rien n'a changé » serait faux un jour sur deux.
  d.onhide = () => opts.a_la_fermeture && opts.a_la_fermeture();
  d.show();
  return d;
};

// Le chemin est répété dans chaque page qui s'en sert : le poser ici évite les fautes de frappe.
customization_app.CHEMIN_OUVRIR_DOCUMENT = "/assets/customization_app/js/ouvrir_document.js";
