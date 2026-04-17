"""
Enrichit resultats.json avec le champ titre_court sur tous les actes
en utilisant un prompt allégé (titre + résumé uniquement).
Sauvegarde au fur et à mesure pour reprendre en cas d'interruption.
"""
import json
import os
import re
import sys
import time
from dotenv import load_dotenv
import anthropic

load_dotenv()

OUTPUT_FILE = "data/resultats.json"
PAUSE = 2        # secondes entre appels
PAUSE_429 = 20   # secondes si rate limit

PROMPT = """À partir de ce titre administratif et de ce résumé, génère uniquement un titre_court journalistique (max 8 mots, style presse régionale, factuel, sans jargon).
Réponds UNIQUEMENT avec le titre_court en JSON :
{{"titre_court": "..."}}

Titre : {titre}
Résumé : {resume}"""


def parse_titre_court(texte: str) -> str:
    texte = texte.strip()
    texte = re.sub(r"^```(?:json)?\s*", "", texte)
    texte = re.sub(r"\s*```$", "", texte)
    try:
        data = json.loads(texte)
        return data.get("titre_court", "").strip()
    except json.JSONDecodeError:
        # Tentative extraction directe
        m = re.search(r'"titre_court"\s*:\s*"([^"]+)"', texte)
        return m.group(1).strip() if m else ""


def main():
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[ERREUR] ANTHROPIC_API_KEY manquante.")
        sys.exit(1)

    with open(OUTPUT_FILE, encoding="utf-8") as f:
        actes = json.load(f)

    a_traiter = [i for i, a in enumerate(actes) if not a.get("titre_court", "").strip()]
    total = len(a_traiter)
    print(f"{total} actes sans titre_court sur {len(actes)} au total\n")

    if total == 0:
        print("Rien à faire.")
        return

    client = anthropic.Anthropic(api_key=api_key)
    ok = 0
    erreurs = 0

    for compteur, idx in enumerate(a_traiter, 1):
        acte = actes[idx]
        titre = acte.get("titre", "")[:200]
        resume = acte.get("resume", "")[:400]

        if not resume.strip():
            resume = titre  # fallback si résumé absent

        print(f"  [{compteur}/{total}] [{acte.get('dept','?')}] {titre[:65]}...")

        for tentative in range(2):
            try:
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=64,
                    messages=[{"role": "user", "content": PROMPT.format(
                        titre=titre,
                        resume=resume,
                    )}],
                )
                tc = parse_titre_court(msg.content[0].text)
                if tc:
                    actes[idx]["titre_court"] = tc
                    print(f"    => {tc}")
                    ok += 1
                else:
                    print(f"    [WARN] Réponse vide : {msg.content[0].text[:80]}")
                    actes[idx]["titre_court"] = ""
                    erreurs += 1
                break
            except anthropic.RateLimitError:
                if tentative == 0:
                    print(f"    [429] Rate limit — attente {PAUSE_429}s...")
                    time.sleep(PAUSE_429)
                else:
                    print(f"    [ERREUR] Rate limit persistant, acte ignoré.")
                    actes[idx]["titre_court"] = ""
                    erreurs += 1
            except Exception as e:
                print(f"    [ERREUR] {e}")
                actes[idx]["titre_court"] = ""
                erreurs += 1
                break

        # Sauvegarde intermédiaire toutes les 10 actes
        if compteur % 10 == 0:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(actes, f, ensure_ascii=False, indent=2)
            print(f"  [SAVE] Progression sauvegardée ({compteur}/{total})")

        if compteur < total:
            time.sleep(PAUSE)

    # Sauvegarde finale
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(actes, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"BILAN : {ok} titre_court générés | {erreurs} erreurs | {len(actes)} actes total")
    print(f"Fichier sauvegardé : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
