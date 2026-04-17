import os
from dotenv import load_dotenv

load_dotenv()

# URLs des pages de publication des RAA
PREFECTURE_URLS = {
    "Nord (59)": "https://www.nord.gouv.fr/Publications/Recueils-des-actes-administratifs",
    "Pas-de-Calais (62)": "https://www.pas-de-calais.gouv.fr/Publications/Recueil-des-actes-administratifs",
}

# Clé API Anthropic (via .env ou variable d'environnement)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Modèle Claude à utiliser
CLAUDE_MODEL = "claude-sonnet-4-5"

# Prompt d'analyse envoyé à Claude pour chaque acte administratif
ANALYSIS_PROMPT = """Tu es un assistant spécialisé dans l'analyse d'actes administratifs préfectoraux français.

Analyse l'acte administratif suivant et retourne UNIQUEMENT un objet JSON valide, sans texte autour.

Acte à analyser :
{texte}

Retourne ce JSON exactement (sans markdown, sans explications) :
{{
  "score": <entier de 1 à 5 selon l'intérêt journalistique>,
  "titre_court": "<titre journalistique, max 8 mots, style presse régionale, accrocheur et factuel, sans jargon administratif — ex: '420 arbres abattus au port de Dunkerque', '34 caméras de surveillance à Gondecourt', 'RC Lens : sécurité renforcée pour la demi-finale'>",
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

# Score minimum pour afficher un acte dans les résultats
MIN_SCORE_AFFICHE = 3

# Dossier de stockage des PDFs téléchargés
PDF_DIR = "pdfs_downloaded"

# Fichier de sortie des résultats d'analyse
OUTPUT_FILE = "data/resultats.json"
