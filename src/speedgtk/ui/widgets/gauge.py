"""Animated Cairo speed gauge."""

import math
import sys

import cairo
from gi.repository import Adw, GObject, Gtk, Pango, PangoCairo

from ...formatting import format_number
from ...i18n import _
from ..theme import (
    draw_text,
    gradient_stops,
    pango_layout,
    rgb_at,
    surface_rgb,
    text_rgba,
)
from .gauge_glow import draw_inner_glow


GAUGE_START_DEG = 135.0
GAUGE_SWEEP_DEG = 270.0
GAUGE_SCALES = (
    (100.0, (0, 1, 5, 10, 25, 50, 100)),
    (1000.0, (0, 1, 5, 10, 25, 50, 100, 250, 500, 1000)),
    (10000.0, (0, 1, 5, 10, 20, 50, 100, 300, 500, 1000, 2500, 5000, 10000)),
)
GAUGE_DEFAULT_SCALE = 1
TRACK_DURATION_MS = 250
RESET_DURATION_MS = 600
SCALE_TRANSITION_DURATION_MS = 450


class SpeedGauge(Gtk.DrawingArea):
    """Tachimetro in stile Ookla, interamente disegnato in Cairo.

    API pubblica:
      · property `value`      — valore mostrato dall'ago. È la property animata:
                                si imposta da sola, non va scritta a mano.
      · set_target(mbps)      — valore da raggiungere; l'ago ci arriva interpolando
      · set_phase(phase)      — 'idle' | 'ping' | 'download' | 'upload' | 'done'
      · reset()               — riporta l'ago a zero
      · property `use_accent_color`, `max_value` (sola lettura), `auto_range`

    Tutte le misure del disegno sono frazioni di min(width, height): la resa
    resta identica ridimensionando la finestra e su schermi HiDPI.
    """

    __gtype_name__ = "SpeedGauge"

    PHASES = ("idle", "ping", "download", "upload", "done")

    # Geometria, in frazioni della dimensione minore del widget.
    R_OUTER = 0.470
    RING = 0.068
    TICK_LEN = 0.028
    LABEL_INSET = 0.078
    LABEL_SIZE = 0.042
    NEEDLE_TIP = 0.303
    NEEDLE_TAIL = 0.072
    NEEDLE_HALF = 0.011
    HUB_OUTER = 0.028
    HUB_INNER = 0.013
    VALUE_SIZE = 0.098
    VALUE_OFFSET = 0.223
    UNIT_SIZE = 0.048
    UNIT_OFFSET = 0.335
    VIGNETTE_DITHER_SIZE = 64

    # Ritocchi della scala fino a 1 Gbps. Modifica liberamente le coppie
    # (orizzontale, verticale): ogni unità corrisponde alla larghezza media di
    # una lettera dell'etichetta, con x positivo verso est e y verso sud.
    STANDARD_TICK_OFFSETS = {
        0: (-0.354, 0.354),
        1: (-0.650, 0.140),
        5: (-0.400, -0.125),
        10: (-0.150, -0.250),
        25: (0.000, -0.450),
        50: (0.130, -0.310),
    }

    # Offset in "larghezze di lettera" per le tacche della sola scala 10 Gbps.
    # La scala logaritmica mette alcuni numeri particolarmente vicini ai tagli;
    # questi ritocchi li allontanano senza alterare la geometria delle altre scale.
    EXTENDED_TICK_OFFSETS = {
        0: (-0.354, 0.354),       # mezza lettera a sud-ovest
        1: (-0.462, 0.191),       # mezza lettera verso ovest-sud-ovest
        5: (-0.700, 0.000),       # mezza lettera a ovest
        50: (0.000, -0.250),      # un quarto a nord
        100: (0.000, -0.300),     # un quarto a nord
        300: (-0.088, -0.088),    # un ottavo a nord-ovest
        2500: (-0.500, 0.000),    # mezza lettera a ovest
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._value = 0.0
        self._target = 0.0
        self._phase = "idle"
        self._color_phase = "download"  # fase da cui prendere i colori dell'arco
        self._settling = False  # ritorno a zero fra due fasi in corso
        self._scale_index = GAUGE_DEFAULT_SCALE
        self._scale_from_index = None
        self._scale_progress = 1.0
        self._use_accent = False
        self._auto_range = True
        self._measurement_decimals = 2
        self._vignette_dither_surface = None
        self._vignette_dither_key = None

        # Dimensione naturale: con vexpand il quadrante cresce se c'è spazio.
        self.set_content_width(330)
        self.set_content_height(330)
        self.set_draw_func(self._draw)

        # Animazione: un target sulla property `value`, così l'unica cosa che
        # muove l'ago (e quindi che ridisegna) è il tick dell'animazione.
        self._animation = Adw.TimedAnimation.new(
            self, 0.0, 0.0, TRACK_DURATION_MS, Adw.PropertyAnimationTarget.new(self, "value")
        )
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._animation.connect("done", self._on_animation_done)

        self._scale_animation = Adw.TimedAnimation.new(
            self,
            0.0,
            1.0,
            SCALE_TRANSITION_DURATION_MS,
            Adw.PropertyAnimationTarget.new(self, "scale-progress"),
        )
        self._scale_animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        self._scale_animation.connect("done", self._on_scale_animation_done)

        # Cambi di tema (chiaro/scuro, colore di accento) → ridisegno.
        manager = Adw.StyleManager.get_default()
        known = {spec.name for spec in Adw.StyleManager.list_properties()}
        for name in ("dark", "accent-color"):
            if name in known:
                manager.connect(f"notify::{name}", lambda *_args: self.queue_draw())

    # ------------------------------------------------------------------
    # Property
    # ------------------------------------------------------------------
    @GObject.Property(type=float, default=0.0)
    def value(self):
        """Valore attualmente indicato dall'ago (Mbps)."""
        return self._value

    @value.setter
    def value(self, new_value):
        self._value = float(new_value)
        # Unico queue_draw() legato al valore: ci arriva l'animazione, non gli
        # eventi del test.
        self.queue_draw()

    @GObject.Property(type=bool, default=False)
    def use_accent_color(self):
        """Se True usa il colore di accento del tema invece dei colori Ookla."""
        return self._use_accent

    @use_accent_color.setter
    def use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    @GObject.Property(type=bool, default=True)
    def auto_range(self):
        """Se True la scala sale di livello quando la velocità supera il fondoscala."""
        return self._auto_range

    @auto_range.setter
    def auto_range(self, enabled):
        self._auto_range = bool(enabled)

    @GObject.Property(type=float, default=1000.0, flags=GObject.ParamFlags.READABLE)
    def max_value(self):
        return GAUGE_SCALES[self._scale_index][0]

    @GObject.Property(type=float, default=1.0)
    def scale_progress(self):
        """Avanzamento 0→1 dell'animazione fra due scale del tachimetro."""
        return self._scale_progress

    @scale_progress.setter
    def scale_progress(self, progress):
        self._scale_progress = min(max(float(progress), 0.0), 1.0)
        self.queue_draw()

    # ------------------------------------------------------------------
    # Controllo dell'ago
    # ------------------------------------------------------------------
    def set_target(self, speed):
        """Valore da raggiungere: l'ago ci va interpolando, non di scatto."""
        speed = max(0.0, float(speed))
        self._target = speed
        if self._auto_range:
            self._grow_range_for(speed)
        if self._settling:
            # Stiamo tornando a zero fra due fasi: il valore resta in attesa e
            # verrà inseguito appena l'animazione lenta è finita.
            return
        self._animate_to(speed, TRACK_DURATION_MS, Adw.Easing.EASE_OUT_CUBIC)

    def set_measurement_decimals(self, decimals):
        """Aggiorna la precisione del valore grande senza toccare l'animazione."""
        decimals = min(max(int(decimals), 0), 2)
        if decimals != self._measurement_decimals:
            self._measurement_decimals = decimals
            self.queue_draw()

    def set_phase(self, phase):
        """Fase del test: decide i colori e il ritorno a zero fra una e l'altra."""
        if phase not in self.PHASES or phase == self._phase:
            return
        previous, self._phase = self._phase, phase

        if phase == "idle":
            self._settling = False
            self._target = 0.0
            self._color_phase = "download"
            self._animation.pause()
            self.props.value = 0.0
        elif phase in ("download", "upload"):
            if previous in ("download", "upload") and self._value > 0.5:
                # Si passa da una misura all'altra: l'ago torna a zero più
                # lentamente prima di inseguire la fase nuova. Il colore resta
                # quello della fase appena conclusa finché l'arco non si richiude.
                self._settling = True
                self._animate_to(0.0, RESET_DURATION_MS, Adw.Easing.EASE_IN_OUT_CUBIC)
            else:
                self._color_phase = phase
        elif phase == "done" and self._value > 0.5:
            # Test finito: lo strumento torna a riposo, con l'arco che si
            # richiude nel colore dell'ultima misura.
            self._target = 0.0
            self._settling = True
            self._animate_to(0.0, RESET_DURATION_MS, Adw.Easing.EASE_IN_OUT_CUBIC)

        # Non è un cambio di valore ma di stato: colori ed etichette cambiano.
        self.queue_draw()

    def reset(self):
        """Riporta l'ago a zero e torna alla fase iniziale."""
        self._phase = "ping"  # forza il passaggio di stato in set_phase()
        self.set_phase("idle")

    def _animate_to(self, target_value, duration_ms, easing):
        animation = self._animation
        animation.set_value_from(self._value)  # riparte da dove si trova l'ago
        animation.set_value_to(target_value)
        animation.set_duration(duration_ms)
        animation.set_easing(easing)
        animation.reset()  # niente salti: reset() rimette il valore su value_from
        animation.play()

    def _on_animation_done(self, _animation):
        if not self._settling or self._value > 0.5:
            return  # non è la fine del ritorno a zero
        self._settling = False
        if self._phase in ("download", "upload"):
            self._color_phase = self._phase
        if self._target > 0.0:
            self._animate_to(self._target, TRACK_DURATION_MS, Adw.Easing.EASE_OUT_CUBIC)
        else:
            self.queue_draw()

    def _on_scale_animation_done(self, _animation):
        self._scale_from_index = None
        self.props.scale_progress = 1.0

    def _grow_range_for(self, speed):
        index = self._scale_index
        while index + 1 < len(GAUGE_SCALES) and speed > GAUGE_SCALES[index][0]:
            index += 1
        if index != self._scale_index:
            self._scale_from_index = self._scale_index
            self._scale_index = index
            self.notify("max-value")
            self.props.scale_progress = 0.0
            self._scale_animation.reset()
            self._scale_animation.play()

    # ------------------------------------------------------------------
    # Scala logaritmica
    # ------------------------------------------------------------------
    def _fraction_for_scale(self, speed, scale_index):
        """Posizione 0→1 lungo l'arco.

        log10(1 + v) / log10(1 + fondoscala): con la scala lineare tutto ciò che
        sta sotto i 100 Mbps si schiaccerebbe nel primo decimo dell'arco e l'ago
        sembrerebbe fermo. Il +1 tiene lo zero esattamente a inizio scala.
        """
        top = GAUGE_SCALES[scale_index][0]
        speed = min(max(speed, 0.0), top)
        return math.log10(1.0 + speed) / math.log10(1.0 + top)

    def _fraction(self, speed):
        """Posizione corrente, interpolata mentre il fondoscala si espande."""
        if self._scale_from_index is None:
            return self._fraction_for_scale(speed, self._scale_index)
        before = self._fraction_for_scale(speed, self._scale_from_index)
        after = self._fraction_for_scale(speed, self._scale_index)
        return before + (after - before) * self._scale_progress

    def _angle(self, fraction):
        return math.radians(GAUGE_START_DEG + GAUGE_SWEEP_DEG * fraction)

    # ------------------------------------------------------------------
    # Disegno
    # ------------------------------------------------------------------
    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0

        text = text_rgba(self)
        base = (text.red, text.green, text.blue)
        stops = gradient_stops(self, self._color_phase, self._use_accent)

        r_outer = size * self.R_OUTER
        ring = size * self.RING
        r_mid = r_outer - ring / 2.0
        r_inner = r_outer - ring

        cr.set_line_cap(cairo.LineCap.BUTT)
        cr.set_line_width(ring)

        self._draw_vignette(cr, cx, cy, r_inner, base)
        self._draw_track(cr, cx, cy, r_mid, base)
        fraction = self._fraction(self._value)
        draw_inner_glow(
            cr,
            cx,
            cy,
            r_mid,
            ring,
            stops,
            fraction,
            self._angle(0.0),
            self._angle(fraction),
            self._angle(1.0),
        )
        self._draw_fill(cr, cx, cy, r_mid, stops)
        self._draw_ticks(cr, cx, cy, size, r_inner, base)
        self._draw_needle(cr, cx, cy, size, base)
        self._draw_readout(cr, cx, cy, size, base, stops)

    def _draw_vignette(self, cr, cx, cy, radius, base):
        """Profondità del quadrante, interamente disegnata dal backend Cairo.

        Le fermate seguono una curva più ripida verso il bordo: centro ed
        estremità restano rispettivamente a 0 e 0,035 alpha, ma le poche fasce
        di grigio più visibili diventano più sottili. A differenza del dithering
        raster, non c'è alcun lavoro Python proporzionale ai pixel durante un
        ridimensionamento o l'animazione del layout.
        """
        gradient = cairo.RadialGradient(cx, cy, radius * 0.15, cx, cy, radius)
        for position, alpha in (
            (0.00, 0.000),
            (0.28, 0.002),
            (0.52, 0.008),
            (0.72, 0.016),
            (0.88, 0.026),
            (1.00, 0.035),
        ):
            gradient.add_color_stop_rgba(position, *base, alpha)
        cr.set_source(gradient)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
        self._draw_vignette_dither(cr, cx, cy, radius, base)

    def _draw_vignette_dither(self, cr, cx, cy, radius, base):
        """Dither a un solo livello, ripetuto: elimina le bande senza rallentare.

        La texture misura appena 64×64 px ed è costruita una sola volta per
        tema. Cairo la ripete e la maschera in C, quindi il resize non fa più
        lavoro proporzionale all'area del tachimetro.
        """
        base_key = tuple(round(component, 4) for component in base)
        if base_key != self._vignette_dither_key:
            self._vignette_dither_surface = self._make_vignette_dither_surface(base)
            self._vignette_dither_key = base_key

        pattern = cairo.SurfacePattern(self._vignette_dither_surface)
        pattern.set_extend(cairo.Extend.REPEAT)
        pattern.set_filter(cairo.Filter.NEAREST)
        alpha_mask = cairo.RadialGradient(cx, cy, radius * 0.15, cx, cy, radius)
        for position, alpha in ((0.00, 0.0), (0.20, 1.0), (0.82, 1.0), (1.00, 0.0)):
            alpha_mask.add_color_stop_rgba(position, 0.0, 0.0, 0.0, alpha)

        cr.save()
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.clip()
        cr.set_source(pattern)
        cr.mask(alpha_mask)
        cr.restore()

    def _make_vignette_dither_surface(self, base):
        """Piccola texture di rumore stabile, con un solo livello alpha."""
        size = self.VIGNETTE_DITHER_SIZE
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        words = memoryview(surface.get_data()).cast("I")
        stride_words = surface.get_stride() // 4
        red, green, blue = (round(component) for component in base)

        for y in range(size):
            for x in range(size):
                # Distribuzione pseudocasuale fissa: metà dei pixel illumina
                # appena il livello adiacente, metà resta trasparente.
                noise_word = (
                    (x * 0x1F123BB5) ^ (y * 0x5F356495) ^ ((x + y) * 0x27D4EB2D)
                )
                if ((noise_word >> 16) & 0xFF) >= 128:
                    continue
                if sys.byteorder == "little":
                    words[y * stride_words + x] = blue | (green << 8) | (red << 16) | (1 << 24)
                else:
                    words[y * stride_words + x] = 1 | (red << 8) | (green << 16) | (blue << 24)

        surface.mark_dirty()
        return surface

    def _draw_track(self, cr, cx, cy, radius, base):
        """Traccia dell'arco: colore del testo con alpha bassa."""
        cr.set_source_rgba(*base, 0.13)
        cr.arc(cx, cy, radius, self._angle(0.0), self._angle(1.0))
        cr.stroke()

    def _draw_fill(self, cr, cx, cy, radius, stops):
        """Arco colorato da inizio scala fino al valore corrente.

        Disegnato a segmenti: è il modo più semplice per far seguire la
        sfumatura all'arco invece che a una direzione fissa.
        """
        fraction = self._fraction(self._value)
        if fraction <= 0.0:
            return
        segments = max(2, int(fraction * 110))
        for index in range(segments):
            start = fraction * index / segments
            end = fraction * (index + 1) / segments
            cr.set_source_rgb(*rgb_at(stops, (start + end) / 2.0))
            # micro-sovrapposizione: evita le righine chiare fra un segmento e l'altro
            cr.arc(cx, cy, radius, self._angle(start) - 0.004, self._angle(end) + 0.004)
            cr.stroke()

    def _draw_ticks(self, cr, cx, cy, size, r_inner, base):
        if self._scale_from_index is None:
            for tick in GAUGE_SCALES[self._scale_index][1]:
                geometry = self._tick_geometry(tick, self._scale_index, size, r_inner)
                self._draw_tick(cr, cx, cy, size, base, tick, self._scale_index, geometry)
            return

        # Le tacche condivise scorrono lungo l'arco. Quelle solo della vecchia
        # scala svaniscono, le nuove compaiono gradualmente: niente salti quando
        # si passa dal fondoscala 1G a quello 10G.
        before_index = self._scale_from_index
        before_ticks = set(GAUGE_SCALES[before_index][1])
        after_ticks = set(GAUGE_SCALES[self._scale_index][1])
        progress = self._scale_progress
        for tick in sorted(before_ticks | after_ticks):
            if tick in before_ticks and tick in after_ticks:
                before = self._tick_geometry(tick, before_index, size, r_inner)
                after = self._tick_geometry(tick, self._scale_index, size, r_inner)
                geometry = tuple(
                    before_value + (after_value - before_value) * progress
                    for before_value, after_value in zip(before, after)
                )
                self._draw_tick(cr, cx, cy, size, base, tick, self._scale_index, geometry)
            elif tick in before_ticks:
                geometry = self._tick_geometry(tick, before_index, size, r_inner)
                self._draw_tick(
                    cr, cx, cy, size, base, tick, before_index, geometry, opacity=1.0 - progress
                )
            else:
                geometry = self._tick_geometry(tick, self._scale_index, size, r_inner)
                self._draw_tick(
                    cr, cx, cy, size, base, tick, self._scale_index, geometry, opacity=progress
                )

    def _tick_geometry(self, tick, scale_index, size, r_inner):
        angle = self._angle(self._fraction_for_scale(tick, scale_index))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        outer = r_inner - size * 0.010
        inner = outer - size * self.TICK_LEN
        label_radius = r_inner - size * self.LABEL_INSET
        offset_x, offset_y = self._tick_offset(tick, size, scale_index)
        return (
            cos_a * outer,
            sin_a * outer,
            cos_a * inner,
            sin_a * inner,
            cos_a * label_radius + offset_x,
            sin_a * label_radius + offset_y,
        )

    def _draw_tick(self, cr, cx, cy, size, base, tick, scale_index, geometry, opacity=1.0):
        if opacity <= 0.0:
            return
        outer_x, outer_y, inner_x, inner_y, label_x, label_y = geometry
        cr.set_line_width(max(1.0, size * 0.005))
        cr.set_source_rgba(*base, 0.30 * opacity)
        cr.move_to(cx + outer_x, cy + outer_y)
        cr.line_to(cx + inner_x, cy + inner_y)
        cr.stroke()
        draw_text(
            self,
            cr,
            self._tick_label(tick, scale_index),
            cx + label_x,
            cy + label_y,
            size * self.LABEL_SIZE,
            (*base, 0.80 * opacity),
            weight=Pango.Weight.BOLD,
            tabular=True,
        )

    def _tick_offset(self, tick, size, scale_index=None):
        """Ritocchi ottici delle etichette, esposti nelle due tabelle sopra."""
        if scale_index is None:
            scale_index = self._scale_index
        if GAUGE_SCALES[scale_index][0] <= 1000.0:
            horizontal, vertical = self.STANDARD_TICK_OFFSETS.get(tick, (0.0, 0.0))
        else:
            horizontal, vertical = self.EXTENDED_TICK_OFFSETS.get(tick, (0.0, 0.0))
        letter_width = size * self.LABEL_SIZE * 0.62
        return horizontal * letter_width, vertical * letter_width

    def _tick_label(self, tick, scale_index=None):
        """Etichetta breve per le velocità multi-gigabit della scala estesa."""
        if scale_index is None:
            scale_index = self._scale_index
        if GAUGE_SCALES[scale_index][0] > 1000.0 and tick in (1000, 2500, 5000, 10000):
            decimals = 1 if tick == 2500 else 0
            return "{}G".format(format_number(tick / 1000, decimals))
        return format_number(tick, 0)

    def _draw_needle(self, cr, cx, cy, size, base):
        angle = self._angle(self._fraction(self._value))
        tip = size * self.NEEDLE_TIP
        tail = size * self.NEEDLE_TAIL
        half = size * self.NEEDLE_HALF

        cr.save()
        cr.translate(cx, cy)
        cr.rotate(angle)
        cr.move_to(tip, 0.0)
        cr.line_to(0.0, -half)
        cr.line_to(-tail, 0.0)
        cr.line_to(0.0, half)
        cr.close_path()
        # Sfumatura lungo l'ago: coda smorzata, punta piena.
        needle = cairo.LinearGradient(-tail, 0.0, tip, 0.0)
        needle.add_color_stop_rgba(0.0, *base, 0.30)
        needle.add_color_stop_rgba(1.0, *base, 0.90)
        cr.set_source(needle)
        cr.fill()
        cr.restore()

        cr.set_source_rgba(*base, 0.85)
        cr.arc(cx, cy, size * self.HUB_OUTER, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(*surface_rgb(self))
        cr.arc(cx, cy, size * self.HUB_INNER, 0, 2 * math.pi)
        cr.fill()

    def _draw_readout(self, cr, cx, cy, size, base, stops):
        """Numero grande e unità, sotto l'ago."""
        draw_text(
            self,
            cr,
            format_number(self._value, self._measurement_decimals),
            cx,
            cy + size * self.VALUE_OFFSET,
            size * self.VALUE_SIZE,
            (*base, 1.0),
            weight=Pango.Weight.LIGHT,
            tabular=True,
        )
        unit = _("Mbps")
        unit_layout = pango_layout(self, cr, unit, size * self.UNIT_SIZE)
        unit_width, unit_height = unit_layout.get_pixel_size()
        marker_size = size * 0.044
        marker_gap = size * 0.012
        group_width = marker_size + marker_gap + unit_width
        marker_x = cx - group_width / 2.0 + marker_size / 2.0
        unit_y = cy + size * self.UNIT_OFFSET
        self._draw_readout_marker(cr, marker_x, unit_y, size, stops)
        cr.set_source_rgba(*base, 0.78)
        cr.move_to(marker_x + marker_size / 2.0 + marker_gap, unit_y - unit_height / 2.0)
        PangoCairo.show_layout(cr, unit_layout)

    def _draw_readout_marker(self, cr, cx, cy, size, stops):
        """Freccia colorata che indica a quale misura appartiene il valore live."""
        marker_size = size * 0.044
        radius = marker_size * 0.42
        color = rgb_at(stops, 0.55)
        cr.save()
        cr.new_path()
        cr.set_source_rgb(*color)
        cr.set_line_width(max(1.0, marker_size * 0.085))
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        direction = 1.0 if self._color_phase == "download" else -1.0
        stem = marker_size * 0.20
        head = marker_size * 0.13
        cr.new_path()
        cr.move_to(cx, cy - stem * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.move_to(cx - head, cy + (stem - head) * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.line_to(cx + head, cy + (stem - head) * direction)
        cr.stroke()
        cr.restore()

