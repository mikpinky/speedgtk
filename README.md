# SpeedGTK

<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.speedgtk.SpeedGTK.svg" width="128" alt="SpeedGTK icon">
</p>

SpeedGTK is a native GTK 4 and libadwaita interface for the official [Ookla
Speedtest CLI](https://www.speedtest.net/apps/cli), **inspired by the official
Windows and Mac apps made by Ookla**. It brings download, upload,
idle/download/upload latency, jitter and packet-loss measurements into a modern
GNOME application.

## Features & Highlights

- Live speed gauge with extended multi-gigabit scale.
- Curated animations and icons for a sleek interface.
- Support for a simpler, classic interface.
- Idle, download and upload latency, plus jitter and packet loss.
- Server selection and a manual server ID.
- Local history with sorting by date, download, upload, ping or weighted
  overall result.
- Light, dark and system theme options; follows the GNOME accent color when
  enabled.
- Interface available in English, Italian, German, Spanish, French and
  Russian.

## Screenshots & Demos

Media assets are organized in [`docs/media/`](docs/media/). The section is
ready for the following files:

| Asset | File name |
| --- | --- |
| Download demo | `docs/media/download-demo.mp4` |
| Upload demo | `docs/media/upload-demo.mp4` |
| Main window screenshot | `docs/media/main-window.png` |
| History screenshot | `docs/media/history.png` |

<!-- Replace these slots with embedded media after the files are added. -->

<!-- Download demo: docs/media/download-demo.mp4 -->
<!-- Upload demo: docs/media/upload-demo.mp4 -->

## Requirements

- Linux with GTK 4 and libadwaita.
- Python 3 with PyGObject bindings for GTK 4 and libadwaita.
- The official [Ookla Speedtest CLI](https://www.speedtest.net/apps/cli),
  available as the `speedtest` command.

The application checks the command when it starts. On Debian-derived
distributions it also displays copyable installation commands; other
distributions are directed to Ookla's official installation page.

## Install

Clone the repository and install for the current user:

```bash
git clone https://github.com/mikpinky/speedgtk.git
cd speedgtk
make install-user
```

After installation, launch **SpeedGTK** from the application menu or run:

```bash
speedgtk
```

For a system-wide installation:

```bash
sudo make install
```

To remove a user installation:

```bash
make uninstall-user
```

## Development

Run the basic syntax check with:

```bash
make check
```

Translations are stored in [`po/`](po/). The project intentionally depends on
the official Ookla CLI rather than the unrelated Python `speedtest-cli`
utility, whose output format is incompatible with this application.

## Support

Found a bug or have an idea? [Open an issue](https://github.com/mikpinky/speedgtk/issues).

## License

SpeedGTK is released under the [MIT License](LICENSE).
