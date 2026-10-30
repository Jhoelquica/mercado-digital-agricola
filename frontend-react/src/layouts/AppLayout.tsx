import { useEffect } from 'react';
import { Outlet, useMatches } from 'react-router';
import { Navbar } from './Navbar';

export interface DatosDeRuta {
  titulo?: string;
}

export function useTituloDeRuta(): string | undefined {
  const matches = useMatches();
  return (matches.at(-1)?.handle as DatosDeRuta | undefined)?.titulo;
}

export function AppLayout() {
  const titulo = useTituloDeRuta();

  useEffect(() => {
    document.title = titulo ? `${titulo} — ChakraShop` : 'ChakraShop — Mercado Digital Agrícola';
  }, [titulo]);

  return (
    <>
      <a className="enlace-saltar" href="#app-main">Saltar al contenido</a>
      <Navbar />
      <main id="app-main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}
