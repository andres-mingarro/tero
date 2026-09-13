#!/usr/bin/env -S gjs -m
//
// Renderiza el soul-connector a un PNG, fuera de gnome-shell.
//
// Por qué existe: GNOME cachea los módulos ES de las extensiones, así que
// `disable`/`enable` NO recarga el código (ver README). En Wayland la
// única forma de probar un cambio en vivo es cerrar sesión y volver a
// entrar -- un logout por iteración, a ciegas, y encima GNOME bloquea la
// captura de pantalla del shell, así que el que mira siempre tiene que
// ser el usuario.
//
// `onda.js` y `barra.js` son Cairo puro y no tocan St ni Clutter, o sea
// que corren perfectamente en un gjs suelto. Esto los dibuja sobre una
// ImageSurface con la misma geometría que arma `extension.js` y escribe
// un PNG, que se puede mirar sin gastar una sesión.
//
//   ./previsualizar.js [salida.png]
//
// Ojo con lo que NO prueba: el posicionamiento en pantalla, el work area,
// los St.Label (los tiempos y el nombre salen dibujados acá con Cairo
// aparte, solo para ubicar el ojo) y cualquier cosa que dependa del
// shell. Para eso no hay atajo.

import Cairo from 'cairo';
import GLib from 'gi://GLib';

import {Onda, lineaBase} from './onda.js';
import {Barra} from './barra.js';

const ANCHO = 260;
const ALTO = 74;
const ALTO_BARRA = 20;
const Y_CANCION = 39;

// Mismos colores que extension.js.
const COLORES = {
    idle: [0x4a, 0x44, 0x58],
    escuchando: [0xff, 0xff, 0xff],
    pensando: [0xc7, 0x7d, 0xff],
    musica: [0x5c, 0xff, 0xd4],
};

// Un gris de escritorio para que se vea algo: el soul-connector de verdad va sobre
// lo que haya abajo, no sobre un fondo propio.
const FONDO = [0.12, 0.12, 0.13];

const ESCALA = 3; // el PNG sale ampliado, si no no se distingue nada

function dibujarEscena(cr, {estado, amplitud, fraccion, pausado, cancion}) {
    cr.setSourceRGB(...FONDO);
    cr.paint();

    const color = COLORES[estado] || COLORES.idle;
    const onda = new Onda();
    // Las curvas nacen con amplitud 0 y suben de a poco, así que hay que
    // adelantar varios frames antes de que haya algo que ver. Van a una
    // superficie descartable: dibujarlos encima del bueno los suma en
    // modo ADD y sale todo blanco saturado (pasó).
    const borrador = new Cairo.Context(
        new Cairo.ImageSurface(Cairo.Format.ARGB32, ANCHO, ALTO));
    for (let i = 0; i < 120; i++)
        onda.dibujar(borrador, ANCHO, ALTO, amplitud, 0.1, [color, color, color]);
    onda.dibujar(cr, ANCHO, ALTO, amplitud, 0.1, [color, color, color]);

    if (fraccion !== null) {
        const yLinea = lineaBase(ALTO);
        cr.save();
        // Mismo recorte horizontal que el St.BoxLayout: 10px de margen,
        // 26px de tiempo y 6px de separación a cada lado.
        cr.translate(42, yLinea - ALTO_BARRA / 2);
        new Barra().dibujar(cr, ANCHO - 84, ALTO_BARRA / 2, fraccion, pausado);
        cr.restore();

        cr.selectFontFace('sans-serif', Cairo.FontSlant.NORMAL, Cairo.FontWeight.NORMAL);
        cr.setFontSize(9);
        cr.setSourceRGBA(1, 1, 1, 0.55);
        cr.moveTo(10, yLinea + 3);
        cr.showText('0:46');
        cr.moveTo(ANCHO - 36, yLinea + 3);
        cr.showText('3:29');

        cr.setFontSize(11);
        cr.setSourceRGBA(1, 1, 1, 0.8);
        const te = cr.textExtents(cancion);
        cr.moveTo((ANCHO - te.width) / 2, Y_CANCION + 10);
        cr.showText(cancion);
    }
}

const ESCENAS = [
    {nombre: 'musica', estado: 'musica', amplitud: 0.55, fraccion: 0.42,
     pausado: false, cancion: 'The Downfall of Us All · A Day To Remember'},
    {nombre: 'arranque', estado: 'musica', amplitud: 0.55, fraccion: 0.03,
     pausado: false, cancion: 'Recién empezada: la punta no debe cortarse'},
    {nombre: 'pausado', estado: 'musica', amplitud: 0.2, fraccion: 0.42,
     pausado: true, cancion: 'En pausa · late por brillo'},
    {nombre: 'escuchando', estado: 'escuchando', amplitud: 0.7, fraccion: null},
    {nombre: 'pensando', estado: 'pensando', amplitud: 0.45, fraccion: null},
    {nombre: 'idle', estado: 'idle', amplitud: 0.15, fraccion: null},
];

const salida = ARGV[0] || 'previsualizacion.png';

// Todas las escenas, una debajo de la otra, en una sola imagen.
const superficie = new Cairo.ImageSurface(
    Cairo.Format.ARGB32, ANCHO * ESCALA, ALTO * ESCENAS.length * ESCALA);
const cr = new Cairo.Context(superficie);
cr.scale(ESCALA, ESCALA);

ESCENAS.forEach((escena, i) => {
    cr.save();
    cr.translate(0, i * ALTO);
    cr.rectangle(0, 0, ANCHO, ALTO);
    cr.clip();
    dibujarEscena(cr, escena);
    cr.restore();
});

superficie.writeToPNG(salida);
superficie.finish();
print(`escrito: ${salida} (${ESCENAS.map(e => e.nombre).join(', ')})`);
GLib;
