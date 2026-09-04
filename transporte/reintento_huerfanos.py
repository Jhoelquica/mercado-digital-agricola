"""Red de seguridad para envíos huérfanos: un hilo en segundo plano (mismo patrón que
rabbitmq_consumer.lanzar_consumidor_en_hilo — hilo daemon con loop y time.sleep) que cada
INTERVALO_SEGUNDOS revisa TODOS los envíos en "pendiente_asignacion" y reintenta asignarlos.

Es el respaldo del disparador inmediato (logica_repartidores.intentar_resolver_envio_huerfano,
llamado justo cuando un repartidor pasa a "disponible") — cubre los casos que ese disparador se
pierda, por ejemplo si el servicio se reinició justo en el momento crítico entre que un
repartidor quedó libre y se intentó reasignar el envío, o si el disparador inmediato de un caso
no está cableado (nunca cubre el 100% de los lugares donde un repartidor podría liberarse).

Reutiliza EXACTAMENTE proponer_envio_a_repartidor — no hay lógica de asignación duplicada acá,
solo el bucle que decide CUÁNDO reintentar.
"""
import time
import threading

from database import SessionLocal
import models
from logica_repartidores import proponer_envio_a_repartidor

INTERVALO_SEGUNDOS = 60


def _revisar_envios_huerfanos():
    db = SessionLocal()
    try:
        huerfanos = (
            db.query(models.Envio)
            .filter(models.Envio.estado == "pendiente_asignacion")
            .order_by(models.Envio.fecha_creacion.asc())
            .all()
        )
        if not huerfanos:
            return

        print(f"[Transporte] Job periódico: {len(huerfanos)} envío(s) huérfano(s), reintentando asignación...")
        # Secuencial con la misma sesión: si el primero se lleva al único repartidor
        # disponible, proponer_envio_a_repartidor deja correctamente a los siguientes en
        # "pendiente_asignacion" otra vez (sin repartidor que ofrecerles) — no hay condición de
        # carrera acá porque es un solo hilo recorriendo la lista, uno a la vez.
        for envio in huerfanos:
            proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()


def _bucle_reintento():
    while True:
        try:
            _revisar_envios_huerfanos()
        except Exception as e:
            print(f"[Transporte] Job periódico de envíos huérfanos falló, reintenta en {INTERVALO_SEGUNDOS}s: {e}")
        time.sleep(INTERVALO_SEGUNDOS)


def lanzar_reintento_huerfanos_en_hilo():
    hilo = threading.Thread(target=_bucle_reintento, daemon=True)
    hilo.start()
