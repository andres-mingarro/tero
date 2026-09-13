// Port a Cairo de la barra de progreso de soul_connector/index.html (el reproductor
// del soul-connector de pywebview), con una diferencia pedida a propósito: acá es
// **siempre blanca**. El original cicla el color del glow cada 4s y en
// pausa late en rojo/verde; esta versión no cambia de tono nunca, así que
// lo único que se mueve es el brillo.
//
// En CSS el glow salía de `filter: drop-shadow()`. Acá no hay filtros, así
// que se aproxima con un degradé vertical que se apaga hacia arriba y
// hacia abajo, en modo ADD (igual que la onda: sumar luz es lo que evita
// que se vea como plástico). Apilar elipses sólidas, que fue el primer
// intento, no sirve: se suman en el centro y lo que se ve es una lente
// gris gorda en vez de una línea con brillo. Las animaciones salen del
// reloj en cada frame.
//
// La barra de fondo (la "pista") no se dibuja a propósito: en el original
// es transparente, y esa línea larga que se ve cruzando todo es en
// realidad el eje de la onda, sobre el que esta barra se apoya.

import Cairo from 'cairo';

const CICLO_RESPIRO_MS = 1800;
const CICLO_PULSO_MS = 2200;

// Hasta dónde llega el brillo hacia arriba y hacia abajo de la línea, y
// cuánto pinta en el centro. Equivale a los dos drop-shadow del CSS.
const RADIO_GLOW = 3;
const ALFA_GLOW = 0.07;

const CAPAS_PUNTA = [[5, 0.14], [3, 0.22]];

// Abajo de esto el relleno no se dibuja: una elipse de radio ~0 es una
// matriz degenerada, y Cairo no puede invertirla.
const MINIMO_VISIBLE = 1;

/** 0..1 y de vuelta, suave: el equivalente de un ease-in-out alternado. */
function respiro(ahora, ciclo) {
    return 0.5 - 0.5 * Math.cos((2 * Math.PI * (ahora % ciclo)) / ciclo);
}

function blanco(cr, alfa) {
    cr.setSourceRGBA(1, 1, 1, Math.min(1, alfa));
}

export class Barra {
    /**
     * @param cr contexto Cairo del St.DrawingArea
     * @param yLinea alto al que va la línea, dentro del área de dibujo
     * @param fraccion 0..1 de la canción ya reproducida
     * @param pausado cambia el respiro por un latido más marcado
     */
    dibujar(cr, ancho, yLinea, fraccion, pausado) {
        const ahora = Date.now();
        // El +0.5 es para que una línea de 1px caiga sobre un píxel y no
        // entre dos (donde Cairo la reparte en dos filas grises).
        const y = Math.round(yLinea) + 0.5;
        const x = Math.max(0, Math.min(1, fraccion)) * ancho;

        // Pausado late más marcado y más lento; en reproducción apenas
        // respira. Es la única diferencia entre los dos estados, ya que
        // el color no cambia nunca.
        const late = pausado
            ? respiro(ahora, CICLO_PULSO_MS)
            : respiro(ahora, CICLO_RESPIRO_MS) * 0.4;

        // El relleno se dibuja como elipse, no como línea. En el original
        // lleva `border-radius: 100%`, que sobre una caja de 1px de alto
        // no redondea esquinas: la vuelve una elipse que se afina hasta
        // desaparecer en las dos puntas. Con una línea recta, en cambio,
        // Cairo remata a escuadra y deja un corte vertical feo justo al
        // principio de la barra.
        if (x > MINIMO_VISIBLE) {
            // El glow como degradé vertical y no como elipses sólidas
            // apiladas: apiladas se suman en el centro (0,10 + 0,16 +
            // 0,30 en modo ADD) y lo que se ve es una lente gris gorda,
            // no una línea con brillo. El degradé se apaga hacia arriba
            // y hacia abajo, que es lo que hace un desenfoque de verdad.
            const radio = RADIO_GLOW * (1 + 0.25 * late);
            const degrade = new Cairo.LinearGradient(0, y - radio, 0, y + radio);
            degrade.addColorStopRGBA(0, 1, 1, 1, 0);
            degrade.addColorStopRGBA(0.5, 1, 1, 1, ALFA_GLOW * (1 + 0.3 * late));
            degrade.addColorStopRGBA(1, 1, 1, 1, 0);
            cr.setOperator(Cairo.Operator.ADD);
            cr.setSource(degrade);
            this._elipse(cr, x / 2, y, x / 2, radio);
            cr.fill();
            cr.setOperator(Cairo.Operator.OVER);

            // El relleno real de la barra (background: #ffffffa6).
            blanco(cr, 0.65);
            this._elipse(cr, x / 2, y, x / 2, 0.5);
            cr.fill();
        }

        this._dibujarPunta(cr, x, y, late);
    }

    _dibujarPunta(cr, x, y, late) {
        cr.setOperator(Cairo.Operator.ADD);
        for (const [radio, alfa] of CAPAS_PUNTA) {
            blanco(cr, alfa * (1 + 0.5 * late));
            this._elipse(cr, x, y, radio, radio * 0.6);
            cr.fill();
        }
        cr.setOperator(Cairo.Operator.OVER);

        blanco(cr, 1);
        this._elipse(cr, x, y, 2, 1);
        cr.fill();
    }

    _elipse(cr, cx, cy, rx, ry) {
        // Cairo guarda el trazo ya transformado, así que restaurar la
        // matriz después de armarlo no lo deforma de vuelta.
        cr.save();
        cr.translate(cx, cy);
        cr.scale(rx, ry);
        cr.arc(0, 0, 1, 0, 2 * Math.PI);
        cr.restore();
    }
}
