"""
scraper.py — Détection et téléchargement des nouveaux RAA
  Dept 59 (Nord)          : page mensuelle, URL construite automatiquement
  Dept 62 (Pas-de-Calais) : page annuelle unique

Modes de filtrage disponibles :
  filter_mode="14jours"  → filtre principal : pdfs_deja_vus.txt
                           tout PDF non encore vu est téléchargé, quelle que
                           soit sa date. La fenêtre 14 jours ne s'applique
                           qu'à titre indicatif (marge entre deux runs hebdo).
                           (production / GitHub Actions)
  filter_mode="n_recents" + n=3  → les N PDFs les plus récents non encore vus
                           (test / rattrapage ponctuel)
  filter_mode="tous"     → tous les PDFs non encore vus
"""

import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, unquote

import requests
from bs4 import BeautifulSoup

# ── Constantes ────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}

HEADERS_PDF = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Referer": "https://www.nord.gouv.fr/",
    "Connection": "keep-alive",
}

BASE_59 = "https://www.nord.gouv.fr"
BASE_62 = "https://www.pas-de-calais.gouv.fr"

URL_59_TEMPLATE = (
    "https://www.nord.gouv.fr/Publications/Recueils-des-actes-administratifs"
    "/RAA-du-departement-du-Nord/{annee}/{mois}"
)
URL_62_TEMPLATE = (
    "https://www.pas-de-calais.gouv.fr/Publications/Recueil-des-actes-administratifs"
    "/{annee}-Recueils-des-actes-administratifs"
)


def url_pdc_annee(annee: int) -> str:
    return URL_62_TEMPLATE.format(annee=annee)

PDF_DIR       = Path("pdfs_downloaded")
DEJA_VUS_TXT  = Path("data/pdfs_deja_vus.txt")
NOUVEAUX_TXT  = Path("data/pdfs_nouveaux.txt")

MOIS_FR_URL = {
    1: "Janvier", 2: "Fevrier",  3: "Mars",     4: "Avril",
    5: "Mai",     6: "Juin",     7: "Juillet",  8: "Aout",
    9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Decembre",
}

# Mois en toutes lettres présents dans les noms de fichiers (accents inclus)
MOIS_FR_NOM = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
}

PAUSE_DOWNLOAD = 3   # secondes entre deux téléchargements
RETRY_DELAY    = 10  # secondes d'attente avant retry RemoteDisconnected


# ── Parsing de date dans un nom de fichier ────────────────────────────────────

def extraire_date_nom(nom: str) -> date | None:
    """
    Tente d'extraire une date depuis un nom de fichier PDF.
    Supporte :
      "... du 15 avril 2026 ..."
      "... du 15 avril 2026.pdf"
      "... du 1er avril 2026 ..."
    Retourne None si non trouvé.
    """
    # Pattern : "du 15 avril 2026" ou "du 1er avril 2026"
    pattern = re.compile(
        r"du\s+(\d{1,2})(?:er|ème)?\s+([a-záàâäéèêëîïôùûüœ]+)\s+(\d{4})",
        re.IGNORECASE,
    )
    m = pattern.search(nom)
    if not m:
        return None
    jour, mois_str, annee = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    mois = MOIS_FR_NOM.get(mois_str)
    if not mois:
        return None
    try:
        return date(annee, mois, jour)
    except ValueError:
        return None


def date_recueil_str(nom: str) -> str | None:
    """
    Retourne la date du recueil au format 'YYYY-MM-DD', ou None.
    Exemple : 'Recueil n°141 du 16 avril 2026.pdf' → '2026-04-16'
    """
    d = extraire_date_nom(nom)
    return d.strftime("%Y-%m-%d") if d else None


# ── Helpers réseau ────────────────────────────────────────────────────────────

def charger_deja_vus() -> set[str]:
    if not DEJA_VUS_TXT.exists():
        return set()
    return {l.strip() for l in DEJA_VUS_TXT.read_text(encoding="utf-8").splitlines() if l.strip()}


def enregistrer_deja_vu(nom_fichier: str) -> None:
    DEJA_VUS_TXT.parent.mkdir(parents=True, exist_ok=True)
    with DEJA_VUS_TXT.open("a", encoding="utf-8") as f:
        f.write(nom_fichier + "\n")


PDF_URLS_JSON = Path("data/pdf_urls.json")


def charger_pdf_urls() -> dict[str, str]:
    if not PDF_URLS_JSON.exists():
        return {}
    with PDF_URLS_JSON.open(encoding="utf-8") as f:
        return json.load(f)


def enregistrer_pdf_url(nom_fichier: str, url: str) -> None:
    """Persiste la correspondance nom_fichier → URL complète."""
    mapping = charger_pdf_urls()
    mapping[nom_fichier] = url
    PDF_URLS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with PDF_URLS_JSON.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def get_page(url: str) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"  [HTTP] {url}  →  {r.status_code}")
        return r if r.status_code == 200 else None
    except requests.RequestException as e:
        print(f"  [ERREUR reseau] {url}  →  {e}")
        return None


def extraire_pdfs(html: str, base_url: str) -> list[dict]:
    """
    Retourne [{nom, url, label, date_pdf}, ...] sans doublons.
    date_pdf est un objet date ou None.
    """
    soup = BeautifulSoup(html, "html.parser")
    resultats = []
    vus = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        url_complete = href if href.startswith("http") else urljoin(base_url, href)
        nom = unquote(url_complete.split("/")[-1])
        if nom in vus:
            continue
        vus.add(nom)
        resultats.append({
            "nom":      nom,
            "url":      url_complete,
            "label":    a.get_text(strip=True)[:100],
            "date_pdf": extraire_date_nom(nom),
        })
    return resultats


def _telecharger_une_fois(url: str, dest: Path) -> None:
    """Télécharge url → dest. Lève une exception en cas d'échec."""
    r = requests.get(url, headers=HEADERS_PDF, timeout=60, stream=True)
    r.raise_for_status()
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)


def telecharger_pdf(url: str, nom: str) -> bool:
    from http.client import RemoteDisconnected
    dest = PDF_DIR / nom
    for tentative in range(2):
        try:
            _telecharger_une_fois(url, dest)
            taille_mo = dest.stat().st_size / 1_048_576
            print(f"    OK  {nom}  ({taille_mo:.1f} Mo)")
            return True
        except RemoteDisconnected as e:
            if tentative == 0:
                print(f"    RemoteDisconnected — retry dans {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"    ECHEC (RemoteDisconnected x2)  {nom} : {e}")
                return False
        except Exception as e:
            print(f"    ECHEC  {nom} : {e}")
            return False
    return False


# ── Scraping par département ──────────────────────────────────────────────────

def url_nord_mois(annee: int, mois: int) -> str:
    return URL_59_TEMPLATE.format(annee=annee, mois=MOIS_FR_URL[mois])


def _mois_precedent(annee: int, mois: int) -> tuple[int, int]:
    """Retourne (annee, mois) du mois précédent, avec gestion janvier → décembre N-1."""
    premier = date(annee, mois, 1)
    precedent = premier - timedelta(days=1)
    return precedent.year, precedent.month


def scraper_nord() -> list[dict]:
    """
    Page du mois courant ; repli jusqu'à 3 mois en arrière si 404.
    Gère correctement le changement d'année (janvier → décembre N-1).
    """
    aujourd_hui = date.today()
    annee, mois = aujourd_hui.year, aujourd_hui.month

    for tentative in range(3):
        url = url_nord_mois(annee, mois)
        label = "Tentative 0" if tentative == 0 else f"Repli -{tentative} mois"
        print(f"  [{label}] URL : {url}")
        r = get_page(url)
        if r is not None:
            pdfs = extraire_pdfs(r.text, BASE_59)
            print(f"  Liens PDF trouves sur la page : {len(pdfs)}")
            for p in pdfs[:3]:
                print(f"    - {p['nom']}")
            if len(pdfs) > 3:
                print(f"    ... ({len(pdfs) - 3} autres)")
            return pdfs
        annee, mois = _mois_precedent(annee, mois)

    print("  Impossible d'acceder a la page Nord (3 tentatives).")
    return []


def scraper_pdc() -> list[dict]:
    """
    Page annuelle du Pas-de-Calais.
    L'année est construite dynamiquement ; fallback sur l'année précédente
    si la page de l'année courante est introuvable (début janvier).
    """
    annee = date.today().year
    for tentative in range(2):
        url = url_pdc_annee(annee)
        print(f"  [Tentative {tentative}] URL : {url}")
        r = get_page(url)
        if r is not None:
            pdfs = extraire_pdfs(r.text, BASE_62)
            print(f"  Liens PDF trouves sur la page : {len(pdfs)}")
            for p in pdfs[:3]:
                print(f"    - {p['nom']}")
            if len(pdfs) > 3:
                print(f"    ... ({len(pdfs) - 3} autres)")
            return pdfs
        annee -= 1

    print("  Impossible d'acceder a la page Pas-de-Calais.")
    return []


# ── Filtres ───────────────────────────────────────────────────────────────────

def filtrer_nouveaux(pdfs: list[dict], deja_vus: set[str]) -> list[dict]:
    return [p for p in pdfs if p["nom"] not in deja_vus]


def filtrer_14_jours(pdfs: list[dict]) -> list[dict]:
    """
    Garde uniquement les PDFs dont la date extraite du nom est dans les
    14 derniers jours. Si la date est introuvable, le PDF est conservé
    par précaution.

    Note : dans le pipeline "14jours", pdfs_deja_vus.txt est le filtre
    principal — cette fonction n'est donc jamais appelée sur des PDFs déjà
    filtrés par filtrer_nouveaux() (qui sont par définition tous non vus).
    Elle reste disponible pour des usages ponctuels.
    """
    limite = date.today() - timedelta(days=14)
    retenus = []
    for p in pdfs:
        d = p.get("date_pdf")
        if d is None or d >= limite:
            retenus.append(p)
    return retenus


def filtrer_n_recents(pdfs: list[dict], n: int) -> list[dict]:
    """
    Garde les N PDFs les plus récents (basé sur la date extraite du nom).
    Les PDFs sans date sont mis en fin de liste.
    """
    avec_date    = [p for p in pdfs if p.get("date_pdf")]
    sans_date    = [p for p in pdfs if not p.get("date_pdf")]
    avec_date.sort(key=lambda p: p["date_pdf"], reverse=True)
    ordonnes = avec_date + sans_date
    return ordonnes[:n]


# ── Pipeline principal ────────────────────────────────────────────────────────

def pipeline(
    simulation: bool = True,
    filter_mode: str = "14jours",   # "14jours" | "n_recents" | "tous"
    n_recents: int = 3,
) -> dict:
    """
    Détecte et optionnellement télécharge les nouveaux PDFs.

    filter_mode :
      "14jours"   → filtre principal pdfs_deja_vus.txt : tout PDF non vu est
                    téléchargé quelle que soit sa date (production)
      "n_recents" → les N plus récents non vus  (test)
      "tous"      → tous les non vus            (rattrapage)
    """
    deja_vus = charger_deja_vus()
    print(f"PDFs deja connus : {len(deja_vus)}")
    print(f"Filtre : {filter_mode}" + (f" (n={n_recents})" if filter_mode == "n_recents" else ""))
    print()

    sources_brutes = []

    print("=" * 60)
    print("SCRAPING Nord (59)")
    print("=" * 60)
    pdfs_59 = scraper_nord()
    sources_brutes.append({"dept": "59", "label": "Nord (59)", "pdfs": pdfs_59})

    print()
    print("=" * 60)
    print("SCRAPING Pas-de-Calais (62)")
    print("=" * 60)
    pdfs_62 = scraper_pdc()
    sources_brutes.append({"dept": "62", "label": "Pas-de-Calais (62)", "pdfs": pdfs_62})

    print()
    print("=" * 60)
    label_mode = "SIMULATION" if simulation else "TELECHARGEMENT"
    print(f"{label_mode} — filtre : {filter_mode}")
    print("=" * 60)

    stats = {"telecharges": 0, "deja_connus": 0, "ignores_filtre": 0,
             "erreurs": 0, "nouveaux": [], "telecharges_noms": []}

    for src in sources_brutes:
        dept = src["dept"]
        tous_nouveaux = filtrer_nouveaux(src["pdfs"], deja_vus)
        connus = len(src["pdfs"]) - len(tous_nouveaux)
        stats["deja_connus"] += connus

        # Appliquer le filtre temporel / quantitatif
        if filter_mode == "14jours":
            if dept == "62":
                # PDC liste toute l'année : filtre 30 jours sur les non-vus.
                # Date inconnue → conservé par précaution.
                limite_pdc = date.today() - timedelta(days=30)
                a_traiter = [
                    p for p in tous_nouveaux
                    if p["date_pdf"] is None or p["date_pdf"] >= limite_pdc
                ]
            else:
                # Nord : page du mois courant seulement, pas de filtre date.
                a_traiter = tous_nouveaux
        elif filter_mode == "n_recents":
            a_traiter = filtrer_n_recents(tous_nouveaux, n_recents)
        else:  # "tous"
            a_traiter = tous_nouveaux

        ignores = len(tous_nouveaux) - len(a_traiter)
        stats["ignores_filtre"] += ignores

        print(f"\n  [{dept}] {src['label']}")
        print(f"  Total scrapes         : {len(src['pdfs'])}")
        print(f"  Dans pdfs_deja_vus    : {connus}")
        print(f"  Nouveaux (non vus)    : {len(tous_nouveaux)}")
        print(f"  A telecharger         : {len(a_traiter)}"
              + (f"  (ignores date>30j : {ignores})" if ignores else ""))

        # Diagnostic détaillé : raison pour chaque PDF scrappé
        limite_diag = date.today() - timedelta(days=30)
        print()
        for p in src["pdfs"]:
            nom = p["nom"]
            d = p.get("date_pdf")
            date_str = d.strftime("%Y-%m-%d") if d else "date_inconnue"
            if nom in deja_vus:
                print(f"    SKIP  deja_vu      [{date_str}]  {nom}")
            elif dept == "62" and d is not None and d < limite_diag:
                print(f"    SKIP  date>30j     [{date_str}]  {nom}")
            else:
                print(f"    KEEP  nouveau      [{date_str}]  {nom}")

        if not a_traiter:
            print("\n  -> Rien a telecharger.")
            continue

        print()
        for p in a_traiter:
            date_str = p["date_pdf"].strftime("%d/%m/%Y") if p.get("date_pdf") else "date inconnue"
            print(f"    + [{date_str}] {p['nom']}")
            stats["nouveaux"].append({"dept": dept, **p})

            if not simulation:
                ok = telecharger_pdf(p["url"], p["nom"])
                if ok:
                    enregistrer_deja_vu(p["nom"])
                    enregistrer_pdf_url(p["nom"], p["url"])
                    stats["telecharges"] += 1
                    stats["telecharges_noms"].append(p["nom"])
                    time.sleep(PAUSE_DOWNLOAD)
                else:
                    stats["erreurs"] += 1
            else:
                stats["telecharges"] += 1

    print()
    print("=" * 60)
    if simulation:
        print(f"SIMULATION — {stats['telecharges']} PDF(s) seraient telecharges")
    else:
        print(f"BILAN — {stats['telecharges']} telecharge(s)  |  "
              f"{stats['erreurs']} erreur(s)  |  {stats['deja_connus']} deja connus")
        # Écrire la liste des PDFs téléchargés ce run pour main.py
        NOUVEAUX_TXT.parent.mkdir(parents=True, exist_ok=True)
        if stats["telecharges_noms"]:
            NOUVEAUX_TXT.write_text(
                "\n".join(stats["telecharges_noms"]) + "\n", encoding="utf-8"
            )
            print(f"  -> {NOUVEAUX_TXT} mis a jour ({len(stats['telecharges_noms'])} entrees)")
        else:
            NOUVEAUX_TXT.write_text("", encoding="utf-8")
    print("=" * 60)

    return stats


# ── Entrée ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # --download dans les arguments = téléchargement direct, aucune interaction
    # Sans --download = simulation seule
    _download = "--download" in sys.argv
    _positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    _mode = _positional[0] if _positional else "14jours"
    _n = int(_positional[1]) if len(_positional) > 1 else 3

    if _download:
        print(f"Mode TELECHARGEMENT DIRECT — filtre={_mode}\n")
        _stats = pipeline(simulation=False, filter_mode=_mode, n_recents=_n)
        print(f"\nBilan : {_stats['telecharges']} telecharge(s), {_stats['erreurs']} erreur(s).")
    else:
        print(f"Mode SIMULATION — filtre={_mode}\n")
        _stats = pipeline(simulation=True, filter_mode=_mode, n_recents=_n)
        _nb = len(_stats["nouveaux"])
        if _nb == 0:
            print("\nAucun nouveau PDF.")
        else:
            print(f"\n{_nb} PDF(s) seraient telecharges.")
            print(f"Commande download : python scraper.py {_mode} --download")
