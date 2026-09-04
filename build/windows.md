# ShareG on Windows

## Run from source
```bash
pip install -r requirements.txt
python main.py
```

## Package as a standalone .exe (optional)
```bash
flet build windows
```
Produces a standalone exe; the icon and product name come from the
`[tool.flet]` section of `pyproject.toml` (`windows_icon`).
