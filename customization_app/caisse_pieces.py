"""
Les PIÈCES PAPIER de la caisse — chèque et traite — et ce qu'on leur demande.

Deux règles, valables dans TOUS les dialogues de la caisse journalière (encaissement des dettes,
encaissement Aramex, dépense, règlement d'une dépense à payer, règlement fournisseur) :

  1. LA PHOTO EST OBLIGATOIRE… sauf code de dispense (demande utilisateur 16/09/2026). Le code
     vit dans « Config Cloture Tache » (champ « Code caisse : chèque / traite sans photo »,
     Password, System Manager) ; à défaut, le code superviseur de clôture des tâches sert.
     Chaque usage laisse une trace nominative sur la pièce créée ;

  2. LA PHOTO EST LUE PAR OPENAI et confrontée au saisi (numéro, montant). Ce sont des
     AVERTISSEMENTS, jamais un blocage : l'enregistrement est déjà fait quand ils arrivent,
     l'employé regarde et tranche. La plomberie est celle de l'encaissement des dettes
     (`caisse_encaissement_dettes._verifier_photo`), réutilisée telle quelle.
"""

import hmac
import json
import re

import frappe
from frappe import _

DOCTYPE_CONFIG = "Config Cloture Tache"
CHAMP_CODE_CAISSE = "code_dispense_photo_caisse"
CHAMP_CODE_SUPERVISEUR = "code_deverrouillage"

#: Les modes dont la pièce est un papier : photo exigée, lecture automatique possible.
MODES_PAPIER = ("Chèque", "Traite bancaire")


def _code_attendu():
    from frappe.utils.password import get_decrypted_password

    for champ in (CHAMP_CODE_CAISSE, CHAMP_CODE_SUPERVISEUR):
        try:
            code = get_decrypted_password(DOCTYPE_CONFIG, DOCTYPE_CONFIG, champ,
                                          raise_exception=False)
        except Exception:
            code = None
        if code:
            return str(code)
    return ""


def dispense_photo(code):
    """Le code donné lève-t-il l'obligation de photo ? Vide -> non. Faux -> refus (et trace
    dans le journal d'erreurs : un code qu'on devine à force d'essayer n'est plus un code)."""
    code = (code or "").strip()
    if not code:
        return False
    attendu = _code_attendu()
    if not attendu:
        frappe.throw(_("Aucun code de dispense de photo n'est configuré (Config Cloture Tache)."))
    if not hmac.compare_digest(code, attendu):
        frappe.log_error("Code de dispense de photo refusé — caisse, utilisateur %s"
                         % frappe.session.user, "caisse_pieces")
        frappe.throw(_("Code de dispense incorrect."))
    return True


def tracer_dispense(doctype, name, detail=""):
    """La trace nominative de l'enregistrement sans photo, sur la pièce créée."""
    try:
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Info",
            "reference_doctype": doctype, "reference_name": name,
            "content": _("🔓 Chèque / traite enregistré(e) SANS photo par {0} (code de "
                         "dispense).{1}").format(frappe.session.user,
                                                 (" " + detail) if detail else ""),
        }).insert(ignore_permissions=True)
    except Exception:
        pass    # la trace ne doit jamais faire échouer l'opération


def avertissements(pieces):
    """Lecture OpenAI des photos de chèques / traites -> liste d'avertissements, jamais une
    exception. `pieces` : [{mode, numero, montant, photo}] ; les autres modes et les pièces
    sans photo sont ignorés. À appeler APRÈS le commit."""
    from customization_app.caisse_encaissement_dettes import _avertissements_photos

    lignes = [{"mode": p.get("mode"), "numero": (p.get("numero") or "").strip(),
               "montant": p.get("montant") or 0, "photo": p.get("photo")}
              for p in (pieces or []) if p.get("mode") in MODES_PAPIER and p.get("photo")]
    if not lignes:
        return []
    try:
        return _avertissements_photos(lignes)
    except Exception:
        return [_("La vérification automatique des photos n'a pas abouti — contrôlez les "
                  "pièces à l'œil.")]


# ------------------------------------------------------------------ lecture d'une pièce

#: Alias des banques tels qu'ils apparaissent sur les chèques / traites -> nom de la liste.
_ALIAS_BANQUES = {
    "ZITOUNA": "Banque Zitouna", "ATTIJARI": "Attijari Bank", "AMEN": "Amen Bank",
    "BARAKA": "Al Baraka Bank", "WIFAK": "Al Wifak International Bank", "QNB": "QNB-Tunis",
    "POSTE": "La Poste", "CITI": "Citi Bank", "ARAB BANKING": "ABC",
    "ARAB TUNISIAN BANK": "ATB", "BANQUE DE L'HABITAT": "BH", "BANQUE NATIONALE AGRICOLE": "BNA",
    "SOCIETE TUNISIENNE DE BANQUE": "STB", "BANQUE DE TUNISIE": "BT",
    "UNION INTERNATIONALE": "UIB", "UNION BANCAIRE": "UBCI",
    "BANQUE INTERNATIONALE ARABE": "BIAT", "TUNISIAN SAUDI": "TSB",
}


#: Les banques telles qu'une traite les imprime EN ARABE (case « domiciliation ») -> nom de la
#: liste. Testé sur la chaîne brute, avant l'aplatissement ASCII qui efface l'arabe.
_ALIAS_BANQUES_AR = {
    "الزيتونة": "Banque Zitouna", "زيتونة": "Banque Zitouna",
    "البركة": "Al Baraka Bank", "التجاري": "Attijari Bank", "الأمان": "Amen Bank", "الامان": "Amen Bank",
    "الوفاق": "Al Wifak International Bank", "قطر الوطني": "QNB-Tunis",
    "البريد": "La Poste", "العربي الدولي": "BIAT", "بيات": "BIAT",
    "الوطني الفلاحي": "BNA", "الشركة التونسية للبنك": "STB", "الاتحاد الدولي": "UIB",
    "الإسكان": "BH", "الاسكان": "BH", "الاتحاد البنكي": "UBCI", "التونسي العربي": "ATB",
    "بنك تونس": "BT", "تونس والإمارات": "BTE", "التونسي الكويتي": "BTK", "التونسي الليبي": "BTL",
    "التضامن": "BTS", "التمويل": "BFT", "العربية للمؤسسات": "ABC", "سيتي": "Citi Bank",
    "التونسي السعودي": "TSB",
}


def normaliser_banque(texte, banques):
    """Le nom de la liste des banques qui correspond à ce que la photo montre — "" sinon.

    ⚠️ FONCTION PURE. D'abord le sigle exact en mot entier (« BT » ne doit pas matcher
    « BTE »), puis un alias (« ZITOUNA » -> Banque Zitouna), puis un nom de la liste contenu
    dans le texte.
    """
    import re as _re
    import unicodedata

    def _plat(t):
        t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
        return _re.sub(r"[^A-Z0-9 ]+", " ", t.upper()).strip()

    brut = str(texte or "")
    for alias, nom in _ALIAS_BANQUES_AR.items():
        if alias in brut and nom in (banques or []):
            return nom
    lu = _plat(texte)
    if not lu:
        return ""
    mots = set(lu.split())
    for b in banques or []:
        if _plat(b) in mots:
            return b
    for alias, nom in _ALIAS_BANQUES.items():
        if alias in lu and nom in (banques or []):
            return nom
    for b in sorted(banques or [], key=len, reverse=True):
        pb = _plat(b)
        if len(pb) >= 4 and pb in lu:
            return b
    return ""


def normaliser_numero(valeur, mode=None):
    """Le numéro lu, chiffres seuls, ZÉROS DE TÊTE CONSERVÉS. Fonction pure.

    Le modèle peut rendre un nombre JSON (12345 pour « 0012345 ») : les zéros sont alors
    perdus en amont. Pour un CHÈQUE, dont le numéro tunisien fait toujours 7 chiffres, on
    les restitue en complétant à gauche ; une traite n'a pas de longueur fixe, on garde tel
    quel (le prompt exige une chaîne, et l'employé vérifie sur l'aperçu).
    """
    if valeur is None or valeur is False:
        return ""
    if isinstance(valeur, float) and valeur.is_integer():
        valeur = int(valeur)
    numero = re.sub(r"\D", "", str(valeur))
    if mode == "Chèque" and 0 < len(numero) < 7:
        numero = numero.zfill(7)
    return numero


@frappe.whitelist()
def lire_piece(photo, mode=None):
    """Lit une photo de chèque / traite avec OpenAI pour PRÉ-REMPLIR la saisie (demande
    utilisateur 16/09/2026) : numéro, montant, banque (ramenée à la liste), échéance.

    Ne lève jamais vers l'écran : une panne rend `{"erreur": …}` et l'employé saisit à la
    main. Rien n'est écrit — c'est une aide à la saisie, la vérification après
    enregistrement (`avertissements`) reste en place.
    """
    from customization_app.caisse_encaissement_dettes import banques as _banques

    libelle = "traite (lettre de change)" if mode == "Traite bancaire" else "chèque"
    if not (photo or "").startswith("data:image/"):
        return {"erreur": _("La pièce jointe n'est pas une photo (PDF ?) : saisie manuelle.")}
    liste = _banques()
    try:
        from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

        client_ia, model, _t = _get_client_model_temp()
        res = client_ia.responses.create(
            model=model,
            instructions=(
                "Tu lis la photo d'un chèque ou d'une traite (lettre de change) bancaire "
                "tunisien(ne). Réponds STRICTEMENT en JSON : "
                '{"numero": "<numéro du document EN CHAÎNE DE CARACTÈRES, chiffres uniquement, '
                'EN GARDANT LES ZÉROS DE TÊTE (ex. \"0012345\"), null si illisible>", '
                '"montant": <montant en dinars lu en chiffres, null si illisible>, '
                '"banque": "<la banque du document, CHOISIE DANS CETTE LISTE : ' + ", ".join(liste) + '. '
                'Sur une traite, c\'est la banque DOMICILIATAIRE (case « domiciliation » / « banque »), '
                'souvent écrite en arabe : traduis-la vers la liste. Si aucune ne correspond, le nom tel '
                'qu\'imprimé ; null si absent>", '
                '"echeance": "<date d\'échéance ou date du document, AAAA-MM-JJ, null si absente>", '
                '"beneficiaire": "<bénéficiaire, null si illisible>", '
                '"lisible": <true si la photo montre bien un chèque ou une traite exploitable>}. '
                "Un numéro de chèque tunisien a 7 chiffres, zéros de tête compris : "
                "\"0012345\" et non 12345. Le numéro d'une traite (lettre de change) est la "
                "suite COMPLÈTE de chiffres imprimée après « L.C N° » / « Ordre de paiement » : "
                "souvent 10 à 14 chiffres, recopie-les TOUS, du premier au dernier, sans en "
                "omettre ni en regrouper. Le numéro n'est JAMAIS un nombre JSON."),
            input=[{"role": "user", "content": [
                # detail=high : la lecture d'un numéro long (traite à 12 chiffres) demandait
                # plus de pixels — en définition standard, les derniers chiffres tombaient
                # (retour utilisateur 16/09/2026 : « 011791623 » pour « 011791623804 »).
                {"type": "input_image", "image_url": photo, "detail": "high"},
                {"type": "input_text", "text": "Lis ce document (%s). Recopie le numéro "
                 "intégralement, chiffre par chiffre." % libelle}]}])
        texte = (res.output_text or "").strip().strip("`")
        if texte.lower().startswith("json"):
            texte = texte.split("\n", 1)[1]
        lu = json.loads(texte)
    except Exception:
        frappe.log_error(title="Caisse : lecture de pièce indisponible",
                         message=frappe.get_traceback())
        return {"erreur": _("Lecture automatique indisponible : saisie manuelle.")}
    if not isinstance(lu, dict):
        return {"erreur": _("Lecture automatique inexploitable : saisie manuelle.")}
    numero = normaliser_numero(lu.get("numero"), mode)
    try:
        montant = round(float(lu.get("montant")), 3) if lu.get("montant") is not None else None
    except Exception:
        montant = None
    echeance = str(lu.get("echeance") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", echeance):
        echeance = ""
    return {"numero": numero, "montant": montant,
            "banque": normaliser_banque(lu.get("banque"), liste),
            "banque_lue": lu.get("banque") or "", "echeance": echeance,
            "beneficiaire": lu.get("beneficiaire") or "",
            "lisible": bool(lu.get("lisible", True))}
