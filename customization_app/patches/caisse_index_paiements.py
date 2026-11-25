"""Index sur Payment Entry (posting_date, creation) — 25/11/2026.

Le rapport de caisse cherche les paiements d'une journée par date comptable OU date de saisie ; sans index, chaque
appel parcourait les 12 500 paiements (0,7 s), et la grille « État des caisses » en fait un par jour depuis la date de
départ des caisses (40 s pour 54 jours)."""
import frappe


def execute():
    frappe.db.add_index("Payment Entry", ["posting_date"])
    frappe.db.add_index("Payment Entry", ["creation"])
