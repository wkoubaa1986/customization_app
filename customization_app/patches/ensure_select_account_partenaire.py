"""Le partenaire (rôle « Partenaire », compte Economiq) doit pouvoir CHOISIR un compte comptable.

Constaté le 02/11/2026 : depuis la mise à jour ERPNext du 20/10/2026 (v15.111 → v15.123), le partenaire
ne peut plus enregistrer une commande client : « User don't have permissions to select/read this
account ». Sa dernière commande date du 10/10.

CE QUI SE PASSE. En enregistrant une commande, ERPNext cherche le compte client (« Débiteurs - A&S »)
via `get_party_account`, qui depuis v15.112 vérifie que l'utilisateur a le droit « select » (ou
« read ») sur ce compte (erpnext/accounts/party.py, `account_perm_check`). Le rôle Partenaire n'a
aucun droit sur le doctype Account : refus, et la commande ne s'enregistre pas — même en brouillon,
même pour un client partagé avec lui par sa tâche.

POURQUOI CE DROIT EST ANODIN. « select » permet seulement de désigner un compte dans un champ de lien
et de passer ce contrôle ; il n'ouvre ni la liste des comptes, ni leur fiche, ni le grand livre (ce
serait « read »). C'est le même droit que les rôles Delivery User, Maintenance User, Stock Manager…
ont déjà sur Account. Le contractant (rôle Contractor) a « read » depuis 2025 et n'est pas touché.

⚠️ CUSTOM DOCPERM, ET NON DOCPERM : Account porte déjà des Custom DocPerm, donc Frappe ignore les
permissions standard de l'app ; seule une ligne Custom DocPerm compte (cf. ensure_lecture_payment_term).
"""
import frappe

DOCTYPE = "Account"
ROLES = ("Partenaire",)


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return
    ajoutes = []
    for role in ROLES:
        if not frappe.db.exists("Role", role):
            continue
        if frappe.db.exists("Custom DocPerm", {"parent": DOCTYPE, "role": role, "permlevel": 0}):
            continue
        frappe.get_doc({
            "doctype": "Custom DocPerm",
            "parent": DOCTYPE,
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": role,
            "permlevel": 0,
            "select": 1,
        }).insert(ignore_permissions=True)
        ajoutes.append(role)
    if ajoutes:
        # Les permissions sont mises en cache par utilisateur : sans ce vidage, le partenaire
        # resterait bloqué jusqu'à sa prochaine connexion.
        frappe.clear_cache()
        frappe.db.commit()
    return ajoutes
