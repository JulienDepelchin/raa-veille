import re
import base64
import io
import pdfplumber
import pypdfium2 as pdfium


# Regex pour détecter une entrée du sommaire :
# "... (N pages) Page X" ou "... (1 page) Page X"
SOMMAIRE_RE = re.compile(
    r"(.+?)\s*\((\d+)\s+pages?\)\s+Page\s+(\d+)",
    re.DOTALL,
)

# Regex pour détecter un identifiant d'acte type "2026-03-12-00015"
ID_ACTE_RE = re.compile(r"\d{4}-\d{2}-\d{2}-\d{5}")

# Seuil : si moins de N caractères de texte par page, on considère la page image
SEUIL_TEXTE_CHARS = 200


def _page_est_image(page) -> bool:
    """Retourne True si la page contient principalement des images (peu de texte)."""
    nb_chars = len(page.chars)
    nb_images = len(page.images)
    return nb_chars < SEUIL_TEXTE_CHARS and nb_images > 0


def rendre_page_en_base64(chemin_pdf: str, numero_page: int, dpi: int = 150) -> str:
    """
    Rend la page `numero_page` (1-indexée) en PNG et retourne le base64.
    Utilisé pour envoyer la page à Claude Vision.
    """
    doc = pdfium.PdfDocument(chemin_pdf)
    page = doc[numero_page - 1]
    scale = dpi / 72  # PDFium utilise 72 DPI par défaut
    bitmap = page.render(scale=scale, rotation=0)
    pil_image = bitmap.to_pil()

    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    buffer.seek(0)
    return base64.standard_b64encode(buffer.read()).decode("utf-8")


def extraire_texte_pages(pdf, chemin_pdf: str, debut: int, nb_pages: int) -> dict:
    """
    Extrait le contenu de `nb_pages` pages à partir de `debut` (1-indexé).
    Retourne un dict :
      - "texte" : texte brut si les pages sont textuelles
      - "images_b64" : liste de base64 PNG si les pages sont des images
      - "mode" : "texte" ou "image"
    """
    textes = []
    images_b64 = []
    mode = "texte"

    for i in range(debut, debut + nb_pages):
        if i <= len(pdf.pages):
            page = pdf.pages[i - 1]
            if _page_est_image(page):
                mode = "image"
                images_b64.append(rendre_page_en_base64(chemin_pdf, i))
            else:
                t = page.extract_text()
                if t:
                    textes.append(t)

    if mode == "image":
        return {"mode": "image", "texte": "", "images_b64": images_b64}
    else:
        return {"mode": "texte", "texte": "\n".join(textes), "images_b64": []}


def _nettoyer_titre(texte_brut: str) -> str:
    """Retire les sauts de ligne internes et espaces multiples d'un titre."""
    return re.sub(r"\s+", " ", texte_brut.replace("\n", " ")).strip()


def parser_sommaire(texte_sommaire: str) -> list[dict]:
    """
    Parse le texte du sommaire et retourne une liste de dicts :
    [{"titre": str, "nb_pages": int, "page_debut": int}, ...]
    """
    entrees = []

    for match in SOMMAIRE_RE.finditer(texte_sommaire):
        titre_brut = match.group(1)
        nb_pages = int(match.group(2))
        page_debut = int(match.group(3))

        titre = _nettoyer_titre(titre_brut)
        titre = re.sub(r"^Sommaire\s*", "", titre)

        id_match = ID_ACTE_RE.search(titre)
        if id_match:
            apres_id = titre[id_match.end():].strip().lstrip("-").strip()
            intitule = apres_id if apres_id else titre
        else:
            intitule = titre

        entrees.append({
            "titre": intitule,
            "titre_complet": titre,
            "nb_pages": nb_pages,
            "page_debut": page_debut,
        })

    return entrees


def extraire_actes_depuis_pdf(chemin_pdf: str) -> list[dict]:
    """
    Pipeline complet : PDF → parse sommaire → extrait le contenu de chaque acte.
    Retourne une liste de dicts avec :
      - titre, titre_complet, page_debut, nb_pages
      - mode : "texte" ou "image"
      - texte : texte brut (si mode texte)
      - images_b64 : liste de PNG base64 (si mode image)
    """
    with pdfplumber.open(chemin_pdf) as pdf:
        nb_total = len(pdf.pages)

        # -- 1. Trouver la/les pages du sommaire
        texte_sommaire = ""
        for i in range(1, min(4, nb_total)):
            t = pdf.pages[i].extract_text() or ""
            if "Sommaire" in t or SOMMAIRE_RE.search(t):
                texte_sommaire += "\n" + t

        if not texte_sommaire.strip():
            return _extraction_naive(pdf, chemin_pdf)

        # -- 2. Parser le sommaire
        entrees = parser_sommaire(texte_sommaire)
        if not entrees:
            return _extraction_naive(pdf, chemin_pdf)

        # -- 3. Extraire le contenu de chaque acte
        actes = []
        for entree in entrees:
            contenu = extraire_texte_pages(
                pdf, chemin_pdf, entree["page_debut"], entree["nb_pages"]
            )
            actes.append({
                "titre": entree["titre"],
                "titre_complet": entree["titre_complet"],
                "page_debut": entree["page_debut"],
                "nb_pages": entree["nb_pages"],
                **contenu,
            })

    return actes


def _extraction_naive(pdf, chemin_pdf: str) -> list[dict]:
    """
    Fallback : concatène tout le texte et découpe sur les mots-clés d'actes.
    """
    MOTS_CLES = ["ARRÊTÉ", "ARRETE", "DÉCISION", "DECISION",
                 "HABILITATION", "DÉLÉGATION", "DELEGATION", "NOMINATION"]
    pattern = re.compile(
        r"^(?:" + "|".join(MOTS_CLES) + r")\b",
        re.MULTILINE | re.IGNORECASE,
    )

    texte_total = "\n".join(p.extract_text() or "" for p in pdf.pages)
    matches = list(pattern.finditer(texte_total))

    if not matches:
        return [{
            "titre": "Document complet",
            "titre_complet": "Document complet",
            "page_debut": 1,
            "nb_pages": len(pdf.pages),
            "mode": "texte",
            "texte": texte_total.strip(),
            "images_b64": [],
        }]

    actes = []
    for i, m in enumerate(matches):
        debut = m.start()
        fin = matches[i + 1].start() if i + 1 < len(matches) else len(texte_total)
        bloc = texte_total[debut:fin].strip()
        actes.append({
            "titre": bloc.split("\n")[0].strip(),
            "titre_complet": bloc.split("\n")[0].strip(),
            "page_debut": None,
            "nb_pages": None,
            "mode": "texte",
            "texte": bloc,
            "images_b64": [],
        })

    return actes
