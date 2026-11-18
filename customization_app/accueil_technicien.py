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


TRIS = ("nom", "groupe", "prix_croissant", "prix_decroissant")


def trier(lignes: list, tri: str = "nom") -> list:
    """Les articles dans l'ordre demandé. PURE. `lignes` : [{nom, code, groupe, prix}].

    « nom » : par nom ; « groupe » : par groupe puis par nom ; « prix_croissant » / « prix_decroissant » : par prix,
    les articles SANS prix toujours à la fin."""
    nom = lambda l: ((l.get("nom") or "").lower(), l.get("code") or "")
    if tri == "groupe":
        return sorted(lignes, key=lambda l: ((l.get("groupe") or "").lower(),) + nom(l))
    if tri in ("prix_croissant", "prix_decroissant"):
        avec = [l for l in lignes if l.get("prix") is not None]
        sans = sorted((l for l in lignes if l.get("prix") is None), key=nom)
        signe = -1 if tri == "prix_decroissant" else 1
        return sorted(avec, key=lambda l: (signe * float(l["prix"]),) + nom(l)) + sans
    return sorted(lignes, key=nom)


@frappe.whitelist()
def groupes():
    """Les groupes d'articles qui ont au moins un article vendable, avec leur nombre — pour le filtre de la page."""
    frappe.has_permission("Item", "read", throw=True)
    return [{"groupe": g, "nombre": n} for g, n in frappe.db.sql(
        """select i.item_group, count(*) from `tabItem` i
           where i.disabled = 0 and i.has_variants = 0 and i.is_sales_item = 1 and ifnull(i.item_group, '') != ''
           group by i.item_group order by i.item_group""")]


@frappe.whitelist()
def articles(recherche=None, debut=0, groupe=None, tri=None):
    """{articles: [{code, nom, groupe, description, image, prix, unite}], total, suite, liste_prix, tri}.

    `groupe` : un groupe d'articles (filtre) ; `tri` : nom (défaut), groupe, prix_croissant, prix_decroissant.
    Le prix du jour n'est pas une colonne : pour trier par prix il faut le connaître pour TOUS les articles qui
    correspondent — ils sont un millier au plus, tout se trie ici puis la page demandée est rendue."""
    frappe.has_permission("Item", "read", throw=True)
    debut = max(0, cint(debut))
    tri = tri if tri in TRIS else "nom"
    conditions, valeurs = ["i.disabled = 0", "i.has_variants = 0", "i.is_sales_item = 1"], []
    for mot in mots(recherche):
        motif = "%" + mot.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(i.item_code like %s or i.item_name like %s)")
        valeurs += [motif, motif]
    if groupe:
        conditions.append("i.item_group = %s")
        valeurs.append(groupe)
    tous = frappe.db.sql(
        "select i.name, i.item_name, i.item_group, i.description, i.image, i.stock_uom from `tabItem` i where "
        + " and ".join(conditions), valeurs, as_dict=True)

    prix = {}
    if tous:
        for p in frappe.get_all("Item Price", filters={"price_list": LISTE_PRIX, "selling": 1,
                                                       "item_code": ["in", [l.name for l in tous]]},
                                fields=["item_code", "price_list_rate", "uom", "valid_from", "valid_upto", "customer"],
                                limit_page_length=0):
            prix.setdefault(p.item_code, []).append(p)
    jour = str(getdate(nowdate()))
    lignes = []
    for l in tous:
        p = prix_du_jour(prix.get(l.name), l.stock_uom, jour)
        lignes.append({"code": l.name, "nom": l.item_name or l.name, "groupe": l.item_group or "",
                       "_description": l.description, "image": l.image or "",
                       "prix": flt(p, 3) if p is not None else None, "unite": l.stock_uom or ""})
    page = trier(lignes, tri)[debut:debut + PAR_PAGE]
    for l in page:
        description = texte_brut(l.pop("_description", None))
        # une description qui répète le nom n'apprend rien
        l["description"] = "" if description.lower() in (l["nom"].lower(), l["code"].lower()) else description
    return {"articles": page, "total": len(lignes), "suite": debut + len(page) < len(lignes), "liste_prix": LISTE_PRIX, "tri": tri}
