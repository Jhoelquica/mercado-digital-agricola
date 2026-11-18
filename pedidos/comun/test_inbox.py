# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Pruebas de comun/inbox.py contra el Postgres de las pruebas: la marca de evento procesado vive y muere con la
transacción del efecto, y dos entregas a la vez no procesan las dos."""
import threading
import uuid

import pytest

from comun import inbox
from comun.database import SessionLocal, engine


@pytest.fixture(scope="module", autouse=True)
def tabla():
    inbox.EventoProcesado.__table__.create(bind=engine, checkfirst=True)


@pytest.fixture
def consumidor():
    """Un consumidor propio de cada prueba: no choca con lo que anoten las demás."""
    nombre = f"prueba-{uuid.uuid4()}"
    yield nombre
    db = SessionLocal()
    try:
        db.query(inbox.EventoProcesado).filter(inbox.EventoProcesado.consumidor.like(f"{nombre}%")).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _marcar(consumidor, evento_id, confirmar=True):
    db = SessionLocal()
    try:
        repetido = inbox.ya_procesado(db, consumidor, evento_id)
        if confirmar:
            db.commit()
        else:
            db.rollback()
        return repetido
    finally:
        db.close()


def test_la_primera_vez_se_procesa_y_la_segunda_no(consumidor):
    evento_id = uuid.uuid4()
    assert _marcar(consumidor, evento_id) is False
    assert _marcar(consumidor, evento_id) is True
    assert _marcar(f"{consumidor}-otro", evento_id) is False  # otro consumidor del mismo evento sí lo procesa


def test_si_el_efecto_se_deshace_la_marca_tambien(consumidor):
    evento_id = uuid.uuid4()
    assert _marcar(consumidor, evento_id, confirmar=False) is False
    assert _marcar(consumidor, evento_id) is False  # el reintento lo procesa


def test_dos_entregas_a_la_vez_solo_una_procesa(consumidor):
    evento_id = uuid.uuid4()
    primera = SessionLocal()
    try:
        assert inbox.ya_procesado(primera, consumidor, evento_id) is False  # sin confirmar todavía
        resultado = {}
        segunda = threading.Thread(target=lambda: resultado.setdefault("repetido", _marcar(consumidor, evento_id)))
        segunda.start()
        segunda.join(timeout=1)
        assert segunda.is_alive(), "la segunda tenía que esperar a que la primera confirme"
        primera.commit()
        segunda.join(timeout=10)
        assert resultado == {"repetido": True}
    finally:
        primera.close()


def test_sin_evento_id_se_procesa_siempre(consumidor):
    assert _marcar(consumidor, None) is False
    assert _marcar(consumidor, None) is False


def test_el_evento_id_del_mensaje():
    evento_id = uuid.uuid4()
    assert inbox.evento_id_de({"evento_id": str(evento_id)}) == evento_id
    assert inbox.evento_id_de({"evento": "pago_confirmado"}) is None
    with pytest.raises(ValueError):
        inbox.evento_id_de({"evento_id": "no-es-un-uuid"})
