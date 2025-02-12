# Documentation
Sphinx docs based on ReadTheDocs.

## Styleguide

- Each page's title is in `Title Case <https://en.wikipedia.org/wiki/Title_case>`_.
- Each subsection's title is in `Sentence case <https://en.wikipedia.org/wiki/Sentence_case>`_.

## Build
```bash
pip install -r requirements-docs.txt
./build.sh
```

## View
```bash
cd build/html
python -m http.server
```
Open localhost:8000 in your browser.
