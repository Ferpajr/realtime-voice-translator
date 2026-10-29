import numpy as np

from tradutor_voz.config import Config
from tradutor_voz.segmentacao import Segmentador, melhor_ponto_de_corte, volume_rms

CFG = Config()
N = CFG.tamanho_bloco          # amostras em 0,1 s
LIMIAR = 0.01


def bloco(volume: float) -> np.ndarray:
    return np.full(N, volume, dtype=np.float32)


def alimentar(seg: Segmentador, volumes):
    frases = []
    for v in volumes:
        frases.extend(seg.adicionar(bloco(v)))
    return frases


def duracao(audio: np.ndarray) -> float:
    return round(len(audio) / CFG.taxa_amostragem, 1)


def test_volume_rms():
    assert volume_rms(bloco(0.5)) == 0.5
    assert volume_rms(np.array([], dtype=np.float32)) == 0.0


def test_silencio_nao_gera_frase():
    assert alimentar(Segmentador(LIMIAR, CFG), [0.0] * 50) == []


def test_fala_seguida_de_silencio_gera_uma_frase():
    # 0,5 s de silêncio, 2 s de fala, 0,5 s de silêncio (fim da frase)
    frases = alimentar(Segmentador(LIMIAR, CFG), [0.0] * 5 + [0.1] * 20 + [0.0] * 5)
    assert len(frases) == 1
    # 0,3 s antes da fala (para não cortar o início) + 2 s de fala + 0,5 s de silêncio
    assert duracao(frases[0]) == 2.8


def test_som_muito_curto_e_ignorado():
    # Um "clique" de 0,1 s seguido de silêncio não vira frase
    assert alimentar(Segmentador(LIMIAR, CFG), [0.1] + [0.0] * 10) == []


def test_fala_longa_e_cortada_na_pausa_mais_natural():
    # 12 s de fala contínua com uma micro-pausa em 6,5 s
    volumes = [0.003 if i == 65 else 0.1 for i in range(120)] + [0.0] * 10
    frases = alimentar(Segmentador(LIMIAR, CFG), volumes)
    assert len(frases) == 2
    assert duracao(frases[0]) == 6.6     # cortou logo depois da micro-pausa, não em 8 s
    assert duracao(frases[1]) == 5.9     # o resto continuou na frase seguinte


def test_melhor_ponto_de_corte_respeita_janela_e_minimo():
    volumes = [0.1] * 80
    volumes[10] = 0.0                    # pausa fora da janela dos últimos 3 s
    volumes[70] = 0.001                  # pausa dentro da janela
    assert melhor_ponto_de_corte(volumes, janela_blocos=30, minimo_blocos=5) == 71


def test_duas_frases_separadas_por_silencio():
    volumes = [0.1] * 10 + [0.0] * 6 + [0.1] * 10 + [0.0] * 6
    assert len(alimentar(Segmentador(LIMIAR, CFG), volumes)) == 2
