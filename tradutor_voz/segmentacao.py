"""Junta pedaços de áudio de 0,1 s em frases completas.

Detecção de fala por energia (volume RMS): a frase termina após um silêncio
curto. Falas longas sem pausa são cortadas na micro-pausa mais silenciosa
dos últimos segundos, em vez de no meio de uma palavra.
"""

from collections import deque

import numpy as np

from .config import Config


def volume_rms(bloco: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(bloco)))) if len(bloco) else 0.0


def melhor_ponto_de_corte(volumes, janela_blocos: int, minimo_blocos: int) -> int:
    """Devolve o índice onde cortar: logo depois do bloco mais silencioso
    dentro dos últimos 'janela_blocos' (sem gerar uma frase menor que o mínimo)."""
    inicio = max(len(volumes) - janela_blocos, minimo_blocos)
    if inicio >= len(volumes):
        return len(volumes)
    trecho = volumes[inicio:]
    return inicio + int(np.argmin(trecho)) + 1


class Segmentador:
    """Recebe blocos de áudio e devolve frases prontas para transcrever.

    Uso:
        seg = Segmentador(limiar=0.01, cfg=Config())
        for bloco in blocos:
            for frase in seg.adicionar(bloco):
                ...  # frase é um np.ndarray com o áudio da frase
    """

    def __init__(self, limiar: float, cfg: Config = None):
        self.cfg = cfg or Config()
        self.limiar = limiar
        self._pre_fala = deque(maxlen=3)   # pedacinho antes da fala, p/ não cortar o início
        self._audio = []
        self._volumes = []
        self._falando = False
        self._silencio = 0.0

    @property
    def falando(self) -> bool:
        return self._falando

    def adicionar(self, bloco: np.ndarray):
        """Processa um bloco e devolve uma lista com as frases que terminaram."""
        cfg = self.cfg
        volume = volume_rms(bloco)
        prontas = []

        frase_terminou = False
        if volume > self.limiar:
            if not self._falando:
                self._falando = True
                for bloco_anterior, volume_anterior in self._pre_fala:
                    self._audio.append(bloco_anterior)
                    self._volumes.append(volume_anterior)
            self._silencio = 0.0
            self._audio.append(bloco)
            self._volumes.append(volume)
        elif self._falando:
            self._audio.append(bloco)
            self._volumes.append(volume)
            self._silencio += cfg.bloco_seg
            if self._silencio >= cfg.silencio_para_fim:
                frase_terminou = True
        else:
            self._pre_fala.append((bloco, volume))

        # Fala longa sem pausa: corta na pausa mais natural e o resto continua
        if (self._falando and not frase_terminou
                and len(self._audio) * cfg.bloco_seg >= cfg.duracao_maxima):
            corte = melhor_ponto_de_corte(
                self._volumes,
                janela_blocos=int(cfg.janela_corte_seg / cfg.bloco_seg),
                minimo_blocos=int(cfg.duracao_minima / cfg.bloco_seg),
            )
            self._entregar(self._audio[:corte], self._volumes[:corte], prontas)
            self._audio, self._volumes = self._audio[corte:], self._volumes[corte:]
            self._silencio = 0.0
            return prontas

        if frase_terminou:
            self._entregar(self._audio, self._volumes, prontas)
            self._audio, self._volumes = [], []
            self._falando = False
            self._silencio = 0.0
            self._pre_fala.clear()

        return prontas

    def _entregar(self, blocos, volumes, prontas):
        """Só entrega se houver fala de verdade: cliques e estalos curtos
        (seguidos de silêncio) não viram frase."""
        segundos_de_fala = sum(v > self.limiar for v in volumes) * self.cfg.bloco_seg
        if (len(blocos) * self.cfg.bloco_seg >= self.cfg.duracao_minima
                and segundos_de_fala >= self.cfg.fala_minima):
            prontas.append(np.concatenate(blocos))
