"""Éteint le Server Script « cancel sales order payment » (Sales Order, Before Cancel), repris dans l'app le 26/11/2026.

Le script tournait APRÈS le hook Python `annulation_commande.before_cancel_sales_order` (Document.run_method : hooks des
apps, puis Server Scripts). Ses quatre boucles :
  - paiements validés de la commande annulés puis supprimés      → repris À L'IDENTIQUE (_supprimer_paiements_et_ecritures)
  - écritures (Journal Entry) liées annulées puis supprimées      → repris À L'IDENTIQUE, même ordre (après les paiements)
  - BL liés annulés                                               → l'app les annulait ET supprimait déjà avant lui
  - échéanciers liés annulés puis SUPPRIMÉS                       → l'app les ANNULE désormais, sans jamais les supprimer
Laissé actif, il ne ferait rien de plus (tout est déjà fait quand il passe : un échéancier annulé n'est pas « validé »,
sa boucle l'ignore) ; éteint, il n'y a plus qu'un seul mécanisme, versionné et testé.

PRUDENT : il n'est éteint que si son code en base est TOUJOURS celui qui a été repris (copie lue sur la copie de prod du
25/11/2026, modifié en base le 16/05/2025) — comparaison sans les commentaires ni les espaces. Si quelqu'un l'a modifié
depuis, il est laissé actif et le compte rendu le dit : à relire avant de l'éteindre à la main. DÉSACTIVÉ, pas supprimé :
son code reste consultable, le geste se défait d'une case à cocher. Rejouable sans effet (déjà éteint → rien).
Même précédent que `desactiver_server_scripts_annulation_facture`."""
import frappe

NOM = "cancel sales order payment"

# Le code repris, tel que lu en base le 25/11/2026 (2 147 caractères).
REPRIS = r"""frappe.flags.ignore_permissions = True
payment_entries = frappe.get_all("Payment Entry", filters={"reference_doctype": "Sales Order", "reference_name": doc.name, "docstatus": 1})
for pe in payment_entries:
    payment_entry_doc = frappe.get_doc("Payment Entry", pe.name)
    payment_entry_doc.cancel()
    frappe.delete_doc("Payment Entry", payment_entry_doc.name, ignore_permissions=True)


# 🔹 Supprimer les Journal Entry liés à la Sales Order
journal_entries = frappe.get_all(
    "Journal Entry Account",
    filters={
        "reference_type": "Sales Order",
        "reference_name": doc.name
    },
    fields=["parent"]
)

# Utiliser set() pour éviter les doublons
je_names = set(je.parent for je in journal_entries)

for je_name in je_names:
    journal_entry_doc = frappe.get_doc("Journal Entry", je_name)
    if journal_entry_doc.docstatus == 1:
        journal_entry_doc.cancel()
    frappe.delete_doc("Journal Entry", je_name, ignore_permissions=True)

linked_items = frappe.get_all('Delivery Note Item', filters={'against_sales_order': doc.name}, fields=['parent'])
linked_delivery_notes = {item.parent for item in linked_items}


for dn_name in linked_delivery_notes:
    if frappe.db.exists("Delivery Note", dn_name):
        dn = frappe.get_doc("Delivery Note", dn_name)
        # Check if the Delivery Note is submitted and then cancel it
        if dn.docstatus == 1:
            dn.cancel()


linked_items = frappe.get_all('Maintenance Schedule Item', filters={'sales_order': doc.name}, fields=['parent'])
#linked_items = frappe.get_all('Delivery Note Item', filters={'against_sales_order': doc.name}, fields=['parent'])
linked_maintenance = {item.parent for item in linked_items}
frappe.flags.ignore_permissions = True
for dn_name in linked_maintenance:
    if frappe.db.exists("Maintenance Schedule", dn_name):
        dn = frappe.get_doc("Maintenance Schedule", dn_name)
        # Check if the Delivery Note is submitted and then cancel it
        if dn.docstatus == 1:
            dn.cancel()
            frappe.delete_doc("Maintenance Schedule", dn.name, ignore_permissions=True)

frappe.flags.ignore_permissions = False

"""


def normaliser(code: str) -> str:
    """Le code sans ses lignes de commentaire ni ses espaces : une indentation ou une ligne vide changée ne compte pas,
    une instruction changée, si. PURE."""
    lignes = [l for l in (code or "").replace("\r\n", "\n").split("\n") if not l.strip().startswith("#")]
    return "".join("".join(l.split()) for l in lignes)


def decision(ss) -> str:
    """Ce que le patch doit faire du script lu en base : « absent », « deja_eteint », « eteindre » ou « modifie ». PURE
    (`ss` : dict {disabled, script, reference_doctype, doctype_event} ou None)."""
    if not ss:
        return "absent"
    if ss.get("disabled"):
        return "deja_eteint"
    if ss.get("reference_doctype") != "Sales Order" or ss.get("doctype_event") != "Before Cancel" \
            or normaliser(ss.get("script")) != normaliser(REPRIS):
        return "modifie"
    return "eteindre"


def execute():
    ss = frappe.db.get_value("Server Script", NOM, ["disabled", "script", "reference_doctype", "doctype_event",
                                                    "modified"], as_dict=True)
    quoi = decision(ss)
    if quoi == "eteindre":
        frappe.db.set_value("Server Script", NOM, "disabled", 1)
        # La carte des Server Scripts est en cache : sans ce vidage, l'ancien continuerait de tourner jusqu'au
        # prochain vidage du cache.
        frappe.cache.delete_value("server_script_map")
        texte = "« %s » éteint : son code est repris par customization_app.annulation_commande." % NOM
    elif quoi == "modifie":
        texte = ("« %s » LAISSÉ ACTIF : son code en base a changé depuis la reprise (modifié le %s ; sans effet s'il ne fait "
                 "rien d'autre que l'ancien code : il passe après l'app, qui a déjà tout fait) ; à relire avant de l'éteindre "
                 "à la main."
                 % (NOM, ss.get("modified")))
    elif quoi == "deja_eteint":
        texte = "« %s » déjà éteint : rien à faire." % NOM
    else:
        texte = "« %s » absent : rien à faire." % NOM
    if quoi in ("eteindre", "modifie"):
        frappe.log_error(title="Reprise du Server Script « %s »" % NOM, message=texte)
    print(texte)
    return {"decision": quoi, "texte": texte}
