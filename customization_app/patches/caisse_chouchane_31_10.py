"""Caisse de Mohamed Hedi Chouchane du 31/10/2026 marquée comptée et collectée — demande de l'utilisateur du 25/11/2026
(« marque-la comme comptée et collectée, de toute façon il n'y a rien »).

Ce jour-là : une commande (Attia Mahmoud) payée d'avance, rien en espèces (ni encaissé, ni dépensé, fond 0 avant et
après). Jamais comptée, et plus comptable depuis l'écran : la règle « compter dans l'ordre » (25/11/2026) refuse un jour
antérieur à un jour déjà compté (07/11, CLO-1450), et l'employé est parti le 10/11.

Le patch crée la clôture à zéro, validée directement au nom de Wassim Koubaa, comme le ferait la « validation
directe » de la collecte. Il vérifie d'abord, en prod, qu'il n'y a vraiment rien ce jour-là : sinon il abandonne sans
rien toucher. Il ne fait jamais échouer la migration ; compte rendu : journal des erreurs « Caisse Chouchane 31/10 ».
"""
import frappe
from frappe.utils import flt, now_datetime

CAISSE = "Mohamed Hedi Chouchane"
JOUR = "2026-10-31"
PAR = "koubaawassim@gmail.com"
TITRE = "Caisse Chouchane 31/10"
NOTE = ("Employé parti le 10/11/2026. Journée sans espèces (commande Attia Mahmoud payée d'avance) : marquée comptée "
        "et collectée à la demande de Wassim Koubaa le 25/11/2026 (patch caisse_chouchane_31_10).")


def execute():
    frappe.db.savepoint("chouchane_31_10")
    try:
        texte = _executer()
    except Exception:
        texte = "ÉCHEC — rien n'a été changé : " + frappe.get_traceback().strip().splitlines()[-1]
        try:
            frappe.db.rollback(save_point="chouchane_31_10")
        except Exception:
            texte += " (retour arrière impossible : état à vérifier)"
    try:
        print(TITRE + " — " + texte)
        frappe.log_error(title=TITRE, message=texte)
        frappe.db.commit()
    except Exception:
        pass


def _executer():
    if not frappe.db.exists("Employee", {"employee_name": CAISSE}):
        return "caisse inconnue sur ce site : rien à faire"
    existante = frappe.db.get_value("Cloture Caisse", {"caisse": CAISSE, "date_cloture": JOUR, "docstatus": ["<", 2]},
                                    ["name", "docstatus", "statut"], as_dict=True)
    if existante:
        return "déjà une clôture pour ce jour (%s, %s) : laissée telle quelle" % (existante.name, existante.statut)
    from customization_app import caisse_cloture as CL
    from customization_app import caisse_collecte as CC
    avant = frappe.flags.get("caisse_vue_complete")
    frappe.flags.caisse_vue_complete = True
    try:
        m = CL._mesures(CAISSE, JOUR)
    finally:
        frappe.flags.caisse_vue_complete = avant
    ouverture = flt(CL._ouverture(CAISSE, JOUR), 3)
    chiffres = {"ouverture": ouverture, "encaissé en espèces": m["encaissements_especes"],
                "dépensé en espèces": m["depenses_especes"], "chèques": m["total_cheques"],
                "autres modes": m["total_autres_modes"]}
    pas_rien = {k: v for k, v in chiffres.items() if abs(flt(v)) > 0.0005}
    if pas_rien:
        return "ABANDON — rien n'a été changé : ce jour-là n'est pas vide (%s) ; à compter réellement." % ", ".join(
            "%s %s DT" % (k, CL._fmt_montant(v)) for k, v in pas_rien.items())
    qui = PAR if frappe.db.exists("User", PAR) else "Administrator"
    maintenant = now_datetime()
    doc = frappe.get_doc({
        "doctype": "Cloture Caisse", "caisse": CAISSE, "date_cloture": JOUR, "valide_par": qui, "compte_le": maintenant,
        "statut": CC.STATUT_A_COLLECTER, "especes_comptees": 0.0, "especes_remises": 0.0, "fond_conserve": 0.0,
        "solde_ouverture": 0.0, "encaissements_especes": 0.0, "depenses_especes": 0.0, "solde_theorique": 0.0,
        "ecart": 0.0, "total_cheques": 0.0, "total_autres_modes": 0.0, "nb_cheques_remis": 0, "nb_traites_remis": 0,
        "note": NOTE, "controles": "",
        "echanges": "[%s] ✅ %s — %s" % (str(maintenant)[:16], frappe.utils.get_fullname(qui), NOTE),
    })
    doc.insert(ignore_permissions=True)
    # La « validation directe » du titulaire (caisse_collecte.collecter) : une seule signature, tracée comme telle.
    doc.validation_seule = 1
    doc.statut = CC.STATUT_VALIDEE
    doc.flags.collecte_ok = True
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    doc.submit()
    doc.db_set("collecte_par", qui, update_modified=False)
    doc.db_set("collecte_le", maintenant, update_modified=False)
    pdf = "PDF joint"
    try:
        from frappe.utils.file_manager import save_file
        from frappe.utils.pdf import get_pdf
        contenu = get_pdf(CL._html_instantane(doc, m["data"], None), dict(CL.OPTIONS_PDF))
        save_file("caisse-%s-%s.pdf" % (CAISSE.replace(" ", "_"), JOUR), contenu, "Cloture Caisse", doc.name, is_private=1)
    except Exception:
        pdf = "PDF non joint (%s)" % frappe.get_traceback().strip().splitlines()[-1]
    return "%s créée : comptée 0, collectée par %s (validation directe) ; %s" % (doc.name, frappe.utils.get_fullname(qui), pdf)
