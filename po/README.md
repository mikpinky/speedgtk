# Translating SpeedGTK

The source strings are English. Translations live in this folder as plain
gettext `.po` files, **read directly at runtime** — there is no build step and
no `.mo` compilation. Edit a `.po`, restart the app, and your changes are live.

Currently shipped: `it`, `de`, `fr`, `es`, `ru` (plus English, the source
language, which needs no file).

## Adding a language

1. Copy the template, naming the file after the language code you are adding
   (two letters, as in `pt.po`, `nl.po`, `pl.po`):

   ```bash
   cp speedgtk.pot pt.po
   ```

2. Fill in the header — at least `Language:` and `Plural-Forms:` — then
   translate every `msgstr`. An empty `msgstr` falls back to the English text,
   so a partial translation is fine to start with.

3. Restart SpeedGTK. The new language appears by itself in
   *Preferences → Appearance → Language*: the app lists one entry per `.po`
   found here.

Two details worth knowing:

- **Language names are translated too.** The strings `Italian`, `German`,
  `French`… are the names shown in the language menu, so in a Portuguese
  translation they become `Italiano`, `Alemão`, `Francês`. Translate them into
  *your* language, not into the language they name.
- **Add your language name to the other files** if you want it to show up
  there as well, and add the new code to `LANGUAGE_ORDER` in `speedgtk.py` to
  control where it sits in the menu (unknown codes still work, they just fall
  to the end of the known ones).

## Placeholders

Some strings contain named placeholders in braces, such as
`Saved tests: {count}` or `Saved in {path}`. Keep the names exactly as they
are — they are filled in by the code — but move them wherever your language
needs them.

`%d/%m/%Y %H:%M` is a date format, not a sentence: reorder the fields to suit
local convention (for example `%m/%d/%Y %I:%M %p` or `%d.%m.%Y %H:%M`). The
available codes are the usual `strftime` ones.

Strings that look like `<tt>--format=jsonl</tt>` carry Pango markup: leave the
tags in place, translate only the words around them. Command lines and option
names (`--plain`, `speedtest-cli`) are not translatable and never appear here.

## Checking your work

`msgfmt` is not needed to run the app, but it is a good linter:

```bash
msgfmt --check --statistics -o /dev/null pt.po
```

## After the source strings change

Regenerate the template and merge it into the existing translations:

```bash
xgettext --language=Python --keyword=_ --keyword=N_ --from-code=UTF-8 \
         --package-name=SpeedGTK --sort-by-file \
         -o po/speedgtk.pot speedgtk.py
for f in po/*.po; do msgmerge --update --backup=none "$f" po/speedgtk.pot; done
```

`msgmerge` marks changed entries `#, fuzzy`. The runtime parser skips fuzzy
entries on purpose and shows the English text instead, so a stale translation
never silently replaces a corrected string — review them and drop the flag.
