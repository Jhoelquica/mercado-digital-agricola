import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { sesionVencidaAlIniciar } from './auth/sesion';
import { toast } from './components/Toast';
import './styles/tokens.css';
import './styles/legacy.css';
import './styles/app.css';

if (sesionVencidaAlIniciar) toast('Tu sesión anterior venció. Inicia sesión de nuevo para continuar.');

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
