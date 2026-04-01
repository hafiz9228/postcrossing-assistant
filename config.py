import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent

BASE_FOLDER = Path(os.getenv("BASE_FOLDER", PROJECT_ROOT)).resolve()
DB_PATH = BASE_FOLDER / "postcards.db"
IMAGES_FOLDER = BASE_FOLDER / "images"
BACKUPS_FOLDER = BASE_FOLDER / "backups"

IMAGES_FOLDER.mkdir(parents=True, exist_ok=True)
BACKUPS_FOLDER.mkdir(parents=True, exist_ok=True)

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL")