"""Structured diagnostics emitted by semantic model validation."""

from typing import Literal

from pydantic import BaseModel, Field


class Diagnostic(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    object_id: str
    field: str
    reason: str
    suggestion: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[Diagnostic] = Field(default_factory=list)
    warnings: list[Diagnostic] = Field(default_factory=list)

    @property
    def diagnostics(self) -> list[Diagnostic]:
        return [*self.errors, *self.warnings]
