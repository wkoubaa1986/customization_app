"""Dernier commentaire d'une commande, matérialisé sur la fiche (demande 03/10/2026 : Salma commente les commandes
qu'elle traite ; on veut le voir en pastille dans la liste des commandes, le filtrer, et le voir avec sa date dans
« Commandes à traiter »).

Les commentaires vivent dans tabComment (comment_type « Comment », timeline de la commande). Un hook sur Comment recopie
le DERNIER commentaire humain sur la commande : `custom_dernier_commentaire` (texte sans HTML, 140 car.),
`custom_commentaire_le`, `custom_commentaire_par`, `custom_avec_commentaire` (filtre). Les traces posées par le code
(« SMS d'annulation envoyé… », « 📨 Envoi groupé… ») sont ignorées : elles ont leurs propres pastilles."""
from __future__ import annotations

import re

import frappe
from frappe.utils import strip_html

CHAMPS = ("custom_dernier_commentaire", "custom_commentaire_le", "custom_commentaire_par", "custom_avec_commentaire")
# Débuts de commentaires AUTOMATIQUES (code), à ne pas confondre avec un mot de Salma.
AUTOMATIQUES = ("SMS d'annulation", "SMS d’annulation", "BL consolidé", "📨", "📲", "🤝", "↩️", "📱", "✅", "⏸️", "🧹", "🔁")
LONGUEUR = 140


def texte_propre(html: str) -> str:
    """Le texte d'un commentaire sans HTML ni blancs multiples, tronqué à 140. PURE."""
    t = strip_html(html or "").replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= LONGUEUR else t[: LONGUEUR - 1].rstrip() + "…"


def est_automatique(texte: str) -> bool:
    return (texte or "").lstrip().startswith(AUTOMATIQUES)


def champs_presents() -> bool:
    return frappe.db.has_column("Sales Order", "custom_dernier_commentaire")


def dernier_commentaire(commande: str):
    """{texte, le, par} du dernier commentaire humain, ou None."""
    for c in frappe.get_all("Comment", filters={"reference_doctype": "Sales Order", "reference_name": commande, "comment_type": "Comment"},
                            fields=["content", "creation", "owner", "comment_email", "comment_by"], order_by="creation desc", limit=10):
        texte = texte_propre(c.content)
        if not texte or est_automatique(texte):
            continue
        return {"texte": texte, "le": c.creation, "par": c.comment_by or frappe.utils.get_fullname(c.owner) or c.owner}
    return None


def synchroniser(commande: str):
    """Recopie le dernier commentaire humain sur la commande (db.set_value : la commande est souvent soumise)."""
    if not champs_presents() or not frappe.db.exists("Sales Order", commande):
        return None
    d = dernier_commentaire(commande)
    frappe.db.set_value("Sales Order", commande, {
        "custom_dernier_commentaire": d["texte"] if d else None, "custom_commentaire_le": d["le"] if d else None,
        "custom_commentaire_par": d["par"] if d else None, "custom_avec_commentaire": 1 if d else 0}, update_modified=False)
    return d


def comment_change(doc, method=None):
    """Hook Comment (after_insert / on_update / after_delete — on_trash verrait encore le commentaire supprimé)."""
    if doc.reference_doctype == "Sales Order" and doc.comment_type == "Comment" and doc.reference_name:
        try:
            synchroniser(doc.reference_name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Commentaire commande {doc.reference_name}")


def resynchroniser_tout(depuis=None) -> int:
    """Reprise : toutes les commandes qui ont au moins un commentaire (patch, ou rattrapage)."""
    filtres = {"reference_doctype": "Sales Order", "comment_type": "Comment"}
    if depuis:
        filtres["creation"] = [">=", depuis]
    noms = frappe.get_all("Comment", filters=filtres, pluck="reference_name", distinct=True)
    n = 0
    for nom in set(noms):
        if nom and synchroniser(nom):
            n += 1
    return n
