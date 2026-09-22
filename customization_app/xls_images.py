"""Les images d'un classeur .xls (BIFF8), avec leur cellule d'ancrage.

xlrd ne lit que les valeurs : quand on reconstruit en .xlsx le fichier d'un
fournisseur, ses photos disparaissent (constaté le 22/09/2026 sur la facture
Ang Ran : 103 JPEG + 9 PNG perdus). Il n'y a ni LibreOffice sur le serveur ni
bibliothèque Python qui fasse la conversion avec les images. On lit donc
nous-mêmes les deux endroits où Excel les range :

  - dans le flux « Workbook », les enregistrements MSODRAWINGGROUP (0x00EB,
    + CONTINUE) : le magasin d'images (BStoreContainer 0xF001 → FBSE 0xF007 →
    BLIP), indexé à partir de 1 ;
  - dans chaque feuille, les MSODRAWING (0x00EC, + CONTINUE) : une forme par
    image (SpContainer 0xF004) avec ses propriétés (OPT 0xF00B, la propriété
    0x0104 « pib » = index dans le magasin) et son ancrage (ClientAnchor
    0xF010 : colonne/ligne de début et de fin).

Formats de référence : [MS-XLS] et [MS-ODRAW]. Tout est pur (octets → dicts),
testé sur des flux fabriqués à la main.
"""

from __future__ import annotations

import struct

BOF = 0x0809
CONTINUE = 0x003C
MSODRAWINGGROUP = 0x00EB
MSODRAWING = 0x00EC
BOUNDSHEET = 0x0085

ESCHER_BSTORE = 0xF001
ESCHER_FBSE = 0xF007
ESCHER_SP_CONTAINER = 0xF004
ESCHER_OPT = 0xF00B
ESCHER_CLIENT_ANCHOR = 0xF010
BLIP_MIN, BLIP_MAX = 0xF018, 0xF117
PROP_PIB = 0x0104

MAGIES = ((b"\xff\xd8\xff", "jpeg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF8", "gif"), (b"BM", "bmp"))


# ------------------------------------------------------------ BIFF


def enregistrements(flux: bytes):
    """Itère (id, données) sur un flux BIFF."""
    pos, n = 0, len(flux)
    while pos + 4 <= n:
        rid, taille = struct.unpack_from("<HH", flux, pos)
        pos += 4
        yield rid, flux[pos:pos + taille]
        pos += taille


def dessins_par_feuille(flux: bytes) -> tuple[bytes, list[bytes]]:
    """-> (magasin d'images global, [données de dessin de chaque feuille, dans l'ordre
    des BOF]). Les CONTINUE qui suivent un enregistrement de dessin lui appartiennent."""
    groupe, feuilles = [], []
    courant = None          # liste qui reçoit les CONTINUE en cours
    bofs = 0
    for rid, data in enregistrements(flux):
        if rid == BOF:
            bofs += 1
            if bofs > 1:
                feuilles.append([])
            courant = None
        elif rid == MSODRAWINGGROUP:
            groupe.append(data)
            courant = groupe
        elif rid == MSODRAWING and feuilles:
            feuilles[-1].append(data)
            courant = feuilles[-1]
        elif rid == CONTINUE and courant is not None:
            courant.append(data)
        else:
            courant = None
    return b"".join(groupe), [b"".join(f) for f in feuilles]


# ------------------------------------------------------------ Escher


def escher(flux: bytes, pos: int = 0, fin: int | None = None):
    """Itère (type, instance, version, données, position) sur des enregistrements Escher."""
    fin = len(flux) if fin is None else fin
    while pos + 8 <= fin:
        verinst, rtype, taille = struct.unpack_from("<HHI", flux, pos)
        version, instance = verinst & 0xF, verinst >> 4
        debut = pos + 8
        yield rtype, instance, version, flux[debut:min(debut + taille, fin)], debut
        if version == 0xF:           # conteneur : on descend dedans (ses enfants suivent l'en-tête)
            pos = debut
        else:
            pos = debut + taille


def _image_dans(data: bytes) -> tuple[str, bytes] | None:
    """Le fichier image contenu dans un BLIP : on part de la première signature connue."""
    meilleur = None
    for magie, genre in MAGIES:
        i = data.find(magie, 0, 80)
        if i >= 0 and (meilleur is None or i < meilleur[0]):
            meilleur = (i, genre)
    if meilleur is None:
        return None
    return meilleur[1], data[meilleur[0]:]


def magasin(groupe: bytes) -> list:
    """Les images du magasin, dans l'ordre (index pib = position + 1) ;
    None pour une entrée sans image lisible (référence externe, format inconnu)."""
    images = []
    for rtype, _inst, _ver, data, _pos in escher(groupe):
        if rtype != ESCHER_FBSE:
            continue
        image = None
        for btype, _i, _v, bdata, _p in escher(data, 36):
            if BLIP_MIN <= btype <= BLIP_MAX:
                image = _image_dans(bdata)
                break
        images.append(image)
    return images


def _proprietes(data: bytes) -> dict:
    props, pos = {}, 0
    while pos + 6 <= len(data):
        pid, val = struct.unpack_from("<HI", data, pos)
        props[pid & 0x3FFF] = val
        pos += 6
    return props


def formes(dessin: bytes) -> list[dict]:
    """Les formes ancrées d'une feuille : [{pib, col, row, col2, row2}] (0-based)."""
    sorties = []
    for rtype, _inst, _ver, data, pos in escher(dessin):
        if rtype != ESCHER_SP_CONTAINER:
            continue
        forme = {}
        for ctype, _i, _v, cdata, _p in escher(dessin, pos, pos + len(data)):
            if ctype == ESCHER_OPT:
                forme["pib"] = _proprietes(cdata).get(PROP_PIB)
            elif ctype == ESCHER_CLIENT_ANCHOR and len(cdata) >= 18:
                # dx en 1/1024 de la largeur de colonne, dy en 1/256 de la hauteur de ligne
                _flags, c1, dx1, r1, dy1, c2, dx2, r2, dy2 = struct.unpack_from("<9H", cdata)
                forme.update({"col": c1, "row": r1, "col2": c2, "row2": r2,
                              "dx1": dx1, "dy1": dy1, "dx2": dx2, "dy2": dy2})
        if forme.get("pib") and "row" in forme:
            sorties.append(forme)
    return sorties


def boite_pixels(forme: dict, largeurs_px: list, hauteurs_px: list,
                 largeur_defaut: float = 64.0, hauteur_defaut: float = 20.0) -> tuple:
    """La boîte (x, y, w, h en pixels, depuis le coin de la cellule de départ) qu'occupe une
    forme : x/y = décalage dans la cellule de départ, w/h = somme des cellules traversées
    plus les décalages. Pur."""
    def largeur(c):
        return largeurs_px[c] if c < len(largeurs_px) and largeurs_px[c] else largeur_defaut

    def hauteur(r):
        return hauteurs_px[r] if r < len(hauteurs_px) and hauteurs_px[r] else hauteur_defaut

    x = largeur(forme["col"]) * forme.get("dx1", 0) / 1024.0
    y = hauteur(forme["row"]) * forme.get("dy1", 0) / 256.0
    fin_x = sum(largeur(c) for c in range(forme["col"], forme["col2"])) + largeur(forme["col2"]) * forme.get("dx2", 0) / 1024.0
    fin_y = sum(hauteur(r) for r in range(forme["row"], forme["row2"])) + hauteur(forme["row2"]) * forme.get("dy2", 0) / 256.0
    return (round(x, 1), round(y, 1), round(max(1.0, fin_x - x), 1), round(max(1.0, fin_y - y), 1))


def largeur_colonne_px(largeur_xls) -> float:
    """Largeur xlrd (1/256 de caractère) -> pixels (≈ 7 px par caractère + 5 de marge)."""
    return round((largeur_xls or 0) / 256.0 * 7 + 5, 1) if largeur_xls else 64.0


def hauteur_ligne_px(hauteur_twips) -> float:
    """Hauteur xlrd (twips = 1/20 de point) -> pixels à 96 dpi."""
    return round((hauteur_twips or 0) / 20.0 * 96 / 72, 1) if hauteur_twips else 20.0


# ------------------------------------------------------------ point d'entrée


def images_depuis_flux(flux: bytes) -> list[list[dict]]:
    """Flux « Workbook » -> par feuille (ordre du fichier) : [{row, col, row2, col2, dx1, dy1,
    dx2, dy2, type, data}] (0-based). Pur. ⚠️ Les décalages dx/dy font partie de l'ancrage :
    sans eux, une image logée dans une seule cellule a une boîte de 0 px (vu le 22/09/2026)."""
    groupe, feuilles = dessins_par_feuille(flux)
    store = magasin(groupe) if groupe else []
    resultat = []
    for dessin in feuilles:
        images = []
        for f in formes(dessin) if dessin else []:
            entree = store[f["pib"] - 1] if 0 < f["pib"] <= len(store) else None
            if not entree:
                continue
            genre, data = entree
            image = {k: f.get(k, 0) for k in ("row", "col", "row2", "col2", "dx1", "dy1", "dx2", "dy2")}
            image.update({"type": genre, "data": data})
            images.append(image)
        resultat.append(images)
    return resultat


def images_du_classeur(xls: bytes) -> list[list[dict]]:
    """Fichier .xls (conteneur OLE) -> voir images_depuis_flux."""
    import io

    import olefile

    ole = olefile.OleFileIO(io.BytesIO(xls))
    nom = "Workbook" if ole.exists("Workbook") else "Book"
    return images_depuis_flux(ole.openstream(nom).read())
