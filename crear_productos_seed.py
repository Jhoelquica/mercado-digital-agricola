import httpx

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZGNjYzg3OS1jM2U4LTRmZmYtYjBmMy01MjIyN2YzMGE1NzMiLCJyb2wiOiJwcm9kdWN0b3IiLCJleHAiOjE3ODcxNzgyMjh9.4ONhe8Q_MeyjqOHx1-jdH2f37MdXJsxBZoZHsOyyhPs"

productos = [
    {"nombre": "chirimoya", "categoria": "fruta", "precio": 6.0, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "fresa", "categoria": "fruta", "precio": 8.0, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "limon", "categoria": "fruta", "precio": 4.0, "stock": 30, "unidad_medida": "kg"},
    {"nombre": "mango", "categoria": "fruta", "precio": 5.0, "stock": 25, "unidad_medida": "kg"},
    {"nombre": "manzana", "categoria": "fruta", "precio": 4.5, "stock": 30, "unidad_medida": "kg"},
    {"nombre": "naranja", "categoria": "fruta", "precio": 3.5, "stock": 30, "unidad_medida": "kg"},
    {"nombre": "palta", "categoria": "fruta", "precio": 6.5, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "papaya", "categoria": "fruta", "precio": 4.0, "stock": 15, "unidad_medida": "kg"},
    {"nombre": "platano", "categoria": "fruta", "precio": 2.5, "stock": 40, "unidad_medida": "kg"},
    {"nombre": "sandia", "categoria": "fruta", "precio": 3.0, "stock": 15, "unidad_medida": "kg"},
    {"nombre": "kiwicha", "categoria": "grano", "precio": 9.0, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "maiz morado", "categoria": "grano", "precio": 6.0, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "maiz choclo", "categoria": "grano", "precio": 4.0, "stock": 25, "unidad_medida": "kg"},
    {"nombre": "trigo", "categoria": "grano", "precio": 5.0, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "cilantro", "categoria": "hierba", "precio": 2.0, "stock": 15, "unidad_medida": "kg"},
    {"nombre": "oregano", "categoria": "hierba", "precio": 3.0, "stock": 10, "unidad_medida": "kg"},
    {"nombre": "huevo de campo", "categoria": "huevo", "precio": 12.0, "stock": 30, "unidad_medida": "docena"},
    {"nombre": "huevo de codorniz", "categoria": "huevo", "precio": 8.0, "stock": 20, "unidad_medida": "docena"},
    {"nombre": "leche fresca", "categoria": "lacteo", "precio": 3.5, "stock": 20, "unidad_medida": "litro"},
    {"nombre": "queso", "categoria": "lacteo", "precio": 15.0, "stock": 15, "unidad_medida": "kg"},
    {"nombre": "yogurt natural", "categoria": "lacteo", "precio": 6.0, "stock": 20, "unidad_medida": "litro"},
    {"nombre": "arveja", "categoria": "legumbre", "precio": 5.0, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "habas", "categoria": "legumbre", "precio": 4.5, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "cebolla", "categoria": "verdura", "precio": 2.5, "stock": 30, "unidad_medida": "kg"},
    {"nombre": "lechuga", "categoria": "verdura", "precio": 2.0, "stock": 20, "unidad_medida": "kg"},
    {"nombre": "zanahoria", "categoria": "verdura", "precio": 2.5, "stock": 25, "unidad_medida": "kg"},
]

headers = {"Authorization": f"Bearer {TOKEN}"}

for p in productos:
    resp = httpx.post("http://localhost:8002/productos", json=p, headers=headers)
    if resp.status_code == 200:
        print(f"  ✔ creado: {p['nombre']}")
    else:
        print(f"  ✘ {p['nombre']}: {resp.status_code} - {resp.text}")