// Bascule d'une fiche de caisse payée par erreur vers « Pas payé ». Chargé PARTOUT dans le Desk
// (app_include_js) : le bouton vit sur la fiche « Facture Achat a Saisir » ET sur la facture
// d'achat — un doctype_js de l'une n'est pas chargé sur l'autre (bug prod 22/09 : clic sans effet).
frappe.provide("customization_app");
// Dialogue partagé (fiche de caisse, facture d'achat) : confirmer puis basculer.
customization_app.basculer_fiche_pas_paye = function (fiche, mode, apres) {
  frappe.confirm(
    __("Passer la fiche {0} de « {1} » à « Pas payé » ? L'avance de caisse (ou le paiement qui en est né) sera annulée : la facture redevient due et rejoint « Factures à payer ».", [fiche, mode || "—"]),
    () => frappe.call({
      method: "customization_app.caisse_depenses.basculer_pas_paye",
      args: { fiche }, freeze: true, freeze_message: __("Bascule…"),
      callback: (r) => {
        const m = r.message || {};
        frappe.show_alert({ message: __("Fiche {0} : « Pas payé ». {1} écriture(s) supprimée(s), {2} paiement(s) annulé(s).",
          [m.fiche, (m.ecritures || []).length, (m.paiements || []).length]), indicator: "green" }, 8);
        if (apres) apres();
      },
    })
  );
};
