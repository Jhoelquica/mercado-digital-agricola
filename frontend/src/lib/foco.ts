import type { KeyboardEvent } from 'react';

// Tab no se escapa de un modal abierto.
export function atraparFoco(e: KeyboardEvent<HTMLElement>): void {
  if (e.key !== 'Tab') return;
  const enfocables = [...e.currentTarget.querySelectorAll<HTMLElement>('button, input, select, textarea, [href]')]
    .filter((el) => !(el as HTMLButtonElement).disabled && !el.hidden && el.offsetParent !== null && !((el as HTMLInputElement).type === 'radio' && !(el as HTMLInputElement).checked));
  const primero = enfocables[0];
  const ultimo = enfocables[enfocables.length - 1];
  if (!primero || !ultimo) return;
  if (e.shiftKey && document.activeElement === primero) {
    e.preventDefault();
    ultimo.focus();
  } else if (!e.shiftKey && document.activeElement === ultimo) {
    e.preventDefault();
    primero.focus();
  }
}
