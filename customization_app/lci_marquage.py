"""Marques d'expédition par article d'une « Liste Commande Import » (16/10/2026).

« En plus, regarde le cas de ma commande : est-ce que je peux le faire par IA, par exemple brand Modèle A and so on ? »
Le fournisseur marque les cartons ; plutôt que nos codes (AP-C+-EP-7-MA-SM), une marque courte et lisible par unité et par
accessoire : « ECOPURIUM · MODEL A », « AQUAJAD · MODEL B », « FAUCET DELUXE CHROME »… proposée par l'IA (AI Settings :
clé et modèle OpenAI) — PUIS, décision du 16/10 au soir : la marque reste ABSTRAITE (douane, transporteur) = NOTRE CODE ARTICLE
(`proposition_codes`, par défaut et sans clic) ; l'IA n'est plus proposée dans l'interface (code gardé, `ia=1`) ; relue et
corrigée dans la fenêtre, mémorisée sur la commande (`marques_articles`, JSON
{clé: marque} — clé = nom de la ligne pour une unité, « acc:<code> » pour un accessoire). Sans clé OpenAI : une proposition
déterministe (marque commerciale + lettre par modèle dans l'ordre de la liste). Le PDF du plan de chargement imprime la
marque sous chaque article et dans le bloc « Shipping marks » (accessoires « for ECOPURIUM · MODEL A »).
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import flt

CHAMP = "marques_articles"
LONGUEUR_MAX = 28


def _lignes_a_marquer(doc) -> list:
    """Les unités retenues (une entrée par ligne) et les accessoires embarqués (une entrée par code), avec de quoi nommer."""
    from customization_app.customize_erpnext.doctype.liste_commande_import.liste_commande_import import _adds
    from customization_app.lci_observation import qty_retenue

    out, acc = [], {}
    marques = {r.item_code: (frappe.db.get_value("Item", r.item_code, "brand") or "") for r in doc.articles if r.item_code}
    for r in doc.articles:
        if (r.decision or "") == "Abandonné" or qty_retenue(r) <= 0:
            continue
        out.append({"cle": r.name, "genre": "unite", "code": r.item_code or "", "nom": r.item_name or "", "nom_traduit": r.get("item_name_traduit") or "",
                    "marque_commerciale": marques.get(r.item_code) or "", "groupe": (frappe.db.get_value("Item", r.item_code, "item_group") or "") if r.item_code else "",
                    "qty": qty_retenue(r)})
        for a in _adds(r):
            code = a.get("item_code") or a.get("item_name") or ""
            if code and code not in acc:
                acc[code] = {"cle": "acc:" + code, "genre": "accessoire", "code": code, "nom": a.get("item_name") or code,
                             "nom_traduit": a.get("item_name_traduit") or "", "marque_commerciale": a.get("brand") or "", "groupe": "", "qty": 0}
    return out + list(acc.values())


def _nettoyer(m: str) -> str:
    m = re.sub(r"\s+", " ", (m or "")).strip(" ·-").upper()
    return m[:LONGUEUR_MAX].rstrip(" ·-")


def proposition_deterministe(lignes: list) -> dict:
    """Sans IA : « MARQUE · MODEL A/B/C… » par marque commerciale dans l'ordre, les accessoires par leur nom traduit. PURE."""
    out, compteur = {}, {}
    for l in lignes:
        if l["genre"] == "unite":
            marque = (l.get("marque_commerciale") or "AQUAWORLD").upper()
            n = compteur.get(marque, 0)
            compteur[marque] = n + 1
            lettre = chr(ord("A") + n) if n < 26 else str(n + 1)
            out[l["cle"]] = _nettoyer("%s · MODEL %s" % (marque, lettre))
        else:
            out[l["cle"]] = _nettoyer((l.get("nom_traduit") or l.get("nom") or l["code"]).replace(",", ""))
    return out


def proposition_codes(lignes: list) -> dict:
    """Par défaut (choix du 16/10/2026 : « AQUAWORLD · REF code, c'est mieux pour mes employés ») : la marque = NOTRE code article,
    celui qu'on retrouve à la réception, sur le bon et dans le stock ; une ligne libre sans code garde le nom déterministe. PURE."""
    deter = proposition_deterministe(lignes)
    return {l["cle"]: (_nettoyer(l["code"]) if l.get("code") else deter[l["cle"]]) for l in lignes}


def _prompt(lignes: list) -> str:
    desc = "\n".join("- key=%s | kind=%s | code=%s | name=%s | english=%s | brand=%s | group=%s" % (
        l["cle"], l["genre"], l["code"], l["nom"], l.get("nom_traduit") or "", l.get("marque_commerciale") or "", l.get("groupe") or "") for l in lignes)
    return ("You write SHIPPING MARKS for cartons of a container shipment of reverse-osmosis water purifiers and accessories, "
            "for a Chinese supplier. For each line give ONE short mark in English, uppercase, max %d characters, unique per distinct "
            "product, easy to stencil: for units use 'BRAND · MODEL X' where BRAND is the commercial brand (fallback AQUAWORLD) and "
            "MODEL X is a letter (A, B, C…) numbered within each brand in list order, optionally followed by one distinguishing word "
            "(e.g. 'ECOPURIUM · MODEL A COMPACT+'); for accessories use the product nature and key spec ('FAUCET DELUXE CHROME', "
            "'MEMBRANE 75 GPD VONTRON', 'TANK 20G'). Two lines that are the same product must get the same mark. "
            "Answer ONLY with a JSON object {key: mark}.\n\n%s" % (LONGUEUR_MAX, desc))


def proposition_ia(lignes: list) -> dict:
    """Les marques par OpenAI (AI Settings). None si pas de clé ou si l'appel échoue : on repliera sur le déterministe."""
    try:
        ai = frappe.get_single("AI Settings")
        api_key = ai.get_password("openai_api_key") if hasattr(ai, "get_password") else ai.openai_api_key
        model = ai.open_ai_model or "gpt-4o-mini"
    except Exception:
        return None
    if not api_key or not lignes:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        rep = client.chat.completions.create(model=model, messages=[{"role": "user", "content": _prompt(lignes)}],
                                             response_format={"type": "json_object"})
        brut = json.loads(rep.choices[0].message.content or "{}")
    except Exception:
        frappe.log_error(frappe.get_traceback()[-1500:], "LCI marques IA")
        return None
    cles = {l["cle"] for l in lignes}
    out = {k: _nettoyer(str(v)) for k, v in brut.items() if k in cles and str(v).strip()}
    return out or None


def marques_enregistrees(doc) -> dict:
    try:
        return json.loads(doc.get(CHAMP) or "{}") or {}
    except Exception:
        return {}


@frappe.whitelist()
def proposer(docname, ia=0):
    """La liste à marquer, avec la proposition — par défaut nos codes article (`ia=0`), sinon des noms lisibles par l'IA
    (`ia=1`, repli déterministe) — et ce qui est déjà mémorisé."""
    from customization_app.liste_commande_import import _guard

    _guard()
    doc = frappe.get_doc("Liste Commande Import", docname)
    lignes = _lignes_a_marquer(doc)
    deter = proposition_codes(lignes)
    prop = (proposition_ia(lignes) if int(ia or 0) else None) or {}
    source = "ia" if prop else "codes"
    deja = marques_enregistrees(doc)
    for l in lignes:
        l["proposee"] = prop.get(l["cle"]) or deter.get(l["cle"]) or ""
        l["marque"] = deja.get(l["cle"]) or l["proposee"]
    return {"lignes": lignes, "source": source, "deja": bool(deja)}


@frappe.whitelist(methods=["POST"])
def enregistrer(docname, marques):
    """Mémorise {clé: marque} sur la commande (sans toucher `modified` : le formulaire ouvert ne devient pas périmé)."""
    from customization_app.liste_commande_import import _guard

    _guard()
    marques = frappe.parse_json(marques) if isinstance(marques, str) else (marques or {})
    propre = {k: _nettoyer(str(v)) for k, v in marques.items() if str(v or "").strip()}
    frappe.db.set_value("Liste Commande Import", docname, CHAMP, json.dumps(propre, ensure_ascii=False), update_modified=False)
    return {"n": len(propre)}
