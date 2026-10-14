"""Description du fournisseur en PUCES + comparaison point par point avec la nôtre (14/10/2026).

Le fournisseur décrit son article d'un bloc (« L11 · Reverse Osmosis System: 5 Stages … 2-Add another
1812-M80 membrane (Vontron) +5$ 3-3 Opaque filter housings … ») ; nous, en puces. Lire l'un à côté de
l'autre demandait de tout relire à la main.

L'IA rend, pour chaque ligne :
  - `puces` : SON texte remis en puces, dans la langue de la liste (une caractéristique, un composant ou
    une option par puce, chiffres et suppléments de prix recopiés tels quels) ;
  - `comparaison` : nos puces une à une face à la sienne (identique / différent / absent chez lui),
    puis ce qu'il ajoute et que nous n'avions pas demandé (en plus).

Le texte d'origine du fournisseur (`description_fournisseur`) n'est jamais touché : les puces vont dans
`description_fournisseur_puces`, la comparaison (JSON) dans `comparaison_description`.

Une ligne : appel direct depuis sa fenêtre. Toute la liste (≈ 150 lignes) : tâche de fond suivie par
`etat` (même mécanique que l'analyse de la réponse du fournisseur).
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from customization_app.liste_commande_import import DOCTYPE, LANG_NAMES, _guard, _strip_html, _target_rows

DOCTYPE_LIGNE = "Liste Commande Import Article"
STATUTS = ("identique", "different", "manquant", "en_plus")
LOT = 6
MAX_PUCES = 30
MAX_CAR = 220
PREFIXE = "lci_cmp:"
DUREE_ETAT = 3600

PROMPT = (
    "You compare two descriptions of the same water-treatment product (RO systems, housings, cartridges, "
    "membranes, valves, fittings, pumps, UV, tanks): OUR request (designation + bullet points we sent to the "
    "supplier) and the SUPPLIER's quotation text. For each item return:\n"
    "1) \"puces\": the SUPPLIER text rewritten as bullet points in {lang}: ONE characteristic, component or option "
    "per bullet. Split anything joined by \"+\", commas or \"and\" into separate bullets (\"PP Filter + GAC Filter "
    "+ CTO Filter\" gives 3 bullets, \"Instruction book and drain valve\" gives 2); the product type, the number of "
    "stages, the base and the pump each get their own bullet. But never split inside parentheses, and keep a "
    "component's own details (size, length, packaging, brand, price supplement) in its bullet: \"Add another "
    "Normal Faucet (7 cm length) +1.85$\" stays ONE bullet. Keep every number, size, thread, unit, capacity, "
    "brand, model and price supplement exactly as written (e.g. \"+5$\"); drop line references (\"L11 ·\"), item "
    "numbering (\"1-\", \"2-\", \"3-4.0 Gallon\" is \"4.0 Gallon\"), labels like \"Included Components:\" and repetitions (a stage count said twice "
    "is one bullet); never add anything that is not in his text. At most 25 bullets, no leading bullet sign.\n"
    "2) \"comparaison\": a point-by-point alignment. First one entry per OUR bullet, in our order: "
    "{{\"notre\": our bullet, \"fournisseur\": his matching bullet or null, \"statut\": \"identique\" | "
    "\"different\" | \"manquant\"}}. \"identique\" = same characteristic and same value, whatever the wording "
    "(\"Tank: 3.2 G\" vs \"3.2 Gallon plastic tank\" is identique); \"different\" = same aspect but another "
    "value or type (clover faucet vs normal faucet, 75 GPD vs 50 GPD); \"manquant\" = he does not mention it. "
    "Then one entry per supplier bullet that matches none of ours: {{\"notre\": null, \"fournisseur\": his "
    "bullet, \"statut\": \"en_plus\"}}. Use the bullets of your \"puces\" list, word for word, on the supplier "
    "side. One of his bullets may face several of ours (\"7 Stages with Pump\" answers both \"Number of stages: 7\" "
    "and \"Pump\"); a bullet of his that faces at least one of ours is never listed again as en_plus. "
    "For \"different\" and \"en_plus\" add \"note\": max 10 words in French "
    "(what differs, or the extra cost). Ignore language and wording.\n"
    "Answer JSON only: {{\"resultats\": [{{\"cle\": \"…\", \"puces\": [\"…\"], \"comparaison\": [{{\"notre\": "
    "\"…\", \"fournisseur\": \"…\", \"statut\": \"…\", \"note\": \"…\"}}]}}]}}"
)


# ----------------------------------------------------------------- fonctions pures

def texte_fournisseur(texte):
    """Son texte sans la référence de ligne ajoutée à l'import (« L11 · »). PURE."""
    return re.sub(r"^\s*L\d+\s*·\s*", "", str(texte or "")).strip()


def puces_de(texte):
    """« • a\\n• b » (ou du HTML <br>) → ["a", "b"]. PURE."""
    lignes = re.split(r"\n|<br\s*/?>", str(texte or ""))
    out = []
    for l in lignes:
        l = re.sub(r"<[^>]+>", "", l)
        l = re.sub(r"^\s*[•\-\*·]\s*", "", l).strip()
        if l:
            out.append(l)
    return out


def recoller(puces):
    """Une puce coupée au milieu d'une parenthèse (« Normal Faucet (7 cm » / « length) +1.85$ ») est recollée à la
    précédente. PURE."""
    out = []
    for p in puces:
        if out and out[-1].count("(") > out[-1].count(")") and p.count(")") > p.count("("):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def _txt(v):
    v = re.sub(r"^\s*[•\-\*·]\s*", "", str(v or "")).strip()
    return v[:MAX_CAR]


def normaliser(res):
    """La réponse de l'IA pour UNE ligne → {"puces": [...], "comparaison": [...]}, ou None si inutilisable. PURE.

    Le statut est recalé sur ce qui est rempli : sans notre puce c'est « en plus », sans la sienne c'est
    « absent chez lui » ; un statut inconnu entre deux puces remplies devient « différent » (à regarder)."""
    if not isinstance(res, dict):
        return None
    puces = recoller([_txt(p) for p in (res.get("puces") or []) if _txt(p)])[:MAX_PUCES]
    comparaison, deja_en_face = [], set()
    for c in res.get("comparaison") or []:
        if not isinstance(c, dict):
            continue
        notre, frn = _txt(c.get("notre")), _txt(c.get("fournisseur"))
        if not notre and not frn:
            continue
        statut = c.get("statut") if c.get("statut") in STATUTS else None
        if not notre:
            statut = "en_plus"
        elif not frn:
            statut = "manquant"
        elif statut not in ("identique", "different"):
            statut = "different"
        # une de ses puces déjà mise en face d'une des nôtres n'est pas « en plus » une seconde fois
        if statut == "en_plus" and frn.casefold() in deja_en_face:
            continue
        if notre and frn:
            deja_en_face.add(frn.casefold())
        ligne = {"notre": notre or None, "fournisseur": frn or None, "statut": statut}
        note = str(c.get("note") or "").strip()[:120]
        if note and statut in ("different", "en_plus"):
            ligne["note"] = note
        comparaison.append(ligne)
    if not puces and not comparaison:
        return None
    return {"puces": puces, "comparaison": comparaison}


def resume(comparaison):
    """{statut: nombre} d'une comparaison. PURE."""
    out = {s: 0 for s in STATUTS}
    for c in comparaison or []:
        if c.get("statut") in out:
            out[c["statut"]] += 1
    return out


def texte_puces(puces):
    """["a", "b"] → « • a\\n• b ». PURE."""
    return "\n".join("• " + p for p in puces or [])


# ----------------------------------------------------------------- IA

def _langue(doc):
    return LANG_NAMES.get(doc.langue_cible or "English", "English")


def notre_cote(row, langue_cible):
    """Ce que NOUS avons envoyé : désignation et puces dans la langue de la liste (la traduction), sinon
    en français."""
    traduit = (langue_cible or "Français") != "Français"
    designation = (row.item_name_traduit if traduit else None) or row.item_name or row.item_code or ""
    puces = puces_de(row.description_traduite) if traduit and (row.description_traduite or "").strip() else []
    if not puces:
        puces = puces_de(row.description)
    return {"code": row.item_code or "", "designation": designation, "puces": puces}


def _payload(row, langue_cible):
    return {"cle": row.name, "notre": notre_cote(row, langue_cible),
            "fournisseur": texte_fournisseur(row.description_fournisseur)[:2500]}


def calculer(doc, rows, progres=None):
    """{nom de ligne: résultat normalisé} — l'IA par lots de LOT lignes, 4 lots en parallèle."""
    from concurrent.futures import ThreadPoolExecutor

    from customization_app.liste_commande_import import _completion_json, _contexte_ia

    contexte = _contexte_ia()
    systeme = PROMPT.format(lang=_langue(doc))
    lots = [rows[i:i + LOT] for i in range(0, len(rows), LOT)]

    payloads = [json.dumps({"lignes": [_payload(r, doc.langue_cible) for r in lot]}, ensure_ascii=False) for lot in lots]
    erreurs = []

    def un_lot(user):
        # un lot en échec ne fait pas tomber les autres (pas de Frappe dans un thread : l'erreur est notée)
        try:
            return _completion_json(contexte, systeme, user) or {}
        except Exception as e:
            erreurs.append(str(e)[:300])
            return {}

    out = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for k, rep in enumerate(pool.map(un_lot, payloads), start=1):
            for r in rep.get("resultats") or []:
                propre = normaliser(r)
                if propre and r.get("cle"):
                    out[r["cle"]] = propre
            if progres:
                progres(k, len(lots))
    if erreurs:
        frappe.log_error(title="LCI — comparaison des descriptions : lots en échec",
                         message="\n".join(erreurs))
        if not out:
            frappe.throw(_("L'IA n'a pas répondu : {0}").format(erreurs[0]))
    return out


def ecrire(docname, resultats):
    """Écrit puces + comparaison sur les lignes, puis date la liste : un formulaire ouvert avant ne pourra pas
    les écraser en enregistrant (Frappe refuse un document modifié depuis son ouverture)."""
    for nom, res in resultats.items():
        frappe.db.set_value(DOCTYPE_LIGNE, nom, {
            "description_fournisseur_puces": texte_puces(res["puces"]),
            "comparaison_description": json.dumps(res["comparaison"], ensure_ascii=False),
        }, update_modified=False)
    if resultats:
        frappe.db.set_value(DOCTYPE, docname, "modified", now_datetime(), update_modified=False)


def _lignes_a_traiter(doc, row_names=None):
    return [r for r in _target_rows(doc, row_names) if texte_fournisseur(r.description_fournisseur)]


# ----------------------------------------------------------------- API

@frappe.whitelist()
def comparer_ligne(docname, row_name):
    """Une seule ligne, depuis sa fenêtre : rend {puces, comparaison} et l'enregistre."""
    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    rows = _lignes_a_traiter(doc, [row_name])
    if not rows:
        frappe.throw(_("Cette ligne n'a pas de description du fournisseur."))
    res = calculer(doc, rows)
    if row_name not in res:
        frappe.throw(_("L'IA n'a pas rendu de comparaison exploitable. Réessayez."))
    ecrire(docname, res)
    frappe.db.commit()
    return res[row_name]


@frappe.whitelist()
def lancer(docname, row_names=None):
    """Toute la liste (ou les lignes cochées) en tâche de fond ; rend la clé de suivi et le nombre de lignes."""
    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    noms = [r.name for r in _lignes_a_traiter(doc, row_names)]
    if not noms:
        frappe.throw(_("Aucune ligne n'a de description du fournisseur : importez d'abord sa réponse."))
    cle = "%s%s:%s" % (PREFIXE, docname, frappe.generate_hash(length=10))
    frappe.cache().set_value(cle, {"etat": "en attente", "total": len(noms)}, expires_in_sec=DUREE_ETAT)
    frappe.enqueue("customization_app.lci_comparaison.job", queue="long", timeout=1800,
                   docname=docname, noms=noms, cle=cle)
    return {"cle": cle, "lignes": len(noms)}


@frappe.whitelist()
def etat(cle):
    """{etat: en attente | en cours | termine | echec, fait, total, lignes, erreur}."""
    _guard()
    if not str(cle or "").startswith(PREFIXE):
        frappe.throw(_("Clé de suivi inconnue."))
    return frappe.cache().get_value(cle) or {"etat": "inconnu"}


def job(docname, noms, cle):
    """La tâche de fond : calcule, écrit, dépose le bilan sous la clé de suivi."""
    _guard()

    def progres(fait, total):
        frappe.cache().set_value(cle, {"etat": "en cours", "fait": fait, "total": total}, expires_in_sec=DUREE_ETAT)

    try:
        doc = frappe.get_doc(DOCTYPE, docname)
        rows = _lignes_a_traiter(doc, noms)
        progres(0, -(-len(rows) // LOT))
        res = calculer(doc, rows, progres=progres)
        ecrire(docname, res)
        frappe.db.commit()
        bilan = {s: 0 for s in STATUTS}
        for r in res.values():
            for s, n in resume(r["comparaison"]).items():
                bilan[s] += n
        frappe.cache().set_value(cle, {"etat": "termine", "lignes": len(res), "demandees": len(rows), "bilan": bilan},
                                 expires_in_sec=DUREE_ETAT)
    except Exception as e:
        frappe.cache().set_value(cle, {"etat": "echec", "erreur": str(e)[:1000]}, expires_in_sec=DUREE_ETAT)
        frappe.log_error(title="LCI — puces et comparaison des descriptions", message=frappe.get_traceback())
