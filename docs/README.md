# Documentation
Sphinx docs based on ReadTheDocs.

## Styleguide

- Each page's title is in `Title Case <https://en.wikipedia.org/wiki/Title_case>`_.
- Each subsection's title is in `Sentence case <https://en.wikipedia.org/wiki/Sentence_case>`_.

## Build and view locally

### Install dependencies
```bash
# full setup matching CI in .github/workflows/test-doc-build.yml
uv pip install --prerelease=allow "azure-cli>=2.65.0"
uv pip install ".[all]"
cd docs
uv pip install -r requirements-docs.txt
```

### Build and serve with live reload
```bash
./build.sh --watch --port 8000
```

### Build once and serve manually
```bash
./build.sh
# serve without rebuilding
python3 -m http.server 8000 --directory build/html
```

The documentation will be available at http://127.0.0.1:8000
