"""
Renvoi au fournisseur de SON PROPRE classeur, annoté de nos ajustements.

Le fournisseur a bâti sa cotation dans son fichier, avec ses références, son
ordre et sa langue. Lui répondre dans un classeur maison l'oblige à tout
réapparier à la main — c'est là que se perdent les remises et que reviennent
les mauvaises quantités. On lui renvoie donc le fichier qu'il a envoyé, avec
quatre colonnes ajoutées à droite (quantité retenue, prix cible, décision,
observation), les écarts surlignés, et nos lignes absentes de sa cotation
ajoutées en bas.

Deux limites assumées, dites à l'écran :
  - un .xls (format d'avant 2007) ne se réécrit pas : on le convertit en .xlsx,
    les valeurs sont conservées, la mise en forme d'origine non ;
  - les lignes qu'on n'a pas su apparier à l'import n'ont pas de place dans sa
    grille : elles sont listées en bas plutôt que devinées.
"""

import json
import os

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from customization_app.lci_observation import qty_retenue
from customization_app.liste_commande_import import DOCTYPE, _guard

LABELS = {
    "Français": {
        "qty": "Qté retenue", "prix": "Prix cible", "decision": "Décision",
        "observation": "Observation", "conteneur": "Conteneur",
        "ajoutees": "LIGNES NON COTÉES — merci de les chiffrer",
        "legende": "Cellules surlignées : valeurs modifiées de notre côté. "
                   "Lignes grisées : abandonnées.",
        "code": "Code", "designation": "Désignation", "titre": "Contre-proposition",
    },
    "English": {
        "qty": "Target qty", "prix": "Target price", "decision": "Decision",
        "observation": "Remark", "conteneur": "Container",
        "ajoutees": "LINES NOT QUOTED — please price them",
        "legende": "Highlighted cells: values changed on our side. "
                   "Greyed rows: dropped.",
        "code": "Code", "designation": "Description", "titre": "Counter-offer",
    },
    "Deutsch": {
        "qty": "Zielmenge", "prix": "Zielpreis", "decision": "Entscheidung",
        "observation": "Bemerkung", "conteneur": "Container",
        "ajoutees": "NICHT ANGEBOTENE POSITIONEN — bitte bepreisen",
        "legende": "Markierte Zellen: von uns geänderte Werte. "
                   "Graue Zeilen: gestrichen.",
        "code": "Code", "designation": "Bezeichnung", "titre": "Gegenangebot",
    },
    "Español": {
        "qty": "Cant. objetivo", "prix": "Precio objetivo", "decision": "Decisión",
        "observation": "Observación", "conteneur": "Contenedor",
        "ajoutees": "LÍNEAS NO COTIZADAS — rogamos cotizarlas",
        "legende": "Celdas resaltadas: valores modificados por nosotros. "
                   "Filas grises: descartadas.",
        "code": "Código", "designation": "Descripción", "titre": "Contraoferta",
    },
    "العربية": {
        "qty": "الكمية المعتمدة", "prix": "السعر المستهدف", "decision": "القرار",
        "observation": "ملاحظة", "conteneur": "الحاوية",
        "ajoutees": "بنود غير مسعّرة — يرجى تسعيرها",
        "legende": "الخلايا الملوّنة: قيم عدّلناها. الأسطر الرمادية: بنود مستبعدة.",
        "code": "المرجع", "designation": "التسمية", "titre": "عرض مضاد",
    },
}

DECISIONS = {
    "English": {"À négocier": "To negotiate", "Accepté": "Accepted", "Abandonné": "Dropped"},
    "Deutsch": {"À négocier": "Zu verhandeln", "Accepté": "Angenommen", "Abandonné": "Gestrichen"},
    "Español": {"À négocier": "A negociar", "Accepté": "Aceptado", "Abandonné": "Descartado"},
    "العربية": {"À négocier": "قيد التفاوض", "Accepté": "مقبول", "Abandonné": "مستبعد"},
}


def _labels(doc):
    return LABELS.get(doc.langue_cible) or LABELS["Français"]


def _decision(doc, valeur):
    return (DECISIONS.get(doc.langue_cible) or {}).get(valeur, valeur or "")


def plan_lecture(doc):
    """Plan de lecture du dernier fichier importé, ou throw explicite."""
    if not doc.fichier_fournisseur or not doc.reponse_plan:
        frappe.throw(_("Aucun fichier fournisseur n'a été importé sur cette liste : "
                       "importez d'abord la réponse du fournisseur, le fichier reçu "
                       "sert de base au renvoi."))
    try:
        return json.loads(doc.reponse_plan)
    except Exception:
        frappe.throw(_("Le plan de lecture du fichier fournisseur est illisible : "
                       "réimportez la réponse du fournisseur."))


def _fichier_local(file_url):
    return frappe.get_doc("File", {"file_url": file_url}).get_full_path()


def _charger(chemin):
    """Classeur ouvert EN ÉCRITURE. Rend (workbook, converti?)."""
    from openpyxl import Workbook, load_workbook

    ext = os.path.splitext(chemin)[1].lower()
    if ext != ".xls":
        # data_only=False : les formules du fournisseur restent des formules,
        # sinon son fichier revient figé sur les valeurs du jour de l'envoi.
        return load_workbook(chemin), False

    import xlrd
    src = xlrd.open_workbook(chemin, formatting_info=False)
    wb = Workbook()
    wb.remove(wb.active)
    for sh in src.sheets():
        ws = wb.create_sheet(title=sh.name[:31])
        for i in range(sh.nrows):
            ws.append([sh.cell_value(i, j) for j in range(sh.ncols)])
        for j in range(min(sh.ncols, 40)):
            from openpyxl.utils import get_column_letter
            ws.column_dimensions[get_column_letter(j + 1)].width = 18
    return wb, True


def _prix_cible_export(row):
    """Le prix qu'on AFFICHE dans la colonne « prix cible » du fichier renvoyé.

    Notre contre-proposition si on en a une, sinon notre cible initiale — pas
    son prix à lui : une colonne « Target price » qui recopie sa cotation
    contredirait l'observation qui, juste à côté, dit qu'elle est trop chère.
    Vide quand on n'a aucune cible : on accepte son prix tel quel.
    """
    return flt(row.prix_cible_negocie) or flt(row.prix_cible) or 0.0


def _conteneurs_ligne(row):
    try:
        parts = json.loads(row.repartition_conteneurs or "[]") or []
    except Exception:
        return ""
    if not parts:
        return ""
    if len(parts) == 1:
        return f"C{parts[0].get('no')}"
    return " + ".join(f"C{p.get('no')} ({flt(p.get('qty')):g})" for p in parts)


@frappe.whitelist()
def download_reponse_annotee(docname):
    """Rend le classeur du fournisseur enrichi de nos ajustements."""
    import io

    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    plan = plan_lecture(doc)
    L = _labels(doc)
    devise = doc.devise or "USD"

    wb, converti = _charger(_fichier_local(doc.fichier_fournisseur))
    feuille = plan.get("feuille")
    if feuille not in wb.sheetnames:
        frappe.throw(_("La feuille « {0} » n'existe plus dans le fichier joint.").format(feuille))
    ws = wb[feuille]

    corresp = {k: int(v) for k, v in (plan.get("correspondances") or {}).items()}
    mapping = plan.get("mapping") or {}
    ligne_entete = int(plan.get("ligne_entete") or 0) + 1      # 1-based

    JAUNE = PatternFill("solid", fgColor="FFF3CD")   # valeur changée par nous
    GRIS = PatternFill("solid", fgColor="EDEDED")    # ligne abandonnée
    ENTETE = PatternFill("solid", fgColor="D9E7FF")
    gras = Font(bold=True)

    col0 = (ws.max_column or 1) + 2                  # une colonne vide de respiration
    avec_conteneurs = any((r.repartition_conteneurs or "") for r in doc.articles)
    colonnes = [("qty", L["qty"], 13), ("prix", f'{L["prix"]} ({devise})', 15),
                ("decision", L["decision"], 14), ("observation", L["observation"], 52)]
    if avec_conteneurs:
        colonnes.append(("conteneur", L["conteneur"], 14))
    pos = {cle: col0 + i for i, (cle, _lbl, _w) in enumerate(colonnes)}

    for i, (_cle, lbl, w) in enumerate(colonnes):
        c = ws.cell(row=ligne_entete, column=col0 + i, value=lbl)
        c.font = gras
        c.fill = ENTETE
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(col0 + i)].width = w

    dernier = ligne_entete
    non_appariees = []
    for r in doc.articles:
        xl = corresp.get(r.name)
        if not xl:
            if (r.decision or "") != "Abandonné":
                non_appariees.append(r)
            continue
        dernier = max(dernier, xl)

        qty = qty_retenue(r)
        prix = _prix_cible_export(r)
        abandon = (r.decision or "") == "Abandonné"

        # quantité vide (et non zéro) quand la ligne n'est pas encore tranchée :
        # un « 0 » se lit comme un refus, un blanc comme une question ouverte.
        cq = ws.cell(row=xl, column=pos["qty"],
                     value=0 if abandon else (qty if qty > 0 else None))
        cp = ws.cell(row=xl, column=pos["prix"], value=prix or None)
        ws.cell(row=xl, column=pos["decision"], value=_decision(doc, r.decision))
        co = ws.cell(row=xl, column=pos["observation"], value=r.observation or "")
        co.alignment = Alignment(wrap_text=True, vertical="top")
        if avec_conteneurs:
            ws.cell(row=xl, column=pos["conteneur"], value=_conteneurs_ligne(r))

        # surlignage : seulement ce qui CHANGE par rapport à ce qu'il a coté
        qty_frn = flt(r.qty_fournisseur) or flt(r.qty)
        if abandon or abs(qty - qty_frn) > 0.001:
            cq.fill = JAUNE
        if prix > 0 and abs(prix - flt(r.prix_fournisseur)) > 0.00001:
            cp.fill = JAUNE
        if abandon:
            for c in range(col0, col0 + len(colonnes)):
                ws.cell(row=xl, column=c).fill = GRIS

    # nos lignes que sa cotation ne contient pas : ajoutées sous le tableau,
    # dans SES colonnes quand on sait où elles sont.
    ligne = dernier + 3
    if non_appariees:
        t = ws.cell(row=ligne, column=1, value=L["ajoutees"])
        t.font = Font(bold=True, color="FFB00020")
        ligne += 1
        c_code = (mapping.get("code") or 0) + 1
        c_des = (mapping.get("designation") or 1) + 1
        c_qty = (mapping.get("qty") or 2) + 1
        for r in non_appariees:
            ws.cell(row=ligne, column=c_code, value=r.item_code or "")
            ws.cell(row=ligne, column=c_des,
                    value=r.item_name_traduit or r.item_name or "")
            ws.cell(row=ligne, column=c_qty, value=qty_retenue(r))
            ws.cell(row=ligne, column=pos["qty"], value=qty_retenue(r)).fill = JAUNE
            if _prix_cible_export(r):
                ws.cell(row=ligne, column=pos["prix"], value=_prix_cible_export(r))
            ws.cell(row=ligne, column=pos["decision"], value=_decision(doc, r.decision))
            ws.cell(row=ligne, column=pos["observation"], value=r.observation or "")
            ligne += 1

    ligne += 1
    lg = ws.cell(row=ligne, column=col0,
                 value=f"{L['titre']} — {doc.name} — {doc.date_commande or nowdate()} · {L['legende']}")
    lg.font = Font(italic=True, size=9, color="FF6B7280")
    if converti:
        ws.cell(row=ligne + 1, column=col0,
                value=_("Fichier .xls converti en .xlsx : valeurs conservées, "
                        "mise en forme d'origine perdue.")).font = Font(italic=True, size=9)

    # le plan de chargement part dans un onglet à lui : le fournisseur charge
    # les conteneurs, il lui faut la même feuille que nous.
    from customization_app.lci_conteneurs import ecrire_feuille
    ecrire_feuille(wb, doc)

    buf = io.BytesIO()
    wb.save(buf)
    base = os.path.splitext(os.path.basename(doc.fichier_fournisseur))[0][:60]
    frappe.response["filename"] = f"{base}-{doc.name}-annote.xlsx"
    frappe.response["filecontent"] = buf.getvalue()
    frappe.response["type"] = "binary"
