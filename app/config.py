import os
import logging
from pathlib import Path
from typing import Optional, Tuple
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# --- Rutas base ---
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
LOG_DIR: Path = DATA_DIR / "logs"
FRONTEND_DIR: Path = BASE_DIR / "frontend"

# Crear directorios si no existen
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# --- Ollama ---
def _parse_ollama_think(raw_value: str) -> Tuple[bool, Optional[str]]:
    """
    Interpreta OLLAMA_THINK permitiendo:
    - false/off/0 (desactiva)
    - true/on/1 (activa)
    - low|medium|high (activa + guarda nivel solicitado)
    """
    value = (raw_value or "").strip().lower()
    if value in {"", "0", "false", "no", "off"}:
        return False, None
    if value in {"1", "true", "yes", "on"}:
        return True, None
    if value in {"low", "medium", "high"}:
        return True, value
    return False, None


OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))
_OLLAMA_THINK_RAW: str = os.getenv("OLLAMA_THINK", "false")
OLLAMA_THINK, OLLAMA_THINK_LEVEL = _parse_ollama_think(_OLLAMA_THINK_RAW)

# --- ChromaDB ---
CHROMA_DIR: str = str(DATA_DIR / "chromadb")

# --- Contexto ---
MAX_CONTEXT_MESSAGES: int = int(os.getenv("MAX_CONTEXT_MESSAGES", "20"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID: str = os.getenv("TELEGRAM_USER_ID", "")

# --- Web search ---
BRAVE_API_KEY: str = os.getenv("BRAVE_API_KEY", "")

# --- Servidor ---
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
RELOAD: bool = os.getenv("RELOAD", "false").strip().lower() in {"1", "true", "yes", "on"}

# --- Personalidad ---
PERSONALITY_FILE: Path = Path(__file__).resolve().parent / "personality.yaml"

# --- Logging ---
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: Path = LOG_DIR / "agent.log"


def setup_logging() -> logging.Logger:
    """Configura logging para consola y archivo."""
    logger = logging.getLogger("agent")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    logger.propagate = False

    # Evitar handlers duplicados al recargar el modulo.
    if logger.handlers:
        return logger

    # Formato
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Handler consola
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Handler archivo
    try:
        file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.warning(f"No se pudo crear archivo de log: {e}")

    return logger


# Inicializar logger global
logger = setup_logging()
