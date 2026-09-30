# Serializer for ForgeFIRM

`org.openglow.serializer`, an official ForgeFIRM extension package: serial
numbers, dates, and your own text marked on one item or a grid of them,
cycle after cycle. Close the lid, press the button, and the next numbers go
out.

It is also an example of a package with a Python service and a page that
asks it: the service keeps the profiles and counters and runs the cycles
(`bin/run.py`, `lib/serializer/`), and the page is its screen
(`ui/index.html`).

| Subject | Page |
|---|---|
| Using it | [Serializer](https://docs.forgefirm.org/usage/extensions/serializer/) |
| A page and its own service | [A page and its own service](https://docs.forgefirm.org/technical/forgefirm/extensions/#a-page-and-its-own-service) |
| A repository like this one, and its workflow | [A repository for your package](https://docs.forgefirm.org/developers/extensions/#a-repository-for-your-package) |

## Working on it

| Command | Does |
|---|---|
| `make test` | The service's tests: counters, dates, fonts, fills, programs, the run against a stand-in machine (`tests/fake.py`), and the page's calls |
| `make test-page` | The page itself through a whole run, in a headless browser, in a stand-in panel (`tests/harness.py`) |
| `python3 tests/harness.py` | The stand-in panel to use by hand, with buttons for the machine's lid and button: http://127.0.0.1:8097/ |
| `make stage`, `make lint`, `make pack` | The package as it ships, in `build/pkg` |

`tools/` holds what makes the shipped data: `fetch_fonts.py` (the outline
fonts, from Google Fonts), `stroke_gen.py` (the single-line fonts, from
Inkscape's SVG fonts), `samples_gen.py` (the font picker's samples), and
`bridge.py` (the panel's bridge client, pasted into the page).

## License

The code is MIT; see [`LICENSE`](LICENSE). The fonts keep their own
licenses: the outline fonts are under the SIL Open Font License or the
Apache License (`share/fonts/licenses/`), and the single-line fonts are the
Hershey fonts and the EMS fonts (`share/stroke/HERSHEY.txt`,
`share/stroke/OFL.txt`, `share/stroke/NOTICE.txt`).
