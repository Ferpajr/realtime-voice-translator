"""Nomes e códigos de idioma usados pelo Whisper, pelo NLLB e pelo Google."""

NOMES_IDIOMAS = {
    "en": "Inglês", "es": "Espanhol", "pt": "Português", "fr": "Francês",
    "de": "Alemão", "it": "Italiano", "ja": "Japonês", "zh": "Chinês",
    "ko": "Coreano", "ru": "Russo", "ar": "Árabe", "hi": "Hindi",
    "nl": "Holandês", "tr": "Turco", "pl": "Polonês", "uk": "Ucraniano",
    "sv": "Sueco", "da": "Dinamarquês", "no": "Norueguês", "fi": "Finlandês",
    "cs": "Tcheco", "el": "Grego", "he": "Hebraico", "id": "Indonésio",
    "th": "Tailandês", "vi": "Vietnamita", "ro": "Romeno", "hu": "Húngaro",
}

# Whisper -> NLLB
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

# Whisper -> Google (só os que mudam)
MAPA_GOOGLE = {"zh": "zh-CN", "he": "iw", "jw": "jw"}


def nome_idioma(codigo: str) -> str:
    return NOMES_IDIOMAS.get(codigo, codigo.upper())
