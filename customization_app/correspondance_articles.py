"""La mémoire « article du fournisseur → notre article » (demande du 25/11/2026).

POURQUOI. À la lecture d'une facture (caisse › Dépense › « En partie achat »), l'article ERPNext de chaque ligne était
DEVINÉ par les mots de la désignation : pour « 2030042 REDUCTION LAITON SPECIAL 1/2"F*3/8"M » de MH2C, la devinette
proposait « tuy-02… », alors que c'est BRS-SP-15-12 — ce que la réception du BL B26001723 disait déjà. Sa demande :
« dans cette logique d'identification entre article et mes articles, un learning par rapport à ce qui a été saisi ».

CE QU'ELLE RETIENT. Par fournisseur, une correspondance par CODE article du fournisseur (la colonne « Article » de sa
facture : 2030042, 9910019…), sinon par DÉSIGNATION normalisée → notre article. DocType « Correspondance Article
Fournisseur » : on la voit, on la corrige (responsable du stock, comptable).

ELLE APPREND (sa décision du 25/11/2026 : dès le premier choix, compteur visible) :
  · d'un BL RECONNU : les lignes imprimées sous la rubrique « TOTAL BL N° … » d'un BL couvert, appariées aux lignes de
    sa réception (ligne unique, ou même quantité) — apprentissage sûr (« BL reconnu ») ;
  · du CHOIX de l'employé en « En partie achat », à l'enregistrement (« Choix employé ») ; s'il change l'article que la
    mémoire proposait, la correspondance est remplacée (« Correction ») et l'ancienne reste dans l'historique.
  Elle ne bloque jamais une saisie : un échec d'apprentissage est journalisé, la dépense passe.

ELLE SERT à la lecture des lignes : la mémoire (code, puis désignation) d'abord, la devinette ensuite ; l'écran dit
d'où vient chaque proposition. En prime, le code du fournisseur s'inscrit dans la « Réf. fournisseur » de la fiche
article (onglet Fournisseurs d'ERPNext), sans enregistrer l'article (aucun crochet déclenché).

Les fonctions pures n'utilisent pas `frappe.utils.flt` (hors site, il rend 0).
"""
from __future__ import annotations

import json
import re
import unicodedata

import frappe

DOCTYPE = "Correspondance Article Fournisseur"
CHOIX, CORRECTION, PAR_BL = "Choix employé", "Correction", "BL reconnu"
APPRIS, DEVINE = "appris", "devine"
LONGUEUR_CLE = 240


def _m(valeur) -> float:
    try:
        return round(float(valeur or 0), 3)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------------------------------------------- pur
def _ascii_majuscule(texte) -> str:
    return unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode().upper()


def cle_code(code) -> str:
    """Le code article du fournisseur réduit à ce qui l'identifie. PURE. « 2030042 », « 2030 042 », « 02030042 » → 2030042."""
    txt = re.sub(r"[^A-Z0-9]", "", _ascii_majuscule(code))
    return txt.lstrip("0") or txt


def cle_designation(texte) -> str:
    """La désignation du fournisseur réduite à ses mots. PURE.
    'REDUCTION LAITON SPECIAL 1/2"F*3/8"M' et 'Réduction laiton spécial 1/2" F * 3/8" M' → même clé."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", _ascii_majuscule(texte)).split())


def cles(supplier, code=None, designation=None) -> list:
    """Les clés sous lesquelles chercher (ou ranger) une ligne, de la plus sûre à la moins sûre. PURE."""
    out = []
    if supplier and cle_code(code):
        out.append(("%s|C|%s" % (supplier, cle_code(code)))[:LONGUEUR_CLE])
    if supplier and cle_designation(designation):
        out.append(("%s|D|%s" % (supplier, cle_designation(designation)))[:LONGUEUR_CLE])
    return out


def decision(existante, item_code) -> str:
    """Que faire d'une correspondance existante (dict ou None) quand une saisie donne `item_code`. PURE.
    -> "creer" | "confirmer" (même article : le compteur monte) | "corriger" (autre article : remplacée)."""
    if not existante:
        return "creer"
    return "confirmer" if existante.get("item_code") == item_code else "corriger"


def apparier(lignes_facture, lignes_recu) -> list:
    """Les lignes de la facture imprimées sous la rubrique d'un BL, appariées aux lignes de SA réception. PURE.
    `lignes_facture` : [{code, designation, qty}] ; `lignes_recu` : [{item_code, qty}]. SÛR seulement (relecture du
    25/11/2026) : une ligne de chaque côté ET la même quantité ; plusieurs lignes : autant de lignes des deux côtés, et
    une quantité qui n'apparaît qu'une fois de chaque côté. Une réception partielle ou une ligne manquante n'apprend
    rien. -> [(ligne_facture, item_code)]."""
    factures = [l for l in lignes_facture or [] if (l.get("code") or l.get("designation"))]
    recus = [r for r in lignes_recu or [] if r.get("item_code")]
    meme = lambda a, b: abs(_m(a) - _m(b)) < 0.0005   # noqa: E731
    if len(factures) == 1 and len(recus) == 1:
        return [(factures[0], recus[0]["item_code"])] if meme(factures[0].get("qty"), recus[0].get("qty")) else []
    if len(factures) != len(recus):
        return []
    paires = []
    for l in factures:
        q = _m(l.get("qty"))
        memes_f = [x for x in factures if meme(x.get("qty"), q)]
        memes_r = [r for r in recus if meme(r.get("qty"), q)]
        if q > 0 and len(memes_f) == 1 and len(memes_r) == 1:
            paires.append((l, memes_r[0]["item_code"]))
    return paires


def _mots(texte) -> set:
    return {m for m in cle_designation(texte).split() if len(m) >= 3 and not m.isdigit()}


def libelles_compatibles(a, b) -> bool:
    """Deux désignations du même article ? PURE. Vrai si l'une manque, ou si elles ont au moins un mot en commun (3
    lettres ou plus) : un CODE mal lu par l'IA qui tombe sur le code d'un autre article ne le fait pas proposer."""
    ma, mb = _mots(a), _mots(b)
    return not ma or not mb or bool(ma & mb)


def ligne_historique(quand, ancien, fois, nouveau, par) -> str:
    return "%s : %s (confirmé %s fois) remplacé par %s — %s" % (str(quand)[:16], ancien, fois, nouveau, par or "?")


# --------------------------------------------------------------------------------------------------------- avec le site
def _article_valide(item_code) -> bool:
    return bool(item_code and frappe.db.get_value("Item", {"name": item_code, "disabled": 0}, "name"))


def proposer(supplier, code=None, designation=None) -> dict | None:
    """Ce que la mémoire sait de cette ligne : {item_code, fois, source, cle, par} — le code d'abord, puis la
    désignation. None si rien d'appris (ou l'article appris a été désactivé)."""
    for cle in cles(supplier, code, designation):
        c = frappe.db.get_value(DOCTYPE, {"cle": cle}, ["name", "item_code", "fois", "source", "designation"], as_dict=True)
        if c and "|C|" in cle and not libelles_compatibles(designation, c.designation):
            continue          # code mal lu ? on essaie la désignation
        if c and _article_valide(c.item_code):
            return {"item_code": c.item_code, "fois": int(c.fois or 1), "source": c.source, "cle": cle,
                    "par": "code" if "|C|" in cle else "designation", "name": c.name}
    return None


def proposer_pour_lignes(supplier, lignes: list) -> list:
    """Pose sur chaque ligne lue l'article proposé et d'où il vient : `suggestion_source` = « appris » (mémoire,
    avec `suggestion_fois`) ou « devine » (mots de la désignation) ; `suggestion_item` = la proposition, pour que
    l'enregistrement sache si l'employé l'a changée."""
    from customization_app.caisse_stock_partiel import _suggerer_article

    for l in lignes or []:
        appris = proposer(supplier, l.get("code"), l.get("designation")) if supplier else None
        if appris:
            l.update(item_code=appris["item_code"], suggestion_source=APPRIS, suggestion_fois=appris["fois"],
                     suggestion_cle=appris["cle"])
        else:
            l.update(item_code=_suggerer_article(l.get("designation") or ""), suggestion_source=DEVINE, suggestion_fois=0,
                     suggestion_cle=None)
            if not l["item_code"]:
                l["suggestion_source"] = ""
        l["suggestion_item"] = l.get("item_code")
    return lignes


@frappe.whitelist()
def proposer_lignes(supplier, lignes):
    """La fenêtre Dépense : le fournisseur vient d'être confirmé APRÈS la lecture des lignes — on repropose."""
    from customization_app import caisse_depenses as C

    frappe.only_for(C.ROLES)
    lignes = json.loads(lignes) if isinstance(lignes, str) else (lignes or [])
    if not supplier or not frappe.db.exists("Supplier", supplier):
        return lignes
    return proposer_pour_lignes(supplier, lignes)


def _reference_fournisseur(item_code, supplier, code, ancien_item=None):
    """La « Réf. fournisseur » de la fiche article (Item Supplier), posée SANS enregistrer l'article : aucun crochet
    (synchronisation de la boutique…) n'est déclenché. Une correction l'enlève de l'ancien article."""
    code = (code or "").strip()
    if not code:
        return
    if ancien_item and ancien_item != item_code:
        frappe.db.sql("""update `tabItem Supplier` set supplier_part_no = '' where parenttype = 'Item' and parent = %s
                         and supplier = %s and supplier_part_no = %s""", (ancien_item, supplier, code))
        frappe.clear_document_cache("Item", ancien_item)
    try:
        _poser_reference(item_code, supplier, code)
    finally:
        frappe.clear_document_cache("Item", item_code)   # la commande d'achat relit la fiche : pas de copie périmée


def _poser_reference(item_code, supplier, code):
    ligne = frappe.db.get_value("Item Supplier", {"parenttype": "Item", "parent": item_code, "supplier": supplier},
                                ["name", "supplier_part_no"], as_dict=True)
    if ligne:
        if not (ligne.supplier_part_no or "").strip():
            frappe.db.set_value("Item Supplier", ligne.name, "supplier_part_no", code, update_modified=False)
        return
    d = frappe.new_doc("Item Supplier")
    d.update({"parent": item_code, "parenttype": "Item", "parentfield": "supplier_items", "supplier": supplier,
              "supplier_part_no": code,
              "idx": (frappe.db.sql("select max(idx) from `tabItem Supplier` where parent = %s", item_code)[0][0] or 0) + 1})
    d.name = frappe.generate_hash(length=10)
    d.db_insert()


def apprendre(supplier, code, designation, item_code, source=CHOIX, piece=None, remplacer=False, cle=None) -> str | None:
    """Range (ou confirme) la correspondance de cette ligne. -> le nom de la correspondance, ou None.

    ⚠️ NE REMPLACE une correspondance d'un AUTRE article que si `remplacer` : l'employé a VU la proposition de la
    mémoire et l'a changée (Correction). Une devinette acceptée telle quelle, une proposition calculée sans fournisseur,
    ou un appariement de BL ne détruisent jamais ce qui a été appris : le désaccord est noté dans l'historique
    (relecture du 25/11/2026). `cle` : la clé à corriger quand ce n'est pas la première de la ligne (proposition faite
    par la désignation alors que la ligne a un code)."""
    if not supplier or not _article_valide(item_code):
        return None
    toutes = cles(supplier, code, designation)
    if not toutes:
        return None
    cle = cle or toutes[0]          # le code s'il existe, sinon la désignation
    maintenant = frappe.utils.now()
    existante = frappe.db.get_value(DOCTYPE, {"cle": cle}, ["name", "item_code", "fois", "historique", "designation"],
                                    as_dict=True, for_update=True)
    quoi = decision(existante, item_code)
    if quoi == "corriger" and not remplacer:
        note = "%s : %s a donné %s (%s) — correspondance gardée, à vérifier" % (
            str(maintenant)[:16], source, item_code, piece or "?")
        frappe.db.set_value(DOCTYPE, existante.name, "historique",
                            "\n".join(x for x in ((existante.historique or "").strip(), note) if x), update_modified=False)
        return None
    if quoi == "confirmer":
        valeurs = {"fois": int(existante.fois or 0) + 1, "dernier_usage": maintenant, "appris_par": frappe.session.user,
                   "piece": piece}
        if designation and not existante.designation:
            valeurs["designation"] = designation.strip()[:240]
        frappe.db.set_value(DOCTYPE, existante.name, valeurs, update_modified=True)
        nom = existante.name
    elif quoi == "corriger":
        historique = "\n".join(x for x in ((existante.historique or "").strip(), ligne_historique(
            maintenant, existante.item_code, existante.fois, item_code, frappe.session.user)) if x)
        frappe.db.set_value(DOCTYPE, existante.name, {
            "item_code": item_code, "fois": 1, "source": CORRECTION, "dernier_usage": maintenant,
            "appris_par": frappe.session.user, "piece": piece, "historique": historique}, update_modified=True)
        nom = existante.name
    else:
        doc = frappe.get_doc({"doctype": DOCTYPE, "supplier": supplier, "code_fournisseur": (code or "").strip() or None,
                              "designation": (designation or "").strip()[:240] or None, "item_code": item_code,
                              "fois": 1, "source": source, "cle": cle, "dernier_usage": maintenant,
                              "appris_par": frappe.session.user, "piece": piece})
        doc.flags.cle_calculee = True
        doc.insert(ignore_permissions=True)
        nom = doc.name
    if "|C|" in cle:
        _reference_fournisseur(item_code, supplier, code, ancien_item=existante.item_code if existante else None)
    return nom


def apprendre_lignes_stock(supplier, lignes, piece=None) -> list:
    """Les lignes cochées en « En partie achat » : l'article choisi par l'employé est retenu. Une proposition de la
    mémoire qu'il a VUE (« appris ») et changée est une CORRECTION — elle seule remplace l'ancienne correspondance.
    Une clé qui reçoit deux articles différents dans la même saisie (désignation générique) n'apprend rien."""
    par_cle = {}
    for l in lignes or []:
        toutes = cles(supplier, l.get("code"), l.get("designation"))
        if toutes and l.get("item_code"):
            par_cle.setdefault(toutes[0], []).append(l)
    faits = []
    for cle, groupe in par_cle.items():
        if len({l["item_code"] for l in groupe}) > 1:
            continue                      # ambiguë dans cette saisie : on ne choisit pas
        l = groupe[0]
        vue = l.get("suggestion_source") == APPRIS and l.get("suggestion_item")
        correction = bool(vue and l.get("suggestion_item") != l.get("item_code"))
        nom = apprendre(supplier, l.get("code"), l.get("designation"), l["item_code"],
                        CORRECTION if correction else CHOIX, piece, remplacer=correction)
        # La proposition corrigée venait d'une autre clé (la désignation, alors que la ligne a un code) : elle aussi.
        if correction and l.get("suggestion_cle") and l["suggestion_cle"] != cle:
            apprendre(supplier, l.get("code"), l.get("designation"), l["item_code"], CORRECTION, piece,
                      remplacer=True, cle=l["suggestion_cle"])
        if nom:
            faits.append(nom)
    return faits


def apprendre_depuis_bls(supplier, lignes_facture, bls, piece=None) -> list:
    """Les lignes imprimées sous la rubrique d'un BL couvert, appariées à SA réception : apprentissage sûr.
    `bls` : [{fiche, numero_bl, recu_doc}] (caisse_bl.couvrir_par_depense)."""
    from customization_app.caisse_bl import cle_numero_bl, cles_fiche

    faits = []
    for b in bls or []:
        if not b.get("recu_doc"):
            continue
        fiche = frappe.db.get_value("Facture Achat a Saisir", b["fiche"], ["numero_bl", "numero_facture"], as_dict=True) or {}
        cles_bl = cles_fiche(b.get("numero_bl") or fiche.get("numero_bl"), fiche.get("numero_facture")) \
            | cles_fiche(b.get("numero_bl"))
        rubrique = [l for l in lignes_facture or [] if cle_numero_bl(l.get("bl_cle") or l.get("bl")) in cles_bl]
        recu = frappe.get_all("Purchase Receipt Item", filters={"parent": b["recu_doc"]}, fields=["item_code", "qty"])
        for ligne, item_code in apparier(rubrique, recu):
            nom = apprendre(supplier, ligne.get("code"), ligne.get("designation"), item_code, PAR_BL,
                            piece or b["recu_doc"])
            if nom:
                faits.append(nom)
    return faits


def apprendre_de_la_saisie(supplier, lignes_stock=None, lignes_lues=None, bls=None, piece=None) -> list:
    """Appelé par caisse_depenses.creer, dans sa transaction : tout l'apprentissage d'une saisie, dans un point de
    reprise — un échec est annulé et journalisé, JAMAIS remonté (la dépense passe)."""
    if not supplier:
        return []
    frappe.db.savepoint("apprentissage_articles")
    try:
        faits = apprendre_lignes_stock(supplier, lignes_stock, piece)
        if bls and lignes_lues:
            faits += apprendre_depuis_bls(supplier, lignes_lues, bls, piece)
        return faits
    except Exception:
        frappe.db.rollback(save_point="apprentissage_articles")
        frappe.log_error(title="Caisse : apprentissage des articles", message=frappe.get_traceback())
        return []


def apprendre_en_fond(depense, supplier, bls):
    """BL couverts sans lignes lues (« Seulement dépense ») : la tâche de fond relit les lignes du scan joint à la
    dépense et apprend des BL. Un appel IA, après l'enregistrement ; un échec n'abîme rien."""
    from customization_app import caisse_stock_partiel as S

    bls = [b for b in bls or [] if b.get("recu_doc")]
    if not bls:
        return
    try:
        fichier = frappe.get_all("File", filters={"attached_to_doctype": "Depense A Payer", "attached_to_name": depense,
                                                  "file_name": ["like", "facture-%"]},
                                 fields=["name"], order_by="creation desc", limit=1)
        if not fichier:
            return
        contenu = frappe.get_doc("File", fichier[0].name).get_content()
        if isinstance(contenu, str):
            contenu = contenu.encode("utf-8")
        mimetype = "application/pdf" if contenu[:4] == b"%PDF" else "image/jpeg"
        lu = S.lire_lignes(contenu, mimetype)
        apprendre_depuis_bls(supplier, lu["lignes"], bls, depense)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Caisse : apprentissage des articles (fond)", message=frappe.get_traceback())
