import frappe
from frappe import _
from frappe.model.document import Document


class ModeleBLVirtuel(Document):
    def validate(self):
        self.verifier_articles()

    def verifier_articles(self):
        """Refuse les lignes sans quantité utile et les articles en double."""
        vus = set()
        for row in self.articles:
            if frappe.utils.flt(row.qty) <= 0:
                frappe.throw(
                    _("Ligne {0} : la quantité doit être supérieure à zéro.").format(row.idx)
                )
            if row.item_code in vus:
                frappe.throw(
                    _("L'article {0} apparaît plusieurs fois. Regroupez-le sur une seule ligne.").format(
                        frappe.bold(row.item_code)
                    )
                )
            vus.add(row.item_code)


def get_modele_actif(type_intervention):
    """
    Retourne le document Modele BL Virtuel actif pour ce type d'intervention,
    ou None s'il n'y en a pas (ou s'il est vide / désactivé).

    Utilisé par generer_bl.py pour construire les BL virtuels.
    """
    if not type_intervention:
        return None

    if not frappe.db.exists("Modele BL Virtuel", type_intervention):
        return None

    modele = frappe.get_cached_doc("Modele BL Virtuel", type_intervention)
    if not modele.actif or not modele.articles:
        return None

    return modele
