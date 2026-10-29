"""Lista dispositivos de som e captura áudio do microfone ou do PC (loopback).

As bibliotecas de áudio são importadas só quando usadas, para que o resto do
projeto (e os testes) funcione mesmo sem elas.
"""

import os
import threading
import warnings

import numpy as np

from .config import Config
from .segmentacao import volume_rms


# ---------------- Microfones ----------------

def listar_microfones():
    """Devolve ([(indice, nome), ...], indice_padrao).
    Usa a API MME do Windows: sem duplicados e aceita 16 kHz."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    indice_mme = next((i for i, a in enumerate(apis) if a["name"] == "MME"), None)
    microfones = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        if indice_mme is not None and d["hostapi"] != indice_mme:
            continue
        microfones.append((i, d["name"]))
    return microfones, sd.default.device[0]


def nome_microfone(indice: int) -> str:
    import sounddevice as sd
    return sd.query_devices(indice)["name"]


def calibrar_ruido(indice: int, cfg: Config) -> float:
    """Grava 2 s de 'silêncio' do microfone e devolve o limiar de fala."""
    import sounddevice as sd

    gravacao = sd.rec(int(2 * cfg.taxa_amostragem), samplerate=cfg.taxa_amostragem,
                      channels=1, dtype="float32", device=indice)
    sd.wait()
    ruido = volume_rms(gravacao[:, 0])
    return max(ruido * cfg.multiplicador_limiar, 0.008)


def capturar_microfone(indice: int, cfg: Config, ao_bloco, parar: threading.Event):
    """Lê o microfone em blocos de 0,1 s até 'parar' ser sinalizado."""
    import sounddevice as sd

    with sd.InputStream(device=indice, samplerate=cfg.taxa_amostragem, channels=1,
                        dtype="float32", blocksize=cfg.tamanho_bloco) as stream:
        while not parar.is_set():
            dados, _ = stream.read(cfg.tamanho_bloco)
            ao_bloco(dados[:, 0].copy())


# ---------------- Áudio do PC (loopback) ----------------

def _inicializar_com_na_thread():
    """O loopback do Windows usa COM, que precisa ser iniciado em cada thread.
    (Se já estiver iniciado em outro modo, o erro é ignorado.)"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)   # 0 = COINIT_MULTITHREADED
        except Exception:
            pass


def listar_saidas_captura():
    """Saídas de som cujo áudio pode ser capturado.
    Devolve ([(indice, nome), ...], indice_padrao)."""
    import soundcard as sc

    saidas = sc.all_speakers()
    padrao = sc.default_speaker()
    indice_padrao = next((i for i, s in enumerate(saidas) if s.id == padrao.id), 0)
    return [(i, s.name) for i, s in enumerate(saidas)], indice_padrao


def nome_saida_padrao() -> str:
    import soundcard as sc
    return sc.default_speaker().name


def capturar_audio_pc(nome_saida: str, cfg: Config, ao_bloco, parar: threading.Event):
    """Captura o que está tocando na saída 'nome_saida' até 'parar' ser sinalizado.
    Em silêncio, a biblioteca devolve zeros (não trava)."""
    _inicializar_com_na_thread()
    import soundcard as sc

    warnings.filterwarnings("ignore", message=".*discontinuity.*")
    loopback = sc.get_microphone(id=str(nome_saida), include_loopback=True)
    with loopback.recorder(samplerate=cfg.taxa_amostragem,
                           blocksize=cfg.tamanho_bloco) as gravador:
        while not parar.is_set():
            dados = gravador.record(numframes=cfg.tamanho_bloco)
            if dados.ndim > 1:
                dados = dados.mean(axis=1)    # estéreo -> mono
            ao_bloco(dados.astype(np.float32))


# ---------------- Saídas para tocar a tradução ----------------

def listar_saidas_reproducao():
    """Nomes das saídas de som onde a voz traduzida pode tocar (via pygame/SDL)."""
    import pygame
    from pygame._sdl2 import audio as sdl2_audio

    ja_iniciado = pygame.mixer.get_init() is not None
    if not ja_iniciado:
        pygame.mixer.init()
    nomes = list(sdl2_audio.get_audio_device_names(False))
    if not ja_iniciado:
        pygame.mixer.quit()
    return nomes
