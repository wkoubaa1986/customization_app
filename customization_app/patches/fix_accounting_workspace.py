"""
Remove workspace link rows that reference non-existent doctypes
(Tax Detail, KSA VAT, KSA VAT Setting) to fix the Accounting workspace
save error: "Impossible de trouver Ligne #N: Lié à: ...".
"""

INVALID_LINK_TOS = {"Tax Detail", "KSA VAT", "KSA VAT Setting"}


def execute():
    import frappe

    if not frappe.db.exists("Workspace", "Accounting"):
        return

    doc = frappe.get_doc("Workspace", "Accounting")
    before = len(doc.links)
    doc.links = [row for row in doc.links if row.link_to not in INVALID_LINK_TOS]
    after = len(doc.links)

    if before != after:
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"[fix_accounting_workspace] removed {before - after} invalid link row(s) from Accounting workspace.")
    else:
        print("[fix_accounting_workspace] nothing to remove.")
