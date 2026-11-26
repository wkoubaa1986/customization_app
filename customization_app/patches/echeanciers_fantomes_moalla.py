"""Deux échéanciers FANTÔMES supprimés (analyse « cartographie entretien » du 25/11/2026, son accord du même jour).

MAT-MSH-2026-00796 et MAT-MSH-2026-00799 (cliente Khadija Moalla, AP-M-AJ-7-MA-SM) sont nés le 20/08/2026 à 01:15 de
deux commandes, SAL-ORD-2026-03004 et SAL-ORD-2026-03007, SUPPRIMÉES quelques secondes avant. Ils sont restés validés
avec un statut « Draft » incohérent (les seuls des 3 263) : les SMS et les listes d'appel les ignorent aujourd'hui, mais le
passage de nuit les traite comme des échéanciers actifs (un achat peut y solder une visite) et ils redeviendraient
relançables au premier enregistrement — trois relances pour une machine à partir du 13/02/2027. La vraie machine de la
cliente est suivie par MAT-MSH-2026-00841 (SAL-ORD-2026-03062).

Prudent : chaque échéancier n'est supprimé que si TOUT est encore vrai — validé, cette seule ligne machine liée à cette
commande, commande toujours absente, aucune visite réalisée / relancée / appelée, cité par aucune ligne d'appel, et
l'échéancier de la vraie machine toujours là. Sinon il est laissé et le compte rendu dit pourquoi. Rejouable sans effet.
Annulation puis suppression (comme les devinettes) : la trace reste dans « Deleted Document »."""
import frappe

FANTOMES = {"MAT-MSH-2026-00796": "SAL-ORD-2026-03004", "MAT-MSH-2026-00799": "SAL-ORD-2026-03007"}
VRAI = "MAT-MSH-2026-00841"
CLIENTE = "Khadija Moalla"
RELANCE = ("actual_date", "custom_sms_1", "custom_sms_2", "custom_appelle", "custom_1er_appel", "custom_sales_order")


def motif_de_garder(ms, commande, vrai) -> str:
    """Vide si l'échéancier peut être supprimé, sinon la raison de le garder. PURE. `ms` : le document ; `commande` :
    {name, existe, lignes_appel} ; `vrai` : le nom de l'échéancier de la vraie machine s'il est toujours là."""
    if ms.docstatus != 1:
        return "pas validé (docstatus %s)" % ms.docstatus
    if ms.customer != CLIENTE:
        return "autre client (%s)" % ms.customer
    if [(i.item_code, i.sales_order) for i in ms.items] != [("AP-M-AJ-7-MA-SM", commande["name"])]:
        return "lignes machine inattendues"
    if commande.get("existe"):
        return "la commande %s existe de nouveau" % commande["name"]
    if any(getattr(r, c, None) for r in ms.schedules for c in RELANCE):
        return "une visite est réalisée, relancée ou appelée"
    if commande.get("lignes_appel"):
        return "cité par %s ligne(s) d'appel" % commande["lignes_appel"]
    if not vrai:
        return "l'échéancier de la vraie machine (%s) n'est plus là" % VRAI
    return ""


def execute():
    compte_rendu = []
    vrai = frappe.db.get_value("Maintenance Schedule", {"name": VRAI, "docstatus": 1, "customer": CLIENTE}, "name")
    for nom, commande in FANTOMES.items():
        if not frappe.db.exists("Maintenance Schedule", nom):
            compte_rendu.append("%s : déjà absent" % nom)
            continue
        ms = frappe.get_doc("Maintenance Schedule", nom)
        lignes_appel = frappe.db.count("Appelle Client", {"échéancier_dentretien": nom}) \
            if frappe.db.has_column("Appelle Client", "échéancier_dentretien") else 0
        motif = motif_de_garder(ms, {"name": commande, "existe": bool(frappe.db.exists("Sales Order", commande)),
                                     "lignes_appel": lignes_appel}, vrai)
        if motif:
            compte_rendu.append("%s : GARDÉ — %s" % (nom, motif))
            continue
        ms.flags.ignore_links = ms.flags.ignore_permissions = True
        ms.cancel()
        frappe.delete_doc("Maintenance Schedule", nom, force=True, ignore_permissions=True)
        compte_rendu.append("%s : supprimé (commande %s supprimée le 20/08/2026)" % (nom, commande))
    if frappe.db.exists("Customer", CLIENTE) and any("supprimé" in l for l in compte_rendu):
        frappe.get_doc("Customer", CLIENTE).add_comment(
            "Comment", "🧹 Échéanciers fantômes retirés le %s : %s. Sa machine reste suivie par %s."
            % (frappe.utils.formatdate(frappe.utils.nowdate()), " ; ".join(compte_rendu), VRAI))
    frappe.log_error(title="Échéanciers fantômes (Khadija Moalla)", message="\n".join(compte_rendu))
    return compte_rendu
