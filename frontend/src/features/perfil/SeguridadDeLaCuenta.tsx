import { useRef, useState, type FormEvent, type KeyboardEvent, type RefObject } from 'react';
import { ApiError } from '../../api/errors';
import { usuarios } from '../../api/usuarios';
import { adoptarTokenNuevo } from '../../auth/sesion';
import { toast } from '../../components/Toast';
import {
  CONTRASENA_MINIMA,
  campoDelErrorDelServidor,
  erroresDeCambioDeContrasena,
  type CambioDeContrasena,
  type CampoDeContrasena,
  type ErroresDeContrasena,
} from '../../lib/contrasena';

// "Seguridad de la cuenta" en Mi perfil: cambiar la contraseña y cerrar la sesión en todos los dispositivos. Las dos suben
// la versión de credencial: todas las demás sesiones de la cuenta dejan de valer al instante (el gateway las rechaza) y
// esta pestaña sigue con el token nuevo que devuelve Usuarios (adoptarTokenNuevo).
// 21st.dev "Auth Change Password" (cada campo con mostrar/ocultar, el error junto al campo, la ayuda bajo la nueva y el
// botón con su estado de carga), con las clases del perfil. ui-ux-pro-max: etiquetas visibles, autocomplete
// current-password / new-password (los gestores de contraseñas y el pegado funcionan), aria-invalid + aria-describedby,
// validar al enviar y desde ahí al escribir, y el foco al primer campo con error. "Cerrar sesión en todos los
// dispositivos" se confirma ahí mismo, con el patrón de "Cancelar pedido" (el foco en la opción segura, Escape vuelve).

const ORDEN: CampoDeContrasena[] = ['actual', 'nueva', 'confirmacion'];
const ETIQUETAS: Record<CampoDeContrasena, string> = {
  actual: 'Contraseña actual', nueva: 'Contraseña nueva', confirmacion: 'Repite la contraseña nueva',
};
const AUTOCOMPLETAR: Record<CampoDeContrasena, string> = {
  actual: 'current-password', nueva: 'new-password', confirmacion: 'new-password',
};
const VACIO: CambioDeContrasena = { actual: '', nueva: '', confirmacion: '' };

interface PropsCampo {
  campo: CampoDeContrasena;
  valor: string;
  error?: string;
  ayuda?: string;
  entrada: RefObject<HTMLInputElement | null>;
  alCambiar: (valor: string) => void;
}

function CampoContrasena({ campo, valor, error, ayuda, entrada, alCambiar }: PropsCampo) {
  const [ver, setVer] = useState(false);
  const id = `perfil-contrasena-${campo}`;
  const describe = [error ? `${id}-error` : null, ayuda ? `${id}-ayuda` : null].filter(Boolean).join(' ') || undefined;
  return (
    <div className="perfil-campo">
      <label htmlFor={id}>{ETIQUETAS[campo]}</label>
      <div className="perfil-clave">
        <input ref={entrada} id={id} name={campo} type={ver ? 'text' : 'password'} autoComplete={AUTOCOMPLETAR[campo]}
          maxLength={200} value={valor} aria-invalid={error ? true : undefined} aria-describedby={describe}
          onChange={(e) => alCambiar(e.target.value)} />
        <button type="button" className="auth-ojo" aria-pressed={ver} onClick={() => setVer((v) => !v)}
          aria-label={`${ver ? 'Ocultar' : 'Mostrar'} ${ETIQUETAS[campo].toLowerCase()}`}>
          <i className={`ti ti-eye${ver ? '-off' : ''}`} aria-hidden="true" />
        </button>
      </div>
      {ayuda && <small id={`${id}-ayuda`} className="perfil-campo-ayuda">{ayuda}</small>}
      {error && (
        <small id={`${id}-error`} className="perfil-campo-error" aria-live="polite">
          <i className="ti ti-alert-circle" aria-hidden="true" /><span>{error}</span>
        </small>
      )}
    </div>
  );
}

function CambiarContrasena({ email }: { email: string }) {
  const [datos, setDatos] = useState<CambioDeContrasena>(VACIO);
  const [errores, setErrores] = useState<ErroresDeContrasena>({});
  const [intentado, setIntentado] = useState(false);
  const [errorGeneral, setErrorGeneral] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const entradas: Record<CampoDeContrasena, RefObject<HTMLInputElement | null>> = {
    actual: useRef<HTMLInputElement>(null), nueva: useRef<HTMLInputElement>(null), confirmacion: useRef<HTMLInputElement>(null),
  };

  function cambiar(campo: CampoDeContrasena, valor: string) {
    const nuevos = { ...datos, [campo]: valor };
    setDatos(nuevos);
    if (intentado) setErrores(erroresDeCambioDeContrasena(nuevos, email));
  }

  async function enviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (enviando) return;
    setIntentado(true);
    setErrorGeneral(null);
    const locales = erroresDeCambioDeContrasena(datos, email);
    setErrores(locales);
    const primero = ORDEN.find((campo) => locales[campo]);
    if (primero) {
      entradas[primero].current?.focus();
      return;
    }
    setEnviando(true);
    try {
      const { access_token: token } = await usuarios.cambiarContrasena({ actual: datos.actual, nueva: datos.nueva });
      await adoptarTokenNuevo(token);
      setDatos(VACIO);
      setErrores({});
      setIntentado(false);
      toast('Cambiaste tu contraseña. Cerramos tu sesión en los demás dispositivos; aquí sigues conectado.');
    } catch (err) {
      const campo = campoDelErrorDelServidor(err instanceof ApiError ? err.status : 0);
      const mensaje = (err as Error).message || 'No se pudo cambiar la contraseña. Intenta de nuevo.';
      if (campo) {
        setErrores({ [campo]: mensaje });
        entradas[campo].current?.focus();
      } else {
        setErrorGeneral(mensaje);
      }
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="perfil-seguridad-bloque" id="form-cambiar-contrasena" noValidate aria-labelledby="perfil-cambiar-contrasena-titulo"
      onSubmit={(e) => { void enviar(e); }}>
      <h4 id="perfil-cambiar-contrasena-titulo"><i className="ti ti-key" aria-hidden="true" /> Cambiar contraseña</h4>
      <p className="perfil-seguridad-texto">Al cambiarla, tu sesión se cierra en todos los demás dispositivos. En este sigues conectado.</p>
      {errorGeneral && <p className="perfil-form-error" role="alert">{errorGeneral}</p>}
      {ORDEN.map((campo) => (
        <CampoContrasena key={campo} campo={campo} valor={datos[campo]} error={errores[campo]} entrada={entradas[campo]}
          ayuda={campo === 'nueva' ? `Al menos ${CONTRASENA_MINIMA} caracteres. Que no sea tu correo ni una contraseña común.` : undefined}
          alCambiar={(valor) => cambiar(campo, valor)} />
      ))}
      <button type="submit" className="btn btn-primary perfil-seguridad-enviar" disabled={enviando} aria-busy={enviando}>
        {enviando
          ? <><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Cambiando…</>
          : <><i className="ti ti-lock-check" aria-hidden="true" /> Cambiar contraseña</>}
      </button>
    </form>
  );
}

function SinContrasenaPropia() {
  return (
    <div className="perfil-seguridad-bloque">
      <h4><i className="ti ti-key" aria-hidden="true" /> Contraseña</h4>
      <p className="perfil-seguridad-texto perfil-seguridad-google">
        <i className="ti ti-brand-google" aria-hidden="true" />
        <span>Entras con tu cuenta de Google, así que aquí no hay una contraseña propia que cambiar: la de tu cuenta se cambia en Google.</span>
      </p>
    </div>
  );
}

function CerrarSesiones() {
  const [abierto, setAbierto] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const botonAbrir = useRef<HTMLButtonElement>(null);
  const botonVolver = useRef<HTMLButtonElement>(null);

  function cerrar() {
    setAbierto(false);
    setError(null);
    setTimeout(() => botonAbrir.current?.focus());
  }

  async function confirmar() {
    setEnviando(true);
    setError(null);
    try {
      const { access_token: token } = await usuarios.cerrarSesionesEnTodosLosDispositivos();
      await adoptarTokenNuevo(token);
      toast('Listo: cerramos tu sesión en todos los demás dispositivos. Aquí sigues conectado.');
      cerrar();
    } catch (err) {
      setError((err as Error).message || 'No se pudieron cerrar las sesiones. Intenta de nuevo.');
    } finally {
      setEnviando(false);
    }
  }

  function alTeclear(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape' && !enviando) {
      e.stopPropagation();
      cerrar();
    }
  }

  return (
    <div className="perfil-seguridad-bloque perfil-sesiones">
      <h4><i className="ti ti-devices" aria-hidden="true" /> Tus sesiones</h4>
      <p className="perfil-seguridad-texto">
        ¿Entraste en un celular o una computadora que ya no usas, o crees que alguien más entró con tu cuenta? Cierra la
        sesión en todos ellos de una vez: tendrán que volver a iniciar sesión.
      </p>
      {!abierto ? (
        <button ref={botonAbrir} type="button" className="btn btn-outline perfil-sesiones-abrir" id="btn-cerrar-sesiones"
          onClick={() => { setAbierto(true); setTimeout(() => botonVolver.current?.focus()); }}>
          <i className="ti ti-logout-2" aria-hidden="true" /> Cerrar sesión en todos los dispositivos
        </button>
      ) : (
        <div className="perfil-sesiones-confirmar" role="group" aria-labelledby="perfil-sesiones-pregunta" onKeyDown={alTeclear}>
          <p id="perfil-sesiones-pregunta">
            <i className="ti ti-alert-triangle" aria-hidden="true" />
            <span>¿Cerrar tu sesión en todos los demás dispositivos? En este sigues conectado.</span>
          </p>
          {error && <p className="perfil-form-error" role="alert">{error}</p>}
          <div className="perfil-sesiones-acciones">
            <button ref={botonVolver} type="button" className="btn btn-ghost" disabled={enviando} onClick={cerrar}>No, volver</button>
            <button type="button" className="btn btn-danger" id="btn-confirmar-cerrar-sesiones" disabled={enviando} aria-busy={enviando}
              onClick={() => { void confirmar(); }}>
              {enviando ? <><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Cerrando…</> : 'Sí, cerrar las sesiones'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function SeguridadDeLaCuenta({ email, tieneContrasena }: { email: string; tieneContrasena: boolean }) {
  return (
    <section className="card-panel perfil-tarjeta perfil-seguridad" id="perfil-seguridad" aria-labelledby="perfil-seguridad-titulo">
      <h3 id="perfil-seguridad-titulo"><i className="ti ti-shield-lock" aria-hidden="true" /> Seguridad de la cuenta</h3>
      <div className="perfil-seguridad-grid">
        {tieneContrasena ? <CambiarContrasena email={email} /> : <SinContrasenaPropia />}
        <CerrarSesiones />
      </div>
    </section>
  );
}
