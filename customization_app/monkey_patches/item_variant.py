from erpnext.stock.doctype.item.item import Item

def _disable_item_update_variants(self):
    # Désactivation totale de la mise à jour des variantes
    return

# Monkey-patch : override méthode de classe
Item.update_variants = _disable_item_update_variants
