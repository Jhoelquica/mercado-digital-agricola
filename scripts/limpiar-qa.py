#!/usr/bin/env python
"""Borra del clúster LOCAL las cuentas comprador de prueba (qa-*@chakrashop-demo.pe) y lo que depende de ellas.

Se niega a correr si el contexto de kubectl no es docker-desktop. Hay que elegir el modo a propósito: --dry-run solo
cuenta lo que borraría; --ejecutar lo borra, en una transacción por base. Antes de borrar de verdad conviene un respaldo
(scripts/respaldo_completo.sh).

Uso (desde la raíz del repositorio):
  python scripts/limpiar-qa.py --dry-run
  python scripts/limpiar-qa.py --ejecutar
  python scripts/limpiar-qa.py --dry-run --busqueda papa --busquedas-desde "2026-10-31 00:30:00"

Lo que NO toca, a propósito:
  - qa-comprador@chakrashop-demo.pe, la cuenta fija de las pruebas (frontend/.env.qa), salvo con --incluir-cuenta-fija.
  - Cuentas qa-* que no sean comprador: pueden tener perfil de productor o repartidor, y aquí no se cubre. Se avisan.
  - Los bloques de la cadena de certificación: cada uno está enlazado por hash al anterior, así que borrar uno la rompe.
    Se avisa si alguno menciona un pedido de estas cuentas.
  - Las búsquedas registradas no guardan quién las hizo: solo se borran las que se piden con --busqueda, y a partir de
    --busquedas-desde (por defecto, una hora antes de la primera cuenta qa-*).
  - El stock que descontaron sus pedidos no se repone. Si los hay, se avisa cuánto fue por producto.

El borrado va de los servicios hacia Usuarios: si falla a la mitad, volver a correrlo retoma lo que faltó.
"""
import argparse
import re
import subprocess
import sys

CONTEXTO_LOCAL = 'docker-desktop'
CUENTA_FIJA = 'qa-comprador@chakrashop-demo.pe'
PATRON_CORREO = re.compile(r'^qa-[a-z0-9._-]+@chakrashop-demo\.pe$')
PATRON_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
# Mismos estados que transporte/logica_repartidores.py usa para saber si un repartidor sigue ocupado.
ENVIOS_ACTIVOS = "('propuesto','asignado','en_camino','en_destino')"
RECOJOS_ACTIVOS = "('propuesto','asignado','recogido')"


def falla(mensaje):
    sys.exit(f'ERROR: {mensaje}')


def kubectl(*args, entrada=None):
    return subprocess.run(['kubectl', '--context', CONTEXTO_LOCAL, *args], input=entrada, capture_output=True, text=True, encoding='utf-8')


def exigir_contexto_local():
    resultado = subprocess.run(['kubectl', 'config', 'current-context'], capture_output=True, text=True)
    actual = resultado.stdout.strip()
    if resultado.returncode != 0 or actual != CONTEXTO_LOCAL:
        falla(f'el contexto de kubectl es "{actual or "desconocido"}", no {CONTEXTO_LOCAL}. No se toca nada.')


def sql(base, consulta):
    """Corre SQL en la base de un servicio y devuelve las filas (campos separados por |)."""
    r = kubectl('exec', '-i', f'deploy/db-{base}', '--', 'sh', '-c', 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qtAf -', entrada=consulta)
    if r.returncode != 0:
        falla(f'en la base {base}: {r.stderr.strip()}')
    return [fila for fila in r.stdout.splitlines() if fila.strip()]


def lista_uuid(valores):
    for v in valores:
        if not PATRON_UUID.match(v):
            falla(f'identificador inesperado: {v!r}')
    return ','.join(f"'{v}'::uuid" for v in valores)


def texto_sql(valor):
    return "'" + valor.replace("'", "''") + "'"


def contar(base, tabla, condicion):
    return int(sql(base, f'SELECT count(*) FROM {tabla} WHERE {condicion};')[0])


def elegir_cuentas(incluir_fija):
    filas = sql('usuarios', "SELECT id, email, rol, fecha_registro FROM usuarios WHERE email LIKE 'qa-%@chakrashop-demo.pe' ORDER BY fecha_registro;")
    cuentas, otras = [], []
    for fila in filas:
        id_, correo, rol, fecha = fila.split('|')
        if not PATRON_CORREO.match(correo):
            otras.append((correo, 'correo fuera del patrón'))
        elif correo == CUENTA_FIJA and not incluir_fija:
            otras.append((correo, 'cuenta fija de las pruebas'))
        elif rol != 'comprador':
            otras.append((correo, f'rol {rol}'))
        else:
            cuentas.append({'id': id_, 'correo': correo, 'fecha': fecha})
    return cuentas, otras


def planear(cuentas, pedidos, envios, busquedas):
    """Devuelve [(base, tabla, condición)] en el orden en que hay que borrar."""
    usuarios = lista_uuid([c['id'] for c in cuentas])
    plan = []
    if pedidos:
        p = lista_uuid(pedidos)
        if envios:
            e = lista_uuid(envios)
            plan.append(('transporte', 'envio_rechazos', f'envio_id IN ({e})'))
            plan.append(('transporte', 'envios', f'id IN ({e})'))
        plan.append(('pagos', 'liquidaciones', f'pedido_id IN ({p})'))
        plan.append(('pagos', 'entregas_pedido', f'pedido_id IN ({p})'))
        plan.append(('pagos', 'devoluciones_stock', f'pedido_id IN ({p})'))
        plan.append(('pagos', 'pagos', f'pedido_id IN ({p})'))
        plan.append(('certificacion', 'certificados_pedido', f'pedido_id IN ({p})'))
        plan.append(('notificaciones', 'notificaciones', f'pedido_id IN ({p}) OR usuario_id IN ({usuarios})'))
        plan.append(('pedidos', 'eventos_salientes', 'cuerpo LIKE ANY (ARRAY[' + ','.join(f"'%{x}%'" for x in pedidos) + '])'))
        plan.append(('pedidos', 'pedido_items', f'pedido_id IN ({p})'))
        plan.append(('pedidos', 'pedidos', f'id IN ({p})'))
    else:
        plan.append(('notificaciones', 'notificaciones', f'usuario_id IN ({usuarios})'))
    plan.append(('notificaciones', 'suscripciones_push', f'usuario_id IN ({usuarios})'))
    plan.append(('notificaciones', 'preferencias_correo', f'usuario_id IN ({usuarios})'))
    plan.append(('productos', 'resenas', f'usuario_id IN ({usuarios})'))
    plan.append(('productos', 'reservas', f'comprador_id IN ({usuarios})'))
    if busquedas:
        plan.append(('productos', 'busqueda_logs', busquedas))
    plan.append(('usuarios', 'reclamos_ruc', f'reclamante_id IN ({usuarios})'))
    correos = ','.join(texto_sql(c['correo']) for c in cuentas)
    plan.append(('usuarios', 'intentos_login', f'email IN ({correos})'))
    plan.append(('usuarios', 'usuarios', f'id IN ({usuarios})'))
    return plan


def repartidores_por_liberar(pedidos):
    """Un envío ocupa a su repartidor: al borrarlo, vuelve a disponible si no le queda otro envío o recojo en curso."""
    if not pedidos:
        return []
    p = lista_uuid(pedidos)
    return sql('transporte', f"""
        SELECT r.id, r.nombre FROM repartidores r
        WHERE r.estado_disponibilidad = 'ocupado'
          AND r.id IN (SELECT repartidor_id FROM envios WHERE pedido_id IN ({p}) AND repartidor_id IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM envios o WHERE o.repartidor_id = r.id AND o.pedido_id NOT IN ({p}) AND o.estado IN {ENVIOS_ACTIVOS})
          AND NOT EXISTS (SELECT 1 FROM recojos c WHERE c.repartidor_id = r.id AND c.estado IN {RECOJOS_ACTIVOS});""")


def avisos(cuentas, pedidos):
    salida = []
    usuarios = lista_uuid([c['id'] for c in cuentas])
    n = contar('usuarios', 'reclamos_ruc', f'titular_id IN ({usuarios}) AND reclamante_id NOT IN ({usuarios})')
    if n:
        salida.append(f'{n} reclamo(s) de RUC de otra persona apuntan a estas cuentas: impiden borrarlas. Resuélvelos antes.')
    if not pedidos:
        return salida
    p = lista_uuid(pedidos)
    n = contar('pagos', 'liquidaciones', f'pedido_id IN ({p}) AND pago_beneficiario_id IS NOT NULL')
    if n:
        salida.append(f'{n} liquidación(es) de estos pedidos ya se pagaron a un agricultor o repartidor: el pago semanal quedará sin su detalle.')
    condicion = ' OR '.join(f"datos LIKE '%{x}%'" for x in pedidos)
    n = contar('certificacion', 'bloques', condicion)
    if n:
        salida.append(f'{n} bloque(s) de certificación mencionan estos pedidos: NO se borran (romperían la cadena del producto).')
    filas = sql('pedidos', f"""
        SELECT i.producto_id, coalesce(i.producto_nombre, '?'), coalesce(sum(i.cantidad), 0), coalesce(i.unidad, 'base')
        FROM pedido_items i JOIN pedidos d ON d.id = i.pedido_id
        WHERE i.pedido_id IN ({p}) GROUP BY 1, 2, 4;""")
    for fila in filas:
        _, nombre, cantidad, unidad = fila.split('|')
        salida.append(f'Stock: estos pedidos llevan {cantidad} ({unidad}) de "{nombre}". El stock no se repone.')
    return salida


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Limpia las cuentas qa-*@chakrashop-demo.pe del clúster local.')
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument('--dry-run', action='store_true', help='solo muestra qué borraría')
    modo.add_argument('--ejecutar', action='store_true', help='borra de verdad')
    parser.add_argument('--incluir-cuenta-fija', action='store_true', help=f'incluye también {CUENTA_FIJA}')
    parser.add_argument('--busqueda', action='append', default=[], help='término de búsqueda de prueba a borrar (se puede repetir)')
    parser.add_argument('--busquedas-desde', help='fecha desde la que se borran esos términos (por defecto, 1 h antes de la primera cuenta)')
    args = parser.parse_args()

    exigir_contexto_local()
    print(f'Contexto: {CONTEXTO_LOCAL}. Modo: {"BORRADO REAL" if args.ejecutar else "dry-run (no cambia nada)"}\n')

    cuentas, otras = elegir_cuentas(args.incluir_cuenta_fija)
    for correo, motivo in otras:
        print(f'Se deja: {correo} ({motivo})')
    if not cuentas:
        print('No hay cuentas para limpiar.')
        return

    print(f'Cuentas ({len(cuentas)}):')
    for c in cuentas:
        print(f'  {c["correo"]}  (registrada {c["fecha"][:19]})')

    usuarios = lista_uuid([c['id'] for c in cuentas])
    pedidos = [f for f in sql('pedidos', f'SELECT id FROM pedidos WHERE usuario_id IN ({usuarios});')]
    envios = [f for f in sql('transporte', f'SELECT id FROM envios WHERE pedido_id IN ({lista_uuid(pedidos)});')] if pedidos else []

    busquedas = None
    if args.busqueda:
        desde = args.busquedas_desde or sql('usuarios', f"SELECT min(fecha_registro) - interval '1 hour' FROM usuarios WHERE id IN ({usuarios});")[0]
        terminos = ','.join(texto_sql(t.strip().lower()) for t in args.busqueda)
        busquedas = f'lower(termino) IN ({terminos}) AND fecha >= {texto_sql(desde)}::timestamp'

    plan = planear(cuentas, pedidos, envios, busquedas)
    por_liberar = repartidores_por_liberar(pedidos)
    alertas = avisos(cuentas, pedidos)

    print('\nQué borraría en cada base:')
    base_actual = None
    total = 0
    for base, tabla, condicion in plan:
        if base != base_actual:
            print(f'  [{base}]')
            base_actual = base
        n = contar(base, tabla, condicion)
        total += n
        print(f'    {tabla}: {n}')
    if busquedas:
        print('\nBúsquedas que coinciden:')
        for fila in sql('productos', f'SELECT termino, fecha FROM busqueda_logs WHERE {busquedas} ORDER BY fecha;'):
            print(f'    {fila}')
    else:
        print('\nBúsquedas: no guardan quién las hizo. No se borra ninguna sin --busqueda. Las de las últimas 72 h:')
        for fila in sql('productos', "SELECT termino, count(*), min(fecha), max(fecha) FROM busqueda_logs WHERE fecha > now() - interval '72 hours' GROUP BY 1 ORDER BY 2 DESC;"):
            print(f'    {fila}')
    if por_liberar:
        print('\nRepartidores que volverían a "disponible":')
        for fila in por_liberar:
            print(f'    {fila.split("|")[1]}')
    if alertas:
        print('\nAvisos:')
        for a in alertas:
            print(f'    {a}')
    print(f'\nTotal de filas: {total}')

    if not args.ejecutar:
        print('Dry-run: no se cambió nada.')
        return
    if any('impiden borrarlas' in a for a in alertas):
        falla('hay reclamos de RUC que apuntan a estas cuentas. No se borró nada.')

    ids_liberar = [f.split('|')[0] for f in por_liberar]
    orden = []
    for base, _, _ in plan:
        if base not in orden:
            orden.append(base)
    for base in orden:
        cuerpo = ''.join(f'DELETE FROM {tabla} WHERE {condicion};\n' for b, tabla, condicion in plan if b == base)
        if base == 'transporte' and ids_liberar:
            cuerpo += f"UPDATE repartidores SET estado_disponibilidad = 'disponible' WHERE id IN ({lista_uuid(ids_liberar)});\n"
        sql(base, f'BEGIN;\n{cuerpo}COMMIT;\n')
        print(f'  {base}: listo')

    quedan = contar('usuarios', 'usuarios', f'id IN ({usuarios})')
    if quedan:
        falla(f'quedan {quedan} cuentas sin borrar.')
    print('\nBorrado terminado. La caché de Productos se renueva sola (TTL corto).')


if __name__ == '__main__':
    main()
