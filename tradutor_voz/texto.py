"""Funções puras de texto: dividir frases, filtrar alucinações, ajustar para PT-BR.

Não dependem de áudio nem de modelos, por isso são fáceis de testar.
"""

import re

# ---------------- Alucinações do Whisper ----------------

# Frases que o Whisper costuma INVENTAR em música, ruído ou silêncio
FRASES_FANTASMA = [
    "obrigado por assistir", "obrigada por assistir", "legendas pela comunidade",
    "thanks for watching", "thank you for watching", "subtitles by", "amara.org",
    "спасибо за просмотр", "продолжение следует", "субтитры",
    "gracias por ver", "subtítulos realizados", "sous-titres", "untertitel",
    "ご視聴ありがとうございました", "字幕",
]


def eh_alucinacao(texto: str) -> bool:
    """True se o texto parece uma frase inventada pelo Whisper."""
    t = texto.lower()
    return len(t) < 150 and any(f in t for f in FRASES_FANTASMA)


# ---------------- Divisão em frases ----------------

IDIOMAS_SEM_ESPACO = ("ja", "zh")

_FIM_DE_FRASE = re.compile(r"[.!?。！？][\"'”’»)\]]*$")


def termina_frase(texto: str) -> bool:
    """True se o texto termina com pontuação de fim de frase.
    Reticências ('...', '…') indicam fala interrompida: não contam como fim."""
    t = texto.strip()
    if t.endswith(("...", "…")):
        return False
    return bool(_FIM_DE_FRASE.search(t))


def dividir_frases(texto: str):
    """Quebra o texto em frases pela pontuação, juntando pedaços muito curtos."""
    pedacos = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", texto) if p.strip()]
    frases = []
    for pedaco in pedacos:
        if frases and len(frases[-1]) < 25:
            frases[-1] += " " + pedaco
        else:
            frases.append(pedaco)
    return frases or [texto]


def dividir_frases_cjk(texto: str):
    """Japonês/chinês vêm do Whisper sem pontuação: cada trecho separado por
    espaço (ou 。！？) vira uma frase. Trechos de 1-2 caracteres são juntados."""
    pedacos = [p.strip() for p in re.split(r"[\s　]+|(?<=[。！？!?…])", texto)
               if p and p.strip()]
    frases = []
    for pedaco in pedacos:
        if frases and len(frases[-1]) < 3:
            frases[-1] += pedaco
        else:
            frases.append(pedaco)
    return frases or [texto]


def dividir_para_traducao(texto: str, idioma: str):
    """Escolhe a forma de dividir conforme o idioma de origem."""
    if idioma in IDIOMAS_SEM_ESPACO:
        return dividir_frases_cjk(texto)
    return dividir_frases(texto)


# ---------------- Português de Portugal -> Brasil ----------------

_PRONOMES_ENCLISE = re.compile(r"\b([A-Za-zÀ-ÿ]+)-(me|te|se|nos|lhe|lhes)\b")
_GERUNDIO_PT = re.compile(
    r"\b(estou|estás|está|estamos|estão|estava|estavas|estávamos|estavam|"
    r"estive|esteve|estiveram|estar|fico|ficas|fica|ficamos|ficam|ficou|"
    r"continuo|continua|continuam|ando|anda|andam) a ([A-Za-zÀ-ÿ]+?)(ar|er|ir)\b",
    re.IGNORECASE)
_ESTA_BEM = re.compile(r"(^|[.!?]\s+)Está bem(?=[\s.,!?]|$)")
_VOCABULARIO_PT_BR = {
    "autocarro": "ônibus", "comboio": "trem", "telemóvel": "celular",
    "telemóveis": "celulares", "pequeno-almoço": "café da manhã",
    # palavras que mudam de gênero levam o artigo junto
    "na casa de banho": "no banheiro", "da casa de banho": "do banheiro",
    "à casa de banho": "ao banheiro", "a casa de banho": "o banheiro",
    "casa de banho": "banheiro",
    "o ecrã": "a tela", "no ecrã": "na tela", "do ecrã": "da tela", "ecrã": "tela",
    "o frigorífico": "a geladeira", "no frigorífico": "na geladeira",
    "do frigorífico": "da geladeira", "frigorífico": "geladeira",
    "rapariga": "garota", "raparigas": "garotas", "miúdo": "garoto", "miúdos": "garotos",
    "facto": "fato", "factos": "fatos", "equipa": "equipe", "registo": "registro",
    "utilizador": "usuário", "utilizadores": "usuários",
    "ficheiro": "arquivo", "ficheiros": "arquivos",
    "sumo": "suco", "contacto": "contato", "óptimo": "ótimo", "acção": "ação",
    "direcção": "direção", "actual": "atual", "actualmente": "atualmente",
    "connosco": "conosco", "camisola": "camiseta", "cá": "aqui",
    "pai natal": "Papai Noel", "póni": "pônei", "póneis": "pôneis", "betão": "concreto",
    "fixe": "legal",
    # "ter de" -> "ter que"
    "tenho de": "tenho que", "tens de": "tem que", "tem de": "tem que",
    "temos de": "temos que", "têm de": "têm que",
    # tratamento por "tu" (Portugal) -> "você" (Brasil)
    "tu": "você", "ti": "você", "contigo": "com você",
    "teu": "seu", "tua": "sua", "teus": "seus", "tuas": "suas",
    "estás": "está", "és": "é", "tens": "tem", "queres": "quer", "podes": "pode",
    "sabes": "sabe", "vais": "vai", "dizes": "diz", "fazes": "faz", "vês": "vê",
    "ficas": "fica", "precisas": "precisa", "gostas": "gosta", "pensas": "pensa",
    "sentes": "sente", "conheces": "conhece", "lembras": "lembra", "entendes": "entende",
    "percebes": "entende", "consegues": "consegue", "deves": "deve", "vens": "vem",
    "estavas": "estava", "podias": "podia", "querias": "queria", "foste": "foi",
    "fizeste": "fez", "disseste": "disse", "viste": "viu", "tiveste": "teve",
    "sejas": "seja", "estejas": "esteja", "tenhas": "tenha",
}
_VOCABULARIO_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in _VOCABULARIO_PT_BR),
                             key=len, reverse=True)) + r")\b",
    re.IGNORECASE)


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
    gerundio = {"ar": "ando", "er": "endo", "ir": "indo"}[final.lower()]
    return f"{auxiliar} {raiz}{gerundio}"


def para_pt_br(texto: str) -> str:
    """Aproxima o português de Portugal do brasileiro.

    'Lembro-me' -> 'Me lembro' | 'está a fazer' -> 'está fazendo' |
    'Está bem' -> 'Tudo bem' | 'telemóvel' -> 'celular' |
    'tu dizes' -> 'você diz' | 'tens de' -> 'tem que' | 'à tua frente' -> 'à sua frente'
    """
    texto = _ESTA_BEM.sub(lambda m: m.group(1) + "Tudo bem", texto)
    texto = _GERUNDIO_PT.sub(_trocar_gerundio, texto)
    texto = _PRONOMES_ENCLISE.sub(_trocar_enclise, texto)
    texto = _VOCABULARIO_RE.sub(_trocar_vocabulario, texto)
    return texto


def nomes_parecidos(a: str, b: str) -> bool:
    """Compara nomes de dispositivos de bibliotecas diferentes (que às vezes
    cortam ou formatam o nome de outro jeito)."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    return bool(a and b) and (a == b or a in b or b in a)
