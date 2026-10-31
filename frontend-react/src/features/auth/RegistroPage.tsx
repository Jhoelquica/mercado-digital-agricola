import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router';
import { ApiError } from '../../api/errors';
import { usuarios } from '../../api/usuarios';
import { useAuth } from '../../auth/AuthContext';
import { rutaInicialPara, rutaInternaSegura, rutaTrasRegistro } from '../../auth/rutasPorRol';
import { iniciarSesionConCorreo, leerSesion } from '../../auth/sesion';
import { toast } from '../../components/Toast';
import { rucValido } from '../../lib/reglas';
import { AuthLayout, mensajeDeError } from './AuthLayout';

const ROLES_ABIERTOS = ['comprador', 'productor', 'repartidor'] as const;
type RolAbierto = (typeof ROLES_ABIERTOS)[number];
type TipoComprador = 'persona' | 'negocio';

// Los datos de persona/negocio que se mandan al registrarse: { datos } o { error }. Un negocio tiene que dar su RUC:
// Usuarios lo verifica en SUNAT (activo y habido) antes de crear la cuenta.
function datosDeComprador(tipo: TipoComprador, nombreComercial: string, ruc: string) {
  if (tipo !== 'negocio') return { datos: { tipo_comprador: 'persona' as const } };
  const rucLimpio = ruc.replace(/\s+/g, '');
  if (!rucLimpio) return { error: 'Para comprar como negocio pon el RUC de tu negocio: lo verificamos en SUNAT.' };
  if (!rucValido(rucLimpio)) return { error: 'El RUC no es válido: son 11 dígitos y empieza con 10 o 20. Revisa que esté bien escrito.' };
  return { datos: { tipo_comprador: 'negocio' as const, nombre_comercial: nombreComercial.trim() || null, ruc: rucLimpio } };
}

// El RUC que el comprador quiere ya está en otra cuenta (Usuarios responde 409 con el qué hacer): se ofrece reclamarlo.
function rucOcupadoDe(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 409 || !/ya está registrado en otra cuenta/.test(err.message)) return null;
  return (err.message.match(/RUC (\d{11})/) || [])[1] || '';
}

const OPCIONES_ROL: { valor: RolAbierto; icono: string; titulo: string; detalle: string }[] = [
  { valor: 'comprador', icono: 'shopping-cart', titulo: 'Comprador', detalle: 'Restaurante, mercado o persona' },
  { valor: 'productor', icono: 'tractor', titulo: 'Productor', detalle: 'Agricultor que vende su cosecha' },
  { valor: 'repartidor', icono: 'truck', titulo: 'Repartidor', detalle: 'Entrega pedidos cerca de ti' },
];

export default function RegistroPage() {
  const { autenticado, sesion } = useAuth();
  const [params] = useSearchParams();
  const next = params.get('next');
  const rolInicial = ROLES_ABIERTOS.find((r) => r === params.get('rol')) ?? 'comprador';
  const [nombre, setNombre] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [verPassword, setVerPassword] = useState(false);
  const [rol, setRol] = useState<RolAbierto>(rolInicial);
  const [tipo, setTipo] = useState<TipoComprador>('persona');
  const [nombreComercial, setNombreComercial] = useState('');
  const [ruc, setRuc] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [textoEnviando, setTextoEnviando] = useState('Creando cuenta…');
  const [error, setError] = useState<string | null>(null);
  const [rucOcupado, setRucOcupado] = useState<string | null>(null);
  const reclamoPorAbrir = useRef<string | null>(null);
  const campoNegocio = useRef<HTMLInputElement>(null);

  useEffect(() => {
    document.title = 'Crear cuenta — ChakraShop';
  }, []);

  useEffect(() => {
    if (rol === 'comprador' && tipo === 'negocio') campoNegocio.current?.focus();
  }, [rol, tipo]);

  // reclamarRuc: "Registrarme para mi casa y reclamar el RUC": la cuenta nace como persona y se abre el reclamo al entrar.
  async function crearCuenta(reclamarRuc: string | null) {
    const tipoEfectivo: TipoComprador = reclamarRuc !== null ? 'persona' : tipo;
    let comprador = {};
    if (rol === 'comprador') {
      const { datos, error: errorDatos } = datosDeComprador(tipoEfectivo, nombreComercial, ruc);
      if (errorDatos) {
        setError(errorDatos);
        document.getElementById('registro-ruc')?.focus();
        return;
      }
      comprador = datos ?? {};
    }
    setError(null);
    setRucOcupado(null);
    setTextoEnviando(rol === 'comprador' && tipoEfectivo === 'negocio' ? 'Verificando el RUC en SUNAT…' : 'Creando cuenta…');
    setEnviando(true);
    try {
      const correo = email.trim();
      const creado = await usuarios.registrar({ nombre: nombre.trim(), email: correo, password, rol, ...comprador });
      reclamoPorAbrir.current = reclamarRuc;
      await iniciarSesionConCorreo(correo, password);
      if (reclamarRuc !== null) toast('¡Cuenta creada para tu casa! Ahora reclama tu RUC con tu Ficha RUC y tu DNI.');
      else if (creado.razon_social) toast(`¡Cuenta creada! Negocio verificado en SUNAT: ${creado.razon_social}`);
      else toast(`¡Cuenta creada! Bienvenido a Chakra Shop, ${leerSesion().nombre || nombre.trim()} 🎉`);
    } catch (err) {
      reclamoPorAbrir.current = null;
      setError(mensajeDeError(err, 'No se pudo crear la cuenta. Revisa los datos e inténtalo de nuevo.'));
      setRucOcupado(rucOcupadoDe(err));
    } finally {
      setEnviando(false);
    }
  }

  function alEnviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void crearCuenta(null);
  }

  function limpiarErrores() {
    setError(null);
    setRucOcupado(null);
  }

  if (autenticado && !enviando) {
    if (reclamoPorAbrir.current !== null) {
      return <Navigate to={rutaTrasRegistro(sesion.rol, { reclamaRuc: true })} replace state={{ reclamoRucPorAbrir: reclamoPorAbrir.current }} />;
    }
    return <Navigate to={rutaInternaSegura(next) || rutaInicialPara(sesion.rol)} replace />;
  }

  return (
    <AuthLayout tituloLado="Del productor a tu mesa, sin intermediarios" textoLado="Crea tu cuenta gratis y forma parte de la cadena que lleva el campo de Ayacucho hasta tu mesa.">
      <form className="auth-form" onSubmit={alEnviar} onInput={limpiarErrores}>
        <div className="auth-encabezado">
          <h1 className="auth-titulo">Crea tu cuenta</h1>
          <p>Es gratis y toma menos de un minuto.</p>
        </div>
        {error && (
          <div className="auth-alerta" role="alert">
            <i className="ti ti-alert-circle" aria-hidden="true" /><span>{error}</span>
            {rucOcupado !== null && (
              <button type="button" className="btn btn-primary btn-sm auth-alerta-accion" onClick={() => void crearCuenta(rucOcupado)}>
                <i className="ti ti-file-certificate" aria-hidden="true" /> Registrarme para mi casa y reclamar el RUC
              </button>
            )}
          </div>
        )}
        <div className="auth-grupo">
          <label htmlFor="registro-nombre">Nombre completo</label>
          <div className="auth-campo">
            <i className="ti ti-user" aria-hidden="true" />
            <input type="text" id="registro-nombre" required autoFocus placeholder="Ej. María Quispe" autoComplete="name" value={nombre} onChange={(e) => setNombre(e.target.value)} />
          </div>
        </div>
        <div className="auth-grupo">
          <label htmlFor="registro-email">Correo electrónico</label>
          <div className="auth-campo">
            <i className="ti ti-mail" aria-hidden="true" />
            <input type="email" id="registro-email" required placeholder="tucorreo@ejemplo.com" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        </div>
        <div className="auth-grupo">
          <label htmlFor="registro-password">Contraseña</label>
          <div className="auth-campo">
            <i className="ti ti-lock" aria-hidden="true" />
            <input type={verPassword ? 'text' : 'password'} id="registro-password" required minLength={8} maxLength={72} placeholder="Mínimo 8 caracteres" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="auth-ojo" aria-label={verPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'} aria-pressed={verPassword} onClick={() => setVerPassword((v) => !v)}>
              <i className={`ti ti-eye${verPassword ? '-off' : ''}`} />
            </button>
          </div>
        </div>

        <fieldset className="auth-roles">
          <legend>Quiero registrarme como</legend>
          <div className="auth-roles-grid">
            {OPCIONES_ROL.map((o) => (
              <label className="auth-rol" key={o.valor}>
                <input type="radio" name="registro-rol-opcion" value={o.valor} checked={rol === o.valor} onChange={() => setRol(o.valor)} />
                <span className="auth-rol-caja"><i className={`ti ti-${o.icono}`} aria-hidden="true" /><strong>{o.titulo}</strong><small>{o.detalle}</small></span>
              </label>
            ))}
          </div>
        </fieldset>

        {rol === 'comprador' && (
          <fieldset className="auth-roles auth-tipo-comprador">
            <legend>¿Para qué vas a comprar?</legend>
            <div className="auth-roles-grid">
              <label className="auth-rol">
                <input type="radio" name="registro-tipo-comprador" value="persona" checked={tipo === 'persona'} onChange={() => setTipo('persona')} />
                <span className="auth-rol-caja"><i className="ti ti-home" aria-hidden="true" /><strong>Para mi casa</strong><small>Te mostramos primero el precio por kilo</small></span>
              </label>
              <label className="auth-rol">
                <input type="radio" name="registro-tipo-comprador" value="negocio" checked={tipo === 'negocio'} onChange={() => setTipo('negocio')} />
                <span className="auth-rol-caja"><i className="ti ti-building-store" aria-hidden="true" /><strong>Para mi negocio</strong><small>Restaurante, bodega o puesto: te mostramos primero el precio por saco</small></span>
              </label>
            </div>
            <p className="auth-tipo-nota"><i className="ti ti-info-circle" aria-hidden="true" /> Con cualquiera de los dos puedes comprar por kilos o por sacos: solo cambia qué precio ves primero.</p>
            {tipo === 'negocio' && (
              <div className="auth-datos-negocio">
                <div className="auth-grupo">
                  <label htmlFor="registro-nombre-comercial">Nombre del negocio <span className="auth-opcional">(opcional)</span></label>
                  <div className="auth-campo">
                    <i className="ti ti-building-store" aria-hidden="true" />
                    <input ref={campoNegocio} type="text" id="registro-nombre-comercial" maxLength={120} placeholder="Ej. Restaurante El Ayacuchano" autoComplete="organization" value={nombreComercial} onChange={(e) => setNombreComercial(e.target.value)} />
                  </div>
                </div>
                <div className="auth-grupo">
                  <label htmlFor="registro-ruc">RUC del negocio</label>
                  <div className="auth-campo">
                    <i className="ti ti-id" aria-hidden="true" />
                    <input type="text" id="registro-ruc" inputMode="numeric" maxLength={11} placeholder="11 dígitos, empieza con 10 o 20" autoComplete="off" aria-describedby="registro-ruc-ayuda" value={ruc} onChange={(e) => setRuc(e.target.value)} />
                  </div>
                  <small className="auth-ayuda" id="registro-ruc-ayuda"><i className="ti ti-shield-check" aria-hidden="true" /> Lo verificamos en SUNAT: tiene que estar activo y habido.</small>
                </div>
              </div>
            )}
          </fieldset>
        )}

        <button type="submit" className={`btn btn-primary btn-block auth-enviar ${enviando ? 'cargando' : ''}`} disabled={enviando} aria-busy={enviando}>
          <span className="auth-enviar-texto">{enviando ? textoEnviando : 'Crear cuenta'}</span>
        </button>
        <p className="auth-cambio">¿Ya tienes cuenta? <Link className="auth-cambio-btn" to={`/login${next ? `?next=${encodeURIComponent(next)}` : ''}`}>Inicia sesión</Link></p>
      </form>
    </AuthLayout>
  );
}
