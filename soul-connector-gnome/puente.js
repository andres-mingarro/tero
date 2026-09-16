// Puente D-Bus hacia adentro del compositor: nada visual, ver CLAUDE.md
// ("Reglas de arquitectura") -- SOUL (extension.js/SoulConnector) es
// solo render, así que todo lo que Tero necesita del propio gnome-shell
// (cosas que Wayland no deja tocar desde afuera del compositor) vive
// separado, acá.
//
// Dos casos hoy, los dos por la misma razón: un proceso externo no puede.
// - MoverVentanaAMonitor: wmctrl solo mueve ventanas X11/XWayland: bajo
//   Wayland, Meta.Window.move_to_monitor() tiene que llamarse desde
//   adentro. Ver herramientas/mover_ventana_monitor.py.
// - CapturarPantalla: el D-Bus público org.gnome.Shell.Screenshot tira
//   "AccessDenied: Screenshot is not allowed" a cualquier llamador
//   externo (GNOME reciente lo reserva para el portal). Shell.Screenshot,
//   la misma clase que usa gnome-shell para implementar ese D-Bus, no
//   tiene esa restricción llamada desde acá adentro. Ver
//   herramientas/captura_pantalla.py.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const _IFAZ = `
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
    <method name="CapturarPantalla">
      <arg type="s" direction="out" name="resultado" />
    </method>
  </interface>
</node>`;

export class Puente {
    constructor() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(_IFAZ, this);
        this._dbus.export(Gio.DBus.session, '/org/gnome/Shell/Extensions/Tero');
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

    // Método async (devuelve una Promise): los overrides de Gio en gjs lo
    // notan solo, no hace falta declararlo distinto a los métodos
    // sincrónicos de arriba -- ver _handleMethodCall en el propio gjs
    // (retval?.then?.(...) contesta el D-Bus cuando resuelve).
    //
    // La API moderna de Shell.Screenshot (GNOME 42+) recibe un
    // Gio.OutputStream, no una ruta de archivo -- por eso se abre el
    // stream acá y no se le pasa el nombre directo al método.
    async CapturarPantalla() {
        const ruta = GLib.build_filenamev(
            [GLib.get_tmp_dir(), `tero-captura-${GLib.get_monotonic_time()}.png`]);
        try {
            const archivo = Gio.File.new_for_path(ruta);
            const stream = archivo.replace(
                null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            const captura = new Shell.Screenshot();
            const exito = await new Promise((resolve, reject) => {
                captura.screenshot(false, stream, (obj, res) => {
                    try {
                        const [ok] = obj.screenshot_finish(res);
                        resolve(ok);
                    } catch (e) {
                        reject(e);
                    }
                });
            });
            stream.close(null);
            if (!exito)
                return JSON.stringify({ok: false, error: 'la captura no se completó'});
            return JSON.stringify({ok: true, archivo: ruta});
        } catch (e) {
            return JSON.stringify({ok: false, error: String(e)});
        }
    }

    destruir() {
        if (this._dbus) {
            this._dbus.flush();
            this._dbus.unexport();
            this._dbus = null;
        }
    }
}
