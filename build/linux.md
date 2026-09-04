# ShareG on Linux

## Run from source
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Desktop runtime libs (Debian/Ubuntu):
```bash
sudo apt install libmpv2 libglfw3 libgtk-3-0
```

## Package as a standalone Linux bundle (optional)
```bash
flet build linux
```
