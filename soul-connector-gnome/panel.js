// Toggle de Tero en el menú rápido de GNOME (el que se abre desde arriba
// a la derecha, junto a WiFi/Bluetooth/etc. -- pedido explícito del
// usuario, mostró una captura de ese menú). Controla el servicio
// systemd de usuario (ver systemd/tero.service) en vez de matar procesos
// a mano: iniciar/reiniciar/cerrar, y un enlace directo al log para ver
// qué está pasando sin abrir una terminal.
//
// Por qué no vive adentro de extension.js: es una pieza independiente
// del soul-connector (la onda) -- puede quedar visible aunque el usuario
// desinstale/pruebe otra versión de la onda, y viceversa. Se instancia
// desde `enable()`/`disable()` de extension.js igual que el resto.
//
// El estado (¿está corriendo?) se relee cada pocos segundos con
// `systemctl --user is-active` en vez de asumir que el último click fue
// la verdad -- si salud.py corta a Tero solo (RAM crítica) o alguien lo
// para desde una terminal, el toggle tiene que notarlo igual.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';

const QuickSettingsMenu = Main.panel.statusArea.quickSettings;

const INTERVALO_POLL_S = 4;
const RUTA_LOG = GLib.build_filenamev(
    [GLib.get_home_dir(), 'proyectos', 'tero', 'logs', 'tero.log']);

function _correr(argv) {
    // Fire-and-forget a propósito: no hace falta esperar el resultado
    // para refrescar el estado, el próximo poll ya lo muestra real.
    try {
        const proceso = new Gio.Subprocess({argv, flags: Gio.SubprocessFlags.NONE});
        proceso.init(null);
    } catch (error) {
        logError(error, `Tero (panel): ${argv.join(' ')}`);
    }
}

const _systemctl = accion => _correr(['systemctl', '--user', accion, 'tero']);

const TeroToggle = GObject.registerClass(
class TeroToggle extends QuickSettings.QuickMenuToggle {
    _init() {
        super._init({
            title: 'Tero',
            iconName: 'audio-input-microphone-symbolic',
            toggleMode: false, // el estado real lo manda systemctl, no el click
        });

        this.menu.setHeader('audio-input-microphone-symbolic', 'Tero');

        const seccion = new PopupMenu.PopupMenuSection();
        this._itemIniciar = seccion.addAction('Iniciar', () => _systemctl('start'));
        this._itemReiniciar = seccion.addAction('Reiniciar', () => _systemctl('restart'));
        this._itemCerrar = seccion.addAction('Cerrar', () => _systemctl('stop'));
        this.menu.addMenuItem(seccion);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Ver log', () => _correr(['xdg-open', RUTA_LOG]));

        // Click directo sobre el ícono (sin abrir el submenú): mismo
        // gesto de un solo toque que Wifi/Bluetooth -- prende si está
        // apagado, apaga si está prendido.
        this.connect('clicked', () => _systemctl(this._activo ? 'stop' : 'start'));

        this._activo = null;
        this._actualizarEstado();
        this._idPoll = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, INTERVALO_POLL_S, () => {
                this._actualizarEstado();
                return GLib.SOURCE_CONTINUE;
            });
        this.connect('destroy', () => {
            if (this._idPoll) {
                GLib.Source.remove(this._idPoll);
                this._idPoll = 0;
            }
        });
    }

    _actualizarEstado() {
        let proceso;
        try {
            proceso = new Gio.Subprocess({
                argv: ['systemctl', '--user', 'is-active', 'tero'],
                flags: Gio.SubprocessFlags.STDOUT_PIPE,
            });
            proceso.init(null);
        } catch (error) {
            logError(error, 'Tero (panel): systemctl --user is-active tero');
            return;
        }
        proceso.communicate_utf8_async(null, null, (_p, resultado) => {
            let salida = '';
            try {
                [salida] = proceso.communicate_utf8_finish(resultado);
            } catch (error) {
                return; // el toggle puede haberse destruido mientras esperaba
            }
            const activo = salida.trim() === 'active';
            if (activo === this._activo)
                return;
            this._activo = activo;
            this.checked = activo;
            this.subtitle = activo ? 'Escuchando' : 'Apagado';
            this._itemIniciar.visible = !activo;
            this._itemReiniciar.visible = activo;
            this._itemCerrar.visible = activo;
        });
    }
});

export const TeroIndicator = GObject.registerClass(
class TeroIndicator extends QuickSettings.SystemIndicator {
    _init() {
        super._init();
        this._toggle = new TeroToggle();
        this.quickSettingsItems.push(this._toggle);
        QuickSettingsMenu.addExternalIndicator(this);
    }

    destruir() {
        this.quickSettingsItems.forEach(item => item.destroy());
        this.destroy();
    }
});
