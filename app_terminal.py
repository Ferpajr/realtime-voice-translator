"""Tradutor de voz em tempo real: versão de terminal.

Uso:
    python app_terminal.py                      (pergunta tudo)
    python app_terminal.py --fonte pc --captura 1 --saida 0
    python app_terminal.py --sem-voz            (só texto)
    python app_terminal.py --sem-gpu            (simula um PC sem NVIDIA)
"""

import argparse
import sys
import threading

from tradutor_voz import __version__
from tradutor_voz.config import Config
from tradutor_voz.idiomas import nome_idioma
from tradutor_voz.pipeline import Pipeline

try:
    sys.stdout.reconfigure(encoding="utf-8")   # acentos/emojis no console do Windows
except Exception:
    pass

_trava = threading.Lock()


def log(mensagem: str = ""):
    with _trava:
        print(mensagem, flush=True)


def perguntar_numero(texto, validos, padrao):
    while True:
        resposta = input(texto).strip()
        if resposta == "" and padrao is not None:
            return padrao
        if resposta.isdigit() and int(resposta) in validos:
            return int(resposta)
        print("❌ Número inválido, tente de novo.")


def mostrar_evento(tipo: str, d: dict):
    if tipo in ("status",):
        log(f"⏳ {d['mensagem']}")
    elif tipo == "aviso":
        log(f"   ⚠️ {d['mensagem'].lstrip('⚠️ ')}")
    elif tipo == "erro":
        log(f"❌ {d['mensagem']}")
    elif tipo == "carregado":
        log(f"✅ Whisper: {d['whisper']} | Tradução: {d['traducao']} | Voz: {d['voz']}\n")
    elif tipo == "iniciado":
        if d["eco"]:
            log("⚠️  Captura e tradução no MESMO dispositivo: o áudio é ignorado enquanto a\n"
                "   tradução toca. Para não perder nada, mande a reunião para outra saída.")
        log("👂 Pronto! Ctrl+C para sair.\n")
    elif tipo == "som":
        log("🔊 Som detectado! Captura funcionando.\n")
    elif tipo == "sem_som":
        log(f"🔇 {d['mensagem']}\n")
    elif tipo == "original":
        rotulo = "mantido" if d["mantido"] else f"{d['confianca']:.0%}"
        if d.get("partes", 1) > 1:
            rotulo += f" - {d['partes']} trechos juntados"
        log(f"🌐 [{nome_idioma(d['idioma'])} - {rotulo}] {d['texto']}")
    elif tipo == "ignorado":
        motivos = {"alucinacao": "👻 Ignorado (inventado pelo Whisper)",
                   "confianca": "🤔 Ignorado (idioma incerto)",
                   "mesmo_idioma": "   (já está no seu idioma)"}
        if d["motivo"] == "mesmo_idioma":
            log(f"{motivos['mesmo_idioma']}\n")
        else:
            log(f"{motivos[d['motivo']]}: {d['texto']}\n")
    elif tipo == "traducao":
        log(f"🇧🇷 {d['texto']}")
    elif tipo == "metricas":
        log(f"   📊 Fila {d['fila']:.1f}s | Whisper {d['whisper']:.1f}s | "
            f"Tradução {d['traducao']:.1f}s | Voz {d['voz']:.1f}s"
            + (" | ⏩ acelerando" if d["atrasado"] else ""))
    elif tipo == "latencia":
        aviso = (f" (esperou {d['espera']:.1f}s a anterior terminar)"
                 if d["espera"] > 0.3 else "")
        log(f"   ⏱️ {d['total']:.1f}s até ouvir a tradução{aviso}\n")


def main():
    parser = argparse.ArgumentParser(description="Tradutor de voz em tempo real")
    parser.add_argument("--fonte", choices=["pc", "microfone"])
    parser.add_argument("--captura", type=int, help="número da saída (pc) ou do microfone")
    parser.add_argument("--saida", type=int, help="número da saída onde a tradução toca")
    parser.add_argument("--sem-voz", action="store_true", help="só mostra o texto")
    parser.add_argument("--sem-gpu", action="store_true", help="simula um PC sem NVIDIA")
    args = parser.parse_args()

    from tradutor_voz import captura as cap

    cfg = Config(falar_traducao=not args.sem_voz, usar_gpu=not args.sem_gpu)

    print("=" * 55)
    print(f"   🌎 TRADUTOR DE VOZ EM TEMPO REAL - v{__version__}")
    print("=" * 55)

    # Fonte
    fonte = args.fonte
    if fonte is None:
        print("🔊 O que você quer traduzir?")
        print("   [1] Áudio do PC  (reuniões no Zoom/Teams/Meet, vídeos, etc.)")
        print("   [2] Microfone    (som do ambiente)")
        fonte = "pc" if perguntar_numero("\nDigite 1 ou 2 (Enter = 1): ", [1, 2], 1) == 1 \
            else "microfone"

    # Dispositivo de captura
    if fonte == "pc":
        saidas, padrao = cap.listar_saidas_captura()
        escolha = args.captura
        if escolha is None:
            print("\n💻 De qual saída de som capturar o áudio?")
            for i, nome in saidas:
                print(f"   [{i}] {nome}" + ("  ← padrão do Windows" if i == padrao else ""))
            escolha = perguntar_numero("\nDigite o número (Enter = padrão): ",
                                       [i for i, _ in saidas], padrao)
        dispositivo = dict(saidas)[escolha]
        descricao = dispositivo
    else:
        microfones, padrao = cap.listar_microfones()
        escolha = args.captura
        if escolha is None:
            print("\n🎤 Microfones encontrados:")
            for i, nome in microfones:
                print(f"   [{i}] {nome}" + ("  ← padrão do Windows" if i == padrao else ""))
            escolha = perguntar_numero("\nDigite o número (Enter = padrão): ",
                                       [i for i, _ in microfones], padrao)
        dispositivo = escolha
        descricao = cap.nome_microfone(escolha)

    # Saída da tradução
    saida_traducao = None
    if cfg.falar_traducao:
        nomes = cap.listar_saidas_reproducao()
        escolha = args.saida
        if escolha is None:
            print("\n🎧 Onde você quer OUVIR a tradução?")
            for i, nome in enumerate(nomes):
                print(f"   [{i}] {nome}")
            escolha = perguntar_numero("\nDigite o número (Enter = padrão do Windows): ",
                                       range(len(nomes)), -1)
        saida_traducao = None if escolha == -1 else nomes[escolha]

    print(f"\n{'💻' if fonte == 'pc' else '🎤'} Capturando:  {descricao}")
    print(f"🎧 Tradução em: {saida_traducao or 'padrão do Windows'}")
    print(f"🗣️  Traduzindo para: {nome_idioma(cfg.idioma_destino)}\n")

    pipeline = Pipeline(cfg, ao_evento=mostrar_evento)
    pipeline.carregar()
    pipeline.iniciar(fonte, dispositivo, saida_traducao)

    parado = threading.Event()
    try:
        while pipeline.rodando:
            parado.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.parar()
        print("\n👋 Encerrado.")


if __name__ == "__main__":
    main()
