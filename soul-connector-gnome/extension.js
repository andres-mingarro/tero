// El soul-connector de Tero como extensión de GNOME Shell.
//
// Por qué existe: el soul-connector original (soul_connector/, pywebview +
// QtWebEngine) se
// lleva ~1,3 GB de RAM para dibujar una onda, porque levanta un Chromium
// entero. Acá el dibujo corre adentro de gnome-shell, que ya está en
// memoria, así que el costo extra es esencialmente el de los dos senos
// que se calculan por frame.
//
// De yapa resuelve un problema viejo: el "siempre encima" nunca funcionó
// bien bajo Mutter (ver CLAUDE.md), y el soul-connector de pywebview lo peleaba
// llamando a wmctrl en un bucle mientras Tero hablaba. Siendo parte del
// shell no hay nada que pelear: el actor vive en la capa de chrome, por
// encima de las ventanas, siempre.
//
// No toca el daemon: es otro cliente más del WebSocket de niveles.

import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {Onda, COLORES_ORIGINALES, lineaBase} from './onda.js';
import {Barra} from './barra.js';
import {Particulas} from './particulas.js';
import {Arrastre, leerPosicion} from './mover.js';
import {Enlace} from './enlace.js';
import {TeroIndicator} from './panel.js';

const ANCHO = 260;
const ALTO = 74;
const MARGEN = 20;

// D-Bus para que herramientas/mover_ventana_monitor.py pueda mover
// cualquier ventana (X11 o Wayland nativa) sin pasar por wmctrl, que solo
// puede tocar ventanas X11/XWayland desde afuera del compositor. Acá
// adentro Meta.Window.move_to_monitor() no tiene esa limitación -- ver
// [[proyecto-mover-ventana-monitor]] en la memoria del proyecto, validado
// en vivo en el shell anidado el 2026-09-15 antes de sumarlo acá.
const _IFAZ_VENTANAS = `
<node>
  <interface name="org.gnome.Shell.Extensions.Tero">
    <method name="ListarVentanas">
      <arg type="s" direction="out" name="json" />
    </method>
    <method name="MoverVentanaAMonitor">
      <arg type="s" direction="in" name="app" />
      <arg type="i" direction="in" name="monitor" />
      <arg type="s" direction="out" name="resultado" />
    </method>
  </interface>
</node>`;

// La barra en sí es de 1px, pero el glow y el puntito de la punta se
// salen bastante: sin este alto el dibujo queda recortado.
const ALTO_BARRA = 20;

// Medido con el DOM sobre soul_connector/index.html, que es el original: en un
// cuadro de 260x74 los tiempos ocupan y 23,5-33,5, la línea de la barra
// va en y=34 (o sea apoyada sobre el eje de la onda, por eso se ven como
// una sola línea) y el nombre de la canción en y 39-51.
const Y_CANCION = 39;

const FPS = 60;
const FPS_IDLE = 30; // en reposo la onda apenas respira: no hace falta más

// Mismos colores y constantes que soul_connector/index.html, para que el look no
// cambie al migrar.
const COLORES = {
    idle: [0x4a, 0x44, 0x58],
    escuchando: [0xff, 0xff, 0xff],
    pensando: [0xc7, 0x7d, 0xff],
    hablando: null, // multicolor original de la librería
    musica: [0x5c, 0xff, 0xd4],
    // Cuando Tero delega en Codex/ChatGPT (fase 4): la onda queda cian y
    // suben partículas de colores desde su eje, como pidió el usuario
    // ("que dé la idea de asteroides") -- ver particulas.js.
    codex: [0x00, 0xea, 0xff],
};

const AMPLITUD_IDLE = 0.15;
const AMPLITUD_PENSANDO = 0.45;
const AMPLITUD_CODEX = 0.55;

// Suavizado asimétrico: ataque rápido, decaimiento más lento. El RMS crudo
// tiembla, y esto es lo que separa "se ve pro" de "se ve amateur".
const ATAQUE = 0.7;
const DECAIMIENTO = 0.25;

const SEGUIR_NIVEL = ['hablando', 'musica', 'escuchando'];
const OCULTAR_PAUSADO_MS = 30000;

function formatearTiempo(ms) {
    const totalSeg = Math.max(0, Math.floor(ms / 1000));
    const min = Math.floor(totalSeg / 60);
    const seg = totalSeg % 60;
    return `${min}:${seg.toString().padStart(2, '0')}`;
}

class SoulConnector {
    constructor() {
        this._estado = 'idle';
        this._nivelCrudo = 0;
        this._nivelSuave = AMPLITUD_IDLE;
        this._velocidad = 0.06;
        this._onda = new Onda();
        this._barra = new Barra();
        this._particulas = new Particulas();

        this._cancion = null;
        this._fraccion = 0;
        this._progresoBase = 0;
        this._duracionMs = 0;
        this._recibidoEn = 0;
        this._pausadoDesde = 0;

        // null = todavía no se sabe (recién habilitada): así no aparece un
        // "Tero apagado" fugaz mientras se hace el primer intento.
        this._conectado = null;
        this._carga = null;

        this._construirActores();
        this._idAnimacion = 0;
        this._fpsActual = 0;
        this._animar(FPS_IDLE);

        this._enlace = new Enlace(m => this._alRecibir(m), c => this._alCambiarConexion(c));
        this._enlace.conectar();
    }

    _construirActores() {
        this._raiz = new St.Widget({
            width: ANCHO,
            height: ALTO,
            reactive: false,
            layout_manager: new Clutter.FixedLayout(),
        });

        this._area = new St.DrawingArea({width: ANCHO, height: ALTO});
        this._area.connect('repaint', a => this._pintar(a));
        this._raiz.add_child(this._area);

        // Dos capas para las partículas del estado "codex", no una: el
        // halo sale de aplicarle un blur real (GPU, Shell.BlurEffect --
        // lo mismo que usa gnome-shell para el fondo del overview) a
        // TODA esta capa, y el núcleo nítido va aparte, sin blur, encima
        // -- igual que hace `box-shadow` en CSS (sombra desenfocada
        // detrás, elemento nítido delante). Iba a aproximar el blur a
        // mano con Cairo (gradiente, anillos) y las dos veces se veía a
        // esfera con sombreado -- ver particulas.js.
        this._areaGlow = new St.DrawingArea({width: ANCHO, height: ALTO});
        this._areaGlow.connect('repaint', a => this._pintarParticulasGlow(a));
        // Radio más chico que el primer intento (10): un blur ancho sobre
        // un puntito de 2-3px diluye demasiado el brillo (probado en
        // vivo, casi no se veía). Con esto el halo queda más concentrado.
        this._efectoBlur = new Shell.BlurEffect({
            radius: 6, brightness: 1.0, mode: Shell.BlurMode.ACTOR,
        });
        this._areaGlow.add_effect(this._efectoBlur);
        this._raiz.add_child(this._areaGlow);

        this._areaParticulasNucleo = new St.DrawingArea({width: ANCHO, height: ALTO});
        this._areaParticulasNucleo.connect('repaint', a => this._pintarParticulasNucleo(a));
        this._raiz.add_child(this._areaParticulasNucleo);

        this._etiquetaCancion = new St.Label({
            style_class: 'tero-soul-connector-cancion',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._etiquetaCancion.clutter_text.set_line_wrap(false);
        this._etiquetaCancion.clutter_text.set_ellipsize(3 /* END */);
        this._etiquetaCancion.set_position(10, Y_CANCION);
        this._etiquetaCancion.set_width(ANCHO - 20);
        this._etiquetaCancion.opacity = 0;
        this._raiz.add_child(this._etiquetaCancion);

        // Mismo lugar que el nombre de la canción: mientras Tero arranca o
        // está apagado no hay canción que mostrar, así que no compiten.
        this._etiquetaAviso = new St.Label({
            style_class: 'tero-soul-connector-aviso',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._etiquetaAviso.clutter_text.set_ellipsize(3 /* END */);
        this._etiquetaAviso.set_position(10, Y_CANCION);
        this._etiquetaAviso.set_width(ANCHO - 20);
        this._etiquetaAviso.opacity = 0;
        this._raiz.add_child(this._etiquetaAviso);

        this._filaProgreso = new St.BoxLayout({
            style_class: 'tero-soul-connector-progreso',
            width: ANCHO - 20,
        });
        // La fila se centra sobre el eje de la onda, de modo que la línea
        // de la barra caiga exactamente encima del hilo que dibuja la
        // onda y las dos se lean como una sola. Los tiempos quedan
        // apoyados justo arriba de esa línea, como en el original.
        this._filaProgreso.set_position(
            10, Math.round(lineaBase(ALTO) - ALTO_BARRA / 2));
        this._filaProgreso.opacity = 0;

        this._tiempoActual = new St.Label({style_class: 'tero-soul-connector-tiempo'});
        this._pista = new St.DrawingArea({
            height: ALTO_BARRA,
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._pista.connect('repaint', a => this._pintarBarra(a));
        this._tiempoTotal = new St.Label({style_class: 'tero-soul-connector-tiempo'});

        this._filaProgreso.add_child(this._tiempoActual);
        this._filaProgreso.add_child(this._pista);
        this._filaProgreso.add_child(this._tiempoTotal);
        this._raiz.add_child(this._filaProgreso);
    }

    get actor() {
        return this._raiz;
    }

    _colores() {
        const color = COLORES[this._estado];
        if (color === null || color === undefined)
            return COLORES_ORIGINALES;
        return [color, color, color];
    }

    _pintar(area) {
        const cr = area.get_context();
        const [ancho, alto] = area.get_surface_size();
        this._onda.dibujar(cr, ancho, alto, this._nivelSuave, this._velocidad,
            this._colores());
        cr.$dispose();
    }

    _pintarParticulasGlow(area) {
        const cr = area.get_context();
        const [, alto] = area.get_surface_size();
        this._particulas.dibujarGlow(cr, lineaBase(alto));
        cr.$dispose();
    }

    _pintarParticulasNucleo(area) {
        const cr = area.get_context();
        const [, alto] = area.get_surface_size();
        this._particulas.dibujarNucleos(cr, lineaBase(alto));
        cr.$dispose();
    }

    _pintarBarra(area) {
        const cr = area.get_context();
        const [ancho, alto] = area.get_surface_size();
        const pausado = !this._cancion || !this._cancion.reproduciendo;
        // La fila está centrada sobre el eje de la onda, así que el
        // centro de esta área ya *es* la altura de la línea.
        this._barra.dibujar(cr, ancho, alto / 2, this._fraccion, pausado);
        cr.$dispose();
    }

    _amplitudObjetivo() {
        if (SEGUIR_NIVEL.includes(this._estado))
            return this._nivelCrudo;
        if (this._estado === 'pensando')
            return AMPLITUD_PENSANDO;
        if (this._estado === 'codex')
            return AMPLITUD_CODEX;
        return AMPLITUD_IDLE;
    }

    _animar(fps) {
        if (this._idAnimacion)
            GLib.Source.remove(this._idAnimacion);
        this._fpsActual = fps;
        this._idAnimacion = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, Math.round(1000 / fps), () => this._frame());
    }

    _frame() {
        const objetivo = this._amplitudObjetivo();
        const tasa = objetivo > this._nivelSuave ? ATAQUE : DECAIMIENTO;
        this._nivelSuave += (objetivo - this._nivelSuave) * tasa;

        // La velocidad también sigue al nivel: una onda que se mueve
        // siempre igual sin importar qué tan fuerte suena la voz es
        // justo lo que se ve desacoplado del audio.
        if (SEGUIR_NIVEL.includes(this._estado))
            this._velocidad = 0.04 + this._nivelSuave * 0.18;
        else if (this._estado === 'pensando')
            this._velocidad = 0.12;
        else if (this._estado === 'codex')
            this._velocidad = 0.16; // más viva que "pensando": es el estado "energizado"
        else
            this._velocidad = 0.06;

        this._area.queue_repaint();
        // Una sola vez acá, no adentro de cada _pintarParticulas*: las dos
        // capas tienen que pintar la MISMA lista de partículas en el
        // mismo estado, no cada una la suya (ver particulas.js).
        this._particulas.actualizar(this._estado === 'codex', ANCHO);
        this._areaGlow.queue_repaint();
        this._areaParticulasNucleo.queue_repaint();
        this._actualizarProgreso();
        return GLib.SOURCE_CONTINUE;
    }

    _alCambiarConexion(conectado) {
        this._conectado = conectado;
        if (!conectado) {
            // Lo último que mandó el daemon ya no vale: sin esto la onda
            // quedaba congelada en "hablando" o con la canción de antes.
            this._carga = null;
            this._nivelCrudo = 0;
            this._aplicarEstado('idle');
            this._aplicarCancion(null);
        }
        this._actualizarAviso();
    }

    _actualizarAviso() {
        let texto = null;
        if (this._conectado === false)
            texto = 'Tero apagado';
        else if (this._carga)
            texto = `${this._carga.texto} · ${Math.round(this._carga.progreso * 100)}%`;

        if (texto) {
            this._etiquetaAviso.text = texto;
            this._etiquetaAviso.ease({opacity: 255, duration: 400});
        } else {
            this._etiquetaAviso.ease({opacity: 0, duration: 400});
        }
    }

    _alRecibir(mensaje) {
        if (mensaje.carga !== undefined) {
            this._carga = mensaje.carga;
            this._actualizarAviso();
        }
        if (mensaje.estado !== undefined)
            this._aplicarEstado(mensaje.estado);
        if (mensaje.nivel !== undefined)
            this._nivelCrudo = mensaje.nivel;
        if (mensaje.cancion !== undefined)
            this._aplicarCancion(mensaje.cancion);
    }

    _aplicarEstado(nuevo) {
        this._estado = nuevo;
        // A 30 fps el reposo se ve igual y le ahorra la mitad de los
        // frames a gnome-shell, que es el proceso que dibuja todo el
        // escritorio -- acá el costo no lo paga un proceso aparte.
        const fps = nuevo === 'idle' ? FPS_IDLE : FPS;
        if (fps !== this._fpsActual)
            this._animar(fps);
    }

    _aplicarCancion(info) {
        this._cancion = info;
        if (!info) {
            this._etiquetaCancion.ease({opacity: 0, duration: 600});
            this._filaProgreso.ease({opacity: 0, duration: 600});
            return;
        }
        this._etiquetaCancion.text = info.texto;
        this._progresoBase = info.progreso_ms;
        this._duracionMs = info.duracion_ms;
        this._recibidoEn = GLib.get_monotonic_time() / 1000;
        this._etiquetaCancion.ease({opacity: 180, duration: 600});
        // Sin duración real (un canal de YouTube en vivo, no una canción
        // con punta y final) se oculta la fila de tiempo/barra -- mostrarla
        // fija en 0:00/0:00 se ve roto, no "en vivo".
        this._filaProgreso.ease({opacity: info.duracion_ms ? 255 : 0, duration: 600});
    }

    _actualizarProgreso() {
        if (!this._cancion || !this._duracionMs)
            return;
        const pausado = !this._cancion.reproduciendo;
        const transcurrido = pausado
            ? 0
            : GLib.get_monotonic_time() / 1000 - this._recibidoEn;
        const progreso = Math.min(this._duracionMs, this._progresoBase + transcurrido);
        this._tiempoActual.text = formatearTiempo(progreso);
        this._tiempoTotal.text = formatearTiempo(this._duracionMs);
        this._fraccion = progreso / this._duracionMs;
        // Siempre, no solo cuando cambia la fracción: el glow cicla color,
        // el brillo respira y en pausa el puntito late. Todo eso se mueve
        // aunque la canción esté clavada en el mismo segundo.
        this._pista.queue_repaint();
    }

    destruir() {
        if (this._idAnimacion) {
            GLib.Source.remove(this._idAnimacion);
            this._idAnimacion = 0;
        }
        this._enlace.destruir();
        this._raiz.destroy();
    }
}

export default class SoulConnectorExtension extends Extension {
    enable() {
        this._soulConnector = new SoulConnector();
        // addChrome (y no un actor suelto en uiGroup) es lo que lo pone en
        // la capa de chrome: por encima de las ventanas y bien tratado al
        // entrar y salir de pantalla completa.
        //
        // Ojo: el viejo parámetro `affectsInputRegion: false` ya no existe
        // en GNOME 50 (tira "Unrecognized parameter" y la extensión no
        // carga). Que los clics pasen de largo ahora sale de que el actor
        // es `reactive: false`, que es como se construye en SoulConnector.
        Main.layoutManager.addChrome(this._soulConnector.actor, {
            trackFullscreen: true,
        });
        this._posicion = leerPosicion();
        this._ubicar();
        this._arrastre = new Arrastre(this._soulConnector.actor, (x, y) => {
            this._posicion = {x, y};
        });
        this._idMonitores = Main.layoutManager.connect(
            'monitors-changed', () => this._ubicar());
        // Y este es el que importa de verdad. En el login la extensión
        // puede habilitarse ANTES de que el dock reserve su franja: ahí
        // el work area todavía es el monitor entero, el soul-connector se
        // ubica abajo de todo y queda tapado para siempre, porque
        // `monitors-changed` no se dispara por eso. `workareas-changed`
        // sí, así que el soul-connector se reacomoda solo cuando el dock aparece,
        // se va, cambia de lado o cambia de tamaño.
        this._idAreas = global.display.connect(
            'workareas-changed', () => this._ubicar());

        this._panel = new TeroIndicator();

        this._dbusVentanas = Gio.DBusExportedObject.wrapJSObject(_IFAZ_VENTANAS, this);
        this._dbusVentanas.export(
            Gio.DBus.session, '/org/gnome/Shell/Extensions/Tero');
    }

    // wm_class primero: el título cambia con cada pestaña/documento
    // abierto (mismo motivo por el que _pantalla_youtube.py identifica su
    // ventana por PID y no por título). Si no matchea nada por wm_class
    // se cae a buscar en el título, para apps sin wm_class útil.
    _buscarVentana(app) {
        const buscado = app.toLowerCase();
        const ventanas = global.get_window_actors().map(w => w.meta_window);
        return ventanas.find(w => (w.get_wm_class() || '').toLowerCase().includes(buscado))
            || ventanas.find(w => (w.get_title() || '').toLowerCase().includes(buscado));
    }

    ListarVentanas() {
        const ventanas = global.get_window_actors().map(w => ({
            titulo: w.meta_window.get_title(),
            wm_class: w.meta_window.get_wm_class(),
            monitor: w.meta_window.get_monitor(),
        }));
        return JSON.stringify(ventanas);
    }

    MoverVentanaAMonitor(app, monitor) {
        const cantidad = Main.layoutManager.monitors.length;
        if (monitor < 0 || monitor >= cantidad) {
            return JSON.stringify({
                ok: false, error: `no hay monitor ${monitor} (hay ${cantidad})`,
            });
        }
        const win = this._buscarVentana(app);
        if (!win)
            return JSON.stringify({ok: false, error: 'ventana no encontrada'});

        win.move_to_monitor(monitor);
        return JSON.stringify({
            ok: true, titulo: win.get_title(), monitor,
        });
    }

    _ubicar() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._soulConnector)
            return;
        // Nunca el rectángulo crudo del monitor: si hay un dock (Ubuntu
        // dock, dash-to-dock) reservando franja, el rectángulo del work
        // area ya viene descontado -- así el soul-connector queda arriba del dock
        // en vez de superpuesta y tapada por él (pasó de verdad: con
        // dock abajo, el nombre de la canción quedaba atrás del dock).
        const area = Main.layoutManager.getWorkAreaForMonitor(monitor.index);
        const base = area || monitor;

        // Si el usuario lo movió a mano, mandar esa posición -- pero
        // igual recortada al work area, para que un dock que aparece o un
        // monitor que se desconecta no la dejen fuera de la pantalla, sin
        // forma de agarrarla para traerla de vuelta.
        if (this._posicion) {
            this._soulConnector.actor.set_position(
                Math.max(base.x, Math.min(this._posicion.x, base.x + base.width - ANCHO)),
                Math.max(base.y, Math.min(this._posicion.y, base.y + base.height - ALTO))
            );
            return;
        }

        this._soulConnector.actor.set_position(
            base.x + base.width - ANCHO - MARGEN,
            base.y + base.height - ALTO - MARGEN
        );
    }

    disable() {
        if (this._dbusVentanas) {
            this._dbusVentanas.flush();
            this._dbusVentanas.unexport();
            this._dbusVentanas = null;
        }
        if (this._idMonitores) {
            Main.layoutManager.disconnect(this._idMonitores);
            this._idMonitores = 0;
        }
        if (this._idAreas) {
            global.display.disconnect(this._idAreas);
            this._idAreas = 0;
        }
        if (this._arrastre) {
            this._arrastre.destruir();
            this._arrastre = null;
        }
        if (this._soulConnector) {
            Main.layoutManager.removeChrome(this._soulConnector.actor);
            this._soulConnector.destruir();
            this._soulConnector = null;
        }
        if (this._panel) {
            this._panel.destruir();
            this._panel = null;
        }
    }
}
