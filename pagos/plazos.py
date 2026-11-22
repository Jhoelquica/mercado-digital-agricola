"""Plazos para pagar un pedido. El que vale está guardado en el pago (pagos.vence_en); cada extensión queda anotada en
extensiones_plazo (ver main.extender_plazo). Todos se cuentan desde que se creó el pago (fecha_creacion), nunca desde
el momento de pedir la extensión: así elegir transferencia o abrir Culqi varias veces no lo alarga más de la cuenta.
Ninguna regla acorta un plazo.

- Base: PLAZO_PARA_PAGAR_MINUTOS (15). Con tarjeta o Yape se paga en minutos dentro de la ventana de Culqi.
- Transferencia: creación + PLAZO_TRANSFERENCIA_MINUTOS (120): hay que transferir y subir la constancia; subida, el pago
  queda "en_revision" y ya no vence.
- Al abrir Culqi: si quedan menos de MINUTOS_AL_ABRIR_CULQI (10), hasta ahora + esos minutos, sin pasar de creación +
  TOPE_CON_CULQI_MINUTOS (45): que no venza mientras el comprador escribe su tarjeta.
Un pago "procesando" (un cobro en curso) nunca vence: el vencimiento solo toma los "pendiente".
"""
import os
from datetime import timedelta


def _minutos(nombre: str, por_defecto: int) -> int:
    return int(os.getenv(nombre, str(por_defecto)))


PLAZO_PARA_PAGAR_MINUTOS = _minutos("PLAZO_PARA_PAGAR_MINUTOS", 15)
PLAZO_TRANSFERENCIA_MINUTOS = _minutos("PLAZO_TRANSFERENCIA_MINUTOS", 120)
MINUTOS_AL_ABRIR_CULQI = _minutos("MINUTOS_AL_ABRIR_CULQI", 10)
TOPE_CON_CULQI_MINUTOS = _minutos("TOPE_CON_CULQI_MINUTOS", 45)


def vence_inicial(creacion):
    return creacion + timedelta(minutes=PLAZO_PARA_PAGAR_MINUTOS)


def vence_con_transferencia(creacion, vence):
    """Creación + el plazo de transferencia; si ya tenía más, se queda con el suyo."""
    return max(vence, creacion + timedelta(minutes=PLAZO_TRANSFERENCIA_MINUTOS))


def vence_al_abrir_culqi(creacion, vence, ahora):
    """Con menos de MINUTOS_AL_ABRIR_CULQI por delante: ahora + esos minutos, hasta el tope (creación +
    TOPE_CON_CULQI_MINUTOS). Nunca acorta, y no "extiende" a un momento que ya pasó (pasado el tope, vence)."""
    if vence - ahora >= timedelta(minutes=MINUTOS_AL_ABRIR_CULQI):
        return vence
    candidato = min(ahora + timedelta(minutes=MINUTOS_AL_ABRIR_CULQI), creacion + timedelta(minutes=TOPE_CON_CULQI_MINUTOS))
    return candidato if candidato > vence and candidato > ahora else vence
