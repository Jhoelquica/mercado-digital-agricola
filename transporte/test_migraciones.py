"""Tests de migraciones.agregar_columnas_faltantes.

No necesitan Postgres: usan SQLite en memoria y un Base de prueba propio (no el del servicio, cuyos
tipos UUID de postgresql no existen en SQLite). Lo que se prueba es la lógica de comparar modelo vs
base y agregar columnas; el ALTER real contra Postgres se verifica aparte, contra una base de prueba.
"""
import logging

from sqlalchemy import Column, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base

import migraciones
from migraciones import agregar_columnas_faltantes


def _base_vieja():
    """Una base creada ANTES de las columnas nuevas, con una fila existente."""
    engine = create_engine("sqlite://")
    with engine.begin() as conexion:
        conexion.execute(text("CREATE TABLE envios (id INTEGER PRIMARY KEY, estado VARCHAR)"))
        conexion.execute(text("INSERT INTO envios (id, estado) VALUES (1, 'entregado')"))
    return engine


def _modelo(*, con_not_null=False):
    base = declarative_base()

    class Envio(base):
        __tablename__ = "envios"
        id = Column(Integer, primary_key=True)
        estado = Column(String)
        pin_entrega = Column(String, nullable=True)
        foto_entrega_url = Column(String, nullable=True)
        if con_not_null:
            obligatoria = Column(String, nullable=False)

    return base


def _columnas(engine, tabla="envios"):
    return {c["name"] for c in inspect(engine).get_columns(tabla)}


def test_agrega_las_columnas_nullable_faltantes_y_conserva_los_datos():
    engine = _base_vieja()

    agregadas = agregar_columnas_faltantes(engine, _modelo())

    assert set(agregadas) == {"envios.pin_entrega", "envios.foto_entrega_url"}
    assert {"pin_entrega", "foto_entrega_url"} <= _columnas(engine)
    with engine.connect() as conexion:
        fila = conexion.execute(text("SELECT id, estado, pin_entrega FROM envios")).one()
    assert tuple(fila) == (1, "entregado", None)


def test_es_idempotente():
    engine = _base_vieja()
    agregar_columnas_faltantes(engine, _modelo())

    assert agregar_columnas_faltantes(engine, _modelo()) == []
    assert {"pin_entrega", "foto_entrega_url"} <= _columnas(engine)


def test_no_agrega_una_columna_not_null_sin_default_y_avisa(caplog):
    engine = _base_vieja()

    with caplog.at_level(logging.ERROR, logger="transporte.migraciones"):
        agregadas = agregar_columnas_faltantes(engine, _modelo(con_not_null=True))

    assert "envios.obligatoria" not in agregadas
    assert "obligatoria" not in _columnas(engine)
    assert "envios.obligatoria" in caplog.text
    # Las nullable sí se agregan: una columna problemática no frena a las demás.
    assert {"pin_entrega", "foto_entrega_url"} <= _columnas(engine)


def test_ignora_las_tablas_que_no_existen_en_la_base():
    engine = create_engine("sqlite://")  # base vacía: create_all() creará la tabla completa

    assert agregar_columnas_faltantes(engine, _modelo()) == []
    assert not inspect(engine).has_table("envios")


def test_un_fallo_al_inspeccionar_no_tumba_el_arranque(monkeypatch, caplog):
    def _falla(_engine):
        raise RuntimeError("base caída")

    monkeypatch.setattr(migraciones, "inspect", _falla)

    with caplog.at_level(logging.ERROR, logger="transporte.migraciones"):
        assert agregar_columnas_faltantes(create_engine("sqlite://"), _modelo()) == []
    assert "No se pudo inspeccionar el esquema" in caplog.text
