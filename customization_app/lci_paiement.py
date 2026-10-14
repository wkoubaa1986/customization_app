"""Plan de paiement d'une « Liste Commande Import » (14/10/2026).

« Dans le plan de chargement, j'ajoute le plan de paiement : le montant, quand il sera exécuté, et le type TT ou
LC », « basé sur le montant de chaque conteneur », saisi dans la fenêtre « Répartition en conteneurs ».

Les échéances vivent dans une vraie table (`plan_paiement`, interrogeable pour une prévision de trésorerie) ; chacune
vise un conteneur (C1…), ou toute la commande si le conteneur est vide. Le montant se saisit directement, ou en % du
montant DU CONTENEUR (de la commande sans conteneur) : le % fait alors foi et le montant suit les changements de prix
ou de répartition. Le dialogue de répartition les édite conteneur par conteneur (`paiements` de chaque conteneur du
plan) ; à l'enregistrement, `remplacer_par_conteneur` réécrit les échéances des conteneurs (les échéances sans
conteneur ne bougent pas). Le tout part dans le PDF du plan de chargement.
"""

from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import flt

TYPES = ("TT", "LC")


def part(base, pourcentage):
    """`pourcentage` % de `base`, arrondi au centime « au plus proche, ,5 vers le haut » — comme l'écran
    (Math.round) : 30 % de 181 483,25 = 54 444,975 → 54 444,98, pas 54 444,97 (arrondi flottant). PURE."""
    return float((Decimal(str(flt(base))) * Decimal(str(flt(pourcentage))) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP))


CHAMPS = ("libelle", "type_paiement", "date_execution", "pourcentage", "solde", "montant")


def _get(e, k):
    return e.get(k) if hasattr(e, "get") else getattr(e, k, None)


def _set(e, k, v):
    # une ligne de table enregistrée (Document) ne prend pas e[k] = v
    if isinstance(e, dict):
        e[k] = v
    else:
        setattr(e, k, v)


def calculer(echeances, base_commande, bases_conteneurs):
    """Pose le montant des échéances en % et rend {"total", "reste", "inconnus"}. PURE (rows = dicts ou Documents).

    `bases_conteneurs` : {n° de conteneur: montant du conteneur} ; `inconnus` : les n° de conteneur cités qui
    n'existent pas dans le plan (leur % ne peut pas se calculer : montant laissé tel quel)."""
    total, inconnus = 0.0, []
    cumul = {}   # par base (n° de conteneur, 0 = commande) : % cumulé, montants déjà posés en %
    fixes = {}   # par base : somme des échéances qui ne sont pas « = reste »
    soldes = []
    for e in echeances:
        no = int(_get(e, "conteneur") or 0)
        base = flt(base_commande) if not no else bases_conteneurs.get(no)
        if no and base is None:
            inconnus.append(no)
            fixes[no] = fixes.get(no, 0.0) + flt(_get(e, "montant"))
            continue
        if _get(e, "solde"):
            soldes.append((e, no, flt(base)))
            continue
        if flt(_get(e, "pourcentage")) > 0:
            pct, deja = cumul.get(no, (0.0, 0.0))
            pct += flt(_get(e, "pourcentage"))
            # l'échéance qui complète 100 % prend le reste : 30 % + 70 % de 62 770,95 font 62 770,95, pas ,96
            montant = round(flt(base) - deja, 2) if abs(pct - 100) < 1e-6 else part(base, _get(e, "pourcentage"))
            _set(e, "montant", montant)
            cumul[no] = (pct, deja + montant)
        fixes[no] = fixes.get(no, 0.0) + flt(_get(e, "montant"))
    # « = reste » : le conteneur (ou la commande) moins ses autres paiements — le premier solde prend tout le reste,
    # un second éventuel ne prend rien (jamais négatif)
    for e, no, base in soldes:
        reste = max(round(base - fixes.get(no, 0.0), 2), 0.0)
        _set(e, "montant", reste)
        _set(e, "pourcentage", 0)
        fixes[no] = fixes.get(no, 0.0) + reste
    total = sum(fixes.values())
    return {"total": round(total, 2), "reste": round(flt(base_commande) - total, 2), "inconnus": sorted(set(inconnus))}


def par_conteneur(echeances):
    """{n° de conteneur: [échéances en dicts]} — pour le dialogue de répartition. PURE."""
    out = {}
    for e in echeances:
        no = int(_get(e, "conteneur") or 0)
        if no:
            out.setdefault(no, []).append({k: _get(e, k) for k in CHAMPS})
    return out


def lignes_depuis_plan(conteneurs):
    """Les échéances saisies dans le dialogue (`paiements` de chaque conteneur) → lignes de table, avec le n° FINAL
    du conteneur (le dialogue renumérote en retirant les conteneurs vides). Une ligne sans date ni montant ni % est
    ignorée (ligne ajoutée puis laissée vide). PURE."""
    out = []
    for c in conteneurs or []:
        for p in c.get("paiements") or []:
            if not (p.get("date_execution") or flt(p.get("montant")) or flt(p.get("pourcentage")) or p.get("solde")):
                continue
            ligne = {k: p.get(k) for k in CHAMPS}
            ligne["conteneur"] = int(c.get("no") or 0)
            ligne["type_paiement"] = ligne["type_paiement"] if ligne["type_paiement"] in TYPES else "TT"
            ligne["solde"] = 1 if ligne.get("solde") else 0
            out.append(ligne)
    return out


def remplacer_par_conteneur(doc, conteneurs):
    """Réécrit les échéances DES CONTENEURS depuis le plan du dialogue ; celles de toute la commande restent."""
    garder = [e for e in (doc.get("plan_paiement") or []) if not int(e.conteneur or 0)]
    doc.set("plan_paiement", [])
    for e in garder:
        doc.append("plan_paiement", {k: e.get(k) for k in CHAMPS + ("conteneur",)})
    for ligne in lignes_depuis_plan(conteneurs):
        doc.append("plan_paiement", ligne)


def base_a_payer(montant_retenu, bases_conteneurs):
    """Ce qu'il y a à payer : le total du plan de chargement (ce qui part réellement), ou le montant retenu tant qu'il
    n'y a pas de plan. PURE."""
    return round(sum(bases_conteneurs.values()), 2) if bases_conteneurs else flt(montant_retenu)


def tri(echeances):
    """Dans l'ordre où elles s'exécutent (date, puis ordre de saisie). PURE."""
    return sorted(echeances, key=lambda e: (str(e.get("date_execution") or "9999"), e.get("idx") or 0))


def appliquer(doc):
    """Depuis `validate` de la liste : montants en %, total planifié et reste. Un conteneur cité qui n'existe plus
    (plan refait) ne bloque pas l'enregistrement : il est signalé."""
    from customization_app.lci_conteneurs import plan_enregistre

    echeances = doc.get("plan_paiement") or []
    bases = {int(c["no"]): flt(c["montant"]) for c in plan_enregistre(doc).get("conteneurs") or []} if echeances else {}
    res = calculer(echeances, doc.montant_retenu, bases)
    doc.total_paiements = res["total"]
    doc.reste_a_planifier = round(base_a_payer(doc.montant_retenu, bases) - res["total"], 2)
    if res["inconnus"]:
        frappe.msgprint(_("Plan de paiement : le(s) conteneur(s) {0} n'existe(nt) pas dans le plan de chargement "
                          "enregistré — leur % n'est pas calculé.").format(", ".join("C%d" % n for n in res["inconnus"])),
                        indicator="orange", alert=True)
