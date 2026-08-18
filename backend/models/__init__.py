"""Core data contracts shared by the backend pipeline and IPC layer."""

from models.messages import SourceMessage, TranslationResult

__all__ = ["SourceMessage", "TranslationResult"]
