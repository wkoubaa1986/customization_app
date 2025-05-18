import frappe
from erpnext.selling.doctype.customer.customer import Customer as ErpnextCustomer

@frappe.whitelist()
def check_duplicate_phone(mobile1, mobile2=None, exclude_customer=None):
    numbers = []
    if mobile1:
        numbers.append(mobile1.strip())
    if mobile2:
        numbers.append(mobile2.strip())
    numbers = [n for n in numbers if n]
    if not numbers:
        return []

    # Build SQL WHERE clause with OR
    like_conditions = " OR ".join(["custom_liste_telephone LIKE %s" for _ in numbers])
    values = [f"%{num}%" for num in numbers]

    sql = f"""
        SELECT name, customer_name, custom_liste_telephone
        FROM `tabCustomer`
        WHERE ({like_conditions})
    """

    # Exclude current customer if needed
    if exclude_customer:
        sql += " AND name != %s"
        values.append(exclude_customer)

    return frappe.db.sql(sql, values, as_dict=True)

class SynchroCustomer(ErpnextCustomer):
    def validate(self):
        super().validate()  # Call the original validation

        # Example: validate mobile numbers for Tunisia
        country = self.get("country")
        mobile_no1 = self.get("mobile_number1")
        mobile_no2 = self.get("mobile_number2")

        if country == "Tunisia":
            if not (mobile_no1 and len(mobile_no1) == 8 and mobile_no1.isdigit()):
                frappe.throw("Numéro de mobile 1 doit comporter exactement 8 chiffres pour la Tunisie.")
            if mobile_no2 and (len(mobile_no2) != 8 or not mobile_no2.isdigit()):
                frappe.throw("Numéro de mobile 2 doit comporter exactement 8 chiffres pour la Tunisie.")
    def before_save(self):
        # Map your custom quick entry fields to ERPNext standard fields BEFORE saving
        # So that address creation works
        self.address_line1 = self.get("address_line1") or ""
        self.pincode = self.get("pincode") or ""
        self.country = self.get("country") or ""

        # Set state and city for both Tunisia and non-Tunisia cases
        if self.get("country") == "Tunisia":
            self.state = self.get("custom_state_s") or ""
            self.city = self.get("custom_villes_s") or ""
        else:
            self.state = self.get("custom_state_d") or ""
            self.city = self.get("custom_villes_d") or ""

    def after_insert(self):
        # After the Customer is inserted, create linked Contact and Address
        self.create_primary_contact_from_quick_entry()
        # self.create_primary_address_from_quick_entry()

    def create_primary_contact_from_quick_entry(self):
        """Creates a Contact using quick entry fields if contact info is present."""
        nom_contact = self.get("nom contact")
        email_id = self.get("email_address")
        mobile_no1 = self.get("mobile_number1")
        mobile_no2 = self.get("mobile_number2")

        if nom_contact or email_id or mobile_no1 or mobile_no2:
            contact = frappe.new_doc("Contact")
            contact.first_name = nom_contact or self.customer_name or self.name
            contact.is_primary_contact = 1
            contact.append("links", {
                    "link_doctype": "Customer",
                    "link_name": self.name
                })
            # Add phone numbers, matching your child DocType field names
            if mobile_no1:
                contact.append("phone_nos", {
                    "phone": mobile_no1,
                    "is_primary_phone": 1,
                    "is_primary_mobile": 1,  # Use the field names from your DocType exactly
                })
            if mobile_no2:
                contact.append("phone_nos", {
                    "phone": mobile_no2,
                    "is_primary_phone": 0,
                    "is_primary_mobile": 0,
                })

            # Add email
            if email_id:
                contact.append("email_ids", {
                    "email_id": email_id,
                    "is_primary": 1
                })
            # print("phone_nos:", type(contact.phone_nos), contact.phone_nos)
            # print("email_ids:", type(contact.email_ids), contact.email_ids)
            # print("links:", type(contact.links), contact.links)
            contact.insert(ignore_permissions=True)
            self.db_set("customer_primary_contact", contact.name)

    def create_primary_address_from_quick_entry(self):
        # Only create if we have enough info
        address_line1 = self.address_line1
        pincode = self.pincode
        country = self.country
        state = self.state
        city = self.city
        custom_secteur = self.get("custom_secteur") or ""

        # Avoid creating an Address with missing required fields
        if address_line1 and city:
            address = frappe.new_doc("Address")
            address.address_title = self.customer_name or self.name
            address.address_type = "Billing"
            address.address_line1 = address_line1
            address.pincode = pincode
            address.country = country
            address.state = state
            address.city = city
            # If you have a custom_secteur custom field in Address:
            if hasattr(address, "custom_secteur"):
                address.custom_secteur = custom_secteur
            address.append("links", {
                "link_doctype": "Customer",
                "link_name": self.name
            })
            address.insert(ignore_permissions=True)