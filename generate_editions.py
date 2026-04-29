"""
generate_editions.py — Génère data/editions.json depuis population_2023_locales.xlsx

Structure produite :
  { "ARMENTIERES": "ARMENTIEROIS", "BOIS-GRENIER": "ARMENTIEROIS", ... }

Clés : nom de commune en majuscules, accents supprimés.
Valeurs : nom d'édition tel que présent dans le fichier Excel (déjà normalisé).
"""

import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import openpyxl

_BASE   = Path(__file__).resolve().parent
XLSX    = _BASE / "population_2023_locales.xlsx"
OUTPUT  = _BASE / "data" / "editions.json"


def normaliser(nom: str) -> str:
    """Majuscules + suppression des accents."""
    nfkd = unicodedata.normalize("NFKD", nom)
    sans_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sans_accents.upper()


def main():
    if not XLSX.exists():
        print(f"[ERREUR] Fichier introuvable : {XLSX}")
        print("Placez population_2023_locales.xlsx dans D:/raa-veille/")
        return

    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb.active

    editions_communes: dict[str, str] = {}
    compte_par_edition: dict[str, int] = defaultdict(int)
    ignorees = 0

    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # en-tête

        commune_brut = row[1]
        edition      = row[3]

        if not commune_brut or not edition:
            ignorees += 1
            continue

        cle = normaliser(str(commune_brut))
        editions_communes[cle] = str(edition).strip()
        compte_par_edition[str(edition).strip()] += 1

    # Trier les clés alphabétiquement
    editions_communes = dict(sorted(editions_communes.items()))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(editions_communes, f, ensure_ascii=False, indent=2)

    print(f"editions.json ecrit : {len(editions_communes)} communes")
    if ignorees:
        print(f"  ({ignorees} lignes ignorees — commune ou edition vide)")
    print()

    editions_triees = sorted(compte_par_edition.items(), key=lambda x: (-x[1], x[0]))
    print(f"{'Edition':<30}  {'Communes':>8}")
    print("-" * 42)
    for edition, nb in editions_triees:
        print(f"  {edition:<28}  {nb:>8}")
    print("-" * 42)
    print(f"  {'TOTAL':<28}  {sum(compte_par_edition.values()):>8}")
    print(f"  {len(compte_par_edition)} editions au total")


if __name__ == "__main__":
    main()
