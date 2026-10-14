"""Approbation finale d'une « Liste Commande Import » (14/10/2026).

Règle décidée par l'utilisateur : partout où le fournisseur a donné un prix, on prend SON prix et SA
quantité ; une ligne sans prix du fournisseur reste telle quelle. Les prix contre-proposés
(`prix_cible_negocie`) sont donc retirés — c'est voulu : à l'approbation finale, son dernier mot fait foi.
Les lignes approuvées passent « Accepté » ; la liste reste ouverte (statut inchangé) pour un ajustement.

Deux temps : `approbation_finale(appliquer=0)` rend l'aperçu (rien n'est écrit), `appliquer=1` l'applique,
enregistre et laisse une trace dans le fil du document (anciens prix et quantités).
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from customization_app.liste_commande_import import DOCTYPE, _guard
from customization_app.lci_observation import TOL, TOL_PRIX, prix_retenu, qty_retenue
from customization_app.lci_conteneurs import ajuster_parts  # noqa: F401 — réexporté (tests, appels existants)

ACCEPTE = "Accepté"
ABANDONNE = "Abandonné"


def _get(row, champ):
    return row.get(champ) if hasattr(row, "get") else getattr(row, champ, None)


def plan_approbation(rows):
    """Ce que l'approbation changerait, ligne par ligne. PURE (dicts ou Documents).

    Rend {"lignes": [...changées...], "sans_prix": [...], "abandonnees": n, "deja_ok": n,
          "montant_avant", "montant_apres", "volume_avant", "volume_apres"}."""
    lignes, sans_prix = [], []
    abandonnees = deja_ok = 0
    montant_avant = montant_apres = volume_avant = volume_apres = 0.0
    for r in rows:
        decision = _get(r, "decision") or ""
        q_ret, p_ret = qty_retenue(r), prix_retenu(r)
        vol = flt(_get(r, "volume_ligne_m3"))
        if decision == ABANDONNE:
            abandonnees += 1
            continue
        montant_avant += q_ret * p_ret
        volume_avant += vol
        p_frn, q_frn = flt(_get(r, "prix_fournisseur")), flt(_get(r, "qty_fournisseur"))
        libelle = {"row": _get(r, "name"), "idx": _get(r, "idx"),
                   "code": _get(r, "item_code") or _get(r, "item_name") or ""}
        if p_frn <= 0:
            sans_prix.append(dict(libelle, qty=q_ret, prix=p_ret))
            montant_apres += q_ret * p_ret
            volume_apres += vol
            continue
        q_new = q_frn if q_frn > 0 else q_ret
        change = dict(libelle)
        if abs(p_ret - p_frn) > TOL_PRIX:
            change.update(prix_de=p_ret, prix_a=p_frn)
        if abs(q_new - q_ret) > TOL:
            change.update(qty_de=q_ret, qty_a=q_new)
        if decision != ACCEPTE:
            change.update(decision_de=decision)
        # le prix négocié est retiré même s'il égale le sien : plus rien n'est « en négociation »
        if flt(_get(r, "prix_cible_negocie")) > 0:
            change["retirer_negocie"] = True
        vol_new = vol * q_new / q_ret if q_ret else vol
        montant_apres += q_new * p_frn
        volume_apres += vol_new
        if any(k in change for k in ("prix_de", "qty_de", "decision_de", "retirer_negocie")):
            change.update(montant_de=q_ret * p_ret, montant_a=q_new * p_frn)
            lignes.append(change)
        else:
            deja_ok += 1
    return {"lignes": lignes, "sans_prix": sans_prix, "abandonnees": abandonnees, "deja_ok": deja_ok,
            "montant_avant": round(montant_avant, 4), "montant_apres": round(montant_apres, 4),
            "volume_avant": round(volume_avant, 4), "volume_apres": round(volume_apres, 4)}


def appliquer_plan(doc, plan):
    """Pose prix, quantités et décision sur les lignes du document (sans enregistrer)."""
    par_nom = {r.name: r for r in doc.articles}
    for c in plan["lignes"]:
        r = par_nom.get(c["row"])
        if not r:
            continue
        if c.get("retirer_negocie") or "prix_a" in c:
            r.prix_cible_negocie = 0
        if "qty_a" in c:
            r.qty_cible = c["qty_a"]
            # déjà réparti en conteneurs : le plan suit (sinon 1 pièce resterait à quai sans que personne le voie)
            try:
                parts = json.loads(r.repartition_conteneurs or "[]") or []
            except Exception:
                parts = []
            if parts:
                r.repartition_conteneurs = json.dumps(ajuster_parts(parts, c["qty_de"], c["qty_a"]))
        r.decision = ACCEPTE


def _n(v, dec=2):
    return ("{:,.%df}" % dec).format(flt(v)).replace(",", " ")


def texte_trace(plan, devise):
    """Le commentaire laissé dans le fil du document : ce qui a été remplacé. PURE."""
    prix = [c for c in plan["lignes"] if "prix_de" in c]
    qtes = [c for c in plan["lignes"] if "qty_de" in c]
    lignes = ["✅ Approbation finale : prix et quantités du fournisseur retenus sur %d ligne(s)."
              % (len(plan["lignes"]) + plan["deja_ok"])]
    if prix:
        lignes.append("Prix remplacés : " + " ; ".join(
            "#%s %s %s → %s" % (c["idx"], c["code"], _n(c["prix_de"], 4).rstrip("0").rstrip("."),
                                _n(c["prix_a"], 4).rstrip("0").rstrip(".")) for c in prix))
    if qtes:
        lignes.append("Quantités remplacées : " + " ; ".join(
            "#%s %s %s → %s" % (c["idx"], c["code"], _n(c["qty_de"], 0), _n(c["qty_a"], 0)) for c in qtes))
    if plan["sans_prix"]:
        lignes.append("Sans prix du fournisseur, laissées telles quelles : " + " ; ".join(
            "#%s %s" % (c["idx"], c["code"]) for c in plan["sans_prix"]))
    lignes.append("Montant retenu : %s → %s %s." % (_n(plan["montant_avant"]), _n(plan["montant_apres"]), devise))
    return "\n".join(lignes)


@frappe.whitelist()
def approbation_finale(docname, appliquer=0):
    """Aperçu (appliquer=0) ou application (appliquer=1) de l'approbation finale."""
    _guard()
    doc = frappe.get_doc(DOCTYPE, docname)
    plan = plan_approbation(doc.articles)
    plan["devise"] = doc.devise or "USD"
    if not frappe.utils.cint(appliquer):
        return plan
    if not plan["lignes"]:
        frappe.throw(_("Rien à approuver : toutes les lignes cotées portent déjà le prix et la quantité du fournisseur."))
    appliquer_plan(doc, plan)
    doc.save()
    doc.add_comment("Comment", texte_trace(plan, plan["devise"]).replace("\n", "<br>"))
    frappe.db.commit()
    plan.update(montant_retenu=doc.montant_retenu, volume_total_m3=doc.volume_total_m3)
    return plan
