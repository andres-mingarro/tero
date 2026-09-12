// Cliente del stream de niveles del daemon (boca/server.py), hablado por
// WebSocket contra ws://127.0.0.1:8765.
//
// Se reusa el mismo servidor y el mismo protocolo JSON que ya consume la
// boca de pywebview, sin tocar una línea del daemon: para Tero esto es
// otro cliente más del stream, exactamente como dice CLAUDE.md que tiene
// que ser ("El daemon tiene que funcionar sin la boca. La ventana es un
// cliente opcional del stream de niveles"). Eso es lo que hace que esta
// extensión se pueda sacar sin consecuencias.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';

const URL = 'ws://127.0.0.1:8765';
const REINTENTO_MS = 1000;

export class Enlace {
    /** @param onMensaje recibe el objeto JSON ya parseado */
    constructor(onMensaje) {
        this._onMensaje = onMensaje;
        this._sesion = new Soup.Session();
        this._conexion = null;
        this._cancelable = null;
        this._idReintento = 0;
        this._cerrado = false;
        this._decodificador = new TextDecoder();
    }

    conectar() {
        if (this._cerrado)
            return;
        this._cancelable = new Gio.Cancellable();
        const mensaje = new Soup.Message({
            method: 'GET',
            uri: GLib.Uri.parse(URL, GLib.UriFlags.NONE),
        });
        this._sesion.websocket_connect_async(
            mensaje, null, null, GLib.PRIORITY_DEFAULT, this._cancelable,
            (sesion, res) => this._alConectar(sesion, res)
        );
    }

    _alConectar(sesion, res) {
        if (this._cerrado)
            return;
        let conexion;
        try {
            conexion = sesion.websocket_connect_finish(res);
        } catch (e) {
            // Lo normal es que el daemon no esté levantado todavía: no es
            // un error que valga la pena loguear en cada reintento.
            this._programarReintento();
            return;
        }
        this._conexion = conexion;
        conexion.connect('message', (_c, tipo, datos) => {
            if (tipo !== Soup.WebsocketDataType.TEXT)
                return;
            try {
                this._onMensaje(JSON.parse(this._decodificador.decode(datos.toArray())));
            } catch (e) {
                // Un mensaje malformado no puede tirar abajo la conexión.
            }
        });
        conexion.connect('closed', () => {
            this._conexion = null;
            this._programarReintento();
        });
        conexion.connect('error', () => {
            this._conexion = null;
        });
    }

    _programarReintento() {
        if (this._cerrado || this._idReintento)
            return;
        this._idReintento = GLib.timeout_add(GLib.PRIORITY_DEFAULT, REINTENTO_MS, () => {
            this._idReintento = 0;
            this.conectar();
            return GLib.SOURCE_REMOVE;
        });
    }

    destruir() {
        this._cerrado = true;
        if (this._idReintento) {
            GLib.Source.remove(this._idReintento);
            this._idReintento = 0;
        }
        if (this._cancelable) {
            this._cancelable.cancel();
            this._cancelable = null;
        }
        if (this._conexion) {
            this._conexion.close(Soup.WebsocketCloseCode.NORMAL, null);
            this._conexion = null;
        }
        this._sesion = null;
    }
}
