# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Pruebas de comun/auth.py: la verificación del JWT (RS256) que hace cada servicio por su cuenta, con su copia. Es lo
que protege a un servicio al que se llama directo, sin pasar por el gateway.

La clave de PRUEBA la genera scripts/correr_pruebas.sh: su privada en JWT_CLAVE_PRIVADA (token_de_prueba firma con
ella) y su pública, la única de JWT_CLAVES_PUBLICAS."""
import base64
import hashlib
import hmac
import inspect
import json
import os
import re
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from comun import auth
from comun.conftest import token_de_prueba

PEM, SPKI = serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _claims_validos(**cambios) -> dict:
    ahora = int(time.time())
    return {"sub": "11111111-1111-1111-1111-111111111111", "rol": "admin", "ver": 1, "iss": auth.EMISOR,
            "aud": auth.AUDIENCIA, "iat": ahora, "exp": ahora + 900, **cambios}


def _a_mano(encabezado: dict, carga: dict, firmar=None) -> str:
    """Un token armado a mano, como lo armaría un atacante (sin la librería, que se negaría a hacerlo)."""
    inicio = f"{_b64(json.dumps(encabezado).encode())}.{_b64(json.dumps(carga).encode())}"
    return f"{inicio}.{_b64(firmar(inicio.encode())) if firmar else ''}"


def _kid_publicado() -> str:
    return next(iter(auth.claves_publicas()))


def _publica():
    return auth.claves_publicas()[_kid_publicado()]


def _rechazado(token) -> bool:
    try:
        auth.validar(token)
    except HTTPException as error:
        return error.status_code == 401
    return False


def test_un_token_bien_firmado_vale_directo_en_el_servicio():
    token = token_de_prueba(rol="productor")
    payload = auth.verificar_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert payload["rol"] == "productor" and payload["iss"] == auth.EMISOR and payload["aud"] == auth.AUDIENCIA
    assert jwt.get_unverified_header(token) == {"alg": "RS256", "typ": "JWT", "kid": _kid_publicado()}


def test_un_token_hs256_firmado_con_la_clave_publica_en_pem_o_en_der_se_rechaza():
    """La confusión de algoritmos: con la clave pública (que es pública) como secreto HMAC. Ni en PEM, ni en DER, ni en
    sus variantes: algorithms es solo ["RS256"]."""
    pem = _publica().public_bytes(PEM, SPKI)
    der = _publica().public_bytes(serialization.Encoding.DER, SPKI)
    for secreto in (pem, pem.strip(), pem.replace(b"\n", b"\r\n"), der, base64.b64encode(der)):
        forjado = _a_mano({"alg": "HS256", "typ": "JWT", "kid": _kid_publicado()}, _claims_validos(),
                          lambda datos, s=secreto: hmac.new(s, datos, hashlib.sha256).digest())
        assert _rechazado(forjado), secreto[:24]
        assert auth.usuario_opcional(HTTPAuthorizationCredentials(scheme="Bearer", credentials=forjado)) is None


def test_alg_none_u_otro_algoritmo_rsa_se_rechaza():
    privada = serialization.load_pem_private_key(open(os.environ["JWT_CLAVE_PRIVADA"], "rb").read(), None)
    sin_firma = _a_mano({"alg": "none", "typ": "JWT", "kid": _kid_publicado()}, _claims_validos())
    rs512 = jwt.encode(_claims_validos(), privada, algorithm="RS512", headers={"kid": _kid_publicado()})
    ps256 = jwt.encode(_claims_validos(), privada, algorithm="PS256", headers={"kid": _kid_publicado()})
    for token in (sin_firma, sin_firma[:-1], rs512, ps256):
        assert _rechazado(token)


def test_un_kid_desconocido_sin_kid_o_firmado_con_otra_clave_se_rechaza():
    privada = serialization.load_pem_private_key(open(os.environ["JWT_CLAVE_PRIVADA"], "rb").read(), None)
    otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    casos = [
        jwt.encode(_claims_validos(), otra, algorithm="RS256", headers={"kid": auth.kid_de(otra.public_key())}),
        jwt.encode(_claims_validos(), otra, algorithm="RS256", headers={"kid": _kid_publicado()}),  # el kid, sin la clave
        jwt.encode(_claims_validos(), privada, algorithm="RS256"),  # sin kid
        jwt.encode(_claims_validos(), privada, algorithm="RS256", headers={"kid": "0123456789abcdef"}),
        _a_mano({"alg": "RS256", "typ": "JWT", "kid": ["lista"]}, _claims_validos()),
    ]
    for token in casos:
        assert _rechazado(token)


def test_un_token_alterado_vencido_o_con_otro_emisor_o_audiencia_se_rechaza():
    bueno = token_de_prueba(rol="comprador")
    cabeza, cuerpo, firma = bueno.split(".")
    carga = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    alterado = f"{cabeza}.{_b64(json.dumps({**carga, 'rol': 'admin'}).encode())}.{firma}"
    casos = [alterado, f"{cabeza}.{cuerpo}.{firma[:-6]}AAAAAA", token_de_prueba(exp=int(time.time()) - 5),
             token_de_prueba(iss="otro-emisor"), token_de_prueba(aud="otra-api"), token_de_prueba(aud=None),
             token_de_prueba(iss=None), token_de_prueba(ver=None), token_de_prueba(sub=None), token_de_prueba(rol=None),
             token_de_prueba(exp=None), token_de_prueba(iat=None), "no.es.un-token", "", "a.b"]
    for numero, token in enumerate(casos):
        assert _rechazado(token), numero
    assert not _rechazado(bueno)


def test_las_claves_se_cargan_como_objetos_rsa_de_2048_bits_o_mas_y_su_kid_es_su_huella():
    claves = auth.claves_publicas()
    assert claves
    for kid, clave in claves.items():
        assert isinstance(clave, RSAPublicKey) and clave.key_size >= auth.BITS_MINIMOS
        assert kid == auth.kid_de(clave)


def test_una_clave_corta_de_otro_tipo_o_rota_no_se_acepta(tmp_path):
    for nombre, contenido in (
        ("corta", rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key().public_bytes(PEM, SPKI)),
        ("eliptica", ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(PEM, SPKI)),
        ("rota", b"-----BEGIN PUBLIC KEY-----\nno es una clave\n-----END PUBLIC KEY-----\n"),
    ):
        carpeta = tmp_path / nombre
        carpeta.mkdir()
        (carpeta / f"{nombre}.pem").write_bytes(contenido)
        with pytest.raises(ValueError):
            auth.cargar_claves_publicas(carpeta)


def test_hs256_no_aparece_en_ninguna_lista_de_algoritmos():
    codigo = re.sub(r'"""(.|\n)*?"""|#.*', "", inspect.getsource(auth))  # sin la documentación, que sí lo nombra
    assert auth.ALGORITMO == "RS256"
    assert re.findall(r"algorithms=\[([^\]]*)\]", codigo) == ["ALGORITMO"]
    assert "HS256" not in codigo


def test_rotar_la_clave_no_corta_las_sesiones_y_la_nueva_vale_sin_reiniciar(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_claves", auth.claves_publicas())  # al terminar, vuelven las de antes
    for kid, clave in auth.claves_publicas().items():
        (tmp_path / f"{kid}.pem").write_bytes(clave.public_bytes(PEM, SPKI))
    monkeypatch.setattr(auth, "CARPETA_CLAVES", str(tmp_path))
    nueva = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    de_la_nueva = jwt.encode(_claims_validos(), nueva, algorithm="RS256", headers={"kid": auth.kid_de(nueva.public_key())})
    monkeypatch.setattr(auth, "_ultima_recarga", time.monotonic())
    assert _rechazado(de_la_nueva)  # todavía no está publicada
    (tmp_path / "nueva.pem").write_bytes(nueva.public_key().public_bytes(PEM, SPKI))
    assert _rechazado(de_la_nueva)  # publicada, pero la carpeta se relee como mucho cada RECARGA_SEGUNDOS
    monkeypatch.setattr(auth, "_ultima_recarga", time.monotonic() - auth.RECARGA_SEGUNDOS)
    assert auth.validar(de_la_nueva)["rol"] == "admin"
    assert auth.validar(token_de_prueba())["rol"] == "comprador"  # los tokens de la vieja siguen valiendo


def test_sin_claves_publicas_el_servicio_no_arranca(correr_python, tmp_path):
    sin_variable = correr_python("import comun.auth", JWT_CLAVES_PUBLICAS=None)
    assert sin_variable.returncode != 0 and "JWT_CLAVES_PUBLICAS" in sin_variable.stderr
    vacia = correr_python("import comun.auth", JWT_CLAVES_PUBLICAS=str(tmp_path))
    assert vacia.returncode != 0 and "No hay claves públicas" in vacia.stderr
