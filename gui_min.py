#!/usr/bin/env python3

# Universidad de Costa Rica 
# Escuela de Ingenieria Electrica
# IE0499 - Proyecto Electrico
# Desarrollo de una interfaz grafica interactiva para la
# elaboracion de floorplan en ASICs

# Estudiante: Elizabeth Matamoros Bojorge C04652

# Dependecias necesarias
from pathlib import Path
import json, math
from collections import defaultdict

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPen, QBrush, QPainter, QFont, QColor, QLinearGradient
from PySide6.QtWidgets import (
    QApplication, QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QMainWindow, QStatusBar, QGraphicsItem, QGraphicsSimpleTextItem,
    QGraphicsDropShadowEffect, QWidget, QHBoxLayout, QPushButton,
    QMessageBox, QVBoxLayout
)

# Parametros y rutas
AUTOSCALE_ON_LOAD = True # Activa el autoescalado de los modulos al abrir el diseno
MAX_SIDE_FRAC     = 0.35 # Limite superior del lado del bloque tras autoscale
MIN_SIDE_PX       = 32 # Tamano minimo permitido para los bloques al hacer autoscale
PX_TO_UM = 1.0  # Factor de escala geometrica entre la GUI (px) y OpenLane (µm)
# Calcula la ruta absoluta del proyecto tomando el directorio del script
ROOT   = Path(__file__).resolve().parents[1]
# Construye la ruta absoluta al archivo artifacts/design.json 
# donde la GUI lee y guarda el snapshot del diseño
DESIGN = (ROOT / "artifacts/design.json").resolve()

# Helpers
def _snap(v, g): 
    """
    Funcion encargada de alinear un valor 
    al multiplo de grid mas cercano

    Parametros:
    v: valor continuo a alinear.
    g: tamano del grid en pixeles para el snapping

    Return:
    int: valor alineado al multiplo de g mas cercano
    """
    return int(round(v / g) * g)

def _pair(a,b): 
    """
    Funcion encargada de construir una tupla ordenada 
    e invariante al orden de entrada

    Parametros:
    a: primer elemento comparable
    b: segundo elemento comparable

    Return:
    tuple: tupla (x, y) con (a, b) en orden ascendente
    """
    return tuple(sorted((a,b)))

def _map_width(w, wmax, min_w=0.9, max_w=6.0):
    """
    Funcion encargada de mapear un peso relativo 
    de conexion a un grosor de linea

    Parametros:
    w: peso actual de la arista
    wmax: peso maximo observado
    min_w: grosor minimo de linea permitido
    max_w: grosor maximo de linea permitido

    Return:
    float: grosor sugerido para el QPen, proporcional a sqrt(w/wmax)
    """
    if wmax <= 0: return min_w
    t = math.sqrt(float(w) / float(wmax))
    return min_w + (max_w - min_w) * t

def _map_color(w, wmax):
    """
    Funcion encargada de mapear un peso relativo 
    a un color RGBA para visualizar conexiones

    Parametros:
    w: peso actual de la arista
    wmax: peso maximo observado

    Return:
    QColor: color interpolado con alfa ~200 segun w/wmax
    """
    if wmax <= 0: t = 0.0
    else: t = max(0.0, min(1.0, float(w) / float(wmax)))
    # Interpolacion por tramos
    # Para t en [0, 0.5]: verde - amarillo
    # Para t en [0.5, 1]:  amarillo - rojo
    if t < 0.5:
        u = t / 0.5; 
        c0 = (60,199,74);  # Verde 
        c1 = (255,210,74)  # Amarillo
    else:
        u = (t-0.5)/0.5; 
        c0 = (255,210,74); # Amarillo
        c1 = (229,57,53)   # Rojo
    r = int(c0[0] + (c1[0]-c0[0])*u)
    g = int(c0[1] + (c1[1]-c0[1])*u)
    b = int(c0[2] + (c1[2]-c0[2])*u)
    # Retorna el color con alfa fijo (200) para cierta 
    # transparencia sobre el canvas
    return QColor(r,g,b,200)

def _expand_bus(name: str, width: int):
    """
    Funcion encargada de expandir un puerto/bus 
    en nombres bit a bit

    Parametros:
    name: nombre base del puerto
    width: ancho del bus 

    Return:
    list: Lista de nombres expandidos. Para width <= 1: ["name"]
    Para width > 1: ["name[0]", "name[1]", ..., "name[width-1]"] 
    (LSB -> MSB)
    """
    if width <= 1: return [name] # Si es <= 1, se trata como escalar
    return [f"{name}[{i}]" for i in range(width)]  

class BlockItem(QGraphicsRectItem):
    """
    Clase encargada de representar un modulo del diseno como un rectangulo
    arrastrable dentro del core, con snapping al grid, limites y validacion
    de no-solape
    """
    def __init__(self, module: dict, grid: int, core_rect: QRectF, on_moved, can_place):
        """
        Funcion encargada de inicializar un bloque/modulo 
        arrastrable dentro del core

        Parametros:
        module: datos del modulo
        grid: tamano de la grilla para el snapping (px)
        core_rect: area del core que limita el movimiento del bloque
        on_moved: Callback sin argumentos tras un movimiento valido
        can_place: valida ubicacion (no solape)

        Return:
        None. Configura estilo, etiqueta, z-order y posicion inicial del bloque
        """
        # Crea el rectangulo base del item con origen (0,0) y tamano w × h
        super().__init__(0, 0, module["w"], module["h"])
        # Guarda referencias a los parametros para usarlos en validaciones,
        # limites y notificacion de cambios
        self.module = module
        self.grid = grid
        self.core_rect = core_rect
        self.on_moved = on_moved
        self.can_place = can_place
        # Habilita mover con el mouse, recibir eventos de cambio geometrico 
        # y permitir seleccion
        self.setFlags(QGraphicsRectItem.ItemIsMovable |
                      QGraphicsRectItem.ItemSendsGeometryChanges |
                      QGraphicsRectItem.ItemIsSelectable)
        # Estilo visual: borde negro y relleno cian
        self.setPen(QPen(Qt.black, 1))
        self.setBrush(QBrush(Qt.cyan))
        # Intenta aplicar sombra suave para mejorar percepcion de profundidad
        try:
            eff = QGraphicsDropShadowEffect()
            eff.setBlurRadius(12); eff.setOffset(0, 2)
            self.setGraphicsEffect(eff)
        except Exception: # Si el backend no la soporta, lo ignora
            pass
        # Prioriza el bloque en el apilado Z
        self.setZValue(10)
        # Crea la etiqueta con el nombre de la instancia
        self.label = QGraphicsSimpleTextItem(f'{module["inst"]}', self)
        # Configura tipografia legible (10 pt, negrita)
        f = QFont(); f.setPointSizeF(10.0); f.setBold(True)
        self.label.setFont(f)
        # Bandera para suprimir efectos secundarios 
        # mientras se termina la construccion
        self._constructed = False
        # Posicion inicial (usa 0,0 si no viene en design.json)
        self.setPos(module.get("x", 0), module.get("y", 0))
        # Centra la etiqueta dentro del rectangulo
        self._place_label()
        # A partir de aqui, los movimientos disparan updates
        self._constructed = True

    def _place_label(self):
        """
        Funcion encargada de centrar la etiqueta del bloque dentro 
        de su rectangulo 
        """
        r = self.rect(); # Rectangulo del bloque
        br = self.label.boundingRect() # Rectangulo delimitador del texto de la etiqueta
        # Fija la posicion (x, y) de la etiqueta para centrarla
        self.label.setPos(r.x() + (r.width()-br.width())/2.0,     # Centra horizontalmente
                          r.y() + (r.height()-br.height())/2.0)   # Centra verticalmente

    def itemChange(self, change, value):
        """
        Funcion encargada de interceptar cambios del item 
        y aplicar reglas de movimiento

        Parametros:
        change: tipo de cambio notificado por Qt
        value: valor asociado al cambio

        Return:
        QPointF: nueva posicion corregida o anterior si se revierte el movimiento
        None: en otros cambios, delega al comportamiento base de Qt
        """
        # Rama que se ejecuta antes de aplicar la nueva posicion propuesta
        if change == QGraphicsItem.ItemPositionChange:
            r0 = self.rect() # Rect actual del bloque para conocer w y h
            # Snapping de la posicion propuesta al multiplo mas cercano del grid
            x = _snap(value.x(), self.grid); y = _snap(value.y(), self.grid)
            # Limites permitidos dentro del core para que el bloque no se salga
            minx = self.core_rect.left();  miny = self.core_rect.top()
            maxx = self.core_rect.right()  - r0.width()
            maxy = self.core_rect.bottom() - r0.height()
            # Garantiza que el bloque quede dentro de los limites
            x = min(max(minx, x), maxx); y = min(max(miny, y), maxy)
            # Rect para la nueva posicion 
            new_rect = QRectF(x, y, r0.width(), r0.height())
            # Consulta el callback externo de no-solape
            if not self.can_place(new_rect, self.module["inst"]):
                # Si no se puede ubicar, revierte entregando la posicion previa conocida
                return QPointF(self.module.get("x", self.x()),
                               self.module.get("y", self.y()))
            # Si pasa validacion, devuelve la posicion corregida
            return QPointF(x, y)
        # Rama que se ejecuta despues de que la posicion ya cambio
        elif change == QGraphicsItem.ItemPositionHasChanged:
            # Evita persistencia y recalculos durante la construccion incial
            if not getattr(self, "_constructed", False):
                return super().itemChange(change, value)
            # Persiste la posicion final en el modulo, para que se pueda guardar/exportar
            p = self.pos()
            self.module["x"], self.module["y"] = int(p.x()), int(p.y())
            # Vuelve a centrar la etiqueta
            self._place_label() 
            # Notifica a la GUI
            if self.on_moved: self.on_moved()
        # Para cualquier otro tipo de cambio, delega al comportamiento base de Qt
        return super().itemChange(change, value)

class PinItem(QGraphicsRectItem):
    """
    Clase encargada de representar un pin I/O arrastrable que se ajusta automaticamente 
    al borde del core, con snap al grid, etiqueta y tooltip
    Actualiza su posicion y lado en el diseno y notifica cambios para redibujar
    la conectividad
    """
    def __init__(self, pin: dict, size: int, grid: int, core_rect: QRectF, on_moved):
        """
        Funcion encargada de inicializar un pin I/O arrastrable 
        anclado al perimetro del core

        Parametros:
        pin: datos del pin
        size: tamano del cuadrado del pin (px)
        grid: tamano de la grilla para el snap (px)
        core_rect: rectangulo del core para proyectar y acotar el pin
        on_moved: callback sin argumentos al finalizar un movimiento

        Return:
        None. Configura estilo, tooltip, etiqueta y posicion inicial de la etiqueta
        """
        # Crea el rectangulo base del pin (sizexsize) con origen local (0,0)
        super().__init__(0, 0, size, size)
        # Guarda referencias: datos del pin, tamano, grilla, core y callback
        self.pin   = pin; self.size  = size
        self.grid  = grid; self.core  = core_rect
        self.on_moved = on_moved
        # Habilita arrastre, notificacion de cambios geometricos y seleccion
        self.setFlags(QGraphicsRectItem.ItemIsMovable |
                      QGraphicsRectItem.ItemSendsGeometryChanges |
                      QGraphicsRectItem.ItemIsSelectable)
        # Estilo visual: borde negro fino y relleno magenta
        self.setPen(QPen(Qt.black, 1))
        self.setBrush(QBrush(Qt.magenta))
        # Coloca el pin por debajo de los bloques
        self.setZValue(9)
        # Obtiene nombre/direccion/ancho y arma el tooltip informativo
        name = pin["name"]; d = pin.get("dir","in"); wid = pin.get("width",1)
        self.setToolTip(f'{name} ({d}[{wid}])')
        # Crea la etiqueta de texto y le asigna una fuente legible
        self.label = QGraphicsSimpleTextItem(name, self)
        f = QFont(); f.setPointSizeF(10.0); f.setBold(True)
        self.label.setFont(f)
        # Posiciona la etiqueta alrededor del pin segun el lado indicado
        self._place_label(side=pin.get("side"))

    def _place_label(self, side=None):
        """
        Funcion encargada de ubicar la etiqueta del pin alrededor 
        del cuadrado, segun el lado del core (N/S/E/W)

        Parametros:
        side: lado deseado para la etiqueta (N, S, E, W)
        Si es None, usa pin["side"] o N

        Return:
        None. Solo ajusta la posicion de self.label
        """
        # Si no se pasa side, toma el lado guardado en el pin
        # si no existe, usa "N"
        if side is None: side = self.pin.get("side","N")
        # Obtiene el rectangulo local del pin (para conocer x, y, ancho y alto)
        r = self.rect()
        if side == "N":   self.label.setPos(r.x(), r.y() - 16) # Coloca la etiqueta arriba del pin
        elif side == "S": self.label.setPos(r.x(), r.y() + r.height() + 2) # Coloca la etiqueta debajo del pin
        elif side == "W": self.label.setPos(r.x() - 28, r.y()) # Coloca la etiqueta a la izquierda del pin
        else:             
            self.label.setPos(r.x() + r.width() + 2, r.y()) # Para cualquier otro caso, coloca la etiqueta a la derecha

    def _project_to_perimeter(self, x, y):
        """
        Funcion encargada de proyectar un pin (x,y) al borde del 
        core mas cercano, aplicando snap al grid y limites validos

        Parametros:
        x: coordenada X propuesta (px)
        y: coordenada Y propuesta (px)

        Return:
        tuple: con la posicion corregida del pin (px) y el lado elegido (N, S, E, W)
        """
        # Calcula el centro del pin a partir de su esquina (x,y) y su tamano
        cx = x + self.size/2.0; cy = y + self.size/2.0
        # Lee los limites del core
        top, bottom = self.core.top(), self.core.bottom()
        left, right = self.core.left(), self.core.right()
        # Calcula la distancia del centro del pin a cada borde del core
        dN = abs(cy - top); dS = abs(cy - bottom); dW = abs(cx - left); dE = abs(cx - right)
        # Elige el lado mas cercano comparando las distancias (N/S/W/E)
        side = min([("N",dN),("S",dS),("W",dW),("E",dE)], key=lambda t: t[1])[0]
        # Si es Norte: coloca el pin encima del borde superior hace snap 
        # de x al grid y clamp entre left y right - size
        if side == "N":
            y = top - self.size; x = _snap(x, self.grid); x = max(left, min(x, right - self.size))
        # Si es Sur: alinea el pin debajo del core (y = bottom), snap/clamp en x
        elif side == "S":
            y = bottom; x = _snap(x, self.grid); x = max(left, min(x, right - self.size))
        # Si es Oeste: coloca el pin a la izquierda del core (x = left - size), snap/clamp en y
        elif side == "W":
            x = left - self.size; y = _snap(y, self.grid); y = max(top, min(y, bottom - self.size))
        # Si es Este: alinea el pin a la derecha del core (x = right), snap/clamp en y
        else:
            x = right; y = _snap(y, self.grid); y = max(top, min(y, bottom - self.size))
        # Devuelve la posicion corregida y el lado elegido
        return x, y, side

    def itemChange(self, change, value):
        """
        Funcion encargada de manejar cambios del item para proyectar el pin al borde 
        del core, actualizar su estado y notificar cambios

        Parametros:
        change: tipo de cambio (Qt)
        value: valor asociado

        Return:
        QPointF: nueva posicion corregida cuando es ItemPositionChange
        None: en otros cambios delega a super()
        """
        # Rama que se ejecuta antes de aplicar la nueva posicion propuesta
        if change == QGraphicsItem.ItemPositionChange:
            # Toma la posicion propuesta
            x, y = value.x(), value.y()
            # Proyecta al borde del core mas cercano y obtiene el lado
            x, y, side = self._project_to_perimeter(x, y)
            # Actualiza el modelo del pin con la posicion corregida y el lado
            self.pin["x"], self.pin["y"], self.pin["side"] = int(x), int(y), side
            # Devuelve a Qt la posicion final que debe aplicarse
            return QPointF(x, y)
        # Rama que se ejecuta despues de que la posicion ya cambio
        elif change == QGraphicsItem.ItemPositionHasChanged:
            # Recoloca la etiqueta alrededor del pin segun el lado actual
            self._place_label(side=self.pin.get("side","N"))
            # Notifica cambios para redibujar
            if self.on_moved: self.on_moved()
        # Para otros cambios no tratados, delega al comportamiento base de Qt
        return super().itemChange(change, value)

# Ventana principal
class Main(QMainWindow):

    """
    Clase encargada de la ventana principal de la herramienta, mediante la misma
    se carga el diseno, se dibuja el die y el core, se crean los modulos y pines,
    permite moverlos sin traslape, calcula una metrica simple de conexiones (HPWL)
    y exporta los archivos necesarios para OpenLane
    """

    def __init__(self):

        """
        Funcion que construye la ventana principal de la interfaz de 
        floorplanning, cargando el diseno desde design.json, construye 
        el area de dibujo del floorplan y los botones de control,
        e inicializa modulos, pines y metrica HPWL

        Parametros: self: instancia de la clase Main

        Return: None
        """
        # Constructor de la ventana principal
        super().__init__()
        # Lee el archivo design.json
        # Contenido del archivo en un diccionario de Python
        data = json.loads(DESIGN.read_text())
        # Extrae del JSON las listas de modulos, nets 
        # y puertos 
        self.modules = data.get("modules", [])
        self.nets    = data.get("nets", [])
        self.ports   = data.get("ports", [])
        self.topname = data.get("top", "top") # Guarda el nombre del modulo top del diseno
        # Extrae el subdiccionario die
        die = data["die"]
        # Tamano de la grilla, ancho y alto totales del die
        # margen que se deja alrededor del core
        self.grid   = int(die.get("grid", 20))
        self.w      = int(die.get("width", 1000))
        self.h      = int(die.get("height", 1000))
        self.margin = int(die.get("core_margin", 40))
        # Titulo de la ventana principal
        self.setWindowTitle("ASIC Floorplan")
        # Barra de estado asociaciada a la ventana para mostrar HPWL
        self.status = QStatusBar(self); self.setStatusBar(self.status)

        # Botones
        # Contenedor (Barra superior)
        topbar = QWidget(self)
        # Layout horizontal para la barra, ajuste de margenes y espacio entre widgets
        hl = QHBoxLayout(topbar); hl.setContentsMargins(6, 2, 6, 2); hl.setSpacing(10)
        # Boton de Guardar, conecta el click del boton con _on_save_clicked, que escribe la posicion de modulos 
        # y pines en design.json
        self.btn_save   = QPushButton("Guardar", topbar); self.btn_save.clicked.connect(self._on_save_clicked)
        # Boton para exportar los archivos para OpenLane
        self.btn_export = QPushButton("Exportar OpenLane", topbar); self.btn_export.clicked.connect(self._on_export_clicked)
        # Boton para mostrar u ocultar la leyenda de las conexiones
        self.btn_legend = QPushButton("Mostrar/Ocultar Leyenda", topbar); self.btn_legend.clicked.connect(self._toggle_legend)
        # Boton para mostrar u ocultar el cuadro con HPWL y conteo de modulos/pines
        self.btn_metrics= QPushButton("Mostrar/Ocultar Métricas", topbar); self.btn_metrics.clicked.connect(self._toggle_metrics)
        # Agrega los cuatro botones al layout horizontal
        hl.addWidget(self.btn_save); hl.addWidget(self.btn_export); hl.addWidget(self.btn_legend); hl.addWidget(self.btn_metrics); hl.addStretch(1)

        # Area, Die, Core
        # Crea el area grafica donde se dibujara todo
        self.scene = QGraphicsScene(0, 0, self.w, self.h, self)
        # Dibuja la grilla
        self._draw_grid(self.w, self.h, self.grid)
        # Dibuja el contorno del die completo
        self.scene.addRect(0, 0, self.w, self.h, QPen(Qt.black, 2))
        # Calcula el rectangulo del core (area de modulos)
        self.core_rect = QRectF(self.margin, self.margin,
                                self.w - 2*self.margin, self.h - 2*self.margin)
        # Item grafico para el core
        core = QGraphicsRectItem(self.core_rect)
        core.setBrush(QBrush(Qt.lightGray))
        core.setPen(QPen(Qt.darkGray, 1, Qt.DashLine))
        # ZValue bajo para que quede por detras de modulos y pines
        core.setZValue(-5)
        self.scene.addItem(core)

        # Estructuras internas
        # Diccionario instancia para casa modulo
        # Diccionario nombre de pin
        self.items, self.pin_items = {}, {}
        # Lista de lineas graficas para conexiones modulo-modulo
        # Lista de lineas para conexiones modulo-pin
        self.edge_items_mm, self.edge_items_mp = [], []
        # Items graficos que forman la leyenda de colores
        # Items del cuadro de metricas
        self.legend_items, self.metrics_items = [], []
        # Flags para saber si dibujar leyenda y cuadro
        self.show_legend = True; self.show_metrics = False
        # Indica que se esta construyendo el area
        self._building = True

        # Configura un halo para anti-traslape
        self.HALO_PX = 4
        def can_place_fn(new_rect: QRectF, inst: str) -> bool:
            """
            Funcion que retorna True si new_rect no choca 
            con otros modulos
            """
            return not self._occupied(new_rect, ignore_inst=inst, halo_px=self.HALO_PX)

        # Se crea un BlockItem por cada modulo del diseno 
        # y se agrega al area de trabajo
        for m in self.modules:
            it = BlockItem(m, self.grid, self.core_rect, self._post_move, can_place_fn)
            self.scene.addItem(it); self.items[m["inst"]] = it

        # Pines y conectividad
        # Coloca los pines y construye las estructuras de pesos entre modulos y pines
        self._place_pins_initial(self.ports)
        self._build_connectivity()

        # Se crea la vista grafica y el layout principal de la ventana
        self.view = QGraphicsView(self.scene, self)
        # Permite seleccionar varios elementos arrastrando el mouse
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        # Mejora la calidad del dibujo para que las lineas se vean menos dentadas
        self.view.setRenderHint(QPainter.Antialiasing, True)
        # Widget contenedor principal
        main = QWidget(self)
        # Quita margenes y espaciado para que todo se vea compacto
        v = QVBoxLayout(main); v.setContentsMargins(0,0,0,0); v.setSpacing(0)
        # Coloca la barra y la vista en el layout
        v.addWidget(topbar); v.addWidget(self.view)
        # Pone el widget como contenido central de la ventana principal
        self.setCentralWidget(main)

        self.resize(1100, 900) # Fija un tamaño inicial de ventana
        # Si AUTOSCALE_ON_LOAD es True, llama a autoscale_modules 
        # para escalar los bloques
        if AUTOSCALE_ON_LOAD:
            self.autoscale_modules(MAX_SIDE_FRAC, MIN_SIDE_PX)
        self._fit() # Ajusta el zoom de la vista para que todo el die se vea dentro de la ventana

        # Finaliza la fase de construccion y dibuja conectividad, leyenda y metricas
        self._building = False
        self._redraw_edges()
        self._draw_legend()
        self._update_hpwl()

    # GUI helpers
    def resizeEvent(self, event):
        """
        Funcion que maneja el redimensionamiento de la ventana

        Parametros:
        event: Evento de tipo QResizeEvent que Qt entrega 
        cuando la ventana cambia de tamano

        Return:
        None: solo ajusta la vista y la posicion de elementos graficos
        """
        super().resizeEvent(event); self._fit(); self._place_legend(); self._place_metrics_box()

    def keyPressEvent(self, e):
        """
        Funcion que maneja las teclas presionadas dentro 
        de la ventana principal

        Parametros:
        e: Evento de tipo QKeyEvent que contiene la tecla presionada

        Return:
        None: No retorna un valor, solo ejecuta acciones segun la tecla
        """
        # Si se presiona L muestra/oculta la leyenda
        if e.key() == Qt.Key_L: self._toggle_legend()
        # Si se presiona M muestra/oculta el cuadro de metricas
        elif e.key() == Qt.Key_M: self._toggle_metrics()
        else: super().keyPressEvent(e)

    def _fit(self):
        """
        Funcion que ajusta la vista para encajar toda 
        el area grafica en la ventana

        Parametros:
        None

        Return:
        None: Solo modifica el zoom de la QGraphicsView
        """
        self.view.fitInView(self.scene.sceneRect().adjusted(-12, -12, 12, 12), Qt.KeepAspectRatio)

    def _draw_grid(self, w, h, g):
        """
        Funcion que dibuja una cuadricula de fondo sobre el area grafica

        Parametros:
        w: Ancho total del area grafica
        h: Alto total del area grafica
        g: Espaciado de la cuadricula

        Return:
        None: Solo agrega lineas de fondo a la escena
        """
        # Configuraccion para dibujar las lineas de la cuadricula
        pen = QPen(Qt.gray); pen.setCosmetic(True); pen.setWidth(0)
        # Dibuja lineas verticales en el recorrido 
        for x in range(0, w+1, g): self.scene.addLine(x, 0, x, h, pen).setZValue(-10)
        # Dibuja lineas horizontales en el recorrido
        for y in range(0, h+1, g): self.scene.addLine(0, y, w, y, pen).setZValue(-10)

    # Core
    def _core_rect_tuple(self):
        """
        Funcion encargada de calcular el rectangulo del core

        Parameters:
        Ninguno. Usa los atributos de la clase: self.w, self.h y self.margin

        Return:
        tuple: Coordenadas y dimensiones del core en el formato 
        (cx, cy, cw, ch), donde (cx, cy) es la esquina superior 
        izquierda y (cw, ch) son ancho y alto del core
        """
        # esquina superior izquierda del core
        cx, cy = self.margin, self.margin
        # ancho y alto del core
        cw, ch = self.w - 2*self.margin, self.h - 2*self.margin
        return cx, cy, cw, ch

    def _utilization(self):
        """
        Funcion encargada de calcular la utilizacion del core

        Parameters:
        Ninguno. Usa self.modules y las dimensiones del core

        Return:
        float: Porcentaje de area del core ocupada por los bloques
        """
        # Obtiene ancho y alto del core
        cx, cy, cw, ch = self._core_rect_tuple()
        # Area total del core
        core_area = cw * ch
        # Suma el area de todos los bloques
        mods_area = sum(int(x["w"]) * int(x["h"]) for x in self.modules)
        return (100.0 * mods_area / core_area) if core_area > 0 else 0.0

    def _occupied(self, rect: QRectF, ignore_inst: str = None, halo_px: int = 0) -> bool:
        """
        Funcion encargada de verificar si un rectangulo se solapa con algun modulo

        Parameters:
        rect: Rectangulo que se quiere probar dentro del core
        ignore_inst: Nombre de instancia que se debe ignorar
        en la comprobacion 
        halo_px: Margen adicional en pixeles que se agrega
        alrededor de cada rectangulo para evitar que queden 
        demasiado pegados

        Return:
        bool: True si hay solape con algun modulo, False en caso contrario.
        """
        # Aplica un halo alrededor del rectangulo a probar
        rx = rect.adjusted(-halo_px, -halo_px, halo_px, halo_px)
        for m in self.modules:
            if m["inst"] == ignore_inst: continue
            r2 = QRectF(m["x"], m["y"], m["w"], m["h"]).adjusted(-halo_px, -halo_px, halo_px, halo_px)
            if rx.intersects(r2): 
                return True # Hay solape con otro bloque
        return False # No hay solape con ningun bloque
    
    # Autoscale
    def autoscale_modules(self, max_side_frac=MAX_SIDE_FRAC, min_side_px=MIN_SIDE_PX):
        """
        Funcion encargada de ajustar el tamano de todos los modulos
        para que quepan dentro del core con una escala razonable

        Parameters:
        max_side_fra: Fraccion maxima del lado del core que puede ocupar
        el bloque mas grande 
        min_side_px: Tamano minimo permitido para el lado de cada bloque,
        expresado en pixeles.

        Return:
        None: Solo actualiza los tamanos y posiciones de los bloques en
        self.modules y en los objetos graficos asociados
        """
        # Obtiene posicion (cx, cy) y dimensiones (cw, ch) del core
        cx, cy, cw, ch = self._core_rect_tuple()
        #Ttoma el lado mas pequeno del core (ancho o alto)
        die_min = min(cw, ch)
        # Lista donde se guarda (modulo, tamano_base) para cada bloque
        bases = []
        # Itera sobre todos los modulos del diseno
        for m in self.modules:
            # Calcula el area del modulo
            area = m.get("area", max(1, m["w"] * m["h"]))
            # Calcula un lado base a partir del area y lo guarda con el modulo
            bases.append((m, max(1.0, math.sqrt(float(area)))))
        # Si no hay modulos, no hay nada que escalar
        if not bases: return
        # Obtiene el mayor tamano base entre todos los modulos
        max_base = max(b for _, b in bases)
        # Define el lado objetivo maximo del bloque mas grande
        target_max = max(min_side_px, int(round(die_min * max_side_frac)))
        # Factor de escala global para llevar max_base a target_max
        k = target_max / max_base
        # Lado minimo permitido para cualquier bloque
        min_side = max(2 * self.grid, min_side_px)
        # Itera sobre cada modulo con su tamano base
        for m, base in bases:
            # Calcula el nuevo lado escalado y aplica el minimo permitido
            side = max(min_side, int(round(base * k)))
            # Actualiza ancho y alto del modulo
            m["w"], m["h"] = side, side
            # Obtiene el objeto grafico asociado al modulo
            it = self.items[m["inst"]]
            # Actualiza el rectangulo grafico y reposiciona la etiqueta interna
            it.setRect(0, 0, side, side); it._place_label()
            # Intenta conservar la posicion original, pero recortada al interior del core (en X)
            x = max(cx, min(m.get("x", cx), cx + cw - side))
            # Intenta conservar la posicion original, pero recortada al interior del core (en Y)
            y = max(cy, min(m.get("y", cy), cy + ch - side))
            # Rectangulo de prueba con la nueva posicion y tamano
            test = QRectF(x, y, side, side)
            # Mientras haya solape con otros modulos y aun haya espacio hacia la derecha
            # Desplaza el bloque una celda hacia la derecha y fija la posicion final del modulo
            while self._occupied(test, ignore_inst=m["inst"], halo_px=self.HALO_PX) and x + side + self.grid <= cx + cw:
                x += self.grid; test.moveTo(x, y)
            m["x"], m["y"] = x, y; it.setPos(x, y)
        # Actualiza conexiones y metricas despues del escalado
        self._post_move()
        # Muestra en la barra de estado la nueva utilizacion del core
        self.status.showMessage(f"Autoscale | Utilización≈{self._utilization():.1f}%")


    # Pines
    def _place_pins_initial(self, ports):
        """
        Funcion encargada de colocar los pines iniciales 
        alrededor del core, usando posiciones guardadas si 
        existen o un acomodo automatico por lado

        Parametros:
        ports: Lista de diccionarios que describen cada puerto
        del diseno

        Retorn:
        None: Actualiza internamente el area grafica 
        y el diccionario self.pin_items con los pines colocados

        """

        # Revisa si al menos un puerto ya tiene posicion (x, y) y lado definidos
        any_has_xy = any(('x' in p and 'y' in p and 'side' in p) for p in ports)

        # Si hay posiciones guardadas, reconstruye todos los pines con esos datos
        if any_has_xy:
            # Recorre todos los puertos para crear sus PinItem con la posicion guardada
            for p in ports:
                # Calcula el tamano visual del pin
                pin_s = max(10, int(0.8*self.grid))
                # Obtiene el lado guardado o usa Norte por defecto
                side = p.get("side", "N")
                # Construye el diccionario interno del pin con nombre, direccion, ancho y coordenadas
                pin = {"name":p["name"], "dir":p.get("dir","in"), "width":p.get("width",1),
                       "side":side, "x": int(p.get("x", 0)), "y": int(p.get("y", 0))}
                # Crea el objeto grafico del pin y lo inserta en la escena
                it = PinItem(pin, pin_s, self.grid, self.core_rect, self._post_move)
                it.setPos(pin["x"], pin["y"]); self.scene.addItem(it); self.pin_items[p["name"]] = it
            return

        # Listas para agrupar puertos segun el lado al que se enviaran
        north, west, east = [], [], []

        # Clasifica cada puerto segun su nombre y direccion
        for p in ports:
            name = p["name"].lower()
            d = p.get("dir","input").lower()
            # Clk y rst se envian al lado Norte
            if name in ("clk","rst"): north.append(p | {"side":"N"})
            # Salidas se envian al lado Este
            elif d == "output":       east.append(p | {"side":"E"})
            # Entradas u otros se envian al lado Oeste
            else:                     west.append(p | {"side":"W"})

        def place_line(pins, side):
            """
            Funcion encargada de acomodar una lista de pines a lo 
            largo de un lado del core
            """
            # Si no hay pines para este lado, no hace nada
            if not pins: 
                return
            # Calcula el tamanio visual del pin
            pin_s = max(10, int(0.8*self.grid))
            # Obtiene los limites laterales del core
            left, right = int(self.core_rect.left()), int(self.core_rect.right())
            # Obtiene los limites superior e inferior del core
            top, bottom = int(self.core_rect.top()), int(self.core_rect.bottom())

            # Caso para lados Norte y Sur: se reparten a lo largo del eje X
            if side in ("N","S"):
                # Define rango horizontal disponible para colocar pines
                x0 = left + self.grid; x1 = right - self.grid - pin_s
                # Calcula el paso entre pines para repartirlos de forma uniforme
                step = (x1 - x0) / (len(pins) + 1) if len(pins) > 0 else 1
                # Fija la coordenada Y segun sea Norte o Sur
                y = top - pin_s if side=="N" else bottom
                # Recorre la lista de pines y los ubica espaciados en X
                for i, p in enumerate(pins, 1):
                    x = int(x0 + i*step)
                    # Crea el diccionario interno del pin con su lado y posicion
                    pin = {"name":p["name"], "dir":p.get("dir","in"), "width":p.get("width",1),
                           "side":side, "x": x, "y": y}
                    # Crea el objeto grafico del pin y lo agrega al area grafica
                    it = PinItem(pin, pin_s, self.grid, self.core_rect, self._post_move)
                    it.setPos(pin["x"], pin["y"]); self.scene.addItem(it); self.pin_items[p["name"]] = it
            else:
                # Caso para lados Oeste y Este: se reparten a lo largo del eje Y
                y0 = top + self.grid; y1 = bottom - self.grid - pin_s
                # Calcula el paso entre pines para repartirlos de forma uniforme
                step = (y1 - y0) / (len(pins) + 1) if len(pins) > 0 else 1
                # Fija la coordenada X segun sea Oeste o Este
                x = left - pin_s if side=="W" else right
                # Recorre la lista de pines y los ubica espaciados en Y
                for i, p in enumerate(pins, 1):
                    y = int(y0 + i*step)
                    # Crea el diccionario interno del pin con su lado y posicion
                    pin = {"name":p["name"], "dir":p.get("dir","in"), "width":p.get("width",1),
                           "side":side, "x": x, "y": y}
                    # Crea el objeto grafico del pin y lo agrega al area grafica
                    it = PinItem(pin, pin_s, self.grid, self.core_rect, self._post_move)
                    it.setPos(pin["x"], pin["y"]); self.scene.addItem(it); self.pin_items[p["name"]] = it
        # Llama a la funcion auxiliar para colocar pines en Norte, Oeste y Este
        place_line(north, "N"); place_line(west, "W"); place_line(east, "E")

    # Centros
    def _center_block(self, inst):
        """
        Funcion encargada de obtener el centro en pantalla 
        de un bloque dado su nombre
        """
        # Busca el item grafico asociado a la instancia en el diccionario de bloques
        it = self.items.get(inst)
        # Si no existe el bloque, no hay centro que calcular
        if not it: 
            return None
        # Obtiene el rectangulo local del bloque y su posicion en la vista
        r, p = it.rect(), it.pos()
        # Calcula y retorna las coordenadas del centro (x, y) del bloque
        return (p.x() + r.width()/2.0, p.y() + r.height()/2.0)

    def _center_pin(self, name):
        """
        Funcion encargada de obtener el centro en pantalla de un pin dado su nombre
        """
        # Busca el item grafico asociado al pin en el diccionario de pines
        it = self.pin_items.get(name)
        # Si no existe el pin, no hay centro que calcular
        if not it: 
            return None
        # Obtiene el rectangulo local del pin y su posicion en la vista
        r, p = it.rect(), it.pos()
        # Calcula y retorna las coordenadas del centro (x, y) del pin
        return (p.x() + r.width()/2.0, p.y() + r.height()/2.0)

    # Conectividad
    def _build_connectivity(self):
        """
        Funcion encargada de construir los pesos de conexion entre bloques (mm)
        y entre bloques y pines (mp), a partir de las nets del diseno
        """
        # Crea diccionario para pesos entre pares de bloques (module-module)
        # y para pesos entre bloque y pin (module-pin), mas el peso maximo visto
        mm = defaultdict(float); mp = defaultdict(float); maxw = 0.0
        # Itera sobre todas las nets del diseno
        for net in self.nets:
            # Obtiene la lista de endpoints y el ancho (peso) de la net
            eps = net.get("endpoints", []); w = float(net.get("bw", 1))
            # Conjuntos para acumular bloques y pines conectados en esta net
            mods, pins = set(), set()
            # Clasifica cada endpoint como pin top.* o como instancia de bloque
            for ep in eps:
                if ep.startswith("top."): pins.add(ep.split(".",1)[1])
                else: mods.add(ep.split(".",1)[0])
            # Convierte el conjunto de bloques a lista para poder indexar
            mods = list(mods)
            # Para cada par de bloques en la net, incrementa el peso entre ellos
            for i in range(len(mods)):
                for j in range(i+1, len(mods)):
                    # Normaliza el par (ordenado) y acumula el peso
                    k = _pair(mods[i], mods[j]); mm[k] += w
                    # Actualiza el peso maximo observado para escalado de colores
                    maxw = max(maxw, mm[k])
            # Para cada bloque y cada pin en la misma net, acumula peso bloque-pin
            for m in mods:
                for p in pins:
                    k = (m,p); mp[k] += w
                    # Actualiza el peso maximo observado si es necesario
                    maxw = max(maxw, mp[k])
        # Convierte los diccionarios en listas de aristas 
        self.mm_edges = [(a,b,wt) for (a,b), wt in mm.items()]
        self.mp_edges = [(m,p,wt) for (m,p), wt in mp.items()]
        # Guarda el peso maximo para normalizar el grosor/color de las lineas
        self.max_weight = maxw if maxw > 0 else 1.0


    # HPWL
    def _hpwl_net(self, endpoints):
        """
        Funcion encargada de calcular la HPWL aproximada de una net
        a partir de las posiciones de sus endpoints.
        
        """
        # Lista de puntos (x,y) de todos los endpoints que tienen posicion valida
        pts = []
        # Recorre todos los endpoints de la net
        for ep in endpoints:
            # Si el endpoint es un pin de top, busca el centro del pin
            if ep.startswith("top."): c = self._center_pin(ep.split(".",1)[1])
            # Si no, asume que es un bloque interno y busca el centro del bloque
            else: c = self._center_block(ep.split(".",1)[0])
            # Si se obtuvo una posicion valida, se agrega a la lista de puntos
            if c: pts.append(c)
        # Si hay menos de dos puntos, la HPWL es cero
        if len(pts) < 2: return 0.0
        # Separa las coordenadas x e y de todos los puntos
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        # HPWL = (max_x - min_x) + (max_y - min_y)
        return (max(xs)-min(xs)) + (max(ys)-min(ys))
    
    def _update_hpwl(self):
        """
        Funcion encargada de recalcular la HPWL total aproximada
        y actualizar el mensaje de estado y la caja de metricas.
        """
        # Inicializa el acumulador de HPWL total
        total = 0.0
        # Recorre las primeras 500 nets (limite para no hacer el calculo muy pesado)
        for net in self.nets[:500]:
            # Suma la HPWL de cada net a partir de sus endpoints
            total += self._hpwl_net(net.get("endpoints", []))
        # Muestra en la barra de estado la HPWL total y el conteo de bloques y pines
        self.status.showMessage(f"L≈{total:.1f} | Mods={len(self.items)} Pins={len(self.pin_items)}")
        # Si la opcion de metricas esta activa, actualiza la caja de metricas en pantalla
        if self.show_metrics: self._draw_metrics_box(total)

    # Dibujo
    def _redraw_edges(self):
        """
        Funcion encargada de borrar y volver a dibujar las conexiones
        entre bloques y pines segun los pesos de conectividad
        """
        # Elimina del area grafica todas las lineas entre modulos-modulos
        for it in self.edge_items_mm: self.scene.removeItem(it)
        # Elimina del area grafica todas las lineas entre modulos-pines
        for it in self.edge_items_mp: self.scene.removeItem(it)
        # Reinicia las listas internas de lineas dibujadas
        self.edge_items_mm, self.edge_items_mp = [], []
        # Itera sobre todas las conexiones entre pares de modulos
        for a, b, wt in self.mm_edges:
            # Calcula el centro del primer modulo
            c1 = self._center_block(a)
            # Calcula el centro del segundo modulo
            c2 = self._center_block(b)
            # Si alguno no tiene centro valido, pasa a la siguiente conexion
            if not c1 or not c2: continue
            # Crea un lapiz con color segun el peso relativo de la conexion
            pen = QPen(_map_color(wt, self.max_weight))
            # Ajusta el grosor del trazo segun el peso de la conexion
            pen.setWidthF(_map_width(wt, self.max_weight))
            pen.setCosmetic(True)
            # Dibuja una linea entre los centros de ambos modulos
            line = self.scene.addLine(c1[0], c1[1], c2[0], c2[1], pen)
            # Coloca las lineas por encima de los modulos en el orden de dibujo
            line.setZValue(50)
            # Guarda la referencia de la linea en la lista de mm_edges dibujadas
            self.edge_items_mm.append(line)
        # Recorre todas las conexiones entre modulos y pines (mp_edges)
        for m, p, wt in self.mp_edges:
            # Calcula el centro del modulo
            c1 = self._center_block(m)
            # Calcula el centro del pin
            c2 = self._center_pin(p)
            # Si alguna posicion no es valida, pasa a la siguiente conexion
            if not c1 or not c2: continue
            # Crea un lapiz con color segun el peso relativo de la conexion
            pen = QPen(_map_color(wt, self.max_weight))
            # Ajusta el grosor del trazo segun el peso de la conexion
            pen.setWidthF(_map_width(wt, self.max_weight))
            pen.setCosmetic(True)
            # Dibuja una linea entre el modulo y el pin
            line = self.scene.addLine(c1[0], c1[1], c2[0], c2[1], pen)
            # Coloca las lineas por encima de los modulos y pines
            line.setZValue(50)
            # Guarda la referencia de la linea en la lista de mp_edges dibujadas
            self.edge_items_mp.append(line)
        # Redibuja la leyenda de colores para que coincida con las nuevas lineas
        self._draw_legend()


    # Leyenda
    def _clear_legend(self):
        """
        Funcion encargada de eliminar la leyenda actual del area grafica
        y limpiar la lista interna de elementos de leyenda
        """
        # Elimina del area grafica cada item que forma parte de la leyenda
        for it in self.legend_items: self.scene.removeItem(it)
        # Deja la lista de items de leyenda vacia
        self.legend_items = []

    def _place_legend(self):
        """
        Funcion encargada de posicionar la leyenda ya creada
        en una esquina fija de la vista
        """
        # Si no hay items de leyenda creados, no hace nada
        if not self.legend_items: 
            return
        # Define el espaciado al borde y el tamano del cuadro de la leyenda
        pad = 12; box_w, box_h = 160, 64
        # Calcula la posicion superior derecha del cuadro de la leyenda
        x = self.w - box_w - pad; y = pad
        # Ajusta el rectangulo de fondo de la leyenda a la nueva posicion y tamano
        bg = self.legend_items[0]; bg.setRect(x, y, box_w, box_h)
        # Ajusta el rectangulo del gradiente de colores dentro del cuadro
        grad_rect = self.legend_items[1]; grad_rect.setRect(x+12, y+28, box_w-24, 14)
        # Coloca el titulo de la leyenda cerca de la parte superior del cuadro
        t_title = self.legend_items[2]; t_title.setPos(x+12, y+8)
        # Coloca la etiqueta de valor minimo del gradiente
        t_min   = self.legend_items[3]; t_min.setPos(x+12, y+44)
        # Coloca la etiqueta de valor maximo alineada a la derecha del cuadro
        t_max   = self.legend_items[4]; t_max.setPos(x+box_w-12-t_max.boundingRect().width(), y+44)


    def _draw_legend(self):
        """
        Funcion encargada de crear y dibujar la leyenda de pesos de conexion
        en la vista, usando un cuadro con gradiente de colores y etiquetas
        """
        # Limpia cualquier leyenda previa de la vista
        self._clear_legend()
        # Si la leyenda esta desactivada, no dibuja nada
        if not self.show_legend: 
            return
        # Define el margen y el tamano del cuadro de la leyenda
        pad = 12; box_w, box_h = 160, 64
        x = self.w - box_w - pad; y = pad
        # Crea el rectangulo de fondo del cuadro de la leyenda
        bg = QGraphicsRectItem(x, y, box_w, box_h)
        # Asigna un color de fondo
        bg.setBrush(QBrush(QColor(255,255,255,220)))
        # Define el borde del cuadro
        bg.setPen(QPen(QColor(0,0,0,160))); bg.setZValue(80)
        # Agrega el fondo a la vista y lo guarda en la lista de items de leyenda
        self.scene.addItem(bg); self.legend_items.append(bg)
        # Crea el rectangulo donde se dibuja el gradiente de colores
        grad_rect = QGraphicsRectItem(x+12, y+28, box_w-24, 14)
        # Define un gradiente lineal horizontal para el color
        lg = QLinearGradient(grad_rect.rect().topLeft(), grad_rect.rect().topRight())
        # Color en el extremo izquierdo (peso bajo)
        lg.setColorAt(0.0, _map_color(0.0, 1.0))
        # Color en el centro (peso medio)
        lg.setColorAt(0.5, _map_color(0.5, 1.0))
        # Color en el extremo derecho (alto peso)
        lg.setColorAt(1.0, _map_color(1.0, 1.0))
        grad_rect.setBrush(QBrush(lg))
        grad_rect.setPen(QPen(QColor(0,0,0,120))); grad_rect.setZValue(81)
        # Agrega el rectangulo de gradiente a la vista y a la lista de items de leyenda
        self.scene.addItem(grad_rect); self.legend_items.append(grad_rect)
        # Crea el texto del titulo de la leyenda
        t_title = QGraphicsSimpleTextItem("Peso de conexion")
        # Configura la fuente del titulo
        f = QFont(); f.setPointSizeF(9.0); f.setBold(True)
        t_title.setFont(f); t_title.setBrush(QBrush(Qt.black)); t_title.setZValue(82)
        # Agrega el titulo a la vista y a la lista de items de leyenda
        self.scene.addItem(t_title); self.legend_items.append(t_title)
        # Crea la etiqueta de valor bajo del gradiente
        t_min = QGraphicsSimpleTextItem("Bajo"); t_min.setFont(f); t_min.setZValue(82)
        self.scene.addItem(t_min); self.legend_items.append(t_min)
        # Crea la etiqueta de valor alto del gradiente
        t_max = QGraphicsSimpleTextItem("Alto"); t_max.setFont(f); t_max.setZValue(82)
        self.scene.addItem(t_max); self.legend_items.append(t_max)
        # Ajusta la posicion final de todos los elementos de la leyenda
        self._place_legend()

    def _toggle_legend(self):
        """
        Funcion encargada de alternar la visibilidad de la leyenda
        y redibujarla segun el nuevo estado.
        """
        # Invierte el estado de la bandera que indica si se muestra la leyenda
        self.show_legend = not self.show_legend
        # Redibuja la leyenda (la crea o la oculta segun show_legend)
        self._draw_legend()

    # Metricas
    def _clear_metrics_box(self):
        """
        Funcion encargada de borrar el recuadro de metricas de la vista
        """
        # Elimina de la vista todos los items graficos asociados a las metricas
        for it in self.metrics_items: self.scene.removeItem(it)
        # Limpia la lista interna de items de metricas
        self.metrics_items = []

    def _place_metrics_box(self):
        """
        Funcion encargada de ubicar el recuadro de metricas en 
        la esquina superior izquierda
        """
        # Si no hay items de metricas creados, no hay nada que posicionar
        if not self.metrics_items: 
            return
        # Define el margen y el tamano del recuadro de metricas
        pad = 12; box_w, box_h = 210, 54
        # Coloca el recuadro pegado al borde superior izquierdo
        x = pad; y = pad
        # Ajusta la posicion y tamano del fondo del recuadro de metricas
        bg = self.metrics_items[0]; bg.setRect(x, y, box_w, box_h)
        # Posiciona la primera linea de texto (HPWL) dentro del recuadro
        t1 = self.metrics_items[1]; t1.setPos(x+10, y+8)
        # Posiciona la segunda linea de texto (mods/pins) dentro del recuadro
        t2 = self.metrics_items[2]; t2.setPos(x+10, y+28)

    def _draw_metrics_box(self, hpwl_value: float):
        """
        Funcion encargada de crear y mostrar el recuadro de metricas
        con el valor de HPWL y el numero de modulos y pines
        """
        # Limpia cualquier recuadro de metricas dibujado previamente
        self._clear_metrics_box()
        # Si la opcion de mostrar metricas esta desactivada, no dibuja nada
        if not self.show_metrics: 
            return
        # Define el margen y el tamano del recuadro de metricas
        pad = 12; box_w, box_h = 210, 54
        # Coloca el recuadro pegado al borde superior izquierdo
        x = pad; y = pad
        # Crea el rectangulo de fondo del recuadro de metricas
        bg = QGraphicsRectItem(x, y, box_w, box_h)
        # Asigna un color de fondo
        bg.setBrush(QBrush(QColor(255,255,255,220)))
        # Define el borde del recuadro
        bg.setPen(QPen(QColor(0,0,0,160))); bg.setZValue(80)
        # Agrega el recuadro al area grafica y lo guarda en la lista de metricas
        self.scene.addItem(bg); self.metrics_items.append(bg)
        # Crea una fuente los textos de metricas
        f = QFont(); f.setPointSizeF(9.5); f.setBold(True)
        # Crea el texto con el valor de HPWL aproximado
        t1 = QGraphicsSimpleTextItem(f"HPWL ≈ {hpwl_value:.1f}")
        # Aplica la fuente al texto de HPWL y lo coloca por encima del fondo
        t1.setFont(f); t1.setZValue(82); self.scene.addItem(t1); self.metrics_items.append(t1)
        # Crea el texto con la cantidad de modulos y pines presentes
        t2 = QGraphicsSimpleTextItem(f"Mods={len(self.items)}  |  Pins={len(self.pin_items)}")
        # Aplica la misma fuente al texto de conteo y lo coloca por encima del fondo
        t2.setFont(f); t2.setZValue(82); self.scene.addItem(t2); self.metrics_items.append(t2)
        # Ajusta la posicion exacta del recuadro y de los textos dentro de el
        self._place_metrics_box()

    def _toggle_metrics(self):
        """
        Funcion encargada de alternar la visibilidad del recuadro de metricas
        y actualizarlo con el HPWL actual si esta activado
        """
        # Invierte el estado de la bandera que indica si se muestran metricas
        self.show_metrics = not self.show_metrics
        # Inicializa el acumulador de HPWL
        total = 0.0
        # Suma el HPWL de las primeras 500 redes
        for net in self.nets[:500]:
            total += self._hpwl_net(net.get("endpoints", []))
        # Redibuja el recuadro de metricas con el nuevo valor total de HPWL
        self._draw_metrics_box(total)

    
    # Post-Move
    def _post_move(self):
        """
        Funcion encargada de actualizar las conexiones y metricas
        despues de mover un modulo o un pin en la interfaz
        """
        # Si la GUI todavia se esta construyendo, no hace nada
        if getattr(self, "_building", False): 
            return
        # Redibuja las aristas segun las nuevas posiciones
        self._redraw_edges()
        # Recalcula HPWL y actualiza la barra de estado/metricas
        self._update_hpwl()

    # Guardar
    def _on_save_clicked(self):
        """
        Funcion encargada de guardar en el archivo design.json
        las posiciones y tamanos actuales de modulos y pines.
        """
        # Lee el contenido actual de design.json
        data = json.loads(DESIGN.read_text())
        # Crea un mapa de instancias a sus diccionarios de modulo en memoria
        mods_map = {m["inst"]: m for m in self.modules}
        # Itera sobre los modulos almacenados en el JSON original
        for m in data.get("modules", []):
            # Solo actualiza los modulos que existen en mods_map
            if m["inst"] in mods_map:
                # Obtiene la version actual del modulo
                cur = mods_map[m["inst"]]
                # Actualiza posicion X en el JSON usando el valor actual o el existente
                m["x"] = int(cur.get("x", m.get("x", 0)))
                # Actualiza posicion Y en el JSON
                m["y"] = int(cur.get("y", m.get("y", 0)))
                # Actualiza ancho en el JSON
                m["w"] = int(cur.get("w", m.get("w", 0)))
                # Actualiza alto en el JSON
                m["h"] = int(cur.get("h", m.get("h", 0)))
                # Si hay orientacion en el modulo actual, la copia al JSON
                if "orient" in cur: m["orient"] = cur.get("orient", m.get("orient","N"))
        # Crea un diccionario nombre_de_puerto objeto PinItem
        name_to_pinitem = {n: it for n, it in self.pin_items.items()}
        # Itera sobre los puertos guardados en el JSON
        for p in data.get("ports", []):
            nm = p["name"]; it = name_to_pinitem.get(nm)
            # Si el puerto existe en la GUI, actualiza su posicion y lado
            if it:
                # Guarda la posicion X actual del pin
                p["x"] = int(it.pos().x())
                # Guarda la posicion Y actual del pin
                p["y"] = int(it.pos().y())
                # Guarda el lado actual donde se ubica el pin (N, S, E, W)
                p["side"] = it.pin.get("side", "N")
        # Escribe de vuelta el JSON actualizado a disco con indentacion
        DESIGN.write_text(json.dumps(data, indent=2))
        # Muestra un mensaje informando que el guardado fue exitoso
        QMessageBox.information(self, "Guardado", f"Guardado en:\n{DESIGN}")

    # Exportacion a OpenLane
    def _gui_to_ll(self, x_gui: int, y_gui: int, h_box: int = 0):
        """
        Funcion encargada de convertir coordenadas de la GUI 
        a coordenadas tipo lower-left en micrometros para exportar 
        a OpenLane
        """
        # Si se especifica un alto, se ajusta Y para tomar la base del rectangulo
        if h_box:
            x_ll = x_gui
            y_ll = self.h - (y_gui + h_box)
        else:
            # Si no hay alto, se invierte Y directamente respecto a la altura total
            x_ll = x_gui
            y_ll = self.h - y_gui
        # Se redondean y escalan las coordenadas de pixeles a micrometros
        return int(round(x_ll * PX_TO_UM)), int(round(y_ll * PX_TO_UM))

    def _infer_pin_side(self, it: PinItem) -> str:
        """
        Funcion encargada de inferir en que lado del core (N, S, E o W)
        esta ubicado un pin, segun su posicion actual
        """
        # Obtiene la posicion del pin en la vista
        pos = it.pos()
        x = pos.x()
        y = pos.y()
        # Obtiene limites verticales del core
        top, bottom = self.core_rect.top(), self.core_rect.bottom()
        # Obtiene limites horizontales del core
        left, right = self.core_rect.left(), self.core_rect.right()
        # Calcula el centro del rectangulo del pin en X
        cx = x + it.rect().width() / 2.0
        # Calcula el centro del rectangulo del pin en Y
        cy = y + it.rect().height() / 2.0
        # Calcula distancia del centro del pin a cada lado del core
        d = {
            'N': abs(cy - top),
            'S': abs(cy - bottom),
            'W': abs(cx - left),
            'E': abs(cx - right),
        }
        # Devuelve el lado con menor distancia al centro del pin
        return min(d.items(), key=lambda t: t[1])[0]

    def _pins_grouped_sorted(self):
        """
        Funcion encargada de agrupar los pines por lado del core
        y ordenarlos a lo largo de cada lado segun su posicion
        """
        # Inicializa grupos de pines por lado del core
        groups = {'N': [], 'S': [], 'E': [], 'W': []}
        # Recorre todos los pines graficos disponibles
        for name, it in self.pin_items.items():
            # Determina el lado del core donde se ubica el pin
            side = self._infer_pin_side(it)
            # Obtiene la posicion del pin en pixeles
            pos = it.pos()
            # Convierte la posicion a coordenadas lower-left en micrometros
            x_ll, y_ll = self._gui_to_ll(int(pos.x()), int(pos.y()))
            if side in ('N', 'S'):
                # Para N y S se usa la coordenada X relativa al borde izquierdo del core
                key_um = x_ll - int(round(self.core_rect.left() * PX_TO_UM))
            else:
                # Para E y W se usa la coordenada Y relativa al borde inferior del core
                key_um = y_ll - int(round((self.h - self.core_rect.bottom()) * PX_TO_UM))
            # Agrega el pin al grupo del lado correspondiente con su coordenada relativa
            groups[side].append((name, int(key_um)))
        # Ordena los pines de cada lado por su coordenada a lo largo del lado
        for k in groups:
            groups[k].sort(key=lambda t: t[1])
        # Devuelve el diccionario de pines agrupados y ordenados
        return groups
   
    def _write_pin_placement_cfg(self, outpath: Path):
        """
        Funcion encargada de generar el archivo pin_placement.cfg
        con la posicion de cada bit de pin a lo largo de los lados del core
        """
        # Calcula la coordenada izquierda del core en micrometros
        core_left_um  = int(round(self.core_rect.left()  * PX_TO_UM))
        # Calcula la coordenada derecha del core en micrometros
        core_right_um = int(round(self.core_rect.right() * PX_TO_UM))
        # Calcula la coordenada superior del core en micrometros (sistema lower-left)
        core_top_um   = int(round((self.h - self.core_rect.top())    * PX_TO_UM))
        # Calcula la coordenada inferior del core en micrometros (sistema lower-left)
        core_bot_um   = int(round((self.h - self.core_rect.bottom()) * PX_TO_UM))
        # Paso entre pines en micrometros segun el grid de la GUI
        step_um = max(int(round(self.grid * PX_TO_UM)), 1)

        # Obtiene los pines agrupados por lado y ordenados a lo largo de cada lado
        grouped = self._pins_grouped_sorted()
        # Lista donde se acumularan las lineas del archivo pin_placement.cfg
        lines = []
        # Recorre cada lado del core
        for side in ('N', 'S', 'E', 'W'):
            if side in ('N', 'S'):
                # Para N y S, el desplazamiento es a lo largo del ancho del core
                min_off = 0
                max_off = int(round((core_right_um - core_left_um)))
            else:
                # Para E y W, el desplazamiento es a lo largo de la altura del core
                min_off = 0
                max_off = int(round((core_top_um - core_bot_um)))

            # Inicializa cursor muy bajo para controlar separacion minima entre pines
            cursor = -10**9
            # Recorre cada pin agrupado en este lado
            for name, base in grouped[side]:
                # Busca el puerto correspondiente para conocer su ancho
                port = next((p for p in self.ports if p["name"] == name), {"width": 1})
                width = int(port.get("width", 1))
                # Calcula el ancho total que ocuparan todos los bits del bus
                total_span = step_um * (width - 1)
                # Calcula la posicion ideal inicial centrada alrededor del die
                ideal_start = int(round(base - total_span / 2))
                # Limite inferior de inicio
                lo = max(min_off, cursor + step_um)
                # Limite superior de inicio
                hi = max(min_off, max_off - total_span)
                # Ajusta el inicio real entre los limites permitidos
                start = max(lo, min(ideal_start, hi))
                # Recorre cada bit individual del bus expandido
                for i, bit_name in enumerate(_expand_bus(name, width)):
                    # Calcula el offset de este bit a partir del inicio
                    off = start + i * step_um
                    # Recorta el offset para que se mantenga en el rango [min_off, max_off]
                    off = max(min_off, min(off, max_off))
                    # Si por alguna razon queda por detras del cursor, lo adelanta al menos un paso
                    if off <= cursor:
                        off = min(cursor + step_um, max_off)
                    # Agrega una linea con el nombre del bit, lado y offset en micrometros
                    lines.append(f"{bit_name} {side} {off}")
                    # Actualiza el cursor a la ultima posicion usada
                    cursor = off
        # Escribe todas las lineas al archivo de salida con salto de linea final
        outpath.write_text("\n".join(lines) + "\n")

    def _on_export_clicked(self):
        """
        Funcion encargada de preparar los archivos de salida para OpenLane
        a partir del estado actual del floorplan en la GUI
        """
        # Define la carpeta de salida donde se guardaran los archivos de OpenLane
        outdir = ROOT / "artifacts" / "openlane_export"
        # Crea la carpeta (y sus padres) si no existen
        outdir.mkdir(parents=True, exist_ok=True)

        # Archivos auxiliares
        # Bandera para indicar si se escribio el archivo macro.cfg
        wrote_macro = False

        # macro.cfg (solo si hay modulos duros)
        # Verifica si hay modulos en la lista para exportar su posicion
        if len(self.modules) > 0:
            # Lista donde se construiran las lineas de macro.cfg
            macro_lines = []
            # Recorre cada modulo definido en el diseño
            for m in self.modules:
                # Convierte la posicion del modulo de coordenadas de GUI a coordenadas en micrometros
                x_ll_um, y_ll_um = self._gui_to_ll(int(m["x"]), int(m["y"]), int(m["h"]))
                # Obtiene la orientacion del modulo, por defecto "N"
                orient = m.get("orient", "N")
                # Agrega una linea con instancia, posicion y orientacion
                macro_lines.append(f'{m["inst"]} {x_ll_um} {y_ll_um} {orient}')
            # Escribe el archivo macro.cfg con todas las lineas generadas
            (outdir / "macro.cfg").write_text("\n".join(macro_lines) + "\n")
            # Marca que el archivo macro.cfg fue generado
            wrote_macro = True

        # pin_placement.cfg (desde la GUI; offsets a lo largo del core)
        # Calcula el numero total de bits de todos los puertos del diseno
        total_pin_bits = sum(int(p.get("width", 1)) for p in self.ports)
        # Solo genera pin_placement.cfg si existe al menos un bit de pin
        if total_pin_bits > 0:
            # Escribe el archivo pin_placement.cfg con la posicion de cada bit de pin
            self._write_pin_placement_cfg(outdir / "pin_placement.cfg")
            # Chequeo simple
            # Lee las lineas generadas en pin_placement.cfg
            pp_lines = (outdir / "pin_placement.cfg").read_text().strip().splitlines()
            # Verifica que la cantidad de lineas coincida con el numero esperado de bits
            if len(pp_lines) != total_pin_bits:
                # Muestra una advertencia si hay diferencia entre bits exportados y ancho total
                QMessageBox.warning(self, "Advertencia",
                    f"Pin bits exportados ({len(pp_lines)}) != suma de anchos ({total_pin_bits}). "
                    "Revisa nombres/ancho de puertos.")

        #  Geometria (µm; 1 px = 1)
        # Coordenadas inferiores del die en micrometros
        die_x0_um, die_y0_um = 0, 0
        # Coordenadas superiores del die en micrometros
        die_x1_um, die_y1_um = int(round(self.w * PX_TO_UM)), int(round(self.h * PX_TO_UM))
        # Coordenadas inferiores del core en micrometros
        core_x0_um = int(round(self.margin * PX_TO_UM))
        core_y0_um = int(round(self.margin * PX_TO_UM))
        # Coordenadas superiores del core en micrometros
        core_x1_um = int(round((self.w - self.margin) * PX_TO_UM))
        core_y1_um = int(round((self.h - self.margin) * PX_TO_UM))

        # TOP
        # Define el nombre del modulo top que usara OpenLane
        top = self.topname 


        # config.tcl
        # Inicializa la lista de lineas que formaran el archivo config.tcl
        cfg = []
        # Agrega un encabezado descriptivo al archivo de configuracion
        cfg.append("# ===== OpenLane config (auto-generado desde la GUI) =====")
        # Define el nombre del diseno para OpenLane
        cfg.append(f'set ::env(DESIGN_NAME) "{top}"')
        # Define el modulo top que usara Yosys en la sintesis
        cfg.append(f'set ::env(SYNTH_TOP)   "{top}"')
        # Define el modulo top para Verilator
        cfg.append(f'set ::env(VERILATOR_TOP) "{top}"')
        # Define el modulo top para el linter
        cfg.append(f'set ::env(LINTER_TOP)    "{top}"')
        # Agrega una linea en blanco para separar bloques
        cfg.append("")
        # Seccion de archivos RTL
        cfg.append("# RTL")
        # Indica que OpenLane debe tomar todos los .v del directorio src
        cfg.append('set ::env(VERILOG_FILES) [glob -nocomplain $::env(DESIGN_DIR)/src/*.v]')
        # Agrega una linea en blanco para separar bloques
        cfg.append("")
        # Seccion de geometria del die y el core
        cfg.append("# Geometria ")
        # Indica que se usara un tamano absoluto para el die
        cfg.append('set ::env(FP_SIZING) "absolute"')
        # Define el rectangulo del die en micrometros
        cfg.append(f'set ::env(DIE_AREA)  "{die_x0_um} {die_y0_um} {die_x1_um} {die_y1_um}"')
        # Define el rectangulo del core en micrometros
        cfg.append(f'set ::env(CORE_AREA) "{core_x0_um} {core_y0_um} {core_x1_um} {core_y1_um}"')
        # Agrega una linea en blanco para separar bloques
        cfg.append("")
        # Seccion de configuracion de pines de IO
        cfg.append("# Pines: usa SOLO el placement explicito exportado por la GUI")
        # Fija los pines de IO segun la posicion indicada en los archivos de configuracion
        cfg.append("set ::env(PL_FIXED_IO) 1")
        # Habilita el placement explicito de pines
        cfg.append("set ::env(FP_IO_PLACEMENT)      1")
        # Si hay bits de pines, referencia el archivo pin_placement.cfg
        if total_pin_bits > 0:
            # Indica el archivo con el placement de cada bit de pin
            cfg.append('set ::env(FP_PIN_PLACEMENT_CFG) "$::env(DESIGN_DIR)/pin_placement.cfg"')
            # No usar orden por lados
        else:
            # Comentario que explica el comportamiento si no se exportaron pines
            cfg.append("# (No se exportaron pines: si dejas esto en 1, ioPlacer los ubicara automaticamente.)")
        # Agrega una linea en blanco para separar bloques
        cfg.append("")
        # Seccion de capas de IO
        cfg.append("# Capas IO (evita warnings por variables deprecadas)")
        # Define la capa horizontal de IO
        cfg.append('set ::env(FP_IO_HLAYER) {met1}')
        # Define la capa vertical de IO
        cfg.append('set ::env(FP_IO_VLAYER) {met2}')
        # Agrega una linea en blanco para separar bloques
        cfg.append("")
        # Configuracion relacionada con macros duros
        if wrote_macro:
            # Comentario indicando que se usa archivo de placement de macros
            cfg.append("# Macros duros (si NO tienes macros, comenta la linea siguiente)")
            # Indica el archivo macro.cfg con la posicion de cada macro
            cfg.append('set ::env(MACRO_PLACEMENT_CFG) "$::env(DESIGN_DIR)/macro.cfg"')
            # Agrega una linea en blanco para separar bloques
            cfg.append("")
        else:
            # Comentario para el caso en que no haya macros en el diseno
            cfg.append("# Sin macros duros")
            # Linea comentada que muestra como seria la variable en caso de usar macros
            cfg.append('# set ::env(MACRO_PLACEMENT_CFG) "$::env(DESIGN_DIR)/macro.cfg"')
            # Agrega una linea en blanco para separar bloques
            cfg.append("")
        # Seccion de configuracion de reloj por defecto
        cfg.append("# Reloj por defecto")
        # Si no existe CLOCK_PORT, lo define con el nombre "clk"
        cfg.append('if {![info exists ::env(CLOCK_PORT)]}   { set ::env(CLOCK_PORT)   "clk" }')
        # Si no existe CLOCK_PERIOD, lo define con 20 ns
        cfg.append('if {![info exists ::env(CLOCK_PERIOD)]} { set ::env(CLOCK_PERIOD) "20.0" }')
        # Agrega una linea en blanco final
        cfg.append("")

        # Escribe todas las lineas en el archivo config.tcl en el directorio de salida
        (outdir / "config.tcl").write_text("\n".join(cfg))

        # Inicializa la lista de nombres de archivos generados
        produced = ["config.tcl"]
        # Si se genero macro.cfg, lo agrega a la lista
        if wrote_macro: produced.append("macro.cfg")
        # Si se genero pin_placement.cfg, lo agrega a la lista
        if total_pin_bits > 0: produced.append("pin_placement.cfg")
        # Muestra un cuadro de dialogo informando la ruta y archivos exportados
        QMessageBox.information(self, "Exportacion",
            "Exportado a:\n"
            f"{outdir}\n"
            f"Archivos: {', '.join(produced)}")

# Main
if __name__ == "__main__":
    import sys
    # Crea la aplicacion Qt y le pasa los argumentos de linea de comandos
    app = QApplication(sys.argv)
    # Crea la ventana principal de la herramienta de floorplan
    win = Main()
    # Muestra la ventana en pantalla
    win.show()
    sys.exit(app.exec())
