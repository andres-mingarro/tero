// Port a Cairo del estilo "ios9" de SiriWave (boca/siriwave.umd.js), que
// en la boca de pywebview se dibujaba en un <canvas>. La matemática es la
// misma, traducida 1:1 desde iOS9Curve para que la onda se vea igual:
// cada "curva" de color es en realidad un puñado de senos superpuestos que
// nacen y mueren solos, recortados por una envolvente que los apaga hacia
// los bordes.
//
// Las dos diferencias con el original, ambas a propósito:
//
// - El paso de muestreo. SiriWave usa pixelDepth 0.02, o sea 2500 puntos
//   por curva y por lado. A 260 px de ancho eso es ~10 puntos por píxel:
//   invisible, y acá se paga caro porque esto corre adentro de
//   gnome-shell. Con 0.1 (500 puntos) el trazo es indistinguible.
// - El blending. El canvas usaba globalCompositeOperation "lighter";
//   el equivalente en Cairo es el operador ADD.

import Cairo from 'cairo';

const GRAPH_X = 25;
const FACTOR_AMPLITUD = 0.8;
const FACTOR_ATENUACION = 4;
const FACTOR_DESPAWN = 0.02;
const PIXELES_MUERTOS = 2;
const PASO = 0.1;
const ALFA = 0.7;

const RANGO_SUBCURVAS = [2, 5];
const RANGO_AMPLITUD = [0.3, 1];
const RANGO_OFFSET = [-3, 3];
const RANGO_ANCHO = [1, 3];
const RANGO_VELOCIDAD = [0.5, 1];
const RANGO_DESPAWN_MS = [500, 2000];

// Los tres colores que trae la librería (azul, rojo, verde). Son los que
// se usan en el estado "hablando", que a pedido del usuario es el único
// que conserva el multicolor original; el resto pinta las tres curvas de
// un color plano.
export const COLORES_ORIGINALES = [
    [15, 82, 169],
    [173, 57, 76],
    [48, 220, 155],
];

function azar([desde, hasta]) {
    return desde + Math.random() * (hasta - desde);
}

function atenuacion(x) {
    return Math.pow(FACTOR_ATENUACION / (FACTOR_ATENUACION + x * x), FACTOR_ATENUACION);
}

/** Una banda de color: varios senos que nacen, viven y se apagan solos. */
class Curva {
    constructor() {
        this._nacidaEn = 0;
        this._maxYPrevio = 0;
        this._subcurvas = [];
    }

    _nacer() {
        this._nacidaEn = Date.now();
        const cuantas = Math.floor(azar(RANGO_SUBCURVAS));
        this._subcurvas = [];
        for (let i = 0; i < cuantas; i++) {
            this._subcurvas.push({
                fase: 0,
                amplitud: 0,
                amplitudFinal: azar(RANGO_AMPLITUD),
                offset: azar(RANGO_OFFSET),
                ancho: azar(RANGO_ANCHO),
                velocidad: azar(RANGO_VELOCIDAD),
                muereA: azar(RANGO_DESPAWN_MS),
                sentido: azar([-1, 1]),
            });
        }
    }

    _yRelativa(i) {
        const total = this._subcurvas.length;
        let y = 0;
        this._subcurvas.forEach((sub, ci) => {
            // Separación fija entre subcurvas, más un corrimiento propio:
            // es lo que hace que no queden todas encimadas.
            const t = 4 * (-1 + (ci / (total - 1)) * 2) + sub.offset;
            const x = i / sub.ancho - t;
            y += Math.abs(
                sub.amplitud * Math.sin(sub.sentido * x - sub.fase) * atenuacion(x)
            );
        });
        return y / total;
    }

    _avanzar(velocidadGlobal) {
        const ahora = Date.now();
        for (const sub of this._subcurvas) {
            const muriendo = this._nacidaEn + sub.muereA <= ahora;
            sub.amplitud += muriendo ? -FACTOR_DESPAWN : FACTOR_DESPAWN;
            sub.amplitud = Math.min(Math.max(sub.amplitud, 0), sub.amplitudFinal);
            sub.fase = (sub.fase + velocidadGlobal * sub.velocidad) % (2 * Math.PI);
        }
    }

    dibujar(cr, ancho, altoMax, amplitudGlobal, velocidadGlobal, color) {
        if (this._nacidaEn === 0)
            this._nacer();
        this._avanzar(velocidadGlobal);

        const [r, g, b] = color;
        let maxY = -Infinity;

        // Dos ondas espejadas: la de arriba y la de abajo del eje.
        for (const signo of [1, -1]) {
            cr.newPath();
            for (let i = -GRAPH_X; i <= GRAPH_X; i += PASO) {
                const x = ancho * ((i + GRAPH_X) / (GRAPH_X * 2));
                const y =
                    FACTOR_AMPLITUD *
                    altoMax *
                    amplitudGlobal *
                    this._yRelativa(i) *
                    atenuacion((i / GRAPH_X) * 2);
                cr.lineTo(x, altoMax - signo * y);
                maxY = Math.max(maxY, y);
            }
            cr.closePath();
            cr.setSourceRGBA(r / 255, g / 255, b / 255, ALFA);
            cr.fill();
        }

        // Cuando la banda se apagó del todo, vuelve a nacer con senos
        // nuevos: de ahí que la onda nunca repita exactamente el mismo
        // dibujo.
        if (maxY < PIXELES_MUERTOS && this._maxYPrevio > maxY)
            this._nacidaEn = 0;
        this._maxYPrevio = maxY;
    }
}

/**
 * Y del eje de la onda, donde vive el hilo horizontal tenue. La barra de
 * progreso se apoya justo acá: en el original las dos se ven como una
 * sola línea continua con el puntito de luz encima, así que el offset
 * tiene que salir de un solo lado y no repetirse a mano.
 */
export function lineaBase(alto) {
    return alto / 2 - 6;
}

export class Onda {
    constructor() {
        this._curvas = [new Curva(), new Curva(), new Curva()];
    }

    /**
     * @param cr contexto Cairo del St.DrawingArea
     * @param colores un [r,g,b] por curva (tres)
     */
    dibujar(cr, ancho, alto, amplitud, velocidad, colores) {
        const altoMax = lineaBase(alto);

        cr.setOperator(Cairo.Operator.OVER);
        this._dibujarLineaBase(cr, ancho, altoMax);

        // Aditivo: donde se cruzan dos bandas el color se suma y aclara,
        // que es lo que le da el aspecto de luz y no de plástico.
        cr.setOperator(Cairo.Operator.ADD);
        this._curvas.forEach((curva, i) => {
            curva.dibujar(cr, ancho, altoMax, amplitud, velocidad, colores[i]);
        });
        cr.setOperator(Cairo.Operator.OVER);
    }

    /** Hilo horizontal tenue sobre el eje, desvanecido en las puntas. */
    _dibujarLineaBase(cr, ancho, altoMax) {
        const degrade = new Cairo.LinearGradient(0, altoMax, ancho, 1);
        degrade.addColorStopRGBA(0, 1, 1, 1, 0);
        degrade.addColorStopRGBA(0.1, 1, 1, 1, 0.5);
        degrade.addColorStopRGBA(0.8, 1, 1, 1, 0.5);
        degrade.addColorStopRGBA(1, 1, 1, 1, 0);
        cr.setSource(degrade);
        cr.rectangle(0, altoMax, ancho, 1);
        cr.fill();
    }
}
