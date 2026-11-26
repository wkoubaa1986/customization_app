"""Réglages ENTIERS de « Config Relances » ajoutés après son premier enregistrement : leur défaut est écrit en base
(relecture du 26/11/2026, groupe « réponses » des lots d'entretien).

LE CAS MESURÉ (copie de prod du 25/11/2026) : « Config Relances » est un Single déjà enregistré (26 valeurs dans
tabSingles, modifié le 03/10/2026). Un champ ajouté depuis (fait_lui_meme_solde, jours_rappel_magasin,
jours_reveil_requalifies, et aussi plafond_hors_secteur, jours_brouillon_alerte des autres lots) n'y a pas de ligne :
relances_config.config() rend alors la valeur de DEFAUTS et tout marche… jusqu'au premier enregistrement du formulaire,
même pour changer un autre réglage. Frappe n'applique pas le « default » du JSON à un Single existant
(Document.load_from_db) : une case à cocher arrive à 0 (_fix_numeric_types), un entier vide est écrit 0
(BaseDocument.get_valid_dict : cint(None)). Et 0 est justement la valeur « fonction coupée » : « Fait lui-même » ne
solderait plus rien, « magasin » ne reporterait plus, AUCUN réveil ne partirait plus — sans aucun signal.

EFFET : pour chaque réglage entier (DEFAUTS_REPONSES ci-dessous + chaque entier de relances_config.DEFAUTS) qui n'a
PAS de ligne, la valeur que config() rend déjà aujourd'hui est écrite. Rien ne change donc dans le comportement, sauf
qu'il survit à un enregistrement du formulaire. Une valeur déjà en base (même 0, choisi à la main) n'est jamais
touchée. Un Single jamais enregistré n'est pas touché (à sa première ouverture, Frappe applique les défauts du JSON ;
écrire quelques lignes l'en priverait pour les autres champs). Rejouable sans effet.

À inscrire dans patches.txt, section [post_model_sync], DANS LE MÊME DÉPLOIEMENT que l'ajout des champs au JSON (le patch
doit passer avant tout enregistrement du formulaire)."""
import frappe

DOCTYPE = "Config Relances"

# Les réglages du groupe « réponses » (liste_appels.reglages, sms_reveil.jours_reveil) : écrits même si relances_config
# n'a pas encore ces clés dans DEFAUTS (sinon config() ne les lirait de toute façon pas).
DEFAUTS_REPONSES = {"fait_lui_meme_solde": 1, "jours_rappel_magasin": 15, "jours_reveil_requalifies": 180}


def defauts_entiers(defauts_config=None) -> dict:
    """{champ: défaut entier} : ceux du groupe « réponses », puis ceux de relances_config.DEFAUTS (qui font foi en cas de
    doublon : c'est la valeur que config() rend). Seuls les entiers : un texte vide est déjà relu comme son défaut par
    config() (`(v or "").strip()`), il ne risque rien. PURE avec `defauts_config`."""
    if defauts_config is None:
        from customization_app import relances_config as RC
        defauts_config = RC.DEFAUTS
    out = dict(DEFAUTS_REPONSES)
    out.update({k: int(v) for k, v in (defauts_config or {}).items() if isinstance(v, int)})
    return out


def a_poser(presents, defauts) -> dict:
    """Les valeurs à écrire : les champs SANS ligne en base ; rien si le réglage n'a jamais été enregistré. PURE."""
    presents = set(presents or ())
    if not presents:
        return {}
    return {k: v for k, v in (defauts or {}).items() if k not in presents}


def execute():
    presents = frappe.db.sql_list("select field from `tabSingles` where doctype = %s", (DOCTYPE,))
    poses = a_poser(presents, defauts_entiers())
    for champ, valeur in poses.items():
        frappe.db.set_single_value(DOCTYPE, champ, valeur, update_modified=False)
    if presents:
        # Relecture finale du 26/11/2026 : un formulaire « Config Relances » ouvert AVANT le déploiement (sans les champs
        # neufs) puis enregistré après passait le contrôle « modifié depuis » — et écrivait 0 dans tous les réglages neufs
        # (essai : 12 réglages à 0, « Fait lui-même », rappel magasin, réveil, SMS unique… coupés sans bruit). La date de
        # modification avance une fois : ce formulaire est refusé et doit être rechargé (il lit alors les valeurs posées).
        frappe.db.set_single_value(DOCTYPE, "modified", frappe.utils.now(), update_modified=False)
    frappe.local._config_relances = None
    print("Config Relances : %d réglage(s) entier(s) sans valeur écrit(s) à leur défaut : %s"
          % (len(poses), ", ".join("%s=%s" % kv for kv in sorted(poses.items())) or "aucun"))
    return poses
