# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Pruebas de comun/bandeja_salida.py contra el Postgres de las pruebas, con un publicador falso en lugar de RabbitMQ: el
evento vive y muere con la transacción del cambio, el relevo publica en orden de creación, se detiene en el primer
fallo, una sola réplica publica a la vez y nada se publica dos veces."""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pika
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from comun import bandeja_salida
from comun.bandeja_salida import EventoSaliente
from comun.database import SessionLocal, engine

EXCHANGE = "eventos_prueba"


def _marcar_todo_publicado():
    db = SessionLocal()
    try:
        (db.query(EventoSaliente).filter(EventoSaliente.fecha_envio.is_(None))
         .update({EventoSaliente.fecha_envio: datetime.utcnow()}, synchronize_session=False))
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def tabla():
    EventoSaliente.__table__.create(bind=engine, checkfirst=True)
    bandeja_salida.asegurar_esquema(engine, EXCHANGE)


@pytest.fixture(autouse=True)
def sin_pendientes_de_otras_pruebas():
    """Las pruebas del servicio dejan eventos sin publicar (en ellas no corre el relevo): aquí se dan por publicados."""
    _marcar_todo_publicado()
    yield
    _marcar_todo_publicado()


class PublicadorFalso:
    """Hace de RabbitMQ. Guarda lo que se publicó, y puede fallar al conectar o al publicar el evento con un n dado."""

    def __init__(self, falla_al_conectar=False, falla_en=None, demora=0.0):
        self.falla_al_conectar, self.falla_en, self.demora = falla_al_conectar, falla_en, demora
        self.publicados = []
        self._candado = threading.Lock()

    def __call__(self):
        return self

    def __enter__(self):
        if self.falla_al_conectar:
            raise pika.exceptions.AMQPConnectionError("RabbitMQ no responde")
        return self

    def publicar(self, exchange, cuerpo, evento_id):
        mensaje = json.loads(cuerpo)
        if self.falla_en is not None and mensaje.get("n") == self.falla_en:
            raise pika.exceptions.NackError([])
        time.sleep(self.demora)
        with self._candado:
            self.publicados.append((exchange, mensaje, evento_id))

    def __exit__(self, *_):
        return False

    def numeros(self):
        return [mensaje["n"] for _, mensaje, _ in self.publicados]


def _registrar(*numeros, tipo="algo_paso"):
    db = SessionLocal()
    try:
        ids = [bandeja_salida.registrar_evento(db, tipo, {"n": n}, EXCHANGE) for n in numeros]
        db.commit()
        return ids
    finally:
        db.close()


def _ciclo(publicador):
    db = SessionLocal()
    try:
        return bandeja_salida.publicar_pendientes(db, publicador)
    finally:
        db.close()


def _filas(ids):
    db = SessionLocal()
    try:
        filas = {fila.id: fila for fila in db.query(EventoSaliente).filter(EventoSaliente.id.in_(ids)).all()}
        return [filas[i] for i in ids]
    finally:
        db.close()


# ---- registrar_evento ----

def test_el_evento_sale_con_el_commit_del_cambio_y_no_con_su_rollback():
    db = SessionLocal()
    try:
        deshecho = bandeja_salida.registrar_evento(db, "algo_paso", {"n": 1}, EXCHANGE)
        assert _filas_existentes([deshecho]) == []  # todavía en la transacción: nadie más lo ve
        db.rollback()
        assert _filas_existentes([deshecho]) == []
        confirmado = bandeja_salida.registrar_evento(db, "algo_paso", {"n": 2}, EXCHANGE)
        db.commit()
    finally:
        db.close()
    [fila] = _filas([confirmado])
    assert fila.fecha_envio is None and fila.exchange == EXCHANGE and fila.intentos == 0


def _filas_existentes(ids):
    db = SessionLocal()
    try:
        return db.query(EventoSaliente).filter(EventoSaliente.id.in_(ids)).all()
    finally:
        db.close()


def test_el_mensaje_queda_en_json_con_su_nombre_y_su_evento_id():
    pedido_id = uuid.uuid4()
    db = SessionLocal()
    try:
        evento_id = bandeja_salida.registrar_evento(
            db, "pago_confirmado", {"pedido_id": pedido_id, "monto": Decimal("12.50"), "evento_id": "otro",
                                    "fecha": datetime(2026, 11, 18, 10, 30)}, EXCHANGE)
        db.commit()
    finally:
        db.close()
    [fila] = _filas([evento_id])
    assert fila.tipo == "pago_confirmado"
    assert json.loads(fila.cuerpo) == {"evento": "pago_confirmado", "pedido_id": str(pedido_id), "monto": "12.50",
                                       "fecha": "2026-11-18 10:30:00", "evento_id": str(evento_id)}


def test_un_nombre_distinto_en_los_datos_no_se_registra():
    db = SessionLocal()
    try:
        with pytest.raises(ValueError):
            bandeja_salida.registrar_evento(db, "pago_confirmado", {"evento": "pago_rechazado"}, EXCHANGE)
    finally:
        db.close()


# ---- El relevo ----

def test_publica_en_orden_de_creacion_y_marca_cada_uno():
    ids = _registrar(1, 2, 3) + _registrar(4, 5)
    publicador = PublicadorFalso()
    assert _ciclo(publicador) == 5
    assert publicador.numeros() == [1, 2, 3, 4, 5]
    assert [evento_id for _, _, evento_id in publicador.publicados] == [str(i) for i in ids]
    assert all(fila.fecha_envio is not None and fila.intentos == 1 for fila in _filas(ids))
    assert _ciclo(publicador) == 0 and len(publicador.publicados) == 5


def test_se_detiene_en_el_primer_fallo_y_despues_sigue_en_el_mismo_orden():
    ids = _registrar(1, 2, 3, 4)
    assert _ciclo(PublicadorFalso(falla_en=2)) == 1
    primero, segundo, tercero, cuarto = _filas(ids)
    assert primero.fecha_envio is not None
    assert segundo.fecha_envio is None and segundo.intentos == 1  # se intentó y RabbitMQ no lo confirmó
    assert (tercero.fecha_envio, tercero.intentos, cuarto.fecha_envio) == (None, 0, None)  # ni se intentaron
    publicador = PublicadorFalso()
    assert _ciclo(publicador) == 3 and publicador.numeros() == [2, 3, 4]


def test_si_rabbitmq_no_responde_no_se_marca_nada():
    ids = _registrar(1, 2)
    assert _ciclo(PublicadorFalso(falla_al_conectar=True)) == 0
    assert [(fila.fecha_envio, fila.intentos) for fila in _filas(ids)] == [(None, 0), (None, 0)]


def test_si_otra_replica_tiene_el_turno_este_ciclo_no_publica():
    _registrar(1)
    otra_replica = SessionLocal()
    try:
        assert otra_replica.execute(text(bandeja_salida.CANDADO_DEL_RELEVO)).scalar() is True
        publicador = PublicadorFalso()
        assert _ciclo(publicador) is None and publicador.publicados == []
    finally:
        otra_replica.rollback()  # suelta el candado
        otra_replica.close()
    assert _ciclo(publicador) == 1


def test_tres_relevos_a_la_vez_publican_cada_evento_una_sola_vez_y_en_orden(monkeypatch):
    monkeypatch.setattr(bandeja_salida, "LOTE", 4)  # muchos ciclos cortos: los relevos se turnan
    _registrar(*range(1, 41))
    publicador = PublicadorFalso(demora=0.002)
    errores = []

    def relevo():
        try:
            for _ in range(200):
                if _ciclo(publicador) == 0 and len(publicador.publicados) >= 40:
                    return
        except Exception as error:  # noqa: BLE001 - la prueba lo muestra abajo
            errores.append(error)

    hilos = [threading.Thread(target=relevo) for _ in range(3)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)
    assert errores == []
    assert publicador.numeros() == list(range(1, 41))


def test_borra_solo_los_publicados_hace_mas_de_los_dias_de_retencion():
    ahora = datetime.utcnow()
    viejo_publicado, reciente, viejo_pendiente = _registrar(1, 2, 3)
    db = SessionLocal()
    try:
        for evento_id, creado, enviado in ((viejo_publicado, ahora - timedelta(days=40), ahora - timedelta(days=40)),
                                           (reciente, ahora - timedelta(days=1), ahora - timedelta(days=1)),
                                           (viejo_pendiente, ahora - timedelta(days=40), None)):
            db.query(EventoSaliente).filter(EventoSaliente.id == evento_id).update(
                {EventoSaliente.fecha_creacion: creado, EventoSaliente.fecha_envio: enviado}, synchronize_session=False)
        db.commit()
        assert bandeja_salida.borrar_publicados_viejos(db, dias=30, ahora=ahora) >= 1
        db.commit()
    finally:
        db.close()
    assert {fila.id for fila in _filas_existentes([viejo_publicado, reciente, viejo_pendiente])} == {
        reciente, viejo_pendiente}


class _SesionQueFalla:
    def execute(self, *_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("la base no responde"))

    def rollback(self):
        raise RuntimeError("tampoco se pudo deshacer")

    def close(self):
        raise RuntimeError("ni cerrar")


def test_un_ciclo_nunca_lanza_aunque_falle_la_base_y_tambien_el_rollback():
    assert bandeja_salida.una_pasada(_SesionQueFalla) is False
    assert bandeja_salida.limpiar_una_vez(_SesionQueFalla) is False


class Salir(BaseException):
    """Corta el bucle del relevo (que no termina nunca): no es Exception, así el bucle no la atrapa."""


def test_el_hilo_del_relevo_sigue_vivo_tras_un_error_y_espera_mas_si_falla_seguido(monkeypatch):
    esperas = []

    def dormir(segundos):
        esperas.append(segundos)
        if len(esperas) == 4:
            raise Salir()

    def pasada_que_falla():
        raise RuntimeError("algo cambió y ya no se atrapa adentro")

    monkeypatch.setattr(bandeja_salida, "una_pasada", pasada_que_falla)
    monkeypatch.setattr(bandeja_salida, "limpiar_una_vez", lambda: True)
    monkeypatch.setattr(bandeja_salida.time, "sleep", dormir)
    with pytest.raises(Salir):
        bandeja_salida._relevar_siempre()
    assert len(esperas) == 4 and esperas == sorted(esperas) and esperas[-1] > esperas[0]
    assert max(esperas) <= bandeja_salida.ESPERA_MAXIMA_TRAS_FALLOS


# ---- El esquema de antes (la bandeja de Pedidos) ----

def test_asegurar_esquema_completa_una_bandeja_de_antes_y_numera_los_pendientes_en_su_orden():
    vieja = "eventos_salientes_prueba_vieja"
    ahora = datetime.utcnow()
    publicado, pendiente_nuevo, pendiente_viejo = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conexion:
        conexion.execute(text(f'DROP TABLE IF EXISTS "{vieja}"'))
        conexion.execute(text(f'CREATE TABLE "{vieja}" (id UUID PRIMARY KEY, tipo VARCHAR NOT NULL, cuerpo TEXT NOT NULL, '
                              'fecha_creacion TIMESTAMP NOT NULL, fecha_envio TIMESTAMP, intentos INTEGER NOT NULL)'))
        for evento_id, creado, enviado in ((publicado, ahora - timedelta(hours=3), ahora - timedelta(hours=3)),
                                           (pendiente_nuevo, ahora - timedelta(minutes=1), None),
                                           (pendiente_viejo, ahora - timedelta(minutes=5), None)):
            conexion.execute(text(f'INSERT INTO "{vieja}" VALUES (:id, \'pedido_creado\', \'{{}}\', :creado, :enviado, 0)'),
                             {"id": evento_id, "creado": creado, "enviado": enviado})
    try:
        bandeja_salida.asegurar_esquema(engine, "eventos_pedidos", tabla=vieja)
        bandeja_salida.asegurar_esquema(engine, "eventos_pedidos", tabla=vieja)  # la segunda vez no cambia nada
        with engine.begin() as conexion:
            nuevo = uuid.uuid4()
            conexion.execute(text(f'INSERT INTO "{vieja}" (id, exchange, tipo, cuerpo, fecha_creacion, intentos) '
                                  "VALUES (:id, 'eventos_pedidos', 'pedido_creado', '{}', now(), 0)"), {"id": nuevo})
            filas = {fila.id: fila for fila in conexion.execute(text(f'SELECT id, exchange, secuencia FROM "{vieja}"'))}
            indices = {fila[0] for fila in conexion.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = :tabla"), {"tabla": vieja})}
        assert {fila.exchange for fila in filas.values()} == {"eventos_pedidos"}
        assert (filas[publicado].secuencia < filas[pendiente_viejo].secuencia < filas[pendiente_nuevo].secuencia
                < filas[nuevo].secuencia)
        assert {f"ix_{vieja}_pendientes", f"ix_{vieja}_fecha_envio"} <= indices
    finally:
        with engine.begin() as conexion:
            conexion.execute(text(f'DROP TABLE IF EXISTS "{vieja}"'))
