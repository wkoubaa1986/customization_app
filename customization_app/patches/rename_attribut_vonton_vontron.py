"""
Corrige la faute d'orthographe « Vonton » → « Vontron » sur la marque de membrane.

L'interface refuse ce renommage : ERPNext vérifie, à l'enregistrement d'un
Item Attribute, que toute valeur encore utilisée par un article figure dans la
liste. Le réglage « Autoriser le renommage de la valeur de l'attribut » ne fait
que SAUTER ce contrôle — il ne renomme rien — et laisserait 105 articles avec
une référence orpheline.

Ce patch écrit directement en base, ce qui évite d'avoir à contourner une
validation : la contrainte ne porte que sur l'enregistrement du document
Item Attribute, jamais franchi ici.

Périmètre :
  - la valeur dans l'attribut                        1 ligne
  - les lignes Item Variant Attribute des articles   105
  - le nom et la description des articles            105

Volontairement HORS périmètre : les lignes de commandes, BL, factures et devis
(41 + 41 + 11 + 8 lignes). Ce sont des instantanés de ce qui a été vendu ;
réécrire le libellé d'une facture soumise falsifierait un document comptable.

Le code article n'est pas concerné : il porte l'abréviation « V », inchangée.
Aucun article n'est donc renommé et aucun lien ne casse.
"""

import frappe

ATTRIBUT = "Marque de membrane"
ANCIEN = "Vonton"
NOUVEAU = "Vontron"


def execute():
    existe = frappe.db.exists(
        "Item Attribute Value", {"parent": ATTRIBUT, "attribute_value": ANCIEN}
    )
    if not existe:
        print(f"[rename_attribut_vonton_vontron] « {ANCIEN} » absent, rien à faire.")
        return

    if frappe.db.exists(
        "Item Attribute Value", {"parent": ATTRIBUT, "attribute_value": NOUVEAU}
    ):
        # Fusionner deux valeurs demanderait de réaffecter les articles et de
        # gérer les doublons d'abréviation : hors périmètre de cette correction.
        print(
            f"[rename_attribut_vonton_vontron] « {NOUVEAU} » existe déjà, "
            "fusion non traitée — patch ignoré."
        )
        return

    frappe.db.sql(
        """UPDATE `tabItem Attribute Value` SET attribute_value = %(neuf)s
           WHERE parent = %(attr)s AND attribute_value = %(ancien)s""",
        {"attr": ATTRIBUT, "ancien": ANCIEN, "neuf": NOUVEAU},
    )

    variantes = frappe.db.sql(
        """UPDATE `tabItem Variant Attribute` SET attribute_value = %(neuf)s
           WHERE attribute = %(attr)s AND attribute_value = %(ancien)s""",
        {"attr": ATTRIBUT, "ancien": ANCIEN, "neuf": NOUVEAU},
    )

    articles = frappe.db.sql(
        """UPDATE `tabItem`
           SET item_name   = REPLACE(item_name, %(ancien)s, %(neuf)s),
               description = REPLACE(description, %(ancien)s, %(neuf)s)
           WHERE item_name LIKE %(motif)s OR description LIKE %(motif)s""",
        {"ancien": ANCIEN, "neuf": NOUVEAU, "motif": f"%{ANCIEN}%"},
    )

    frappe.db.commit()
    frappe.clear_cache()

    restant = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabItem` WHERE item_name LIKE %(motif)s",
        {"motif": f"%{ANCIEN}%"},
    )[0][0]
    print(
        f"[rename_attribut_vonton_vontron] « {ANCIEN} » → « {NOUVEAU} » : "
        f"attribut, variantes et articles mis à jour. Articles restants : {restant}."
    )
