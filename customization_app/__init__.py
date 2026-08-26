__version__ = "5.17.3"
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