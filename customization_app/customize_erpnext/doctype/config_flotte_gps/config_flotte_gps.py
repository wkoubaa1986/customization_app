import frappe
from frappe.model.document import Document


class ConfigFlotteGPS(Document):
    def validate(self):
        vus = set()
        for v in self.vehicules:
            v.cbox = (v.cbox or "").strip()
            if v.cbox in vus:
                frappe.throw("Boîtier %s en double" % v.cbox)
            vus.add(v.cbox)
