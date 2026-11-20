"""Migraciones propias de Pedidos: asegurar_un_pendiente_por_comprador, el índice único parcial que deja un solo pedido
sin pagar por comprador (create_all no agrega índices a una tabla que ya existe).

La de las columnas nuevas de una tabla que ya existe es común: agregar_columnas_faltantes, en comun/migraciones.py.
"""
import logging

from sqlalchemy import text

from models import INDICE_UN_PENDIENTE_POR_COMPRADOR

logger = logging.getLogger("pedidos.migraciones")

DEFINICION_UN_PENDIENTE_POR_COMPRADOR = "ON pedidos (usuario_id) WHERE estado = 'pendiente'"


def compradores_con_varios_pendientes(conexion) -> list:
    """Los compradores que hoy tienen más de un pedido "pendiente": con alguno, el índice no se puede crear."""
    return [str(fila[0]) for fila in conexion.execute(text(
        "SELECT usuario_id FROM pedidos WHERE estado = 'pendiente' AND usuario_id IS NOT NULL "
        "GROUP BY usuario_id HAVING count(*) > 1 ORDER BY usuario_id"))]


def asegurar_un_pendiente_por_comprador(engine) -> bool:
    """Crea el índice si falta (idempotente; varias réplicas pueden arrancar a la vez), después de comprobar que ningún
    comprador tiene dos pendientes: si alguno los tiene no se crea (fallaría igual), se registra quiénes son y el servicio
    arranca. Hay que resolverlos (que se paguen, se cancelen o venzan) y reiniciar; verificar_despliegue.py da FALLA
    mientras el índice falte. Nunca lanza. Devuelve si el índice quedó garantizado."""
    try:
        with engine.begin() as conexion:
            if conexion.execute(text("SELECT to_regclass(:nombre) IS NOT NULL"),
                                {"nombre": INDICE_UN_PENDIENTE_POR_COMPRADOR}).scalar():
                return True
            repetidos = compradores_con_varios_pendientes(conexion)
            if repetidos:
                logger.error("No se crea el índice %s: %d comprador(es) tienen más de un pedido pendiente (%s). Hay que "
                             "resolverlos y reiniciar; mientras tanto la base no impide un segundo pendiente.",
                             INDICE_UN_PENDIENTE_POR_COMPRADOR, len(repetidos), ", ".join(repetidos[:10]))
                return False
            conexion.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_UN_PENDIENTE_POR_COMPRADOR} "
                                  f"{DEFINICION_UN_PENDIENTE_POR_COMPRADOR}"))
    except Exception:
        logger.exception("No se pudo crear el índice %s: mientras falte, la base no impide un segundo pedido pendiente "
                         "del mismo comprador.", INDICE_UN_PENDIENTE_POR_COMPRADOR)
        return False
    logger.info("Índice %s listo: un solo pedido pendiente por comprador", INDICE_UN_PENDIENTE_POR_COMPRADOR)
    return True
