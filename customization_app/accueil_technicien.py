"""L'accueil du technicien en écran simplifié — sa liste d'articles (demande du 18/11/2026).

« L'affichage de la liste des articles, pour le mode simple, doit être avec une barre de recherche (nom, code) ;
la liste : l'image, avec le code, le nom, la description et le prix en Vente standard. »

`articles(recherche, debut)` sert la page `articles-technicien` : les articles VENDABLES (actifs, pas les
modèles à variantes), cherchés par mots dans le code ou le nom, par pages de 30, chacun avec son prix du jour
dans la liste « Vente standard ». Lecture seule. Le droit demandé est celui de lire les articles ; le prix de
vente standard est donné avec (c'est le prix public, celui que le technicien annonce au client).
"""
from __future__ import annotations

import html
import re

import frappe
from frappe.utils import cint, flt, getdate, nowdate

LISTE_PRIX = "Vente standard"
PAR_PAGE = 30
DESCRIPTION_MAX = 220


def mots(recherche) -> list:
    """« pompe  QB60 » -> ["pompe", "QB60"] : chaque mot doit se trouver dans le code ou le nom. PURE."""
    return [m for m in re.split(r"\s+", str(recherche or "").strip()) if m][:6]


def texte_brut(source, maximum: int = DESCRIPTION_MAX) -> str:
    """La description d'un article (HTML de l'éditeur) en texte d'une ligne, coupée proprement. PURE."""
    t = re.sub(r"<(br|/p|/div|/li)[^>]*>", " ", str(source or ""), flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= maximum:
        return t
    coupe = t[:maximum].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return (coupe or t[:maximum]) + "…"


def prix_du_jour(lignes: list, unite_stock: str = "", jour=None):
    """Le prix à annoncer parmi les lignes de prix d'un article. PURE.

    `lignes` : [{price_list_rate, uom, valid_from, valid_upto, customer}] de la liste voulue. Sont écartés les
    prix réservés à un client et ceux hors de leur période de validité. Est préféré le prix dans l'unité de
    stock (ou sans unité), puis le plus récent. -> le prix, ou None s'il n'y en a pas."""
    jour = str(jour or "")[:10]
    bons = []
    for l in lignes or []:
        if l.get("customer"):
            continue
        debut, fin = str(l.get("valid_from") or "")[:10], str(l.get("valid_upto") or "")[:10]
        if jour and ((debut and debut > jour) or (fin and fin < jour)):
            continue
        unite = l.get("uom") or ""
        bons.append((1 if (not unite or unite == (unite_stock or "")) else 0, debut, l))
    if not bons:
        return None
    bons.sort(key=lambda b: (b[0], b[1]))
    return float(bons[-1][2].get("price_list_rate") or 0)


@frappe.whitelist()
def articles(recherche=None, debut=0):
    """{articles: [{code, nom, description, image, prix, unite}], total, suite, liste_prix}."""
    frappe.has_permission("Item", "read", throw=True)
    debut = max(0, cint(debut))
    conditions, valeurs = ["i.disabled = 0", "i.has_variants = 0", "i.is_sales_item = 1"], []
    for mot in mots(recherche):
        motif = "%" + mot.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(i.item_code like %s or i.item_name like %s)")
        valeurs += [motif, motif]
    ou = " and ".join(conditions)
    total = frappe.db.sql("select count(*) from `tabItem` i where " + ou, valeurs)[0][0]
    lignes = frappe.db.sql(
        "select i.name, i.item_name, i.description, i.image, i.stock_uom from `tabItem` i where " + ou
        + " order by i.item_name, i.name limit %s offset %s", valeurs + [PAR_PAGE, debut], as_dict=True)

    prix = {}
    if lignes:
        for p in frappe.get_all("Item Price", filters={"price_list": LISTE_PRIX, "selling": 1,
                                                       "item_code": ["in", [l.name for l in lignes]]},
                                fields=["item_code", "price_list_rate", "uom", "valid_from", "valid_upto", "customer"]):
            prix.setdefault(p.item_code, []).append(p)
    jour = str(getdate(nowdate()))
    sortie = []
    for l in lignes:
        description = texte_brut(l.description)
        if description.lower() in ((l.item_name or "").lower(), (l.name or "").lower()):
            description = ""                      # une description qui répète le nom n'apprend rien
        p = prix_du_jour(prix.get(l.name), l.stock_uom, jour)
        sortie.append({"code": l.name, "nom": l.item_name or l.name, "description": description,
                       "image": l.image or "", "prix": flt(p, 3) if p is not None else None, "unite": l.stock_uom or ""})
    return {"articles": sortie, "total": total, "suite": debut + len(lignes) < total, "liste_prix": LISTE_PRIX}
