__version__ = "5.184.1"
# ------------------------------------------------------------------
# Apply ONE-TIME patches (migrations / idempotent overrides)
# ------------------------------------------------------------------
def _apply_get_item_details_override():
    try:
        # idempotent: safe to run multiple times
        from customization_app.patches.override_get_item_details import execute
        execute()
    except Exception as e:
        # don't crash app startup; log if you want
        import sys
        print(f"[customization_app] get_item_details override failed: {e}", file=sys.stderr)

_apply_get_item_details_override()
from customization_app.monkey_patches import item_variant

# Plusieurs paiements le même jour sur une commande / facture de vente : le refus d'ERPNext
# (« duplicate due dates ») est levé, plus aucune échéance n'est décalée (echeances_meme_jour).
def _apply_echeances_meme_jour():
    try:
        from customization_app.echeances_meme_jour import appliquer
        appliquer()
    except Exception as e:
        import sys
        print("[customization_app] échéances du même jour : surcharge non appliquée : %s" % e, file=sys.stderr)


_apply_echeances_meme_jour()


# L'avoir d'une commande n'est plus refusé pour un écart de virgule flottante (206,133 + 78 « > » 284,133) :
# le contrôle d'ERPNext sur l'avance garde une tolérance d'un demi-millime (avance_arrondie).
def _apply_avance_arrondie():
    try:
        from customization_app.avance_arrondie import appliquer
        appliquer()
    except Exception as e:
        import sys
        print("[customization_app] avance arrondie : surcharge non appliquée : %s" % e, file=sys.stderr)


_apply_avance_arrondie()
