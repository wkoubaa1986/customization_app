# -*- coding: utf-8 -*-
"""
Daily image integrity monitor for Item Groups and Items.

For every record whose `image` points to /files/ or /private/files/:
  1. If the file is missing on disk, try to find a same-prefix file on disk
     (handles stale filenames like "JS-80-F.jpg" -> "JS-80-F55d186.jpg") and
     repoint the record to it.
  2. If the image is served from /private/files/ (renders as a broken icon in
     public/portal/listing views), move the file to public/files, flip the
     File doc's is_private flag, and repoint the record.

Anything that still cannot be fixed (missing on disk with no candidate) is
collected and emailed to the configured recipient.

Scheduled daily at 03:00 via hooks.py.
"""
from __future__ import unicode_literals

import os
import shutil
from urllib.parse import unquote

import frappe

# Doctypes to monitor (all have an `image` field)
MONITORED_DOCTYPES = ("Item Group", "Item")


# ---------------------------------------------------------------------------
#  Path helpers
# ---------------------------------------------------------------------------

def _roots():
    site = frappe.get_site_path()
    return (
        os.path.realpath(os.path.join(site, "public", "files")),
        os.path.realpath(os.path.join(site, "private", "files")),
    )


def _split_url(url: str):
    """Return (kind, filename) for a /files or /private/files URL, else (None, None)."""
    p = unquote(url or "")
    if p.startswith("/private/files/"):
        return "private", p[len("/private/files/"):]
    if p.startswith("/files/"):
        return "public", p[len("/files/"):]
    return None, None


def _disk_path(kind: str, filename: str, pub_root: str, priv_root: str) -> str:
    root = priv_root if kind == "private" else pub_root
    candidate = os.path.realpath(os.path.join(root, filename))
    # Path-traversal guard: stay inside the intended root.
    if candidate == root or candidate.startswith(root + os.sep):
        return candidate
    return ""


def _find_candidate(filename: str, pool: set) -> str:
    """
    Find a file on disk whose name shares a long common prefix (same extension)
    with the requested filename. Handles hash-suffixed renames in either
    direction.
    """
    base, ext = os.path.splitext(filename)
    best, best_len = None, 0
    for f in pool:
        fb, fe = os.path.splitext(f)
        if fe.lower() != ext.lower():
            continue
        if base.startswith(fb) and len(fb) > best_len and len(fb) >= 8:
            best, best_len = f, len(fb)
        elif fb.startswith(base) and len(base) > best_len and len(base) >= 8:
            best, best_len = f, len(base)
    if best:
        return best

    # Noms courts (JS-80-F = 7 car. : sous le seuil de 8) : l'UNICITÉ remplace
    # la longueur comme garde-fou — un seul candidat possible, on le prend.
    courts = [
        f for f in pool
        if os.path.splitext(f)[1].lower() == ext.lower()
        and os.path.splitext(f)[0].startswith(base)
        and len(base) >= 5
    ]
    return courts[0] if len(courts) == 1 else None


def _candidat_via_file_doc(doctype: str, name: str, filename: str, pub_files: set) -> str:
    """Le document File sait où le fichier est parti : un re-téléversement
    renomme le fichier sur disque (suffixe aléatoire) et met à jour
    File.file_url, mais PAS les champs image qui pointaient sur l'ancien nom
    (cas réels du 25/08/2026 : L-UV.jpg → L-UV914f7e.jpg). Déterministe, donc
    prioritaire sur l'appariement par préfixe.

    Deux recherches, dans l'ordre de fiabilité :
      1. le File ATTACHÉ à CE document avec attached_to_field = image — c'est
         l'image officielle de la fiche, même si son nom a complètement changé
         (P-F-20'-34.jpg → P-F-20'.jpg : un préfixe aurait choisi la photo
         d'un AUTRE produit) ;
      2. un File portant le nom d'origine, où qu'il soit attaché.
    """
    attaches = frappe.get_all(
        "File",
        filters={"attached_to_doctype": doctype, "attached_to_name": name,
                 "attached_to_field": "image"},
        fields=["file_url"], limit=5)
    portant_le_nom = frappe.get_all(
        "File", filters={"file_name": filename}, fields=["file_url"], limit=5)
    for fdoc in attaches + portant_le_nom:
        kind, disk_name = _split_url(fdoc.file_url)
        if kind == "public" and disk_name in pub_files:
            return disk_name
    return ""


# ---------------------------------------------------------------------------
#  Fix primitives
# ---------------------------------------------------------------------------

def _repoint(doctype: str, name: str, new_url: str):
    frappe.db.set_value(doctype, name, "image", new_url, update_modified=False)


def _make_public(old_url: str, disk_name: str, priv_root: str, pub_root: str) -> str:
    """Move a private file to public, update its File docs, return new /files URL."""
    src = os.path.join(priv_root, disk_name)
    dst = os.path.join(pub_root, disk_name)
    new_url = "/files/" + disk_name

    if not os.path.isfile(dst):
        if os.path.isfile(src):
            shutil.move(src, dst)
    elif os.path.isfile(src):
        os.remove(src)

    for fdoc in frappe.get_all(
        "File",
        filters={"file_url": ["in", [old_url, "/private/files/" + disk_name]]},
        fields=["name"],
    ):
        frappe.db.set_value(
            "File", fdoc.name, {"is_private": 0, "file_url": new_url}, update_modified=False
        )
    return new_url


# ---------------------------------------------------------------------------
#  Core scan + fix
# ---------------------------------------------------------------------------

def scan_and_fix() -> dict:
    pub_root, priv_root = _roots()
    pub_files = set(os.listdir(pub_root)) if os.path.isdir(pub_root) else set()
    priv_files = set(os.listdir(priv_root)) if os.path.isdir(priv_root) else set()

    fixed, unfixable = [], []

    for doctype in MONITORED_DOCTYPES:
        rows = frappe.get_all(
            doctype, filters={"image": ["like", "/%files/%"]}, fields=["name", "image"]
        )
        for row in rows:
            kind, filename = _split_url(row.image)
            if not kind:
                continue

            disk = _disk_path(kind, filename, pub_root, priv_root)
            exists = bool(disk) and os.path.isfile(disk)

            try:
                if kind == "private":
                    # Private image -> broken in public views. Make it public.
                    disk_name = filename if filename in priv_files else _find_candidate(
                        filename, priv_files
                    )
                    if not disk_name:
                        # Maybe the file already lives in public/files.
                        pub_name = filename if filename in pub_files else _find_candidate(
                            filename, pub_files
                        )
                        if pub_name:
                            new_url = "/files/" + pub_name
                            _repoint(doctype, row.name, new_url)
                            fixed.append((doctype, row.name, row.image, new_url))
                        else:
                            unfixable.append((doctype, row.name, row.image, "missing on disk"))
                        continue
                    new_url = _make_public(row.image, disk_name, priv_root, pub_root)
                    _repoint(doctype, row.name, new_url)
                    pub_files.add(disk_name)
                    priv_files.discard(disk_name)
                    fixed.append((doctype, row.name, row.image, new_url))

                elif not exists:
                    # Public URL but file missing -> le File doc d'abord (il
                    # connaît le nom réel après re-téléversement), sinon un
                    # candidat par préfixe.
                    cand = _candidat_via_file_doc(
                        doctype, row.name, filename, pub_files
                    ) or _find_candidate(filename, pub_files)
                    if cand:
                        new_url = "/files/" + cand
                        _repoint(doctype, row.name, new_url)
                        fixed.append((doctype, row.name, row.image, new_url))
                    else:
                        unfixable.append((doctype, row.name, row.image, "missing on disk"))
                # else: public file exists -> healthy, nothing to do.
            except Exception:
                frappe.log_error(
                    f"image_monitor failed for {doctype} {row.name}", frappe.get_traceback()
                )
                unfixable.append((doctype, row.name, row.image, "error during fix"))

    if fixed:
        frappe.db.commit()
        frappe.clear_cache()

    return {"fixed": fixed, "unfixable": unfixable}


# ---------------------------------------------------------------------------
#  Reporting
# ---------------------------------------------------------------------------

def _recipient() -> str:
    return frappe.get_hooks("app_email") and frappe.get_hooks("app_email")[0] or None


def _rows_html(rows, with_target=False) -> str:
    cells = []
    for r in rows:
        if with_target:
            doctype, name, old, new = r
            cells.append(
                f"<tr><td>{doctype}</td><td>{frappe.utils.escape_html(name)}</td>"
                f"<td>{frappe.utils.escape_html(old)}</td>"
                f"<td>{frappe.utils.escape_html(new)}</td></tr>"
            )
        else:
            doctype, name, old, reason = r
            cells.append(
                f"<tr><td>{doctype}</td><td>{frappe.utils.escape_html(name)}</td>"
                f"<td>{frappe.utils.escape_html(old)}</td>"
                f"<td>{frappe.utils.escape_html(reason)}</td></tr>"
            )
    return "".join(cells)


def _send_report(result: dict):
    recipient = _recipient()
    if not recipient:
        return

    fixed, unfixable = result["fixed"], result["unfixable"]

    # Only email when something needs human attention.
    if not unfixable:
        return

    msg = [f"<h3>Image monitor — {frappe.utils.nowdate()}</h3>"]
    msg.append(
        f"<p><b>{len(fixed)}</b> image(s) auto-corrigée(s), "
        f"<b>{len(unfixable)}</b> non résolue(s).</p>"
    )

    msg.append("<h4 style='color:#c0392b'>Images NON résolues (action requise)</h4>")
    msg.append(
        "<table border='1' cellpadding='6' cellspacing='0' "
        "style='border-collapse:collapse'>"
        "<tr><th>Doctype</th><th>Enregistrement</th><th>Image manquante</th><th>Raison</th></tr>"
        + _rows_html(unfixable)
        + "</table>"
    )

    if fixed:
        msg.append("<h4 style='color:#27ae60'>Images corrigées automatiquement</h4>")
        msg.append(
            "<table border='1' cellpadding='6' cellspacing='0' "
            "style='border-collapse:collapse'>"
            "<tr><th>Doctype</th><th>Enregistrement</th><th>Ancienne</th><th>Nouvelle</th></tr>"
            + _rows_html(fixed, with_target=True)
            + "</table>"
        )

    frappe.sendmail(
        recipients=[recipient],
        subject=f"[ERPNext] Images non résolues : {len(unfixable)} à corriger",
        message="".join(msg),
    )


# ---------------------------------------------------------------------------
#  Scheduler entrypoint
# ---------------------------------------------------------------------------

def run_cron():
    """Daily at 03:00 — scan, auto-fix, and email any unresolved image issues."""
    try:
        result = scan_and_fix()
        _send_report(result)
        frappe.logger("image_monitor").info(
            f"image_monitor: fixed={len(result['fixed'])} unfixable={len(result['unfixable'])}"
        )
    except Exception:
        tb = frappe.get_traceback()
        frappe.log_error(tb, "image_monitor.run_cron")
        recipient = _recipient()
        if recipient:
            frappe.sendmail(
                recipients=[recipient],
                subject="[ERPNext] Erreur dans le job image_monitor",
                message=f"<pre>{frappe.as_unicode(tb)}</pre>",
            )
