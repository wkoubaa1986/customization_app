"""Le Server Script « Update Schedule based on Real Date » (Maintenance Schedule, Before Save d'un document soumis) est
remplacé par le hook Python customization_app.Maintenance.echeancier_hooks.before_update_after_submit (26/11/2026,
proposition 8 de l'analyse « cartographie entretien » : plus d'effet invisible hors de l'application).

Il n'existait qu'en base (absent de fixtures/server_script.json : le migrate ne le réactivera pas). Mesuré sur la copie
de prod du 25/11/2026 : il ne change aucune date aujourd'hui (aucune visite réalisée n'a une date prévue différente de
sa date réelle) ; sa règle, reprise par article et dans l'ordre des dates, vit maintenant dans echeancier_hooks.

Prudent : le script n'est DÉSACTIVÉ (jamais supprimé) que si son code, son doctype et son événement sont encore ceux
lus le 26/11/2026 — quelqu'un qui l'aurait modifié depuis l'a fait exprès, il est alors laissé actif et le compte
rendu le dit (les deux ensemble ne décalent pas deux fois : la reprise aligne d'abord, le script ne voit plus d'écart)
— ET si le hook qui le remplace est bien branché dans hooks.py (sinon on garderait un échéancier sans recalage).
Rejouable sans effet. → compte rendu (aussi dans le Journal des erreurs)."""
import frappe

NOM = "Update Schedule based on Real Date"
DOCTYPE = "Maintenance Schedule"
EVENEMENT = "Before Save (Submitted Document)"
HOOK = "customization_app.Maintenance.echeancier_hooks.before_update_after_submit"
CODE_ATTENDU = """article=''
for isch in doc.schedules:
    if isch.item_code != article:
        article=isch.item_code
        shift_days=0
    if isch.actual_date:
        shift_days=frappe.utils.date_diff(isch.actual_date,isch.scheduled_date)
    isch.scheduled_date=frappe.utils.add_days(isch.scheduled_date,shift_days)"""


def normaliser(code) -> str:
    """Le code sans fins de ligne Windows, espaces de fin de ligne ni lignes vides. PURE."""
    lignes = (code or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(l.rstrip() for l in lignes if l.strip())


def hook_branche(doc_events) -> bool:
    """Le hook de remplacement est-il déclaré pour Maintenance Schedule / before_update_after_submit ? PURE."""
    h = ((doc_events or {}).get(DOCTYPE) or {}).get("before_update_after_submit")
    return HOOK in (h if isinstance(h, (list, tuple)) else [h])


def motif_de_garder(script, branche=True) -> str:
    """Vide si le script peut être désactivé, sinon pourquoi on le laisse. PURE. `script` : dict ou None ; `branche` :
    le hook de remplacement est déclaré."""
    if not script:
        return "absent de la base"
    if int(script.get("disabled") or 0):
        return "déjà désactivé"
    if script.get("reference_doctype") != DOCTYPE or script.get("doctype_event") != EVENEMENT:
        return "doctype / événement modifiés (%s / %s)" % (script.get("reference_doctype"), script.get("doctype_event"))
    if normaliser(script.get("script")) != normaliser(CODE_ATTENDU):
        return "code modifié depuis le 26/11/2026 : laissé actif, à relire"
    if not branche:
        return "le hook %s n'est pas branché dans hooks.py : laissé actif" % HOOK
    return ""


def execute():
    script = frappe.db.get_value("Server Script", NOM, ["name", "script", "reference_doctype", "doctype_event", "disabled"],
                                 as_dict=True)
    motif = motif_de_garder(script, hook_branche(frappe.get_hooks("doc_events")))
    if motif:
        compte_rendu = "%s : rien fait — %s." % (NOM, motif)
    else:
        frappe.db.set_value("Server Script", NOM, "disabled", 1)
        frappe.clear_cache()
        compte_rendu = ("%s : désactivé (code inchangé). Le recalage sur la date réelle est fait par "
                        "customization_app.Maintenance.echeancier_hooks.before_update_after_submit." % NOM)
    frappe.log_error(title="Recalage date réelle : Server Script repris dans l'application", message=compte_rendu)
    return compte_rendu
