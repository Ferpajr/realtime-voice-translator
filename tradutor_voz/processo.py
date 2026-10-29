"""Roda o Pipeline (Whisper, tradutor, voz) num PROCESSO separado da interface.

Por quê: o Qt (interface) e as bibliotecas de IA (CTranslate2, sentencepiece,
ONNX Runtime) trazem cada um suas próprias DLLs do Visual C++. No mesmo processo
isso causa travamentos aleatórios ("access violation"). Em processos separados
elas nunca dividem memória — e se o motor cair, a janela continua aberta.

O motor é iniciado com `python -m tradutor_voz.processo` (e não com
multiprocessing, que no Windows reimportaria o script da janela — e o Qt — no
processo filho). A conversa é por linhas JSON:

    janela -> motor (stdin):   {"cmd": "iniciar", "args": [fonte, dispositivo, saida]}
                               {"cmd": "parar"} | {"cmd": "config", "args": [chave, valor]}
                               {"cmd": "sair"}
    motor -> janela (stdout):  {"tipo": "...", "dados": {...}}  (eventos do Pipeline)

Tudo o mais que o motor imprimir vai para o stderr, que aparece no terminal.
"""

import dataclasses
import json
import os
import queue
import subprocess
import sys
import threading

from .config import Config


# ======================= Lado do motor (processo filho) =======================

def _executar_motor(cfg_json: str):
    import faulthandler
    faulthandler.enable()

    # Guarda o stdout real só para os eventos; qualquer outro print (das
    # bibliotecas, inclusive em C) vai para o stderr e aparece no terminal.
    canal = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    trava = threading.Lock()

    def ao_evento(tipo, dados):
        if tipo == "nivel" and canal.closed:
            return
        linha = json.dumps({"tipo": tipo, "dados": dados}, ensure_ascii=False, default=str)
        with trava:
            canal.write(linha + "\n")

    from .pipeline import Pipeline

    cfg = Config(**json.loads(cfg_json))
    pipeline = Pipeline(cfg, ao_evento=ao_evento)
    try:
        pipeline.carregar()
    except Exception as erro:
        ao_evento("erro", {"mensagem": f"Falha ao carregar os modelos: "
                                       f"{type(erro).__name__}: {erro}"})

    for linha in sys.stdin:                      # termina se a janela fechar
        try:
            comando = json.loads(linha)
        except json.JSONDecodeError:
            continue
        nome, args = comando.get("cmd"), comando.get("args", [])
        try:
            if nome == "iniciar":
                pipeline.iniciar(*args)
            elif nome == "parar":
                pipeline.parar()
            elif nome == "config":
                setattr(cfg, args[0], args[1])
            elif nome == "sair":
                break
        except Exception as erro:
            ao_evento("erro", {"mensagem": f"Não foi possível '{nome}': "
                                           f"{type(erro).__name__}: {erro}"})
    if pipeline.rodando:
        pipeline.parar()


# ======================= Lado da janela (processo principal) =======================

class MotorEmProcesso:
    """Mesma ideia do Pipeline (carregar / iniciar / parar), mas rodando em
    outro processo. A interface chama coletar_eventos() periodicamente."""

    def __init__(self, cfg: Config = None):
        self.cfg = cfg or Config()
        self._processo = None
        self._eventos = queue.Queue()
        self._encerrando = False
        self._avisou_queda = False

    def carregar(self):
        """Inicia o processo do motor (ele carrega os modelos e avisa com 'carregado')."""
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ambiente = dict(os.environ, PYTHONIOENCODING="utf-8",
                        PYTHONPATH=raiz + os.pathsep + os.environ.get("PYTHONPATH", ""))
        self._processo = subprocess.Popen(
            [sys.executable, "-u", "-m", "tradutor_voz.processo",
             json.dumps(dataclasses.asdict(self.cfg))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,  # stderr -> terminal
            text=True, encoding="utf-8", bufsize=1, cwd=raiz, env=ambiente)
        threading.Thread(target=self._ler_eventos, args=(self._processo.stdout,),
                         name="leitor-motor", daemon=True).start()

    def _ler_eventos(self, saida):
        for linha in saida:
            try:
                mensagem = json.loads(linha)
                self._eventos.put((mensagem["tipo"], mensagem["dados"]))
            except (json.JSONDecodeError, KeyError):
                pass

    def _enviar(self, nome: str, *args):
        if not self.vivo:
            return
        try:
            self._processo.stdin.write(json.dumps({"cmd": nome, "args": list(args)}) + "\n")
            self._processo.stdin.flush()
        except OSError:
            pass   # o motor caiu; coletar_eventos() avisa

    def iniciar(self, fonte, dispositivo, saida_traducao=None):
        self._enviar("iniciar", fonte, dispositivo, saida_traducao)

    def parar(self):
        self._enviar("parar")

    def definir(self, chave: str, valor):
        setattr(self.cfg, chave, valor)
        self._enviar("config", chave, valor)

    @property
    def vivo(self) -> bool:
        return self._processo is not None and self._processo.poll() is None

    def coletar_eventos(self, limite: int = 300):
        """Devolve os eventos que chegaram desde a última chamada.
        Se o motor morrer sozinho, devolve (uma vez) um evento 'motor_caiu'."""
        eventos = []
        for _ in range(limite):
            try:
                eventos.append(self._eventos.get_nowait())
            except queue.Empty:
                break
        if (self._processo is not None and self._processo.poll() is not None
                and not self._encerrando and not self._avisou_queda and not eventos):
            self._avisou_queda = True
            codigo = self._processo.returncode
            eventos.append(("motor_caiu", {"mensagem": (
                f"O motor de tradução fechou inesperadamente (código {codigo}). "
                "Veja o terminal para detalhes. Clique em Reiniciar motor para tentar de novo.")}))
        return eventos

    def encerrar(self, espera: float = 5):
        self._encerrando = True
        if self.vivo:
            self._enviar("sair")
            try:
                self._processo.wait(timeout=espera)
            except subprocess.TimeoutExpired:
                self._processo.kill()


if __name__ == "__main__":
    _executar_motor(sys.argv[1] if len(sys.argv) > 1 else "{}")
