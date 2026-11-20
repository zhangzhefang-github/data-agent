"""Published semantic model registry."""

from app.semantic.models import SemanticModel
from app.semantic.validator import validate_semantic_model


class SemanticModelRegistry:
    def __init__(self) -> None:
        self._models: dict[tuple[str, str], SemanticModel] = {}

    def publish(self, model: SemanticModel) -> None:
        result = validate_semantic_model(model)
        if not result.valid:
            raise ValueError("Semantic model was not published because it contains hard errors")
        self._models[(model.id, model.version)] = model

    def get(self, model_id: str, version: str) -> SemanticModel:
        return self._models[(model_id, version)]
