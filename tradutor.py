"""
Tradutor de Voz em Tempo Real - v7
-----------------------------------
Áudio do PC (reuniões/vídeos) ou Microfone
    -> Whisper (detecta idioma + transcreve) -> NLLB (traduz) -> Piper (voz) -> fone

Tudo roda localmente: o áudio nunca sai do computador.

v7: perfil automático (com ou sem placa NVIDIA), tradução ajustada para o
    português do Brasil e japonês/chinês divididos pelos espaços (sem perder trechos).
v6: tradução local (NLLB-200) e falas curtas mantêm o idioma da conversa.
v5: corte das falas longas na pausa mais natural e frase anterior como contexto.
v4: voz local (Piper), pausa automática no Google e filtro de alucinações.
v3: captura do áudio do PC (loopback do Windows) e proteção contra eco.

Uso: python tradutor.py   (Ctrl+C para sair)
"""

import asyncio
import os
import queue
import re
import sys
import tempfile
import threading
import shutil
import subprocess
import time
import warnings
import wave
from collections import deque

# Evita erro de acentos/emojis no console do Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _configurar_cuda_windows():
    """Aponta o Windows para as DLLs da NVIDIA instaladas via pip
    (requirements-gpu.txt), sem precisar instalar o CUDA manualmente."""
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


_configurar_cuda_windows()

import numpy as np
import sounddevice as sd
import edge_tts
import pygame
import requests
from deep_translator import GoogleTranslator
from faster_whisper import WhisperModel

# =================== CONFIGURAÇÕES ===================
IDIOMA_DESTINO = "pt"                 # idioma que você quer ouvir

MOTOR_TRADUCAO = "local"              # "local" (GPU, sem internet) | "internet" (Google/MyMemory)
MODELO_TRADUCAO = "JustFrederik/nllb-200-distilled-600M-ct2-float16"

MOTOR_VOZ = "piper"                   # "piper" (local, rápido) | "edge" (internet, mais natural)
VOZ_PIPER = "pt_BR-faber-medium"      # outras: "pt_BR-cadu-medium", "pt_BR-jeff-medium"
VELOCIDADE_PIPER = 0.87               # menor = mais rápido (1.0 = normal)
VELOCIDADE_PIPER_ATRASADO = 0.70      # quando há traduções acumuladas na fila

VOZ = "pt-BR-FranciscaNeural"         # voz da internet (Edge). Outra: "pt-BR-AntonioNeural"
VELOCIDADE_VOZ = "+15%"               # velocidade normal da voz (Edge)
VELOCIDADE_ATRASADO = "+45%"          # velocidade quando há traduções acumuladas (Edge)

# Escolhas feitas ao iniciar. Deixe None para o programa perguntar,
# ou fixe o valor para pular a pergunta:
FONTE = None                          # "pc" (áudio do computador) ou "microfone"
MICROFONE = None                      # número do microfone, ex: 2
CAPTURAR_SAIDA = None                 # número da saída de som a capturar (modo "pc")
SAIDA_TRADUCAO = None                 # número da saída onde a tradução toca

USAR_GPU = True                       # usa a placa NVIDIA se estiver disponível
                                      # (False = simula um PC sem NVIDIA)

# Perfil automático: o programa escolhe o modelo do Whisper pelo hardware.
# Opções: tiny | base | small | medium | large-v3-turbo
MODELO_WHISPER = "auto"               # "auto" = usa as duas linhas abaixo
MODELO_WHISPER_GPU = "small"          # com NVIDIA ("large-v3-turbo" = melhor qualidade)
MODELO_WHISPER_CPU = "base"           # sem NVIDIA (leve, para rodar rápido no processador)
THREADS_CPU = min(8, os.cpu_count() or 4)   # núcleos usados no modo processador

TRADUZIR_PARA_PT_BR = True            # troca construções de Portugal por brasileiras

TAXA_AMOSTRAGEM = 16000               # Whisper trabalha com 16 kHz
BLOCO_SEG = 0.1                       # tamanho de cada pedaço de áudio lido (segundos)
SILENCIO_PARA_FIM = 0.5               # silêncio (s) que indica fim da frase
DURACAO_MINIMA = 0.5                  # ignora sons mais curtos que isso (s)
DURACAO_MAXIMA = 8.0                  # corta frases muito longas (s) - menor = resposta mais rápida
JANELA_CORTE_SEG = 3.0                # procura a pausa mais natural nos últimos X s antes de cortar
USAR_CONTEXTO = True                  # passa a frase anterior ao Whisper (melhora a continuidade)
MULTIPLICADOR_LIMIAR = 3.0            # microfone: maior = precisa falar mais alto
LIMIAR_PC = 0.006                     # áudio do PC: volume mínimo considerado fala
CONFIANCA_MINIMA = 0.7                # confiança mínima na detecção de idioma
MANTER_IDIOMA_SEG = 30                # se em dúvida, mantém o idioma falado nos últimos X s
PAUSA_GOOGLE_SEG = 60                 # quanto tempo deixar o Google de lado após um bloqueio
AVISO_SEM_SOM_SEG = 15                # avisa se nenhum som chegar nesse tempo após iniciar
# =====================================================

NOMES_IDIOMAS = {
    "en": "Inglês", "es": "Espanhol", "pt": "Português", "fr": "Francês",
    "de": "Alemão", "it": "Italiano", "ja": "Japonês", "zh": "Chinês",
    "ko": "Coreano", "ru": "Russo", "ar": "Árabe", "hi": "Hindi",
    "nl": "Holandês", "tr": "Turco", "pl": "Polonês", "uk": "Ucraniano",
}

# Whisper e Google usam códigos diferentes para alguns idiomas
MAPA_GOOGLE = {"zh": "zh-CN", "he": "iw", "jw": "jw"}

TAMANHO_BLOCO = int(TAXA_AMOSTRAGEM * BLOCO_SEG)

# Filas que ligam as partes do programa (capturar -> separar frases -> traduzir -> falar)
fila_blocos = queue.Queue(maxsize=600)   # pedaços de áudio crus (0,1 s cada)
fila_frases = queue.Queue()              # áudios de frases completas para transcrever
fila_falas = queue.Queue()               # arquivos de voz prontos para tocar

# De onde o áudio está vindo (usado nas mensagens de diagnóstico)
fonte_atual = "pc"
nome_fonte_atual = ""

# Sinaliza quando a tradução está tocando (usado para evitar eco)
tocando_traducao = threading.Event()
bloquear_eco = False

_trava_print = threading.Lock()

# Reaproveita a conexão com os tradutores (evita reconectar a cada frase)
sessao = requests.Session()

# Voz local (Piper) carregada ao iniciar; None = usa a voz da internet (Edge)
voz_piper = None
PASTA_VOZES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vozes")
URL_VOZES = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Momento até quando o Google fica pausado depois de um bloqueio
google_pausado_ate = 0.0

# Tradutor local (NLLB) carregado ao iniciar; None = usa a internet
tradutor_local = None
tokenizador_nllb = None

# Códigos de idioma: Whisper -> NLLB
NLLB_CODIGOS = {
    "en": "eng_Latn", "es": "spa_Latn", "pt": "por_Latn", "fr": "fra_Latn",
    "de": "deu_Latn", "it": "ita_Latn", "ja": "jpn_Jpan", "zh": "zho_Hans",
    "ko": "kor_Hang", "ru": "rus_Cyrl", "ar": "arb_Arab", "hi": "hin_Deva",
    "nl": "nld_Latn", "tr": "tur_Latn", "pl": "pol_Latn", "uk": "ukr_Cyrl",
    "sv": "swe_Latn", "da": "dan_Latn", "no": "nob_Latn", "fi": "fin_Latn",
    "cs": "ces_Latn", "el": "ell_Grek", "he": "heb_Hebr", "id": "ind_Latn",
    "ms": "zsm_Latn", "th": "tha_Thai", "vi": "vie_Latn", "ro": "ron_Latn",
    "hu": "hun_Latn", "bg": "bul_Cyrl", "ca": "cat_Latn", "fa": "pes_Arab",
    "bn": "ben_Beng", "ta": "tam_Taml", "ur": "urd_Arab", "tl": "tgl_Latn",
    "sk": "slk_Latn", "hr": "hrv_Latn", "sr": "srp_Cyrl", "lt": "lit_Latn",
    "lv": "lvs_Latn", "et": "est_Latn", "sl": "slv_Latn", "gl": "glg_Latn",
}

# Frases que o Whisper costuma INVENTAR em música, ruído ou silêncio
FRASES_FANTASMA = [
    "obrigado por assistir", "obrigada por assistir", "legendas pela comunidade",
    "thanks for watching", "thank you for watching", "subtitles by", "amara.org",
    "спасибо за просмотр", "продолжение следует", "субтитры",
    "gracias por ver", "subtítulos realizados", "sous-titres", "untertitel",
    "ご視聴ありがとうございました", "字幕",
]


def eh_alucinacao(texto: str) -> bool:
    t = texto.lower()
    return len(t) < 150 and any(f in t for f in FRASES_FANTASMA)


def log(mensagem: str = ""):
    """Print seguro para várias threads ao mesmo tempo."""
    with _trava_print:
        print(mensagem, flush=True)


def nome_idioma(codigo: str) -> str:
    return NOMES_IDIOMAS.get(codigo, codigo.upper())


def perguntar_numero(texto: str, validos, padrao):
    """Pergunta um número até o usuário digitar um válido (Enter = padrão)."""
    while True:
        resposta = input(texto).strip()
        if resposta == "" and padrao is not None:
            return padrao
        if resposta.isdigit() and int(resposta) in validos:
            return int(resposta)
        print("❌ Número inválido, tente de novo.")


def nomes_parecidos(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or a in b or b in a


# ------------------------- ESCOLHAS INICIAIS -------------------------

def escolher_fonte() -> str:
    if FONTE in ("pc", "microfone"):
        return FONTE
    print("🔊 O que você quer traduzir?")
    print("   [1] Áudio do PC  (reuniões no Zoom/Teams/Meet, vídeos, etc.)")
    print("   [2] Microfone    (som do ambiente)")
    escolha = perguntar_numero("\nDigite 1 ou 2 (Enter = 1): ", [1, 2], 1)
    return "pc" if escolha == 1 else "microfone"


def escolher_microfone() -> int:
    """Lista os microfones (API MME do Windows: sem duplicados e aceita 16 kHz)."""
    if MICROFONE is not None:
        return MICROFONE

    apis = sd.query_hostapis()
    indice_mme = next((i for i, a in enumerate(apis) if a["name"] == "MME"), None)
    microfones = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        if indice_mme is not None and d["hostapi"] != indice_mme:
            continue
        microfones.append((i, d["name"]))

    padrao = sd.default.device[0]
    print("\n🎤 Microfones encontrados:")
    for i, nome in microfones:
        marca = "  ← padrão do Windows" if i == padrao else ""
        print(f"   [{i}] {nome}{marca}")
    return perguntar_numero("\nDigite o número do microfone (Enter = padrão): ",
                            [i for i, _ in microfones], padrao)


def escolher_saida_para_capturar():
    """Escolhe de qual saída de som (fone, caixa...) o áudio do PC será capturado."""
    import soundcard as sc

    saidas = sc.all_speakers()
    padrao = sc.default_speaker()
    indice_padrao = next((i for i, s in enumerate(saidas) if s.id == padrao.id), 0)

    if CAPTURAR_SAIDA is not None:
        return saidas[CAPTURAR_SAIDA]

    print("\n💻 De qual saída de som capturar o áudio?")
    print("   (escolha onde o Zoom/Teams/YouTube está tocando)")
    for i, s in enumerate(saidas):
        marca = "  ← padrão do Windows" if i == indice_padrao else ""
        print(f"   [{i}] {s.name}{marca}")
    escolha = perguntar_numero("\nDigite o número (Enter = padrão): ",
                               range(len(saidas)), indice_padrao)
    return saidas[escolha]


def escolher_saida_traducao():
    """Escolhe em qual saída de som a voz traduzida vai tocar.
    Retorna o nome do dispositivo (ou None = padrão do Windows)."""
    from pygame._sdl2 import audio as sdl2_audio

    pygame.mixer.init()
    nomes = list(sdl2_audio.get_audio_device_names(False))
    pygame.mixer.quit()

    if SAIDA_TRADUCAO is not None and 0 <= SAIDA_TRADUCAO < len(nomes):
        return nomes[SAIDA_TRADUCAO]

    print("\n🎧 Onde você quer OUVIR a tradução?")
    for i, nome in enumerate(nomes):
        print(f"   [{i}] {nome}")
    escolha = perguntar_numero("\nDigite o número (Enter = padrão do Windows): ",
                               range(len(nomes)), -1)
    return None if escolha == -1 else nomes[escolha]


# ------------------------- CAPTURA DE ÁUDIO -------------------------
# A captura roda na thread principal (o loopback do Windows prefere assim)
# e só joga pedaços de 0,1 s na fila. Quem separa as frases é outra thread.

def _enviar_bloco(bloco: np.ndarray):
    if bloquear_eco and tocando_traducao.is_set():
        bloco = np.zeros_like(bloco)   # ignora o que tocar enquanto a tradução fala
    try:
        fila_blocos.put_nowait(bloco)
    except queue.Full:
        pass


def capturar_microfone(indice: int):
    with sd.InputStream(device=indice, samplerate=TAXA_AMOSTRAGEM, channels=1,
                        dtype="float32", blocksize=TAMANHO_BLOCO) as stream:
        while True:
            dados, _ = stream.read(TAMANHO_BLOCO)
            _enviar_bloco(dados[:, 0].copy())


def capturar_audio_pc(saida):
    import soundcard as sc

    warnings.filterwarnings("ignore", message=".*discontinuity.*")
    loopback = sc.get_microphone(id=str(saida.name), include_loopback=True)
    with loopback.recorder(samplerate=TAXA_AMOSTRAGEM, blocksize=TAMANHO_BLOCO) as gravador:
        while True:
            dados = gravador.record(numframes=TAMANHO_BLOCO)
            if dados.ndim > 1:
                dados = dados.mean(axis=1)    # estéreo -> mono
            _enviar_bloco(dados.astype(np.float32))


def calibrar_ruido(indice: int) -> float:
    """Mede o ruído ambiente do microfone para separar silêncio de fala."""
    print("🔇 Calibrando... fique em silêncio por 2 segundos.")
    gravacao = sd.rec(int(2 * TAXA_AMOSTRAGEM), samplerate=TAXA_AMOSTRAGEM,
                      channels=1, dtype="float32", device=indice)
    sd.wait()
    ruido = float(np.sqrt(np.mean(gravacao ** 2)))
    limiar = max(ruido * MULTIPLICADOR_LIMIAR, 0.008)
    print(f"✅ Calibrado (ruído: {ruido:.4f} | limiar de fala: {limiar:.4f})\n")
    return limiar


def separar_frases(limiar: float):
    """Junta os pedaços de áudio em frases completas (fala + silêncio)."""
    pre_fala = deque(maxlen=3)   # guarda um pedacinho antes da fala p/ não cortar o início
    audio = []
    volumes = []                 # volume de cada pedaço (para achar pausas naturais)
    falando = False
    silencio = 0.0

    # Diagnóstico: avisa se nenhum som chegar (provável saída/microfone errado)
    inicio = time.time()
    ultimo_som = None
    avisou_sem_som = False

    while True:
        # Só avisa antes do primeiro som (pausas normais de reunião não disparam o aviso)
        if (ultimo_som is None and not avisou_sem_som
                and time.time() - inicio > AVISO_SEM_SOM_SEG):
            avisou_sem_som = True
            if fonte_atual == "pc":
                log(f"🔇 Nenhum som chegando de '{nome_fonte_atual}' há {AVISO_SEM_SOM_SEG}s.\n"
                    "   Confira se o vídeo/reunião está tocando NESSA saída\n"
                    "   (ícone 🔊 na barra do Windows mostra a saída em uso).\n")
            else:
                log(f"🔇 Nenhum som chegando do microfone '{nome_fonte_atual}' há "
                    f"{AVISO_SEM_SOM_SEG}s. Confira se é o microfone certo.\n")

        try:
            dados = fila_blocos.get(timeout=BLOCO_SEG)
        except queue.Empty:
            # O loopback do Windows não manda nada quando o PC está em silêncio
            dados = np.zeros(TAMANHO_BLOCO, dtype=np.float32)

        volume = float(np.sqrt(np.mean(dados ** 2)))

        if volume > limiar:
            if ultimo_som is None:
                log("🔊 Som detectado! Captura funcionando.\n")
            ultimo_som = time.time()

        frase_terminou = False
        if volume > limiar:
            if not falando:
                falando = True
                for bloco_anterior, volume_anterior in pre_fala:
                    audio.append(bloco_anterior)
                    volumes.append(volume_anterior)
            silencio = 0.0
            audio.append(dados)
            volumes.append(volume)
        elif falando:
            audio.append(dados)
            volumes.append(volume)
            silencio += BLOCO_SEG
            if silencio >= SILENCIO_PARA_FIM:
                frase_terminou = True
        else:
            pre_fala.append((dados, volume))

        # Fala longa sem pausa: corta na pausa mais natural entre palavras
        # (não no meio de uma palavra) e o resto continua na próxima frase.
        if falando and not frase_terminou and len(audio) * BLOCO_SEG >= DURACAO_MAXIMA:
            corte = melhor_ponto_de_corte(volumes)
            _enviar_frase(audio[:corte])
            audio, volumes = audio[corte:], volumes[corte:]
            silencio = 0.0
            continue

        if frase_terminou:
            _enviar_frase(audio)
            audio, volumes = [], []
            falando = False
            silencio = 0.0
            pre_fala.clear()


def melhor_ponto_de_corte(volumes) -> int:
    """Procura o trecho mais silencioso nos últimos segundos (uma micro-pausa
    entre palavras) e devolve onde cortar."""
    janela = int(JANELA_CORTE_SEG / BLOCO_SEG)
    minimo = int(DURACAO_MINIMA / BLOCO_SEG)
    inicio = max(len(volumes) - janela, minimo)
    if inicio >= len(volumes):
        return len(volumes)
    trecho = volumes[inicio:]
    return inicio + int(np.argmin(trecho)) + 1


def _enviar_frase(blocos):
    if len(blocos) * BLOCO_SEG < DURACAO_MINIMA:
        return
    fila_frases.put((np.concatenate(blocos), time.time()))
    if fila_frases.qsize() > 2:
        log(f"⚠️ {fila_frases.qsize()} frases na fila (o processamento está atrasando)")


# ------------------------- WHISPER -------------------------

def tem_gpu_nvidia() -> bool:
    """Verifica se existe uma placa NVIDIA utilizável (e se o uso está ligado)."""
    if not USAR_GPU:
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def nome_modelo_whisper(gpu: bool) -> str:
    if MODELO_WHISPER != "auto":
        return MODELO_WHISPER
    return MODELO_WHISPER_GPU if gpu else MODELO_WHISPER_CPU


def carregar_modelo():
    """Perfil automático: com NVIDIA usa a GPU; sem NVIDIA (ou se falhar),
    usa um modelo mais leve no processador."""
    if tem_gpu_nvidia():
        nome = nome_modelo_whisper(gpu=True)
        try:
            print(f"⏳ Carregando Whisper '{nome}' na GPU NVIDIA...")
            modelo = WhisperModel(nome, device="cuda", compute_type="float16")
            # Teste rápido: força o carregamento das DLLs da NVIDIA agora
            segmentos, _ = modelo.transcribe(np.zeros(TAXA_AMOSTRAGEM, dtype=np.float32))
            list(segmentos)
            return modelo, f"GPU 🚀, modelo '{nome}'"
        except Exception as erro:
            print(f"⚠️ GPU não funcionou ({type(erro).__name__}: {erro})")
            print("   Dica: rode  pip install -r requirements-gpu.txt")
    elif USAR_GPU:
        print("ℹ️ Nenhuma placa NVIDIA encontrada: usando o perfil para processador.")
    else:
        print("ℹ️ GPU desligada (USAR_GPU = False): usando o perfil para processador.")

    nome = nome_modelo_whisper(gpu=False)
    print(f"⏳ Carregando Whisper '{nome}' no processador ({THREADS_CPU} núcleos)...")
    modelo = WhisperModel(nome, device="cpu", compute_type="int8", cpu_threads=THREADS_CPU)
    return modelo, f"CPU, modelo '{nome}'"


def transcrever(modelo: WhisperModel, audio: np.ndarray, contexto=None, idioma=None):
    """Detecta o idioma e transcreve o áudio. O 'contexto' (frase anterior)
    ajuda o Whisper a continuar o raciocínio quando uma fala foi cortada.
    Se 'idioma' for informado, o Whisper não tenta adivinhar o idioma."""
    segmentos, info = modelo.transcribe(audio, beam_size=1, vad_filter=True,
                                        condition_on_previous_text=False,
                                        initial_prompt=contexto, language=idioma)
    texto = " ".join(s.text.strip() for s in segmentos).strip()
    return texto, info.language, info.language_probability


# ------------------------- TRADUÇÃO LOCAL (GPU) -------------------------

def carregar_tradutor_local() -> str:
    """Baixa (1ª vez) e carrega o NLLB-200 na GPU. Se não der, usa a internet."""
    global tradutor_local, tokenizador_nllb
    if MOTOR_TRADUCAO != "local":
        return "internet"
    try:
        import ctranslate2
        import sentencepiece
        from huggingface_hub import snapshot_download

        print("⏳ Preparando o tradutor local (na 1ª vez baixa ~1,2 GB, pode demorar)...")
        pasta = snapshot_download(
            MODELO_TRADUCAO,
            allow_patterns=["model.bin", "config.json", "shared_vocabulary.txt",
                            "sentencepiece.bpe.model"],
        )

        tokenizador_nllb = sentencepiece.SentencePieceProcessor()
        tokenizador_nllb.load(os.path.join(pasta, "sentencepiece.bpe.model"))

        onde = "CPU"
        if tem_gpu_nvidia():
            try:
                tradutor_local = ctranslate2.Translator(pasta, device="cuda",
                                                        compute_type="float16")
                onde = "GPU 🚀"
            except Exception as erro:
                print(f"⚠️ Tradutor na GPU falhou ({type(erro).__name__}), usando o processador.")
        if tradutor_local is None:
            tradutor_local = ctranslate2.Translator(pasta, device="cpu", compute_type="int8",
                                                    intra_threads=THREADS_CPU)

        teste = _traduzir_local("Hello, how are you?", "en")   # aquecimento + teste
        print(f"   Teste: 'Hello, how are you?' → '{teste}'")
        return f"local, {onde}"
    except Exception as erro:
        tradutor_local = None
        print(f"⚠️ Tradutor local não funcionou ({type(erro).__name__}: {erro})")
        print("   Dica: rode  pip install -r requirements.txt")
        print("   Usando a tradução pela internet.")
        return "internet"


def _traduzir_local(texto: str, idioma_origem: str) -> str:
    """Traduz com o NLLB, frase a frase (melhor qualidade em textos longos)."""
    origem = NLLB_CODIGOS.get(idioma_origem)
    destino = NLLB_CODIGOS.get(IDIOMA_DESTINO)
    if not origem or not destino:
        raise KeyError(f"idioma '{idioma_origem}' sem código no tradutor local")

    # Japonês e chinês vêm do Whisper sem pontuação: quebra pelos espaços
    # para o tradutor não "resumir" e perder pedaços
    if idioma_origem in IDIOMAS_SEM_ESPACO:
        frases = dividir_frases_cjk(texto)
    else:
        frases = dividir_frases(texto)
    entradas = [[origem] + tokenizador_nllb.encode(f, out_type=str) + ["</s>"] for f in frases]
    resultados = tradutor_local.translate_batch(
        entradas,
        target_prefix=[[destino]] * len(entradas),
        beam_size=2,
        max_decoding_length=256,
    )
    traducoes = []
    for resultado in resultados:
        tokens = resultado.hypotheses[0]
        if tokens and tokens[0] == destino:
            tokens = tokens[1:]
        traducoes.append(tokenizador_nllb.decode(tokens))
    resultado = " ".join(t.strip() for t in traducoes if t.strip())

    if TRADUZIR_PARA_PT_BR and IDIOMA_DESTINO == "pt":
        resultado = para_pt_br(resultado)
    return resultado


# ------------------------- AJUSTES DE TEXTO -------------------------

IDIOMAS_SEM_ESPACO = ("ja", "zh")


def dividir_frases_cjk(texto: str):
    """Japonês/chinês: cada trecho separado por espaço (ou 。！？) vira uma frase.
    Trechos de 1-2 caracteres são juntados ao anterior."""
    pedacos = [p.strip() for p in re.split(r"[\s　]+|(?<=[。！？!?…])", texto)
               if p and p.strip()]
    frases = []
    for pedaco in pedacos:
        if frases and len(frases[-1]) < 3:
            frases[-1] += pedaco
        else:
            frases.append(pedaco)
    return frases or [texto]


# O tradutor local escreve em português de Portugal; isto aproxima do Brasil.
_PRONOMES_ENCLISE = re.compile(r"\b([A-Za-zÀ-ÿ]+)-(me|te|se|nos|lhe|lhes)\b")
_GERUNDIO_PT = re.compile(
    r"\b(estou|estás|está|estamos|estão|estava|estavas|estávamos|estavam|"
    r"estive|esteve|estiveram|estar|fico|ficas|fica|ficamos|ficam|ficou|"
    r"continuo|continua|continuam|ando|anda|andam) a ([A-Za-zÀ-ÿ]+?)(ar|er|ir)\b",
    re.IGNORECASE)
_ESTA_BEM = re.compile(r"(^|[.!?]\s+)Está bem(?=[\s.,!?]|$)")
_VOCABULARIO_PT_BR = {
    "autocarro": "ônibus", "comboio": "trem", "telemóvel": "celular",
    "pequeno-almoço": "café da manhã",
    # palavras que mudam de gênero levam o artigo junto
    "na casa de banho": "no banheiro", "da casa de banho": "do banheiro",
    "à casa de banho": "ao banheiro", "a casa de banho": "o banheiro",
    "casa de banho": "banheiro",
    "o ecrã": "a tela", "no ecrã": "na tela", "do ecrã": "da tela", "ecrã": "tela",
    "o frigorífico": "a geladeira", "no frigorífico": "na geladeira",
    "do frigorífico": "da geladeira",
    "rapariga": "garota", "raparigas": "garotas", "miúdo": "garoto", "miúdos": "garotos",
    "facto": "fato", "factos": "fatos", "equipa": "equipe", "registo": "registro",
    "utilizador": "usuário", "ficheiro": "arquivo", "frigorífico": "geladeira",
    "telemóveis": "celulares", "utilizadores": "usuários", "ficheiros": "arquivos",
    "sumo": "suco", "contacto": "contato", "óptimo": "ótimo", "acção": "ação",
    "direcção": "direção", "actual": "atual", "actualmente": "atualmente",
    "connosco": "conosco", "camisola": "camiseta", "cá": "aqui",
}
_VOCABULARIO_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in _VOCABULARIO_PT_BR), key=len, reverse=True))
    + r")\b", re.IGNORECASE)


def _trocar_vocabulario(m):
    original = m.group(0)
    novo = _VOCABULARIO_PT_BR[original.lower()]
    return novo[0].upper() + novo[1:] if original[0].isupper() else novo


def _trocar_enclise(m):
    verbo, pronome = m.group(1), m.group(2)
    if verbo[0].isupper():
        return f"{pronome.capitalize()} {verbo[0].lower()}{verbo[1:]}"
    return f"{pronome} {verbo}"


def _trocar_gerundio(m):
    auxiliar, raiz, final = m.group(1), m.group(2), m.group(3)
    gerundio = {"ar": "ando", "er": "endo", "ir": "indo"}[final]
    return f"{auxiliar} {raiz}{gerundio}"


def para_pt_br(texto: str) -> str:
    """'Lembro-me' → 'Me lembro' | 'está a fazer' → 'está fazendo' |
    'Está bem' → 'Tudo bem' | 'telemóvel' → 'celular'..."""
    texto = _ESTA_BEM.sub(lambda m: m.group(1) + "Tudo bem", texto)
    texto = _GERUNDIO_PT.sub(_trocar_gerundio, texto)
    texto = _PRONOMES_ENCLISE.sub(_trocar_enclise, texto)
    texto = _VOCABULARIO_RE.sub(_trocar_vocabulario, texto)
    return texto


# ------------------------- TRADUÇÃO -------------------------

def _traduzir_google_api(texto: str, origem: str) -> str:
    """Endpoint público do Google Tradutor (o mesmo usado por extensões de navegador)."""
    resposta = sessao.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": origem, "tl": IDIOMA_DESTINO, "dt": "t", "q": texto},
        timeout=10,
    )
    resposta.raise_for_status()
    dados = resposta.json()
    return "".join(parte[0] for parte in dados[0] if parte and parte[0])


def _traduzir_deep_translator(texto: str, origem: str) -> str:
    return GoogleTranslator(source=origem, target=IDIOMA_DESTINO).translate(texto)


def _traduzir_mymemory(texto: str, origem: str) -> str:
    """MyMemory: grátis, sem cadastro (limite de ~500 caracteres por pedido)."""
    if origem == "auto":
        origem = "en"
    partes, resultado = [], []
    atual = ""
    for frase in dividir_frases(texto):
        if len(atual) + len(frase) > 450 and atual:
            partes.append(atual)
            atual = ""
        atual += frase + " "
    if atual.strip():
        partes.append(atual)
    for parte in partes:
        resposta = sessao.get(
            "https://api.mymemory.translated.net/get",
            params={"q": parte.strip(), "langpair": f"{origem}|{IDIOMA_DESTINO}"},
            timeout=10,
        )
        resposta.raise_for_status()
        resultado.append(resposta.json()["responseData"]["translatedText"])
    return " ".join(resultado)


def traduzir(texto: str, idioma_origem: str):
    """Usa o tradutor local (GPU) se estiver carregado. Senão (ou se falhar),
    tenta 3 serviços gratuitos na internet. Retorna None se todos falharem.
    Se o Google bloquear, ele fica pausado por um tempo e o reserva assume direto."""
    global google_pausado_ate

    if tradutor_local is not None:
        try:
            traducao = _traduzir_local(texto, idioma_origem)
            if traducao:
                return traducao
        except Exception as erro:
            log(f"   ⚠️ Tradutor local falhou ({type(erro).__name__}: {erro}), usando a internet...")

    origem = MAPA_GOOGLE.get(idioma_origem, idioma_origem)
    tentativas = [
        ("Google API", _traduzir_google_api, True),
        ("Google (deep-translator)", _traduzir_deep_translator, True),
        ("MyMemory", _traduzir_mymemory, False),
    ]
    for nome, funcao, eh_google in tentativas:
        if eh_google and time.time() < google_pausado_ate:
            continue
        try:
            traducao = funcao(texto, origem)
            if traducao and traducao.strip():
                return traducao.strip()
        except Exception as erro:
            log(f"   ⚠️ {nome} falhou ({type(erro).__name__}), tentando outro...")
            if eh_google and time.time() >= google_pausado_ate:
                google_pausado_ate = time.time() + PAUSA_GOOGLE_SEG
                log(f"   ⏸️ Google pausado por {PAUSA_GOOGLE_SEG}s, usando o reserva direto.")
    return None


# ------------------------- VOZ -------------------------

def dividir_frases(texto: str):
    """Quebra o texto em frases, juntando pedaços muito curtos."""
    pedacos = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", texto) if p.strip()]
    frases = []
    for pedaco in pedacos:
        if frases and len(frases[-1]) < 25:
            frases[-1] += " " + pedaco
        else:
            frases.append(pedaco)
    return frases or [texto]


def _baixar_voz_piper() -> str:
    """Baixa a voz do Piper na 1ª vez (cerca de 60 MB) e devolve o caminho do modelo."""
    regiao, falante, qualidade = VOZ_PIPER.split("-")
    idioma = regiao.split("_")[0]
    url_base = f"{URL_VOZES}/{idioma}/{regiao}/{falante}/{qualidade}/{VOZ_PIPER}"
    os.makedirs(PASTA_VOZES, exist_ok=True)

    for extensao in (".onnx", ".onnx.json"):
        destino = os.path.join(PASTA_VOZES, VOZ_PIPER + extensao)
        if os.path.exists(destino) and os.path.getsize(destino) > 0:
            continue
        print(f"⬇️  Baixando a voz {VOZ_PIPER}{extensao} (só na 1ª vez)...")
        temporario = destino + ".parcial"
        with requests.get(url_base + extensao, stream=True, timeout=60) as resposta:
            resposta.raise_for_status()
            with open(temporario, "wb") as arquivo:
                for pedaco in resposta.iter_content(chunk_size=1 << 20):
                    arquivo.write(pedaco)
        os.replace(temporario, destino)

    return os.path.join(PASTA_VOZES, VOZ_PIPER + ".onnx")


def _preparar_espeak() -> str:
    """O espeak (usado pelo Piper para a pronúncia) não aceita acentos no caminho
    no Windows (ex.: pasta 'programação'). Copia os dados dele para uma pasta
    só com letras simples e devolve esse caminho."""
    import piper
    origem = os.path.join(os.path.dirname(piper.__file__), "espeak-ng-data")
    if origem.isascii():
        return origem

    base = os.environ.get("LOCALAPPDATA", "")
    if not base or not base.isascii():
        base = "C:\\tradutor_dados"
    destino = os.path.join(base, "tradutor", "espeak-ng-data")
    if not os.path.exists(os.path.join(destino, "phontab")):
        print("📁 Copiando dados de pronúncia para uma pasta sem acentos (só na 1ª vez)...")
        shutil.copytree(origem, destino, dirs_exist_ok=True)
    return destino


def _testar_piper_separado(modelo: str, pasta_espeak: str) -> bool:
    """Testa o Piper num processo separado. Se o espeak der erro, ele fecha o
    Python inteiro, então o teste fora daqui protege o programa principal."""
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
        resultado = subprocess.run([sys.executable, "-c", codigo, modelo, pasta_espeak],
                                   capture_output=True, text=True, timeout=120, env=ambiente)
    except Exception as erro:
        print(f"⚠️ Teste da voz local falhou ({type(erro).__name__}).")
        return False
    if resultado.returncode == 0 and "ok" in resultado.stdout:
        return True
    detalhe = (resultado.stderr or resultado.stdout).strip().splitlines()
    print(f"⚠️ Teste da voz local falhou: {detalhe[-1] if detalhe else 'sem detalhes'}")
    return False


def carregar_voz() -> str:
    """Prepara a voz local (Piper). Se não der, usa a voz da internet (Edge)."""
    global voz_piper
    if MOTOR_VOZ == "piper":
        try:
            from piper import PiperVoice
            modelo = _baixar_voz_piper()
            pasta_espeak = _preparar_espeak()
            os.environ["ESPEAK_DATA_PATH"] = pasta_espeak
            if not _testar_piper_separado(modelo, pasta_espeak):
                raise RuntimeError("o teste em processo separado não passou")
            voz_piper = PiperVoice.load(modelo, espeak_data_dir=pasta_espeak)
            os.remove(gerar_voz("Pronto.", atrasado=False))   # aquecimento
            return "Piper (local) ⚡"
        except Exception as erro:
            voz_piper = None
            print(f"⚠️ Voz local não funcionou ({type(erro).__name__}: {erro})")
            print("   Dica: rode  pip install -r requirements.txt")
            print("   Usando a voz da internet.")
    return "Edge (internet)"


def gerar_voz(texto: str, atrasado: bool) -> str:
    """Gera um arquivo de áudio com a voz e devolve o caminho."""
    if voz_piper is not None:
        from piper import SynthesisConfig
        fd, caminho = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        config = SynthesisConfig(
            length_scale=VELOCIDADE_PIPER_ATRASADO if atrasado else VELOCIDADE_PIPER)
        with wave.open(caminho, "wb") as arquivo_wav:
            voz_piper.synthesize_wav(texto, arquivo_wav, syn_config=config)
        return caminho

    fd, caminho = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    velocidade = VELOCIDADE_ATRASADO if atrasado else VELOCIDADE_VOZ
    asyncio.run(edge_tts.Communicate(texto, VOZ, rate=velocidade).save(caminho))
    return caminho


# ------------------------- THREADS -------------------------

def trabalhador(modelo: WhisperModel):
    """Pega frases da fila, transcreve, traduz e gera a voz frase a frase."""
    ultimo_texto, ultimo_tempo = None, 0.0
    ultimo_idioma, tempo_idioma = None, 0.0
    while True:
        audio, fim_da_fala = fila_frases.get()
        try:
            # Contexto só vale se a frase anterior foi há pouco tempo
            contexto = (ultimo_texto[-200:]
                        if USAR_CONTEXTO and ultimo_texto and time.time() - ultimo_tempo < 15
                        else None)

            t_inicio = time.time()
            texto, idioma, confianca = transcrever(modelo, audio, contexto)
            if not texto:
                continue

            # Fala curta com dúvida no idioma: mantém o idioma que estava sendo falado
            idioma_mantido = False
            if (confianca < CONFIANCA_MINIMA and ultimo_idioma
                    and time.time() - tempo_idioma < MANTER_IDIOMA_SEG):
                if idioma != ultimo_idioma:
                    texto, _, _ = transcrever(modelo, audio, contexto, idioma=ultimo_idioma)
                idioma, idioma_mantido = ultimo_idioma, True
            t_whisper = time.time()

            if not texto:
                continue

            if eh_alucinacao(texto):
                log(f"👻 Ignorado (o Whisper inventou isso em música/silêncio): {texto}\n")
                continue

            if confianca < CONFIANCA_MINIMA and not idioma_mantido:
                log(f"🤔 Não tenho certeza do idioma ({confianca:.0%}), ignorando: {texto}\n")
                continue

            ultimo_texto, ultimo_tempo = texto, time.time()
            ultimo_idioma, tempo_idioma = idioma, time.time()
            if idioma_mantido:
                log(f"🌐 [{nome_idioma(idioma)} - mantido] {texto}")
            else:
                log(f"🌐 [{nome_idioma(idioma)} - {confianca:.0%}] {texto}")

            if idioma == IDIOMA_DESTINO:
                log("   (já está no seu idioma, nada a traduzir)\n")
                continue

            traducao = traduzir(texto, idioma)
            t_traducao = time.time()
            if not traducao:
                log("   ❌ Nenhum serviço de tradução respondeu. Verifique a internet.\n")
                continue

            log(f"🇧🇷 {traducao}")

            # Se já tem tradução esperando para tocar, fala mais rápido para alcançar
            atrasado = fila_falas.qsize() >= 1 or fila_frases.qsize() >= 1

            # Gera a voz frase a frase: a 1ª já vai tocando enquanto as outras são geradas
            for i, frase in enumerate(dividir_frases(traducao)):
                try:
                    caminho = gerar_voz(frase, atrasado)
                    if i == 0:
                        t_voz = time.time()
                        log(f"   📊 Fila {t_inicio - fim_da_fala:.1f}s | "
                            f"Whisper {t_whisper - t_inicio:.1f}s | "
                            f"Tradução {t_traducao - t_whisper:.1f}s | "
                            f"Voz {t_voz - t_traducao:.1f}s"
                            + (" | ⏩ acelerando" if atrasado else ""))
                    fila_falas.put((caminho, fim_da_fala if i == 0 else None, time.time()))
                except Exception as erro:
                    log(f"   ⚠️ Não consegui gerar a voz ({type(erro).__name__}).")
        except Exception as erro:
            log(f"❌ Erro: {type(erro).__name__}: {erro}\n   Continuando...\n")


def falante():
    """Toca as vozes na ordem em que ficam prontas."""
    while True:
        caminho, fim_da_fala, pronto_em = fila_falas.get()
        try:
            if fim_da_fala is not None:
                espera = time.time() - pronto_em
                aviso = (f" (esperou {espera:.1f}s a tradução anterior terminar)"
                         if espera > 0.3 else "")
                log(f"   ⏱️ {time.time() - fim_da_fala:.1f}s até ouvir a tradução{aviso}\n")
            tocando_traducao.set()
            pygame.mixer.music.load(caminho)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.03)
            pygame.mixer.music.unload()
        except Exception as erro:
            log(f"   ⚠️ Erro ao tocar a voz ({type(erro).__name__}).")
        finally:
            time.sleep(0.15)   # margem para o fim do som não ser capturado
            tocando_traducao.clear()
            try:
                os.remove(caminho)
            except OSError:
                pass


# ------------------------- PRINCIPAL -------------------------

def main():
    global bloquear_eco, fonte_atual, nome_fonte_atual

    print("=" * 55)
    print("   🌎 TRADUTOR DE VOZ EM TEMPO REAL - Protótipo v7")
    print("=" * 55)

    fonte = escolher_fonte()

    if fonte == "pc":
        import soundcard as sc
        saida_capturada = escolher_saida_para_capturar()
        saida_traducao = escolher_saida_traducao()
        nome_saida_real = saida_traducao or sc.default_speaker().name
        bloquear_eco = nomes_parecidos(saida_capturada.name, nome_saida_real)
        fonte_atual, nome_fonte_atual = "pc", saida_capturada.name

        print(f"\n💻 Capturando:  {saida_capturada.name}")
        print(f"🎧 Tradução em: {nome_saida_real}")
        if bloquear_eco:
            print("⚠️  Captura e tradução estão no MESMO dispositivo.")
            print("   Para não traduzir a própria voz, o áudio é ignorado enquanto a")
            print("   tradução toca (pode perder pedaços). Melhor: mande o som da")
            print("   reunião para outra saída e ouça a tradução no fone.")
    else:
        indice_microfone = escolher_microfone()
        fonte_atual = "microfone"
        nome_fonte_atual = sd.query_devices(indice_microfone)["name"]
        saida_traducao = escolher_saida_traducao()
        print(f"\n🎤 Microfone:   {sd.query_devices(indice_microfone)['name']}")
        print(f"🎧 Tradução em: {saida_traducao or 'padrão do Windows'}")

    print(f"🗣️  Traduzindo tudo para: {nome_idioma(IDIOMA_DESTINO)}\n")

    modelo, onde = carregar_modelo()
    print(f"✅ Modelo carregado ({onde}).")

    motor_traducao = carregar_tradutor_local()
    print(f"✅ Tradução: {motor_traducao}")

    motor = carregar_voz()
    print(f"✅ Voz: {motor}\n")

    if saida_traducao:
        pygame.mixer.init(devicename=saida_traducao)
    else:
        pygame.mixer.init()

    limiar = LIMIAR_PC if fonte == "pc" else calibrar_ruido(indice_microfone)

    threading.Thread(target=separar_frases, args=(limiar,), daemon=True).start()
    threading.Thread(target=trabalhador, args=(modelo,), daemon=True).start()
    threading.Thread(target=falante, daemon=True).start()

    if fonte == "pc":
        print("👂 Pronto! Toque a reunião ou um vídeo no PC. Ctrl+C para sair.\n")
        capturar_audio_pc(saida_capturada)
    else:
        print("👂 Pronto! Fale perto do microfone. Ctrl+C para sair.\n")
        capturar_microfone(indice_microfone)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Encerrado.")
