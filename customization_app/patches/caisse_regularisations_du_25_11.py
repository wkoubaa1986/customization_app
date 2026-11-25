"""Les 4 décisions qu'il a prises sur le dev le 25/11/2026 au soir (page « Où est l'argent liquide » › « À régulariser »),
rejouées en prod à son nom et avec ses motifs — sa demande : « tu peux patcher ce que j'ai écrit dans l'outil pour la prod ».

  - saut:CLO-1055 (Nejib Koubaa, +5 293 DT le 20/10/2026) : « Cet argent n'existe pas » → −5 293 DT sur la carte ;
  - jv:ACC-JV-2026-00854 (100 DT, remboursement facture STEG), jv:ACC-JV-2026-01040 (76,6 DT) et
    jv:ACC-JV-2026-01051 (80 DT) : « C'est bien de l'argent entré en caisse » → aucun montant ne change.

Prudent : chaque anomalie est RELUE sur la base de prod (customization_app.argent_liquide.situation) ; si elle n'y est
plus, ou si son effet n'est plus celui qu'il a vu (le document a changé), elle est laissée à sa décision sur la page et
le compte rendu le dit. Rejouable sans effet (une clé déjà régularisée est sautée). Aucune écriture comptable.
"""
import frappe

AUTEUR = "koubaawassim@gmail.com"
DECISIONS = (
    # (clé, décision, effet attendu, motif écrit par lui, décidé le)
    ("jv:ACC-JV-2026-00854", "entree", 0.0, "oui argent pour paiement d'une facture de la part de nos voisin", "2026-11-25 21:30:48"),
    ("jv:ACC-JV-2026-01040", "entree", 0.0, "c'est expliqué", "2026-11-25 21:31:32"),
    ("jv:ACC-JV-2026-01051", "entree", 0.0, "c'est explqué", "2026-11-25 21:31:58"),
    ("saut:CLO-1055", "retirer", -5293.0, "erreur néjib", "2026-11-25 21:38:40"),
)
DOCTYPE = "Regularisation Caisse"


def execute():
    from customization_app import argent_liquide as A

    if not frappe.db.table_exists(DOCTYPE):
        return
    auteur = AUTEUR if frappe.db.exists("User", AUTEUR) else frappe.session.user
    ouvertes = None
    lignes = []
    for cle, code, effet, motif, quand in DECISIONS:
        if frappe.db.exists(DOCTYPE, {"cle": cle}):
            lignes.append("%s : déjà régularisée" % cle)
            continue
        if ouvertes is None:
            ouvertes = {a["cle"]: a for a in A.situation().get("a_regulariser") or []}
        anomalie = ouvertes.get(cle)
        option = next((o for o in (anomalie or {}).get("options") or [] if o["code"] == code), None)
        if not anomalie or not option:
            lignes.append("%s : plus sur la carte de prod — laissée à sa décision" % cle)
            continue
        if abs(float(option["effet"]) - effet) > 0.0005:
            lignes.append("%s : l'effet serait %s au lieu de %s — laissée à sa décision" % (cle, option["effet"], effet))
            continue
        doc = frappe.get_doc({
            "doctype": DOCTYPE, "cle": cle, "nature": anomalie["nature"], "caisse": anomalie.get("caisse"),
            "poche": anomalie.get("poche"), "date_anomalie": anomalie.get("date"),
            "document_type": anomalie.get("document_type"), "document": anomalie.get("document"),
            "montant": anomalie.get("montant"), "effet": option["effet"], "decision": option["libelle"],
            "motif": motif, "decide_par": auteur, "decide_le": quand})
        doc.insert(ignore_permissions=True)
        if anomalie.get("document_type") and frappe.db.exists(anomalie["document_type"], anomalie.get("document")):
            frappe.get_doc(anomalie["document_type"], anomalie["document"]).add_comment(
                "Comment", "🧹 Anomalie de caisse régularisée (%s) par %s, décision du 25/11/2026 reprise du dev : %s — %s" % (
                    doc.name, frappe.utils.get_fullname(auteur), option["libelle"], motif))
        lignes.append("%s : régularisée (%s, effet %s)" % (cle, doc.name, option["effet"]))
    frappe.log_error(title="Régularisations de caisse du 25/11", message="\n".join(lignes))
