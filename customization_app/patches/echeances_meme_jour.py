"""Plusieurs paiements le même jour (18/11/2026) : la ligne « Avoir client » ne décale plus sa date.

Le Client Script « Verification Sales order » (EN BASE, jamais versionné) cherchait pour la ligne d'avoir une
date que l'échéancier ne portait pas encore (`date_libre_avoir`, patch case_paiement_avec_avoir), parce
qu'ERPNext refusait deux échéances le même jour. Ce refus est levé (customization_app.echeances_meme_jour) :
la ligne d'avoir porte le jour même. On neutralise la boucle de décalage en remplaçant UN jeton d'une ligne
(fins de ligne CRLF du script intactes). Idempotent.
"""
import frappe

SCRIPT = "Verification Sales order"
JETON = "while (prises.indexOf(jour) !== -1) {"
NOUVEAU_JETON = "while (false) {   // 18/11/2026 : plusieurs paiements le même jour sont permis, la date n'est plus décalée"


def corriger(script: str):
    """-> (script corrigé, ce qui a été fait). Pur."""
    if NOUVEAU_JETON in script:
        return script, "déjà fait"
    if JETON not in script:
        return script, "jeton introuvable"
    return script.replace(JETON, NOUVEAU_JETON), "modifié"


def execute():
    if not frappe.db.exists("Client Script", SCRIPT):
        return {"script": "absent"}
    script = frappe.db.get_value("Client Script", SCRIPT, "script") or ""
    nouveau, fait = corriger(script)
    if fait == "modifié":
        frappe.db.set_value("Client Script", SCRIPT, "script", nouveau, update_modified=False)
        frappe.db.commit()
        frappe.clear_cache(doctype="Sales Order")
    return {"script": fait}
