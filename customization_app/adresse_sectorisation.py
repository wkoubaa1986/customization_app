"""Gouvernorat, ville et secteur d'une adresse : UNE information, deux jeux de champs à garder d'accord.

Le constat du 20/11/2026 (« mon paramétrage fait perdre des informations entre Tunisie et les gouvernorats ») :
la fiche Adresse montre des champs maison — « Gouvernorats Tunisie » / « Villes Tunisie » (custom_state_s,
custom_villes_s) ou, hors Tunisie, custom_state_d / custom_villes_d — alors que tout le reste d'ERPNext lit les champs
standard `state` / `city`, cachés ou en lecture seule sur la fiche. Rien ne garantissait que les deux disent la même
chose :

- la saisie rapide d'un client (ERPNext `make_address`), la création depuis la tournée ou WooCommerce n'écrivent
  que les champs standard : 2 216 adresses tunisiennes sur 6 027 avaient les champs de la fiche VIDES ;
- l'ancien Client Script « Sectorisation Adresse » (en base) ne chargeait les listes que lorsque le pays changeait :
  sur une adresse existante, aucune liste ; à l'enregistrement il recopiait le champ visible — vide — dans `city`,
  d'où « City/Town is required » : ces adresses ne s'enregistraient plus ; et il forçait le pays à Tunisia à chaque
  ouverture, ce qui basculait une adresse étrangère ;
- l'ancien Server Script « sectorisation de l'adresse » plantait (KeyError) sur un couple inconnu de la table.

Ici, à chaque enregistrement et QUEL QUE SOIT le chemin (fiche, saisie rapide, portail, tournée, import) :

1. les deux jeux de champs sont alignés (`aligner`) : celui qui vient d'être modifié fait foi ; à la création, le
   champ de la fiche s'il est rempli, sinon le champ standard ; devant deux valeurs contradictoires que personne ne
   vient de toucher, c'est le champ standard — celui qu'affiche la fiche client — qui fait foi (sa décision du
   20/11/2026 : « pour ces cas prends les valeurs de la fiche client ») ;
2. en Tunisie, les noms sont ramenés à leur graphie officielle quand ils la rejoignent (« ARIANA », espace
   insécable…) — sinon la liste de la fiche ne retrouve pas sa valeur ;
3. le secteur est calculé s'il manque, et recalculé quand la ville change sans que le secteur ait été choisi à la
   main dans le même enregistrement ; un couple inconnu laisse le secteur tel quel, sans erreur.

La table gouvernorat → ville → secteur reste celle de `sectorisation.py` ; la fiche la lit par `referentiel()`
(public/js/address_sectorisation.js) au lieu d'en porter une copie de plus.

Les fonctions du haut sont pures (sans site) : tests/test_adresse_sectorisation.py.
"""
from __future__ import annotations

import frappe

from customization_app.sectorisation import VILLES_PAR_GOUVERNORAT, normaliser_nom

TUNISIE = "Tunisia"
HORS_SECTEUR = "Hors Secteur"
# (champ standard, champ de la fiche en Tunisie, champ de la fiche hors Tunisie)
PAIRES = (("state", "custom_state_s", "custom_state_d"), ("city", "custom_villes_s", "custom_villes_d"))

_GOUVERNORATS = {normaliser_nom(g): g for g in VILLES_PAR_GOUVERNORAT}
_VILLES = {g: {normaliser_nom(v): v for v in villes} for g, villes in VILLES_PAR_GOUVERNORAT.items()}


def _t(v) -> str:
    return (v or "").strip() if isinstance(v, str) else (v or "")


def gouvernorat_officiel(nom):
    """« ARIANA », « ariana » -> « Ariana » ; inconnu -> None. PURE. Volontairement strict (pas de ressemblance
    approximative comme sectorisation.gouvernorat_proche) : on réécrit le champ, on ne devine pas."""
    return _GOUVERNORATS.get(normaliser_nom(nom))


def ville_officielle(gouvernorat, nom):
    """La graphie officielle d'une ville DANS son gouvernorat, ou None. PURE."""
    return (_VILLES.get(gouvernorat_officiel(gouvernorat) or "") or {}).get(normaliser_nom(nom))


def secteur_attendu(gouvernorat, ville):
    """Le secteur de la table pour ce couple, graphies approximatives comprises ; None si inconnu. PURE."""
    g = gouvernorat_officiel(gouvernorat)
    v = ville_officielle(g, ville) if g else None
    return VILLES_PAR_GOUVERNORAT[g][v] if g and v else None


def champs_actifs(pays) -> tuple:
    """Les champs que la fiche montre pour ce pays : ((standard, visible, masqué), ...). PURE."""
    return tuple((std, s, d) if pays == TUNISIE else (std, d, s) for std, s, d in PAIRES)


def aligner(valeurs: dict, avant: dict | None = None) -> dict:
    """Ce qu'il faut écrire pour que les champs standard et ceux de la fiche disent la même chose. PURE.

    `valeurs` : l'adresse telle qu'on s'apprête à l'enregistrer ; `avant` : la même en base (None à la création).
    -> {champ: valeur} à poser (seulement ce qui change).

    Qui fait foi, pour chaque paire (standard, visible) :
    - création : le champ visible s'il est rempli, sinon le standard (les programmes n'écrivent que lui) ;
    - le champ visible vient d'être modifié : lui, même vidé (la fiche) ;
    - seul le standard vient d'être modifié : lui (un programme) ;
    - le pays change : les champs visibles du nouveau pays s'ils sont renseignés, sinon on garde le standard ;
    - l'un des deux est vide : l'autre le remplit (adresses créées avant ce module) ;
    - les deux sont remplis, différents, et personne n'y a touché : le standard, celui qu'affiche la fiche client.
    Au changement de pays, les champs de l'ancien pays sont vidés — ils décrivaient un autre endroit."""
    pays = valeurs.get("country")
    paires = champs_actifs(pays)
    pays_change = bool(avant) and avant.get("country") != pays
    visibles_remplis = any(_t(valeurs.get(vis)) for _, vis, _ in paires)
    out = {}
    for std, vis, masque in paires:
        s, v = _t(valeurs.get(std)), _t(valeurs.get(vis))
        if not avant:
            gagnant = v or s
        elif v != _t(avant.get(vis)):
            gagnant = v
        elif s != _t(avant.get(std)):
            gagnant = s
        elif pays_change:
            gagnant = v if visibles_remplis else s
        else:
            gagnant = s or v
        out[std], out[vis] = gagnant, gagnant
        if pays_change and _t(valeurs.get(masque)):
            out[masque] = ""
    if pays == TUNISIE:
        # graphie officielle, pour que la liste de la fiche retrouve sa valeur
        g_std, g_vis = PAIRES[0][0], PAIRES[0][1]
        v_std, v_vis = PAIRES[1][0], PAIRES[1][1]
        g = out.get(g_std, _t(valeurs.get(g_std)))
        g_off = gouvernorat_officiel(g)
        if g_off and _t(out.get(g_vis, valeurs.get(g_vis))) == g and g_off != g:
            out[g_std], out[g_vis] = g_off, g_off
        v = out.get(v_std, _t(valeurs.get(v_std)))
        v_off = ville_officielle(g_off, v) if g_off else None
        if v_off and _t(out.get(v_vis, valeurs.get(v_vis))) == v and v_off != v:
            out[v_std], out[v_vis] = v_off, v_off
    return {k: val for k, val in out.items() if _t(valeurs.get(k)) != val}


def secteur_a_poser(valeurs: dict, avant: dict | None = None):
    """Le secteur à écrire, ou None pour ne pas y toucher. PURE. `valeurs` : l'adresse APRÈS `aligner`.

    - secteur vide : celui de la table (Tunisie) ou « Hors Secteur » (autre pays) ;
    - la ville, le gouvernorat ou le pays viennent de changer et le secteur n'a pas été modifié dans le même
      enregistrement : il est périmé, on le recalcule ;
    - un secteur choisi à la main reste ; un couple inconnu de la table ne change rien."""
    tunisie = valeurs.get("country") == TUNISIE
    attendu = secteur_attendu(valeurs.get("state"), valeurs.get("city")) if tunisie else HORS_SECTEUR
    actuel = _t(valeurs.get("custom_secteur"))
    if not attendu or attendu == actuel:
        return None
    if not actuel:
        return attendu
    if not avant:
        return None
    lieu_change = any(_t(valeurs.get(k)) != _t(avant.get(k)) for k in ("country", "state", "city"))
    return attendu if lieu_change and actuel == _t(avant.get("custom_secteur")) else None


CHAMPS = ("country", "state", "city", "custom_state_s", "custom_villes_s", "custom_state_d", "custom_villes_d", "custom_secteur")


def valider(doc, method=None):
    """doc_events Address.validate — avant geocodage_adresses.adresse_modifiee, qui compare `city` à l'ancienne."""
    if not frappe.get_meta("Address").has_field("custom_state_s"):
        return
    precedent = None if doc.is_new() else doc.get_doc_before_save()
    avant = {k: precedent.get(k) for k in CHAMPS} if precedent else None
    valeurs = {k: doc.get(k) for k in CHAMPS}
    for champ, valeur in aligner(valeurs, avant).items():
        doc.set(champ, valeur)
        valeurs[champ] = valeur
    secteur = secteur_a_poser(valeurs, avant)
    if secteur and frappe.db.exists("Secteur geographique", secteur):
        doc.custom_secteur = secteur


@frappe.whitelist()
def referentiel():
    """La table de la fiche Adresse : {gouvernorat: {ville: secteur}}, dans l'ordre de sectorisation.py."""
    return VILLES_PAR_GOUVERNORAT
