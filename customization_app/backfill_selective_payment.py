"""
Backfill one-time pour la regeneration selective des paiements.

Tague chaque Payment Entry / Journal Entry (Avoir) existant avec l'uid de sa
ligne de payment_schedule source + une empreinte, et remplit custom_row_uid sur
les lignes. A lancer UNE FOIS par site, APRES l'import des custom fields
(fixtures) et AVANT que les utilisateurs editent des Sales Order soumises.

Idempotent (saute ce qui est deja tague).

Usage:
    bench --site <site> execute customization_app.backfill_selective_payment.backfill
"""
import frappe

def row_fp_from_vals(amount, mode, banque, chq, due):
    src = "|".join([
        "%.3f" % frappe.utils.flt(amount, 3),
        mode or "",
        banque or "",
        chq or "",
        str(frappe.utils.getdate(due)) if due else "",
    ])
    return frappe.utils.sha256_hash(src)

def backfill(limit=None, commit_every=200, verbose=False):
    # SOs (submitted) that have at least one PE referencing them
    so_names = frappe.db.sql_list("""
        SELECT DISTINCT per.reference_name
        FROM `tabPayment Entry Reference` per
        JOIN `tabPayment Entry` pe ON pe.name = per.parent
        WHERE per.reference_doctype='Sales Order' AND pe.docstatus=1
    """)
    # plus SOs referenced by Journal Entry Account (Avoir)
    je_so = frappe.db.sql_list("""
        SELECT DISTINCT reference_name FROM `tabJournal Entry Account`
        WHERE reference_type='Sales Order' AND reference_name IS NOT NULL
    """)
    allso = list(set(so_names) | set(je_so))
    allso = [s for s in allso if s]
    if limit:
        allso = allso[:limit]
    total = len(allso)
    stats = {"so": 0, "rows_uid": 0, "pe_tagged": 0, "je_tagged": 0, "pe_unmatched": 0}
    i = 0
    for soname in allso:
        i += 1
        if not frappe.db.exists("Sales Order", soname):
            continue
        so = frappe.get_doc("Sales Order", soname)
        # 1) fill row uids
        row_by_uid = {}
        for r in so.payment_schedule:
            if not r.custom_row_uid:
                uid = frappe.utils.generate_hash(length=12)
                frappe.db.set_value("Payment Schedule", r.name, "custom_row_uid", uid, update_modified=False)
                r.custom_row_uid = uid
                stats["rows_uid"] += 1
            row_by_uid[r.custom_row_uid] = r
        # 2) match PEs (ordered by creation) to rows
        pes = frappe.get_all("Payment Entry",
            filters={"reference_doctype": "Sales Order", "reference_name": soname, "docstatus": 1},
            fields=["name", "mode_of_payment", "paid_amount", "custom_source_row_uid"],
            order_by="`tabPayment Entry`.creation asc")
        claimed = set()
        for pe in pes:
            if pe.custom_source_row_uid:
                claimed.add(pe.custom_source_row_uid)
        for pe in pes:
            if pe.custom_source_row_uid:
                continue
            match = None
            # by mode + amount
            for r in so.payment_schedule:
                if r.custom_row_uid in claimed:
                    continue
                if r.mode_of_payment == pe.mode_of_payment and \
                   round(frappe.utils.flt(r.payment_amount, 3), 3) == round(frappe.utils.flt(pe.paid_amount, 3), 3):
                    match = r
                    break
            if not match:
                # fallback: amount only (encashment changed mode)
                for r in so.payment_schedule:
                    if r.custom_row_uid in claimed:
                        continue
                    if round(frappe.utils.flt(r.payment_amount, 3), 3) == round(frappe.utils.flt(pe.paid_amount, 3), 3):
                        match = r
                        break
            if match:
                claimed.add(match.custom_row_uid)
                fp = row_fp_from_vals(match.payment_amount, match.mode_of_payment,
                                      match.get("custom_banque"), match.get("custom__n_chèque__transaction"),
                                      match.due_date)
                frappe.db.set_value("Payment Entry", pe.name,
                    {"custom_source_row_uid": match.custom_row_uid, "custom_source_fingerprint": fp,
                     "custom_source_sales_order": soname},
                    update_modified=False)
                stats["pe_tagged"] += 1
            else:
                stats["pe_unmatched"] += 1
        # 3) match Avoir JEs (by amount) to Avoir rows
        je_parents = frappe.db.sql_list("""
            SELECT DISTINCT parent FROM `tabJournal Entry Account`
            WHERE reference_type='Sales Order' AND reference_name=%s
        """, soname)
        for jn in je_parents:
            jd = frappe.db.get_value("Journal Entry", jn,
                ["name", "docstatus", "custom_source_row_uid", "total_debit"], as_dict=True)
            if not jd or jd.docstatus != 1 or jd.custom_source_row_uid:
                continue
            match = None
            for r in so.payment_schedule:
                if r.mode_of_payment == "Avoir client" and r.custom_row_uid not in claimed and \
                   round(frappe.utils.flt(r.payment_amount, 3), 3) == round(frappe.utils.flt(jd.total_debit, 3), 3):
                    match = r
                    break
            if match:
                claimed.add(match.custom_row_uid)
                fp = row_fp_from_vals(match.payment_amount, match.mode_of_payment,
                                      match.get("custom_banque"), match.get("custom__n_chèque__transaction"),
                                      match.due_date)
                frappe.db.set_value("Journal Entry", jd.name,
                    {"custom_source_row_uid": match.custom_row_uid, "custom_source_fingerprint": fp,
                     "custom_source_sales_order": soname},
                    update_modified=False)
                stats["je_tagged"] += 1
        stats["so"] += 1
        if i % commit_every == 0:
            frappe.db.commit()
            print("... progress %d/%d  %s" % (i, total, stats))
    frappe.db.commit()

    # Final pass: ensure custom_source_sales_order is set on every tagged PE/JE
    # (catches entries whose Payment Entry Reference was already moved to an invoice).
    uid2so = {}
    for r in frappe.db.sql("""SELECT parent, custom_row_uid FROM `tabPayment Schedule`
            WHERE parenttype='Sales Order' AND custom_row_uid IS NOT NULL AND custom_row_uid!=''""", as_dict=True):
        uid2so[r.custom_row_uid] = r.parent
    for dt in ("Payment Entry", "Journal Entry"):
        recs = frappe.db.sql("""SELECT name, custom_source_row_uid FROM `tab%s`
            WHERE docstatus=1 AND custom_source_row_uid IS NOT NULL AND custom_source_row_uid!=''
            AND (custom_source_sales_order IS NULL OR custom_source_sales_order='')""" % dt, as_dict=True)
        for rec in recs:
            so_name = uid2so.get(rec.custom_source_row_uid)
            if so_name:
                frappe.db.set_value(dt, rec.name, "custom_source_sales_order", so_name, update_modified=False)
                stats["source_so_pass"] = stats.get("source_so_pass", 0) + 1
    frappe.db.commit()
    print("BACKFILL DONE total_so=%d  %s" % (total, stats))
    return stats
