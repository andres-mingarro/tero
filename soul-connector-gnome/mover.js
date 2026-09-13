// Mover el soul-connector con el mouse (Ctrl+Alt + arrastrar), sin perder el
// click-through.
//
// Cómo decide GNOME 50 quién recibe un clic: con el *pick* de Clutter en
// ese momento. Si el actor bajo el puntero es reactivo, el clic va al
// shell; si no, sigue de largo a la ventana de abajo. Ya no hay una
// "región de entrada" precalculada (`_updateRegions()` de layout.js solo
// arma struts), así que cambiar `reactive` vale desde el próximo clic.
//
// El soul-connector en sí nunca es reactivo. Encima tiene un "asa": un
// widget del tamaño de todo el soul-connector que solo aparece (y solo es reactivo) mientras
// Ctrl+Alt está apretado con el puntero encima. El resto del tiempo el
// soul-connector es atravesable, como siempre.
//
// Tres intentos anteriores que no sirvieron, para no repetirlos:
//
// - `captured-event` en `global.stage` con el actor no reactivo: el clic
//   nunca llega. En Wayland va directo a la ventana de abajo.
// - Leer el puntero dejando pasar el clic: la ventana de abajo también
//   recibe el Ctrl+Alt+arrastre, y tiling-assistant usa justamente Ctrl y
//   Alt durante un arrastre de ventana -- la ventana se movía en
//   cuadrícula mientras el soul-connector no.
// - Volver reactivo el actor raíz del soul-connector: anda, pero solo si el clic cae
//   exactamente sobre lo que está dibujado (la línea de la onda). La raíz
//   no tiene fondo, así que el resto del rectángulo no cuenta como suyo.
//   El asa sí tiene fondo, así que se agarra desde cualquier punto.
//
// Ctrl+Alt y no Super porque Mutter usa Super+arrastrar para mover
// ventanas.

import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

const MODIFICADORES = Clutter.ModifierType.CONTROL_MASK | Clutter.ModifierType.MOD1_MASK;
const BOTON = Clutter.ModifierType.BUTTON1_MASK;

// Consultar el puntero es barato; 30 ms alcanza para que el arrastre se
// sienta pegado a la mano y para que el asa ya esté cuando llega el clic.
const INTERVALO_MS = 30;

function rutaArchivo() {
    return GLib.build_filenamev([GLib.get_user_config_dir(), 'tero', 'soul_connector_posicion.json']);
}

/** Posición guardada, o null si no hay ninguna o el archivo está roto. */
export function leerPosicion() {
    try {
        const [ok, datos] = GLib.file_get_contents(rutaArchivo());
        if (!ok)
            return null;
        const {x, y} = JSON.parse(new TextDecoder().decode(datos));
        if (typeof x !== 'number' || typeof y !== 'number')
            return null;
        return {x, y};
    } catch {
        return null;
    }
}

function guardarPosicion(x, y) {
    try {
        const ruta = rutaArchivo();
        GLib.mkdir_with_parents(GLib.path_get_dirname(ruta), 0o700);
        GLib.file_set_contents(ruta, JSON.stringify({x: Math.round(x), y: Math.round(y)}));
    } catch (e) {
        logError(e, 'soul-connector: no pude guardar la posición');
    }
}

export class Arrastre {
    /**
     * @param actor la raíz del soul-connector (St.Widget con FixedLayout, no reactiva)
     * @param alSoltar callback con (x, y) cuando termina el arrastre
     */
    constructor(actor, alSoltar) {
        this._actor = actor;
        this._alSoltar = alSoltar;
        this._arrastrando = false;
        this._dx = 0;
        this._dy = 0;

        const [ancho, alto] = actor.get_size();
        this._asa = new St.Widget({
            style_class: 'tero-soul-connector-asa',
            width: ancho,
            height: alto,
            reactive: false,
            visible: false,
        });
        // Último hijo: queda encima de la onda y de los textos.
        actor.add_child(this._asa);

        this._idClic = this._asa.connect('button-press-event', (_a, evento) => {
            if (evento.get_button() !== 1)
                return Clutter.EVENT_PROPAGATE;
            const [px, py] = evento.get_coords();
            const [ax, ay] = actor.get_position();
            this._arrastrando = true;
            this._dx = px - ax;
            this._dy = py - ay;
            return Clutter.EVENT_STOP;
        });

        this._idTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, INTERVALO_MS, () => {
            try {
                this._revisar();
                return GLib.SOURCE_CONTINUE;
            } catch (e) {
                // Se desarma solo y vuelve a ser atravesable: se pierde
                // el arrastre, nunca los clics.
                logError(e, 'soul-connector: arrastre desactivado por un error');
                this._mostrarAsa(false);
                this._idTimer = 0;
                return GLib.SOURCE_REMOVE;
            }
        });
    }

    _mostrarAsa(si) {
        if (this._asa.reactive !== si)
            this._asa.reactive = si;
        if (this._asa.visible !== si)
            this._asa.visible = si;
    }

    _revisar() {
        const [px, py, mods] = global.get_pointer();

        if (this._arrastrando) {
            if ((mods & BOTON) !== 0) {
                this._actor.set_position(Math.round(px - this._dx), Math.round(py - this._dy));
                return;
            }
            this._arrastrando = false;
            const [x, y] = this._actor.get_position();
            guardarPosicion(x, y);
            this._alSoltar(x, y);
            // Sin return: si Ctrl+Alt sigue apretado y el puntero encima,
            // el asa se queda y se puede volver a agarrar enseguida.
        }

        const [ax, ay] = this._actor.get_position();
        const [ancho, alto] = this._actor.get_size();
        const encima = px >= ax && px <= ax + ancho && py >= ay && py <= ay + alto;
        const conTeclas = (mods & MODIFICADORES) === MODIFICADORES;
        this._mostrarAsa(encima && conTeclas);
    }

    destruir() {
        if (this._idTimer) {
            GLib.Source.remove(this._idTimer);
            this._idTimer = 0;
        }
        if (this._idClic) {
            this._asa.disconnect(this._idClic);
            this._idClic = 0;
        }
        this._asa.destroy();
    }
}
