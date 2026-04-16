"""
scraper.py — Détection et téléchargement des nouveaux RAA
  Dept 59 (Nord)          : page mensuelle, URL construite automatiquement
  Dept 62 (Pas-de-Calais) : page annuelle unique

Modes de filtrage disponibles :
  filter_mode="7jours"   → uniquement les PDFs publiés dans les 7 derniers jours
                           (production / GitHub Actions)
  filter_mode="n_recents" + n=3  → les N PDFs les plus récents non encore vus
                           (test / rattrapage ponctuel)
  filter_mode="tous"     → tous les PDFs non encore vus
"""

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

BASE_59 = "https://www.nord.gouv.fr"
BASE_62 = "https://www.pas-de-calais.gouv.fr"

URL_59_TEMPLATE = (
    "https://www.nord.gouv.fr/Publications/Recueils-des-actes-administratifs"
    "/RAA-du-departement-du-Nord/{annee}/{mois}"
)
URL_62 = (
    "https://www.pas-de-calais.gouv.fr/Publications/Recueil-des-actes-administratifs"
    "/2026-Recueils-des-actes-administratifs"
)

PDF_DIR      = Path("pdfs_downloaded")
DEJA_VUS_TXT = Path("data/pdfs_deja_vus.txt")

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

PAUSE_DOWNLOAD = 2   # secondes entre deux téléchargements


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


# ── Helpers réseau ────────────────────────────────────────────────────────────

def charger_deja_vus() -> set[str]:
    if not DEJA_VUS_TXT.exists():
        return set()
    return {l.strip() for l in DEJA_VUS_TXT.read_text(encoding="utf-8").splitlines() if l.strip()}


def enregistrer_deja_vu(nom_fichier: str) -> None:
    DEJA_VUS_TXT.parent.mkdir(parents=True, exist_ok=True)
    with DEJA_VUS_TXT.open("a", encoding="utf-8") as f:
        f.write(nom_fichier + "\n")


def get_page(url: str) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        return r if r.status_code == 200 else None
    except requests.RequestException as e:
        print(f"  [ERREUR reseau] {e}")
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


def telecharger_pdf(url: str, nom: str) -> bool:
    dest = PDF_DIR / nom
    try:
        r = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        r.raise_for_status()
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        taille_mo = dest.stat().st_size / 1_048_576
        print(f"    OK  {nom}  ({taille_mo:.1f} Mo)")
        return True
    except Exception as e:
        print(f"    ECHEC  {nom} : {e}")
        return False


# ── Scraping par département ──────────────────────────────────────────────────

def url_nord_mois(annee: int, mois: int) -> str:
    return URL_59_TEMPLATE.format(annee=annee, mois=MOIS_FR_URL[mois])


def scraper_nord() -> list[dict]:
    """Page du mois courant, repli sur le mois précédent si 404."""
    aujourd_hui = date.today()
    annee, mois = aujourd_hui.year, aujourd_hui.month

    url = url_nord_mois(annee, mois)
    print(f"  Tentative : {url}")
    r = get_page(url)

    if r is None:
        premier = aujourd_hui.replace(day=1)
        precedent = premier - timedelta(days=1)
        annee, mois = precedent.year, precedent.month
        url = url_nord_mois(annee, mois)
        print(f"  Repli mois precedent : {url}")
        r = get_page(url)

    if r is None:
        print("  Impossible d'acceder a la page Nord.")
        return []

    print(f"  Status 200 — {MOIS_FR_URL[mois]} {annee}")
    pdfs = extraire_pdfs(r.text, BASE_59)
    print(f"  {len(pdfs)} PDF(s) trouves")
    return pdfs


def scraper_pdc() -> list[dict]:
    """Page annuelle du Pas-de-Calais."""
    print(f"  URL : {URL_62}")
    r = get_page(URL_62)
    if r is None:
        print("  Impossible d'acceder a la page Pas-de-Calais.")
        return []
    pdfs = extraire_pdfs(r.text, BASE_62)
    print(f"  {len(pdfs)} PDF(s) trouves (apres dedup)")
    return pdfs


# ── Filtres ───────────────────────────────────────────────────────────────────

def filtrer_nouveaux(pdfs: list[dict], deja_vus: set[str]) -> list[dict]:
    return [p for p in pdfs if p["nom"] not in deja_vus]


def filtrer_7_jours(pdfs: list[dict]) -> list[dict]:
    """
    Garde uniquement les PDFs dont la date extraite du nom
    est dans les 7 derniers jours. Si la date est introuvable,
    le PDF est conservé par précaution (ne pas rater un acte).
    """
    limite = date.today() - timedelta(days=7)
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
    filter_mode: str = "7jours",   # "7jours" | "n_recents" | "tous"
    n_recents: int = 3,
) -> dict:
    """
    Détecte et optionnellement télécharge les nouveaux PDFs.

    filter_mode :
      "7jours"    → PDFs des 7 derniers jours (production)
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
             "erreurs": 0, "nouveaux": []}

    for src in sources_brutes:
        dept = src["dept"]
        tous_nouveaux = filtrer_nouveaux(src["pdfs"], deja_vus)
        connus = len(src["pdfs"]) - len(tous_nouveaux)
        stats["deja_connus"] += connus

        # Appliquer le filtre temporel / quantitatif
        if filter_mode == "7jours":
            a_traiter = filtrer_7_jours(tous_nouveaux)
        elif filter_mode == "n_recents":
            a_traiter = filtrer_n_recents(tous_nouveaux, n_recents)
        else:  # "tous"
            a_traiter = tous_nouveaux

        ignores = len(tous_nouveaux) - len(a_traiter)
        stats["ignores_filtre"] += ignores

        print(f"\n  [{dept}] {src['label']}")
        print(f"  Total : {len(src['pdfs'])}  |  Deja connus : {connus}  "
              f"|  Nouveaux bruts : {len(tous_nouveaux)}  |  Apres filtre : {len(a_traiter)}"
              + (f"  |  Ignores : {ignores}" if ignores else ""))

        if not a_traiter:
            print("  -> Rien a telecharger.")
            continue

        for p in a_traiter:
            date_str = p["date_pdf"].strftime("%d/%m/%Y") if p.get("date_pdf") else "date inconnue"
            print(f"    + [{date_str}] {p['nom']}")
            stats["nouveaux"].append({"dept": dept, **p})

            if not simulation:
                ok = telecharger_pdf(p["url"], p["nom"])
                if ok:
                    enregistrer_deja_vu(p["nom"])
                    stats["telecharges"] += 1
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
    print("=" * 60)

    return stats


# ── Entrée ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # --download dans les arguments = téléchargement direct, aucune interaction
    # Sans --download = simulation seule
    _download = "--download" in sys.argv
    _positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    _mode = _positional[0] if _positional else "7jours"
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
