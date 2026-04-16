"""
Reanalyse uniquement les actes en erreur (score=0, type_acte="ERREUR")
depuis resultats.json, fusionne les résultats et réexporte le fichier.
"""
import json
import os
import sys

from dotenv import load_dotenv
from config import OUTPUT_FILE
from extractor import extraire_actes_depuis_pdf
from analyzer import analyser_actes

load_dotenv()


def charger_resultats(chemin: str) -> list[dict]:
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def sauvegarder(resultats: list[dict], chemin: str) -> None:
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)
    print(f"Resultats sauvegardes : {chemin}  ({len(resultats)} actes)")


def main():
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[ERREUR] ANTHROPIC_API_KEY absent. Definissez la variable d'environnement ou creez un fichier .env")
        sys.exit(1)

    # -- 1. Charger le JSON existant
    resultats = charger_resultats(OUTPUT_FILE)
    erreurs = [(i, a) for i, a in enumerate(resultats)
               if a.get("score") == 0 and a.get("type_acte") == "ERREUR"]

    if not erreurs:
        print("Aucun acte en erreur dans resultats.json. Rien a faire.")
        sys.exit(0)

    print(f"\n{len(erreurs)} acte(s) en erreur a re-analyser :")
    for _, a in erreurs:
        print(f"  [{a.get('dept')}] p.{a.get('page_debut')} | {a['titre'][:65]}")

    # -- 2. Reconstruire les actes avec leur contenu (images ou texte)
    # On regroupe par PDF source pour ne lire chaque fichier qu'une fois
    SOURCES = {
        "59": "Recueil n\u00b0125 du 31 mars 2026.pdf",
        "62": "Recueil des actes administratifs n\u00b0101 en date du 15 avril 2026.pdf",
    }

    # Extraire tous les actes des PDFs concernés
    actes_par_dept: dict[str, list[dict]] = {}
    depts_concernes = {a.get("dept") for _, a in erreurs}
    for dept in depts_concernes:
        pdf = SOURCES.get(dept)
        if not pdf or not os.path.exists(pdf):
            print(f"[ERREUR] PDF introuvable pour le dept {dept} : {pdf}")
            continue
        print(f"\nExtraction du PDF dept {dept}...")
        actes_par_dept[dept] = extraire_actes_depuis_pdf(pdf)

    # -- 3. Pour chaque acte en erreur, retrouver son contenu par page_debut
    actes_a_analyser = []
    indices_a_remplacer = []

    for idx, acte_err in erreurs:
        dept = acte_err.get("dept")
        page = acte_err.get("page_debut")
        actes_source = actes_par_dept.get(dept, [])

        acte_complet = next(
            (a for a in actes_source if a.get("page_debut") == page),
            None
        )
        if acte_complet is None:
            print(f"  [WARN] Acte introuvable en page {page} (dept {dept}) — ignore")
            continue

        # Conserver le champ dept du JSON existant
        acte_complet["dept"] = dept
        actes_a_analyser.append(acte_complet)
        indices_a_remplacer.append(idx)

    if not actes_a_analyser:
        print("Aucun acte recuperable. Verifiez les PDFs sources.")
        sys.exit(1)

    # -- 4. Re-analyser
    print(f"\n{'='*60}")
    print(f"RE-ANALYSE  —  {len(actes_a_analyser)} actes")
    print('='*60 + "\n")

    nouveaux = analyser_actes(actes_a_analyser, api_key=api_key)

    # -- 5. Fusionner dans le JSON existant
    for idx, nouveau in zip(indices_a_remplacer, nouveaux):
        resultats[idx] = nouveau

    # -- 6. Re-trier par score décroissant et sauvegarder
    resultats.sort(key=lambda x: x.get("score", 0), reverse=True)
    sauvegarder(resultats, OUTPUT_FILE)

    # -- 7. Afficher les actes re-analysés
    print(f"\n{'='*60}")
    print("ACTES RE-ANALYSES")
    print('='*60)
    for a in nouveaux:
        print(f"\n  [{a.get('dept')}] Score {a.get('score','?')}/5 | {a.get('type_acte','?')}")
        print(f"  Titre  : {a['titre'][:75]}")
        print(f"  Resume : {a.get('resume','')}")
        if a.get('communes'):
            print(f"  Communes : {', '.join(a['communes'])}")
        if a.get('mots_cles'):
            print(f"  Mots-cles: {', '.join(a['mots_cles'])}")


if __name__ == "__main__":
    main()
