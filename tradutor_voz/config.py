"""Todas as configurações do tradutor num só lugar.

Para mudar um padrão, edite o valor aqui (ou crie um Config(...) com os
valores que quiser e passe para o Pipeline).
"""

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # ---------------- Idioma ----------------
    idioma_destino: str = "pt"                 # idioma que você quer ouvir

    # ---------------- Tradução ----------------
    motor_traducao: str = "local"              # "local" (sem internet) | "internet"
    modelo_traducao: str = "JustFrederik/nllb-200-distilled-600M-ct2-float16"
    traduzir_para_pt_br: bool = True           # troca construções de Portugal por brasileiras
    pausa_google_seg: float = 60               # deixa o Google de lado após um bloqueio

    # ---------------- Voz ----------------
    falar_traducao: bool = True                # False = só legendas, sem voz
    motor_voz: str = "piper"                   # "piper" (local, rápido) | "edge" (internet)
    voz_piper: str = "pt_BR-faber-medium"      # outras: "pt_BR-cadu-medium", "pt_BR-jeff-medium"
    velocidade_piper: float = 0.87             # menor = mais rápido (1.0 = normal)
    velocidade_piper_atrasado: float = 0.70    # quando há traduções acumuladas
    voz_edge: str = "pt-BR-FranciscaNeural"
    velocidade_edge: str = "+15%"
    velocidade_edge_atrasado: str = "+45%"

    # ---------------- Reconhecimento de fala (Whisper) ----------------
    usar_gpu: bool = True                      # False = simula um PC sem NVIDIA
    modelo_whisper: str = "auto"               # "auto" usa as duas linhas abaixo
    modelo_whisper_gpu: str = "small"          # "large-v3-turbo" = melhor qualidade
    modelo_whisper_cpu: str = "base"           # leve, para rodar no processador
    threads_cpu: int = field(default_factory=lambda: min(8, os.cpu_count() or 4))
    usar_contexto: bool = True                 # passa a frase anterior ao Whisper
    confianca_minima: float = 0.7              # confiança mínima na detecção de idioma
    manter_idioma_seg: float = 30              # em dúvida, mantém o idioma dos últimos X s
    juntar_frases: bool = True                 # junta trechos cortados no meio da frase
    espera_fim_frase: float = 1.2              # quanto esperar pela continuação (s)
    max_caracteres_juntar: int = 320           # acima disso, traduz mesmo sem o final

    # ---------------- Áudio e separação de frases ----------------
    taxa_amostragem: int = 16000               # o Whisper trabalha com 16 kHz
    bloco_seg: float = 0.1                     # tamanho de cada pedaço lido
    silencio_para_fim: float = 0.5             # silêncio que indica fim da frase
    duracao_minima: float = 0.5                # ignora sons mais curtos que isso
    fala_minima: float = 0.3                   # mínimo de fala real (ignora cliques/estalos)
    duracao_maxima: float = 8.0                # falas longas são cortadas nesse tempo
    janela_corte_seg: float = 3.0              # procura a pausa mais natural nos últimos X s
    multiplicador_limiar: float = 3.0          # microfone: maior = precisa falar mais alto
    limiar_pc: float = 0.006                   # áudio do PC: volume mínimo considerado fala
    aviso_sem_som_seg: float = 15              # avisa se nenhum som chegar nesse tempo

    @property
    def tamanho_bloco(self) -> int:
        return int(self.taxa_amostragem * self.bloco_seg)
