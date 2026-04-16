import json
import re
import time
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, ANALYSIS_PROMPT

PAUSE_ENTRE_APPELS = 3      # secondes entre chaque appel API
PAUSE_RATE_LIMIT   = 15     # secondes d'attente en cas d'erreur 429
MAX_RETRIES        = 1      # nombre de retentatives après un 429

# Prompt adapté pour les actes envoyés en image
ANALYSIS_PROMPT_IMAGE = """Tu es un assistant spécialisé dans l'analyse d'actes administratifs préfectoraux français.

Analyse l'acte administratif affiché dans les images ci-jointes (pages scannées du Recueil des Actes Administratifs).
Son titre est : {titre}

Retourne UNIQUEMENT un objet JSON valide, sans texte autour :
{{
  "score": <entier de 1 à 5 selon l'intérêt journalistique>,
  "resume": "<résumé journalistique en 2-3 phrases, en français, adapté à un lecteur non spécialiste>",
  "type_acte": "<type parmi : ARRÊTÉ, DÉCISION, HABILITATION, DÉLÉGATION, NOMINATION, AUTRE>",
  "communes": ["<liste des communes mentionnées, vide si aucune>"],
  "mots_cles": ["<5 mots-clés maximum représentatifs de l'acte>"]
}}

Critères de scoring (1 à 5) :
1 = Acte purement administratif interne, aucun intérêt pour le grand public
2 = Acte de gestion courante, intérêt très limité
3 = Acte notable, peut intéresser des acteurs locaux ou des professionnels
4 = Acte d'intérêt public marqué, susceptible d'impacter des citoyens ou des territoires
5 = Acte majeur : décision structurante, sécurité publique, environnement, urbanisme important"""


def _parse_json_response(contenu: str) -> dict:
    """Parse la réponse JSON de Claude, tolère les blocs markdown."""
    contenu = contenu.strip()
    # Retirer les blocs ```json ... ```
    contenu = re.sub(r"^```(?:json)?\s*", "", contenu)
    contenu = re.sub(r"\s*```$", "", contenu)
    try:
        return json.loads(contenu)
    except json.JSONDecodeError:
        return {
            "score": 0,
            "resume": "Erreur de parsing de la réponse Claude.",
            "type_acte": "INCONNU",
            "communes": [],
            "mots_cles": [],
            "raw_response": contenu,
        }


def _appel_api_avec_retry(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    """Appel API avec retry unique sur erreur 429."""
    for tentative in range(MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if tentative < MAX_RETRIES:
                print(f"    [429] Rate limit — attente {PAUSE_RATE_LIMIT}s puis retry...")
                time.sleep(PAUSE_RATE_LIMIT)
            else:
                raise


def analyser_acte_texte(texte_acte: str, client: anthropic.Anthropic) -> dict:
    """Analyse un acte textuel via l'API Claude."""
    prompt = ANALYSIS_PROMPT.format(texte=texte_acte[:4000])
    message = _appel_api_avec_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json_response(message.content[0].text)


def analyser_acte_image(titre: str, images_b64: list[str], client: anthropic.Anthropic) -> dict:
    """Analyse un acte image via Claude Vision (liste de pages PNG en base64)."""
    contenu_message = []

    for img_b64 in images_b64[:8]:  # max 8 pages par acte pour l'API
        contenu_message.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img_b64,
            },
        })

    contenu_message.append({
        "type": "text",
        "text": ANALYSIS_PROMPT_IMAGE.format(titre=titre),
    })

    message = _appel_api_avec_retry(
        client,
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": contenu_message}],
    )
    return _parse_json_response(message.content[0].text)


def analyser_actes(actes: list[dict], api_key: str = None) -> list[dict]:
    """
    Analyse une liste d'actes (texte ou image) et retourne les résultats enrichis.
    """
    cle = api_key or ANTHROPIC_API_KEY
    client = anthropic.Anthropic(api_key=cle)

    resultats = []
    total = len(actes)

    for i, acte in enumerate(actes, 1):
        mode = acte.get("mode", "texte")
        print(f"  [{mode.upper()}] Analyse {i}/{total} : {acte['titre'][:60]}...")
        try:
            if mode == "image" and acte.get("images_b64"):
                analyse = analyser_acte_image(acte["titre"], acte["images_b64"], client)
            else:
                texte = acte.get("texte", acte.get("titre", ""))
                analyse = analyser_acte_texte(texte, client)
        except Exception as e:
            analyse = {
                "score": 0,
                "resume": f"Erreur lors de l'analyse : {e}",
                "type_acte": "ERREUR",
                "communes": [],
                "mots_cles": [],
            }

        # On exclut les images_b64 du résultat JSON final (trop volumineux)
        acte_sans_images = {k: v for k, v in acte.items() if k != "images_b64"}
        resultats.append({**acte_sans_images, **analyse})

        # Pause entre chaque appel (sauf après le dernier)
        if i < total:
            time.sleep(PAUSE_ENTRE_APPELS)

    return resultats
