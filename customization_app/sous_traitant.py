"""Sous-traitants : des intervenants externes qui travaillent comme nos techniciens.

Décisions utilisateur du 30/10/2026
-----------------------------------
- Un sous-traitant est un EMPLOYÉ coché « Sous-traitant » (`custom_sous_traitant`), avec un
  utilisateur (rôle « Sous-traitant »), SON entrepôt (`custom_warehouse`, « Stock <Nom> - A&S »)
  et SON compte de caisse (`custom_compte_caisse`, « <Nom> - A&S ») qui reçoit espèces, chèques et
  traites. Les dettes restent les nôtres (Dettes - A&S) : virement et carte ne lui sont pas offerts.
- Il remet tout comme un employé : clôture de caisse et collecte existantes ; sa rémunération est
  réglée hors système. Ses prix sont les prix de vente standard.
- Ses interventions sont créées par le bureau (planning) ; il ne voit que les siennes ; tous nos
  utilisateurs voient son calendrier comme celui d'un employé.
- Ses transferts de stock passent par la double validation Magasin → stock ; une vérification de
  stock est planifiée (chaque semaine ou toutes les deux semaines) comme pour les techniciens.
- Economiq (partenaire historique, HR-EMP-00007) n'est PAS touché.

Ce module : la création en un geste (`creer`), la clôture mobile (`cloturer_intervention` : commande
depuis son stock + règlement + bon de livraison + tâche), la recherche dans son stock, les droits
(chaque sous-traitant ne lit que ses tâches et ses commandes) et le profil pour l'écran.
"""
from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

ROLE = "Sous-traitant"
CHAMP_FLAG = "custom_sous_traitant"
CHAMP_COMPTE = "custom_compte_caisse"
CHAMP_COULEUR = "custom_couleur"
COMPTE_PARENT = "Comptes bancaires - A&S"
ENTREPOT_PARENT = "Tous les entrepôts - A&S"
PRIX_LISTE = "Vente standard"
DOCTYPE_TACHE = "Tache de travail"

#: les modes qu'il peut saisir à la clôture — pas de virement, pas de carte (décision 30/10/2026)
MODES_PERMIS = ("Espèces", "Chèque", "Traite bancaire LC", "Dette non payée")
MODES_AVEC_PIECE = ("Chèque", "Traite bancaire LC")
TOLERANCE = 0.005
#: couleurs de calendrier attribuées aux sous-traitants sans couleur choisie (lisibles sur texte blanc)
PALETTE = ("#7C3AED", "#0E7490", "#B45309", "#BE185D", "#4D7C0F", "#1D4ED8", "#9F1239", "#374151")


# --------------------------------------------------------------------------- règles pures

def nom_entrepot(nom: str) -> str:
    return "Stock %s" % " ".join((nom or "").split())


def nom_compte(nom: str) -> str:
    return " ".join((nom or "").split())


def couleur_par_defaut(employe: str) -> str:
    """Toujours la même couleur pour le même employé, sans réglage."""
    h = int(hashlib.md5((employe or "").encode("utf-8")).hexdigest(), 16)
    return PALETTE[h % len(PALETTE)]


def lignes_articles(articles: list, stock: dict, prix: dict) -> tuple[list, list]:
    """Les lignes de commande depuis la saisie de l'écran. -> (lignes, erreurs). PURE.
    `articles` : [{item_code, qty}] ; `stock` : {item_code: qté dans SON entrepôt} ;
    `prix` : {item_code: prix de vente}. Refus : quantité nulle, stock insuffisant, prix inconnu."""
    lignes, erreurs = [], []
    cumul: dict[str, float] = {}
    for a in articles or []:
        code, qte = (a.get("item_code") or "").strip(), flt(a.get("qty"))
        if not code:
            continue
        if qte <= 0:
            erreurs.append(_("{0} : quantité invalide.").format(code))
            continue
        cumul[code] = cumul.get(code, 0.0) + qte
        if cumul[code] > flt(stock.get(code)) + 1e-9:
            erreurs.append(_("{0} : {1} demandé, {2} dans votre stock.").format(code, cumul[code], flt(stock.get(code))))
            continue
        if flt(prix.get(code)) <= 0:
            erreurs.append(_("{0} : pas de prix de vente — demandez au bureau.").format(code))
            continue
        lignes.append({"item_code": code, "qty": qte, "rate": flt(prix.get(code), 3)})
    if not lignes and not erreurs:
        erreurs.append(_("Aucun article."))
    return lignes, erreurs


def lignes_paiement(paiements: list, total: float, jour=None) -> tuple[list, list]:
    """L'échéancier de la commande depuis la saisie. -> (lignes, erreurs). PURE.
    `paiements` : [{mode, montant, numero, banque}]. Modes permis seulement ; chèque et traite exigent
    un numéro ; la somme doit faire le total (fractionné = plusieurs lignes)."""
    lignes, erreurs = [], []
    for p in paiements or []:
        mode, montant = (p.get("mode") or "").strip(), flt(p.get("montant"))
        if mode not in MODES_PERMIS:
            erreurs.append(_("Mode de paiement non permis : {0}.").format(mode or "?"))
            continue
        if montant <= 0:
            erreurs.append(_("{0} : montant invalide.").format(mode))
            continue
        numero = (p.get("numero") or "").strip()
        if mode in MODES_AVEC_PIECE and not numero:
            erreurs.append(_("{0} : le numéro est obligatoire.").format(mode))
            continue
        # ERPNext refuse deux échéances à la même date : la 2e ligne d'un fractionné prend J+1, la 3e J+2…
        ligne = {"mode_of_payment": mode, "payment_amount": round(montant, 3),
                 "due_date": str(add_days(getdate(jour), len(lignes))) if jour else None}
        if mode in MODES_AVEC_PIECE:
            ligne["custom__n_chèque__transaction"] = numero
            ligne["custom_banque"] = (p.get("banque") or "").strip()
        lignes.append(ligne)
    somme = round(sum(l["payment_amount"] for l in lignes), 3)
    if not lignes:
        erreurs.append(_("Aucun règlement saisi."))
    elif abs(somme - flt(total)) > TOLERANCE:
        erreurs.append(_("Les règlements font {0}, la commande {1}.").format(somme, round(flt(total), 3)))
    return lignes, erreurs


# --------------------------------------------------------------------------- qui est qui

def _champs_prets() -> bool:
    return frappe.db.has_column("Employee", CHAMP_FLAG)


def employe_courant(user=None):
    """La fiche employé SOUS-TRAITANT de l'utilisateur, ou None."""
    user = user or frappe.session.user
    if not _champs_prets():
        return None
    return frappe.db.get_value("Employee", {"user_id": user, "status": "Active", CHAMP_FLAG: 1},
                               ["name", "employee_name", "custom_warehouse", CHAMP_COMPTE], as_dict=True)


def est_sous_traitant(employe: str) -> bool:
    return bool(employe) and _champs_prets() and bool(frappe.db.get_value("Employee", employe, CHAMP_FLAG))


def _est_supervision() -> bool:
    return "System Manager" in frappe.get_roles()


def couleur_calendrier(employe: str):
    """Couleur réglée sur la fiche (`custom_couleur`), sinon celle de la palette — pour un sous-traitant
    seulement ; les employés gardent le barème STAFF_COLORS de api.py."""
    if not employe or not _champs_prets():
        return None
    cache = frappe.local.__dict__.setdefault("_couleurs_sous_traitants", {})
    if employe not in cache:
        r = frappe.db.get_value("Employee", employe, [CHAMP_FLAG, CHAMP_COULEUR], as_dict=True)
        cache[employe] = ((r.get(CHAMP_COULEUR) or couleur_par_defaut(employe)) if r and cint(r.get(CHAMP_FLAG)) else None)
    return cache[employe]


# --------------------------------------------------------------------------- droits

def permission_query_conditions_tache(user=None):
    """Un sous-traitant ne liste que SES tâches (calendrier, liste, Ma journée). Les autres : rien de changé."""
    emp = employe_courant(user)
    if not emp or _est_supervision():
        return ""
    return "`tabTache de travail`.`custom_choix_du_staff` = %s" % frappe.db.escape(emp.name)


def has_permission_tache(doc, user=None, permission_type=None):
    emp = employe_courant(user)
    if not emp or _est_supervision():
        return True
    return doc.get("custom_choix_du_staff") == emp.name


def permission_query_conditions_commande(user=None):
    """Ses commandes : celles de SES tâches, et celles qu'il a créées."""
    emp = employe_courant(user)
    if not emp or _est_supervision():
        return ""
    return ("(`tabSales Order`.`owner` = {u} or `tabSales Order`.`name` in (select commande_client from "
            "`tabTache de travail` where custom_choix_du_staff = {e} and ifnull(commande_client, '') != ''))"
            .format(u=frappe.db.escape(user or frappe.session.user), e=frappe.db.escape(emp.name)))


def has_permission_commande(doc, user=None, permission_type=None):
    emp = employe_courant(user)
    if not emp or _est_supervision():
        return True
    if doc.get("owner") == (user or frappe.session.user):
        return True
    return bool(frappe.db.exists(DOCTYPE_TACHE, {"commande_client": doc.name, "custom_choix_du_staff": emp.name}))


def boot_session(bootinfo):
    """`frappe.boot.sous_traitant` : l'écran sait d'emblée s'il parle à un sous-traitant."""
    try:
        emp = employe_courant()
        bootinfo.sous_traitant = ({"employe": emp.name, "nom": emp.employee_name, "entrepot": emp.custom_warehouse}
                                  if emp else None)
    except Exception:
        bootinfo.sous_traitant = None


# --------------------------------------------------------------------------- son stock

def _mon_stock(emp) -> dict:
    if not emp or not emp.custom_warehouse:
        return {}
    return {b.item_code: flt(b.actual_qty)
            for b in frappe.get_all("Bin", filters={"warehouse": emp.custom_warehouse, "actual_qty": [">", 0]},
                                    fields=["item_code", "actual_qty"])}


def _prix(codes: list) -> dict:
    if not codes:
        return {}
    rows = frappe.get_all("Item Price", filters={"price_list": PRIX_LISTE, "item_code": ["in", codes], "selling": 1},
                          fields=["item_code", "price_list_rate"], order_by="valid_from desc")
    out = {}
    for r in rows:
        out.setdefault(r.item_code, flt(r.price_list_rate))
    return out


def _mien_ou_supervision():
    emp = employe_courant()
    if not emp:
        frappe.throw(_("Réservé aux sous-traitants."), frappe.PermissionError)
    return emp


@frappe.whitelist()
def profil():
    emp = employe_courant()
    return ({"employe": emp.name, "nom": emp.employee_name, "entrepot": emp.custom_warehouse,
             "compte": emp.get(CHAMP_COMPTE)} if emp else None)


@frappe.whitelist()
def rechercher_articles(txt=None, limite=30):
    """Les articles de SON stock (quantité > 0) qui correspondent au nom ou au code, avec leur prix."""
    emp = _mien_ou_supervision()
    stock = _mon_stock(emp)
    if not stock:
        return []
    motif = "%%%s%%" % (txt or "").strip()
    items = frappe.get_all("Item", filters={"name": ["in", list(stock)], "disabled": 0},
                           or_filters={"item_code": ["like", motif], "item_name": ["like", motif]},
                           fields=["name", "item_name", "image", "stock_uom"], limit=cint(limite) or 30,
                           order_by="item_name")
    prix = _prix([i.name for i in items])
    return [{"item_code": i.name, "item_name": i.item_name, "image": i.image, "uom": i.stock_uom,
             "stock": stock[i.name], "prix": prix.get(i.name, 0.0)} for i in items]


@frappe.whitelist()
def mon_stock():
    """Le résumé de son entrepôt : chaque article, sa quantité, son prix de vente, la valeur de vente."""
    emp = _mien_ou_supervision()
    stock = _mon_stock(emp)
    noms = {i.name: i for i in frappe.get_all("Item", filters={"name": ["in", list(stock) or [""]]},
                                              fields=["name", "item_name", "image"])}
    prix = _prix(list(stock))
    lignes = sorted(({"item_code": c, "item_name": noms.get(c, {}).get("item_name") or c,
                      "image": noms.get(c, {}).get("image"), "qty": q, "prix": prix.get(c, 0.0),
                      "valeur": round(q * prix.get(c, 0.0), 3)} for c, q in stock.items()),
                    key=lambda l: l["item_name"] or "")
    return {"entrepot": emp.custom_warehouse, "lignes": lignes,
            "valeur_vente": round(sum(l["valeur"] for l in lignes), 3), "nb_articles": len(lignes)}


# --------------------------------------------------------------------------- clôture

def _tache_a_moi(nom):
    from customization_app.cloture_partenaire import _ma_tache
    return _ma_tache(nom)


def _nouvelle_commande(doc, emp, articles, paiements):
    """La commande depuis son stock, en brouillon, sous SON identité (il en est l'auteur)."""
    from customization_app import cloture_tache as C

    if not doc.get("custom_client"):
        frappe.throw(_("Cette intervention n'a pas de client : demandez au bureau de le renseigner."))
    stock = _mon_stock(emp)
    codes = [(a.get("item_code") or "").strip() for a in articles if a.get("item_code")]
    lignes, erreurs = lignes_articles(articles, stock, _prix(codes))
    if erreurs:
        frappe.throw("<br>".join(erreurs), title=_("Articles"))
    pre = (C.exigences(doc.name) or {}).get("nouvelle_commande") or {}
    so = frappe.new_doc("Sales Order")
    so.update({
        "customer": doc.custom_client, "transaction_date": nowdate(),
        "delivery_date": pre.get("delivery_date") or nowdate(),
        "set_warehouse": emp.custom_warehouse, "selling_price_list": PRIX_LISTE,
        "custom_type_de_transaction": pre.get("custom_type_de_transaction"),
        "taxes_and_charges": pre.get("taxes_and_charges"),
    })
    for l in lignes:
        so.append("items", {"item_code": l["item_code"], "qty": l["qty"], "rate": l["rate"],
                            "warehouse": emp.custom_warehouse, "delivery_date": so.delivery_date})
    so.flags.ignore_permissions = True
    so.run_method("set_missing_values")
    so.calculate_taxes_and_totals()
    echeances, erreurs = lignes_paiement(paiements, so.grand_total, nowdate())
    if erreurs:
        frappe.throw("<br>".join(erreurs), title=_("Règlement"))
    so.set("payment_schedule", [])
    for e in echeances:
        e["custom_row_uid"] = frappe.generate_hash(length=12)
        so.append("payment_schedule", e)
    # ⚠️ Sous l'identité système : ERPNext vérifie le droit de LIRE le compte client (Débiteurs) à
    # l'insertion d'une commande, et `ignore_permissions` n'y change rien (cf. cloture_partenaire).
    # L'auteur reste lui : `owner` est reposé sur l'utilisateur réel, c'est ce qui lui ouvre SES commandes.
    from customization_app.cloture_partenaire import _en_systeme
    reel = frappe.session.user
    with _en_systeme():
        so.insert()
        frappe.db.set_value("Sales Order", so.name, "owner", reel, update_modified=False)
    frappe.db.set_value(DOCTYPE_TACHE, doc.name, "commande_client", so.name, update_modified=False)
    frappe.db.commit()
    return so.name


@frappe.whitelist()
def cloturer_intervention(tache, articles=None, paiements=None, rapport_visite=None):
    """Tout en un depuis le téléphone : commande depuis son stock + règlement, bon de livraison,
    puis la tâche. Si une commande est déjà liée (bureau), on ne la refait pas : on la mène au bout."""
    from customization_app import cloture_partenaire as P

    emp = _mien_ou_supervision()
    doc = _tache_a_moi(tache)
    if doc.custom_choix_du_staff != emp.name and not _est_supervision():
        frappe.throw(_("Cette intervention ne vous est pas affectée."), frappe.PermissionError)
    articles = json.loads(articles) if isinstance(articles, str) else (articles or [])
    paiements = json.loads(paiements) if isinstance(paiements, str) else (paiements or [])

    P._verifier_photos_et_position(doc)
    etapes = []
    if not doc.commande_client:
        nom = _nouvelle_commande(doc, emp, articles, paiements)
        doc.reload()
        etapes.append({"quoi": "Commande", "doc": nom, "etat": "créée depuis votre stock"})
    with P._en_systeme():
        nom, etat = P._valider_commande(doc.commande_client)
        etapes.append({"quoi": "Commande", "doc": nom, "etat": etat})
        nom, etat = P._bon_de_livraison(doc, doc.commande_client)
        etapes.append({"quoi": "Bon de livraison", "doc": nom, "etat": etat})
    doc.reload()
    if rapport_visite:
        doc.rapport_visite = rapport_visite
    doc.status = "Completed"
    doc.save()          # les contrôles de clôture (photos, GPS, rapport) jouent comme pour tout le monde
    frappe.db.commit()
    etapes.append({"quoi": "Intervention", "doc": doc.name, "etat": "terminée"})
    return {"etapes": etapes, "tache": doc.name, "commande": doc.commande_client}


# --------------------------------------------------------------------------- création

def _nouveau_compte(nom: str) -> str:
    nom_c = "%s - %s" % (nom_compte(nom), frappe.get_cached_value("Company", _societe(), "abbr"))
    if frappe.db.exists("Account", nom_c):
        return nom_c
    return frappe.get_doc({"doctype": "Account", "account_name": nom_compte(nom), "parent_account": COMPTE_PARENT,
                           "account_type": "Bank", "company": _societe()}).insert(ignore_permissions=True).name


def _nouvel_entrepot(nom: str) -> str:
    nom_w = "%s - %s" % (nom_entrepot(nom), frappe.get_cached_value("Company", _societe(), "abbr"))
    if frappe.db.exists("Warehouse", nom_w):
        return nom_w
    return frappe.get_doc({"doctype": "Warehouse", "warehouse_name": nom_entrepot(nom),
                           "parent_warehouse": ENTREPOT_PARENT, "company": _societe()}).insert(ignore_permissions=True).name


def _societe() -> str:
    return frappe.db.get_value("Warehouse", ENTREPOT_PARENT, "company") or frappe.defaults.get_global_default("company")


def _inscrire_verification(entrepot: str, jour: str, heure: str, periodicite: str):
    """La ligne de Config Stock Entrepot Verification : la vérification planifiée comme pour les techniciens."""
    from customization_app import stock_entrepots as S
    if not frappe.db.exists("DocType", S.CONFIG):
        return
    cfg = frappe.get_single(S.CONFIG)
    for r in cfg.get("verifications") or []:
        if r.entrepot == entrepot:
            r.jour, r.heure, r.actif = jour, heure, 1
            if r.meta.has_field("periodicite"):
                r.periodicite = periodicite
            cfg.save(ignore_permissions=True)
            return
    ligne = {"entrepot": entrepot, "jour": jour, "heure": heure, "actif": 1}
    if frappe.get_meta(cfg.meta.get_field("verifications").options).has_field("periodicite"):
        ligne["periodicite"] = periodicite
    cfg.append("verifications", ligne)
    cfg.save(ignore_permissions=True)


@frappe.whitelist()
def creer(nom, email, mot_de_passe, telephone=None, genre="Male", date_naissance=None,
          jour_verification="Lundi", heure_verification="09:00", periodicite="Chaque semaine", couleur=None):
    """Tout en un geste : utilisateur (rôle Sous-traitant), employé, entrepôt, compte, vérification planifiée."""
    frappe.only_for("System Manager")
    nom = " ".join((nom or "").split())
    email = (email or "").strip().lower()
    if not nom or not email or not mot_de_passe:
        frappe.throw(_("Nom, e-mail et mot de passe sont obligatoires."))
    if frappe.db.exists("User", email):
        frappe.throw(_("L'utilisateur {0} existe déjà.").format(email))
    if frappe.db.exists("Employee", {"employee_name": nom, "status": "Active"}):
        frappe.throw(_("Un employé actif s'appelle déjà {0}.").format(nom))
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)

    prenom, _sep, reste = nom.partition(" ")
    user = frappe.get_doc({"doctype": "User", "email": email, "first_name": prenom, "last_name": reste or None,
                           "send_welcome_email": 0, "enabled": 1, "user_type": "System User",
                           "new_password": mot_de_passe, "mobile_no": telephone or None})
    user.flags.ignore_permissions = True
    user.insert()
    user.add_roles(ROLE)

    entrepot = _nouvel_entrepot(nom)
    compte = _nouveau_compte(nom)
    emp = frappe.get_doc({
        "doctype": "Employee", "first_name": prenom, "last_name": reste or None, "employee_name": nom,
        "gender": genre or "Male", "date_of_birth": date_naissance or "1990-01-01", "date_of_joining": nowdate(),
        "company": _societe(), "status": "Active", "user_id": email, "cell_number": telephone or None,
        "custom_warehouse": entrepot, CHAMP_FLAG: 1, CHAMP_COMPTE: compte,
        CHAMP_COULEUR: couleur or None, "create_user_permission": 0,
    })
    emp.flags.ignore_permissions = True
    emp.insert()
    _inscrire_verification(entrepot, jour_verification, heure_verification or "09:00", periodicite)
    frappe.db.commit()
    return {"utilisateur": email, "employe": emp.name, "entrepot": entrepot, "compte": compte,
            "couleur": couleur or couleur_par_defaut(emp.name)}


@frappe.whitelist()
def liste():
    """Les sous-traitants, pour l'écran d'administration."""
    frappe.only_for("System Manager")
    if not _champs_prets():
        return []
    return frappe.get_all("Employee", filters={CHAMP_FLAG: 1},
                          fields=["name", "employee_name", "user_id", "custom_warehouse", CHAMP_COMPTE, "status"],
                          order_by="employee_name")
