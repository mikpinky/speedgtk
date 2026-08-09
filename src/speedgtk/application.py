#!/usr/bin/env python3
"""
SpeedGTK — frontend GTK4 + libadwaita per la CLI ufficiale `speedtest` di Ookla.

Tutto gira sul main loop di GLib: i sottoprocessi sono lanciati con Gio.Subprocess
e lo stdout viene letto riga per riga con Gio.DataInputStream.read_line_async().
Niente modulo `subprocess`, niente thread → la UI non si blocca mai.

Due interfacce, intercambiabili dalle preferenze:
  · tachimetro in stile Ookla disegnato in Cairo (predefinita)
  · label testuali GNOME "pure"  (opzione --plain)

Le stringhe sorgente sono in inglese e le traduzioni stanno nei file po/*.po,
letti direttamente a runtime: non serve né msgfmt né un build system.

Requisiti: GTK 4, libadwaita >= 1.5, PyGObject, e la CLI ufficiale `speedtest`.
"""

import json
import locale
import math
import os
import re
import signal
import sys

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango, PangoCairo  # noqa: E402

APP_ID = "io.github.speedgtk.SpeedGTK"
APP_NAME = "SpeedGTK"
APP_VERSION = "1.9"

BIN = "speedtest"
# Firma stampata da `speedtest --version`: serve a distinguere la CLI ufficiale
# Ookla dal vecchio script Python `speedtest-cli`, che ha CLI e output diversi.
OOKLA_SIGNATURE = "Speedtest by Ookla"
# Flag accettati dalla CLI dopo che l'utente ha dato il consenso esplicito
# nell'app: evitano il prompt interattivo su stdin.
ACCEPT_FLAGS = ["--accept-license", "--accept-gdpr"]
# Il flag accetta 100–1000 ms (verificato con `speedtest --help`).
PROGRESS_INTERVAL_MS = 100
# Al termine del test la barra resta un attimo piena, poi si libera per non
# sembrare il risultato persistente di un test ancora in corso.
PROGRESS_HIDE_DELAY_MS = 600
# Espansioni della pagina: abbastanza lente da rendere fluido il ridimensionamento
# del tachimetro e delle righe sottostanti.
LAYOUT_TRANSITION_DURATION_MS = 600
# I pulsanti che accompagnano il risultato devono comparire rapidamente, senza
# rubare attenzione alla chiusura del test.
RESULT_ACTION_TRANSITION_DURATION_MS = 330
# Secondi di grazia fra SIGTERM e SIGKILL quando si annulla un test.
KILL_GRACE_SECONDS = 3

PLACEHOLDER = "—"

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_PO_DIR = os.path.abspath(os.path.join(PACKAGE_DIR, "..", "..", "po"))
INSTALLED_PO_DIR = os.path.join(os.path.dirname(PACKAGE_DIR), "po")
# Durante lo sviluppo le traduzioni vivono accanto allo script. L'installer le
# copia invece sia lo script sia i cataloghi in $prefix/share/speedgtk. La
# variabile consente inoltre a pacchetti di terze parti di scegliere un percorso
# diverso, senza modificare il codice.
PO_DIR = os.environ.get(
    "SPEEDGTK_PO_DIR",
    SOURCE_PO_DIR if os.path.isdir(SOURCE_PO_DIR) else INSTALLED_PO_DIR,
)

# Preferenze e storico: file JSON nelle directory standard dell'utente.
SETTINGS_PATH = os.path.join(GLib.get_user_config_dir(), "speedgtk", "settings.json")
HISTORY_PATH = os.path.join(GLib.get_user_data_dir(), "speedgtk", "history.json")
HISTORY_LIMIT = 200

# Righe che la CLI scrive su stderr anche quando va tutto bene (informativa
# privacy alla prima esecuzione): non sono errori e non vanno mostrate.
BENIGN_STDERR = (
    "Ookla collects certain data",
    "License acceptance recorded",
    "speedtest.net/privacy",
    "personally identifiable",
    "legitimate interest",
    "faster internet",
    "shared, where the data",
    "please see our Privacy Policy",
    "industry regulators",
    "identifiers or location",
)


# ---------------------------------------------------------------------------
# Traduzioni
# ---------------------------------------------------------------------------
def N_(message):
    """Marca una stringa per l'estrazione; la traduzione avviene poi con _()."""
    return message


HISTORY_SORTS = (
    ("date", N_("Sort by date (default)")),
    ("download", N_("Best download")),
    ("upload", N_("Best upload")),
    ("ping", N_("Best ping")),
    ("overall", N_("Best overall")),
)

THEME_OPTIONS = (
    ("system", N_("Same as the system")),
    ("light", N_("Light")),
    ("dark", N_("Dark")),
)

# La maggior parte dell'uso quotidiano della connessione dipende dal download,
# ma l'upload resta abbastanza rilevante da incidere sulla classifica finale.
OVERALL_DOWNLOAD_WEIGHT = 0.7
OVERALL_UPLOAD_WEIGHT = 0.3


def po_unquote(token):
    """Toglie le virgolette e le sequenze di escape di una stringa .po."""
    token = token.strip()
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        token = token[1:-1]
    return (
        token.replace("\\\\", "\x00")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\x00", "\\")
    )


def parse_po(text):
    """Parser .po minimo: msgid/msgstr, stringhe su più righe, fuzzy saltate.

    Basta per questa app, che non usa né plurali né contesti, ed evita di
    dover compilare i .po in .mo con msgfmt.
    """
    catalog = {}
    msgid = None
    msgstr = None
    where = None  # "id" oppure "str": dove finiscono le righe di continuazione
    fuzzy = False
    next_fuzzy = False

    def store():
        if msgid and msgstr and not fuzzy:
            catalog[msgid] = msgstr

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                next_fuzzy = True
            continue
        if line.startswith("msgid "):
            store()  # la voce precedente è finita
            msgid, msgstr, where = po_unquote(line[6:]), None, "id"
            fuzzy, next_fuzzy = next_fuzzy, False
        elif line.startswith("msgstr "):
            msgstr, where = po_unquote(line[7:]), "str"
        elif line.startswith('"') and where == "id":
            msgid += po_unquote(line)
        elif line.startswith('"') and where == "str":
            msgstr += po_unquote(line)
        else:
            # msgctxt, msgid_plural, msgstr[n]: non usati, la voce si scarta
            where = None
    store()
    return catalog


class Translations:
    """Catalogo di traduzioni caricato dai file .po presenti in `directory`."""

    SOURCE_CODE = "en"  # lingua in cui sono scritti i msgid

    def __init__(self, directory=PO_DIR):
        self._directory = directory
        self._catalog = {}
        self._code = self.SOURCE_CODE
        self._requested_code = self.SOURCE_CODE

    @property
    def code(self):
        return self._code

    @property
    def follows_system(self):
        """True quando l'utente ha scelto di seguire la lingua di sistema."""
        return self._requested_code == "system"

    def available(self):
        """Codici lingua utilizzabili: l'inglese più un codice per ogni .po."""
        codes = {self.SOURCE_CODE}
        try:
            for name in os.listdir(self._directory):
                if name.endswith(".po"):
                    codes.add(name[: -len(".po")])
        except OSError:
            pass
        return codes

    def use(self, code):
        """Attiva una lingua; "system" segue le impostazioni di sistema."""
        self._requested_code = code or "system"
        if not code or code == "system":
            code = self._system_code()
        self._code = code
        self._catalog = {} if code == self.SOURCE_CODE else self._load(code)
        return code

    def _system_code(self):
        available = self.available()
        for name in GLib.get_language_names():
            code = name.split(".")[0].split("_")[0].lower()
            if code in available:
                return code
        return self.SOURCE_CODE

    def _load(self, code):
        try:
            with open(os.path.join(self._directory, f"{code}.po"), encoding="utf-8") as handle:
                return parse_po(handle.read())
        except OSError:
            return {}

    def gettext(self, message):
        return self._catalog.get(message) or message


TRANSLATIONS = Translations()


def _(message):
    return TRANSLATIONS.gettext(message)


# Lingue offerte nelle preferenze, nell'ordine in cui compaiono. I nomi sono
# tradotti come tutto il resto: nell'app in tedesco si legge "Italienisch".
LANGUAGE_ORDER = ("system", "it", "en", "de", "fr", "es", "ru")


def language_names():
    return {
        "system": _("Same as the system"),
        "it": _("Italian"),
        "en": _("English"),
        "de": _("German"),
        "fr": _("French"),
        "es": _("Spanish"),
        "ru": _("Russian"),
    }


# Errori della CLI: (frammento da cercare, riga breve per il toast, spiegazione).
# La riga breve deve stare in un toast senza troncarsi, i dettagli vanno nel
# dialogo che si apre dal pulsante "Details".
CLI_ERROR_HINTS = (
    (
        "too many requests",
        N_("Too many tests in a short time"),
        N_(
            "Ookla is temporarily limiting this connection because too many tests "
            "were run in a short time. Wait a few minutes before repeating it."
        ),
    ),
    (
        "no servers",
        N_("No test server available"),
        N_(
            "Ookla returned no usable server. Refresh the list, or pick a "
            "different server, and try again."
        ),
    ),
    (
        "could not retrieve or read configuration",
        N_("Cannot reach Ookla's servers"),
        N_(
            "The configuration service could not be reached. Check that the "
            "connection is working and that no proxy or firewall is blocking it."
        ),
    ),
    (
        "name resolution",
        N_("Name resolution failed"),
        N_("The server name could not be resolved: this usually points at a DNS problem."),
    ),
    (
        "cannot resolve",
        N_("Name resolution failed"),
        N_("The server name could not be resolved: this usually points at a DNS problem."),
    ),
    (
        "timeout",
        N_("The connection timed out"),
        N_("The test server did not answer in time. It may be overloaded: try another one."),
    ),
    (
        "cannot open socket",
        N_("Cannot connect to the test server"),
        N_("The connection to the chosen server could not be opened. Try another server."),
    ),
    (
        "socket error",
        N_("Cannot connect to the test server"),
        N_("The connection to the chosen server could not be opened. Try another server."),
    ),
    (
        "unable to connect",
        N_("Cannot connect to the test server"),
        N_("The connection to the chosen server could not be opened. Try another server."),
    ),
    (
        "forbidden",
        N_("Request refused by the server"),
        N_("The test server refused the request. Try a different server."),
    ),
    (
        "interrupted",
        N_("Test interrupted"),
        N_("The test ended early. Check that the connection stayed up."),
    ),
)


def humanize_cli_error(raw_message):
    """(riga breve tradotta, spiegazione tradotta o None) per un errore della CLI."""
    lowered = (raw_message or "").lower()
    for needle, short, detail in CLI_ERROR_HINTS:
        if needle in lowered:
            return _(short), _(detail)
    if raw_message:
        return raw_message, None
    return _("speedtest reported an unspecified error"), None


# --- Tachimetro -----------------------------------------------------------

# L'arco va da 135° a 405° in coordinate Cairo (y verso il basso, quindi angoli
# crescenti = senso orario): parte in basso a sinistra, passa dall'alto e
# finisce in basso a destra. 270° in tutto.
GAUGE_START_DEG = 135.0
GAUGE_SWEEP_DEG = 270.0

# Scale disponibili: (fondoscala, tacche etichettate). Si parte dalla seconda,
# quella chiesta; con `auto_range` si sale di scala se la velocità la supera —
# senza, su una linea multi-gigabit l'ago resterebbe piantato a fondoscala.
GAUGE_SCALES = (
    (100.0, (0, 1, 5, 10, 25, 50, 100)),
    (1000.0, (0, 1, 5, 10, 25, 50, 100, 250, 500, 1000)),
    (10000.0, (0, 1, 5, 10, 20, 50, 100, 300, 500, 1000, 2500, 5000, 10000)),
)
GAUGE_DEFAULT_SCALE = 1

# Durata dell'inseguimento del valore corrente e del ritorno a zero fra le fasi.
TRACK_DURATION_MS = 250
RESET_DURATION_MS = 600
SCALE_TRANSITION_DURATION_MS = 450

# Sfumature Ookla: azzurro → verde acqua → verde per il download, violetto per
# l'upload. Ogni stop è (posizione sulla scala 0→1, (r, g, b)).
STOPS_DOWNLOAD = ((0.0, (0.07, 0.64, 0.96)), (0.55, (0.25, 0.92, 0.80)), (1.0, (0.43, 0.94, 0.48)))
STOPS_UPLOAD = ((0.0, (0.42, 0.29, 0.90)), (0.55, (0.66, 0.36, 0.95)), (1.0, (0.85, 0.47, 0.98)))

try:  # i numeri seguono le convenzioni locali (2.208,06 in italiano)
    locale.setlocale(locale.LC_NUMERIC, "")
except locale.Error:
    pass

# Convenzioni per le lingue offerte dall'app: (separatore decimale, migliaia).
# Il formato di partenza di Python è sempre inglese e viene convertito in
# format_number(); usarle qui evita di dipendere dalla lingua di Ubuntu.
NUMBER_SEPARATORS = {
    "en": (".", ","),
    "it": (",", "."),
    "de": (",", "."),
    "es": (",", "."),
    "fr": (",", "\u202f"),  # spazio sottile inseparabile
    "ru": (",", "\u00a0"),  # spazio inseparabile
}


class Settings:
    """Preferenze persistite in un JSON. Salvataggio a ogni modifica."""

    DEFAULTS = {
        "plain_ui": False,
        "accent_colors": False,
        "auto_range": True,
        "measurement_decimals": 2,
        "color_scheme": "system",
        "keep_history": True,
        "language": "system",
        "ookla_terms_accepted": False,
        "last_auto_server": None,  # descrizione dell'ultimo server scelto in automatico
    }

    def __init__(self, path=SETTINGS_PATH):
        self._path = path
        self._values = dict(self.DEFAULTS)
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(stored, dict):
            self._values.update({k: v for k, v in stored.items() if k in self.DEFAULTS})

    def __getitem__(self, key):
        return self._values.get(key, self.DEFAULTS.get(key))

    def override(self, key, value):
        """Cambia il valore solo per questa sessione (opzioni da riga di comando)."""
        self._values[key] = value

    def set(self, key, value):
        if self._values.get(key) == value:
            return
        self._values[key] = value
        self.save()

    def save(self):
        try:
            GLib.mkdir_with_parents(os.path.dirname(self._path), 0o700)
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._values, handle, indent=1, ensure_ascii=False)
        except OSError:
            pass  # preferenze non salvabili: non è un motivo per disturbare l'utente


class History:
    """Storico dei test riusciti, dal più recente al più vecchio."""

    def __init__(self, path=HISTORY_PATH, limit=HISTORY_LIMIT):
        self._path = path
        self._limit = limit
        self._entries = []
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(stored, list):
            self._entries = [entry for entry in stored if isinstance(entry, dict)][:limit]

    @property
    def path(self):
        return self._path

    @property
    def entries(self):
        return list(self._entries)

    def add(self, entry):
        self._entries.insert(0, entry)
        del self._entries[self._limit :]
        self._save()

    def clear(self):
        self._entries = []
        self._save()

    def _save(self):
        try:
            GLib.mkdir_with_parents(os.path.dirname(self._path), 0o700)
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._entries, handle, indent=1, ensure_ascii=False)
        except OSError:
            pass


class ServerItem(GObject.Object):
    """Voce dell'elenco server: `label` per la riga chiusa, title/subtitle nel menu."""

    __gtype_name__ = "ServerItem"

    label = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    subtitle = GObject.Property(type=str, default="")

    def __init__(self, label, title, subtitle="", server_id=None):
        super().__init__()
        self.props.label = label
        self.props.title = title
        self.props.subtitle = subtitle
        self.server_id = server_id  # None = scelta automatica


def mbps(bandwidth_bytes_per_second):
    """La CLI Ookla esprime `bandwidth` in BYTE al secondo.

    I Mbps si ottengono moltiplicando per 8 (bit) e dividendo per 1e6, cioè in
    base decimale — è la stessa convenzione usata da speedtest.net.
    """
    return bandwidth_bytes_per_second * 8 / 1e6


def format_number(value, decimals=2):
    """Formatta un numero secondo la lingua scelta nell'app.

    LC_NUMERIC descrive la lingua del sistema, non necessariamente quella
    selezionata nelle preferenze di SpeedGTK: per esempio, un'app in inglese
    su Ubuntu in italiano deve mostrare 1,234.56 e non 1.234,56. Solo
    l'opzione "Same as the system" continua quindi a consultare la locale.
    """
    try:
        rendered = f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)

    if TRANSLATIONS.follows_system:
        convention = locale.localeconv()
        decimal = convention.get("decimal_point") or "."
        grouping = convention.get("thousands_sep") or ""
    else:
        decimal, grouping = NUMBER_SEPARATORS.get(TRANSLATIONS.code, NUMBER_SEPARATORS["en"])

    # Il formato Python è volutamente il punto di partenza canonico inglese;
    # i due rimpiazzi con un segnaposto evitano che punto e virgola si pestino.
    return rendered.replace(",", "\x00").replace(".", decimal).replace("\x00", grouping)


def format_timestamp(iso_text):
    """ "2026-08-08T09:27:46Z" → data e ora locali leggibili."""
    stamp = GLib.DateTime.new_from_iso8601(iso_text or "", None)
    if stamp is None:
        return iso_text or PLACEHOLDER
    # Formato traducibile: ogni lingua può disporre giorno e mese a modo suo.
    return stamp.to_local().format(_("%d/%m/%Y %H:%M"))


def clean_version(version_output):
    """Prima riga di `speedtest --version` ridotta all'essenziale."""
    match = re.search(r"Speedtest by Ookla\s+([0-9][0-9.]*)", version_output or "")
    if match:
        return "{} {}".format(_("Speedtest CLI"), match.group(1))
    return _("Speedtest CLI")


def call_later(func, *args):
    """Esegue `func(*args)` al prossimo giro di main loop."""

    def _once():
        func(*args)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(_once)


def is_cancelled(err):
    return err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)


def extract_cli_error(stdout_text, stderr_text):
    """Cerca un messaggio d'errore leggibile nell'output della CLI.

    Prima nelle righe JSON di stdout (la 1.2 riporta gli errori lì), poi
    nell'ultima riga utile di stderr, saltando l'informativa GDPR.
    """
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (event.get("type") == "error" or event.get("level") == "error"):
            message = event.get("message") or event.get("error")
            if message:
                return str(message)

    for line in reversed(stderr_text.splitlines()):
        line = line.strip()
        if not line or set(line) <= {"=", "-", "*"}:
            continue
        if any(noise in line for noise in BENIGN_STDERR):
            continue
        return line
    return ""


def run_and_capture(argv, callback, cancellable=None):
    """Lancia `argv` e ne cattura stdout/stderr in modo asincrono.

    Chiama `callback(status, stdout, stderr)` sul main loop a fine processo.
    `status < 0` significa "non è stato possibile eseguire il comando".
    """
    try:
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )
    except GLib.Error as err:
        call_later(callback, -1, "", err.message)
        return None

    def _on_done(process, result):
        try:
            _ok, out, errout = process.communicate_utf8_finish(result)
        except GLib.Error as err:
            if not is_cancelled(err):
                callback(-1, "", err.message)
            return
        status = process.get_exit_status() if process.get_if_exited() else -1
        callback(status, out or "", errout or "")

    proc.communicate_utf8_async(None, cancellable, _on_done)
    return proc


# ---------------------------------------------------------------------------
# Colori: presi dallo stile del widget, così il disegno segue il tema di sistema
# ---------------------------------------------------------------------------
def text_rgba(widget):
    """Colore del testo corrente (chiaro/scuro lo decide il tema)."""
    return widget.get_color()


def surface_rgb(_widget):
    """Colore di "fondo" approssimato, per i pieni sopra cui passa l'ago."""
    dark = Adw.StyleManager.get_default().get_dark()
    return (0.09, 0.09, 0.10) if dark else (0.99, 0.99, 0.99)


def accent_rgb(widget):
    """Colore di accento pieno del sistema, con ripiego sulle API meno recenti."""
    manager = Adw.StyleManager.get_default()
    if hasattr(manager, "get_accent_color"):
        accent = manager.get_accent_color()
        # to_standalone_rgba() aumenta intenzionalmente il contrasto: in tema
        # scuro trasforma rosso/magenta in un rosa chiaro. Per frecce e arco
        # disegnati su misura vogliamo invece il colore saturo scelto dal tema.
        rgba = accent.to_rgba() if hasattr(accent, "to_rgba") else accent.to_standalone_rgba(False)
        return (rgba.red, rgba.green, rgba.blue)
    ok, rgba = widget.get_style_context().lookup_color("accent_color")
    if ok:
        return (rgba.red, rgba.green, rgba.blue)
    return (0.21, 0.52, 0.89)


def shade(rgb, factor):
    """Schiarisce (factor > 1) o scurisce (factor < 1) un colore."""
    if factor <= 1.0:
        return tuple(component * factor for component in rgb)
    return tuple(component + (1.0 - component) * (factor - 1.0) for component in rgb)


def gradient_stops(widget, phase, use_accent):
    """Stop della sfumatura per la fase indicata.

    Con `use_accent` la sfumatura è costruita attorno al colore di accento del
    tema, altrimenti si usano i colori Ookla.
    """
    if use_accent:
        base = accent_rgb(widget)
        # Manteniamo profondità senza mescolare il colore con il bianco: quello
        # renderebbe l'accento troppo pastello, soprattutto sul tema scuro.
        return ((0.0, shade(base, 0.82)), (0.55, base), (1.0, shade(base, 0.92)))
    return STOPS_UPLOAD if phase == "upload" else STOPS_DOWNLOAD


def rgb_at(stops, position):
    """Interpola gli stop di una sfumatura nel punto `position` (0→1)."""
    position = min(max(position, 0.0), 1.0)
    for (start, first), (end, second) in zip(stops, stops[1:]):
        if position <= end:
            k = 0.0 if end == start else (position - start) / (end - start)
            return tuple(first[i] + (second[i] - first[i]) * k for i in range(3))
    return stops[-1][1]


def pango_layout(widget, cr, text, pixel_size, weight=Pango.Weight.NORMAL, tabular=False):
    """Layout Pango dimensionato in pixel del widget (regge bene l'HiDPI)."""
    layout = PangoCairo.create_layout(cr)
    description = widget.get_pango_context().get_font_description().copy()
    # set_absolute_size() lavora in unità del contesto Cairo, quindi la
    # dimensione resta proporzionale al widget qualunque sia il DPI.
    description.set_absolute_size(pixel_size * Pango.SCALE)
    description.set_weight(weight)
    layout.set_font_description(description)
    if tabular:
        # cifre a larghezza fissa: senza, il numero animato "balla"
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_font_features_new("tnum=1"))
        layout.set_attributes(attributes)
    layout.set_text(text, -1)
    return layout


def draw_text(widget, cr, text, x, y, pixel_size, rgba, weight=Pango.Weight.NORMAL, tabular=False):
    """Scrive `text` centrato in (x, y). Niente cr.show_text(): solo Pango."""
    layout = pango_layout(widget, cr, text, pixel_size, weight, tabular)
    width, height = layout.get_pixel_size()
    cr.set_source_rgba(*rgba)
    cr.move_to(x - width / 2.0, y - height / 2.0)
    PangoCairo.show_layout(cr, layout)


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
        self._draw_inner_glow(cr, cx, cy, r_mid, ring, stops)
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

    def _draw_inner_glow(self, cr, cx, cy, radius, ring, stops):
        """Alone colorato che sfuma dall'interno dell'arco verso il quadrante.

        Una maschera radiale continua sostituisce gli archi concentrici: il
        bordo resta morbido anche su display molto nitidi, senza rendere visibili
        livelli distinti. Il bagliore si spinge all'interno per 1,25 spessori
        d'arco: abbastanza da restare chiaramente leggibile senza sembrare un
        secondo anello.
        """
        fraction = self._fraction(self._value)
        if fraction <= 0.0:
            return

        cr.save()
        start_angle = self._angle(0.0)
        end_angle = self._angle(fraction)
        # L'estremità esterna invade appena il pieno: _draw_fill() la copre
        # subito dopo e lascia il picco di luminosità esattamente al suo bordo
        # interno. Quella interna porta il glow a 1,25 spessori oltre il bordo
        # dell'arco, così si legge bene anche sui temi scuri.
        glow_outer_radius = radius - ring * 0.30
        glow_inner_radius = radius - ring * (0.50 + 1.25)

        # Limitiamo il gradiente al solo settore attivo dell'anello, evitando
        # qualunque alone fuori dall'arco o dietro le tacche.
        cr.arc(cx, cy, glow_outer_radius, start_angle, end_angle)
        cr.arc_negative(cx, cy, glow_inner_radius, end_angle, start_angle)
        cr.close_path()
        cr.clip()

        gradient_start = (
            cx + radius * math.cos(start_angle),
            cy + radius * math.sin(start_angle),
        )
        gradient_end = (
            cx + radius * math.cos(self._angle(1.0)),
            cy + radius * math.sin(self._angle(1.0)),
        )
        color_gradient = cairo.LinearGradient(*gradient_start, *gradient_end)
        for position, color in stops:
            color_gradient.add_color_stop_rgb(position, *color)
        cr.set_source(color_gradient)

        alpha_mask = cairo.RadialGradient(
            cx, cy, glow_inner_radius, cx, cy, glow_outer_radius
        )
        for position, alpha in (
            (0.00, 0.0),
            (0.30, 0.020),
            (0.58, 0.120),
            (0.80, 0.340),
            (1.00, 0.600),
        ):
            alpha_mask.add_color_stop_rgba(position, 0.0, 0.0, 0.0, alpha)
        cr.mask(alpha_mask)
        cr.restore()

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


class PhaseIcon(Gtk.DrawingArea):
    """Freccia in un cerchio: grigia finché la sua fase non entra in gioco.

    Disegnata in Cairo per poterle dare i colori Ookla senza CSS custom (le
    icone simboliche prenderebbero il colore dal foglio di stile).
    """

    __gtype_name__ = "PhaseIcon"

    def __init__(self, phase, size=22, **kwargs):
        super().__init__(**kwargs)
        self._phase = phase  # 'download' | 'upload'
        self._active = False
        self._use_accent = False
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_active(self, active):
        if active != self._active:
            self._active = bool(active)
            self.queue_draw()

    def set_use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0
        text = text_rgba(self)

        if self._active:
            color = (*rgb_at(gradient_stops(self, self._phase, self._use_accent), 0.5), 1.0)
        else:
            color = (text.red, text.green, text.blue, 0.35)

        cr.set_source_rgba(*color)
        cr.set_line_width(max(1.0, size * 0.085))
        radius = size * 0.42
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        direction = 1.0 if self._phase == "download" else -1.0
        stem = size * 0.20
        head = size * 0.13
        cr.move_to(cx, cy - stem * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.stroke()
        cr.move_to(cx - head, cy + (stem - head) * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.line_to(cx + head, cy + (stem - head) * direction)
        cr.stroke()


class LatencyIcon(Gtk.DrawingArea):
    """Indicatore della latenza idle, in download o in upload.

    Il ping idle usa le frecce orizzontali gialle; durante i trasferimenti le
    frecce verticali riusano i colori verde acqua e violetto delle rispettive
    intestazioni. Rimangono attenuati finché non arriva una misura.
    """

    __gtype_name__ = "LatencyIcon"

    IDLE_COLOR = (0.90, 0.76, 0.00)

    def __init__(self, phase, size=22, **kwargs):
        super().__init__(**kwargs)
        self._phase = phase  # 'idle' | 'download' | 'upload'
        self._active = False
        self._use_accent = False
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_active(self, active):
        if active != self._active:
            self._active = bool(active)
            self.queue_draw()

    def set_use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0
        text = text_rgba(self)

        if self._active:
            if self._phase == "idle" and not self._use_accent:
                color = (*self.IDLE_COLOR, 1.0)
            else:
                color = (*rgb_at(gradient_stops(self, self._phase, self._use_accent), 0.5), 1.0)
        else:
            color = (text.red, text.green, text.blue, 0.35)

        cr.set_source_rgba(*color)
        cr.set_line_width(max(1.0, size * 0.085))
        radius = size * 0.42
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        stem = size * 0.20
        head = size * 0.13
        if self._phase == "idle":
            cr.move_to(cx - stem, cy)
            cr.line_to(cx + stem, cy)
            cr.move_to(cx - stem + head, cy - head)
            cr.line_to(cx - stem, cy)
            cr.line_to(cx - stem + head, cy + head)
            cr.move_to(cx + stem - head, cy - head)
            cr.line_to(cx + stem, cy)
            cr.line_to(cx + stem - head, cy + head)
        else:
            direction = 1.0 if self._phase == "download" else -1.0
            cr.move_to(cx, cy - stem * direction)
            cr.line_to(cx, cy + stem * direction)
            cr.move_to(cx - head, cy + (stem - head) * direction)
            cr.line_to(cx, cy + stem * direction)
            cr.line_to(cx + head, cy + (stem - head) * direction)
        cr.stroke()


class DetailIcon(Gtk.DrawingArea):
    """Icona cerchiata per i dettagli di server e provider del risultato."""

    __gtype_name__ = "DetailIcon"

    def __init__(self, kind, size=42, **kwargs):
        super().__init__(**kwargs)
        self._kind = kind  # 'server' | 'isp'
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    @staticmethod
    def _rounded_rectangle(cr, x, y, width, height, radius):
        radius = min(radius, width / 2.0, height / 2.0)
        cr.new_sub_path()
        cr.arc(x + width - radius, y + radius, radius, -math.pi / 2.0, 0.0)
        cr.arc(x + width - radius, y + height - radius, radius, 0.0, math.pi / 2.0)
        cr.arc(x + radius, y + height - radius, radius, math.pi / 2.0, math.pi)
        cr.arc(x + radius, y + radius, radius, math.pi, 3.0 * math.pi / 2.0)
        cr.close_path()

    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0
        text = text_rgba(self)

        cr.set_source_rgba(text.red, text.green, text.blue, 0.38)
        cr.set_line_width(max(0.75, size * 0.024))
        cr.arc(cx, cy, size * 0.44, 0, 2 * math.pi)
        cr.stroke()

        cr.set_source_rgba(text.red, text.green, text.blue, 0.72)
        cr.set_line_width(max(0.75, size * 0.032))
        if self._kind == "isp":
            cr.arc(cx, cy - size * 0.105, size * 0.125, 0, 2 * math.pi)
            cr.stroke()
            # Busto più basso della testa e schiacciato: la linea resta aperta
            # e non si sovrappone al cerchio del volto.
            cr.save()
            cr.translate(cx, cy + size * 0.235)
            cr.scale(1.0, 0.55)
            cr.new_path()
            cr.arc(0.0, 0.0, size * 0.235, math.pi, 2.0 * math.pi)
            cr.restore()
            cr.stroke()
            return

        # Server: tre piccoli nodi, uno centrale sopra due affiancati.
        # Riduciamo sia ciascun server sia il gruppo attorno al suo centro,
        # così gli angoli inferiori non sfiorano il bordo del cerchio.
        box_width = size * 0.23
        box_height = size * 0.195
        box_radius = size * 0.028
        group_scale = 0.80
        for center_x, center_y in (
            (cx, cy - size * 0.110 * group_scale),
            (cx - size * 0.170 * group_scale, cy + size * 0.170 * group_scale),
            (cx + size * 0.170 * group_scale, cy + size * 0.170 * group_scale),
        ):
            x = center_x - box_width / 2.0
            y = center_y - box_height / 2.0
            self._rounded_rectangle(cr, x, y, box_width, box_height, box_radius)
            cr.stroke()
            cr.new_path()
            cr.move_to(x + size * 0.035, y + box_height * 0.52)
            cr.line_to(x + box_width - size * 0.035, y + box_height * 0.52)
            cr.stroke()


class PhaseProgress(Gtk.DrawingArea):
    """Barra di avanzamento con la sfumatura della fase in corso.

    Una Gtk.ProgressBar userebbe il colore di accento del tema; qui serve il
    verde acqua del download e il violetto dell'upload, e l'accento solo se
    l'utente lo ha scelto nelle preferenze.
    """

    __gtype_name__ = "PhaseProgress"

    HEIGHT = 5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fraction = 0.0
        self._phase = "download"
        self._use_accent = False
        self.set_content_height(self.HEIGHT)
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction):
        fraction = min(max(float(fraction), 0.0), 1.0)
        if fraction != self._fraction:
            self._fraction = fraction
            self.queue_draw()

    def get_fraction(self):
        return self._fraction

    def set_phase(self, phase):
        if phase in ("download", "upload"):
            new_phase = phase
        elif phase in ("idle", "ping"):
            new_phase = "download"  # il ping usa i colori del download, come Ookla
        else:
            return  # 'done': la barra piena resta del colore dell'ultima misura
        if new_phase != self._phase:
            self._phase = new_phase
            self.queue_draw()

    def set_use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        text = text_rgba(self)
        cr.set_source_rgba(text.red, text.green, text.blue, 0.10)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        filled = width * self._fraction
        if filled <= 0.0:
            return
        # La sfumatura è distesa su tutta la larghezza e poi ritagliata: così il
        # colore dipende dal punto della barra, non da quanto è piena.
        gradient = cairo.LinearGradient(0, 0, width, 0)
        for position, rgb in gradient_stops(self, self._phase, self._use_accent):
            gradient.add_color_stop_rgb(position, *rgb)
        cr.set_source(gradient)
        cr.rectangle(0, 0, filled, height)
        cr.fill()


class SpeedtestRun:
    """Una esecuzione di `speedtest --format=jsonl`, letta riga per riga.

    `on_event(dict)` viene chiamata per ogni oggetto JSON di stdout;
    `on_done(status, stderr_text, cancelled)` una sola volta, quando il
    processo è uscito ed entrambe le pipe sono a EOF.
    """

    def __init__(self, argv, on_event, on_done):
        self._on_event = on_event
        self._on_done = on_done
        self._stderr_lines = []
        self._cancelled = False
        self._finished = False
        # Ci servono tre segnali di completamento: EOF su stdout, EOF su stderr
        # e la terminazione del processo. Solo allora chiamiamo on_done().
        self._pending = 3

        # Può sollevare GLib.Error se il binario sparisce fra il check e qui.
        self._proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )

        stdout = Gio.DataInputStream.new(self._proc.get_stdout_pipe())
        stderr = Gio.DataInputStream.new(self._proc.get_stderr_pipe())
        self._read_next(stdout, self._handle_stdout_line)
        self._read_next(stderr, self._stderr_lines.append)

        # Nessun Gio.Cancellable sulle letture: annullare vuol dire terminare il
        # processo, e a quel punto le pipe vanno a EOF da sole. Smettere di
        # leggere mentre il figlio scrive lo bloccherebbe su una pipe piena.
        self._proc.wait_async(None, self._on_wait_done)

    # ------------------------------------------------------------------
    # Lettura asincrona delle pipe
    # ------------------------------------------------------------------
    def _read_next(self, stream, handler):
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, handler)

    def _on_line(self, stream, result, handler):
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None  # errore di lettura: lo trattiamo come fine stream
        if line is None:
            self._step()  # EOF
            return
        handler(line)
        self._read_next(stream, handler)

    def _handle_stdout_line(self, line):
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            return  # righe non JSON (banner, warning): ignorate
        if isinstance(event, dict):
            self._on_event(event)

    # ------------------------------------------------------------------
    # Terminazione
    # ------------------------------------------------------------------
    def cancel(self):
        """Annullamento pulito: SIGTERM, con SIGKILL di riserva."""
        if self._finished or self._cancelled:
            return
        self._cancelled = True
        self._proc.send_signal(signal.SIGTERM)
        GLib.timeout_add_seconds(KILL_GRACE_SECONDS, self._force_exit)

    def kill(self):
        """Terminazione immediata (usata alla chiusura della finestra)."""
        if not self._finished:
            self._cancelled = True
            self._proc.force_exit()

    def _force_exit(self):
        if not self._finished:
            self._proc.force_exit()
        return GLib.SOURCE_REMOVE

    def _on_wait_done(self, process, result):
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        self._step()

    def _step(self):
        self._pending -= 1
        if self._pending > 0 or self._finished:
            return
        self._finished = True
        # get_exit_status() è valido solo se il processo è uscito da sé: se è
        # stato terminato da un segnale (annullamento) usiamo -1.
        status = self._proc.get_exit_status() if self._proc.get_if_exited() else -1
        self._on_done(status, "\n".join(self._stderr_lines), self._cancelled)


class SpeedGTKWindow(Adw.ApplicationWindow):
    def __init__(self, application, settings, history):
        super().__init__(application=application, title=APP_NAME)
        # Le dimensioni GTK sono in pixel logici: il compositor applica il
        # fattore di scala del monitor, quindi questi 984 px (+20%) restano
        # proporzionati sia su display standard sia su schermi HiDPI/4K.
        self.set_default_size(560, 984)

        self._settings = settings
        self._history = history
        self._run = None  # SpeedtestRun in corso (None = nessun test attivo)
        self._servers_cancellable = None
        self._last_error = None  # messaggio dell'ultimo evento di errore
        self._phase = "idle"  # fase corrente, per il tachimetro
        self._live = {"download": None, "upload": None}  # ultimi valori visti
        self._latencies = {"idle": None, "download": None, "upload": None}
        self._jitter = None
        self._loss = None
        self._result_url = None
        self._auto_server = True  # il test in corso usa la scelta automatica?
        self._updating_servers = False  # ricostruzione dell'elenco in corso
        self._has_run = False  # almeno un test concluso in questa finestra
        self._progress_hide_source = None  # timer della barra al termine del test
        self._result_action_reveal_source = None

        self._toasts = Adw.ToastOverlay()
        self.set_content(self._toasts)

        self._window_title = Adw.WindowTitle.new(APP_NAME, "")
        header = Adw.HeaderBar()
        header.set_title_widget(self._window_title)

        self._refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Refresh the server list")
        )
        self._refresh_button.set_sensitive(False)
        self._refresh_button.connect("clicked", lambda *_args: self._load_servers())
        header.pack_start(self._refresh_button)

        menu = Gio.Menu()
        menu.append(_("History…"), "win.history")
        menu.append(_("Preferences…"), "win.preferences")
        menu.append(_("About"), "win.about")
        header.pack_end(
            Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=_("Menu"))
        )
        for name, callback in (
            ("history", self._present_history),
            ("preferences", self._present_preferences),
            ("about", self._present_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        # Azione richiamata dal pulsante "Details" del toast d'errore: il testo
        # completo viaggia come parametro, così non serve tenerlo da parte.
        details = Gio.SimpleAction.new("error-details", GLib.VariantType.new("s"))
        details.connect("activate", lambda _action, param: self._present_error(param.get_string()))
        self.add_action(details)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._build_loading_page(), "loading")
        self._stack.add_named(self._build_main_page(), "main")
        self._unavailable_page = Adw.StatusPage()
        self._stack.add_named(self._unavailable_page, "unavailable")

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._stack)
        self._toasts.set_child(view)

        self._apply_appearance()
        self.connect("close-request", self._on_close_request)
        if self._settings["ookla_terms_accepted"]:
            self._check_binary()
        else:
            self._present_ookla_terms()

    # ------------------------------------------------------------------
    # Costruzione della UI
    # ------------------------------------------------------------------
    def _build_loading_page(self):
        return Adw.StatusPage(
            icon_name="preferences-system-network-symbolic",
            title=_("Checking speedtest…"),
            description=_("Looking for the official Ookla CLI."),
        )

    def _present_ookla_terms(self):
        """Richiede consenso esplicito prima di passare gli --accept-* alla CLI."""
        dialog = Adw.Dialog(title=_("Use of the Ookla Speedtest CLI"), content_width=460)
        dialog.set_can_close(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        description = Gtk.Label(
            label=_(
                "SpeedGTK uses Ookla's official Speedtest CLI. Before continuing, please "
                "read and accept Ookla's End User License Agreement, Terms of Use and "
                "Privacy Policy."
            ),
            wrap=True,
            xalign=0.0,
        )
        description.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        content.append(description)

        links = (
            (_("End User License Agreement"), "https://www.speedtest.net/about/eula"),
            (_("Terms of Use"), "https://www.speedtest.net/about/terms"),
            (_("Privacy Policy"), "https://www.speedtest.net/about/privacy"),
        )
        for label, uri in links:
            link = Gtk.LinkButton(uri=uri, label=label)
            link.set_halign(Gtk.Align.START)
            content.append(link)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        actions.set_halign(Gtk.Align.END)
        quit_button = Gtk.Button(label=_("Quit"))
        accept_button = Gtk.Button(label=_("Accept and continue"))
        accept_button.add_css_class("suggested-action")

        def decline(_button):
            dialog.force_close()
            self.get_application().quit()

        def accept(_button):
            self._settings.set("ookla_terms_accepted", True)
            dialog.force_close()
            self._check_binary()

        quit_button.connect("clicked", decline)
        accept_button.connect("clicked", accept)
        actions.append(quit_button)
        actions.append(accept_button)
        content.append(actions)

        dialog.set_child(content)
        dialog.present(self)

    def _build_main_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(12)
        box.set_margin_bottom(18)
        box.set_margin_start(12)
        box.set_margin_end(12)

        # Le due viste delle misure: tachimetro o label testuali.
        self._measures = Gtk.Stack(vexpand=True)
        self._measures.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._measures.add_named(self._build_gauge_view(), "gauge")
        self._measures.add_named(self._build_classic_view(), "classic")
        box.append(self._measures)

        # --- Avvio / annullamento e azioni sul risultato ---
        # I Revealer laterali sono collassati all'avvio: non lasciano alcuno
        # spazio vuoto finché non esiste un risultato da gestire.
        test_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        test_actions.set_halign(Gtk.Align.CENTER)
        test_actions.set_valign(Gtk.Align.CENTER)

        self._clear_result_button = Gtk.Button(
            icon_name="go-home-symbolic", tooltip_text=_("Clear test")
        )
        self._clear_result_button.set_size_request(42, 42)
        self._clear_result_button.set_sensitive(False)
        self._clear_result_button.add_css_class("circular")
        self._clear_result_button.add_css_class("suggested-action")
        self._clear_result_button.connect("clicked", self._on_clear_result_clicked)
        self._clear_result_revealer = self._result_action_revealer(self._clear_result_button)
        test_actions.append(self._clear_result_revealer)

        self._start_button = Gtk.Button(label=_("Start test"))
        self._start_button.add_css_class("suggested-action")
        self._start_button.add_css_class("pill")
        self._start_button.connect("clicked", self._on_start_clicked)
        test_actions.append(self._start_button)

        self._online_result_button = Gtk.Button(
            icon_name="external-link-symbolic", tooltip_text=_("View this result online")
        )
        self._online_result_button.set_size_request(42, 42)
        self._online_result_button.set_sensitive(False)
        self._online_result_button.add_css_class("circular")
        self._online_result_button.add_css_class("suggested-action")
        self._online_result_button.connect("clicked", self._on_view_result_online_clicked)
        self._online_result_revealer = self._result_action_revealer(self._online_result_button)
        test_actions.append(self._online_result_revealer)
        box.append(test_actions)

        # --- Dettagli del risultato: nascosti finché non c'è un test ---
        # Il Revealer fa crescere l'area gradualmente quando arriva il primo
        # evento del test. Di conseguenza anche il tachimetro riceve meno
        # spazio a ogni frame, anziché ridursi in un unico scatto.
        self._details_group = Adw.PreferencesGroup()
        self._isp_row = Adw.ActionRow(title=_("ISP"), subtitle=PLACEHOLDER)
        self._isp_row.set_subtitle_selectable(True)
        self._isp_row.add_prefix(DetailIcon("isp"))
        self._details_group.add(self._isp_row)

        self._server_detail_row = Adw.ActionRow(title=_("Server used"), subtitle=PLACEHOLDER)
        self._server_detail_row.set_subtitle_selectable(True)
        self._server_detail_row.add_prefix(DetailIcon("server"))
        self._details_group.add(self._server_detail_row)

        self._details_revealer = Gtk.Revealer()
        self._details_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._details_revealer.set_transition_duration(LAYOUT_TRANSITION_DURATION_MS)
        self._details_revealer.set_child(self._details_group)
        box.append(self._details_revealer)

        # --- Selezione del server ---
        server_group = Adw.PreferencesGroup()
        self._server_store = Gio.ListStore.new(ServerItem)
        self._server_row = Adw.ComboRow(title=_("Server"))
        # L'espressione alimenta la riga chiusa (etichetta corta), il factory
        # disegna le voci del menu su due righe: nomi e località per intero.
        self._server_row.set_expression(Gtk.PropertyExpression.new(ServerItem, None, "label"))
        self._server_row.set_list_factory(self._build_server_factory())
        self._server_row.set_model(self._server_store)
        self._server_row.connect("notify::selected", self._on_server_selected)
        server_group.add(self._server_row)

        self._manual_row = Adw.EntryRow(title=_("Manual server ID"))
        self._manual_row.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._manual_row.connect("changed", self._on_manual_changed)
        server_group.add(self._manual_row)
        box.append(server_group)
        self._reset_server_store()

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(Adw.Clamp(child=box, maximum_size=620))

        # La barra di avanzamento resta ancorata in fondo alla finestra, come
        # nella pagina web di Ookla.
        self._progress = PhaseProgress()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(scroller)
        column.append(self._progress)
        return column

    @staticmethod
    def _result_action_revealer(button):
        """Contenitore collassabile con l'animazione nativa dell'azione."""
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SWING_DOWN)
        revealer.set_transition_duration(RESULT_ACTION_TRANSITION_DURATION_MS)
        revealer.set_child(button)
        return revealer

    def _build_server_factory(self):
        """Voci del menu a due righe: nome del server sopra, località sotto."""
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, list_item):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=36)
            subtitle = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=36)
            subtitle.add_css_class("caption")
            subtitle.add_css_class("dim-label")
            column.append(title)
            column.append(subtitle)
            list_item.set_child(column)

        def bind(_factory, list_item):
            item = list_item.get_item()
            column = list_item.get_child()
            title, subtitle = column.get_first_child(), column.get_last_child()
            title.set_label(item.props.title)
            subtitle.set_label(item.props.subtitle)
            subtitle.set_visible(bool(item.props.subtitle))

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def _build_gauge_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Intestazione DOWNLOAD / UPLOAD con l'icona che si illumina.
        headers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=12)
        self._download_icon, self._gauge_download_label = self._build_phase_header(
            headers, "download", _("DOWNLOAD")
        )
        self._upload_icon, self._gauge_upload_label = self._build_phase_header(
            headers, "upload", _("UPLOAD")
        )
        box.append(headers)

        # Ping idle e sotto carico, come nell'interfaccia di speedtest.net.
        # Jitter e perdita stanno sotto: i tre valori di latenza restano così
        # leggibili anche nella finestra stretta.
        latency_stats = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=20, halign=Gtk.Align.CENTER
        )
        latency_caption = Gtk.Label(label=_("Ping ms"))
        latency_caption.add_css_class("caption")
        latency_caption.add_css_class("dim-label")
        latency_stats.append(latency_caption)
        self._idle_ping_icon, self._gauge_ping_label = self._build_latency_stat(
            latency_stats, "idle", _("Idle ping")
        )
        self._download_ping_icon, self._gauge_download_ping_label = self._build_latency_stat(
            latency_stats, "download", _("Download ping")
        )
        self._upload_ping_icon, self._gauge_upload_ping_label = self._build_latency_stat(
            latency_stats, "upload", _("Upload ping")
        )
        box.append(latency_stats)

        secondary_stats = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=18, halign=Gtk.Align.CENTER
        )
        self._gauge_jitter_label = self._build_stat(secondary_stats, _("Jitter ms"))
        self._gauge_loss_label = self._build_stat(secondary_stats, _("Loss %"))
        box.append(secondary_stats)

        self._gauge = SpeedGauge(vexpand=True)
        frame = Gtk.AspectFrame(ratio=1.0, obey_child=False, vexpand=True)
        frame.set_child(self._gauge)
        box.append(frame)

        self._gauge_phase_label = Gtk.Label(label=_("Ready"), halign=Gtk.Align.CENTER)
        self._gauge_phase_label.add_css_class("dim-label")
        box.append(self._gauge_phase_label)
        return box

    def _build_phase_header(self, parent, phase, caption):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)
        icon = PhaseIcon(phase)
        title.append(icon)
        name = Gtk.Label(label=caption)
        name.add_css_class("heading")
        title.append(name)
        unit = Gtk.Label(label=_("Mbps"))
        unit.add_css_class("dim-label")
        title.append(unit)
        column.append(title)

        value = Gtk.Label(label=PLACEHOLDER, halign=Gtk.Align.CENTER)
        value.add_css_class("title-1")
        value.add_css_class("numeric")
        value.set_selectable(True)
        value.set_focusable(False)  # altrimenti prende il focus e mostra il cursore
        column.append(value)
        parent.append(column)
        return icon, value

    def _build_stat(self, parent, caption):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label=caption, halign=Gtk.Align.CENTER)
        title.add_css_class("caption")
        title.add_css_class("dim-label")
        column.append(title)
        value = Gtk.Label(label=PLACEHOLDER, halign=Gtk.Align.CENTER)
        value.add_css_class("heading")
        value.add_css_class("numeric")
        column.append(value)
        parent.append(column)
        return value

    def _build_latency_stat(self, parent, phase, tooltip):
        """Coppia icona-valore per un ping idle o durante un trasferimento."""
        stat = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        # 20 px conserva l'allineamento della riga, ma alleggerisce appena le
        # tre icone rispetto alle intestazioni download/upload da 22 px.
        icon = LatencyIcon(phase, size=20)
        icon.set_tooltip_text(tooltip)
        stat.append(icon)
        value = Gtk.Label(label=PLACEHOLDER, valign=Gtk.Align.CENTER)
        value.add_css_class("heading")
        value.add_css_class("numeric")
        value.set_selectable(True)
        value.set_focusable(False)
        stat.append(value)
        parent.append(stat)
        return icon, value

    def _build_classic_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.START)

        state_group = Adw.PreferencesGroup(title=_("Status"))
        self._phase_label = self._add_value_row(state_group, _("Phase"), _("Ready"))
        box.append(state_group)

        measure_group = Adw.PreferencesGroup(title=_("Measurements"))
        self._ping_label = self._add_value_row(measure_group, _("Idle ping"))
        self._download_ping_label = self._add_value_row(measure_group, _("Download ping"))
        self._upload_ping_label = self._add_value_row(measure_group, _("Upload ping"))
        self._jitter_label = self._add_value_row(measure_group, _("Jitter"))
        self._download_label = self._add_value_row(measure_group, _("Download"))
        self._upload_label = self._add_value_row(measure_group, _("Upload"))
        self._loss_label = self._add_value_row(measure_group, _("Packet loss"))
        box.append(measure_group)
        return box

    def _add_value_row(self, group, title, initial=PLACEHOLDER):
        row = Adw.ActionRow(title=title)
        label = Gtk.Label(label=initial)
        label.add_css_class("numeric")
        label.add_css_class("dim-label")
        label.set_selectable(True)
        label.set_focusable(False)
        row.add_suffix(label)
        group.add(row)
        return label

    # ------------------------------------------------------------------
    # Preferenze
    # ------------------------------------------------------------------
    def _apply_appearance(self):
        """Riporta le preferenze su tutti i widget che ne dipendono."""
        color_schemes = {
            "system": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }
        Adw.StyleManager.get_default().set_color_scheme(
            color_schemes.get(self._settings["color_scheme"], Adw.ColorScheme.DEFAULT)
        )
        accent = bool(self._settings["accent_colors"])
        self._gauge.props.use_accent_color = accent
        self._gauge.props.auto_range = bool(self._settings["auto_range"])
        self._download_icon.set_use_accent_color(accent)
        self._upload_icon.set_use_accent_color(accent)
        self._idle_ping_icon.set_use_accent_color(accent)
        self._download_ping_icon.set_use_accent_color(accent)
        self._upload_ping_icon.set_use_accent_color(accent)
        self._progress.set_use_accent_color(accent)
        self._measures.set_visible_child_name(
            "classic" if self._settings["plain_ui"] else "gauge"
        )
        self._apply_measurement_precision()

    def _measurement_decimals(self):
        """Numero di decimali scelto dall'utente, sempre nell'intervallo 0–2."""
        value = self._settings["measurement_decimals"]
        return value if type(value) is int and value in (0, 1, 2) else 2

    def _jitter_decimals(self):
        """Jitter resta a due cifre, tranne nella visualizzazione senza decimali."""
        return 1 if self._measurement_decimals() == 0 else 2

    def _apply_measurement_precision(self):
        """Riformatta i valori già in vista quando cambia la preferenza."""
        self._gauge.set_measurement_decimals(self._measurement_decimals())
        for kind, value in self._live.items():
            if isinstance(value, (int, float)):
                self._render_speed(kind, value)
                if kind != self._phase:
                    self._commit_header(kind)
        for kind, latency in self._latencies.items():
            if isinstance(latency, (int, float)):
                self._render_latency(kind, latency)
        if isinstance(self._jitter, (int, float)):
            self._render_jitter(self._jitter)
        if isinstance(self._loss, (int, float)):
            self._render_loss(self._loss)

    def _on_setting_toggled(self, row, _pspec, key):
        self._settings.set(key, row.get_active())
        self._apply_appearance()

    def _present_preferences(self, *_args):
        dialog = Adw.PreferencesDialog(title=_("Preferences"))
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")

        appearance = Adw.PreferencesGroup(title=_("Appearance"))
        appearance.add(
            self._switch_row(
                _("Classic interface"), _("Text labels only, no gauge"), "plain_ui"
            )
        )
        appearance.add(
            self._switch_row(
                _("System accent colors"),
                _("Instead of Ookla's teal and violet"),
                "accent_colors",
            )
        )
        appearance.add(self._theme_row())
        appearance.add(self._language_row())
        page.add(appearance)

        measures = Adw.PreferencesGroup(title=_("Measurements"))
        measures.add(self._decimal_places_row())
        measures.add(
            self._switch_row(
                _("Automatic scale"),
                _("Extends the gauge full scale beyond 1000 Mbps"),
                "auto_range",
            )
        )
        page.add(measures)

        history_group = Adw.PreferencesGroup(
            title=_("History"), description=_("Saved in {path}").format(path=self._history.path)
        )
        history_group.add(
            self._switch_row(
                _("Save results"), _("Every completed test is added to the history"), "keep_history"
            )
        )
        open_row = Adw.ActionRow(
            title=_("Open the history"),
            subtitle=_("Saved tests: {count}").format(count=len(self._history.entries)),
        )
        open_row.set_activatable(True)
        open_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        open_row.connect("activated", lambda *_args: self._present_history())
        history_group.add(open_row)
        page.add(history_group)

        dialog.add(page)
        dialog.present(self)

    def _switch_row(self, title, subtitle, key):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(self._settings[key]))
        row.connect("notify::active", self._on_setting_toggled, key)
        return row

    def _decimal_places_row(self):
        """SpinRow compatta: mostra frecce su/giù invece di un menu a tendina."""
        row = Adw.SpinRow.new_with_range(0, 2, 1)
        row.set_title(_("Decimal places"))
        row.set_subtitle(_("Download, upload and ping"))
        row.set_digits(0)
        row.set_numeric(True)
        row.set_snap_to_ticks(True)
        row.set_wrap(False)
        row.set_value(self._measurement_decimals())

        def changed(spin_row, _pspec):
            decimals = int(round(spin_row.get_value()))
            if decimals != spin_row.get_value():
                spin_row.set_value(decimals)
                return
            self._settings.set("measurement_decimals", decimals)
            self._apply_measurement_precision()

        row.connect("notify::value", changed)
        return row

    def _language_row(self):
        """Scelta della lingua fra quelle per cui esiste un .po in po/."""
        available = TRANSLATIONS.available()
        names = language_names()
        codes = [c for c in LANGUAGE_ORDER if c == "system" or c in available]
        model = Gtk.StringList()
        for code in codes:
            model.append(names.get(code, code))

        current = self._settings["language"]
        row = Adw.ComboRow(title=_("Language"), model=model)
        row.set_selected(codes.index(current) if current in codes else 0)
        row.set_subtitle(_("Active: {language}").format(language=names.get(TRANSLATIONS.code, "")))

        def changed(combo, _pspec):
            index = combo.get_selected()
            if index >= len(codes):
                return
            self._settings.set("language", codes[index])
            if self._run is not None:
                # Ricostruire la finestra a test in corso lo interromperebbe.
                self._toast(_("The language will be applied at the next launch"))
                return
            self.get_application().reload_ui(reopen_preferences=True)

        row.connect("notify::selected", changed)
        return row

    def _theme_row(self):
        model = Gtk.StringList()
        for _code, label in THEME_OPTIONS:
            model.append(_(label))

        codes = [code for code, _label in THEME_OPTIONS]
        current = self._settings["color_scheme"]
        row = Adw.ComboRow(title=_("Theme"), model=model)
        row.set_selected(codes.index(current) if current in codes else 0)

        def changed(combo, _pspec):
            index = combo.get_selected()
            if index >= len(codes):
                return
            self._settings.set("color_scheme", codes[index])
            self._apply_appearance()

        row.connect("notify::selected", changed)
        return row

    def _present_about(self, *_args):
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=APP_VERSION,
            developer_name="Michele · mikpinky",
            website="https://github.com/mikpinky",
            issue_url="https://github.com/mikpinky/speedgtk/issues",
        )
        dialog.set_comments(_("A GTK 4 interface for the official Ookla Speedtest CLI."))
        dialog.set_copyright("© 2026 Michele · mikpinky")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.present(self)

    # ------------------------------------------------------------------
    # Storico
    # ------------------------------------------------------------------
    def _present_history(self, *_args):
        dialog = Adw.Dialog(title=_("History"), content_width=680, content_height=580)
        header = Adw.HeaderBar()
        sort_button = Gtk.MenuButton(
            icon_name="view-sort-ascending-symbolic", tooltip_text=_("Sort history")
        )
        sort_button.add_css_class("flat")
        sort_menu = Gio.Menu()
        for _key, label in HISTORY_SORTS:
            sort_menu.append(_(label), f"history.sort::{_key}")
        sort_button.set_menu_model(sort_menu)

        clear_button = Gtk.Button(
            icon_name="user-trash-symbolic", tooltip_text=_("Clear the history")
        )
        clear_button.add_css_class("flat")
        header.pack_start(clear_button)
        header.pack_end(sort_button)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._build_history_content())
        dialog.set_child(view)

        sort_order = "date"

        def refresh():
            view.set_content(self._build_history_content(sort_order))
            clear_button.set_sensitive(bool(self._history.entries))

        def select_sort(_action, parameter):
            nonlocal sort_order
            sort_order = parameter.get_string()
            refresh()

        sort_actions = Gio.SimpleActionGroup()
        sort_action = Gio.SimpleAction.new("sort", GLib.VariantType.new("s"))
        sort_action.connect("activate", select_sort)
        sort_actions.add_action(sort_action)
        sort_button.insert_action_group("history", sort_actions)

        clear_button.set_sensitive(bool(self._history.entries))
        clear_button.connect("clicked", lambda *_args: self._confirm_clear_history(dialog, refresh))
        dialog.present(self)

    def _build_history_content(self, sort_order="date"):
        entries = self._sorted_history_entries(sort_order)
        if not entries:
            return Adw.StatusPage(
                icon_name="document-open-recent-symbolic",
                title=_("No saved test"),
                description=_("Completed tests show up here, if the history is enabled."),
            )

        group = Adw.PreferencesGroup(
            description=_("Saved tests: {count} — at most {limit}").format(
                count=len(entries), limit=HISTORY_LIMIT
            )
        )
        for entry in entries:
            row = Adw.ActionRow(title=format_timestamp(entry.get("timestamp")))
            row.set_subtitle(self._history_subtitle(entry))
            row.set_tooltip_text(self._history_details(entry))
            url = entry.get("url")
            if url:
                link = Gtk.LinkButton(uri=url)
                link.set_icon_name("external-link-symbolic")
                link.add_css_class("flat")
                link.set_valign(Gtk.Align.CENTER)
                link.set_tooltip_text(_("View this result online"))
                row.add_suffix(link)
            group.add(row)

        page = Adw.PreferencesPage()
        page.add(group)
        return page

    @staticmethod
    def _history_metric(entry, key):
        """Restituisce solo misure finite, per gestire anche vecchi JSON corrotti."""
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
        return None

    @staticmethod
    def _percentile(values, fraction):
        """Percentile interpolato, senza dipendere da versioni specifiche di Python."""
        position = (len(values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return values[lower]
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    @classmethod
    def _historical_mean(cls, entries, key):
        """Media storica, scartando gli outlier bassi con la recinzione di Tukey."""
        values = sorted(
            value
            for entry in entries
            if (value := cls._history_metric(entry, key)) is not None and value > 0
        )
        if not values:
            return None

        # Con pochi test non è possibile distinguere una normale oscillazione da
        # un outlier. Da quattro misure in poi, la soglia inferiore di Tukey
        # elimina solo valori eccezionalmente bassi, senza penalizzare variazioni
        # realistiche della linea.
        if len(values) >= 4:
            first_quartile = cls._percentile(values, 0.25)
            third_quartile = cls._percentile(values, 0.75)
            lower_fence = first_quartile - 1.5 * (third_quartile - first_quartile)
            retained = [value for value in values if value >= lower_fence]
            if retained:
                values = retained

        return sum(values) / len(values)

    @staticmethod
    def _sort_history_entries(entries, value_for_entry, reverse=False):
        """Ordina valori validi davanti a quelli mancanti, conservando le parità."""
        def sort_key(indexed_entry):
            index, entry = indexed_entry
            value = value_for_entry(entry)
            if value is None:
                return (1, 0, index)
            return (0, -value if reverse else value, index)

        return [entry for _index, entry in sorted(enumerate(entries), key=sort_key)]

    def _sorted_history_entries(self, sort_order):
        entries = self._history.entries
        if sort_order == "download":
            return self._sort_history_entries(
                entries, lambda entry: self._history_metric(entry, "download"), reverse=True
            )
        if sort_order == "upload":
            return self._sort_history_entries(
                entries, lambda entry: self._history_metric(entry, "upload"), reverse=True
            )
        if sort_order == "ping":
            return self._sort_history_entries(
                entries, lambda entry: self._history_metric(entry, "ping")
            )
        if sort_order == "overall":
            download_mean = self._historical_mean(entries, "download")
            upload_mean = self._historical_mean(entries, "upload")
            if download_mean is not None and upload_mean is not None:
                def overall_score(entry):
                    download = self._history_metric(entry, "download")
                    upload = self._history_metric(entry, "upload")
                    if download is None or upload is None:
                        return None
                    return (
                        OVERALL_DOWNLOAD_WEIGHT * download / download_mean
                        + OVERALL_UPLOAD_WEIGHT * upload / upload_mean
                    )

                return self._sort_history_entries(entries, overall_score, reverse=True)

        # I timestamp della CLI sono ISO 8601 in UTC, quindi l'ordinamento
        # lessicografico coincide con quello cronologico. Le righe senza data
        # restano in fondo e le parità mantengono il loro ordine nello storico.
        return sorted(
            entries,
            key=lambda entry: entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else "",
            reverse=True,
        )

    def _history_number(self, entry, key, decimals=None):
        if decimals is None:
            decimals = self._measurement_decimals()
        value = entry.get(key)
        return format_number(value, decimals) if isinstance(value, (int, float)) else PLACEHOLDER

    def _history_subtitle(self, entry):
        return "↓ {download} · ↑ {upload} {unit} · {ping} ms".format(
            download=self._history_number(entry, "download"),
            upload=self._history_number(entry, "upload"),
            unit=_("Mbps"),
            ping=self._history_number(entry, "ping"),
        )

    def _history_details(self, entry):
        """Il resto (server, ISP, jitter, perdita) sta nel tooltip della riga."""
        lines = []
        if entry.get("server"):
            lines.append(_("Server: {server}").format(server=entry["server"]))
        if entry.get("isp"):
            lines.append(_("ISP: {isp}").format(isp=entry["isp"]))
        lines.append(
            _("Jitter {jitter} ms · loss {loss} %").format(
                jitter=self._history_number(entry, "jitter", self._jitter_decimals()),
                loss=self._history_number(entry, "loss", 1),
            )
        )
        return "\n".join(lines)

    def _confirm_clear_history(self, parent, on_cleared):
        alert = Adw.AlertDialog(
            heading=_("Clear the history?"),
            body=_(
                "The results saved on this computer will be deleted. The tests stay "
                "available at their speedtest.net links."
            ),
        )
        alert.add_response("cancel", _("Cancel"))
        alert.add_response("clear", _("Clear"))
        alert.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")

        def responded(_dialog, response):
            if response == "clear":
                self._history.clear()
                on_cleared()

        alert.connect("response", responded)
        alert.present(parent)

    def _record_result(self, event):
        """Aggiunge il test appena concluso allo storico."""
        if not self._settings["keep_history"]:
            return
        server = event.get("server") if isinstance(event.get("server"), dict) else {}
        entry = {
            "timestamp": event.get("timestamp"),
            "download": self._live.get("download"),
            "upload": self._live.get("upload"),
            "ping": event.get("ping", {}).get("latency"),
            "jitter": event.get("ping", {}).get("jitter"),
            "loss": event.get("packetLoss"),
            "server": "{} — {} ({})".format(
                server.get("name", "?"), server.get("location", "?"), server.get("country", "?")
            ),
            "server_id": server.get("id"),
            "isp": event.get("isp"),
            "url": event.get("result", {}).get("url"),
        }
        self._history.add(entry)

    # ------------------------------------------------------------------
    # Controllo iniziale del binario
    # ------------------------------------------------------------------
    def _accepted_cli_flags(self):
        """Restituisce i flag di consenso solo dopo l'azione esplicita dell'utente."""
        return ACCEPT_FLAGS if self._settings["ookla_terms_accepted"] else []

    def _check_binary(self):
        if not self._settings["ookla_terms_accepted"]:
            return
        self._stack.set_visible_child_name("loading")
        self._refresh_button.set_sensitive(False)
        if GLib.find_program_in_path(BIN) is None:
            self._show_unavailable(found=False, output="")
            return
        run_and_capture([BIN, "--version"], self._on_version_done)

    def _on_version_done(self, status, stdout_text, stderr_text):
        blob = f"{stdout_text}\n{stderr_text}"
        if status < 0 or OOKLA_SIGNATURE not in blob:
            self._show_unavailable(found=status >= 0, output=blob.strip())
            return

        first_line = next((l.strip() for l in stdout_text.splitlines() if l.strip()), "")
        # Sottotitolo essenziale ("Speedtest CLI 1.2.0.84"); la riga completa,
        # con build e piattaforma, resta nel tooltip.
        self._window_title.set_subtitle(clean_version(first_line))
        self._window_title.set_tooltip_text(first_line)
        self._stack.set_visible_child_name("main")
        self._refresh_button.set_sensitive(True)
        self._load_servers()

    def _show_unavailable(self, found, output):
        """StatusPage che spiega la differenza fra le due `speedtest` e disabilita il test."""
        if found:
            title = _("The `speedtest` found is not the official one")
            description = _(
                "The <tt>speedtest</tt> command on this system is not Ookla's official "
                "CLI, but almost certainly the old Python script <tt>speedtest-cli</tt>."
                "\n\n"
                "They are two different programs: <tt>speedtest-cli</tt> is a third-party "
                "project using unofficial APIs, it takes different options and supports "
                "neither <tt>--format=jsonl</tt> nor the live progress updates this app "
                "is built on.\n\n"
                "On Debian-derived distributions, remove the old one and install the "
                "official one with:"
            )
        else:
            title = _("Ookla's `speedtest` CLI was not found")
            description = _(
                "This app is a frontend for Ookla's <b>official</b> CLI, which is not "
                "installed.\n\n"
                "Careful not to mix it up with <tt>speedtest-cli</tt>, the old "
                "third-party Python script: same name, but different options and output, "
                "and no <tt>--format=jsonl</tt>.\n\n"
                "On Debian-derived distributions, you can install the official one with:"
            )

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_halign(Gtk.Align.CENTER)

        commands = Gtk.Label(
            label=(
                "sudo apt remove speedtest-cli\n"
                "curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash\n"
                "sudo apt install speedtest"
            ),
            selectable=True,
            wrap=True,
            xalign=0,
        )
        commands.add_css_class("monospace")
        content.append(commands)

        other_distributions = Gtk.LinkButton(
            uri="https://www.speedtest.net/apps/cli",
            label=_("Installation instructions for other distributions"),
        )
        other_distributions.set_halign(Gtk.Align.CENTER)
        content.append(other_distributions)

        if output:
            got = Gtk.Label(
                label=_("Output received: {output}").format(output=output.splitlines()[0]),
                use_markup=False,
                wrap=True,
                xalign=0,
            )
            got.add_css_class("dim-label")
            got.add_css_class("caption")
            content.append(got)

        retry = Gtk.Button(label=_("Try again"))
        retry.add_css_class("pill")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_args: self._check_binary())
        content.append(retry)

        self._unavailable_page.set_icon_name("dialog-warning-symbolic")
        self._unavailable_page.set_title(title)
        self._unavailable_page.set_description(description)
        self._unavailable_page.set_child(content)

        self._window_title.set_subtitle("")
        self._refresh_button.set_sensitive(False)
        self._stack.set_visible_child_name("unavailable")

    # ------------------------------------------------------------------
    # Elenco dei server
    # ------------------------------------------------------------------
    def _load_servers(self):
        if not self._settings["ookla_terms_accepted"]:
            return
        if self._servers_cancellable is not None:
            self._servers_cancellable.cancel()
        self._servers_cancellable = Gio.Cancellable()

        self._refresh_button.set_sensitive(False)
        self._server_row.set_subtitle(_("Loading the list…"))
        # Il consenso è stato richiesto esplicitamente prima del controllo
        # della CLI; i flag evitano ora il prompt interattivo su stdin.
        run_and_capture(
            [BIN, "--servers", "--format=json"] + self._accepted_cli_flags(),
            self._on_servers_done,
            self._servers_cancellable,
        )

    def _on_servers_done(self, status, stdout_text, stderr_text):
        self._refresh_button.set_sensitive(True)

        servers = None
        if status == 0:
            try:
                payload = json.loads(stdout_text)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                servers = payload.get("servers")

        if not isinstance(servers, list):
            short, detail = humanize_cli_error(extract_cli_error(stdout_text, stderr_text))
            self._toast(_("Could not load the servers"), detail or short)
            self._refresh_button.set_tooltip_text(_("Refresh the server list"))
            self._update_server_row_subtitle()
            return

        self._reset_server_store(servers)
        self._refresh_button.set_tooltip_text(
            _("Refresh the server list — nearby: {count}").format(count=len(servers))
        )

    def _reset_server_store(self, servers=()):
        """Ricostruisce l'elenco: prima voce automatica, poi i server vicini."""
        self._updating_servers = True  # le modifiche al modello non sono scelte dell'utente
        try:
            self._server_store.remove_all()
            self._server_store.append(self._auto_item())
            for server in servers:
                self._server_store.append(
                    ServerItem(
                        label="{} — {}".format(
                            server.get("name", "?"), server.get("location", "?")
                        ),
                        title=str(server.get("name", "?")),
                        subtitle="{} ({}) · {} {}".format(
                            server.get("location", "?"),
                            server.get("country", "?"),
                            _("id"),
                            server.get("id", "?"),
                        ),
                        server_id=server.get("id"),
                    )
                )
            self._server_row.set_selected(0)
        finally:
            self._updating_servers = False
        self._update_server_row_subtitle()

    def _auto_item(self):
        """Voce "Automatico", con l'ultimo server effettivamente scelto se noto."""
        last = self._settings["last_auto_server"]
        return ServerItem(
            label=_("Automatic"),
            title=_("Automatic"),
            subtitle=(
                _("Last one: {server}").format(server=last)
                if last
                else _("Picked by speedtest, by latency")
            ),
            server_id=None,
        )

    def _remember_auto_server(self, server):
        """Memorizza quale server ha scelto la modalità automatica."""
        if not self._auto_server or not isinstance(server, dict):
            return
        # Senza il paese: la riga deve stare nel menu a tendina senza troncarsi.
        description = "{} — {}".format(server.get("name", "?"), server.get("location", "?"))
        if description == self._settings["last_auto_server"]:
            return
        self._settings.set("last_auto_server", description)
        if self._server_store.get_n_items():
            self._updating_servers = True
            try:
                selected = self._server_row.get_selected()
                self._server_store.splice(0, 1, [self._auto_item()])
                self._server_row.set_selected(selected)
            finally:
                self._updating_servers = False
        self._update_server_row_subtitle()

    def _on_server_selected(self, *_args):
        if self._updating_servers:
            return
        # Scegliere un server dall'elenco svuota l'ID manuale: altrimenti
        # resterebbe a vincere in silenzio sulla scelta appena fatta.
        if self._manual_row.get_text().strip():
            self._manual_row.set_text("")  # provoca già _update_server_row_subtitle()
        else:
            self._update_server_row_subtitle()

    def _on_manual_changed(self, *_args):
        self._update_server_row_subtitle()

    def _update_server_row_subtitle(self):
        if self._manual_row.get_text().strip():
            self._server_row.set_subtitle(_("Ignored: a manual ID is set"))
            return
        item = self._selected_item()
        self._server_row.set_subtitle(item.props.subtitle if item is not None else "")

    def _selected_item(self):
        index = self._server_row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= self._server_store.get_n_items():
            return None
        return self._server_store.get_item(index)

    def _resolve_server_id(self):
        """ID del server da usare, o None per la scelta automatica.

        L'EntryRow manuale ha la precedenza sul ComboRow. Solleva ValueError se
        contiene qualcosa che non è un numero.
        """
        manual = self._manual_row.get_text().strip()
        if manual:
            if not manual.isdigit():
                raise ValueError(_("The manual server ID must be a number"))
            return manual

        item = self._selected_item()
        if item is not None and item.server_id is not None:
            return str(item.server_id)
        return None

    # ------------------------------------------------------------------
    # Avvio / annullamento del test
    # ------------------------------------------------------------------
    def _on_start_clicked(self, _button):
        if not self._settings["ookla_terms_accepted"]:
            return
        if self._run is not None:
            self._set_phase("cancel", _("Cancelling…"))
            self._start_button.set_sensitive(False)  # riabilitato in _on_run_done
            self._run.cancel()
            return

        try:
            server_id = self._resolve_server_id()
        except ValueError as err:
            self._toast(str(err))
            return

        argv = [
            BIN,
            "--format=jsonl",
            f"--progress-update-interval={PROGRESS_INTERVAL_MS}",
        ] + self._accepted_cli_flags()
        if server_id is not None:
            argv += ["-s", server_id]
        self._auto_server = server_id is None

        self._reset_results()
        try:
            self._run = SpeedtestRun(argv, self._on_event, self._on_run_done)
        except GLib.Error as err:
            self._toast(_("Cannot start speedtest"), err.message)
            return
        self._set_running(True)

    def _set_running(self, running):
        self._start_button.set_sensitive(True)
        if running:
            self._start_button.set_label(_("Cancel"))
            self._start_button.remove_css_class("suggested-action")
            self._start_button.add_css_class("destructive-action")
        else:
            self._start_button.set_label(_("Repeat test") if self._has_run else _("Start test"))
            self._start_button.remove_css_class("destructive-action")
            self._start_button.add_css_class("suggested-action")
        for widget in (self._server_row, self._manual_row, self._refresh_button):
            widget.set_sensitive(not running)

    def _reset_results(self):
        self._cancel_progress_hide()
        self._last_error = None
        self._live = {"download": None, "upload": None}
        self._latencies = {"idle": None, "download": None, "upload": None}
        self._jitter = None
        self._loss = None
        self._result_url = None
        self._progress.set_fraction(0.0)
        self._set_phase("idle", _("Starting…"))
        for label in (
            self._ping_label,
            self._download_ping_label,
            self._upload_ping_label,
            self._jitter_label,
            self._download_label,
            self._upload_label,
            self._loss_label,
            self._gauge_ping_label,
            self._gauge_download_ping_label,
            self._gauge_upload_ping_label,
            self._gauge_jitter_label,
            self._gauge_loss_label,
            self._gauge_download_label,
            self._gauge_upload_label,
        ):
            label.set_label(PLACEHOLDER)
        self._idle_ping_icon.set_active(False)
        self._download_ping_icon.set_active(False)
        self._upload_ping_icon.set_active(False)
        self._download_icon.set_active(False)
        self._upload_icon.set_active(False)
        # I dettagli tornano nascosti: si ripopolano al primo evento del test.
        self._details_revealer.set_reveal_child(False)
        self._set_result_actions_visible(False)
        self._server_detail_row.set_subtitle(PLACEHOLDER)
        self._isp_row.set_subtitle(PLACEHOLDER)

    def _set_result_actions_visible(self, visible):
        """Mostra prima l'azione di reset e poi, se presente, quella online."""
        self._cancel_result_action_delay()
        if not visible:
            self._set_result_action_visible(
                self._clear_result_revealer, self._clear_result_button, False
            )
            self._set_result_action_visible(
                self._online_result_revealer, self._online_result_button, False
            )
            return

        self._set_result_action_visible(
            self._clear_result_revealer, self._clear_result_button, True
        )
        self._set_result_action_visible(
            self._online_result_revealer, self._online_result_button, False
        )
        if self._result_url:
            self._result_action_reveal_source = GLib.timeout_add(
                RESULT_ACTION_TRANSITION_DURATION_MS, self._reveal_online_result_action
            )

    def _cancel_result_action_delay(self):
        if self._result_action_reveal_source is not None:
            GLib.source_remove(self._result_action_reveal_source)
            self._result_action_reveal_source = None

    def _reveal_online_result_action(self):
        self._result_action_reveal_source = None
        if self._run is None and self._has_run and self._result_url:
            self._set_result_action_visible(
                self._online_result_revealer, self._online_result_button, True
            )
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _set_result_action_visible(revealer, button, visible):
        """Accoppia il collasso del layout a un pulsante realmente attivo."""
        button.set_sensitive(visible)
        revealer.set_reveal_child(visible)

    def _on_clear_result_clicked(self, _button):
        """Torna allo stato iniziale e richiude i dettagli del test appena visto."""
        if self._run is not None:
            return
        self._has_run = False
        self._reset_results()
        self._set_phase("idle", _("Ready"))
        self._set_running(False)

    def _on_view_result_online_clicked(self, _button):
        if self._result_url:
            Gtk.show_uri(self, self._result_url, 0)

    # ------------------------------------------------------------------
    # Aggiornamento delle due viste
    # ------------------------------------------------------------------
    def _set_phase(self, phase, text):
        """Cambia fase: aggiorna le etichette di entrambe le viste e il tachimetro."""
        self._phase_label.set_label(text)
        self._gauge_phase_label.set_label(text)
        if phase != self._phase:
            # Chiudendo download o upload il valore finale sale in intestazione,
            # come nella pagina di Ookla (durante la fase il numero è nel quadrante).
            if self._phase in ("download", "upload"):
                self._commit_header(self._phase)
            self._phase = phase
        self._gauge.set_phase(phase if phase in SpeedGauge.PHASES else "idle")
        self._progress.set_phase(phase)

    def _commit_header(self, kind):
        value = self._live.get(kind)
        if value is None:
            return
        label = self._gauge_download_label if kind == "download" else self._gauge_upload_label
        label.set_label(format_number(value, self._measurement_decimals()))

    def _render_speed(self, kind, value):
        """Aggiorna le etichette della vista classica senza muovere l'ago."""
        classic = self._download_label if kind == "download" else self._upload_label
        classic.set_label(
            "{} {}".format(format_number(value, self._measurement_decimals()), _("Mbps"))
        )

    def _show_speed(self, kind, value):
        """Nuova velocità per download o upload, in Mbps."""
        self._live[kind] = value
        self._render_speed(kind, value)
        icon = self._download_icon if kind == "download" else self._upload_icon
        icon.set_active(True)
        if self._phase == kind:
            # L'ago non ci salta sopra: set_target() interpola.
            self._gauge.set_target(value)

    def _latency_widgets(self, kind):
        labels = {
            "idle": (self._ping_label, self._gauge_ping_label, self._idle_ping_icon),
            "download": (
                self._download_ping_label,
                self._gauge_download_ping_label,
                self._download_ping_icon,
            ),
            "upload": (
                self._upload_ping_label,
                self._gauge_upload_ping_label,
                self._upload_ping_icon,
            ),
        }
        return labels[kind]

    def _render_latency(self, kind, latency):
        classic, gauge, _icon = self._latency_widgets(kind)
        rendered = format_number(latency, self._measurement_decimals())
        classic.set_label(f"{rendered} ms")
        gauge.set_label(rendered)

    def _render_jitter(self, jitter):
        rendered = format_number(jitter, self._jitter_decimals())
        self._jitter_label.set_label(f"{rendered} ms")
        self._gauge_jitter_label.set_label(rendered)

    def _show_latency(self, kind, latency, jitter=None):
        """Mostra la latenza idle oppure la latenza misurata sotto carico."""
        if isinstance(latency, (int, float)):
            self._latencies[kind] = latency
            self._render_latency(kind, latency)
            _classic, _gauge, icon = self._latency_widgets(kind)
            icon.set_active(True)
        if isinstance(jitter, (int, float)):
            self._jitter = jitter
            self._render_jitter(jitter)

    @staticmethod
    def _loaded_latency(latency):
        """Ricava l'IQM della latenza sotto carico da un evento della CLI."""
        if isinstance(latency, (int, float)):
            return latency
        if isinstance(latency, dict):
            return latency.get("iqm")
        return None

    def _render_loss(self, loss):
        rendered = format_number(loss, 1)
        self._loss_label.set_label(f"{rendered} %")
        self._gauge_loss_label.set_label(rendered)

    def _show_loss(self, loss):
        self._loss = loss if isinstance(loss, (int, float)) else None
        if self._loss is not None:
            self._render_loss(self._loss)
            return
        self._loss_label.set_label(_("not available"))
        self._gauge_loss_label.set_label(PLACEHOLDER)

    # ==================================================================
    # PARSER DEGLI EVENTI JSONL
    # ==================================================================
    # `speedtest --format=jsonl` scrive su stdout un oggetto JSON per riga.
    # Schema osservato con "Speedtest by Ookla 1.2.0.84":
    #
    #   {"type":"testStart","timestamp":"...","isp":"Aruba Broadband",
    #    "interface":{"internalIp":...,"externalIp":...,"isVpn":false},
    #    "server":{"id":7839,"host":"...","port":8080,"name":"Fastweb SpA",
    #              "location":"Milan","country":"Italy","ip":"..."}}
    #
    #   {"type":"ping","ping":{"jitter":0.0,"latency":11.671,"progress":0.2}}
    #
    #   {"type":"download","download":{"bandwidth":55297003,"bytes":4044644,
    #                                  "elapsed":73,"progress":0.005}}
    #       · bandwidth è in BYTE/s  → Mbps = bandwidth * 8 / 1e6
    #       · bytes    = totale trasferito, elapsed = ms dall'inizio della fase
    #       · negli ultimi eventi compare anche "latency":{"iqm":...} (loaded latency)
    #
    #   {"type":"upload","upload":{...}}          # stessa forma di download
    #
    #   {"type":"result","ping":{"jitter":..,"latency":..,"low":..,"high":..},
    #    "download":{...},"upload":{...},"packetLoss":0,"isp":"...","server":{...},
    #    "result":{"id":"...","url":"https://www.speedtest.net/result/c/...",
    #              "persisted":true}}
    #       · "packetLoss" può mancare del tutto se il server non lo misura
    #
    #   Errori: la 1.2 li emette su stdout come
    #       {"type":"log","level":"error","message":"... (NoServersException)"}
    #   mentre le versioni precedenti usavano {"type":"error","message":"..."}.
    #   Gestiamo entrambe le forme. Esistono anche log con level "info"/"warning",
    #   che qui ignoriamo.
    #
    # NOTA su "progress" (0→1): è relativo alla SINGOLA fase, non al test intero,
    # quindi la ProgressBar riparte da zero su ping, download e upload.
    # ==================================================================
    def _on_event(self, event):
        event_type = event.get("type")

        if event_type == "testStart":
            self._set_phase("ping", _("Test started…"))
            self._set_server_details(event.get("server"), event.get("isp"))

        elif event_type == "ping":
            data = event.get("ping", {})
            self._set_phase("ping", _("Measuring ping…"))
            # La CLI espone un progresso anche per il ping, che arriva a 100%
            # in pochi istanti. La barra in basso rappresenta però il
            # trasferimento dati: mostrarlo qui la faceva sembrare completata
            # prima ancora che iniziasse il download.
            self._show_latency("idle", data.get("latency"), data.get("jitter"))

        elif event_type in ("download", "upload"):
            data = event.get(event_type, {})
            is_download = event_type == "download"
            # Il cambio di fase va prima del valore: è quello che fa tornare
            # l'ago a zero prima di ripartire con la fase nuova.
            self._set_phase(event_type, _("Download…") if is_download else _("Upload…"))
            self._set_progress(data.get("progress"))
            bandwidth = data.get("bandwidth")
            if isinstance(bandwidth, (int, float)):
                self._show_speed(event_type, mbps(bandwidth))
            self._show_latency(event_type, self._loaded_latency(data.get("latency")))

        elif event_type == "result":
            self._apply_result(event)

        elif event_type == "error" or (event_type == "log" and event.get("level") == "error"):
            # Memorizzato e mostrato in un toast quando il processo termina:
            # così un errore non viene sovrascritto dagli eventi successivi.
            self._last_error = str(event.get("message") or event.get("error") or "")

    def _apply_result(self, event):
        """Valori definitivi presi dall'evento `result` (più precisi dei parziali)."""
        ping = event.get("ping", {})
        self._show_latency("idle", ping.get("latency"), ping.get("jitter"))

        for key in ("download", "upload"):
            bandwidth = event.get(key, {}).get("bandwidth")
            if isinstance(bandwidth, (int, float)):
                self._live[key] = mbps(bandwidth)
                self._show_speed(key, mbps(bandwidth))

        self._show_loss(event.get("packetLoss"))
        self._set_server_details(event.get("server"), event.get("isp"))

        url = event.get("result", {}).get("url")
        self._result_url = url if isinstance(url, str) and url else None

        self._progress.set_fraction(1.0)
        self._commit_header("download")
        self._commit_header("upload")
        # 'done' riporta l'ago a riposo: i valori finali sono in intestazione.
        self._set_phase("done", _("Completed"))
        self._record_result(event)
        self._schedule_progress_hide()

    def _schedule_progress_hide(self):
        """Lascia visibile il completamento upload per un breve istante."""
        self._cancel_progress_hide()
        self._progress_hide_source = GLib.timeout_add(
            PROGRESS_HIDE_DELAY_MS, self._hide_finished_progress
        )

    def _cancel_progress_hide(self):
        if self._progress_hide_source is not None:
            GLib.source_remove(self._progress_hide_source)
            self._progress_hide_source = None

    def _hide_finished_progress(self):
        self._progress_hide_source = None
        # Un nuovo test può essere partito mentre il timer era in attesa:
        # in quel caso la barra appartiene già alla sua nuova fase.
        if self._phase == "done":
            self._progress.set_fraction(0.0)
        return GLib.SOURCE_REMOVE

    def _set_server_details(self, server, isp):
        self._remember_auto_server(server)
        if isinstance(server, dict):
            self._server_detail_row.set_subtitle(
                "{} — {} ({}) · {} {}".format(
                    server.get("name", "?"),
                    server.get("location", "?"),
                    server.get("country", "?"),
                    _("id"),
                    server.get("id", "?"),
                )
            )
            self._details_revealer.set_reveal_child(True)
        if isp:
            self._isp_row.set_subtitle(str(isp))
            self._details_revealer.set_reveal_child(True)

    def _set_progress(self, progress):
        if isinstance(progress, (int, float)):
            self._progress.set_fraction(min(max(float(progress), 0.0), 1.0))

    # ------------------------------------------------------------------
    # Fine del test
    # ------------------------------------------------------------------
    def _on_run_done(self, status, stderr_text, cancelled):
        self._run = None
        self._has_run = True
        self._set_running(False)

        if cancelled:
            self._set_phase("idle", _("Test cancelled"))
            self._progress.set_fraction(0.0)
            self._toast(_("Test cancelled"))
            return

        if self._last_error:
            short, detail = humanize_cli_error(self._last_error)
            self._set_phase("idle", _("Error"))
            self._toast(short, detail or self._last_error)
            return

        if status != 0:
            raw = extract_cli_error("", stderr_text)
            short, detail = humanize_cli_error(raw)
            if not raw:
                short = _("speedtest exited with code {code}").format(code=status)
                detail = None
            self._set_phase("idle", _("Error"))
            self._toast(short, detail or raw)
            return

        # Uscita pulita. Nota: stderr NON vuoto non è di per sé un errore — alla
        # prima esecuzione la CLI ci scrive l'informativa GDPR anche quando il
        # test riesce, quindi lo segnaliamo solo con exit code diverso da zero.
        self._set_result_actions_visible(True)

    def _toast(self, message, detail=None):
        """Toast breve; se c'è un testo lungo va nel dialogo dei dettagli."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(6)
        if detail and detail != message:
            toast.set_button_label(_("Details"))
            toast.set_action_name("win.error-details")
            toast.set_action_target_value(GLib.Variant.new_string(detail))
        self._toasts.add_toast(toast)

    def _present_error(self, detail):
        alert = Adw.AlertDialog(heading=_("speedtest error"), body=detail)
        alert.add_response("close", _("Close"))
        alert.set_default_response("close")
        alert.set_close_response("close")
        alert.present(self)

    def _on_close_request(self, *_args):
        self._cancel_progress_hide()
        if self._run is not None:
            self._run.kill()  # niente processi orfani
        return False


class SpeedGTKApplication(Adw.Application):
    def __init__(self, settings=None, history=None):
        super().__init__(application_id=APP_ID)
        self._settings = settings if settings is not None else Settings()
        self._history = history if history is not None else History()
        self.set_accels_for_action("win.preferences", ["<Primary>comma"])
        self.set_accels_for_action("win.history", ["<Primary>h"])

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = SpeedGTKWindow(self, self._settings, self._history)
        window.present()

    def reload_ui(self, reopen_preferences=False):
        """Ricostruisce la finestra: serve al cambio di lingua.

        Tutto lo stato durevole vive in Settings e History, quindi ricreare la
        finestra è sufficiente e più semplice che ritradurre widget per widget.
        """
        TRANSLATIONS.use(self._settings["language"])
        previous = self.props.active_window
        window = SpeedGTKWindow(self, self._settings, self._history)
        window.present()
        if previous is not None:
            previous.destroy()
        if reopen_preferences:
            window._present_preferences()


def usage():
    return _(
        """Usage: speedgtk.py [options]

  --plain     start with the classic, label-only GNOME interface
  --accent    use the theme accent color instead of Ookla's colors
  -h, --help  show this message

Both options apply to this run only; the persistent settings live in
Preferences (Ctrl+,). Test history: Ctrl+H.
"""
    )


def main(argv):
    if "-h" in argv or "--help" in argv:
        settings = Settings()
        TRANSLATIONS.use(settings["language"])
        print(usage(), end="")
        return 0
    unknown = [a for a in argv[1:] if a not in ("--plain", "--accent")]
    if unknown:
        print(f"Unknown option: {unknown[0]}\n\n{usage()}", end="", file=sys.stderr)
        return 2

    settings = Settings()
    # Le opzioni da riga di comando non sovrascrivono le preferenze salvate.
    if "--plain" in argv:
        settings.override("plain_ui", True)
    if "--accent" in argv:
        settings.override("accent_colors", True)
    TRANSLATIONS.use(settings["language"])
    return SpeedGTKApplication(settings, History()).run([argv[0]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
