try:
    import erpnext.stock.get_item_details
    import customization_app.get_item_details
    erpnext.stock.get_item_details.get_item_details = customization_app.get_item_details.get_item_details
except ImportError as e:
    print(f"Monkey patch failed: {e}")

__version__ = "develop"