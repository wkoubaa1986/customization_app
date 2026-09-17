"""
Observations de ligne d'une « Liste Commande Import ».

Quand on ajuste une cotation (quantité retenue, prix contre-proposé, abandon),
le fournisseur doit lire NOIR SUR BLANC ce qui a changé par rapport à ce qu'il
a coté : sans cela il compare deux classeurs à la main et la négociation se
perd en allers-retours.

Deux étages, volontairement séparés :

  1. la TRACE (`trace_ligne`) — purement mécanique, recalculée à chaque
     enregistrement : « 900 demandées, 600 cotées, 750 retenues, 40,10 → 38,00 ».
     Elle ne se périme jamais puisqu'elle se relit dans les champs ;
  2. l'OBSERVATION (`observation`) — la phrase qui part au fournisseur, dans SA
     langue. Écrite automatiquement depuis la trace, reformulable par l'IA.

Règle d'écrasement : l'observation n'est réécrite que si elle est vide ou
identique au dernier texte généré (`observation_auto`). Dès que quelqu'un la
retouche à la main, elle est à lui et l'automatisme n'y touche plus.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from customization_app.liste_commande_import import (
    DOCTYPE,
    _chat_json,
    _chunks,
    _guard,
    _target_rows,
)

TOL = 0.001          # deux quantités plus proches que ça sont la même
TOL_PRIX = 0.00001   # idem pour les prix (0,195 USD est une valeur courante)


# --------------------------------------------------------------- valeurs

def qty_retenue(row):
    """Quantité qui fait foi : la nôtre, sauf contre-proposition explicite."""
    return flt(row.get("qty_cible")) or flt(row.get("qty"))


def prix_retenu(row):
    """Prix qui fait foi : notre contre-proposition, sinon celui du fournisseur."""
    return flt(row.get("prix_cible_negocie")) or flt(row.get("prix_fournisseur"))


def total_fichier_ligne(row):
    """Montant de la ligne TEL QUE LE FOURNISSEUR L'A ÉCRIT.

    Sa colonne « Amount » quand il en a une ; à défaut sa quantité × son prix.
    Jamais notre quantité : c'est précisément la différence qu'on cherche à
    voir dans le rapprochement.
    """
    if flt(row.get("total_fichier")) > 0:
        return flt(row.get("total_fichier"))
    prix = flt(row.get("prix_fournisseur"))
    if prix <= 0:
        return 0.0
    qty = flt(row.get("qty_fournisseur")) or flt(row.get("qty"))
    return qty * prix


# ----------------------------------------------------------------- trace

def trace_ligne(row):
    """Liste des écarts d'une ligne, du plus structurant au plus fin.

    `row` : un dict ou un Document — tout objet qui répond à `.get(champ)`.
    """
    out = []
    qty = flt(row.get("qty"))
    qty_frn = flt(row.get("qty_fournisseur"))
    qty_ret = qty_retenue(row)
    prix_frn = flt(row.get("prix_fournisseur"))
    prix_neg = flt(row.get("prix_cible_negocie"))
    cible = flt(row.get("prix_cible"))
    decision = row.get("decision") or ""

    if decision == "Abandonné":
        out.append({"type": "abandon"})

    # ce que LUI a changé
    if qty_frn > 0 and qty > 0 and abs(qty_frn - qty) > TOL:
        out.append({"type": "qty_fournisseur", "de": qty, "a": qty_frn})
    if qty <= 0 and qty_frn > 0:
        out.append({"type": "qty_non_demandee", "a": qty_frn})

    # ce que NOUS changeons
    base_qty = qty_frn if qty_frn > 0 else qty
    if flt(row.get("qty_cible")) > 0 and abs(qty_ret - base_qty) > TOL:
        out.append({"type": "qty_retenue", "de": base_qty, "a": qty_ret})
    if prix_neg > 0 and prix_frn > 0 and abs(prix_neg - prix_frn) > TOL_PRIX:
        out.append({"type": "prix", "de": prix_frn, "a": prix_neg,
                    "pct": (prix_neg - prix_frn) / prix_frn * 100})
    elif prix_neg > 0 and prix_frn <= 0:
        out.append({"type": "prix_demande", "a": prix_neg})

    # son prix face à notre cible initiale — l'argument de la négociation
    if prix_frn > 0 and cible > 0 and prix_frn > cible + TOL_PRIX and not prix_neg:
        out.append({"type": "au_dessus_cible", "cible": cible, "a": prix_frn,
                    "pct": (prix_frn - cible) / cible * 100})

    if decision == "Accepté" and not out:
        out.append({"type": "accepte"})
    return out


# ------------------------------------------------------------- rédaction

def _n(v, dec=2):
    """Nombre lisible : pas de zéros inutiles, jamais de notation exposant."""
    v = flt(v)
    s = f"{v:.{dec}f}".rstrip("0").rstrip(".")
    return s or "0"


def _pct(v):
    return f"{'+' if v > 0 else ''}{_n(v, 1)} %"


PHRASES = {
    "Français": {
        "abandon": "Ligne abandonnée pour cette commande.",
        "qty_fournisseur": "Vous avez coté {a} au lieu des {de} demandées.",
        "qty_non_demandee": "Ligne ajoutée par vos soins ({a}).",
        "qty_retenue": "Quantité retenue : {a} (au lieu de {de}).",
        "prix": "Prix demandé : {a} {dev} au lieu de {de} {dev} ({pct}).",
        "prix_demande": "Prix demandé : {a} {dev}.",
        "au_dessus_cible": "Votre prix de {a} {dev} dépasse notre cible de {cible} {dev} ({pct}).",
        "accepte": "Ligne acceptée en l'état.",
    },
    "English": {
        "abandon": "Line dropped from this order.",
        "qty_fournisseur": "You quoted {a} instead of the {de} requested.",
        "qty_non_demandee": "Line added on your side ({a}).",
        "qty_retenue": "Quantity retained: {a} (instead of {de}).",
        "prix": "Price requested: {a} {dev} instead of {de} {dev} ({pct}).",
        "prix_demande": "Price requested: {a} {dev}.",
        "au_dessus_cible": "Your price of {a} {dev} is above our target of {cible} {dev} ({pct}).",
        "accepte": "Line accepted as quoted.",
    },
    "Deutsch": {
        "abandon": "Position für diese Bestellung gestrichen.",
        "qty_fournisseur": "Sie haben {a} statt der angefragten {de} angeboten.",
        "qty_non_demandee": "Von Ihnen ergänzte Position ({a}).",
        "qty_retenue": "Übernommene Menge: {a} (statt {de}).",
        "prix": "Gewünschter Preis: {a} {dev} statt {de} {dev} ({pct}).",
        "prix_demande": "Gewünschter Preis: {a} {dev}.",
        "au_dessus_cible": "Ihr Preis von {a} {dev} liegt über unserem Ziel von {cible} {dev} ({pct}).",
        "accepte": "Position wie angeboten angenommen.",
    },
    "Español": {
        "abandon": "Línea retirada de este pedido.",
        "qty_fournisseur": "Ha cotizado {a} en lugar de las {de} solicitadas.",
        "qty_non_demandee": "Línea añadida por usted ({a}).",
        "qty_retenue": "Cantidad retenida: {a} (en lugar de {de}).",
        "prix": "Precio solicitado: {a} {dev} en lugar de {de} {dev} ({pct}).",
        "prix_demande": "Precio solicitado: {a} {dev}.",
        "au_dessus_cible": "Su precio de {a} {dev} supera nuestro objetivo de {cible} {dev} ({pct}).",
        "accepte": "Línea aceptada tal cual.",
    },
    "العربية": {
        "abandon": "تم استبعاد هذا البند من الطلب.",
        "qty_fournisseur": "سعّرتم {a} بدل {de} المطلوبة.",
        "qty_non_demandee": "بند أضفتموه ({a}).",
        "qty_retenue": "الكمية المعتمدة: {a} (بدل {de}).",
        "prix": "السعر المطلوب: {a} {dev} بدل {de} {dev} ({pct}).",
        "prix_demande": "السعر المطلوب: {a} {dev}.",
        "au_dessus_cible": "سعركم {a} {dev} يتجاوز هدفنا {cible} {dev} ({pct}).",
        "accepte": "تم قبول البند كما هو.",
    },
}


def texte_trace(changes, langue="Français", devise="USD"):
    """Rend la trace en phrases, dans la langue de la cotation."""
    mots = PHRASES.get(langue) or PHRASES["Français"]
    out = []
    for c in changes:
        gabarit = mots.get(c["type"])
        if not gabarit:
            continue
        out.append(gabarit.format(
            a=_n(c.get("a"), 4 if c["type"].startswith("prix") or c["type"] == "au_dessus_cible" else 2),
            de=_n(c.get("de"), 4 if c["type"].startswith("prix") or c["type"] == "au_dessus_cible" else 2),
            cible=_n(c.get("cible"), 4),
            pct=_pct(flt(c.get("pct"))),
            dev=devise,
        ))
    return " ".join(out)


def maj_ligne(row, langue="Français", devise="USD"):
    """Recalcule trace + observation d'une ligne. Rend True si l'observation a bougé.

    L'observation manuelle est sacrée : on ne réécrit que le texte qu'on a
    soi-même écrit la fois d'avant.
    """
    changes = trace_ligne(row)
    texte = texte_trace(changes, langue, devise)
    row.trace_changements = json.dumps(changes, ensure_ascii=False) if changes else ""

    actuelle = (row.observation or "").strip()
    auto = (row.observation_auto or "").strip()
    if actuelle and actuelle != auto:
        return False          # texte de l'utilisateur : on n'y touche pas
    if actuelle == texte:
        return False
    row.observation = texte
    row.observation_auto = texte
    return True


# ------------------------------------------------------------------- IA

SYS_OBS = (
    "Tu rédiges les observations d'une demande de cotation adressée à un "
    "fournisseur. Pour chaque ligne on te donne les écarts constatés, déjà "
    "formulés mécaniquement. Réécris-les en UNE phrase courte, polie et "
    "commerciale, dans la langue demandée. Reprends TOUS les chiffres sans en "
    "inventer ni en arrondir. Pas de formule de politesse d'ouverture ni de "
    "signature. Réponds en JSON : {\"lignes\": [{\"i\": <indice>, \"texte\": \"...\"}]}"
)


@frappe.whitelist()
def ai_observations(docname, row_names=None):
    """Reformule les observations des lignes qui portent un écart."""
    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    langue = doc.langue_cible or "Français"
    devise = doc.devise or "USD"
    rows = [r for r in _target_rows(doc, row_names) if trace_ligne(r)]
    if not rows:
        return {"maj": 0, "message": _("Aucun écart à commenter.")}

    maj = 0
    for lot in _chunks(rows, 20):
        charge = [{
            "i": i,
            "article": r.item_name_traduit or r.item_name or r.item_code or "",
            "ecarts": texte_trace(trace_ligne(r), "Français", devise),
            "note_libre": (r.observation or "") if (r.observation or "").strip()
                          != (r.observation_auto or "").strip() else "",
        } for i, r in enumerate(lot)]
        rep = _chat_json(SYS_OBS, json.dumps(
            {"langue": langue, "devise": devise, "lignes": charge}, ensure_ascii=False))
        for item in (rep.get("lignes") or []):
            try:
                r = lot[int(item.get("i"))]
            except (TypeError, ValueError, IndexError):
                continue
            texte = (item.get("texte") or "").strip()
            if not texte:
                continue
            r.observation = texte
            r.observation_auto = texte   # reste régénérable tant qu'on n'y touche pas
            maj += 1

    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"maj": maj}
