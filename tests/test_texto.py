import pytest

from tradutor_voz.texto import (dividir_frases, dividir_frases_cjk, dividir_para_traducao,
                                eh_alucinacao, nomes_parecidos, para_pt_br)


# ---------------- Português de Portugal -> Brasil ----------------

@pytest.mark.parametrize("entrada, esperado", [
    ("Eu lembro-me bem dos nomes.", "Eu me lembro bem dos nomes."),
    ("Diga-me onde está.", "Me diga onde está."),
    ("Chama-se Maria.", "Se chama Maria."),
    ("Estou a fazer o jantar.", "Estou fazendo o jantar."),
    ("Ela está a sair do autocarro.", "Ela está saindo do ônibus."),
    ("Eles continuam a correr.", "Eles continuam correndo."),
    ("Está bem, vamos.", "Tudo bem, vamos."),
    ("O meu telemóvel está no ecrã.", "O meu celular está na tela."),
    ("O guarda-chuva está na casa de banho.", "O guarda-chuva está no banheiro."),
    ("Vem cá com a tua equipa.", "Vem aqui com a sua equipe."),
    # casos reais tirados de uma sessão de teste (série em inglês -> NLLB)
    ("Estou à tua frente.", "Estou à sua frente."),
    ("Desculpa, tu dizes o melhor para mim?", "Desculpa, você diz o melhor para mim?"),
    ("Só tens de perguntar formalmente.", "Só tem que perguntar formalmente."),
    ("Olá, como estás?", "Olá, como está?"),
    ("Diante de ti, estás...", "Diante de você, está..."),
    ("Pede ao Pai Natal um póni.", "Pede ao Papai Noel um pônei."),
    ("Primeiro vamos derramar algum betão.", "Primeiro vamos derramar algum concreto."),
    ("Tu és o meu melhor amigo.", "Você é o meu melhor amigo."),
])
def test_para_pt_br_converte_construcoes_de_portugal(entrada, esperado):
    assert para_pt_br(entrada) == esperado


@pytest.mark.parametrize("frase", [
    "Ele está bem.",                         # "está bem" no meio da frase não muda
    "O guarda-chuva é azul.",                # hífen sem pronome não muda
    "Eu me lembro de tudo.",                 # já está em português do Brasil
])
def test_para_pt_br_nao_altera_o_que_ja_esta_certo(frase):
    assert para_pt_br(frase) == frase


def test_para_pt_br_preserva_maiuscula_no_inicio():
    assert para_pt_br("Telemóvel novo.") == "Celular novo."


# ---------------- Alucinações ----------------

@pytest.mark.parametrize("texto", [
    "Obrigado por assistir!",
    "Thanks for watching!",
    "в области. Спасибо за просмотр! До скорых встреч!",
    "ご視聴ありがとうございました",
])
def test_detecta_alucinacoes_conhecidas(texto):
    assert eh_alucinacao(texto)


def test_nao_confunde_fala_normal_com_alucinacao():
    assert not eh_alucinacao("If this couple can lose 150 pounds, they win $250,000!")


def test_texto_longo_com_frase_fantasma_nao_e_descartado():
    longo = "A palestra de hoje foi sobre redes neurais e aprendizado profundo. " * 3
    assert not eh_alucinacao(longo + "Obrigado por assistir.")


# ---------------- Divisão em frases ----------------

def test_dividir_frases_por_pontuacao():
    texto = "Nos veremos pela manhã. Obviamente, não vamos deixar vocês esperando. Trouxemos um treinador."
    assert dividir_frases(texto) == [
        "Nos veremos pela manhã. Obviamente, não vamos deixar vocês esperando.",
        "Trouxemos um treinador.",
    ]


def test_dividir_frases_junta_pedacos_curtos():
    assert dividir_frases("Oi. Tudo bem? Sim.") == ["Oi. Tudo bem? Sim."]


def test_dividir_frases_cjk_separa_por_espacos():
    # Antes, "うん ごめんね" sumia na tradução porque tudo virava uma frase só
    assert dividir_frases_cjk("いいよ 誰にでも間違いはあるから うん ごめんね") == [
        "いいよ", "誰にでも間違いはあるから", "うんごめんね"]


def test_dividir_frases_cjk_separa_por_pontuacao_japonesa():
    assert dividir_frases_cjk("こんにちは。元気ですか？はい") == ["こんにちは。", "元気ですか？", "はい"]


def test_dividir_para_traducao_escolhe_pelo_idioma():
    assert dividir_para_traducao("はい いいえ", "ja") == ["はいいいえ"]   # trechos curtos juntos
    assert dividir_para_traducao("Yes. No.", "en") == ["Yes. No."]


# ---------------- Nomes de dispositivos ----------------

def test_nomes_parecidos_entre_bibliotecas():
    assert nomes_parecidos("Headphones (High Definition Audio Device)",
                           "Headphones (High Definition Aud")
    assert not nomes_parecidos("Headphones (High Definition Audio Device)",
                               "2460G4 (NVIDIA High Definition Audio)")
    assert not nomes_parecidos("", "Headphones")
