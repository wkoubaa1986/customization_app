"""Le NOM de l'employé affiché sur une tâche (champ texte `custom_employé`, repris par le calendrier,
Ma tournée, l'historique client, les SMS) doit TOUJOURS valoir le nom de l'employé du « Choix du staff »
(demande utilisateur 04/10/2026 : des tâches du 05/10 en prod affichaient un autre employé ou un nom vide).

Il est donc recalculé à chaque enregistrement, quel que soit le chemin de création (formulaire, portail
RDV, atelier, tournée, scripts en base) — plus aucun écrivain n'a à y penser."""
import frappe


def nom_attendu(staff, nom_employee):
    """Le nom à afficher : celui de la fiche Employé du staff ; vide si aucun staff. Fonction pure."""
    if not staff:
        return ""
    return (nom_employee or "").strip() or staff


def aligner_nom_employe(doc, method=None):
    """before_save de la Tache de travail : custom_employé := employee_name du choix du staff."""
    staff = doc.get("custom_choix_du_staff")
    nom = frappe.db.get_value("Employee", staff, "employee_name") if staff else None
    voulu = nom_attendu(staff, nom)
    if (doc.get("custom_employé") or "") != voulu:
        doc.set("custom_employé", voulu)
