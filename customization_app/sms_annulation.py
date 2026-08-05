"""
SMS d'annulation des commandes WEB.

Une commande WooCommerce annulée doit être signalée au client, sur tous ses
numéros. Le traitement des numéros réutilise celui de « Compagne SMS » —
normalisation, filtrage des mobiles tunisiens, dédoublonnage — pour qu'une
seule logique gouverne les numéros dans toute l'application.

L'envoi est mis en file d'attente : la passerelle WinSMSPro a un délai
d'attente de 15 s par numéro, et l'annulation d'une commande ne doit pas
attendre l'aboutissement des SMS.
"""

import frappe
from frappe.utils import cstr, flt

from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
    _send_sms_with_fallback,
    traiter_numero_tel,
)

# Numéro de contact affiché dans le SMS : celui de l'employé référent, lu sur sa
# fiche. Un changement de numéro ne demande donc aucun redéploiement.
EMPLOYE_CONTACT = "HR-EMP-00001"  # Sadok Bouziri
TELEPHONE_SECOURS = "98 511 119"  # utilisé si la fiche employé n'a pas de mobile


def telephone_contact():
    """Numéro à afficher dans le message, formaté par groupes lisibles."""
    brut = frappe.db.get_value("Employee", EMPLOYE_CONTACT, "cell_number") or ""
    numeros = traiter_numero_tel(brut)
    if not numeros:
        return TELEPHONE_SECOURS
    n = numeros[0]
    return f"{n[:2]} {n[2:5]} {n[5:]}"

MESSAGE = (
    "Bonjour {nom_client},\n"
    "Votre commande {ref} d'un montant de {total} DT a été annulée.\n"
    "Pour toute question, contactez-nous au {telephone}."
)


def _numeros_du_client(nom_client):
    """Numéros mobiles tunisiens valides d'un client, depuis custom_liste_telephone."""
    brut = frappe.db.get_value("Customer", nom_client, "custom_liste_telephone")
    return traiter_numero_tel(brut)


def construire_message(doc, nom_affiche=None):
    return MESSAGE.format(
        nom_client=nom_affiche or doc.get("customer_name") or doc.get("customer") or "",
        ref=doc.get("name") or "",
        total=f"{flt(doc.get('grand_total')):.3f}",
        telephone=telephone_contact(),
    )


def envoyer_pour_commande(nom_commande):
    """
    Envoie le SMS d'annulation aux numéros du client d'une commande.

    Retourne le nombre de numéros servis. Ne lève jamais : un échec d'envoi ne
    doit pas remonter dans une file d'attente déjà détachée de l'annulation.
    """
    try:
        doc = frappe.get_doc("Sales Order", nom_commande)
    except Exception:
        frappe.log_error(title=f"SMS annulation — {nom_commande}", message=frappe.get_traceback())
        return 0

    numeros = _numeros_du_client(doc.customer)
    if not numeros:
        doc.add_comment(
            "Comment",
            "SMS d'annulation non envoyé : aucun numéro mobile tunisien valide "
            "dans la liste téléphone du client.",
        )
        frappe.db.commit()
        return 0

    message = construire_message(doc)
    # Préfixe international attendu par la passerelle, comme dans Compagne SMS.
    _send_sms_with_fallback([f"216{n}" for n in numeros], cstr(message))

    doc.add_comment(
        "Comment",
        f"SMS d'annulation envoyé à {len(numeros)} numéro(s) : {', '.join(numeros)}.",
    )
    frappe.db.commit()
    return len(numeros)


def on_sales_order_cancel(doc, method=None):
    """
    Hook d'annulation. Ne concerne que les commandes WEB.

    Discriminant : woocommerce_id — les 270 commandes WEB en ont un, aucune
    commande saisie au Desk n'en a.
    """
    if not doc.get("woocommerce_id"):
        return

    frappe.enqueue(
        "customization_app.sms_annulation.envoyer_pour_commande",
        queue="short",
        enqueue_after_commit=True,
        nom_commande=doc.name,
    )


@frappe.whitelist()
def tester_envoi(numero, commande=None):
    """
    Envoie un SMS d'annulation de test à un numéro donné, sans rien annuler.

    Sert à vérifier la passerelle et le rendu du message. Si `commande` est
    fourni, le message reprend sa référence et son total ; sinon un exemple.
    """
    frappe.only_for("System Manager")

    numeros = traiter_numero_tel(numero)
    if not numeros:
        frappe.throw("Numéro invalide : un mobile tunisien à 8 chiffres est attendu.")

    if commande:
        doc = frappe.get_doc("Sales Order", commande)
    else:
        doc = frappe._dict(
            name="WEB1-000000", customer_name="Client de test", grand_total=123.456
        )

    message = construire_message(doc)
    _send_sms_with_fallback([f"216{n}" for n in numeros], cstr(message))
    return {"numeros": numeros, "message": message}
