"""Les bons de livraison VALIDÉS sortis moins chers que leur commande par réapplication de la règle de prix
(27/11/2026) : leurs lignes reprennent le prix de la commande.

Le cas d'origine : SAL-ORD-2026-04838 (Amri Sadok), BL MAT-DN-2026-06359 à 233,790 pour une commande de 236,615 —
deux cartouches relevées par la marge minimale sur la commande, retombées au prix remisé sur le BL. Sa demande :
« crée le patch de correction des BL qui ont eu un souci pareil ».

La règle, la liste et la reprise sont dans `bl_prix_commande` (`corriger_bl_valides`) : seules les lignes dont le
prix est exactement celui de la règle de prix et inférieur à celui de la commande ; un BL déjà facturé est laissé
(et dit dans le journal du migrate). Rien de comptable ne bouge. Rejouable : une fois corrigé, un BL ne répond plus."""
import frappe


def execute():
    from customization_app.bl_prix_commande import corriger_bl_valides

    for b in corriger_bl_valides(simuler=False):
        print("  BL %s (commande %s) : %s%s" % (
            b["bl"], b["commande"], b["statut"],
            " — %s → %s" % (b.get("avant"), b.get("apres")) if b.get("apres") is not None else ""))
