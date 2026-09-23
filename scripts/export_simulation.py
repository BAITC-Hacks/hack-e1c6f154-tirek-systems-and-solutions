"""Bundle the dependency-free simulation into one portable HTML file."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1] / "simulation"
html = (source / "index.html").read_text(encoding="utf-8")
css = (source / "styles.css").read_text(encoding="utf-8")
html = html.replace('<link rel="stylesheet" href="styles.css">', f"<style>{css}</style>")
scripts = []
for name in ("monitor-core.js", "app.js"):
    html = html.replace(f'<script defer src="{name}"></script>', "")
    script = (source / name).read_text(encoding="utf-8").replace("</script", "<\\/script")
    scripts.append(f"<script>{script}</script>")
html = html.replace('href="./index.html"', 'href="#"')
html = html.replace("</body>", "\n".join(scripts) + "\n</body>")
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(html, encoding="utf-8")
print(args.output.resolve())
