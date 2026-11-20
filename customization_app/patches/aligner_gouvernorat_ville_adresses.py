"""Adresses : champs de la fiche (« Gouvernorats Tunisie » / « Villes Tunisie », ou « Hors Tunisie ») remplis depuis
Gouvernorat / Ville, et les deux anciens scripts en base désactivés (20/11/2026).

Pourquoi : la saisie rapide d'un client, la tournée et les imports n'écrivaient que les champs standard `state` /
`city` ; la fiche Adresse, elle, montre les champs maison. 2 216 adresses tunisiennes sur 6 027 s'affichaient donc
sans gouvernorat ni ville, et ne s'enregistraient plus. Le code qui garde désormais les deux alignés est
customization_app/adresse_sectorisation.py ; la fiche est pilotée par public/js/address_sectorisation.js.

Ce que fait ce patch, une fois :
1. désactive le Client Script « Sectorisation Adresse » (remplacé par address_sectorisation.js — les deux ensemble
   se marcheraient dessus : l'ancien force le pays et vide la ville) et le Server Script « sectorisation de
   l'adresse » (remplacé par adresse_sectorisation.valider — l'ancien plante sur un couple inconnu) ;
2. remplit les champs de la fiche VIDES depuis les champs standard, graphie officielle quand elle se reconnaît
   (« ARIANA » -> « Ariana ») — et l'inverse pour les rares adresses où seul le champ de la fiche est rempli ;
3. pose le secteur là où il manque et où la table le connaît (« Hors Secteur » hors Tunisie) ;
4. là où les deux jeux de champs sont remplis et se contredisent (12 adresses sur la copie du 18/11/2026), prend
   les champs standard — ceux que la fiche client affiche (sa décision du 20/11/2026 : « pour ces cas prends les
   valeurs de la fiche client ») ; le secteur suit quand il avait été déduit de l'ancienne valeur de la fiche. Ces
   adresses sont listées, avant / après, dans la sortie de la migration.

Hors ces contradictions rien n'est écrasé : seuls des champs vides sont remplis, et une graphie ramenée à
l'officielle. `modified` n'est pas touché.
"""
import frappe

from customization_app.adresse_sectorisation import HORS_SECTEUR, TUNISIE, gouvernorat_officiel, secteur_attendu, ville_officielle

CLIENT_SCRIPT = "Sectorisation Adresse"
SERVER_SCRIPT = "sectorisation de l'adresse"


def _vide(v):
    return not (v or "").strip()


def _desactiver_scripts():
    faits = []
    noms = set(frappe.get_all("Client Script", filters={"dt": "Address", "enabled": 1, "script": ["like", "%villes_par_gouvernorat%"]}, pluck="name"))
    if frappe.db.exists("Client Script", CLIENT_SCRIPT):
        noms.add(CLIENT_SCRIPT)
    for nom in sorted(noms):
        if frappe.db.get_value("Client Script", nom, "enabled"):
            frappe.db.set_value("Client Script", nom, "enabled", 0, update_modified=False)
            faits.append("Client Script « %s »" % nom)
    noms = set(frappe.get_all("Server Script", filters={"reference_doctype": "Address", "disabled": 0,
                                                        "script": ["like", "%sectorisation[doc.state][doc.city]%"]}, pluck="name"))
    if frappe.db.exists("Server Script", SERVER_SCRIPT):
        noms.add(SERVER_SCRIPT)
    for nom in sorted(noms):
        if not frappe.db.get_value("Server Script", nom, "disabled"):
            frappe.db.set_value("Server Script", nom, "disabled", 1, update_modified=False)
            faits.append("Server Script « %s »" % nom)
    if faits:
        frappe.cache.delete_value("server_script_map")
        frappe.clear_cache(doctype="Address")
    return faits


def corrections(a) -> dict:
    """Ce qu'il faut poser sur UNE adresse (dict des champs). PURE.

    Pour le gouvernorat puis la ville : la valeur connue remplit le champ vide ; si le champ de la fiche et le champ
    standard sont remplis et différents, le STANDARD fait foi (c'est lui que la fiche client affiche). En Tunisie la
    valeur est ramenée à la graphie officielle quand elle la rejoint (« ARIANA », espace insécable) — dans les DEUX
    champs, pour qu'ils restent identiques et que la liste de la fiche retrouve sa valeur.

    Secteur : posé s'il manque ; après une contradiction tranchée, recalculé s'il avait été déduit de l'ancienne
    valeur de la fiche (un secteur choisi à la main, qui ne correspond à aucune des deux, reste)."""
    out = {}
    tunisie = a.get("country") == TUNISIE
    c_gouv, c_ville = ("custom_state_s", "custom_villes_s") if tunisie else ("custom_state_d", "custom_villes_d")
    tranche = []

    def paire(std, fiche, officielle):
        s, f = (a.get(std) or "").strip(), (a.get(fiche) or "").strip()
        if s and f and s != f:
            tranche.append(fiche)
        valeur = s or f
        valeur = (officielle(valeur) or valeur) if valeur and tunisie else valeur
        for champ in (std, fiche):
            if valeur and (a.get(champ) or "") != valeur:
                out[champ] = valeur
        return valeur

    gouv = paire("state", c_gouv, gouvernorat_officiel)
    ville = paire("city", c_ville, lambda v: ville_officielle(gouv, v) if gouv else None)
    actuel = (a.get("custom_secteur") or "").strip()
    attendu = secteur_attendu(gouv, ville) if tunisie else HORS_SECTEUR
    if attendu and attendu != actuel:
        deduit_de_la_fiche = tunisie and tranche and actuel == secteur_attendu(a.get(c_gouv), a.get(c_ville))
        if not actuel or deduit_de_la_fiche:
            out["custom_secteur"] = attendu
    return out


def contradictoires():
    return frappe.db.sql("""select name, state, custom_state_s, city, custom_villes_s, custom_secteur from tabAddress
        where country = %s and ((ifnull(custom_state_s, '') <> '' and ifnull(state, '') <> '' and custom_state_s <> state)
                             or (ifnull(custom_villes_s, '') <> '' and ifnull(city, '') <> '' and custom_villes_s <> city))
        order by name""", TUNISIE, as_dict=True)


def execute():
    if not frappe.get_meta("Address").has_field("custom_state_s"):
        return
    for fait in _desactiver_scripts():
        print("  désactivé : %s" % fait)
    secteurs = set(frappe.get_all("Secteur geographique", pluck="name"))
    champs = ["name", "country", "state", "city", "custom_state_s", "custom_villes_s", "custom_state_d", "custom_villes_d", "custom_secteur"]
    n = 0
    avant = {r.name: r for r in contradictoires()}
    for a in frappe.get_all("Address", fields=champs, limit_page_length=0):
        valeurs = corrections(a)
        if valeurs.get("custom_secteur") and valeurs["custom_secteur"] not in secteurs:
            valeurs.pop("custom_secteur")
        if valeurs:
            frappe.db.set_value("Address", a.name, valeurs, update_modified=False)
            n += 1
    print("  adresses complétées : %d" % n)
    if avant:
        print("  %d adresse(s) où la fiche Adresse contredisait la fiche client — la fiche client fait foi :" % len(avant))
        for nom, r in avant.items():
            v = frappe.db.get_value("Address", nom, ["state", "city", "custom_secteur"], as_dict=True)
            print("    %s : %s / %s (%s) -> %s / %s (%s)" % (nom, r.custom_state_s, r.custom_villes_s, r.custom_secteur or "sans secteur",
                                                           v.state, v.city, v.custom_secteur or "sans secteur"))
