"""
core/embedding_service.py
==========================
SERVICE D'ENCODAGE SÉMANTIQUE

Ce fichier contient un "robot" (service) qui sait transformer du texte en
une liste de nombres (un vecteur). C'est ce qu'on appelle un embedding.

Pourquoi on en a besoin ?
Parce que notre base de données (SQLite) ne comprend pas le texte,
elle ne comprend que des nombres. Pour rechercher des missions similaires,
on va comparer ces listes de nombres entre elles.

Ce service est conçu pour être utilisé partout dans le code.
Tout le monde appelle la fonction get_embedding_service() pour
récupérer le robot, puis lui demande de faire le travail.
"""

# ============================================================
# 1. IMPORTS NÉCESSAIRES
# ============================================================

# Ces imports servent à décrire le type des données
# (pour que l'éditeur nous aide à ne pas faire d'erreurs)
from typing import List, Optional, TYPE_CHECKING

# On importe la bibliothèque numpy pour manipuler des listes de nombres
# (même si on ne l'utilise pas directement, elle est utilisée en arrière-plan)
import numpy as np

# ============================================================
# 2. IMPORT SPÉCIAL POUR QUE L'ÉDITEUR NE CRIE PAS (Pylance)
# ============================================================

# TYPE_CHECKING est une astuce : ça veut dire "ce code ne s'exécute pas,
# il sert seulement à dire à l'éditeur quel est le type de la variable".
# Ça permet d'éviter l'erreur que tu as eue tout à l'heure.
if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

# ============================================================
# 3. LE VRAI IMPORT (POUR QUE LE CODE S'EXÉCUTE)
# ============================================================

# On essaie d'importer la bibliothèque sentence-transformers.
# C'est elle qui contient le "cerveau" qui transforme le texte en nombres.
# On la renomme _SentenceTransformer avec un underscore devant
# pour montrer qu'on ne l'utilise que dans ce fichier.
try:
    from sentence_transformers import SentenceTransformer as _SentenceTransformer
    _HAS_ST = True  # On se souvient que l'import a réussi
except ImportError:
    # Si l'import échoue (parce que le package n'est pas installé),
    # on met la variable à None et on passe _HAS_ST à False
    _SentenceTransformer = None  # type: ignore
    _HAS_ST = False

# On importe notre propre outil de log (pour écrire des messages)
from utils.logger import Logger


# ============================================================
# 4. LA CLASSE PRINCIPALE : LE SERVICE D'EMBEDDING
# ============================================================

class EmbeddingService:
    """
    Ce robot sait charger un modèle une seule fois (lazy loading)
    et l'utiliser pour transformer du texte en vecteurs (listes de nombres).

    Pourquoi "lazy loading" ? Parce qu'on ne veut pas charger le modèle
    (qui est lourd, ~80 Mo) dès le démarrage du programme.
    On le charge seulement quand on en a besoin pour la première fois.
    """

    # Le nom du modèle qu'on va utiliser.
    # C'est un modèle léger et rapide (384 dimensions).
    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
    def __init__(self, model_name: Optional[str] = None):
        """
        Constructeur : on crée le robot, mais on ne charge pas encore le modèle.
        Le modèle sera chargé plus tard, quand on appellera embed().
        """
        # Si on ne donne pas de nom de modèle, on utilise celui par défaut
        self.model_name = model_name or self.DEFAULT_MODEL

        # _model : c'est là qu'on stockera le "cerveau" (le modèle) une fois chargé.
        # Au départ, il n'y a rien, donc on met None.
        # Le "Optional['SentenceTransformer']" veut dire "peut être un modèle ou rien".
        self._model: Optional['SentenceTransformer'] = None

        # _loaded : un petit drapeau (booléen) pour savoir si le modèle a déjà été chargé
        self._loaded = False

    def _load_model(self) -> None:
        """
        Cette fonction interne charge le modèle en mémoire.
        Elle est appelée automatiquement la première fois qu'on veut calculer un embedding.
        """
        # Si le modèle est déjà chargé, on ne fait rien (on ne le recharge pas)
        if self._loaded:
            return

        # Si sentence-transformers n'a pas pu être importé, on lève une erreur
        if not _HAS_ST:
            raise ImportError(
                "sentence-transformers n'est pas installé. "
                "Veuillez l'installer avec : pip install sentence-transformers"
            )

        # On écrit un log pour dire qu'on commence le chargement
        Logger.info(f"[EmbeddingService] Chargement du modèle {self.model_name}...")

        try:
            # Voici le moment crucial : on charge le modèle.
            # Cela peut prendre quelques secondes (c'est normal).
            self._model = _SentenceTransformer(self.model_name)

            # On marque le modèle comme chargé
            self._loaded = True

            # On écrit un log pour dire que c'est fini, avec la dimension du vecteur
            # (généralement 384 pour ce modèle)
            try:
                dim = self._model.get_embedding_dimension()
            except AttributeError:
                dim = self._model.get_embedding_dimension()
            Logger.info(f"[EmbeddingService] Modèle chargé avec succès (dim: {dim}).")
        except Exception as e:
            # Si une erreur survient (ex: pas de mémoire, fichier corrompu...)
            # on la logue et on la remonte
            Logger.error(f"[EmbeddingService] Échec du chargement du modèle : {e}")
            raise

    def embed(self, text: str) -> List[float]:
        """
        C'est la fonction la plus importante du fichier !

        Elle prend du texte (une phrase) et retourne son embedding :
        une liste de nombres (float) qui représentent sémantiquement le texte.

        Exemple :
            embed("ouvrir chrome") -> [0.12, -0.34, 0.56, ...] (384 nombres)

        Si le texte est vide, on retourne une liste de zéros (pour éviter une erreur).
        """
        # Si le texte est vide ou ne contient que des espaces, on s'arrête là
        if not text or not text.strip():
            Logger.warning("[EmbeddingService] Texte vide reçu, retour d'un vecteur nul.")
            # On retourne une liste de 384 zéros (la dimension du modèle)
            return [0.0] * 384

        # On s'assure que le modèle est chargé (c'est ici que le "lazy loading" se déclenche)
        self._load_model()

        # Maintenant, on demande au modèle de transformer le texte en vecteur.
        # Le modèle s'attend à recevoir une liste de textes (même si on en a qu'un).
        # On met [text] pour créer une liste avec un seul élément.

        # convert_to_numpy=True : on récupère un tableau numpy (plus rapide)
        # normalize_embeddings=True : on rend la longueur du vecteur égale à 1
        #   (cela permet de comparer plus facilement les vecteurs entre eux)
        embedding = self._model.encode(
            [text],
            convert_to_numpy=True,
            normalize_embeddings=True
        )[0]  # [0] parce qu'on a envoyé une liste d'un seul texte, on récupère le premier résultat

        # Le résultat est un tableau numpy, on le transforme en liste Python normale
        return embedding.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Cette fonction fait la même chose que embed(), mais pour plusieurs textes à la fois.
        C'est plus rapide quand on a beaucoup de phrases à traiter d'un coup.

        Exemple :
            embed_batch(["ouvrir chrome", "fermer excel"]) ->
            [[0.1, ...], [0.2, ...]]
        """
        if not texts:
            return []

        # On charge le modèle une fois pour toutes
        self._load_model()

        # On encode tout le lot d'un coup (plus efficace que de faire une boucle)
        embeddings = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True
        )

        # On transforme chaque tableau numpy en liste Python
        return [emb.tolist() for emb in embeddings]

    @property
    def dimension(self) -> int:
        """
        Une "propriété" (comme une variable, mais qui calcule une valeur à la volée).
        Elle retourne le nombre de dimensions (taille) des vecteurs.
        Pour notre modèle, c'est 384.
        """
        # On s'assure que le modèle est chargé
        self._load_model()
        if self._model is None:
            raise RuntimeError("Le modèle n'a pas pu être chargé.")
        return self._model.get_embedding_dimension()


# ============================================================
# 5. LE SINGLETON : UN SEUL ROBOT POUR TOUT LE PROJET
# ============================================================

# On crée une variable globale (au niveau du fichier) pour stocker l'instance unique du service.
# Elle est initialisée à None (pas encore créée).
_embedding_service_instance: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """
    Cette fonction est le point d'entrée principal pour tout le projet.
    Elle garantit qu'on n'a qu'UNE SEULE instance du service (un seul modèle chargé).

    C'est ce qu'on appelle un "Singleton".

    Si le service n'a pas encore été créé, on le crée.
    Sinon, on retourne celui qui existe déjà.
    """
    global _embedding_service_instance
    if _embedding_service_instance is None:
        # Création de l'instance (le modèle n'est pas encore chargé, c'est fait dans __init__)
        _embedding_service_instance = EmbeddingService()
    return _embedding_service_instance


# ============================================================
# 6. FONCTION DE CONFIANCE (RACCOURCI)
# ============================================================

def embed_text(text: str) -> List[float]:
    """
    Un petit raccourci très pratique.
    Au lieu d'écrire :
        get_embedding_service().embed("mon texte")
    On peut écrire :
        embed_text("mon texte")

    C'est juste pour gagner du temps et rendre le code plus court.
    """
    return get_embedding_service().embed(text)