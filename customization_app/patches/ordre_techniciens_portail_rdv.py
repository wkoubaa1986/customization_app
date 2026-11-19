"""Portail de rendez-vous : ordre des techniciens = Radhouan, Sadok, Jamel (demande du 19/11/2026).

L'ordre de la liste « Employés disponibles » de Config Portail RDV est la PRIORITÉ d'affectation de l'app. Depuis le
départ de Mohamed Hedi (retrait par le code, rangs non renumérotés) puis l'arrivée de Radhouan, Jamel et Radhouan
portaient tous deux le rang 3. Ce patch pose l'ordre demandé et renumérote de 1 à N ; un technicien absent de `ORDRE`
garde sa place relative, après les autres. Écrit directement sur les lignes : rien d'autre ne change dans le réglage.
Joué une fois.
"""
import frappe

ORDRE = ("HR-EMP-00013", "HR-EMP-00001", "HR-EMP-00002")      # Radhouan Sifi, Sadok Bouziri, Jamel Aloui


def ordonner(lignes: list, ordre=ORDRE) -> list:
    """Les lignes [{name, employe, idx}] dans l'ordre voulu ; les autres suivent, dans leur ordre actuel. PURE."""
    rang = {e: i for i, e in enumerate(ordre)}
    return sorted(lignes, key=lambda l: (rang.get(l["employe"], len(ordre)),))


def execute():
    if not frappe.db.table_exists("Portail RDV Employe"):
        return
    lignes = frappe.get_all("Portail RDV Employe",
                            filters={"parent": "Config Portail RDV", "parenttype": "Config Portail RDV"},
                            fields=["name", "employe", "idx"], order_by="idx, creation")
    for i, ligne in enumerate(ordonner(lignes), start=1):
        if ligne.idx != i:
            frappe.db.set_value("Portail RDV Employe", ligne.name, "idx", i, update_modified=False)
    frappe.clear_document_cache("Config Portail RDV", "Config Portail RDV")
