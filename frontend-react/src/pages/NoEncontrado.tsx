import { useEffect } from 'react';
import { Link, useRouteError } from 'react-router';

export function NoEncontrado() {
  useEffect(() => {
    document.title = 'Página no encontrada — ChakraShop';
  }, []);
  return (
    <section className="empty-state pagina-pendiente">
      <span className="empty-state-icon"><i className="ti ti-map-question" aria-hidden="true" /></span>
      <h2>No encontramos esta página</h2>
      <p>Puede que el enlace esté mal escrito o que la página ya no exista.</p>
      <Link className="btn btn-primary" to="/">Volver al inicio</Link>
    </section>
  );
}

// Un error al cargar una ruta (por ejemplo, sin red al traer su código): se ofrece recargar en vez de una pantalla en blanco.
export function ErrorDeRuta() {
  console.error(useRouteError());
  return (
    <main className="empty-state pagina-pendiente">
      <span className="empty-state-icon"><i className="ti ti-alert-triangle" aria-hidden="true" /></span>
      <h2>No se pudo abrir esta página</h2>
      <p>Revisa tu conexión e inténtalo de nuevo.</p>
      <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>Recargar</button>
    </main>
  );
}
