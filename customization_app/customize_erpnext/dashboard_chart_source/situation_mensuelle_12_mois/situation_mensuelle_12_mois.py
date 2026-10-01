"""Graphe « Situation Mensuelle » de l'onglet Comptabilité — même calcul que la page « Situation
mensuelle » (omissions comprises). Filtres (entonnoir du graphe) :
- 12 derniers mois (défaut) ;
- Un mois : les 5 barres d'un seul mois ;
- Une année (mois par mois) : janvier → décembre (ou le mois en cours) de l'année choisie ;
- Par année : une colonne par année (2023, 2024…) ;
- Total : depuis le début, bénéfice initial compris (vue « Total » de l'ancien rapport).
Un clic sur le graphe ouvre la page de détail (public/js/situation_mensuelle_clic.js)."""

from datetime import date

import frappe
from frappe.utils import getdate, nowdate

VUES = ("12 derniers mois", "Un mois", "Une année (mois par mois)", "Par année", "Total")


def _ligne(libelle, c):
    return {"court": libelle, "ventes": c["ventes"], "cout": c["cout"]["net"], "charges": c["charges"]["net"],
            "tva": c["tva"]["net"], "benefice": c["benefice"]}


def points(vue=None, mois=None, annee=None) -> list[dict]:
    from customization_app import situation_mensuelle as SM

    vue = vue if vue in VUES else "12 derniers mois"
    if vue == "Un mois":
        p = SM.periode("mois", mois)
        return [_ligne(p["libelle"], SM.calculer(p))]
    if vue == "Total":
        return [_ligne("Total depuis 2023", SM.calculer(SM.periode("annee", annee="Total")))]
    if vue == "Par année":
        return [_ligne(str(a), SM.calculer(SM.periode("annee", annee=str(a))))
                for a in range(2023, getdate(nowdate()).year + 1)]
    if vue == "Une année (mois par mois)":
        an = int(annee or getdate(nowdate()).year)
        aujourd = getdate(nowdate())
        dernier = 12 if an < aujourd.year else aujourd.month
        out = []
        for m in range(1, dernier + 1):
            if date(an, m, 1) < SM.DEBUT:
                continue
            p = SM.periode("mois", SM.libelle_mois(date(an, m, 1)))
            out.append(_ligne("%s %s" % (SM.MOIS_COURTS[m - 1], str(an)[2:]), SM.calculer(p)))
        return out
    return SM.serie()


@frappe.whitelist()
def get(chart_name=None, chart=None, no_cache=None, filters=None, from_date=None, to_date=None,
        timespan=None, time_interval=None, heatmap_year=None):
    from customization_app.situation_mensuelle import _lecture

    _lecture()
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    s = points(filters.get("vue"), filters.get("mois"), filters.get("annee"))
    jeu = lambda nom, cle, signe=1: {"name": nom, "values": [round(signe * m[cle], 2) for m in s]}
    return {
        "labels": [m["court"] for m in s],
        # Bénéfice et perte en deux séries : un mois en perte sort en rouge foncé, pas en vert.
        "datasets": [jeu("Ventes livrées", "ventes"), jeu("Coût de la marchandise", "cout", -1),
                     jeu("Charges", "charges", -1), jeu("TVA achat", "tva", -1),
                     {"name": "Bénéfice", "values": [round(max(0, m["benefice"]), 2) for m in s]},
                     {"name": "Perte", "values": [round(min(0, m["benefice"]), 2) for m in s]}],
    }
