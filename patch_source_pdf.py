"""
Backfille le champ source_pdf avec l'URL complète dans resultats.json.

Stratégie :
  1. Scrape les pages préfectorales Nord et Pas-de-Calais
  2. Construit un dict nom_fichier → url_complete
  3. Met à jour source_pdf dans resultats.json pour chaque acte correspondant
  4. Persiste aussi le mapping dans data/pdf_urls.json pour les runs futurs
"""
import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote
from datetime import date, timedelta

from scraper import (
    HEADERS, BASE_59, BASE_62,
    url_nord_mois, url_pdc_annee, get_page, extraire_pdfs,
    enregistrer_pdf_url, charger_pdf_urls,
    MOIS_FR_URL,
)

OUTPUT_FILE = "data/resultats.json"


def scraper_urls_nord() -> dict[str, str]:
    """Retourne {nom_fichier: url} pour le mois courant (+ repli mois précédent)."""
    from datetime import date, timedelta
    aujourd_hui = date.today()
    annee, mois = aujourd_hui.year, aujourd_hui.month
    urls_a_tester = [url_nord_mois(annee, mois)]
    premier = aujourd_hui.replace(day=1)
    precedent = premier - timedelta(days=1)
    urls_a_tester.append(url_nord_mois(precedent.year, precedent.month))

    mapping = {}
    for url in urls_a_tester:
        r = get_page(url)
        if r:
            for p in extraire_pdfs(r.text, BASE_59):
                mapping[p["nom"]] = p["url"]
            print(f"  [59] {url} — {len(mapping)} PDFs")
    return mapping


def scraper_urls_pdc() -> dict[str, str]:
    """Retourne {nom_fichier: url} depuis la page annuelle PdC (avec fallback N-1)."""
    from datetime import date as _date
    annee = _date.today().year
    for _ in range(2):
        url = url_pdc_annee(annee)
        r = get_page(url)
        if r:
            mapping = {p["nom"]: p["url"] for p in extraire_pdfs(r.text, BASE_62)}
            print(f"  [62] {url} — {len(mapping)} PDFs")
            return mapping
        annee -= 1
    print("  [62] Page inaccessible.")
    return {}


def main():
    print("Scraping des pages préfectorales...\n")
    mapping = {}
    mapping.update(scraper_urls_nord())
    mapping.update(scraper_urls_pdc())
    print(f"\nTotal : {len(mapping)} PDFs indexés\n")

    # Persister dans pdf_urls.json (merge avec l'existant)
    for nom, url in mapping.items():
        enregistrer_pdf_url(nom, url)
    print(f"data/pdf_urls.json mis à jour\n")

    # Charger resultats.json
    with open(OUTPUT_FILE, encoding="utf-8") as f:
        actes = json.load(f)

    # Mettre à jour source_pdf
    mis_a_jour = 0
    non_trouves = set()

    for acte in actes:
        source = acte.get("source_pdf", "")
        # Extraire le nom de fichier depuis source (chemin local ou déjà URL)
        nom = Path(source).name if source else ""
        if nom and nom in mapping:
            acte["source_pdf"] = mapping[nom]
            mis_a_jour += 1
        elif nom:
            non_trouves.add(nom)

    print(f"{'='*60}")
    print(f"BILAN : {mis_a_jour} actes mis à jour sur {len(actes)}")
    if non_trouves:
        print(f"{len(non_trouves)} nom(s) non trouvés dans le scraping :")
        for n in sorted(non_trouves):
            print(f"  - {n}")

    # Sauvegarder
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(actes, f, ensure_ascii=False, indent=2)
    print(f"\nFichier sauvegardé : {OUTPUT_FILE}")

    # Aperçu
    print(f"\n{'='*60}")
    print("APERÇU (3 premiers actes)")
    print('='*60)
    for a in actes[:3]:
        print(f"\n  [{a.get('dept')}] {a['titre'][:60]}")
        print(f"  source_pdf : {a.get('source_pdf','(absent)')}")


if __name__ == "__main__":
    main()
