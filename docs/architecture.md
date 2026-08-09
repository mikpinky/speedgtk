# Architecture

SpeedGTK is a Python package under `src/speedgtk`. The GTK layer depends on the
application services and domain rules; lower-level modules never import the UI.

```text
__main__ -> application -> ui -> speedtest
                         |  `-> domain
                         `----> storage
```

## Package boundaries

- `application.py` owns command-line parsing and the `Adw.Application`
  lifecycle.
- `config.py`, `i18n.py`, and `formatting.py` provide shared metadata,
  translations, and presentation formatting.
- `domain/` contains pure history validation, ranking, and result mapping.
- `storage/` owns the JSON settings and history files.
- `speedtest/` owns Gio subprocesses, JSONL parsing, and CLI error handling.
- `ui/main_window.py` coordinates the current test and top-level navigation.
- `ui/results_view.py` owns measurement state and the gauge/classic views.
- `ui/server_picker.py` owns server-list state and manual-ID validation.
- `ui/dialogs/` contains independent dialog presenters.
- `ui/widgets/` contains reusable Cairo widgets. The gauge glow is isolated in
  `gauge_glow.py` so its rendering can be tested independently from animation
  and scale state.

## Runtime flow

1. `SpeedGTKApplication` creates a `SpeedGTKWindow` with shared settings and
   history stores.
2. The window validates the official Ookla CLI and loads nearby servers.
3. `SpeedtestRun` reads JSONL events asynchronously on the GLib main loop.
4. The window coordinates each event while `MeasurementsView` renders values
   and `ResultDetails` renders server and ISP metadata.
5. A successful final event is mapped to the stable history schema by the
   pure domain layer before being persisted.

## Compatibility rules

- Keep settings and history file paths and JSON schemas backward compatible.
- Keep source translation strings stable unless catalogs are regenerated.
- Never block the GLib main loop or stop draining child-process pipes.
- Pass Ookla acceptance flags only after explicit user consent.
- Keep custom drawing inside `ui/widgets`; process and domain code must not
  depend on GTK widgets.

## Verification

`make check` compiles every package module and runs the unit tests. `make run`
starts the application from the source tree. Installation remains Makefile
based and copies the full package plus PO catalogs into the configured data
directory.
