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


def libelle_sans_client(titre) -> str:
    """Le nom d'une étape SANS client : la première ligne de son titre (« 🧾 Vérification stock », « ☕ Autre »…). PURE.
    L'itinéraire écrivait « Autre · Tache-09488 » pour une vérification de stock : on ne savait ni ce que c'était, ni
    qu'elle se passe au Magasin (22/11/2026)."""
    for ligne in str(titre or "").split("\n"):
        ligne = ligne.strip()
        # « ☕ Autre: Client: » (titre d'une tâche sans client) -> « ☕ Autre »
        ligne = ligne.replace(": Client:", "").strip().rstrip(":").strip()
        if ligne and ligne not in ("terminé!!!", "Annulé!!!"):
            return ligne
    return ""


def _minutes(hm) -> int:
    """« 08:45 » -> 525. PURE."""
    h, m = str(hm).split(":")[:2]
    return int(h) * 60 + int(m)


def passage_magasin(depart, retour, premier_sur_place: bool, dernier_sur_place: bool, aller: tuple, rentree: tuple,
                    matin: int, soir: int, majoration=None) -> dict:
    """Le domicile autour d'une tournée qui part du Magasin et y revient (22/11/2026). PURE.

    Demande : « j'ai l'impression qu'il calcule de là où habite l'employé, ça doit être du magasin, non ? » puis « oui
    mais généralement il passe au magasin le matin et après avoir fini la journée ». La tournée des clients se calcule
    donc Magasin → clients → Magasin, et on ajoute ce qui l'entoure : domicile → Magasin le matin (`matin` minutes sur
    place avant de partir), Magasin → domicile le soir (`soir` minutes sur place avant de rentrer).

    `depart` / `retour` : minutes où il quitte le Magasin pour le premier arrêt, et où il y revient après le dernier.
    `premier_sur_place` / `dernier_sur_place` : la première (dernière) tâche est AU Magasin (vérification de stock) — il
    y est déjà, pas de temps ajouté. `aller` / `rentree` : (minutes, km) domicile → Magasin et Magasin → domicile, hors
    heure de pointe ; `majoration(minute)` : le facteur d'un trajet qui démarre à cette minute (défaut : 1)."""
    majoration = majoration or (lambda minute: 1.0)
    sur_place_matin = 0 if premier_sur_place else max(0, int(matin))
    arrivee = depart - sur_place_matin
    aller_min = int(round(aller[0] * majoration(arrivee - aller[0])))
    sur_place_soir = 0 if dernier_sur_place else max(0, int(soir))
    fin = retour + sur_place_soir
    rentree_min = int(round(rentree[0] * majoration(fin)))
    return {"depart_domicile": arrivee - aller_min, "arrivee_magasin": arrivee, "depart_magasin": depart, "sur_place_matin": sur_place_matin,
            "aller_min": aller_min, "aller_km": round(aller[1], 1),
            "retour_magasin": retour, "fin_magasin": fin, "sur_place_soir": sur_place_soir,
            "rentree_min": rentree_min, "rentree_km": round(rentree[1], 1), "retour_domicile": fin + rentree_min}


def _google(origine, destination, minute: int, jour):
    """(minutes, km) d'un trajet mesuré par Google à cette heure, ou None s'il ne peut pas l'être (pas de clé, essai sans
    réseau, plafond du jour, erreur, heure déjà passée — Google n'estime que l'avenir)."""
    from customization_app import tournee_google as G

    cle_api = G.cle()
    quand = get_datetime("%s %s:00" % (getdate(jour), T._hm(max(0, min(int(minute), 24 * 60 - 1)))))
    if not cle_api or frappe.flags.get("tournee_sans_reseau") or quand <= frappe.utils.now_datetime():
        return None
    try:
        g = G.mesurer(tuple(origine), tuple(destination), G.heure_utc(jour, minute - minute % G.PAS, frappe.utils.get_system_timezone()), cle_api)
    except Exception:
        return None
    return (int(g["minutes"]), float(g["km"]))


def journee(jour, employes: list[str] | None = None) -> dict:
    """L'itinéraire planifié de chaque employé (tous ceux qui ont des tâches ce jour-là, ou ceux demandés)."""
    jour = getdate(jour)
    cfg = T.config()
    filtres = {"starts_on": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]], "status": ["!=", "Cancelled"],
               "custom_choix_du_staff": ["in", employes] if employes else ["is", "set"]}
    taches = frappe.get_all(T.TACHE, filters=filtres, order_by="starts_on asc", limit_page_length=0,
                            fields=["name", "custom_choix_du_staff", "custom_type_dintervention", "starts_on", "ends_on", "temps",
                                    "status", "custom_client", "nom_client", "select_address", "google_map", "secteur", "details_adresse",
                                    "commande_client", "dans_local", "titre"])
    commandes = {t.commande_client for t in taches if t.commande_client and t.custom_type_dintervention == "Livraison"}
    aramex = set(frappe.get_all("Sales Order", filters={"name": ["in", list(commandes)], "payment_terms_template": PAYMENT_TERMS_ARAMEX},
                                pluck="name")) if commandes else set()
    centres = T._centres_secteurs()
    par_employe, hors = {}, {}
    # Ceux qui gardent la voiture chez eux mais passent par le Magasin matin et soir : leur tournée part du Magasin.
    via_magasin = cfg.get("via_magasin") or set()
    controle_position = T.controle_localisation_actif()
    positions_a_verifier = []
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
        if controle_position and T.intervention_terrain(t, cfg) and not pos:
            positions_a_verifier.append({"tache": t.name, "client": nom_client, "adresse": t.select_address,
                                        "motif": T.motif_position_a_verifier(t, pos)})
            hors.setdefault(t.custom_choix_du_staff, []).append({"tache": t.name, "client": nom_client,
                                                                "motif": _("position à vérifier sur la fiche Adresse")})
            continue
        if not pos:
            if not (cfg["debut"] - 60 <= debut <= cfg["fin"] + 60):
                continue                     # une tâche sans lieu hors journée (« Autre » à minuit) : pas une étape
            pos = (cfg["depot"][0], cfg["depot"][1], "dépôt")
        depart = cfg["depot"] if t.custom_choix_du_staff in via_magasin else cfg["departs"].get(t.custom_choix_du_staff, cfg["depot"])
        loin = T.haversine_km(depart, pos[:2])
        if loin > LOIN_KM:
            hors.setdefault(t.custom_choix_du_staff, []).append({"tache": t.name, "client": nom_client,
                                                                 "motif": _("à {0} km du départ").format(int(loin))})
            continue
        par_employe.setdefault(t.custom_choix_du_staff, []).append({
            "tache": t.name, "client": nom_client, "libelle": "" if nom_client else libelle_sans_client(t.titre),
            "type": t.custom_type_dintervention or "", "statut": t.status, "debut_min": debut, "original_min": debut, "service": service, "mobile": False, "fenetre": None,
            "debut": T._hm(debut), "fin": T._hm(debut + service), "lat": pos[0], "lng": pos[1], "position": pos[2]})
    if positions_a_verifier:
        return {"date": str(jour), "employes": [], "depot": cfg["depot"], "hors_itineraire": hors,
                "positions_a_verifier": positions_a_verifier,
                "message": _("Confirmez les positions indiquées avant de calculer les trajets.")}
    if not par_employe:
        return {"date": str(jour), "employes": [], "depot": cfg["depot"], "hors_itineraire": hors}
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", filters={"name": ["in", list(par_employe)]},
                                                            fields=["name", "employee_name"])}
    # Les nœuds de la matrice : le Magasin, les domiciles réglés, puis toutes les étapes.
    points, depot_de, domicile_de = [cfg["depot"]], {}, {}
    for e in par_employe:
        if e in via_magasin:
            depot_de[e] = 0                       # tournée Magasin → clients → Magasin ; le domicile s'ajoute autour
            domicile_de[e] = len(points)
            points.append(cfg["departs"][e])
        elif e in cfg["departs"]:
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
    recalage.pop("mesures", None)                 # clés tuples : pas pour le JSON
    for e in out:
        n, h = domicile_de.get(e["employe"]), e.get("horaires") or {}
        if n is None or not h.get("depart") or not h.get("retour"):
            continue
        arrets = e["apres"]["arrets"]
        bornes = (_minutes(h["depart"]), _minutes(h["retour"]), arrets[0]["position"] == "dépôt", arrets[-1]["position"] == "dépôt")
        temps = (cfg.get("magasin_matin", 15), cfg.get("magasin_soir", 10))
        p = passage_magasin(*bornes, (mn[n][0], km[n][0]), (mn[0][n], km[0][n]), *temps,
                            lambda minute: T.facteur_pointe(minute, cfg["pointes"]))
        source = "osrm"
        # Les deux trajets du domicile mesurés par Google à leur heure (trafic compris), comme le reste de la tournée ;
        # sans clé, sans réseau, plafond atteint ou jour passé : l'estimation OSRM corrigée ci-dessus reste.
        g_aller = _google(points[n], points[0], p["depart_domicile"], jour)
        g_rentree = _google(points[0], points[n], p["fin_magasin"], jour) if g_aller else None
        if g_aller and g_rentree:
            p, source = passage_magasin(*bornes, g_aller, g_rentree, *temps), "google"
        # Le résumé parle de la journée ENTIÈRE (départ et retour au domicile, route et km compris) ; `magasin` détaille
        # les deux passages. Le dépassement de fin de journée reste jugé sur le retour au Magasin, comme pour les autres.
        h["magasin"] = {"arrivee": T._hm(p["arrivee_magasin"]), "depart": h["depart"], "sur_place_matin": p["sur_place_matin"],
                        "aller_min": p["aller_min"], "aller_km": p["aller_km"], "source": source,
                        "retour": h["retour"], "fin": T._hm(p["fin_magasin"]), "sur_place_soir": p["sur_place_soir"],
                        "retour_trajet": h.get("retour_trajet"), "retour_source": h.get("retour_source"),
                        "rentree_min": p["rentree_min"], "rentree_km": p["rentree_km"],
                        # la tournée seule, de Magasin à Magasin (« et la distance magasin to magasin ? »)
                        "km": h.get("km") or 0, "trajets": h.get("trajets") or 0}
        h.update({"depart": T._hm(p["depart_domicile"]), "retour": T._hm(p["retour_domicile"]),
                  "trajets": (h.get("trajets") or 0) + p["aller_min"] + p["rentree_min"],
                  "km": round((h.get("km") or 0) + p["aller_km"] + p["rentree_km"], 1),
                  "retour_trajet": p["rentree_min"], "retour_source": source})
        e["depart"], e["domicile_point"] = "domicile", points[n]
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
