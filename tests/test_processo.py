"""Testa o motor rodando em outro processo: comunicação por filas e o que
acontece quando ele falha ou cai (a interface precisa continuar viva)."""

import time

from tradutor_voz.config import Config
from tradutor_voz.processo import MotorEmProcesso


def esperar_evento(motor, tipo, limite=60):
    """Coleta eventos até aparecer o tipo pedido (ou estourar o tempo)."""
    vistos = []
    fim = time.time() + limite
    while time.time() < fim:
        for evento in motor.coletar_eventos():
            vistos.append(evento)
            if evento[0] == tipo:
                return evento, vistos
        time.sleep(0.05)
    raise AssertionError(f"evento '{tipo}' não chegou; vistos: {[t for t, _ in vistos]}")


def test_motor_roda_em_outro_processo_e_encerra_limpo():
    # Força uma falha conhecida no carregamento: o evento de erro tem que chegar
    # pela fila, e o processo continua vivo esperando comandos.
    motor = MotorEmProcesso(Config(modelo_whisper="modelo-que-nao-existe", usar_gpu=False,
                                   motor_traducao="internet", motor_voz="edge"))
    motor.carregar()
    try:
        (tipo, dados), _ = esperar_evento(motor, "erro")
        assert "carregar os modelos" in dados["mensagem"]
        assert motor.vivo
    finally:
        motor.encerrar()
    assert not motor.vivo


def test_motor_que_cai_vira_evento_e_nao_derruba_a_interface():
    motor = MotorEmProcesso(Config(modelo_whisper="modelo-que-nao-existe", usar_gpu=False,
                                   motor_traducao="internet", motor_voz="edge"))
    motor.carregar()
    esperar_evento(motor, "erro")
    motor._processo.kill()               # simula um travamento do motor
    motor._processo.wait(timeout=10)

    (tipo, dados), _ = esperar_evento(motor, "motor_caiu", limite=10)
    assert "fechou inesperadamente" in dados["mensagem"]
    assert not motor.vivo
    assert motor.coletar_eventos() == []  # avisa uma vez só
