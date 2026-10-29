"""Tradutor de voz em tempo real: Whisper + NLLB + Piper, 100% local."""

import os

__version__ = "0.8.0"

# Esconde a mensagem de boas-vindas do pygame no console
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
