"""
main.py — Orchestration du pipeline RAA

Modes d'appel :
  python main.py                   → traite pdfs_downloaded/ (nouveaux non encore analysés)
  python main.py 59                → idem, uniquement dept 59
  python main.py 62                → idem, uniquement dept 62
  python main.py chemin/fichier.pdf dept  → analyse un PDF précis
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import MIN_SCORE_AFFICHE, OUTPUT_FILE
from extractor import extraire_actes_depuis_pdf
from scraper import date_recueil_str, charger_pdf_urls

_BASE         = Path(__file__).resolve().parent
PDF_DIR       = _BASE / "pdfs_downloaded"
ANALYSES_TXT  = _BASE / "data" / "pdfs_analyses.txt"
NOUVEAUX_TXT  = _BASE / "data" / "pdfs_nouveaux.txt"


# ── Détection du département depuis le nom de fichier ────────────────────────

def detecter_dept(nom_fichier: str) -> str:
    """
    Heuristique :
      "Recueil des actes administratifs …"  → 62
      "Recueil n°…" / "recueil-2026-…"     → 59
    """
    nom = nom_fichier.lower()
    if "des actes administratifs" in nom:
        return "62"
    return "59"


# ── Persistance des PDFs analysés ────────────────────────────────────────────

def charger_analyses() -> set[str]:
    if not ANALYSES_TXT.exists():
        return set()
    return {l.strip() for l in ANALYSES_TXT.read_text(encoding="utf-8").splitlines() if l.strip()}


def enregistrer_analyse(nom_fichier: str) -> None:
    ANALYSES_TXT.parent.mkdir(parents=True, exist_ok=True)
    with ANALYSES_TXT.open("a", encoding="utf-8") as f:
        f.write(nom_fichier + "\n")


# ── Chargement / fusion resultats.json ───────────────────────────────────────

def charger_resultats_existants() -> list[dict]:
    if not Path(OUTPUT_FILE).exists():
        return []
    with open(OUTPUT_FILE, encoding="utf-8") as f:
        return json.load(f)


def sauvegarder(resultats: list[dict], chemin: str = OUTPUT_FILE) -> None:
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)
    print(f"Resultats sauvegardes : {chemin}  ({len(resultats)} actes au total)")


# ── Pipeline par PDF ──────────────────────────────────────────────────────────

def charger_api_key() -> str:
    from dotenv import load_dotenv
    load_dotenv()
    cle = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not cle:
        print("[ERREUR] ANTHROPIC_API_KEY absent. Definissez la variable d'environnement ou creez un fichier .env")
        sys.exit(1)
    return cle


def etape_extraction(chemin_pdf: str, dept: str) -> list[dict]:
    print(f"\n{'='*65}")
    print(f"EXTRACTION  [{dept}]  {Path(chemin_pdf).name}")
    print("=" * 65)

    if not Path(chemin_pdf).exists():
        print(f"[ERREUR] Fichier introuvable : {chemin_pdf}")
        return []

    actes = extraire_actes_depuis_pdf(chemin_pdf)
    nb_img = sum(1 for a in actes if a.get("mode") == "image")
    nb_txt = sum(1 for a in actes if a.get("mode") == "texte")
    print(f"Actes detectes : {len(actes)}  (texte: {nb_txt}  |  vision: {nb_img})")

    for i, a in enumerate(actes, 1):
        print(f"  [{i:02d}] p.{a['page_debut']} | {a['mode']:6s} | {a['titre'][:65]}")

    nom_pdf = Path(chemin_pdf).name
    dr = date_recueil_str(nom_pdf)
    pdf_urls = charger_pdf_urls()
    url_pdf = pdf_urls.get(nom_pdf, nom_pdf)   # URL si connue, sinon nom fichier
    for a in actes:
        a["dept"] = dept
        a["source_pdf"] = url_pdf
        a["date_recueil"] = dr
    return actes


def etape_analyse(actes: list[dict], api_key: str) -> list[dict]:
    from analyzer import analyser_actes
    print(f"\n{'='*65}")
    print(f"ANALYSE CLAUDE  —  {len(actes)} actes")
    print("=" * 65 + "\n")
    return analyser_actes(actes, api_key=api_key)


def afficher_resume(nouveaux: list[dict], tous: list[dict]) -> None:
    print(f"\n{'='*65}")
    print("BILAN DE LA SESSION")
    print("=" * 65)

    for dept in ["59", "62"]:
        sous   = [r for r in nouveaux if r.get("dept") == dept]
        retenus = [r for r in sous if r.get("score", 0) >= MIN_SCORE_AFFICHE]
        if sous:
            print(f"  Dept {dept} : {len(retenus)}/{len(sous)} actes retenus (score >= {MIN_SCORE_AFFICHE})")

    notables = sorted(
        [r for r in nouveaux if r.get("score", 0) >= MIN_SCORE_AFFICHE],
        key=lambda x: x.get("score", 0), reverse=True
    )
    print(f"\n  Actes notables cette session : {len(notables)}")
    for a in notables:
        print(f"\n  [{a.get('dept')}] Score {a.get('score')}/5  |  {a.get('type_acte','?')}")
        print(f"    Titre  : {a['titre'][:75]}")
        print(f"    Resume : {a.get('resume','')}")
        if a.get("communes"):
            print(f"    Communes : {', '.join(a['communes'])}")
        if a.get("mots_cles"):
            print(f"    Mots-cles: {', '.join(a['mots_cles'])}")

    print(f"\n  Total cumule dans {OUTPUT_FILE} : {len(tous)} actes")


# ── Sélection des PDFs à traiter ─────────────────────────────────────────────

def pdfs_depuis_run(filtre_dept: str | None = None) -> list[dict]:
    """
    PDFs téléchargés lors du run scraper courant (lus depuis pdfs_nouveaux.txt).
    Retourne None si le fichier n'existe pas ou est vide.
    """
    if not NOUVEAUX_TXT.exists():
        return None
    noms = [l.strip() for l in NOUVEAUX_TXT.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not noms:
        return None
    deja_analyses = charger_analyses()
    sources = []
    for nom in noms:
        if nom in deja_analyses:
            continue
        dept = detecter_dept(nom)
        if filtre_dept and dept != filtre_dept:
            continue
        pdf_path = PDF_DIR / nom
        if pdf_path.exists():
            sources.append({"pdf": str(pdf_path), "dept": dept, "nom": nom})
        else:
            print(f"  [AVERT] PDF liste dans pdfs_nouveaux.txt mais introuvable : {nom}")
    return sources


def pdfs_a_traiter(filtre_dept: str | None = None) -> list[dict]:
    """
    Fallback : PDFs de pdfs_downloaded/ non encore analysés.
    Utilisé uniquement en appel manuel (sans pdfs_nouveaux.txt).
    """
    deja_analyses = charger_analyses()
    sources = []

    if not PDF_DIR.exists():
        return sources

    for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
        nom = pdf_path.name
        if nom in deja_analyses:
            continue
        dept = detecter_dept(nom)
        if filtre_dept and dept != filtre_dept:
            continue
        sources.append({"pdf": str(pdf_path), "dept": dept, "nom": nom})

    return sources


# ── Filtre glissant 30 jours ─────────────────────────────────────────────────

def filtrer_30_jours(actes: list[dict]) -> list[dict]:
    """
    Ne conserve que les actes dont date_recueil est dans les 30 derniers jours.
    Les actes sans date_recueil (null ou absent) sont conservés par sécurité.
    """
    limite = (datetime.now() - timedelta(days=30)).date()
    conserves, supprimes = [], 0

    for a in actes:
        dr = a.get("date_recueil")
        if not dr:
            conserves.append(a)   # pas de date → on garde
            continue
        try:
            from datetime import date as _date
            d = _date.fromisoformat(dr)
            if d >= limite:
                conserves.append(a)
            else:
                supprimes += 1
        except ValueError:
            conserves.append(a)   # date malformée → on garde

    print(f"  Filtre 30 jours : {len(conserves)} actes conserves / {supprimes} supprimes"
          f"  (limite : {limite})")
    return conserves


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Parsing des arguments
    filtre_dept = None
    sources_manuelles = None

    if len(sys.argv) == 3 and sys.argv[1].endswith(".pdf"):
        # Mode PDF explicite : python main.py fichier.pdf 59
        sources_manuelles = [{"pdf": sys.argv[1], "dept": sys.argv[2],
                               "nom": Path(sys.argv[1]).name}]
    elif len(sys.argv) == 2 and sys.argv[1] in ("59", "62"):
        filtre_dept = sys.argv[1]

    api_key = charger_api_key()

    if sources_manuelles:
        sources = sources_manuelles
    else:
        sources_run = pdfs_depuis_run(filtre_dept)
        if sources_run is not None:
            print(f"Mode run scraper : {len(sources_run)} PDF(s) depuis pdfs_nouveaux.txt")
            sources = sources_run
        else:
            print("Mode manuel : scan de pdfs_downloaded/ (pdfs_nouveaux.txt absent)")
            sources = pdfs_a_traiter(filtre_dept)

    if not sources:
        print("Aucun nouveau PDF a analyser.")
        if NOUVEAUX_TXT.exists():
            NOUVEAUX_TXT.unlink()
        sys.exit(0)

    print(f"\n{len(sources)} PDF(s) a analyser :")
    for s in sources:
        print(f"  [{s['dept']}] {s['nom']}")

    # Charger les résultats existants
    tous_resultats = charger_resultats_existants()
    nouveaux_resultats = []

    for src in sources:
        actes = etape_extraction(src["pdf"], src["dept"])
        if not actes:
            continue
        resultats = etape_analyse(actes, api_key)
        nouveaux_resultats.extend(resultats)
        enregistrer_analyse(src["nom"])

    # Nettoyer pdfs_nouveaux.txt : consommé, ne doit pas persister
    if NOUVEAUX_TXT.exists():
        NOUVEAUX_TXT.unlink()

    if not nouveaux_resultats:
        print("Aucun acte produit.")
        return

    # Fusionner, trier, filtrer, sauvegarder
    tous_resultats.extend(nouveaux_resultats)
    tous_resultats.sort(key=lambda x: (x.get("score", 0), x.get("dept", "")), reverse=True)
    tous_resultats = filtrer_30_jours(tous_resultats)
    sauvegarder(tous_resultats)
    afficher_resume(nouveaux_resultats, tous_resultats)


if __name__ == "__main__":
    main()
