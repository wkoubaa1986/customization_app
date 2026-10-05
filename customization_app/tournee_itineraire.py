"""Itinéraire d'une journée TELLE QU'ELLE EST PLANIFIÉE (05/10/2026).

« 🗺️ Mon itinéraire » de Ma journée, et résumé par employé de « Générer les BL » (pour choisir le véhicule) : les
tâches dans l'ordre de leurs heures, chacune avec sa durée planifiée ; chaque trajet est mesuré par Google à son heure,
trafic habituel compris (sinon OSRM corrigé — voir tournee_google.recaler_proposition) ; départ du point de départ de
l'employé (Magasin, ou domicile réglé) juste à temps pour la 1re tâche, retour après la dernière ; stationnement et
rangement du réglage. Une tâche garde SON heure : un trajet qui ne tient pas devient un retard signalé.
Une livraison Aramex (expédiée, pas conduite — même règle que « Générer les BL ») ou une étape à plus de 150 km du
départ n'est pas une étape de la tournée : listée à part. Rien n'est réordonné ni écrit.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate

from customization_app import tournee_optimisation as T
from customization_app.generer_bl import PAYMENT_TERMS_ARAMEX
from customization_app.planning_employe import _employe_demande, _supervise

LOIN_KM = 150                                # au-delà, pas une étape conduite (expédition, erreur d'adresse…)


def journee(jour, employes: list[str] | None = None) -> dict:
    """L'itinéraire planifié de chaque employé (tous ceux qui ont des tâches ce jour-là, ou ceux demandés)."""
    jour = getdate(jour)
    cfg = T.config()
    filtres = {"starts_on": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]], "status": ["!=", "Cancelled"],
               "custom_choix_du_staff": ["in", employes] if employes else ["is", "set"]}
    taches = frappe.get_all(T.TACHE, filters=filtres, order_by="starts_on asc", limit_page_length=0,
                            fields=["name", "custom_choix_du_staff", "custom_type_dintervention", "starts_on", "ends_on", "temps",
                                    "status", "custom_client", "nom_client", "select_address", "google_map", "secteur", "details_adresse",
                                    "commande_client"])
    commandes = {t.commande_client for t in taches if t.commande_client and t.custom_type_dintervention == "Livraison"}
    aramex = set(frappe.get_all("Sales Order", filters={"name": ["in", list(commandes)], "payment_terms_template": PAYMENT_TERMS_ARAMEX},
                                pluck="name")) if commandes else set()
    centres = T._centres_secteurs()
    par_employe, hors = {}, {}
    for t in taches:
        if t.custom_type_dintervention in T.TYPES_HORS_JOURNEE:
            continue
        nom_client = t.nom_client or t.custom_client or ""
        if t.custom_type_dintervention == "Livraison" and t.commande_client in aramex:
            hors.setdefault(t.custom_choix_du_staff, []).append({"tache": t.name, "client": nom_client, "motif": _("livraison Aramex")})
            continue
        d = get_datetime(t.starts_on)
        debut = d.hour * 60 + d.minute
        duree = int((get_datetime(t.ends_on) - d).total_seconds() // 60) if t.ends_on else 0
        service = duree if 5 <= duree <= 600 else T._duree(t)
        pos = T.coordonnees_tache(t, centres)
        if not pos:
            if not (cfg["debut"] - 60 <= debut <= cfg["fin"] + 60):
                continue                     # une tâche sans lieu hors journée (« Autre » à minuit) : pas une étape
            pos = (cfg["depot"][0], cfg["depot"][1], "dépôt")
        depart = cfg["departs"].get(t.custom_choix_du_staff, cfg["depot"])
        loin = T.haversine_km(depart, pos[:2])
        if loin > LOIN_KM:
            hors.setdefault(t.custom_choix_du_staff, []).append({"tache": t.name, "client": nom_client,
                                                                 "motif": _("à {0} km du départ").format(int(loin))})
            continue
        par_employe.setdefault(t.custom_choix_du_staff, []).append({
            "tache": t.name, "client": nom_client, "type": t.custom_type_dintervention or "",
            "statut": t.status, "debut_min": debut, "original_min": debut, "service": service, "mobile": False, "fenetre": None,
            "debut": T._hm(debut), "fin": T._hm(debut + service), "lat": pos[0], "lng": pos[1], "position": pos[2]})
    if not par_employe:
        return {"date": str(jour), "employes": [], "depot": cfg["depot"], "hors_itineraire": hors}
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", filters={"name": ["in", list(par_employe)]},
                                                            fields=["name", "employee_name"])}
    # Les nœuds de la matrice : le Magasin, les domiciles réglés, puis toutes les étapes.
    points, depot_de = [cfg["depot"]], {}
    for e in par_employe:
        if e in cfg["departs"]:
            depot_de[e] = len(points)
            points.append(cfg["departs"][e])
        else:
            depot_de[e] = 0
    for e, arrets in par_employe.items():
        for a in arrets:
            a["noeud"] = len(points)
            points.append((a["lat"], a["lng"]))
    mn_brut, km, source = T.matrice(points, cfg["osrm"])
    mn = [[int(round(x * cfg["coef"])) for x in ligne] for ligne in mn_brut] if cfg["coef"] != 1 else mn_brut
    out = []
    for e, arrets in sorted(par_employe.items(), key=lambda x: noms.get(x[0], x[0])):
        d0, f0 = cfg["horaires"].get(e, (cfg["debut"], cfg["fin"]))
        out.append({"employe": e, "nom": noms.get(e, e), "depart": "domicile" if depot_de[e] else "Magasin",
                    "depart_point": points[depot_de[e]], "depot_noeud": depot_de[e],
                    "journee_min": [min(d0, arrets[0]["debut_min"]), max(f0, arrets[-1]["debut_min"] + arrets[-1]["service"])],
                    "apres": {"arrets": arrets}, "hors_itineraire": hors.get(e, [])})
    from customization_app.tournee_google import recaler_proposition
    recalage = recaler_proposition(out, jour, cfg, mn_brut, mn, km)
    for e in out:
        for a in e["apres"]["arrets"]:
            for interne in ("noeud", "debut_min", "original_min", "mobile", "fenetre", "decale", "ecart_min"):
                a.pop(interne, None)
        for interne in ("depot_noeud", "journee_min"):
            e.pop(interne, None)
    return {"date": str(jour), "employes": out, "depot": cfg["depot"], "source": source, "recalage": recalage,
            "marge": cfg["marge"], "rangement": cfg["rangement"], "coef": cfg["coef"]}


@frappe.whitelist()
def itineraire_employe(date=None, employe=None):
    """Ma journée : l'itinéraire de MON employé (un superviseur peut demander celui de n'importe qui)."""
    cible = _employe_demande(employe)
    if not cible:
        frappe.throw(_("Choisissez un employé."))
    return journee(date or frappe.utils.nowdate(), [cible])


@frappe.whitelist()
def itineraires_du_jour(date, employes=None):
    """Générer les BL : le résumé de chaque employé (km, temps de route, départ, retour) — superviseurs seulement."""
    if not _supervise():
        return {"date": str(getdate(date)), "employes": []}     # résumé simplement absent, sans message d'erreur
    liste = frappe.parse_json(employes) if isinstance(employes, str) else employes
    return journee(date, [e for e in (liste or []) if e] or None)
