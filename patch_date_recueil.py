"""
Backfille les champs source_pdf et date_recueil sur les actes existants
de resultats.json en ré-extrayant les sommaires de chaque PDF disponible.

Stratégie de matching :
  - Re-extrait les actes de tous les PDFs dans pdfs_downloaded/
  - Indexe par (dept, page_debut, titre[:40])
  - Pour chaque acte dans resultats.json, cherche la clé correspondante
  - Si trouvé → assigne source_pdf + date_recueil
  - Si non trouvé → date_recueil = None, source_pdf = None
"""
import json
from pathlib import Path

from scraper import date_recueil_str
from extractor import extraire_actes_depuis_pdf

OUTPUT_FILE = "data/resultats.json"
PDF_DIR = Path("pdfs_downloaded")


def cle(dept: str, page: int, titre: str) -> tuple:
    return (dept, page, titre[:40].strip())


def main():
    # 1. Charger resultats.json
    with open(OUTPUT_FILE, encoding="utf-8") as f:
        actes = json.load(f)
    print(f"{len(actes)} actes chargés depuis {OUTPUT_FILE}")

    # 2. Construire l'index depuis tous les PDFs disponibles
    index: dict[tuple, dict] = {}   # cle → {"source_pdf": ..., "date_recueil": ...}

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"\n{len(pdfs)} PDF(s) à parcourir :")
    for pdf in pdfs:
        nom = pdf.name
        dept = "62" if "des actes administratifs" in nom.lower() else "59"
        dr = date_recueil_str(nom)
        print(f"  [{dept}] {nom}  date_recueil={dr}")

        try:
            actes_pdf = extraire_actes_depuis_pdf(str(pdf))
        except Exception as e:
            print(f"    [ERREUR extraction] {e}")
            continue

        for a in actes_pdf:
            k = cle(dept, a.get("page_debut", 0), a.get("titre", ""))
            index[k] = {"source_pdf": nom, "date_recueil": dr}

    print(f"\nIndex construit : {len(index)} entrées\n")

    # 3. Matcher chaque acte et injecter les champs
    # PDFs initiaux supprimés de pdfs_downloaded/ — dates connues par leur nom
    FALLBACK = {
        "59": {"source_pdf": "Recueil n°125 du 31 mars 2026.pdf",
               "date_recueil": "2026-03-31"},
        "62": {"source_pdf": "Recueil des actes administratifs n°101 en date du 15 avril 2026.pdf",
               "date_recueil": "2026-04-15"},
    }

    trouves = 0
    fallback_utilises = 0
    manquants = []

    for acte in actes:
        # Ignorer si déjà renseigné
        if acte.get("date_recueil"):
            trouves += 1
            continue

        dept = acte.get("dept", "")
        page = acte.get("page_debut", 0)
        titre = acte.get("titre", "")
        k = cle(dept, page, titre)

        if k in index:
            acte["source_pdf"]   = index[k]["source_pdf"]
            acte["date_recueil"] = index[k]["date_recueil"]
            trouves += 1
        elif dept in FALLBACK:
            acte["source_pdf"]   = FALLBACK[dept]["source_pdf"]
            acte["date_recueil"] = FALLBACK[dept]["date_recueil"]
            fallback_utilises += 1
        else:
            acte["source_pdf"]   = None
            acte["date_recueil"] = None
            manquants.append(acte)

    # 4. Sauvegarder
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(actes, f, ensure_ascii=False, indent=2)

    print(f"{'='*60}")
    print(f"BILAN : {trouves} matchés | {fallback_utilises} via fallback | {len(manquants)} non trouvés")
    if manquants:
        print("Non trouvés :")
        for a in manquants:
            print(f"  [{a.get('dept')}] p.{a.get('page_debut')} | {a['titre'][:65]}")

    # 5. Aperçu de 5 actes
    print(f"\n{'='*60}")
    print("APERÇU (5 actes score >= 4)")
    print('='*60)
    hauts = sorted([a for a in actes if a.get("score", 0) >= 4],
                   key=lambda x: x.get("score", 0), reverse=True)
    for a in hauts[:5]:
        print(f"\n  [{a.get('dept')}] score={a.get('score')} | p.{a.get('page_debut')}")
        print(f"  Titre       : {a['titre'][:70]}")
        print(f"  source_pdf  : {a.get('source_pdf','(absent)')}")
        print(f"  date_recueil: {a.get('date_recueil','(absent)')}")

    print(f"\nFichier sauvegardé : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
