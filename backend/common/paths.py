"""Paths shared by local launches and container images."""

from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
WEB_STATIC_DIR = PROJECT_DIR / "frontend" / "static"
RESOURCES_DIR = BACKEND_DIR / "resources"
ONTOLOGIES_DIR = PROJECT_DIR / "ontologies"
