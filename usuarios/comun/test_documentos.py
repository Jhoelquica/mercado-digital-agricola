# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Pruebas de comun/documentos.py: el tope por cantidad de documentos y el tipo por la firma del contenido."""
import io

import pytest
from fastapi import HTTPException

from comun import documentos


class Archivo:
    def __init__(self, datos: bytes):
        self.file = io.BytesIO(datos)


def test_el_limite_del_cuerpo_crece_con_los_documentos():
    medio_mega = 512 * 1024
    assert documentos.limite_del_cuerpo(2) == 2 * documentos.TAMANO_MAXIMO_BYTES + medio_mega  # reclamo de un RUC
    assert documentos.limite_del_cuerpo(3) == 3 * documentos.TAMANO_MAXIMO_BYTES + medio_mega  # transportista


def test_acepta_fotos_y_pdf_por_su_firma():
    assert documentos.leer_documento_validado(Archivo(b"%PDF-1.7 contenido"))[1] == "application/pdf"
    assert documentos.leer_documento_validado(Archivo(b"\xff\xd8\xff\xe0 foto"))[1] == "image/jpeg"


def test_rechaza_lo_que_no_es_foto_ni_pdf_y_lo_que_pesa_de_mas():
    with pytest.raises(HTTPException) as error:
        documentos.leer_documento_validado(Archivo(b"<html><script>alert(1)</script>"))
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        documentos.leer_documento_validado(Archivo(b"%PDF-" + bytes(documentos.TAMANO_MAXIMO_BYTES)))
    assert error.value.status_code == 413
