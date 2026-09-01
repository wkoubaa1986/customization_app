"""Le technicien doit pouvoir LIRE les termes de paiement de sa commande.

Constaté le 02/09/2026 : en clôturant une tâche, un technicien (Sales User)
ouvre la commande liée et se heurte à « Pas permis — l'utilisateur n'a pas
d'accès au type de document (lecture) pour le document Terme de paiement ».

CE QUI SE PASSE. La commande porte un échéancier ; chaque ligne pointe un
`Payment Term`. Afficher la commande, c'est lire ces lignes — et ce doctype-là
n'était lisible que par la comptabilité, le partenaire et le contractant. Le
technicien voit donc sa commande, mais bute sur son échéancier : l'écran refuse
avant d'avoir rien montré, et la clôture s'arrête.

POURQUOI CE DROIT EST ANODIN. Un `Payment Term` est un RÉFÉRENTIEL — « 50 % à
la commande », « à la livraison » — pas une donnée client. Le rôle Sales User
lit déjà la commande elle-même, ses articles, ses montants et le modèle de
termes de paiement (`Payment Terms Template`) : lui refuser la ligne d'échéance
ne protégeait rien, cela cassait seulement l'affichage. On n'accorde que la
LECTURE, à ce seul rôle.

⚠️ CUSTOM DOCPERM, ET NON DOCPERM. Ce doctype porte déjà des Custom DocPerm
(posés en 2024) : dès qu'il en existe un, Frappe IGNORE les permissions
standard de l'app. Ajouter une ligne standard n'aurait donc rien changé.
"""
import frappe

DOCTYPE = "Payment Term"
ROLES = ("Sales User",)


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return
    ajoutes = []
    for role in ROLES:
        if not frappe.db.exists("Role", role):
            continue
        if frappe.db.exists("Custom DocPerm",
                            {"parent": DOCTYPE, "role": role, "permlevel": 0}):
            continue
        frappe.get_doc({
            "doctype": "Custom DocPerm",
            "parent": DOCTYPE,
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": role,
            "permlevel": 0,
            "read": 1,
        }).insert(ignore_permissions=True)
        ajoutes.append(role)
    if ajoutes:
        # Les permissions sont mises en cache par utilisateur : sans ce vidage,
        # le technicien resterait bloqué jusqu'à sa prochaine connexion.
        frappe.clear_cache()
        frappe.db.commit()
    return ajoutes
