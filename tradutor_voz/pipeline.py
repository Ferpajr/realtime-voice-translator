"""Liga todas as etapas em threads conectadas por filas:

    captura -> [blocos] -> segmentação -> [frases] -> Whisper + tradução + voz
            -> [falas] -> reprodução no fone

A interface (terminal ou janela) recebe tudo por eventos:
    ao_evento(tipo: str, dados: dict)

Tipos de evento:
    status       {mensagem}                   andamento (carregando modelos etc.)
    aviso        {mensagem}                   algo merece atenção, mas segue rodando
    erro         {mensagem}                   falha que interrompeu a captura
    carregado    {whisper, traducao, voz}     modelos prontos
    iniciado     {eco}                        captura começou (eco=True: mesmo dispositivo)
    parado       {}
    som          {}                           primeiro som detectado
    sem_som      {mensagem}                   nenhum som chegou após alguns segundos
    nivel        {valor, limiar}              volume atual (10x por segundo, p/ medidor)
    original     {texto, idioma, confianca, mantido}
    ignorado     {motivo, texto}
    traducao     {texto, original, idioma}
    metricas     {fila, whisper, traducao, voz, atrasado}
    latencia     {total, espera}              do fim da fala ao início da voz
"""

import os
import queue
import threading
import time

import numpy as np

from .config import Config
from .segmentacao import Segmentador, volume_rms
from .texto import dividir_frases, eh_alucinacao, nomes_parecidos


class Pipeline:
    def __init__(self, cfg: Config = None, ao_evento=None,
                 transcritor=None, tradutor=None, voz=None):
        self.cfg = cfg or Config()
        self._ao_evento = ao_evento or (lambda tipo, dados: None)

        # Componentes: podem ser injetados (útil para testes)
        if transcritor is None:
            from .transcricao import Transcritor
            transcritor = Transcritor(self.cfg, log=self._status)
        if tradutor is None:
            from .traducao import Tradutor
            tradutor = Tradutor(self.cfg, log=self._status)
        if voz is None:
            from .voz import Voz
            voz = Voz(self.cfg, log=self._status)
        self.transcritor, self.tradutor, self.voz = transcritor, tradutor, voz

        self.fila_blocos = queue.Queue(maxsize=600)   # pedaços de 0,1 s
        self.fila_frases = queue.Queue()              # (áudio, fim_da_fala)
        self.fila_falas = queue.Queue()               # (arquivo, fim_da_fala, pronto_em)

        self._parar = threading.Event()
        self._tocando = threading.Event()
        self._bloquear_eco = False
        self._threads = []
        self.reprodutor = None

    # ---------------- Eventos ----------------

    def _emitir(self, tipo: str, **dados):
        try:
            self._ao_evento(tipo, dados)
        except Exception:
            pass   # um erro na interface nunca derruba o pipeline

    def _status(self, mensagem: str):
        tipo = "aviso" if mensagem.startswith(("⚠️", "⏸️")) else "status"
        self._emitir(tipo, mensagem=mensagem)

    # ---------------- Ciclo de vida ----------------

    def carregar(self):
        """Carrega Whisper, tradutor e voz (pode demorar na 1ª vez: downloads)."""
        from .transcricao import configurar_cuda_windows
        configurar_cuda_windows()
        whisper = self.transcritor.carregar()
        traducao = self.tradutor.carregar()
        voz = self.voz.carregar()
        self._emitir("carregado", whisper=whisper, traducao=traducao, voz=voz)

    @property
    def rodando(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def iniciar(self, fonte: str, dispositivo, saida_traducao=None,
                reprodutor=None, captura=None, limiar=None):
        """Começa a traduzir.

        fonte: "pc" (dispositivo = nome da saída capturada) ou
               "microfone" (dispositivo = índice do microfone).
        saida_traducao: nome da saída onde a voz toca (None = padrão do Windows).
        reprodutor/captura/limiar: opcionais, para testes.
        """
        if self.rodando:
            return
        from . import captura as cap

        self._parar.clear()
        self._tocando.clear()
        for fila in (self.fila_blocos, self.fila_frases, self.fila_falas):
            self._esvaziar(fila)

        cfg = self.cfg
        if captura is None:
            if fonte == "pc":
                nome_saida = saida_traducao or cap.nome_saida_padrao()
                self._bloquear_eco = nomes_parecidos(dispositivo, nome_saida)
                if limiar is None:
                    limiar = cfg.limiar_pc

                def captura():
                    cap.capturar_audio_pc(dispositivo, cfg, self._receber_bloco, self._parar)
            else:
                self._bloquear_eco = False
                if limiar is None:
                    self._status("Calibrando o microfone: fique em silêncio por 2 segundos...")
                    limiar = cap.calibrar_ruido(dispositivo, cfg)

                def captura():
                    cap.capturar_microfone(dispositivo, cfg, self._receber_bloco, self._parar)
        elif limiar is None:
            limiar = cfg.limiar_pc

        if reprodutor is None:
            from .voz import Reprodutor
            reprodutor = Reprodutor(saida_traducao)
        self.reprodutor = reprodutor
        self.reprodutor.abrir()

        self._threads = [
            threading.Thread(target=self._executar_captura, args=(captura,),
                             name="captura", daemon=True),
            threading.Thread(target=self._laco_segmentacao, args=(limiar,),
                             name="segmentacao", daemon=True),
            threading.Thread(target=self._laco_trabalho, name="trabalho", daemon=True),
            threading.Thread(target=self._laco_fala, name="fala", daemon=True),
        ]
        # Avisa ANTES de ligar as threads, para "iniciado" ser sempre o 1º evento
        self._emitir("iniciado", eco=self._bloquear_eco)
        for t in self._threads:
            t.start()

    def parar(self):
        """Para tudo e libera a saída de som."""
        self._parar.set()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=3)
        self._threads = []
        if self.reprodutor is not None:
            try:
                self.reprodutor.fechar()
            except Exception:
                pass
        while not self.fila_falas.empty():
            caminho, _, _ = self.fila_falas.get_nowait()
            self._apagar(caminho)
        self._emitir("parado")

    # ---------------- Threads ----------------

    def _executar_captura(self, captura):
        try:
            captura()
        except Exception as erro:
            if not self._parar.is_set():
                self._emitir("erro", mensagem=f"Falha na captura de áudio: "
                                              f"{type(erro).__name__}: {erro}")
                self._parar.set()

    def _receber_bloco(self, bloco: np.ndarray):
        if self._bloquear_eco and self._tocando.is_set():
            bloco = np.zeros_like(bloco)   # ignora o que tocar enquanto a tradução fala
        try:
            self.fila_blocos.put_nowait(bloco)
        except queue.Full:
            pass

    def _laco_segmentacao(self, limiar: float):
        cfg = self.cfg
        segmentador = Segmentador(limiar, cfg)
        inicio = time.time()
        houve_som = avisou = False

        while not self._parar.is_set():
            try:
                bloco = self.fila_blocos.get(timeout=cfg.bloco_seg)
            except queue.Empty:
                bloco = np.zeros(cfg.tamanho_bloco, dtype=np.float32)

            volume = volume_rms(bloco)
            self._emitir("nivel", valor=volume, limiar=limiar)

            if not houve_som:
                if volume > limiar:
                    houve_som = True
                    self._emitir("som")
                elif not avisou and time.time() - inicio > cfg.aviso_sem_som_seg:
                    avisou = True
                    self._emitir("sem_som", mensagem=(
                        f"Nenhum som chegou em {cfg.aviso_sem_som_seg:.0f}s. Confira se o "
                        "áudio está tocando no dispositivo escolhido."))

            for frase in segmentador.adicionar(bloco):
                self.fila_frases.put((frase, time.time()))
                if self.fila_frases.qsize() > 2:
                    self._emitir("aviso", mensagem=f"{self.fila_frases.qsize()} frases na "
                                                   "fila (o processamento está atrasando)")

    def _laco_trabalho(self):
        cfg = self.cfg
        ultimo_texto, ultimo_tempo = None, 0.0
        ultimo_idioma, tempo_idioma = None, 0.0

        while not self._parar.is_set():
            try:
                audio, fim_da_fala = self.fila_frases.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                contexto = (ultimo_texto[-200:]
                            if cfg.usar_contexto and ultimo_texto
                            and time.time() - ultimo_tempo < 15 else None)

                t_inicio = time.time()
                texto, idioma, confianca = self.transcritor.transcrever(audio, contexto)
                if not texto:
                    continue

                # Fala curta com dúvida no idioma: mantém o idioma da conversa
                mantido = False
                if (confianca < cfg.confianca_minima and ultimo_idioma
                        and time.time() - tempo_idioma < cfg.manter_idioma_seg):
                    if idioma != ultimo_idioma:
                        texto, _, _ = self.transcritor.transcrever(audio, contexto,
                                                                   idioma=ultimo_idioma)
                    idioma, mantido = ultimo_idioma, True
                t_whisper = time.time()

                if not texto:
                    continue
                if eh_alucinacao(texto):
                    self._emitir("ignorado", motivo="alucinacao", texto=texto)
                    continue
                if confianca < cfg.confianca_minima and not mantido:
                    self._emitir("ignorado", motivo="confianca", texto=texto)
                    continue

                ultimo_texto, ultimo_tempo = texto, time.time()
                ultimo_idioma, tempo_idioma = idioma, time.time()
                self._emitir("original", texto=texto, idioma=idioma,
                             confianca=confianca, mantido=mantido)

                if idioma == cfg.idioma_destino:
                    self._emitir("ignorado", motivo="mesmo_idioma", texto=texto)
                    continue

                traducao = self.tradutor.traduzir(texto, idioma)
                t_traducao = time.time()
                if not traducao:
                    self._emitir("aviso", mensagem="Nenhum tradutor respondeu.")
                    continue
                self._emitir("traducao", texto=traducao, original=texto, idioma=idioma)

                atrasado = self.fila_falas.qsize() >= 1 or self.fila_frases.qsize() >= 1
                if not cfg.falar_traducao:
                    self._emitir("metricas", fila=t_inicio - fim_da_fala,
                                 whisper=t_whisper - t_inicio,
                                 traducao=t_traducao - t_whisper, voz=0.0, atrasado=False)
                    continue

                # Voz frase a frase: a 1ª já toca enquanto as outras são geradas
                for i, frase in enumerate(dividir_frases(traducao)):
                    if self._parar.is_set():
                        break
                    try:
                        caminho = self.voz.gerar(frase, atrasado)
                    except Exception as erro:
                        self._emitir("aviso", mensagem=f"Não consegui gerar a voz "
                                                       f"({type(erro).__name__}).")
                        continue
                    if i == 0:
                        self._emitir("metricas", fila=t_inicio - fim_da_fala,
                                     whisper=t_whisper - t_inicio,
                                     traducao=t_traducao - t_whisper,
                                     voz=time.time() - t_traducao, atrasado=atrasado)
                    self.fila_falas.put((caminho, fim_da_fala if i == 0 else None, time.time()))
            except Exception as erro:
                self._emitir("aviso", mensagem=f"Erro ao processar uma frase: "
                                               f"{type(erro).__name__}: {erro}")

    def _laco_fala(self):
        while not self._parar.is_set():
            try:
                caminho, fim_da_fala, pronto_em = self.fila_falas.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if fim_da_fala is not None:
                    agora = time.time()
                    self._emitir("latencia", total=agora - fim_da_fala, espera=agora - pronto_em)
                self._tocando.set()
                self.reprodutor.tocar(caminho, self._parar)
            except Exception as erro:
                self._emitir("aviso", mensagem=f"Erro ao tocar a voz ({type(erro).__name__}).")
            finally:
                time.sleep(0.15)   # margem para o fim do som não ser capturado
                self._tocando.clear()
                self._apagar(caminho)

    # ---------------- Utilidades ----------------

    @staticmethod
    def _esvaziar(fila):
        while True:
            try:
                fila.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _apagar(caminho):
        try:
            os.remove(caminho)
        except (OSError, TypeError):
            pass
