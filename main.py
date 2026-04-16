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
from pathlib import Path

from config import MIN_SCORE_AFFICHE, OUTPUT_FILE
from extractor import extraire_actes_depuis_pdf

PDF_DIR       = Path("pdfs_downloaded")
ANALYSES_TXT  = Path("data/pdfs_analyses.txt")   # PDFs déjà analysés


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

    for a in actes:
        a["dept"] = dept
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

def pdfs_a_traiter(filtre_dept: str | None = None) -> list[dict]:
    """
    Retourne les PDFs de pdfs_downloaded/ non encore analysés.
    filtre_dept : '59', '62', ou None pour les deux.
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

    sources = sources_manuelles if sources_manuelles else pdfs_a_traiter(filtre_dept)

    if not sources:
        print("Aucun nouveau PDF a analyser dans pdfs_downloaded/.")
        print("(Verifiez data/pdfs_analyses.txt et le contenu du dossier)")
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

    if not nouveaux_resultats:
        print("Aucun acte produit.")
        return

    # Fusionner, trier, sauvegarder
    tous_resultats.extend(nouveaux_resultats)
    tous_resultats.sort(key=lambda x: (x.get("score", 0), x.get("dept", "")), reverse=True)
    sauvegarder(tous_resultats)
    afficher_resume(nouveaux_resultats, tous_resultats)


if __name__ == "__main__":
    main()
