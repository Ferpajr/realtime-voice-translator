"""Testa o fluxo completo (captura -> frases -> transcrição -> tradução -> voz)
com componentes falsos: sem modelos, sem placa de som, sem internet."""

import os
import tempfile
import threading
import time

import numpy as np
import pytest

from tradutor_voz.config import Config
from tradutor_voz.pipeline import Pipeline


class TranscritorFalso:
    """Devolve respostas pré-definidas, na ordem das frases recebidas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def transcrever(self, audio, contexto=None, idioma=None):
        self.chamadas.append({"contexto": contexto, "idioma": idioma})
        if idioma is not None:                       # idioma forçado (re-transcrição)
            return self.ultimo[0], idioma, 1.0
        self.ultimo = self.respostas.pop(0)
        return self.ultimo


class TradutorFalso:
    def traduzir(self, texto, idioma):
        return f"[pt] {texto}"


class VozFalsa:
    def __init__(self):
        self.textos = []

    def gerar(self, texto, atrasado):
        self.textos.append(texto)
        fd, caminho = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        return caminho


class ReprodutorFalso:
    def __init__(self):
        self.tocados = []

    def abrir(self):
        pass

    def tocar(self, caminho, parar):
        self.tocados.append(caminho)

    def fechar(self):
        pass


def blocos_de_fala(cfg, *frases_seg):
    """Gera blocos de 0,1 s: cada frase com N segundos de fala + 0,6 s de silêncio."""
    blocos = []
    for segundos in frases_seg:
        blocos += [np.full(cfg.tamanho_bloco, 0.1, np.float32)] * int(segundos * 10)
        blocos += [np.zeros(cfg.tamanho_bloco, np.float32)] * 6
    return blocos


def rodar(respostas, frases_seg, cfg=None, esperar_eventos=1, tipo_final="traducao"):
    cfg = cfg or Config(aviso_sem_som_seg=999)
    eventos = []
    trava = threading.Lock()

    def ao_evento(tipo, dados):
        with trava:
            eventos.append((tipo, dados))

    transcritor, voz, reprodutor = TranscritorFalso(respostas), VozFalsa(), ReprodutorFalso()
    pipeline = Pipeline(cfg, ao_evento, transcritor=transcritor,
                        tradutor=TradutorFalso(), voz=voz)

    def captura_falsa():
        for b in blocos_de_fala(cfg, *frases_seg):
            pipeline._receber_bloco(b)
        pipeline._parar.wait()

    pipeline.iniciar("pc", "falso", reprodutor=reprodutor, captura=captura_falsa, limiar=0.01)

    limite = time.time() + 5
    while time.time() < limite:
        with trava:
            finais = [e for e in eventos if e[0] == tipo_final]
        if len(finais) >= esperar_eventos:
            break
        time.sleep(0.05)
    time.sleep(0.3)   # deixa a fala terminar
    pipeline.parar()
    return eventos, transcritor, voz, reprodutor


def tipos(eventos):
    return [t for t, _ in eventos]


def test_fluxo_completo_traduz_e_fala():
    eventos, _, voz, reprodutor = rodar([("Hello there.", "en", 0.99)], [2])
    t = tipos(eventos)
    assert t[0] == "iniciado" and t[-1] == "parado"
    assert t.index("som") < t.index("original") < t.index("traducao") < t.index("metricas")
    assert dict(eventos)["traducao"]["texto"] == "[pt] Hello there."
    assert voz.textos == ["[pt] Hello there."]
    assert len(reprodutor.tocados) == 1
    assert "latencia" in t


def test_alucinacao_e_ignorada():
    eventos, _, voz, _ = rodar([("Thanks for watching!", "en", 0.95)], [2],
                               tipo_final="ignorado")
    ignorados = [d for t, d in eventos if t == "ignorado"]
    assert ignorados and ignorados[0]["motivo"] == "alucinacao"
    assert "traducao" not in tipos(eventos)
    assert voz.textos == []


def test_mesmo_idioma_nao_e_traduzido():
    eventos, _, _, _ = rodar([("Bom dia a todos.", "pt", 0.99)], [2], tipo_final="ignorado")
    assert [d["motivo"] for t, d in eventos if t == "ignorado"] == ["mesmo_idioma"]
    assert "traducao" not in tipos(eventos)


def test_fala_curta_incerta_mantem_o_idioma_da_conversa():
    respostas = [("私の名前は西村じゃない", "ja", 0.98),   # frase normal em japonês
                 ("えっ", "ko", 0.20)]                   # interjeição: Whisper em dúvida
    eventos, transcritor, _, _ = rodar(respostas, [2, 1], esperar_eventos=2)
    originais = [d for t, d in eventos if t == "original"]
    assert originais[1]["idioma"] == "ja" and originais[1]["mantido"] is True
    assert transcritor.chamadas[-1]["idioma"] == "ja"     # re-transcreveu forçando japonês
    assert len([t for t in tipos(eventos) if t == "traducao"]) == 2


def test_frase_anterior_vira_contexto():
    respostas = [("First part of the sentence", "en", 0.99), ("and the end.", "en", 0.99)]
    _, transcritor, _, _ = rodar(respostas, [2, 1], esperar_eventos=2)
    assert transcritor.chamadas[0]["contexto"] is None
    assert transcritor.chamadas[1]["contexto"] == "First part of the sentence"


def test_modo_so_legendas_nao_gera_voz():
    cfg = Config(aviso_sem_som_seg=999, falar_traducao=False)
    eventos, _, voz, reprodutor = rodar([("Hello.", "en", 0.99)], [2], cfg=cfg,
                                        tipo_final="metricas")
    assert "traducao" in tipos(eventos)
    assert voz.textos == [] and reprodutor.tocados == []


@pytest.mark.parametrize("eco", [True, False])
def test_protecao_contra_eco_silencia_durante_a_fala(eco):
    cfg = Config(aviso_sem_som_seg=999)
    pipeline = Pipeline(cfg, transcritor=TranscritorFalso([]), tradutor=TradutorFalso(),
                        voz=VozFalsa())
    pipeline._bloquear_eco = eco
    pipeline._tocando.set()
    pipeline._receber_bloco(np.full(cfg.tamanho_bloco, 0.5, np.float32))
    recebido = pipeline.fila_blocos.get_nowait()
    assert bool(recebido.max() == 0.0) is eco
