"""Testa a captura de áudio do PC com uma placa de som falsa."""

import threading
import types

import numpy as np

from tradutor_voz import captura
from tradutor_voz.config import Config


def soundcard_falso(blocos):
    """Imita a biblioteca soundcard: um loopback que devolve blocos estéreo."""
    class Gravador:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def record(self, numframes):
            return blocos.pop(0) if blocos else np.zeros((numframes, 2), np.float32)

    class Microfone:
        def recorder(self, samplerate, blocksize):
            return Gravador()

    return types.SimpleNamespace(get_microphone=lambda id, include_loopback: Microfone())


def test_biblioteca_e_carregada_antes_de_iniciar_o_com(monkeypatch):
    # Regressão: iniciar o COM antes de importar a soundcard fazia a importação
    # falhar com "Error 0x100000001" (S_FALSE) no processo do motor.
    ordem = []
    cfg = Config()
    falso = soundcard_falso([np.ones((cfg.tamanho_bloco, 2), np.float32)])

    def carregar():
        ordem.append("importa soundcard")
        return falso

    monkeypatch.setattr(captura, "_carregar_soundcard", carregar)
    monkeypatch.setattr(captura, "_inicializar_com_na_thread",
                        lambda: ordem.append("inicia COM"))

    parar = threading.Event()
    recebidos = []

    def ao_bloco(bloco):
        recebidos.append(bloco)
        parar.set()

    captura.capturar_audio_pc("Headphones", cfg, ao_bloco, parar)
    assert ordem == ["importa soundcard", "inicia COM"]


def test_audio_estereo_vira_mono(monkeypatch):
    cfg = Config()
    estereo = np.stack([np.full(cfg.tamanho_bloco, 0.2), np.full(cfg.tamanho_bloco, 0.4)],
                       axis=1).astype(np.float32)
    monkeypatch.setattr(captura, "_carregar_soundcard", lambda: soundcard_falso([estereo]))
    monkeypatch.setattr(captura, "_inicializar_com_na_thread", lambda: None)

    parar = threading.Event()
    recebidos = []

    def ao_bloco(bloco):
        recebidos.append(bloco)
        parar.set()

    captura.capturar_audio_pc("Headphones", cfg, ao_bloco, parar)
    assert recebidos[0].ndim == 1 and np.allclose(recebidos[0], 0.3)
