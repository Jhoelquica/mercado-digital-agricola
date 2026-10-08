"""Genera k8s/grafana-provisioning.yaml (uso: python scripts/generar_tablero_grafana.py): fuente de datos Prometheus + tablero de los 8 servicios (RNF05), cargados por
Grafana al arrancar (antes se armaban a mano en la interfaz y se perdían al reiniciar el pod)."""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SERVICIOS = ["usuarios", "productores", "productos", "pedidos", "transporte", "notificaciones", "pagos", "certificacion"]
# Paleta categórica de referencia (skill dataviz), pasos para fondo oscuro; validada sobre #181b1f (fondo de panel de
# Grafana): CVD ΔE 8,4 · visión normal 19,3 · contraste >= 3:1. Orden fijo: el color sigue al servicio en todos los paneles.
COLORES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
DS = {"type": "prometheus", "uid": "prometheus"}
JOBS = "|".join(SERVICIOS)

overrides_color = [
    {"matcher": {"id": "byName", "options": s},
     "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
    for s, c in zip(SERVICIOS, COLORES)
]


def serie(titulo, expr, unidad, x, y, descripcion, w=12, h=9, decimales=None, tope_suave=None):
    defaults = {"unit": unidad, "min": 0, "color": {"mode": "fixed", "fixedColor": COLORES[0]},
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0, "showPoints": "never",
                           "spanNulls": True, "axisSoftMin": 0}}
    if decimales is not None:
        defaults["decimals"] = decimales
    if tope_suave is not None:  # sin errores la escala no salta a 0–100
        defaults["custom"]["axisSoftMax"] = tope_suave
    return {
        "type": "timeseries", "title": titulo, "description": descripcion, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "datasource": DS, "expr": expr, "legendFormat": "{{job}}", "range": True}],
        "fieldConfig": {"defaults": defaults, "overrides": overrides_color},
        "options": {"tooltip": {"mode": "multi", "sort": "desc"},
                    "legend": {"showLegend": True, "displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}},
    }


def indicador(titulo, expr, unidad, x, descripcion, umbrales=None, decimales=None):
    defaults = {"unit": unidad, "color": {"mode": "thresholds"},
                "thresholds": umbrales or {"mode": "absolute", "steps": [{"color": "text", "value": None}]}}
    if decimales is not None:
        defaults["decimals"] = decimales
    return {
        "type": "stat", "title": titulo, "description": descripcion, "datasource": DS,
        "gridPos": {"x": x, "y": 0, "w": 6, "h": 4},
        "targets": [{"refId": "A", "datasource": DS, "expr": expr, "instant": True}],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "none", "textMode": "value", "justifyMode": "center"},
    }


cero_por_servicio = f'(sum by (job) (rate(http_requests_total{{job=~"{JOBS}"}}[5m])) * 0)'
p95 = 'histogram_quantile(0.95, sum by (job, le) (rate(http_request_duration_highr_seconds_bucket{{job=~"{j}"}}[{v}])))'

panels = [
    indicador("Servicios en línea", f'sum(up{{job=~"{JOBS}"}})', "suffix: de 8", 0,
              "Cuántos de los 8 microservicios responde a Prometheus ahora mismo.",
              {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 8}]}),
    indicador("Peticiones/s (total)", f'sum(rate(http_requests_total{{job=~"{JOBS}"}}[5m]))', "reqps", 6,
              "Tráfico de los 8 servicios juntos, promedio de los últimos 5 minutos.", decimales=2),
    indicador("Latencia p95", f'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{job=~"{JOBS}"}}[5m])))',
              "s", 12, "El 95 % de las respuestas tarda menos que esto (últimos 5 minutos).", decimales=1),
    indicador("Errores 5xx (1 h)", f'sum(increase(http_requests_total{{job=~"{JOBS}",status="5xx"}}[1h])) or vector(0)',
              "none", 18, "Respuestas con error del servidor en la última hora, sumando los 8 servicios.",
              {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}, decimales=0),
    serie("Tráfico por servicio", f'sum by (job) (rate(http_requests_total{{job=~"{JOBS}"}}[5m]))', "reqps", 0, 4,
          "Peticiones por segundo de cada servicio (ventana de 5 minutos).", decimales=2),
    serie("Latencia p95 por servicio", p95.format(j=JOBS, v="5m"), "s", 12, 4,
          "Percentil 95 del tiempo de respuesta de cada servicio (ventana de 5 minutos)."),
    serie("Errores 5xx por servicio", f'sum by (job) (rate(http_requests_total{{job=~"{JOBS}",status="5xx"}}[5m])) or {cero_por_servicio}',
          "reqps", 0, 13, "Respuestas 5xx por segundo de cada servicio; en 0 mientras no haya errores.", decimales=2, tope_suave=0.1),
    {
        "type": "table", "title": "Resumen por servicio (última hora)", "datasource": DS,
        "description": "La misma información en tabla: tráfico, latencia p95 y errores 5xx de cada servicio en la última hora.",
        "gridPos": {"x": 12, "y": 13, "w": 12, "h": 9},
        "targets": [
            {"refId": "A", "datasource": DS, "expr": f'sum by (job) (rate(http_requests_total{{job=~"{JOBS}"}}[1h]))', "instant": True, "format": "table"},
            {"refId": "B", "datasource": DS, "expr": p95.format(j=JOBS, v="1h"), "instant": True, "format": "table"},
            {"refId": "C", "datasource": DS, "expr": f'sum by (job) (increase(http_requests_total{{job=~"{JOBS}",status="5xx"}}[1h])) or {cero_por_servicio}', "instant": True, "format": "table"},
            {"refId": "D", "datasource": DS, "expr": f'up{{job=~"{JOBS}"}}', "instant": True, "format": "table"},
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "instance": True},
                "indexByName": {"job": 0, "Value #D": 1, "Value #A": 2, "Value #B": 3, "Value #C": 4},
                "renameByName": {"job": "Servicio", "Value #D": "En línea", "Value #A": "Peticiones/s",
                                 "Value #B": "Latencia p95", "Value #C": "Errores 5xx (1 h)"}}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": [
            {"matcher": {"id": "byName", "options": "Peticiones/s"}, "properties": [{"id": "unit", "value": "reqps"}, {"id": "decimals", "value": 3}]},
            {"matcher": {"id": "byName", "options": "Latencia p95"}, "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 1}]},
            {"matcher": {"id": "byName", "options": "Errores 5xx (1 h)"}, "properties": [{"id": "decimals", "value": 0}]},
            {"matcher": {"id": "byName", "options": "Servicio"}, "properties": [{"id": "custom.width", "value": 130}]},
            {"matcher": {"id": "byName", "options": "En línea"}, "properties": [
                {"id": "mappings", "value": [{"type": "value", "options": {"1": {"text": "Sí", "color": "green"}, "0": {"text": "No", "color": "red"}}}]},
                {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "custom.width", "value": 80}]},
        ]},
        "options": {"showHeader": True, "sortBy": [{"displayName": "Servicio", "desc": False}]},
    },
]
for i, p in enumerate(panels, start=1):
    p["id"] = i

tablero = {
    "uid": "chakrashop-servicios", "title": "ChakraShop — Microservicios", "tags": ["chakrashop"],
    "timezone": "America/Lima", "refresh": "30s", "time": {"from": "now-3h", "to": "now"},
    "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 1, "panels": panels,
}

json_tablero = json.dumps(tablero, ensure_ascii=False, indent=2)
sangrado = "\n".join("    " + linea for linea in json_tablero.splitlines())

yaml = f"""# Grafana arranca ya configurado (RNF05): la fuente de datos Prometheus y el tablero de los 8 servicios (tráfico,
# latencia p95 y errores 5xx) se cargan desde estos ConfigMaps. Antes se armaban a mano en la interfaz y, sin volumen
# persistente, se perdían cada vez que el pod se reiniciaba. Lo genera scripts/generar_tablero_grafana.py.
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-fuentes
data:
  prometheus.yaml: |
    apiVersion: 1
    datasources:
      - name: Prometheus
        uid: prometheus
        type: prometheus
        access: proxy
        url: http://prometheus:9090
        isDefault: true
        editable: false
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-proveedor-tableros
data:
  chakrashop.yaml: |
    apiVersion: 1
    providers:
      - name: chakrashop
        folder: ChakraShop
        type: file
        disableDeletion: true
        allowUiUpdates: false
        options:
          path: /var/lib/grafana/dashboards/chakrashop
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-tableros
data:
  servicios.json: |
{sangrado}
"""
(RAIZ / "k8s/grafana-provisioning.yaml").write_text(yaml, encoding="utf-8")
print("k8s/grafana-provisioning.yaml escrito,", len(panels), "paneles")
