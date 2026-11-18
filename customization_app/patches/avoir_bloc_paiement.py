"""Commande : c'est le bloc de paiement qui ajuste l'avoir du client, plus le Client Script (18/11/2026).

Le Client Script « Verification Sales order » (EN BASE, jamais versionné) ajustait lui-même l'échéancier quand la
case « Paiement avec Avoir » changeait : à « Oui » il diminuait les lignes et ajoutait une ligne « Avoir client »
(après un appel serveur) ; à « Non » il rechargeait le modèle de termes de paiement, avec un message. Le bloc de
paiement (public/js/sales_order_paiement.js) fait désormais cet ajustement tout de suite et le tient à jour ; les
deux ensemble posaient l'avoir DEUX fois dès qu'une carte était en jeu (les montants étaient en cours de
recalcul quand le script vérifiait si l'avoir était déjà posé).

Ce patch, idempotent, fait rendre la main au script quand le bloc est là (`window.__so_paiement_bloc`), en
ajoutant une garde sur deux lignes — sans retour à la ligne, les fins de ligne CRLF du script restent intactes.
Sans le bloc (application ancienne, autre écran), le script se comporte exactement comme avant.
"""
import re

import frappe

SCRIPT = "Verification Sales order"
GARDE = "if (window.__so_paiement_bloc) { return; }"
# la définition ACTIVE (l'ancienne version commentée commence par « // function »)
RX_FONCTION = re.compile(r"(?m)^(function adjust_payment_schedule\(frm\) \{)")
JETON_NON = "frm._avoir_applied=false;"


def corriger(script: str):
    """-> (script corrigé, ce qui a été fait). Pur."""
    if GARDE in script:
        return script, "déjà fait"
    if not RX_FONCTION.search(script) or JETON_NON not in script:
        return script, "jeton introuvable"
    nouveau = RX_FONCTION.sub(lambda m: m.group(1) + " " + GARDE, script, count=1)
    nouveau = nouveau.replace(JETON_NON, JETON_NON + " " + GARDE, 1)
    return nouveau, "modifié"


def execute():
    if not frappe.db.exists("Client Script", SCRIPT):
        return {"script": "absent"}
    script = frappe.db.get_value("Client Script", SCRIPT, "script") or ""
    nouveau, fait = corriger(script)
    if fait == "modifié":
        frappe.db.set_value("Client Script", SCRIPT, "script", nouveau, update_modified=False)
        frappe.db.commit()
        frappe.clear_cache(doctype="Sales Order")
    elif fait == "jeton introuvable":
        frappe.log_error(script[:3000], "avoir_bloc_paiement : script non reconnu, laissé tel quel")
    return {"script": fait}
