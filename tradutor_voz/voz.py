"""Gera a voz traduzida (Piper local ou Edge pela internet) e toca no fone."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

from .config import Config

URL_VOZES = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
PASTA_VOZES = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "tradutor_voz", "vozes")


class Voz:
    """Transforma texto em um arquivo de áudio."""

    def __init__(self, cfg: Config, log=print):
        self.cfg = cfg
        self.log = log
        self._piper = None
        self.descricao = "Edge (internet)"

    # ---------------- Piper (local) ----------------

    def _baixar_voz_piper(self) -> str:
        """Baixa a voz na 1ª vez (~60 MB) e devolve o caminho do modelo."""
        import requests

        nome = self.cfg.voz_piper
        regiao, falante, qualidade = nome.split("-")
        idioma = regiao.split("_")[0]
        url_base = f"{URL_VOZES}/{idioma}/{regiao}/{falante}/{qualidade}/{nome}"
        os.makedirs(PASTA_VOZES, exist_ok=True)

        for extensao in (".onnx", ".onnx.json"):
            destino = os.path.join(PASTA_VOZES, nome + extensao)
            if os.path.exists(destino) and os.path.getsize(destino) > 0:
                continue
            self.log(f"Baixando a voz {nome}{extensao} (só na 1ª vez)...")
            temporario = destino + ".parcial"
            with requests.get(url_base + extensao, stream=True, timeout=60) as resposta:
                resposta.raise_for_status()
                with open(temporario, "wb") as arquivo:
                    for pedaco in resposta.iter_content(chunk_size=1 << 20):
                        arquivo.write(pedaco)
            os.replace(temporario, destino)
        return os.path.join(PASTA_VOZES, nome + ".onnx")

    def _preparar_espeak(self) -> str:
        """O espeak (pronúncia do Piper) não aceita acentos no caminho no Windows
        (ex.: pasta 'programação'): copia os dados para uma pasta sem acentos."""
        import piper
        origem = os.path.join(os.path.dirname(piper.__file__), "espeak-ng-data")
        if origem.isascii():
            return origem
        base = os.environ.get("LOCALAPPDATA", "")
        if not base or not base.isascii():
            base = "C:\\tradutor_dados"
        destino = os.path.join(base, "tradutor_voz", "espeak-ng-data")
        if not os.path.exists(os.path.join(destino, "phontab")):
            self.log("Copiando dados de pronúncia para uma pasta sem acentos (só na 1ª vez)...")
            shutil.copytree(origem, destino, dirs_exist_ok=True)
        return destino

    @staticmethod
    def _testar_piper_separado(modelo: str, pasta_espeak: str):
        """Testa o Piper em outro processo: se o espeak der erro, ele derruba o
        Python inteiro, então o teste fora daqui protege o programa principal.
        Devolve (ok, detalhe)."""
        codigo = (
            "import sys, wave, tempfile, os\n"
            "from piper import PiperVoice\n"
            "v = PiperVoice.load(sys.argv[1], espeak_data_dir=sys.argv[2])\n"
            "fd, c = tempfile.mkstemp(suffix='.wav'); os.close(fd)\n"
            "with wave.open(c, 'wb') as w: v.synthesize_wav('Teste.', w)\n"
            "os.remove(c); print('ok')\n"
        )
        ambiente = dict(os.environ, ESPEAK_DATA_PATH=pasta_espeak)
        try:
            resultado = subprocess.run(
                [sys.executable, "-c", codigo, modelo, pasta_espeak],
                capture_output=True, text=True, timeout=120, env=ambiente,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as erro:
            return False, type(erro).__name__
        if resultado.returncode == 0 and "ok" in resultado.stdout:
            return True, ""
        linhas = (resultado.stderr or resultado.stdout).strip().splitlines()
        return False, linhas[-1] if linhas else "sem detalhes"

    def carregar(self) -> str:
        """Prepara a voz local (Piper). Se não der, usa a voz da internet (Edge)."""
        if self.cfg.motor_voz != "piper":
            return self.descricao
        try:
            from piper import PiperVoice
            modelo = self._baixar_voz_piper()
            pasta_espeak = self._preparar_espeak()
            os.environ["ESPEAK_DATA_PATH"] = pasta_espeak
            ok, detalhe = self._testar_piper_separado(modelo, pasta_espeak)
            if not ok:
                raise RuntimeError(f"teste da voz falhou: {detalhe}")
            self._piper = PiperVoice.load(modelo, espeak_data_dir=pasta_espeak)
            os.remove(self.gerar("Pronto.", atrasado=False))   # aquecimento
            self.descricao = "Piper (local)"
        except Exception as erro:
            self._piper = None
            self.log(f"⚠️ Voz local não funcionou ({type(erro).__name__}: {erro}). "
                     "Usando a voz da internet.")
            self.descricao = "Edge (internet)"
        return self.descricao

    # ---------------- Geração ----------------

    def gerar(self, texto: str, atrasado: bool) -> str:
        """Gera um arquivo de áudio (wav ou mp3) e devolve o caminho."""
        cfg = self.cfg
        if self._piper is not None:
            from piper import SynthesisConfig
            fd, caminho = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            config = SynthesisConfig(length_scale=(cfg.velocidade_piper_atrasado if atrasado
                                                   else cfg.velocidade_piper))
            with wave.open(caminho, "wb") as arquivo_wav:
                self._piper.synthesize_wav(texto, arquivo_wav, syn_config=config)
            return caminho

        import asyncio
        import edge_tts
        fd, caminho = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        velocidade = cfg.velocidade_edge_atrasado if atrasado else cfg.velocidade_edge
        asyncio.run(edge_tts.Communicate(texto, cfg.voz_edge, rate=velocidade).save(caminho))
        return caminho


class Reprodutor:
    """Toca arquivos de áudio numa saída de som escolhida (via pygame)."""

    def __init__(self, nome_saida=None):
        self.nome_saida = nome_saida   # None = padrão do Windows

    def abrir(self):
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        if self.nome_saida:
            pygame.mixer.init(devicename=self.nome_saida)
        else:
            pygame.mixer.init()

    def tocar(self, caminho: str, parar: threading.Event):
        """Toca até o fim (ou até 'parar' ser sinalizado)."""
        import pygame
        pygame.mixer.music.load(caminho)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if parar.is_set():
                pygame.mixer.music.stop()
                break
            time.sleep(0.03)
        pygame.mixer.music.unload()

    def fechar(self):
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.quit()
