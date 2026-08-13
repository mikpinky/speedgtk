# Architecture

SpeedGTK is a Python package under `src/speedgtk`. The GTK layer depends on the
application services and domain rules; lower-level modules never import the UI.

```text
__main__ -> application -> ui -> speedtest/process
                         |  |-> speedtest/providers/ookla
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
- `speedtest/process.py` owns provider-neutral Gio subprocess management.
- `speedtest/providers/ookla/` owns the active Ookla CLI adapter, including
  constants, JSONL parsing, error handling, the streaming run lifecycle, and
  the optional isolated directory client for worldwide search and exact
  server-ID resolution.
- `speedtest/providers/librespeed/` reserves the boundary for a future
  LibreSpeed adapter. It intentionally contains no implementation yet, so a
  later change can choose between direct HTTP and `librespeed-cli` integration.
- `ui/main_window.py` coordinates the current test and top-level navigation.
- `ui/results_view.py` owns measurement state and the gauge/classic views.
- `ui/server_picker.py` owns server-list state and selection precedence;
  `ui/dialogs/server_selector.py` owns the advanced selector interface while
  `ui/dialogs/server_id.py` isolates numeric input and remote verification.
- `ui/dialogs/` contains independent dialog presenters.
- `ui/presentation/` owns visual timing that may intentionally lag behind the
  provider event stream without delaying the actual speed test. The initial
  download ramp preserves provider timing; stable samples replay faster only
  while the presentation catches up to the live stream.
- `ui/widgets/` contains reusable visual components. `server_context.py`
  animates the handoff between server selection and active-test metadata;
  `gauge.py` composes gauge state, ticks and needle while `gauge_face.py`,
  `gauge_glow.py`, and `gauge_readout.py` isolate its drawing layers.
- `progress.py` only renders the bottom bar; `progress_timeline.py` smooths
  provider samples and sequences phase completion and fades.
- `ui/widgets/gauge_animation/` separates the reversible scale timeline, pure
  polar geometry and easing functions, and the final needle-reset controller.
  Animation tuning therefore stays independent from Cairo rendering.

## Runtime flow

1. `SpeedGTKApplication` creates a `SpeedGTKWindow` with shared settings and
   history stores.
2. The window validates the official Ookla CLI and loads nearby servers. The
   optional advanced selector can also query Speedtest.net by place/provider
   or resolve an exact numeric server ID without blocking the GTK main loop.
3. The selector keeps its query, results, and verified-ID state within the UI
   session; only the chosen ID is passed to the external CLI.
4. `OoklaRun` reads JSONL events asynchronously on the GLib main loop.
5. The window coordinates each event while `MeasurementsView` renders values
   and `ResultDetails` renders server and ISP metadata.
6. A successful final event is mapped to the stable history schema by the
   pure domain layer before being persisted.

## Compatibility rules

- Keep settings and history file paths and JSON schemas backward compatible.
- Keep source translation strings stable unless catalogs are regenerated.
- Never block the GLib main loop or stop draining child-process pipes.
- Every CLI subprocess must be owned and force-terminated during window or
  application shutdown.
- Provider adapters own parsing and emit the application event shape consumed
  by the UI; provider-specific parsing details stay inside their package.
- Directory searches use anonymous requests without cookies or stored tokens;
  response fields related to client identity or authentication are discarded.
- Pass Ookla acceptance flags only after explicit user consent.
- Keep custom drawing inside `ui/widgets`; process and domain code must not
  depend on GTK widgets.

## Verification

`make check` compiles every package module and runs the unit tests. `make run`
starts the application from the source tree. Installation remains Makefile
based and copies the full package plus PO catalogs into the configured data
directory.
