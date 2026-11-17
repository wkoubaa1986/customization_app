"""La case « Paiement avec Avoir » de la commande lit l'avoir UTILISABLE, et vit dans « Termes de paiement » (17/11/2026).

Le Client Script « Verification Sales order » (EN BASE, jamais versionné) montre sur la commande une case
« Paiement avec Avoir <montant> » qui, mise à « Oui », diminue les échéances et ajoute une ligne « Avoir
client ». Elle lisait `final_result['Total des avances du client']` : un chiffre qui ignore les avoirs créés
par « Créer un avoir » et ceux nés d'une retenue à la source, et qui ne baisse jamais quand on s'en sert.
La case ne s'affichait donc que pour 2 clients (dont un qui avait déjà tout consommé) et pour aucun des 5
clients qui avaient réellement un avoir — « pourquoi elle ne s'affiche pas ? » (MH2C).

Ce patch, idempotent, sur le script en base :
  - remplace ce chiffre par `avoir_utilisable(final_result)` : la clé « Avoir utilisable » du Server Script
    « get customer information » (avoirs disponibles + avances nettes), avec repli sur l'ancien chiffre tant
    que ce script n'est pas à jour ;
  - donne à la ligne « Avoir client » une date d'échéance LIBRE (ERPNext refuse deux échéances le même jour :
    un avoir partiel posé le jour de la commande tombait sur la date de l'échéance qu'il diminuait) ;
et déplace la case sous le titre « Termes de paiement », à côté de l'échéancier qu'elle ajuste.

Fins de ligne du script : CRLF — on ne remplace que des jetons d'une ligne, et l'ajout suit la même convention.
"""
import frappe

SCRIPT = "Verification Sales order"
JETON = "final_result['Total des avances du client']"
NOUVEAU_JETON = "avoir_utilisable(final_result)"
DATE = "due_date: frappe.datetime.now_date(),"
NOUVELLE_DATE = "due_date: date_libre_avoir(frm),"
MARQUE = "function avoir_utilisable(final_result)"
AIDES = """

// ── 17/11/2026 (patch customization_app.patches.case_paiement_avec_avoir) ────────────────────────────────
// Le montant que la case « Paiement avec Avoir » propose : l'avoir UTILISABLE du client (avoirs disponibles,
// retenues à la source comprises, + avances nettes). Il baisse quand on s'en sert. Repli sur l'ancien chiffre
// si le Server Script « get customer information » ne rend pas encore cette clé.
function avoir_utilisable(final_result) {
    var v = final_result ? final_result['Avoir utilisable'] : null;
    if (v === undefined || v === null) {
        v = final_result ? final_result['Total des avances du client'] : 0;
    }
    return flt(v, 3);
}

// Une date d'échéance que l'échéancier ne porte pas encore : ERPNext refuse deux échéances le même jour.
function date_libre_avoir(frm) {
    var prises = (frm.doc.payment_schedule || []).map(function (r) { return r.due_date; });
    var jour = frappe.datetime.now_date();
    while (prises.indexOf(jour) !== -1) {
        jour = frappe.datetime.add_days(jour, 1);
    }
    return jour;
}
"""
CHAMP = "Sales Order-custom_paiement_avec_avoir"
APRES = "payment_terms_section"


def corriger(script: str):
    """-> (script corrigé, ce qui a été fait). Pur."""
    if MARQUE in script:
        return script, "déjà fait"
    if JETON not in script:
        return script, "jeton introuvable"
    fin_de_ligne = "\r\n" if "\r\n" in script else "\n"
    nouveau = script.replace(JETON, NOUVEAU_JETON).replace(DATE, NOUVELLE_DATE)
    return nouveau.rstrip() + AIDES.replace("\n", fin_de_ligne), "modifié"


def execute():
    bilan = {}
    if frappe.db.exists("Client Script", SCRIPT):
        script = frappe.db.get_value("Client Script", SCRIPT, "script") or ""
        nouveau, fait = corriger(script)
        bilan["script"] = fait
        if fait == "modifié":
            frappe.db.set_value("Client Script", SCRIPT, "script", nouveau, update_modified=False)
        elif fait == "jeton introuvable":
            frappe.log_error(script[:3000], "case_paiement_avec_avoir : script non reconnu, laissé tel quel")
    else:
        bilan["script"] = "absent"
    if frappe.db.exists("Custom Field", CHAMP) and frappe.get_meta("Sales Order").has_field(APRES):
        if frappe.db.get_value("Custom Field", CHAMP, "insert_after") != APRES:
            frappe.db.set_value("Custom Field", CHAMP, "insert_after", APRES, update_modified=False)
            bilan["champ"] = "déplacé"
        else:
            bilan["champ"] = "déjà en place"
    frappe.db.commit()
    frappe.clear_cache(doctype="Sales Order")
    return bilan
