// Partículas que nacen en el eje de la onda y suben apagándose -- el
// estado "energizado" que se ve cuando Tero delega en Codex/ChatGPT
// (fase 4, `delegar_a_codex`). Pedido del usuario, con un demo en CSS de
// referencia (partículas full-screen subiendo con `@keyframes rise`):
// nacen abajo, fade-in rápido, derivan un poco al costado, fade-out
// mientras suben. Acá el "arriba" es la punta de una onda de 74px, no la
// pantalla entera, así que la duración y el recorrido se achican.
//
// La vuelta larga hasta llegar acá: un primer intento con un gradiente
// radial (centro brillante apagándose hacia el borde, todo en un mismo
// círculo) se veía a esfera con sombreado -- es percepción humana, no
// un tema de números: cualquier brillo que dependa de la distancia al
// centro se lee como volumen 3D. Un segundo intento apilando anillos de
// alfa decreciente para aproximar un blur seguía mostrando el mismo
// problema a upa cerrada. La solución real: nada de aproximar el blur a
// mano. `Shell.BlurEffect` (mismo efecto que usa gnome-shell para el
// fondo del overview, real, por GPU) se aplica sobre una capa aparte que
// dibuja los puntos CHICOS y PLANOS (sin degradé), y el blur de esa capa
// entera hace de halo -- exactamente cómo funciona `box-shadow` en CSS:
// el elemento nítido arriba, una copia desenfocada abajo. Por eso esta
// clase expone dos métodos de dibujo separados (`dibujarNucleos` para la
// capa nítida, `dibujarGlow` para la que lleva el `Shell.BlurEffect`) en
// vez de un solo `dibujar` con anillos adentro. El wiring de la
// extensión (dos `St.DrawingArea`, una con el efecto) está en
// `extension.js`.
//
// Mismo patrón que `onda.js`/`barra.js`: cada partícula guarda cuándo
// nació (`Date.now()`) y todo se recalcula a partir de esa marca, sin un
// tick de "update" aparte -- salvo que acá `actualizar()` sí está
// separado de los dos `dibujar*`, porque los dos necesitan ver la MISMA
// lista de partículas en el mismo estado (si cada uno reprodujera el
// spawn por su cuenta, saldrían dos conjuntos de partículas distintos:
// uno para el núcleo, otro para el glow).

import Cairo from 'cairo';

// Mismo acento que el demo: cian, violeta, rosa, blanco.
const PALETA = [
    [0x00, 0xea, 0xff],
    [0x7b, 0x61, 0xff],
    [0xff, 0x3c, 0xac],
    [0xff, 0xff, 0xff],
];

// Mucha más variación de velocidad que el demo (ahí era nomás 4-8s, un
// rango 1:2) -- pedido explícito del usuario, más rango hace que no
// todas suban "en fila" a la vez.
const DURACION_MS = [400, 2000];
const RADIO_NUCLEO_PX = [0.6, 1.4]; // punto nítido, capa sin blur
const RADIO_GLOW_PX = [1.8, 3.6]; // punto plano en la capa CON blur -- el
                                   // halo lo hace el Shell.BlurEffect, no
                                   // este radio (que es chico a propósito)
const ALFA_NUCLEO = [0.75, 1];
const DERIVA_X_PX = [-16, 16];
// Cuánto más arriba del eje de la onda llegan a subir antes de morir.
// Probado en vivo (shell anidado + sesión real): con 18px quedaban dentro
// del propio vaivén de la onda en "codex" (amplitud 0.55, que ya ocupa
// ~14px de cada lado del eje) -- se leían mezcladas, no como algo que sube
// por separado. Con esto despegan claramente por arriba, y siguen
// muriendo bastante antes del borde de los 74px del widget (el fade-out
// ya está completo en progreso=1, ver opacidad()).
const ALTURA_SUBIDA_PX = 27;

// Mucha densidad: muchas a la vez, no unas pocas grandes.
const INTERVALO_SPAWN_MS = 22;
const MAX_PARTICULAS = 45;

function azar([desde, hasta]) {
    return desde + Math.random() * (hasta - desde);
}

function colorAzar() {
    return PALETA[Math.floor(Math.random() * PALETA.length)];
}

/** 0..1 -> 0..1: fade-in rápido (15% de la vida), fade-out el resto, igual
 * forma que el keyframe del demo (ahí sale de interpolar 0%→15%→100%). */
function opacidad(progreso) {
    if (progreso < 0.15)
        return progreso / 0.15;
    return 1 - (progreso - 0.15) / 0.85;
}

export class Particulas {
    constructor() {
        this._particulas = [];
        this._ultimoSpawn = 0;
    }

    /** Envejece, poda las muertas y hace nacer una nueva si corresponde.
     * Se llama una vez por frame, antes de los dos `dibujar*` -- ver
     * comentario de arriba sobre por qué no va adentro de cada dibujo. */
    actualizar(activo, ancho) {
        const ahora = Date.now();
        if (activo && this._particulas.length < MAX_PARTICULAS
            && ahora - this._ultimoSpawn >= INTERVALO_SPAWN_MS) {
            this._ultimoSpawn = ahora;
            const x = azar([8, ancho - 8]);
            // 0 en el centro del widget, 1 en el borde -- cuadrático para que
            // la caída sea suave cerca del centro y se note más rápido hacia
            // las puntas. Pedido del usuario: en las esquinas la partícula
            // casi no se despega de la línea, en el centro sube lo de
            // siempre (ALTURA_SUBIDA_PX entero).
            const centro = ancho / 2;
            const distancia = Math.abs(x - centro) / centro;
            const factorAltura = Math.pow(1 - distancia, 2);
            this._particulas.push({
                nacidaEn: ahora,
                duracion: azar(DURACION_MS),
                x,
                factorAltura,
                derivaX: azar(DERIVA_X_PX),
                radioNucleo: azar(RADIO_NUCLEO_PX),
                radioGlow: azar(RADIO_GLOW_PX),
                alfaNucleo: azar(ALFA_NUCLEO),
                color: colorAzar(),
            });
        }
        this._particulas = this._particulas.filter(
            p => (ahora - p.nacidaEn) / p.duracion < 1);
    }

    _paraCadaUna(yBase, cb) {
        const ahora = Date.now();
        for (const p of this._particulas) {
            const progreso = (ahora - p.nacidaEn) / p.duracion;
            const x = p.x + p.derivaX * progreso;
            const y = yBase - ALTURA_SUBIDA_PX * p.factorAltura * progreso;
            const alfa = opacidad(progreso);
            // Crece un poco mientras sube, igual que el demo (scale 0.3 -> 1).
            const crecimiento = 0.3 + 0.7 * progreso;
            cb(p, x, y, alfa, crecimiento);
        }
    }

    /** Capa nítida, sin blur: el "destello" -- un puntito chico y sólido
     * por partícula, color parejo. */
    dibujarNucleos(cr, yBase) {
        cr.setOperator(Cairo.Operator.ADD);
        this._paraCadaUna(yBase, (p, x, y, alfa, crecimiento) => {
            const [r, g, b] = p.color;
            cr.setSourceRGBA(r / 255, g / 255, b / 255, alfa * p.alfaNucleo);
            cr.arc(x, y, p.radioNucleo * crecimiento, 0, 2 * Math.PI);
            cr.fill();
        });
        cr.setOperator(Cairo.Operator.OVER);
    }

    /** Capa que lleva el `Shell.BlurEffect` puesto encima (ver
     * extension.js): puntos igual de planos, sin degradé -- el halo sale
     * de desenfocar esta capa entera con la GPU, no de nada que se haga
     * acá adentro. */
    dibujarGlow(cr, yBase) {
        cr.setOperator(Cairo.Operator.ADD);
        this._paraCadaUna(yBase, (p, x, y, alfa, crecimiento) => {
            const [r, g, b] = p.color;
            cr.setSourceRGBA(r / 255, g / 255, b / 255, Math.min(1, alfa * 1.6));
            cr.arc(x, y, p.radioGlow * crecimiento, 0, 2 * Math.PI);
            cr.fill();
        });
        cr.setOperator(Cairo.Operator.OVER);
    }
}
