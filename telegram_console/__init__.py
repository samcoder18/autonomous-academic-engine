"""Telegram control console for the legal-academic workflow."""

from .config import TelegramConsoleConfig
from .orchestrator import WorkflowOrchestrator

__all__ = ["TelegramConsoleConfig", "WorkflowOrchestrator"]
