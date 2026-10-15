"""Liste d'appels — ce que l'appel change ailleurs (porté du Server Script « update donnee appelle », After Save, qui
n'existait qu'en base ; éteint par patch desactiver_server_script_update_donnee_appelle, 03/10/2026).

Pour chaque ligne cochée « a été appelé » : la fiche client reprend les deux cases « intéressé par l'entretien » et
« envoi SMS » (le script d'origine n'en mettait à jour qu'UNE par passage : `elif`), et les visites concernées
(detail_articles = {article: date}) sont marquées `custom_appelle` sur TOUS les échéanciers soumis du client — un
client regroupé n'a plus qu'une ligne, ses autres échéanciers doivent aussi savoir qu'il a été appelé. Une visite déjà
marquée garde sa date (le script réécrivait la date du jour à chaque enregistrement)."""
from __future__ import annotations

import json

import frappe
from frappe.utils import getdate, nowdate

NE_REPOND_PAS = "Ne répond pas 1er appel"
NE_VEUT_PLUS = "Ne veut plus d'entretien"        # 15/10/2026 : « pas besoin », « pas d'appareil », « ne plus appeler »
RAPPELER = "Rappeler plus tard"                  # 15/10/2026 : « rappel fin décembre » → relance reportée à la date
PAS_DE_SYSTEME = "Il n'y a pas de système de traitement installé"
RAPPEL_DEFAUT_MOIS = 3


def effet_reponse(issue: str, statut: str) -> dict:
    """Ce que la réponse d'un appel écrit sur la fiche client. « Ne veut plus d'entretien » et « pas de système » arrêtent
    la relance (Perdu ; le premier passe aussi « Non » à l'entretien) ; « Rendez-vous pris » réactive un client
    « À requalifier ». Un client déjà « Perdu » ne bouge plus. PURE."""
    if issue == NE_VEUT_PLUS and statut != "Perdu":
        return {"custom_statut_relance": "Perdu", "custom_intéressé_par_le_service_entretien": "Non"}
    if issue == PAS_DE_SYSTEME and statut != "Perdu":
        return {"custom_statut_relance": "Perdu"}
    if issue == "Rendez-vous pris" and statut == "À requalifier":
        return {"custom_statut_relance": ""}
    return {}


def date_de_rappel(rappeler_le, today, mois=RAPPEL_DEFAUT_MOIS):
    """La date de reprise : celle saisie si elle est à venir, sinon dans `mois`. PURE."""
    from frappe.utils import add_months
    if rappeler_le and getdate(rappeler_le) > getdate(today):
        return getdate(rappeler_le)
    return add_months(getdate(today), mois)


def articles_appeles(detail_articles) -> dict:
    """{article: date} depuis le JSON de la ligne ; vide si illisible. PURE."""
    try:
        d = json.loads(detail_articles or "{}")
        return {k: getdate(v) for k, v in d.items() if k and v and not k.startswith("_")}
    except Exception:
        return {}


def synchroniser_appels(doc, method=None):
    """Hook on_update de Liste Appelle Entretien."""
    for ligne in doc.get("clients") or []:
        if not ligne.get("a_été_appelé") or not ligne.get("client"):
            continue
        try:
            _synchroniser_ligne(ligne)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Liste d'appels {doc.name} : ligne {ligne.get('client')}")


def _synchroniser_ligne(ligne):
    client = frappe.db.get_value("Customer", ligne.client, ["custom_intéressé_par_le_service_entretien", "custom_envoi_sms"], as_dict=True)
    if client:
        maj = {}
        if ligne.get("intéressé_par_le_service_dentretien") and client.custom_intéressé_par_le_service_entretien != ligne.intéressé_par_le_service_dentretien:
            maj["custom_intéressé_par_le_service_entretien"] = ligne.intéressé_par_le_service_dentretien
        if ligne.get("intéressé_par_le_service_de_relance") and client.custom_envoi_sms != ligne.intéressé_par_le_service_de_relance:
            maj["custom_envoi_sms"] = ligne.intéressé_par_le_service_de_relance
        if maj:
            frappe.db.set_value("Customer", ligne.client, maj)
    # Issue de l'appel sur la fiche client : Perdu / réactivé / relance reportée (effet_reponse, date_de_rappel).
    if frappe.db.has_column("Customer", "custom_statut_relance"):
        issue = ligne.get("resume_appel") or ""
        statut = frappe.db.get_value("Customer", ligne.client, "custom_statut_relance") or ""
        maj = effet_reponse(issue, statut)
        if maj:
            frappe.db.set_value("Customer", ligne.client, maj)
            if issue == NE_VEUT_PLUS:
                _commenter(ligne.client, "⛔ Relance arrêtée (Perdu, Non à l’entretien) : « %s » à l’appel du %s." % (NE_VEUT_PLUS, _jj(nowdate())))
        if issue == RAPPELER and frappe.db.has_column("Customer", "custom_relance_reportee_au"):
            d = date_de_rappel(ligne.get("rappeler_le"), nowdate())
            frappe.db.set_value("Customer", ligne.client, "custom_relance_reportee_au", d)
            _commenter(ligne.client, "⏰ Relance reportée au %s (« %s » à l’appel du %s)." % (d.strftime("%d/%m/%Y"), RAPPELER, _jj(nowdate())))
    articles = articles_appeles(ligne.get("detail_articles"))
    if not articles:
        return
    aujourd_hui = nowdate()
    for row in frappe.db.sql("""select d.name, d.item_code, d.scheduled_date, d.custom_appelle, d.custom_1er_appel
                                from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                                where ms.customer = %s and ms.docstatus = 1 and d.item_code in %s""",
                             (ligne.client, tuple(articles)), as_dict=True):
        if articles.get(row.item_code) != getdate(row.scheduled_date):
            continue
        maj = {}
        if not row.custom_appelle:
            maj["custom_appelle"] = aujourd_hui
        if ligne.get("resume_appel") == NE_REPOND_PAS and not row.custom_1er_appel:
            maj["custom_1er_appel"] = aujourd_hui
        if maj:
            frappe.db.set_value("Maintenance Schedule Detail", row.name, maj, update_modified=False)


def _jj(d):
    return getdate(d).strftime("%d/%m/%Y")


def _commenter(client, contenu):
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer", "reference_name": client,
                    "content": contenu}).insert(ignore_permissions=True)
