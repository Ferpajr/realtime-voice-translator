"""Tradução: local com NLLB-200 (GPU ou processador) e serviços da internet como reserva."""

import os
import time

from .config import Config
from .idiomas import MAPA_GOOGLE, NLLB_CODIGOS
from .texto import dividir_frases, dividir_para_traducao, para_pt_br
from .transcricao import tem_gpu_nvidia


class Tradutor:
    """Traduz textos para cfg.idioma_destino.

    Ordem de tentativa: NLLB local -> Google (web) -> deep-translator -> MyMemory.
    Quando o Google bloqueia, ele fica pausado por um tempo.
    """

    def __init__(self, cfg: Config, log=print):
        self.cfg = cfg
        self.log = log
        self._modelo = None
        self._tokenizador = None
        self._google_pausado_ate = 0.0
        self._sessao = None
        self.descricao = "internet"

    # ---------------- Local (NLLB) ----------------

    def carregar(self) -> str:
        """Baixa (1ª vez, ~1,2 GB) e carrega o NLLB. Se falhar, fica só na internet."""
        if self.cfg.motor_traducao != "local":
            return self.descricao
        try:
            import ctranslate2
            import sentencepiece
            from huggingface_hub import snapshot_download

            self.log("Preparando o tradutor local (na 1ª vez baixa ~1,2 GB)...")
            pasta = snapshot_download(
                self.cfg.modelo_traducao,
                allow_patterns=["model.bin", "config.json", "shared_vocabulary.txt",
                                "sentencepiece.bpe.model"],
            )
            self._tokenizador = sentencepiece.SentencePieceProcessor()
            self._tokenizador.load(os.path.join(pasta, "sentencepiece.bpe.model"))

            onde = "CPU"
            if tem_gpu_nvidia(self.cfg):
                try:
                    self._modelo = ctranslate2.Translator(pasta, device="cuda",
                                                          compute_type="float16")
                    onde = "GPU"
                except Exception as erro:
                    self.log(f"⚠️ Tradutor na GPU falhou ({type(erro).__name__}), "
                             "usando o processador.")
            if self._modelo is None:
                self._modelo = ctranslate2.Translator(pasta, device="cpu", compute_type="int8",
                                                      intra_threads=self.cfg.threads_cpu)

            teste = self._traduzir_local("Hello, how are you?", "en")   # aquecimento
            self.log(f"Teste do tradutor: 'Hello, how are you?' → '{teste}'")
            self.descricao = f"local ({'GPU' if onde == 'GPU' else 'processador'})"
        except Exception as erro:
            self._modelo = None
            self.log(f"⚠️ Tradutor local não funcionou ({type(erro).__name__}: {erro}). "
                     "Usando a tradução pela internet.")
            self.descricao = "internet"
        return self.descricao

    @property
    def local_disponivel(self) -> bool:
        return self._modelo is not None

    def _traduzir_local(self, texto: str, idioma_origem: str) -> str:
        origem = NLLB_CODIGOS.get(idioma_origem)
        destino = NLLB_CODIGOS.get(self.cfg.idioma_destino)
        if not origem or not destino:
            raise KeyError(f"idioma '{idioma_origem}' sem código no tradutor local")

        frases = dividir_para_traducao(texto, idioma_origem)
        entradas = [[origem] + self._tokenizador.encode(f, out_type=str) + ["</s>"]
                    for f in frases]
        resultados = self._modelo.translate_batch(
            entradas, target_prefix=[[destino]] * len(entradas),
            beam_size=2, max_decoding_length=256)

        traducoes = []
        for resultado in resultados:
            tokens = resultado.hypotheses[0]
            if tokens and tokens[0] == destino:
                tokens = tokens[1:]
            traducoes.append(self._tokenizador.decode(tokens))
        saida = " ".join(t.strip() for t in traducoes if t.strip())

        if self.cfg.traduzir_para_pt_br and self.cfg.idioma_destino == "pt":
            saida = para_pt_br(saida)
        return saida

    # ---------------- Internet ----------------

    def _http(self):
        if self._sessao is None:
            import requests
            self._sessao = requests.Session()   # reaproveita a conexão
        return self._sessao

    def _google_api(self, texto: str, origem: str) -> str:
        resposta = self._http().get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": origem, "tl": self.cfg.idioma_destino,
                    "dt": "t", "q": texto},
            timeout=10)
        resposta.raise_for_status()
        return "".join(parte[0] for parte in resposta.json()[0] if parte and parte[0])

    def _deep_translator(self, texto: str, origem: str) -> str:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source=origem, target=self.cfg.idioma_destino).translate(texto)

    def _mymemory(self, texto: str, origem: str) -> str:
        """Grátis, sem cadastro (limite de ~500 caracteres por pedido)."""
        partes, atual = [], ""
        for frase in dividir_frases(texto):
            if len(atual) + len(frase) > 450 and atual:
                partes.append(atual)
                atual = ""
            atual += frase + " "
        if atual.strip():
            partes.append(atual)
        resultado = []
        for parte in partes:
            resposta = self._http().get(
                "https://api.mymemory.translated.net/get",
                params={"q": parte.strip(), "langpair": f"{origem}|{self.cfg.idioma_destino}"},
                timeout=10)
            resposta.raise_for_status()
            resultado.append(resposta.json()["responseData"]["translatedText"])
        return " ".join(resultado)

    # ---------------- Entrada principal ----------------

    def traduzir(self, texto: str, idioma_origem: str):
        """Devolve a tradução ou None se nenhum método funcionar."""
        if self._modelo is not None:
            try:
                traducao = self._traduzir_local(texto, idioma_origem)
                if traducao:
                    return traducao
            except Exception as erro:
                self.log(f"⚠️ Tradutor local falhou ({type(erro).__name__}: {erro}), "
                         "usando a internet...")

        origem = MAPA_GOOGLE.get(idioma_origem, idioma_origem)
        tentativas = [
            ("Google API", self._google_api, True),
            ("Google (deep-translator)", self._deep_translator, True),
            ("MyMemory", self._mymemory, False),
        ]
        for nome, funcao, eh_google in tentativas:
            if eh_google and time.time() < self._google_pausado_ate:
                continue
            try:
                traducao = funcao(texto, origem)
                if traducao and traducao.strip():
                    return traducao.strip()
            except Exception as erro:
                self.log(f"⚠️ {nome} falhou ({type(erro).__name__}), tentando outro...")
                if eh_google and time.time() >= self._google_pausado_ate:
                    self._google_pausado_ate = time.time() + self.cfg.pausa_google_seg
                    self.log(f"⏸️ Google pausado por {self.cfg.pausa_google_seg:.0f}s.")
        return None
