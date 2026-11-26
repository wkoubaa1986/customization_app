"""Les trois Server Scripts des listes d'appels sont repris dans l'app le 25/11/2026 (customization_app.liste_validation) :

- « Valider Liste appelle » (Liste Appelle Entretien, Before Save) → liste_validation.controle_saisie (hook validate) ;
- « validation finale liste appelle » (Before Submit) → liste_validation.validation_finale (hook before_submit), avec
  les règles du 25/11/2026 : les lignes non appelées ne bloquent plus, « pas de système » ANNULE l'échéancier au lieu
  de le supprimer, Non / Non le garde ;
- « ajuster rendez vous pris par partenaire » (planifié « 0 8,19 * * * ») → liste_validation.passage_8h_19h.

Ils sont éteints SEULEMENT si leur code en base est encore celui qui a été porté (empreinte du code, espaces et fins de
ligne ignorés, relevée sur la copie de prod du 25/11/2026 12:26). Un script modifié depuis contient une règle que l'app
ne connaît pas : il reste actif, et le patch le dit (Error Log « Patch listes d'appels » + sortie du migrate).
Désactivés, jamais supprimés : réversible d'une case à cocher. Rejouable : un script déjà éteint ne bouge pas.

⚠️ Un Server Script planifié porte son propre Scheduled Job Type, que Frappe lance même script désactivé
(ScheduledJobType.execute → execute_scheduled_method ne regarde pas `disabled`) : on le SUPPRIME (26/11/2026), on ne
l'arrête plus comme pour « Rappelle Rendez vous ». « ajuster rendez vous pris par partenaire » est exporté dans
fixtures/server_script.json (disabled = 0), que chaque migrate réimporte de force APRÈS les patches : suppression
« pour rechargement » du script puis insertion, dont le validate (ServerScript.sync_scheduled_jobs) réenregistre une
tâche arrêtée (stopped 1 → 0) qui pointe vers un script momentanément absent → LinkValidationError, que sync_fixtures
ne rattrape pas : le migrate s'arrête sur server_script.json (essai du 26/11/2026 sur la copie du 25/11). Sans tâche, rien
à réenregistrer. Relecture finale du 26/11/2026 : le script n'est PLUS une fixture (retiré de hooks.py et de
server_script.json, comme « cancel sales order payment ») — la réimportation forcée écrasait une retouche faite en prod
et refaisait échouer le migrate dès qu'une tâche liée existait (script retouché laissé actif, ou recoché à la main).
Réversible : recocher le script recrée sa tâche (ServerScript.on_update → sync_scheduler_events), sans risque désormais.

Valeurs initiales des trois réglages du lot (relecture du 26/11/2026) : « Config Relances » est un Single déjà enregistré
(26 lignes dans tabSingles sur la copie du 25/11/2026). Frappe n'applique le défaut d'un champ qu'à un Single NEUF ; un
champ ajouté ensuite est vide, et le premier enregistrement du réglage y écrit 0 (get_valid_dict : Check et Int vides
→ 0). Ce jour-là, sans un mot, la validation refuserait de nouveau toute ligne non appelée et l'alerte du brouillon
s'éteindrait. Le patch écrit donc 1 / 30 / 3 dans tabSingles, SEULEMENT pour une clé absente ou vide, et seulement si
le réglage a déjà été enregistré (sinon ses défauts s'appliqueront à sa création, et relances_config.config() lit
DEFAUTS). Une valeur déjà posée, même 0, n'est jamais touchée. Les champs doivent être dans le DocType au moment du
patch (même déploiement) : un enregistrement du réglage AVANT leur ajout effacerait ces lignes (update_single
réécrit le Single entier).
"""
import hashlib

import frappe

SCRIPTS = {
    # nom : (empreinte du code porté, morceau caractéristique pour le message)
    "Valider Liste appelle": ("a02532dadbdaf530a5d2bdc201bb8220c17e438f",
                              "Merci d'indiquer le Résumé d'appel pour le client"),
    "validation finale liste appelle": ("f2f81b97de03c96522eadd8f4baa2da1e8015f98",
                                        'frappe.delete_doc("Maintenance Schedule", maintenance.name'),
    "ajuster rendez vous pris par partenaire": ("be4241e617417872f08ae1ca69a7955d0e69e530",
                                                'frappe.call("customization_app.api.recolorer_taches_partenaire")'),
}


CONFIG = "Config Relances"
# Les réglages du lot « listes » (25/11/2026), lus par RC.config().get(clé, même défaut) dans le code.
VALEURS_INITIALES = {"valider_avec_lignes_non_appelees": 1, "plafond_hors_secteur": 30, "jours_brouillon_alerte": 3}


def valeurs_a_ecrire(singles: dict) -> dict:
    """Les valeurs initiales à écrire : rien si le réglage n'a jamais été enregistré (`singles` vide), sinon les clés
    absentes ou vides. Une valeur posée (même « 0 », choix possible du propriétaire) reste. PURE."""
    if not singles:
        return {}
    return {k: v for k, v in VALEURS_INITIALES.items() if singles.get(k) in (None, "")}


def _ecrire_valeurs_initiales() -> dict:
    singles = dict(frappe.db.sql("select field, value from tabSingles where doctype = %s", (CONFIG,)))
    a_ecrire = valeurs_a_ecrire(singles)
    if a_ecrire:
        frappe.db.set_single_value(CONFIG, a_ecrire, update_modified=False)
        try:
            meta = frappe.get_meta(CONFIG)
            absents = [k for k in a_ecrire if not meta.has_field(k)]
        except Exception:
            absents = []
        if absents:
            print("listes_appels_dans_l_app : champ(s) %s absent(s) du DocType %s : valeurs écrites, mais un "
                  "enregistrement du réglage avant leur ajout les effacerait" % (", ".join(absents), CONFIG))
    return a_ecrire


def normaliser(code) -> str:
    """Le code sans différence d'espaces ni de fins de ligne (CRLF en base pour deux des trois). PURE."""
    return " ".join((code or "").split())


def empreinte(code) -> str:
    return hashlib.sha1(normaliser(code).encode("utf-8")).hexdigest()


def etat_script(code, disabled, attendu) -> str:
    """« deja_eteint » | « a_eteindre » (code identique au code porté) | « modifie » (on n'y touche pas). PURE."""
    if disabled:
        return "deja_eteint"
    return "a_eteindre" if empreinte(code) == attendu else "modifie"


def _supprimer_jobs(nom) -> list:
    """Les tâches planifiées du script, supprimées (voir l'en-tête : une tâche arrêtée fait tomber le migrate)."""
    supprimes = []
    for job in frappe.get_all("Scheduled Job Type", filters={"server_script": nom}, pluck="name"):
        frappe.delete_doc("Scheduled Job Type", job, force=True, ignore_permissions=True, delete_permanently=True)
        supprimes.append(job)
    return supprimes


def execute():
    bilan = {"eteints": [], "deja_eteints": [], "laisses_actifs": [], "absents": [], "jobs_supprimes": []}
    bilan["reglages_initialises"] = _ecrire_valeurs_initiales()
    for nom, (attendu, morceau) in SCRIPTS.items():
        if not frappe.db.exists("Server Script", nom):
            bilan["absents"].append(nom)
            continue
        s = frappe.db.get_value("Server Script", nom, ["script", "disabled"], as_dict=True)
        etat = etat_script(s.script, s.disabled, attendu)
        if etat == "modifie":
            present = normaliser(morceau) in normaliser(s.script)
            msg = ("Server Script « %s » laissé ACTIF : son code a changé depuis le portage du 25/11/2026 dans "
                   "customization_app.liste_validation (empreinte %s, attendue %s ; morceau caractéristique %s). "
                   "Comparer, porter la différence, puis le désactiver à la main."
                   % (nom, empreinte(s.script), attendu, "présent" if present else "ABSENT"))
            # Arguments nommés : un message d'une ligne passé en premier serait pris pour le titre (140 caractères au plus).
            frappe.log_error(title="Patch listes d'appels : Server Script laissé actif", message=msg)
            print(msg)
            bilan["laisses_actifs"].append(nom)
            continue
        if etat == "a_eteindre":
            frappe.db.set_value("Server Script", nom, "disabled", 1)
            bilan["eteints"].append(nom)
        else:
            bilan["deja_eteints"].append(nom)
        # Script éteint (par ce patch ou avant) : sa tâche planifiée ne doit plus le lancer.
        bilan["jobs_supprimes"] += _supprimer_jobs(nom)
    if bilan["eteints"] or bilan["jobs_supprimes"] or bilan["reglages_initialises"]:
        frappe.clear_cache()   # la carte des Server Scripts est en cache
        frappe.db.commit()
    print("listes_appels_dans_l_app :", bilan)
    return bilan
