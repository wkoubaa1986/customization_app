# customization_app/patches/override_get_item_details.py

import importlib
import sys
import types

def apply():
    # alias for app_ready hook
    return execute()
def execute():
    print("🔧 Applying override for get_item_details...")

    # import your override
    from customization_app.get_item_details import get_item_details as my_get_item_details

    # load the original ERPNext module
    target = importlib.import_module("erpnext.stock.get_item_details")

    # save original
    old = getattr(target, "get_item_details", None)

    if not hasattr(target, "_orig_get_item_details"):
        setattr(target, "_orig_get_item_details", old)

    # replace it on the source module
    setattr(target, "get_item_details", my_get_item_details)

    # Fix all modules that imported it as “from … import get_item_details”
    for name, mod in list(sys.modules.items()):
        if not isinstance(mod, types.ModuleType):
            continue

        if hasattr(mod, "get_item_details") and getattr(mod, "get_item_details") is old:
            setattr(mod, "get_item_details", my_get_item_details)

    print("✅ get_item_details override applied.")
