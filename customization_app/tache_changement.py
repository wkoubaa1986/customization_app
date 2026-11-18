"""Ce qui a changé dans la journée d'un technicien APRÈS son départ du matin (demande du 18/11/2026).

« S'il y a une tâche dans la journée créée après 8h30 de la journée en question, ou dont l'heure ou le type a été
modifié, je veux qu'elle soit en rouge clair avec une pastille "nouvelle tâche à faire", ou en orange clair avec une
pastille qui dit la modification. »

  - NOUVELLE (rouge clair) : la tâche a été créée le jour même à partir de 8h30 — ou confiée à cet employé le jour
    même à partir de 8h30 (un transfert : pour lui, c'est une tâche de plus) ;
  - MODIFIÉE (orange clair) : son heure ou son type a changé le jour même à partir de 8h30 ; la pastille dit quoi.

Frappe ne garde pas l'historique des tâches (le suivi des modifications est désactivé sur ce type, et ses valeurs
seraient mises en forme, donc illisibles par un programme). À l'enregistrement, `noter` écrit donc sur la tâche,
dans un champ caché (`changement_du_jour`), ce qui a changé ce jour-là : la valeur d'AVANT le premier changement et
la valeur d'après le dernier — une heure changée puis remise ne laisse rien.
Seules les tâches encore à faire sont signalées ; une tâche terminée n'appelle plus l'attention.
"""
from __future__ import annotations

import json

import frappe

CHAMP = "changement_du_jour"
HEURE_DEBUT = "08:30:00"
#: ce que l'on suit sur la tâche -> la clé dans la marque
SUIVIS = {"starts_on": "heure", "custom_type_dintervention": "type", "custom_choix_du_staff": "employe"}


def seuil(jour) -> str:
    """« 2026-11-19 08:30:00 » : le moment à partir duquel un changement compte pour cette journée."""
    return "%s %s" % (str(jour)[:10], HEURE_DEBUT)


def lire(marque) -> dict:
    if isinstance(marque, dict):
        return marque
    try:
        valeur = json.loads(marque) if marque else {}
        return valeur if isinstance(valeur, dict) else {}
    except Exception:
        return {}


def marque_apres(marque, avant: dict, apres: dict, maintenant, par: str = ""):
    """La marque à garder après un enregistrement. PURE.

    `avant` / `apres` : {starts_on, custom_type_dintervention, custom_choix_du_staff} avant et après ;
    `maintenant` : « AAAA-MM-JJ HH:MM:SS ». -> la nouvelle marque (dict, vide pour l'effacer), ou None s'il n'y a
    rien à changer. Un changement ne compte que s'il est fait le JOUR de la tâche, à partir de 8h30."""
    ancienne = lire(marque)
    marque = dict(ancienne)
    jour = str(apres.get("starts_on") or "")[:10]
    maintenant = str(maintenant)[:19]
    if marque and marque.get("jour") != jour:
        marque = {}                                   # la tâche a changé de jour : ce qui valait pour l'autre jour tombe
    changes = {cle: (str(avant.get(champ) or ""), str(apres.get(champ) or "")) for champ, cle in SUIVIS.items()
               if str(avant.get(champ) or "") != str(apres.get(champ) or "")}
    # Un changement fait la veille, ou avant le départ du matin, n'est pas une surprise : rien à signaler.
    if changes and jour and maintenant[:10] == jour and maintenant >= seuil(jour):
        for cle, (ancien, nouveau) in changes.items():
            origine = (marque.get(cle) or {}).get("avant", ancien)
            if origine == nouveau:
                marque.pop(cle, None)                 # remis comme c'était : plus rien à dire
            else:
                marque[cle] = {"avant": origine, "apres": nouveau}
        if any(cle in marque for cle in SUIVIS.values()):
            marque.update({"jour": jour, "quand": maintenant, "par": par or ""})
        else:
            marque = {}
    return None if marque == ancienne else marque


def _hm(instant) -> str:
    return str(instant or "")[11:16]


def _quand(instant, jour) -> str:
    """« 14:00 » le jour même, « 20/11 14:00 » un autre jour."""
    t = str(instant or "")
    if not t:
        return "—"
    return _hm(t) if t[:10] == str(jour)[:10] else "%s/%s %s" % (t[8:10], t[5:7], _hm(t))


def alerte(jour, creation, marque, employe: str = "", noms: dict | None = None):
    """Ce que « Ma journée » montre sur la carte : None, ou {genre, texte, detail}. PURE.

    genre « nouvelle » (rouge clair) ou « modifiee » (orange clair)."""
    jour = str(jour)[:10]
    creation = str(creation or "")[:19]
    if creation[:10] == jour and creation >= seuil(jour):
        return {"genre": "nouvelle", "texte": "Nouvelle tâche à faire", "detail": "créée à %s" % _hm(creation)}
    marque = lire(marque)
    if not marque or marque.get("jour") != jour:
        return None
    a = marque.get("employe") or {}
    if employe and a.get("apres") == employe and a.get("avant") != employe:
        de = (noms or {}).get(a.get("avant")) or ""
        return {"genre": "nouvelle", "texte": "Nouvelle tâche à faire",
                "detail": "confiée à vous à %s%s" % (_hm(marque.get("quand")), (" (avant : %s)" % de) if de else "")}
    morceaux = []
    h = marque.get("heure") or {}
    if h and h.get("avant") != h.get("apres"):
        morceaux.append("Heure modifiée : %s → %s" % (_quand(h.get("avant"), jour), _quand(h.get("apres"), jour)))
    t = marque.get("type") or {}
    if t and t.get("avant") != t.get("apres"):
        morceaux.append("Type modifié : %s → %s" % (t.get("avant") or "—", t.get("apres") or "—"))
    if not morceaux:
        return None
    return {"genre": "modifiee", "texte": " · ".join(morceaux), "detail": "à %s" % _hm(marque.get("quand"))}


def noter(doc, method=None):
    """Hook before_save de la tâche : tient la marque à jour. Ne bloque jamais un enregistrement."""
    try:
        if doc.is_new() or not doc.meta.has_field(CHAMP):
            return
        avant = doc.get_doc_before_save()
        if not avant:
            return
        from frappe.utils import now_datetime

        nouvelle = marque_apres(doc.get(CHAMP), {c: avant.get(c) for c in SUIVIS}, {c: doc.get(c) for c in SUIVIS},
                                now_datetime().strftime("%Y-%m-%d %H:%M:%S"), frappe.session.user)
        if nouvelle is not None:
            doc.set(CHAMP, json.dumps(nouvelle, ensure_ascii=False) if nouvelle else None)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "tache_changement.noter")
