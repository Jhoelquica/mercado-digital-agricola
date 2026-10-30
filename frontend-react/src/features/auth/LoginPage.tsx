import { useEffect, useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation } from 'react-router';
import { MENSAJE_SESION_EXPIRADA } from '../../api/errors';
import { useAuth } from '../../auth/AuthContext';
import { rutaInicialPara } from '../../auth/rutasPorRol';
import { haySesionRecienExpirada, leerSesion, olvidarSesionRecienExpirada } from '../../auth/sesion';
import { Logo } from '../../components/Logo';
import { toast } from '../../components/Toast';

export interface EstadoLogin {
  desde?: string;
}

// Si el servidor no dio un texto útil (por ejemplo, un JSON crudo) se muestra el mensaje de respaldo.
function mensajeDeError(err: unknown, respaldo: string): string {
  const mensaje = err instanceof Error ? err.message.trim() : '';
  return mensaje && !/^[[{]/.test(mensaje) ? mensaje : respaldo;
}

export default function LoginPage() {
  const { autenticado, sesion, iniciarSesion } = useAuth();
  const estado = useLocation().state as EstadoLogin | null;
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

  // Mientras se piden los datos de la cuenta ya hay token: se espera a terminar para no salir de aquí antes de tiempo.
  if (autenticado && !enviando) return <Navigate to={estado?.desde || rutaInicialPara(sesion.rol)} replace />;

  return (
    <main className="login-pagina">
      <aside className="auth-lado">
        <Link className="auth-lado-marca" to="/" aria-label="ChakraShop, ir al inicio">
          <span className="auth-lado-logo"><Logo /></span>
          <span className="auth-lado-nombre">Chakra<strong>Shop</strong></span>
        </Link>
        <div className="auth-lado-cuerpo">
          <div className="auth-lado-texto">
            <h2>Qué bueno verte de nuevo</h2>
            <p>Sigue tus pedidos, tu pago y tu entrega en un solo lugar.</p>
          </div>
          <ul className="auth-lado-lista">
            <li><i className="ti ti-shield-check" aria-hidden="true" /><span>Origen verificado con blockchain</span></li>
            <li><i className="ti ti-credit-card" aria-hidden="true" /><span>Pago seguro con tarjeta o Yape</span></li>
            <li><i className="ti ti-truck-delivery" aria-hidden="true" /><span>Seguimiento en vivo de tu envío</span></li>
          </ul>
        </div>
      </aside>

      <div className="auth-panel">
        <div className="login-contenido">
          <Link className="login-volver" to="/"><i className="ti ti-arrow-left" aria-hidden="true" /> Volver a la tienda</Link>
          <form className="auth-form" onSubmit={alEnviar}>
            <div className="auth-encabezado">
              <h1 className="auth-titulo">Bienvenido de nuevo</h1>
              <p>Ingresa a tu cuenta para continuar.</p>
            </div>
            {error && (
              <div className="auth-alerta" role="alert">
                <i className="ti ti-alert-circle" aria-hidden="true" /> <span>{error}</span>
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
                  <i className={`ti ti-${verPassword ? 'eye-off' : 'eye'}`} aria-hidden="true" />
                </button>
              </div>
            </div>
            <button type="submit" className={`btn btn-primary btn-block auth-enviar ${enviando ? 'cargando' : ''}`} disabled={enviando} aria-busy={enviando}>
              <span className="auth-enviar-texto">{enviando ? 'Ingresando…' : 'Ingresar'}</span>
            </button>
            <p className="auth-cambio">¿Aún no tienes cuenta? <Link to="/registro">Regístrate gratis</Link></p>
          </form>
        </div>
      </div>
    </main>
  );
}
