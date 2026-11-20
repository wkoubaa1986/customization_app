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
3. pose le secteur là où il manque et où la table le connaît (« Hors Secteur » hors Tunisie).

Ce qu'il ne fait PAS : trancher les adresses où les deux jeux de champs sont remplis et se contredisent (12 sur la
copie du 18/11/2026) — elles sont listées dans la sortie de la migration, à corriger à la main. Rien n'est écrasé :
seuls des champs vides sont remplis, et une graphie ramenée à l'officielle. `modified` n'est pas touché.
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

    Pour le gouvernorat puis la ville : si le champ de la fiche et le champ standard sont remplis et différents, on
    n'y touche pas. Sinon la valeur connue remplit le champ vide ; en Tunisie elle est ramenée à la graphie
    officielle quand elle la rejoint (« ARIANA », espace insécable) — dans les DEUX champs, pour qu'ils restent
    identiques et que la liste de la fiche retrouve sa valeur."""
    out = {}
    tunisie = a.get("country") == TUNISIE
    c_gouv, c_ville = ("custom_state_s", "custom_villes_s") if tunisie else ("custom_state_d", "custom_villes_d")

    def paire(std, fiche, officielle):
        s, f = (a.get(std) or "").strip(), (a.get(fiche) or "").strip()
        if s and f and s != f:
            return None
        valeur = f or s
        valeur = (officielle(valeur) or valeur) if valeur and tunisie else valeur
        for champ in (std, fiche):
            if valeur and (a.get(champ) or "") != valeur:
                out[champ] = valeur
        return valeur

    gouv = paire("state", c_gouv, gouvernorat_officiel)
    paire("city", c_ville, lambda v: ville_officielle(gouv, v) if gouv else None)
    if _vide(a.get("custom_secteur")):
        secteur = secteur_attendu(out.get("state", a.get("state")), out.get("city", a.get("city"))) if tunisie else HORS_SECTEUR
        if secteur:
            out["custom_secteur"] = secteur
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
    for a in frappe.get_all("Address", fields=champs, limit_page_length=0):
        valeurs = corrections(a)
        if valeurs.get("custom_secteur") and valeurs["custom_secteur"] not in secteurs:
            valeurs.pop("custom_secteur")
        if valeurs:
            frappe.db.set_value("Address", a.name, valeurs, update_modified=False)
            n += 1
    print("  adresses complétées : %d" % n)
    restes = contradictoires()
    if restes:
        print("  %d adresse(s) où la fiche et les champs standard se contredisent — non modifiées, à corriger à la main :" % len(restes))
        for r in restes:
            print("    %s : fiche %s / %s — standard %s / %s — %s" % (r.name, r.custom_state_s, r.custom_villes_s, r.state, r.city, r.custom_secteur))
