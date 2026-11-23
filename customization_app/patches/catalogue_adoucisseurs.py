"""Catalogue des adoucisseurs : la prod reçoit ce qui a été préparé sur le dev (demande du 23/11/2026).

L'utilisateur a refait sur le dev, avec l'assistant articles (MCP), toute la gamme des adoucisseurs : nouveaux
articles (modèles et variantes par tête de commande et bypass), attributs, groupe « Adoucisseurs Industriels », prix
sur les cinq listes, descriptions, anciens articles désactivés ; et il a transformé à la main le stock de Ad-43L en
Ad-FRP-40L-K4. « Ce patch servira à avoir la même base de données en prod. »

Les données sont dans `donnees_catalogue_adoucisseurs/donnees.json`, écrites par
`/workspace/development/mcp_articles_dev/generer_patch_prod.py` : la DIFFÉRENCE exacte entre le dev et la prod lue le
jour de la génération (lignes brutes des articles à créer ; pour les articles déjà en prod, champ par champ, la valeur
de prod et la valeur du dev).

Règles :
- un article renommé sur le dev est renommé ici par le renommage de Frappe (le code change sur toutes les commandes,
  le stock, les prix), avant tout le reste ;
- rien n'est écrasé : un article, un prix, un attribut déjà présent est laissé tel quel ;
- un champ d'un article existant n'est posé que si la prod porte encore la valeur relevée à la génération — si
  quelqu'un l'a changé en prod entre-temps, la valeur de prod reste et le bilan le dit ;
- les lignes sont insérées telles quelles (pas par les formulaires) : mêmes noms, mêmes dates, aucun envoi vers le
  site marchand, aucun recalcul ;
- la transformation de stock, elle, passe par l'outil « Transformation d'articles » (vraie écriture de stock, à la date
  du jour) et porte sur TOUT le stock présent dans l'entrepôt, comme sur le dev ;
- un ancien article n'est désactivé que s'il n'a plus de stock et aucune commande client en attente de livraison ;
- une erreur n'arrête pas la migration : tout ce que ce patch a fait est annulé, et l'erreur est écrite dans le
  journal des erreurs.

Le bilan (ce qui a été créé, laissé, refusé) est écrit dans le journal des erreurs sous le titre TITRE_BILAN.
"""
import json
import os
import shutil

import frappe
from frappe.utils import flt

DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees_catalogue_adoucisseurs")
TITRE_BILAN = "Catalogue adoucisseurs : bilan de la mise en prod (ce n’est pas une erreur)"
TITRE_ERREUR = "Catalogue adoucisseurs : mise en prod annulée"
ARBRE = ("lft", "rgt", "old_parent")


class Bilan:
    def __init__(self):
        self.comptes, self.alertes = {}, []

    def note(self, cle, n=1):
        self.comptes[cle] = self.comptes.get(cle, 0) + n

    def alerte(self, texte):
        self.alertes.append(texte)

    def texte(self):
        lignes = ["%-58s %d" % (k, self.comptes[k]) for k in sorted(self.comptes)]
        lignes.append("")
        lignes.append("Points à regarder : %d" % len(self.alertes))
        lignes.extend("  ! " + a for a in self.alertes)
        return "\n".join(lignes)


# ── outils ───────────────────────────────────────────────────────────────────
_colonnes = {}


def colonnes(table):
    if table not in _colonnes:
        _colonnes[table] = [r[0] for r in frappe.db.sql("show columns from `%s`" % table)]
    return _colonnes[table]


def existe(table, nom):
    return bool(frappe.db.sql("select 1 from `%s` where name=%%s" % table, (nom,)))


def inserer(table, ligne, bilan):
    cols = colonnes(table)
    garde = {k: v for k, v in ligne.items() if k in cols}
    perdues = sorted(k for k, v in ligne.items() if k not in cols and v not in (None, "", 0, "0", "0.0"))
    if perdues:
        bilan.alerte("%s %s : colonnes absentes de cette base, valeurs non posées : %s" % (table, ligne.get("name"), perdues))
    frappe.db.sql("insert into `%s` (%s) values (%s)" % (
        table, ", ".join("`%s`" % k for k in garde), ", ".join(["%s"] * len(garde))), list(garde.values()))


def pareil(a, b):
    if a in (None, "") and b in (None, ""):
        return True
    if str(a) == str(b):
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


# ── 0. Articles renommés ─────────────────────────────────────────────────────
def renommer(d, bilan):
    """Le code de l'article change partout (commandes, stock, prix, historique) : c'est le renommage de Frappe, le
    même que celui fait sur le dev. La fiche garde sa date : les retouches qui suivent la comparent à celle du dev."""
    for r in d.get("renommages", []):
        de, vers = r["de"], r["vers"]
        if existe("tabItem", vers):
            bilan.note("article déjà renommé")
            if existe("tabItem", de):
                bilan.alerte("Renommage %s → %s : les deux articles existent, rien n'est fusionné" % (de, vers))
            continue
        if not existe("tabItem", de):
            bilan.alerte("Renommage %s → %s : l'article %s n'existe pas ici" % (de, vers, de))
            continue
        date = frappe.db.sql("select modified, modified_by from tabItem where name=%s", (de,))[0]
        frappe.db.savepoint("catalogue_renommage")
        try:
            frappe.rename_doc("Item", de, vers, force=True, show_alert=False, rebuild_search=False)
            frappe.db.sql("update tabItem set modified=%s, modified_by=%s where name=%s", (date[0], date[1], vers))
            bilan.note("article renommé")
        except Exception as e:
            frappe.db.rollback(save_point="catalogue_renommage")
            frappe.clear_messages()
            bilan.note("renommage refusé")
            bilan.alerte("Renommage %s → %s : refusé (%s) — à faire à la main, les retouches de cet article ne sont pas posées"
                         % (de, vers, str(e)[:300]))
    if d.get("renommages"):
        frappe.local.document_cache = {}


# ── 1. Attributs et groupes, avant les articles qui s'y réfèrent ─────────────
def creer_attributs(d, bilan):
    for a in d.get("attributs", []):
        nom = a["ligne"]["name"]
        if existe("tabItem Attribute", nom):
            bilan.note("attribut déjà présent")
            bilan.alerte("Attribut « %s » : déjà présent, laissé tel quel (ses valeurs ne sont pas complétées)" % nom)
            continue
        inserer("tabItem Attribute", a["ligne"], bilan)
        for v in a["valeurs"]:
            if not existe("tabItem Attribute Value", v["name"]):
                inserer("tabItem Attribute Value", v, bilan)
        bilan.note("attribut créé")


def creer_groupes(d, bilan):
    from frappe.utils.nestedset import update_nsm
    for g in d.get("groupes", []):
        nom = g["ligne"]["name"]
        if existe("tabItem Group", nom):
            bilan.note("groupe déjà présent")
            continue
        ligne = dict(g["ligne"], lft=0, rgt=0, old_parent="")
        if ligne.get("parent_item_group") and not existe("tabItem Group", ligne["parent_item_group"]):
            bilan.alerte("Groupe « %s » : son parent « %s » n'existe pas, groupe non créé" % (nom, ligne["parent_item_group"]))
            continue
        inserer("tabItem Group", ligne, bilan)
        update_nsm(frappe.get_doc("Item Group", nom))      # place le groupe dans l'arbre
        bilan.note("groupe créé")


# ── 2. Articles ──────────────────────────────────────────────────────────────
def liens_manquants(d):
    """Chaque fiche citée par une ligne à insérer doit exister ici (ou être créée par ce patch)."""
    crees = {("Item", a["name"]) for a in d.get("articles_a_creer", [])}
    crees |= {("Item Attribute", a["ligne"]["name"]) for a in d.get("attributs", [])}
    crees |= {("Item Group", g["ligne"]["name"]) for g in d.get("groupes", [])}
    return ["%s « %s »" % (dt, nom) for dt, nom in d.get("liens", []) if (dt, nom) not in crees and not frappe.db.exists(dt, nom)]


def creer_articles(d, bilan):
    for a in d.get("articles_a_creer", []):
        nom = a["name"]
        if existe("tabItem", nom):
            bilan.note("article déjà présent")
            bilan.alerte("Article %s : déjà présent, laissé tel quel" % nom)
            continue
        inserer("tabItem", a["ligne"], bilan)
        for table, lignes in a["enfants"].items():
            for l in lignes:
                if existe("tab" + table, l["name"]):
                    bilan.alerte("Article %s : ligne %s (%s) déjà présente, laissée" % (nom, l["name"], table))
                else:
                    inserer("tab" + table, l, bilan)
        bilan.note("article créé")


def modifier(table, liste, bilan, quoi):
    """Champ par champ : posé seulement si la valeur en place est encore celle relevée en prod à la génération."""
    for m in liste:
        nom = m["name"]
        r = frappe.db.sql("select * from `%s` where name=%%s" % table, (nom,), as_dict=True)
        if not r:
            bilan.alerte("%s %s : n'existe pas ici, retouches non posées" % (quoi, nom))
            continue
        actuel, a_poser = r[0], {}
        for champ, v in m["champs"].items():
            if champ not in actuel:
                bilan.alerte("%s %s : le champ %s n'existe pas ici" % (quoi, nom, champ))
            elif pareil(actuel[champ], v["dev"]):
                bilan.note("champ déjà à jour")
            elif pareil(actuel[champ], v["prod"]):
                a_poser[champ] = v["dev"]
            else:
                bilan.note("champ changé en prod entre-temps (laissé)")
                bilan.alerte("%s %s : %s a changé en prod depuis le relevé — valeur de prod gardée" % (quoi, nom, champ))
        if a_poser:
            bilan.note("champ posé", len(a_poser))
            if m.get("modified") and str(m["modified"]) > str(actuel["modified"]):
                a_poser.update(modified=m["modified"], modified_by=m.get("modified_by") or actuel["modified_by"])
            frappe.db.sql("update `%s` set %s where name=%%s" % (table, ", ".join("`%s`=%%s" % k for k in a_poser)),
                          list(a_poser.values()) + [nom])
            bilan.note("%s retouché" % quoi)


# ── 3. Prix ──────────────────────────────────────────────────────────────────
def cle_prix(l):
    return tuple((l.get(k) or "") if k != "valid_from" else str(l.get(k) or "")
                 for k in ("item_code", "price_list", "uom", "customer", "supplier", "valid_from", "batch_no", "packing_unit"))


def creer_prix(d, bilan):
    for l in d.get("prix_a_creer", []):
        if existe("tabItem Price", l["name"]):
            bilan.note("prix déjà présent")
            continue
        memes = frappe.db.sql("select * from `tabItem Price` where item_code=%s and price_list=%s", (l["item_code"], l["price_list"]), as_dict=True)
        if any(cle_prix(json.loads(json.dumps(m, default=str))) == cle_prix(l) for m in memes):
            bilan.note("prix déjà saisi en prod (laissé)")
            bilan.alerte("Prix %s / %s : un prix existe déjà en prod (%s), le nouveau (%s) n'est pas posé" % (
                l["item_code"], l["price_list"], ", ".join(str(flt(m.price_list_rate, 3)) for m in memes), flt(l["price_list_rate"], 3)))
            continue
        inserer("tabItem Price", l, bilan)
        bilan.note("prix créé")


# ── 4. Images jointes ────────────────────────────────────────────────────────
def remettre_fichiers(d, bilan, dossier):
    site = frappe.get_site_path()
    for f in d.get("fichiers", []):
        ligne, rel = f["ligne"], f.get("chemin")
        if rel:
            src, dst = os.path.join(dossier, "fichiers", rel), os.path.join(site, rel)
            if os.path.isfile(dst):
                if os.path.isfile(src) and os.path.getsize(dst) != os.path.getsize(src):
                    bilan.alerte("Image %s : un autre fichier du même nom existe déjà, laissé" % rel)
                bilan.note("image déjà sur le disque")
            elif not os.path.isfile(src):
                bilan.alerte("Image %s : absente des données du patch" % rel)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                bilan.note("image copiée")
        if existe("tabFile", ligne["name"]):
            bilan.note("fiche Fichier déjà présente")
        elif ligne.get("attached_to_name") and not frappe.db.exists(ligne["attached_to_doctype"], ligne["attached_to_name"]):
            bilan.alerte("Image %s : %s %s n'existe pas, non rattachée" % (ligne.get("file_name"), ligne["attached_to_doctype"], ligne["attached_to_name"]))
        else:
            inserer("tabFile", ligne, bilan)
            bilan.note("fiche Fichier créée")


# ── 5. Historique visible sur les fiches (motifs des créations et des retouches) ──
def remettre_commentaires(d, bilan):
    for l in d.get("commentaires", []):
        if existe("tabComment", l["name"]):
            bilan.note("commentaire déjà présent")
        elif not frappe.db.exists(l["reference_doctype"], l["reference_name"]):
            bilan.note("commentaire sans fiche (ignoré)")
        else:
            inserer("tabComment", l, bilan)
            bilan.note("commentaire remis")


# ── 6. Transformation de stock ───────────────────────────────────────────────
def stock(article, entrepot=None):
    cond, args = ("and warehouse=%s", (article, entrepot)) if entrepot else ("", (article,))
    return flt(frappe.db.sql("select sum(actual_qty) from tabBin where item_code=%s " + cond, args)[0][0])


def transformer(d, bilan):
    """Rejoue chaque transformation faite sur le dev, sur le stock réellement présent ici."""
    from customization_app import transformation_articles as T
    for t in d.get("transformations", []):
        de, vers, entrepot = t["de"], t["vers"], t["entrepot"]
        qte = stock(de, entrepot) if t.get("tout_le_stock") else min(flt(t["qte"]), stock(de, entrepot))
        libelle = "%s → %s (%s)" % (de, vers, entrepot)
        if qte <= 0:
            bilan.note("transformation sans stock (rien à faire)")
            continue
        if qte != flt(t["qte"]):
            bilan.alerte("Transformation %s : %s en stock ici au lieu de %s sur le dev — %s transformés" % (libelle, qte, flt(t["qte"]), qte))
        frappe.db.savepoint("catalogue_transfo")
        try:
            r = T.valider([{"item_code": de, "qty": qte, "warehouse": entrepot}],
                          [{"item_code": vers, "qty": qte, "warehouse": entrepot}],
                          warehouse=entrepot, remarque="mise en prod du catalogue adoucisseurs")
            bilan.note("transformation faite")
            bilan.alerte("Transformation %s : %s pièce(s), écriture %s (information)" % (libelle, qte, r["name"]))
        except Exception as e:
            frappe.db.rollback(save_point="catalogue_transfo")
            frappe.clear_messages()
            bilan.note("transformation refusée")
            bilan.alerte("Transformation %s : refusée (%s) — à faire à la main dans « Transformation d'articles »" % (libelle, str(e)[:300]))


# ── 7. Anciens articles désactivés ───────────────────────────────────────────
def commandes_en_attente(article):
    return frappe.db.sql_list("""select distinct so.name from `tabSales Order Item` l join `tabSales Order` so on so.name = l.parent
        where l.item_code = %s and so.docstatus = 1 and so.status not in ('Completed', 'Closed') and l.qty > l.delivered_qty""", (article,))


def brouillons(article):
    """Commandes et devis encore en brouillon, ou devis ouverts : ils ne pourront plus être validés avec cet article."""
    return frappe.db.sql_list("""select distinct p.name from `tabSales Order Item` l join `tabSales Order` p on p.name = l.parent
                                 where l.item_code = %s and p.docstatus = 0""", (article,)) + \
        frappe.db.sql_list("""select distinct p.name from `tabQuotation Item` l join `tabQuotation` p on p.name = l.parent
                              where l.item_code = %s and (p.docstatus = 0 or (p.docstatus = 1 and p.status = 'Open'))""", (article,))


def desactiver(d, bilan):
    for nom in d.get("a_desactiver", []):
        if not existe("tabItem", nom):
            bilan.alerte("Article %s : à désactiver mais absent" % nom)
            continue
        if frappe.db.get_value("Item", nom, "disabled"):
            bilan.note("article déjà désactivé")
            continue
        reste, commandes = stock(nom), commandes_en_attente(nom)
        if reste:
            bilan.note("article laissé actif")
            bilan.alerte("Article %s : laissé ACTIF, il lui reste %s en stock" % (nom, reste))
        elif commandes:
            bilan.note("article laissé actif")
            bilan.alerte("Article %s : laissé ACTIF, commande(s) à livrer : %s — à désactiver à la main après la livraison" % (nom, ", ".join(commandes)))
        else:
            frappe.db.sql("update tabItem set disabled=1 where name=%s", (nom,))
            bilan.note("article désactivé")
            en_cours = brouillons(nom)
            if en_cours:
                bilan.alerte("Article %s : désactivé ; il figure encore sur des brouillons ou devis ouverts (%s) — à remplacer "
                             "par le nouvel article avant de les valider" % (nom, ", ".join(en_cours[:12])))


# ── Tout, dans l'ordre ───────────────────────────────────────────────────────
def appliquer(d, dossier=DOSSIER):
    bilan = Bilan()
    manquants = liens_manquants(d)
    if manquants:
        bilan.alerte("Rien n'a été fait : des fiches citées par les nouveaux articles n'existent pas ici : " + ", ".join(manquants))
        return bilan
    renommer(d, bilan)                 # d'abord : tout le reste parle des articles sous leur nouveau code
    creer_attributs(d, bilan)
    creer_groupes(d, bilan)
    creer_articles(d, bilan)
    modifier("tabItem", d.get("articles_a_modifier", []), bilan, "article existant")
    creer_prix(d, bilan)
    modifier("tabItem Price", d.get("prix_a_modifier", []), bilan, "prix existant")
    remettre_fichiers(d, bilan, dossier)
    remettre_commentaires(d, bilan)
    frappe.clear_cache()               # les articles insérés doivent être vus par l'outil de transformation
    transformer(d, bilan)              # avant de désactiver : une écriture de stock refuse un article désactivé
    desactiver(d, bilan)
    return bilan


def execute():
    chemin = os.path.join(DOSSIER, "donnees.json")
    if not os.path.isfile(chemin):
        return
    with open(chemin, encoding="utf-8") as f:
        d = json.load(f)
    try:
        bilan = appliquer(d)
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title=TITRE_ERREUR, message=frappe.get_traceback())
        return
    texte = "Données générées le %s (prod relevée le %s).\n\n%s" % (d.get("genere_le"), d.get("prod_lue_le"), bilan.texte())
    print(texte)
    frappe.log_error(title=TITRE_BILAN, message=texte)
    frappe.clear_cache()
    try:        # les articles insérés en lignes brutes ne sont pas dans la recherche générale : on la refait pour les articles
        frappe.enqueue("frappe.utils.global_search.rebuild_for_doctype", doctype="Item", enqueue_after_commit=True)
    except Exception:
        pass
