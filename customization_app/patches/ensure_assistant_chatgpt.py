"""Utilisateur technique et rôle de l'assistant ChatGPT (02/11/2026) — voir customization_app.assistant_chatgpt.

Rôle « Assistant ChatGPT » sans aucune permission de doctype ; utilisateur assistant.chatgpt@aquaworld.local (System
User, ce seul rôle, pas d'e-mail de bienvenue). La clé API n'est PAS générée ici : elle se crée dans la fiche de
l'utilisateur (« API Access » → Générer les clés) et ne s'affiche qu'une fois. Idempotent.
"""
import frappe

from customization_app.assistant_chatgpt import ROLE, UTILISATEUR


def execute():
    fait = []
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1, "is_custom": 1}).insert(ignore_permissions=True)
        fait.append("rôle")
    if not frappe.db.exists("User", UTILISATEUR):
        u = frappe.get_doc({"doctype": "User", "email": UTILISATEUR, "first_name": "Assistant ChatGPT", "user_type": "System User",
                            "send_welcome_email": 0, "enabled": 1, "language": "fr", "roles": [{"role": ROLE}]})
        u.flags.no_welcome_mail = True
        u.insert(ignore_permissions=True)
        fait.append("utilisateur")
    else:
        u = frappe.get_doc("User", UTILISATEUR)
        if ROLE not in {r.role for r in u.roles}:
            u.append("roles", {"role": ROLE})
            u.save(ignore_permissions=True)
            fait.append("rôle ajouté à l'utilisateur")
    if fait:
        frappe.db.commit()
    return fait
