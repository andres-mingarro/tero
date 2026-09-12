// La boca de Tero como extensión de GNOME Shell.
//
// Por qué existe: la boca original (boca/, pywebview + QtWebEngine) se
// lleva ~1,3 GB de RAM para dibujar una onda, porque levanta un Chromium
// entero. Acá el dibujo corre adentro de gnome-shell, que ya está en
// memoria, así que el costo extra es esencialmente el de los dos senos
// que se calculan por frame.
//
// De yapa resuelve un problema viejo: el "siempre encima" nunca funcionó
// bien bajo Mutter (ver CLAUDE.md), y la boca de pywebview lo peleaba
// llamando a wmctrl en un bucle mientras Tero hablaba. Siendo parte del
// shell no hay nada que pelear: el actor vive en la capa de chrome, por
// encima de las ventanas, siempre.
//
// No toca el daemon: es otro cliente más del WebSocket de niveles.

import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {Onda, COLORES_ORIGINALES} from './onda.js';
import {Enlace} from './enlace.js';

const ANCHO = 260;
const ALTO = 74;
const MARGEN = 20;

const FPS = 60;
const FPS_IDLE = 30; // en reposo la onda apenas respira: no hace falta más

// Mismos colores y constantes que boca/index.html, para que el look no
// cambie al migrar.
const COLORES = {
    idle: [0x4a, 0x44, 0x58],
    escuchando: [0xff, 0xff, 0xff],
    pensando: [0xc7, 0x7d, 0xff],
    hablando: null, // multicolor original de la librería
    musica: [0x5c, 0xff, 0xd4],
};

const AMPLITUD_IDLE = 0.15;
const AMPLITUD_PENSANDO = 0.45;

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

class Boca {
    constructor() {
        this._estado = 'idle';
        this._nivelCrudo = 0;
        this._nivelSuave = AMPLITUD_IDLE;
        this._velocidad = 0.06;
        this._onda = new Onda();

        this._cancion = null;
        this._progresoBase = 0;
        this._duracionMs = 0;
        this._recibidoEn = 0;
        this._pausadoDesde = 0;

        this._construirActores();
        this._idAnimacion = 0;
        this._fpsActual = 0;
        this._animar(FPS_IDLE);

        this._enlace = new Enlace(m => this._alRecibir(m));
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

        this._etiquetaCancion = new St.Label({
            style_class: 'tero-boca-cancion',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._etiquetaCancion.clutter_text.set_line_wrap(false);
        this._etiquetaCancion.clutter_text.set_ellipsize(3 /* END */);
        this._etiquetaCancion.set_position(10, ALTO - 35);
        this._etiquetaCancion.set_width(ANCHO - 20);
        this._etiquetaCancion.opacity = 0;
        this._raiz.add_child(this._etiquetaCancion);

        this._filaProgreso = new St.BoxLayout({
            style_class: 'tero-boca-progreso',
            width: ANCHO - 20,
        });
        this._filaProgreso.set_position(10, ALTO - 20);
        this._filaProgreso.opacity = 0;

        this._tiempoActual = new St.Label({style_class: 'tero-boca-tiempo'});
        this._pista = new St.Widget({style_class: 'tero-boca-pista', y_align: Clutter.ActorAlign.CENTER});
        this._relleno = new St.Widget({style_class: 'tero-boca-relleno'});
        this._pista.add_child(this._relleno);
        this._tiempoTotal = new St.Label({style_class: 'tero-boca-tiempo'});

        this._filaProgreso.add_child(this._tiempoActual);
        this._filaProgreso.add_child(this._pista);
        this._filaProgreso.add_child(this._tiempoTotal);
        this._pista.x_expand = true;
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
        this._onda.dibujar(cr, ancho, alto, this._nivelSuave, this._velocidad, this._colores());
        cr.$dispose();
    }

    _amplitudObjetivo() {
        if (SEGUIR_NIVEL.includes(this._estado))
            return this._nivelCrudo;
        if (this._estado === 'pensando')
            return AMPLITUD_PENSANDO;
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
        else
            this._velocidad = this._estado === 'pensando' ? 0.12 : 0.06;

        this._area.queue_repaint();
        this._actualizarProgreso();
        return GLib.SOURCE_CONTINUE;
    }

    _alRecibir(mensaje) {
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
        this._filaProgreso.ease({opacity: 255, duration: 600});
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
        const ancho = Math.max(0, this._pista.width);
        this._relleno.set_width(Math.round(ancho * (progreso / this._duracionMs)));
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

export default class BocaExtension extends Extension {
    enable() {
        this._boca = new Boca();
        // addChrome (y no un actor suelto en uiGroup) es lo que lo pone en
        // la capa de chrome: por encima de las ventanas y bien tratado al
        // entrar y salir de pantalla completa.
        //
        // Ojo: el viejo parámetro `affectsInputRegion: false` ya no existe
        // en GNOME 50 (tira "Unrecognized parameter" y la extensión no
        // carga). Que los clics pasen de largo ahora sale de que el actor
        // es `reactive: false`, que es como se construye en Boca.
        Main.layoutManager.addChrome(this._boca.actor, {
            trackFullscreen: true,
        });
        this._ubicar();
        this._idMonitores = Main.layoutManager.connect(
            'monitors-changed', () => this._ubicar());
    }

    _ubicar() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._boca)
            return;
        this._boca.actor.set_position(
            monitor.x + monitor.width - ANCHO - MARGEN,
            monitor.y + monitor.height - ALTO - MARGEN
        );
    }

    disable() {
        if (this._idMonitores) {
            Main.layoutManager.disconnect(this._idMonitores);
            this._idMonitores = 0;
        }
        if (this._boca) {
            Main.layoutManager.removeChrome(this._boca.actor);
            this._boca.destruir();
            this._boca = null;
        }
    }
}
