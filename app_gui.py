"""Tradutor de voz em tempo real: interface gráfica com legendas flutuantes.

Uso: python app_gui.py
"""

import html
import sys
import threading

from PySide6.QtCore import QObject, QPoint, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from tradutor_voz import __version__
from tradutor_voz.config import Config
from tradutor_voz.idiomas import nome_idioma
from tradutor_voz.texto import nomes_parecidos

# ---------------- Cores e fontes ----------------

FUNDO = "#F2F4F7"
SUPERFICIE = "#FFFFFF"
BORDA = "#DCE1E8"
TINTA = "#17212B"
SECUNDARIO = "#5D6A7A"
CANAL = "#0E7C86"          # azul-petróleo: o "canal" de tradução
CANAL_ESCURO = "#0A5F67"
AO_VIVO = "#D64545"
ALERTA_FUNDO = "#FFF4DB"
ALERTA_TEXTO = "#7A5200"

FONTE_UI = "Segoe UI Variable Text"
FONTE_TITULO = "Segoe UI Variable Display"

ESTILO = f"""
QMainWindow, QWidget#raiz {{ background: {FUNDO}; }}
QLabel {{ color: {TINTA}; font-family: "{FONTE_UI}", "Segoe UI", sans-serif; font-size: 13px; }}
QLabel#titulo {{ font-family: "{FONTE_TITULO}", "Segoe UI", sans-serif;
                 font-size: 20px; font-weight: 600; }}
QLabel#rotulo {{ color: {SECUNDARIO}; }}
QLabel#rodape {{ color: {SECUNDARIO}; font-size: 12px; }}
QLabel#alerta {{ background: {ALERTA_FUNDO}; color: {ALERTA_TEXTO}; border-radius: 6px;
                 padding: 8px 10px; }}
QFrame#painel {{ background: {SUPERFICIE}; border: 1px solid {BORDA}; border-radius: 10px; }}
QComboBox {{ background: {SUPERFICIE}; border: 1px solid {BORDA}; border-radius: 6px;
             padding: 5px 8px; color: {TINTA}; font-size: 13px; min-height: 22px; }}
QComboBox:focus {{ border: 2px solid {CANAL}; }}
QComboBox QAbstractItemView {{ background: {SUPERFICIE}; color: {TINTA};
                               selection-background-color: #DDEFF0; selection-color: {TINTA}; }}
QCheckBox {{ color: {TINTA}; font-size: 13px; spacing: 8px; }}
QCheckBox:focus {{ color: {CANAL_ESCURO}; }}
QToolButton {{ background: transparent; border: 1px solid {BORDA}; border-radius: 6px;
               padding: 4px 8px; color: {SECUNDARIO}; }}
QToolButton:hover {{ background: #E8ECF1; }}
QToolButton:focus {{ border: 2px solid {CANAL}; }}
QPushButton#principal {{ background: {CANAL}; color: white; border: none; border-radius: 8px;
                         font-size: 15px; font-weight: 600; padding: 12px; }}
QPushButton#principal:hover {{ background: {CANAL_ESCURO}; }}
QPushButton#principal:focus {{ outline: none; border: 3px solid #8FD3D8; }}
QPushButton#principal:disabled {{ background: #A9C9CC; }}
QPushButton#principal[aoVivo="true"] {{ background: {SUPERFICIE}; color: {AO_VIVO};
                                       border: 2px solid {AO_VIVO}; }}
QPushButton#principal[aoVivo="true"]:hover {{ background: #FCECEC; }}
QTextBrowser {{ background: {SUPERFICIE}; border: 1px solid {BORDA}; border-radius: 10px;
                padding: 6px; color: {TINTA}; font-size: 14px; }}
"""


# ---------------- Ponte entre as threads do pipeline e a interface ----------------

class Ponte(QObject):
    """O pipeline avisa de outras threads; o sinal entrega na thread da interface."""
    evento = Signal(str, dict)


# ---------------- Medidor de sinal ----------------

class MedidorSinal(QWidget):
    """Barra de volume ao vivo, com a marca do limiar de fala."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.valor = 0.0
        self.limiar = 0.006
        self.setToolTip("Volume do áudio capturado. A marca indica a partir de onde conta como fala.")

    @staticmethod
    def _escala(v: float) -> float:
        # escala aproximadamente logarítmica: 0.001 -> 0, 0.3 -> 1
        import math
        if v <= 0.001:
            return 0.0
        return max(0.0, min(1.0, (math.log10(v) + 3) / 2.5))

    def atualizar(self, valor: float, limiar: float):
        # sobe rápido, desce devagar (fica mais fácil de ler)
        self.valor = valor if valor > self.valor else self.valor * 0.8 + valor * 0.2
        self.limiar = limiar
        self.update()

    def zerar(self):
        self.valor = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E3E8EE"))
        p.drawRoundedRect(r, 5, 5)
        largura = r.width() * self._escala(self.valor)
        if largura > 0:
            cor = QColor(CANAL) if self.valor > self.limiar else QColor("#9FB3B6")
            p.setBrush(cor)
            p.drawRoundedRect(QRectF(0, 0, largura, r.height()), 5, 5)
        x = r.width() * self._escala(self.limiar)
        p.setBrush(QColor(TINTA))
        p.drawRect(QRectF(x - 1, 0, 2, r.height()))


# ---------------- Legendas flutuantes ----------------

class LegendaFlutuante(QWidget):
    """Faixa de legenda sempre por cima de tudo. Arraste para mover,
    use a rodinha do mouse para mudar o tamanho da letra."""

    def __init__(self, configuracoes: QSettings):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.configuracoes = configuracoes
        self.tamanho_fonte = int(configuracoes.value("legenda/tamanho", 26))
        self.original = ""
        self.traducao = ""
        self._arrastando = None
        self.setToolTip("Arraste para mover. Rodinha do mouse: tamanho da letra.")

        self._sumir = QTimer(self, singleShot=True, interval=7000)
        self._sumir.timeout.connect(self._limpar)

        tela = QApplication.primaryScreen().availableGeometry()
        largura = int(tela.width() * 0.62)
        self.resize(largura, 120)
        posicao = configuracoes.value("legenda/posicao")
        if isinstance(posicao, QPoint):
            self.move(posicao)
        else:
            self.move(tela.x() + (tela.width() - largura) // 2,
                      tela.y() + int(tela.height() * 0.80))

    def mostrar_texto(self, traducao: str, original: str = ""):
        self.traducao, self.original = traducao, original
        self._ajustar_altura()
        self.update()
        self._sumir.start()

    def _limpar(self):
        self.traducao = self.original = ""
        self.update()

    def _fontes(self):
        principal = QFont(FONTE_TITULO, -1, QFont.DemiBold)
        principal.setPixelSize(self.tamanho_fonte)
        secundaria = QFont(FONTE_UI)
        secundaria.setPixelSize(max(12, int(self.tamanho_fonte * 0.55)))
        return principal, secundaria

    def _ajustar_altura(self):
        principal, secundaria = self._fontes()
        largura_texto = self.width() - 56
        m1, m2 = QFontMetrics(principal), QFontMetrics(secundaria)
        flags = Qt.TextWordWrap
        h_trad = m1.boundingRect(0, 0, largura_texto, 2000, flags, self.traducao or " ").height()
        h_orig = (m2.boundingRect(0, 0, largura_texto, 2000, flags, self.original).height() + 6
                  if self.original else 0)
        self.resize(self.width(), h_trad + h_orig + 36)

    def paintEvent(self, _):
        if not self.traducao:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        principal, secundaria = self._fontes()

        caminho = QPainterPath()
        caminho.addRoundedRect(QRectF(self.rect()), 14, 14)
        p.fillPath(caminho, QColor(14, 22, 30, 214))
        # linha do "canal" à esquerda
        p.fillRect(QRectF(0, 18, 4, self.height() - 36), QColor(CANAL).lighter(140))

        area = self.rect().adjusted(28, 16, -28, -16)
        y = area.top()
        if self.original:
            p.setFont(secundaria)
            p.setPen(QColor(205, 214, 224, 200))
            r = p.boundingRect(area.left(), y, area.width(), 2000, Qt.TextWordWrap, self.original)
            p.drawText(r, Qt.TextWordWrap, self.original)
            y = r.bottom() + 6
        p.setFont(principal)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(area.left(), y, area.width(), area.bottom() - y + 4),
                   Qt.TextWordWrap, self.traducao)

    # mover e redimensionar
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._arrastando = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._arrastando is not None:
            self.move(e.globalPosition().toPoint() - self._arrastando)

    def mouseReleaseEvent(self, _):
        if self._arrastando is not None:
            self._arrastando = None
            self.configuracoes.setValue("legenda/posicao", self.pos())

    def wheelEvent(self, e):
        passo = 2 if e.angleDelta().y() > 0 else -2
        self.tamanho_fonte = max(14, min(56, self.tamanho_fonte + passo))
        self.configuracoes.setValue("legenda/tamanho", self.tamanho_fonte)
        self._ajustar_altura()
        self.update()


# ---------------- Janela principal ----------------

class JanelaPrincipal(QMainWindow):
    MAX_ITENS = 60

    def __init__(self, pipeline=None, cfg: Config = None):
        super().__init__()
        self.cfg = cfg or Config()
        self.configuracoes = QSettings("TradutorVoz", "TradutorVoz")
        self.cfg.falar_traducao = self.configuracoes.value("falar", True, type=bool)

        self.ponte = Ponte()
        self.ponte.evento.connect(self._ao_evento)
        if pipeline is None:
            from tradutor_voz.pipeline import Pipeline
            pipeline = Pipeline(self.cfg, ao_evento=lambda t, d: self.ponte.evento.emit(t, d))
        self.pipeline = pipeline

        self.modelos_prontos = False
        self.rodando = False
        self.estado_texto = None
        self.itens = []
        self._ultimo_original = ""

        self.legenda = LegendaFlutuante(self.configuracoes)

        self.setWindowTitle("Tradutor de voz")
        self.setMinimumSize(480, 620)
        self.resize(540, 720)
        self._montar()
        self.setStyleSheet(ESTILO)
        self._preencher_dispositivos()
        self._atualizar_estado()

    # ---------- montagem ----------

    def _montar(self):
        raiz = QWidget(objectName="raiz")
        self.setCentralWidget(raiz)
        layout = QVBoxLayout(raiz)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        topo = QHBoxLayout()
        self.titulo = QLabel("Tradutor de voz", objectName="titulo")
        self.estado = QLabel()
        topo.addWidget(self.titulo)
        topo.addStretch()
        topo.addWidget(self.estado)
        layout.addLayout(topo)

        painel = QFrame(objectName="painel")
        grade = QGridLayout(painel)
        grade.setContentsMargins(16, 14, 16, 14)
        grade.setHorizontalSpacing(10)
        grade.setVerticalSpacing(10)

        self.combo_fonte = QComboBox()
        self.combo_fonte.addItem("Áudio do computador", "pc")
        self.combo_fonte.addItem("Microfone", "microfone")
        self.combo_fonte.setToolTip("Áudio do computador: reuniões no Zoom, Teams, Meet e vídeos.")
        self.combo_captura = QComboBox()
        self.combo_saida = QComboBox()
        self.botao_atualizar = QToolButton(text="Atualizar")
        self.botao_atualizar.setToolTip("Procurar dispositivos de som de novo")

        grade.addWidget(QLabel("Traduzir o som de", objectName="rotulo"), 0, 0)
        grade.addWidget(self.combo_fonte, 0, 1, 1, 2)
        grade.addWidget(QLabel("Capturar de", objectName="rotulo"), 1, 0)
        grade.addWidget(self.combo_captura, 1, 1)
        grade.addWidget(self.botao_atualizar, 1, 2)
        grade.addWidget(QLabel("Ouvir a tradução em", objectName="rotulo"), 2, 0)
        grade.addWidget(self.combo_saida, 2, 1, 1, 2)
        grade.setColumnStretch(1, 1)

        opcoes = QHBoxLayout()
        self.check_falar = QCheckBox("Falar a tradução")
        self.check_falar.setChecked(self.cfg.falar_traducao)
        self.check_legendas = QCheckBox("Legendas na tela")
        self.check_legendas.setChecked(self.configuracoes.value("legendas", True, type=bool))
        opcoes.addWidget(self.check_falar)
        opcoes.addSpacing(16)
        opcoes.addWidget(self.check_legendas)
        opcoes.addStretch()
        grade.addLayout(opcoes, 3, 0, 1, 3)

        self.alerta = QLabel(objectName="alerta", wordWrap=True)
        self.alerta.hide()
        grade.addWidget(self.alerta, 4, 0, 1, 3)

        self.medidor = MedidorSinal()
        grade.addWidget(self.medidor, 5, 0, 1, 3)
        layout.addWidget(painel)

        self.botao = QPushButton(objectName="principal")
        self.botao.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.botao)

        self.transcricao = QTextBrowser()
        self.transcricao.setOpenLinks(False)
        layout.addWidget(self.transcricao, 1)

        self.rodape = QLabel(objectName="rodape", wordWrap=True)
        layout.addWidget(self.rodape)

        # conexões
        self.combo_fonte.currentIndexChanged.connect(self._ao_mudar_fonte)
        self.combo_captura.currentIndexChanged.connect(self._verificar_eco)
        self.combo_saida.currentIndexChanged.connect(self._verificar_eco)
        self.botao_atualizar.clicked.connect(self._preencher_dispositivos)
        self.check_falar.toggled.connect(self._ao_mudar_falar)
        self.check_legendas.toggled.connect(self._ao_mudar_legendas)
        self.botao.clicked.connect(self._iniciar_ou_parar)

        self._mostrar_vazio()

    # ---------- dispositivos ----------

    def _preencher_dispositivos(self):
        fonte_salva = self.configuracoes.value("fonte", "pc")
        indice = self.combo_fonte.findData(fonte_salva)
        self.combo_fonte.blockSignals(True)
        self.combo_fonte.setCurrentIndex(max(0, indice))
        self.combo_fonte.blockSignals(False)
        self._preencher_captura()

        from tradutor_voz import captura as cap
        self.combo_saida.blockSignals(True)
        self.combo_saida.clear()
        self.combo_saida.addItem("Padrão do Windows", None)
        try:
            for nome in cap.listar_saidas_reproducao():
                self.combo_saida.addItem(nome, nome)
        except Exception as erro:
            self._alertar(f"Não foi possível listar as saídas de som ({type(erro).__name__}).")
        salvo = self.configuracoes.value("saida")
        i = self.combo_saida.findData(salvo) if salvo else 0
        self.combo_saida.setCurrentIndex(max(0, i))
        self.combo_saida.blockSignals(False)
        self._verificar_eco()

    def _preencher_captura(self):
        from tradutor_voz import captura as cap
        fonte = self.combo_fonte.currentData()
        self.combo_captura.blockSignals(True)
        self.combo_captura.clear()
        try:
            if fonte == "pc":
                itens, padrao = cap.listar_saidas_captura()
                for i, nome in itens:
                    rotulo = f"{nome}  (padrão)" if i == padrao else nome
                    self.combo_captura.addItem(rotulo, nome)
                    self.combo_captura.setItemData(self.combo_captura.count() - 1, nome,
                                                   Qt.ToolTipRole)
                salvo = self.configuracoes.value("captura_pc")
            else:
                itens, padrao = cap.listar_microfones()
                for i, nome in itens:
                    rotulo = f"{nome}  (padrão)" if i == padrao else nome
                    self.combo_captura.addItem(rotulo, i)
                salvo = self.configuracoes.value("captura_microfone", type=int)
            i = self.combo_captura.findData(salvo) if salvo is not None else -1
            if i < 0:
                i = next((k for k, (j, _) in enumerate(itens) if j == padrao), 0)
            self.combo_captura.setCurrentIndex(i)
        except Exception as erro:
            self._alertar(f"Não foi possível listar os dispositivos ({type(erro).__name__}: {erro}).")
        self.combo_captura.blockSignals(False)

    def _ao_mudar_fonte(self):
        self.configuracoes.setValue("fonte", self.combo_fonte.currentData())
        self._preencher_captura()
        self._verificar_eco()

    def _verificar_eco(self):
        if self.combo_fonte.currentData() != "pc" or not self.check_falar.isChecked():
            self.alerta.hide()
            return
        captura = self.combo_captura.currentData() or ""
        saida = self.combo_saida.currentData()
        if saida is None:
            try:
                from tradutor_voz import captura as cap
                saida = cap.nome_saida_padrao()
            except Exception:
                saida = ""
        if nomes_parecidos(captura, saida):
            self._alertar("A tradução toca no mesmo dispositivo capturado, então o áudio fica "
                          "em pausa enquanto ela fala. Para não perder nada, envie a reunião "
                          "para outra saída de som.")
        else:
            self.alerta.hide()

    def _alertar(self, texto: str):
        self.alerta.setText(texto)
        # o QLabel com quebra de linha não ganha altura sozinho dentro da grade:
        # pergunta a ele quanto precisa na largura disponível (já com o estilo aplicado)
        self.alerta.ensurePolished()
        largura = max(200, self.alerta.parentWidget().width() - 34)
        self.alerta.setFixedHeight(self.alerta.heightForWidth(largura) + 8)
        self.alerta.show()

    def resizeEvent(self, evento):
        super().resizeEvent(evento)
        # isHidden (e não isVisible): também recalcula antes de a janela aparecer
        if not self.alerta.isHidden():
            self._alertar(self.alerta.text())

    # ---------- opções ----------

    def _ao_mudar_falar(self, ligado: bool):
        self.cfg.falar_traducao = ligado
        self.configuracoes.setValue("falar", ligado)
        self.combo_saida.setEnabled(ligado and not self.rodando)
        self._verificar_eco()

    def _ao_mudar_legendas(self, ligado: bool):
        self.configuracoes.setValue("legendas", ligado)
        if ligado and self.rodando:
            self.legenda.show()
        else:
            self.legenda.hide()

    # ---------- iniciar / parar ----------

    def carregar_modelos(self):
        self.estado_texto = "Carregando modelos"
        self._atualizar_estado()
        threading.Thread(target=self._carregar_em_segundo_plano, daemon=True).start()

    def _carregar_em_segundo_plano(self):
        try:
            self.pipeline.carregar()
        except Exception as erro:
            self.ponte.evento.emit("erro", {"mensagem": f"Falha ao carregar os modelos: "
                                                        f"{type(erro).__name__}: {erro}"})

    def _iniciar_ou_parar(self):
        self.botao.setEnabled(False)
        if self.rodando:
            self.estado_texto = "Parando"
            self._atualizar_estado()
            threading.Thread(target=self.pipeline.parar, daemon=True).start()
            return

        fonte = self.combo_fonte.currentData()
        dispositivo = self.combo_captura.currentData()
        saida = self.combo_saida.currentData() if self.check_falar.isChecked() else None
        if dispositivo is None:
            self._alertar("Escolha de onde capturar o som.")
            self.botao.setEnabled(True)
            return
        chave = "captura_pc" if fonte == "pc" else "captura_microfone"
        self.configuracoes.setValue(chave, dispositivo)
        self.configuracoes.setValue("saida", saida)

        self.estado_texto = ("Calibrando o microfone, fique em silêncio" if fonte == "microfone"
                             else "Iniciando")
        self._atualizar_estado()

        def iniciar():
            try:
                self.pipeline.iniciar(fonte, dispositivo, saida)
            except Exception as erro:
                self.ponte.evento.emit("erro", {"mensagem": f"Não foi possível iniciar: "
                                                            f"{type(erro).__name__}: {erro}"})
        threading.Thread(target=iniciar, daemon=True).start()

    # ---------- eventos do pipeline ----------

    def _ao_evento(self, tipo: str, d: dict):
        if tipo == "nivel":
            self.medidor.atualizar(d["valor"], d["limiar"])
        elif tipo == "status":
            self.estado_texto = d["mensagem"].rstrip(".")
            self._atualizar_estado()
        elif tipo == "carregado":
            self.modelos_prontos = True
            self.estado_texto = None
            self.rodape.setText(f"Reconhecimento: {d['whisper']}      Tradução: {d['traducao']}"
                                f"      Voz: {d['voz']}")
            self._atualizar_estado()
        elif tipo == "iniciado":
            self.rodando = True
            self.estado_texto = None
            self._limpar_vazio()
            if self.check_legendas.isChecked():
                self.legenda.show()
            self._atualizar_estado()
        elif tipo == "parado":
            self.rodando = False
            self.estado_texto = None
            self.medidor.zerar()
            self.legenda.hide()
            self._atualizar_estado()
        elif tipo == "erro":
            QMessageBox.warning(self, "Tradutor de voz", d["mensagem"])
            if self.rodando:
                threading.Thread(target=self.pipeline.parar, daemon=True).start()
            else:
                self.estado_texto = None
                self._atualizar_estado()
        elif tipo in ("aviso", "sem_som"):
            self._adicionar_nota(d["mensagem"].lstrip("⚠️⏸️ "))
        elif tipo == "som":
            # o som chegou: some com avisos antigos, menos o de eco (que continua valendo)
            if not self.alerta.text().startswith("A tradução"):
                self.alerta.hide()
        elif tipo == "original":
            self._ultimo_original = d["texto"]
            idioma = nome_idioma(d["idioma"])
            confianca = "idioma mantido" if d["mantido"] else f"{d['confianca']:.0%}"
            self._adicionar(
                f'<div style="margin-top:10px; color:{SECUNDARIO}; font-size:12px;">'
                f'{idioma}, {confianca}</div>'
                f'<div style="color:{SECUNDARIO};">{html.escape(d["texto"])}</div>')
        elif tipo == "traducao":
            self._adicionar(f'<div style="color:{TINTA}; font-size:16px; font-weight:600;">'
                            f'{html.escape(d["texto"])}</div>')
            if self.check_legendas.isChecked():
                self.legenda.mostrar_texto(d["texto"], d.get("original", ""))
        elif tipo == "ignorado" and d["motivo"] == "mesmo_idioma":
            self._adicionar(f'<div style="color:{SECUNDARIO}; font-size:12px;">'
                            'Já está no seu idioma.</div>')
        elif tipo == "latencia":
            self._adicionar(f'<div style="color:{SECUNDARIO}; font-size:12px;">'
                            f'Ouvida {d["total"]:.1f} s depois da fala</div>')

    # ---------- transcrição ----------

    def _mostrar_vazio(self):
        self.transcricao.setHtml(
            f'<div style="color:{SECUNDARIO}; margin:16px 8px; line-height:140%;">'
            'As falas e as traduções aparecem aqui.<br>'
            'Escolha de onde vem o som e clique em <b>Iniciar tradução</b>.</div>')
        self._vazio = True

    def _limpar_vazio(self):
        if getattr(self, "_vazio", False):
            self.transcricao.clear()
            self.itens = []
            self._vazio = False

    def _adicionar(self, trecho_html: str):
        self._limpar_vazio()
        self.itens.append(trecho_html)
        self.itens = self.itens[-self.MAX_ITENS * 3:]
        self.transcricao.setHtml("".join(self.itens))
        barra = self.transcricao.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _adicionar_nota(self, texto: str):
        self._adicionar(f'<div style="color:{ALERTA_TEXTO}; font-size:12px; margin-top:8px;">'
                        f'{html.escape(texto)}</div>')

    # ---------- estado visual ----------

    def _atualizar_estado(self):
        texto_estado = getattr(self, "estado_texto", None)
        if texto_estado:
            self.estado.setText(f'<span style="color:{SECUNDARIO};">{html.escape(texto_estado)}…</span>')
        elif self.rodando:
            self.estado.setText(f'<span style="color:{AO_VIVO}; font-weight:600;">● Ao vivo</span>')
        elif self.modelos_prontos:
            self.estado.setText(f'<span style="color:{CANAL_ESCURO};">Pronto</span>')
        else:
            self.estado.setText(f'<span style="color:{SECUNDARIO};">Carregando modelos…</span>')

        ocupado = bool(texto_estado)
        self.botao.setText("Parar tradução" if self.rodando else "Iniciar tradução")
        self.botao.setProperty("aoVivo", self.rodando)
        self.botao.style().unpolish(self.botao)
        self.botao.style().polish(self.botao)
        self.botao.setEnabled(self.modelos_prontos and not ocupado)

        travado = self.rodando or ocupado
        for w in (self.combo_fonte, self.combo_captura, self.botao_atualizar):
            w.setEnabled(not travado)
        self.combo_saida.setEnabled(not travado and self.check_falar.isChecked())

    def closeEvent(self, evento):
        self.legenda.close()
        if self.rodando:
            self.pipeline.parar()
        super().closeEvent(evento)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Tradutor de voz")
    app.setApplicationVersion(__version__)
    janela = JanelaPrincipal()
    janela.show()
    janela.carregar_modelos()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
