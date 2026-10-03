import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, get_datetime

# Ce qui ne se change que par le circuit de la page « Stock par entrepôt » (stock_entrepots._sauver pose le
# drapeau `circuit_verification`) : le statut et les signatures des deux validations, le rapprochement, le
# comptage ligne à ligne. Le formulaire /app/verification-stock garde note, date, employé, responsable.
CHAMPS_PROTEGES = ("statut", "rapprochement", "valide_employe_par", "valide_employe_le", "valide_responsable_par",
                   "valide_responsable_le", "termine_le", "termine_par", "renvois")
DATES = {"valide_employe_le", "valide_responsable_le", "termine_le"}


def _norm(champ, valeur):
    if valeur in (None, ""):
        return None
    if champ in DATES:
        return get_datetime(valeur)
    if champ == "renvois":
        return cint(valeur)
    return cstr(valeur)


def _cle_ligne(l):
    return (l.item_code, cint(l.compte), flt(l.qte_comptee, 6), flt(l.qte_employe, 6), cint(l.ajuste),
            cint(l.a_recompter), flt(l.qte_systeme, 6))


class VerificationStock(Document):
    def validate(self):
        if self.flags.circuit_verification:
            return
        if self.is_new():
            if self.statut != "En cours" or self.rapprochement or self.get("lignes"):
                self._refus(["statut" if self.statut != "En cours" else "", "rapprochement" if self.rapprochement else "",
                             "lignes" if self.get("lignes") else ""])
            return
        avant = self.get_doc_before_save() or frappe.get_doc(self.doctype, self.name)
        changes = [c for c in CHAMPS_PROTEGES if _norm(c, avant.get(c)) != _norm(c, self.get(c))]
        if [_cle_ligne(l) for l in avant.get("lignes") or []] != [_cle_ligne(l) for l in self.get("lignes") or []]:
            changes.append("lignes")
        if changes:
            self._refus(changes)

    def _refus(self, changes):
        libelles = {c: self.meta.get_label(c) for c in CHAMPS_PROTEGES}
        libelles["lignes"] = _("comptage")
        frappe.throw(_("{0} se traite depuis la page « Stock par entrepôt » (comptage, validation, confirmation, "
                       "renvoi) : {1} ne se modifie pas ici. Seuls la note, la date, l’employé et le responsable "
                       "s’éditent dans ce formulaire.")
                     .format(self.name or _("Cette vérification"), ", ".join(libelles.get(c, c) for c in changes if c)),
                     title=_("Circuit de vérification"))
