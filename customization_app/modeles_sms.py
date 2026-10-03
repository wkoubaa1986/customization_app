"""Modèles des SMS automatiques — rappel de la veille (20:00) et relance entretien (10:00).

Jusqu'au 03/10/2026, ces textes étaient figés dans le code (rappel_rdv.py, relance_maintenance_sms.py) : seuls
les numéros et le lien de la boutique se réglaient (Responsable Relance). Ils vivent désormais dans le réglage
« Config Modeles SMS », avec les textes historiques comme valeurs par défaut : un champ vide = le texte d'origine,
et un site qui n'a jamais enregistré le réglage envoie exactement ce qu'il envoyait avant.

RÈGLE DE RENDU : une balise inconnue reste telle quelle ; une LIGNE dont toutes les balises sont vides disparaît
(pas de « Technicien : . » quand la tâche n'a pas d'employé, pas de ligne de coût quand le tarif est inconnu).

GSM-7 : un seul caractère hors alphabet GSM 03.38 bascule tout le SMS en unicode (67 caractères par segment au
lieu de 153). `analyser` dit ce que coûte un texte avant de l'enregistrer ; les textes par défaut sont sans accent
sauf é è à ù, qui font partie de l'alphabet GSM.
"""
from __future__ import annotations

import math
import re

import frappe
from frappe import _

DOCTYPE = "Config Modeles SMS"

DEFAUTS = {
    "signature": "Aqua World - 98511119",
    "avertissement_horaire": "Horaire indicatif, il peut varier dans la journee.",
    "rappel_rdv": "Bonsoir {nom_client},\n"
                  "Rappel : {type} demain {date} vers {heure}. {avertissement}\n"
                  "Technicien : {technicien}.\n"
                  "{signature}",
    "rappel_livraison": "Bonsoir {nom_client},\n"
                        "Votre commande sera livree demain {date} vers {heure}. {avertissement}\n"
                        "Livreur : {technicien}.\n"
                        "{signature}",
    "avis_aramex": "Bonsoir {nom_client},\n"
                   "Votre commande a ete remise aujourd'hui a ARAMEX pour livraison.\n"
                   "{ligne_suivi}\n"
                   "{signature}",
    "aramex_ligne_suivi": "N de suivi : {bordereau}.",
    "aramex_sans_suivi": "Le numero de suivi vous sera communique.",
    "relance_entretien": "Bonjour {nom_client},{voeux}\n"
                         "Rappel: La maintenance de {appareil} est arrivée à échéance.{promo}\n"
                         "Cout main-d'oeuvre: {cout} DT. Ce tarif exclut les filtres de remplacement, "
                         "facturés séparément selon entretien.\n"
                         "{suite}",
    "relance_rdv_en_ligne": "Prenez RDV en ligne: {lien_rdv}\nOu appelez le {telephones}.",
    "relance_sans_lien": "Pour planifier votre entretien, contactez-nous au {telephones}.",
    "relance_hors_secteur": "Commandez vos filtres directement sur notre site :\n{lien_boutique}\n"
                            "Ou contactez-nous au {telephones} pour passer votre commande",
    # Nos clients de Sousse / Monastir / Mahdia : c'est NOTRE équipe partenaire qui passe, mais nous qui gérons.
    "relance_zone_partenaire": "Dans votre region, l'entretien est assure par notre equipe partenaire {partenaire}.\n"
                               "Prenez RDV en ligne: {lien_rdv}\nOu appelez le {telephones}.",
}

BALISES = {
    "rappel_rdv": "{nom_client} {type} {date} {heure} {avertissement} {technicien} {signature}",
    "rappel_livraison": "{nom_client} {date} {heure} {avertissement} {technicien} {signature}",
    "avis_aramex": "{nom_client} {ligne_suivi} {signature}",
    "aramex_ligne_suivi": "{bordereau}",
    "relance_entretien": "{nom_client} {voeux} {appareil} {promo} {cout} {suite}",
    "relance_rdv_en_ligne": "{lien_rdv} {telephones}",
    "relance_sans_lien": "{telephones}",
    "relance_hors_secteur": "{lien_boutique} {telephones}",
    "relance_zone_partenaire": "{partenaire} {lien_rdv} {telephones}",
}

_BALISE = re.compile(r"\{(\w+)\}")


def rendre(modele: str, valeurs: dict) -> str:
    """Remplace les balises ligne par ligne. PURE. Une balise inconnue reste écrite ; une ligne dont toutes les
    balises sont vides (None, "") est retirée ; une balise multi-lignes (ex. {suite}) s'étale normalement."""
    def vide(v):
        return v is None or (isinstance(v, str) and not v.strip())
    lignes = []
    for ligne in (modele or "").split("\n"):
        noms = [n for n in _BALISE.findall(ligne) if n in valeurs]
        if noms and all(vide(valeurs[n]) for n in noms):
            continue
        lignes.append(_BALISE.sub(lambda m: "" if m.group(1) in valeurs and vide(valeurs[m.group(1)])
                                  else (str(valeurs[m.group(1)]) if m.group(1) in valeurs else m.group(0)), ligne))
    return "\n".join(lignes)


def textes() -> dict:
    """Les modèles en vigueur : réglage s'il est rempli, texte d'origine sinon. Lit tabSingles directement
    (un Single jamais enregistré ne porte aucune valeur) et ne lève jamais : sans base (tests purs), les défauts."""
    out = dict(DEFAUTS)
    try:
        if getattr(frappe.local, "db", None):
            for champ, valeur in frappe.db.sql("select field, value from tabSingles where doctype = %s", (DOCTYPE,)):
                if champ in DEFAUTS and (valeur or "").strip():
                    out[champ] = valeur
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ GSM-7

GSM_BASE = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXTENSION = "^{}\\[~]|€"


def analyser(texte: str) -> dict:
    """Ce que coûte un SMS : après translittération (compagne_sms), caractères hors GSM restants, longueur,
    nombre de segments, et si le message part en unicode. PURE (hors import)."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import normaliser_sms
    t = normaliser_sms(texte or "")
    hors = sorted({c for c in t if c not in GSM_BASE and c not in GSM_EXTENSION})
    unicode_ = bool(hors)
    longueur = len(t) if unicode_ else sum(2 if c in GSM_EXTENSION else 1 for c in t)
    simple, multi = (70, 67) if unicode_ else (160, 153)
    segments = 0 if not t else (1 if longueur <= simple else math.ceil(longueur / multi))
    return {"longueur": longueur, "segments": segments, "unicode": unicode_, "hors_gsm": hors, "texte": t}


# ------------------------------------------------------------------ API du réglage

def _modeles_demandes(modeles) -> dict:
    """Les textes du formulaire (non enregistrés) par-dessus ceux en vigueur ; vide = défaut."""
    base = textes()
    brut = frappe.parse_json(modeles) if isinstance(modeles, str) else (modeles or {})
    for champ in DEFAUTS:
        if champ in brut:
            base[champ] = brut[champ] if (brut.get(champ) or "").strip() else DEFAUTS[champ]
    return base


@frappe.whitelist()
def defauts():
    frappe.only_for(("System Manager", "Maintenance Manager", "Sales Manager"))
    return DEFAUTS


@frappe.whitelist()
def apercu(modeles=None):
    """Chaque message rendu sur des données réelles quand il y en a (prochaine tâche de chaque type), sinon sur
    un exemple, avec son coût en segments. Rien n'est envoyé ni enregistré."""
    frappe.only_for(("System Manager", "Maintenance Manager", "Sales Manager"))
    from customization_app import rappel_rdv as R
    T = _modeles_demandes(modeles)
    out = []

    def tache_exemple(filtres, libelle):
        t = (frappe.get_all(R.DOCTYPE_TACHE, filters=dict(filtres, starts_on=[">=", frappe.utils.nowdate()]),
                            fields=["*"], order_by="starts_on asc", limit=1)
             or frappe.get_all(R.DOCTYPE_TACHE, filters=filtres, fields=["*"], order_by="starts_on desc", limit=1))
        return (t[0], "%s — %s (%s)" % (libelle, t[0].get("nom_client") or t[0].name, t[0].name)) if t else (None, libelle + " — exemple")

    t, titre = tache_exemple({"status": "Open", "custom_type_dintervention": ["in", list(R.TYPES_RAPPELES)]}, _("Rappel de rendez-vous"))
    out.append({"champ": "rappel_rdv", "titre": titre, "texte": R.message_rendez_vous(t, T) if t else rendre(T["rappel_rdv"], {
        "nom_client": "Ahmed Farhat", "type": "Entretien", "date": "04/10", "heure": "09:30", "avertissement": T["avertissement_horaire"],
        "technicien": "Jamel Aloui - 51511918", "signature": T["signature"]})})
    t, titre = tache_exemple({"custom_type_dintervention": R.TYPE_LIVRAISON}, _("Rappel de livraison"))
    out.append({"champ": "rappel_livraison", "titre": titre, "texte": R.message_livraison(t, T) if t else rendre(T["rappel_livraison"], {
        "nom_client": "Ahmed Farhat", "date": "04/10", "heure": "08:30", "avertissement": T["avertissement_horaire"],
        "technicien": "Hedi ibidhii - 98511119", "signature": T["signature"]})})
    t, titre = tache_exemple({"custom_type_dintervention": R.TYPE_LIVRAISON, "status": "Completed", "commande_client": ["is", "set"]},
                             _("Avis de remise Aramex"))
    bordereau = R._bordereau_aramex(t["commande_client"]) if t else "41234567890"
    out.append({"champ": "avis_aramex", "titre": titre, "texte": R.message_aramex(t or {"nom_client": "Ahmed Farhat"}, bordereau, T)})

    from customization_app.Maintenance import relance_maintenance_sms as M
    try:
        lien_boutique, phones = M.get_relance_config()
        telephones = M._format_phones_for_message(phones)
    except Exception:
        lien_boutique, telephones = "https://aquaworld.tn", "98 511 119"
    try:
        cout = M.get_price_for_item("M-E-OD", "Vente standard")
    except Exception:
        cout = None
    lien_rdv = frappe.utils.get_url("/rdv")
    for champ, titre, kw in (("relance_rdv_en_ligne", _("Relance entretien — client de nos secteurs, portail ouvert"), dict(secteur="Secteur 1", lien_rdv=lien_rdv)),
                             ("relance_sans_lien", _("Relance entretien — client de nos secteurs, portail fermé"), dict(secteur="Secteur 1", lien_rdv="")),
                             ("relance_hors_secteur", _("Relance entretien — hors secteur, sans partenaire"), dict(secteur="Hors Secteur", lien_rdv="")),
                             ("relance_zone_partenaire", _("Relance entretien — notre client en zone partenaire (Sousse, Monastir…)"),
                              dict(secteur="Hors Secteur", lien_rdv=lien_rdv, partenaire="Economic Aqua Solution"))):
        out.append({"champ": champ, "titre": titre, "texte": M.message_relance(
            T, nom_client="Ahmed Farhat", appareil="votre osmoseur", cout=cout, telephones=telephones, lien_boutique=lien_boutique, **kw)})
    for m in out:
        m["analyse"] = analyser(m["texte"])
    return out
