// Liste des fiches de caisse (factures et BL photographiés).
// 26/11/2026 : après une « Facture d'achat sur BL » saisie à la caisse, la facture et ses BL restaient affichés
// « À saisir » alors que la facture d'achat était déjà PRÉPARÉE (brouillon) — « pourquoi une nouvelle facture a été
// créée et le BL reconnu n'a pas été pris en compte ? ». La pastille dit maintenant où en est chaque fiche, et un BL
// montre son n° de BL à la place d'un n° de facture vide.
frappe.listview_settings["Facture Achat a Saisir"] = {
  add_fields: ["statut", "est_bl", "numero_bl", "purchase_invoice"],
  get_indicator(doc) {
    if (doc.statut === "Saisie") return [__("Saisie"), "green", "statut,=,Saisie"];
    if (doc.purchase_invoice) {
      return [doc.est_bl ? __("BL pris dans la facture préparée") : __("Facture préparée : à valider"), "blue",
              "statut,=,À saisir|purchase_invoice,is,set"];
    }
    return doc.est_bl ? [__("BL à facturer"), "orange", "statut,=,À saisir|est_bl,=,1|purchase_invoice,is,not set"]
      : [__("À saisir"), "orange", "statut,=,À saisir|purchase_invoice,is,not set"];
  },
  formatters: {
    numero_facture(val, df, doc) {
      if (doc.est_bl && doc.numero_bl && (!val || val === doc.numero_bl)) {
        return `<span class="text-muted">BL</span> ${frappe.utils.escape_html(doc.numero_bl)}`;
      }
      return frappe.utils.escape_html(val || "");
    },
  },
};
