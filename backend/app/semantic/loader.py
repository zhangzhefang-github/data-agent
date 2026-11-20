"""YAML loader for versioned canonical semantic models."""

from pathlib import Path

import yaml

from app.semantic.models import SemanticModel


SUPPORTED_VERSION = "1.0"


def load_semantic_model(path: Path) -> SemanticModel:
    """Load a semantic model without connecting to a database or Agent runtime."""
    payload = yaml.safe_load(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Semantic model YAML must contain an object")
    model = SemanticModel.model_validate(payload)
    if model.version != SUPPORTED_VERSION:
        raise ValueError(f"Unsupported semantic model version: {model.version}")
    return model
