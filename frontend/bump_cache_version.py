"""Cache busting real para los estáticos del frontend (sin proceso de build: HTML/CSS/JS a mano).

Reescribe el "?v=" de css/style.css, js/api.js, js/app.js y js/verificar.js en index.html y
verificar.html con un hash corto (8 hex) del CONTENIDO ACTUAL de cada archivo. Como el valor sale
del contenido (no de un contador que hay que acordarse de subir a mano), es imposible reusar el
mismo "?v=" por error tras un cambio real: si el archivo cambió, el hash cambia solo; si no cambió,
el hash queda igual (no se invalida caché sin necesidad). El navegador nunca vuelve a servir una
versión vieja de style.css/app.js desde caché después de un refresh normal, sin depender de que el
usuario haga Ctrl+Shift+R.

Sin dependencias nuevas: usa solo hashlib/re/pathlib de la librería estándar — Python ya es una
dependencia del proyecto (ver crear_productos_seed.py, seed_imagenes.py, y el server de desarrollo
en .claude/launch.json).

Uso: correr este script después de editar css/style.css, js/api.js, js/app.js o js/verificar.js,
antes de commitear/desplegar.

    python bump_cache_version.py
"""
import hashlib
import re
from pathlib import Path

FRONTEND = Path(__file__).parent

# (ruta relativa tal como aparece en los <link>/<script>, ruta absoluta real en disco)
ARCHIVOS = [
    ("css/style.css", FRONTEND / "css" / "style.css"),
    ("js/api.js", FRONTEND / "js" / "api.js"),
    ("js/app.js", FRONTEND / "js" / "app.js"),
    ("js/verificar.js", FRONTEND / "js" / "verificar.js"),
]

# Páginas HTML que referencian esos estáticos y necesitan su "?v=" actualizado.
PAGINAS = [FRONTEND / "index.html", FRONTEND / "verificar.html"]


def hash_corto(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()[:8]


def main():
    hashes = {rel: hash_corto(abs_) for rel, abs_ in ARCHIVOS if abs_.exists()}

    for pagina in PAGINAS:
        if not pagina.exists():
            continue
        html = pagina.read_text(encoding="utf-8")
        original = html
        total_encontradas = 0
        for rel, h in hashes.items():
            # Reemplaza "ruta.ext" o "ruta.ext?v=loquesea" (una sola vez, dentro de href="..."/src="...")
            patron = re.compile(r'(href|src)="' + re.escape(rel) + r'(\?v=[0-9a-f]+)?"')
            html, n = patron.subn(lambda m: f'{m.group(1)}="{rel}?v={h}"', html)
            total_encontradas += n
        if not total_encontradas:
            print(f"{pagina.name}: AVISO - no se encontro ninguna referencia a los estaticos, revisar rutas")
        elif html != original:
            pagina.write_text(html, encoding="utf-8")
            print(f"{pagina.name}: actualizado")
        else:
            print(f"{pagina.name}: ya estaba al dia (sin cambios)")

    print()
    for rel, h in hashes.items():
        print(f"  {rel} -> ?v={h}")


if __name__ == "__main__":
    main()
