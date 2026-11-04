import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router';
import { ApiError, MENSAJE_SESION_EXPIRADA } from '../../api/errors';
import { usuarios } from '../../api/usuarios';
import { useAuth } from '../../auth/AuthContext';
import { rutaInicialPara, rutaInternaSegura } from '../../auth/rutasPorRol';
import { haySesionRecienExpirada, iniciarSesionConToken, leerSesion, olvidarSesionRecienExpirada } from '../../auth/sesion';
import { manejarError, toast } from '../../components/Toast';
import { AuthLayout, mensajeDeError } from './AuthLayout';
import { BotonGoogle } from './BotonGoogle';

export default function LoginPage() {
  const { autenticado, sesion, iniciarSesion } = useAuth();
  const [params] = useSearchParams();
  const next = params.get('next');
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [verPassword, setVerPassword] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(() => (haySesionRecienExpirada() ? MENSAJE_SESION_EXPIRADA : null));

  useEffect(() => {
    document.title = 'Iniciar sesión — ChakraShop';
    olvidarSesionRecienExpirada(); // ya se mostró: volver a esta página más tarde no debe repetirlo
  }, []);

  async function alEnviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setEnviando(true);
    try {
      await iniciarSesion(email.trim(), password);
      toast(`¡Bienvenido de nuevo, ${leerSesion().nombre}! 🌱`);
    } catch (err) {
      setError(mensajeDeError(err, 'No se pudo iniciar sesión. Inténtalo de nuevo.'));
    } finally {
      setEnviando(false);
    }
  }

  async function entrarConGoogle(credencial: string) {
    setError(null);
    setEnviando(true);
    try {
      const respuesta = await usuarios.oauthGoogle({ id_token: credencial });
      await iniciarSesionConToken(respuesta.access_token);
      toast(`¡Bienvenido, ${leerSesion().nombre}! 🌱`);
    } catch (err) {
      // Sin cuenta previa (ni por Google ni por correo): el login no pide un rol, así que el servidor no la crea a ciegas.
      if (err instanceof ApiError && err.status === 400) {
        toast('No encontramos una cuenta con ese Google. Usa "Registrarse" para crear una.', 'error');
        void navigate(`/registro${next ? `?next=${encodeURIComponent(next)}` : ''}`);
        return;
      }
      manejarError(err, 'iniciar sesión con Google');
    } finally {
      setEnviando(false);
    }
  }

  // Mientras se piden los datos de la cuenta ya hay token: se espera a terminar para no salir de aquí antes de tiempo.
  if (autenticado && !enviando) return <Navigate to={rutaInternaSegura(next) || rutaInicialPara(sesion.rol)} replace />;

  return (
    <AuthLayout tituloLado="Qué bueno verte de nuevo" textoLado="Sigue tus pedidos, tu pago y tu entrega en un solo lugar.">
      <form className="auth-form" onSubmit={alEnviar}>
        <div className="auth-encabezado">
          <h1 className="auth-titulo">Bienvenido de nuevo</h1>
          <p>Ingresa a tu cuenta para continuar.</p>
        </div>
        {error && (
          <div className="auth-alerta" role="alert">
            <i className="ti ti-alert-circle" aria-hidden="true" /><span>{error}</span>
          </div>
        )}
        <div className="auth-grupo">
          <label htmlFor="login-email">Correo electrónico</label>
          <div className="auth-campo">
            <i className="ti ti-mail" aria-hidden="true" />
            <input type="email" id="login-email" required autoFocus placeholder="tucorreo@ejemplo.com" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        </div>
        <div className="auth-grupo">
          <label htmlFor="login-password">Contraseña</label>
          <div className="auth-campo">
            <i className="ti ti-lock" aria-hidden="true" />
            <input type={verPassword ? 'text' : 'password'} id="login-password" required placeholder="••••••••" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="auth-ojo" aria-label={verPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'} aria-pressed={verPassword} onClick={() => setVerPassword((v) => !v)}>
              <i className={`ti ti-eye${verPassword ? '-off' : ''}`} />
            </button>
          </div>
        </div>
        <button type="submit" className={`btn btn-primary btn-block auth-enviar ${enviando ? 'cargando' : ''}`} disabled={enviando} aria-busy={enviando}>
          <span className="auth-enviar-texto">{enviando ? 'Ingresando…' : 'Ingresar'}</span>
        </button>
        <div className="auth-divider"><span>o continúa con</span></div>
        <BotonGoogle id="google-btn-login" alRecibirCredencial={(credencial) => { void entrarConGoogle(credencial); }} />
        <p className="auth-cambio">¿Aún no tienes cuenta? <Link className="auth-cambio-btn" to={`/registro${next ? `?next=${encodeURIComponent(next)}` : ''}`}>Regístrate gratis</Link></p>
      </form>
    </AuthLayout>
  );
}
