"""Reconhecimento de fala com faster-whisper (GPU NVIDIA ou processador)."""

import os

import numpy as np

from .config import Config


def configurar_cuda_windows():
    """Aponta o Windows para as DLLs da NVIDIA instaladas via pip
    (requirements-gpu.txt), sem precisar instalar o CUDA manualmente.
    Precisa rodar ANTES de importar faster_whisper/ctranslate2."""
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    for base in list(nvidia.__path__):
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            pasta = os.path.join(base, sub, "bin")
            if os.path.isdir(pasta):
                os.add_dll_directory(pasta)
                os.environ["PATH"] = pasta + os.pathsep + os.environ.get("PATH", "")


def tem_gpu_nvidia(cfg: Config) -> bool:
    """True se existe uma placa NVIDIA utilizável e o uso da GPU está ligado."""
    if not cfg.usar_gpu:
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def nome_modelo_whisper(cfg: Config, gpu: bool) -> str:
    if cfg.modelo_whisper != "auto":
        return cfg.modelo_whisper
    return cfg.modelo_whisper_gpu if gpu else cfg.modelo_whisper_cpu


class Transcritor:
    """Detecta o idioma e transcreve o áudio."""

    def __init__(self, cfg: Config, log=print):
        self.cfg = cfg
        self.log = log
        self.modelo = None
        self.descricao = ""

    def carregar(self) -> str:
        """Perfil automático: GPU se houver NVIDIA; senão, modelo leve no processador."""
        from faster_whisper import WhisperModel

        cfg = self.cfg
        if tem_gpu_nvidia(cfg):
            nome = nome_modelo_whisper(cfg, gpu=True)
            try:
                self.log(f"Carregando Whisper '{nome}' na GPU NVIDIA...")
                modelo = WhisperModel(nome, device="cuda", compute_type="float16")
                # Teste rápido: força o carregamento das DLLs da NVIDIA agora
                segmentos, _ = modelo.transcribe(np.zeros(cfg.taxa_amostragem, dtype=np.float32))
                list(segmentos)
                self.modelo = modelo
                self.descricao = f"GPU ({nome})"
                return self.descricao
            except Exception as erro:
                self.log(f"⚠️ GPU não funcionou ({type(erro).__name__}: {erro}). "
                         "Dica: pip install -r requirements-gpu.txt")
        elif cfg.usar_gpu:
            self.log("Nenhuma placa NVIDIA encontrada: usando o perfil para processador.")

        nome = nome_modelo_whisper(cfg, gpu=False)
        self.log(f"Carregando Whisper '{nome}' no processador ({cfg.threads_cpu} núcleos)...")
        self.modelo = WhisperModel(nome, device="cpu", compute_type="int8",
                                   cpu_threads=cfg.threads_cpu)
        self.descricao = f"processador ({nome})"
        return self.descricao

    def transcrever(self, audio: np.ndarray, contexto=None, idioma=None):
        """Devolve (texto, idioma, confiança). 'contexto' é a frase anterior;
        'idioma' força um idioma (o Whisper não tenta adivinhar)."""
        segmentos, info = self.modelo.transcribe(
            audio, beam_size=1, vad_filter=True, condition_on_previous_text=False,
            initial_prompt=contexto, language=idioma)
        texto = " ".join(s.text.strip() for s in segmentos).strip()
        return texto, info.language, info.language_probability
