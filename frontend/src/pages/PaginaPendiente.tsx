import { useLocation } from 'react-router';
import { useTituloDeRuta } from '../layouts/AppLayout';

// Marca el lugar de una pantalla que todavía no se migra desde frontend/.
export default function PaginaPendiente() {
  const titulo = useTituloDeRuta();
  const { pathname } = useLocation();
  return (
    <section className="empty-state pagina-pendiente">
      <span className="empty-state-icon"><i className="ti ti-hammer" aria-hidden="true" /></span>
      <h2>{titulo || 'Pantalla pendiente'}</h2>
      <p>Esta pantalla todavía no se migra. Ruta: <code>{pathname}</code></p>
    </section>
  );
}
