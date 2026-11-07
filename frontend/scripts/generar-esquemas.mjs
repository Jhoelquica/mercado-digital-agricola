// Regenera src/api/esquemas/*.ts (los cuerpos que acepta cada servicio) desde el OpenAPI del clúster:
//   node scripts/generar-esquemas.mjs
// El gateway bloquea /openapi.json, así que se lee dentro de cada pod. Solo se conservan los esquemas: las rutas no
// hacen falta porque el cliente las escribe a mano, como api.js, y muchas son internas entre servicios.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const PUERTOS = {
  usuarios: 8006, productores: 8001, productos: 8002, pedidos: 8003,
  transporte: 8004, notificaciones: 8005, pagos: 8007, certificacion: 8008,
};
const temporal = mkdtempSync(join(tmpdir(), 'esquemas-'));

for (const [servicio, puerto] of Object.entries(PUERTOS)) {
  const leer = `import urllib.request,sys;sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:${puerto}/openapi.json').read().decode())`;
  const spec = JSON.parse(execFileSync('kubectl', ['exec', `deploy/${servicio}`, '--', 'python', '-c', leer], { encoding: 'utf8', maxBuffer: 1 << 26 }));
  const entrada = join(temporal, `${servicio}.json`);
  writeFileSync(entrada, JSON.stringify({ ...spec, paths: {} }));
  execFileSync('npx', ['--yes', '-p', 'openapi-typescript@7.13.0', '-p', 'typescript@5.9.3', 'openapi-typescript', entrada, '--default-non-nullable=false', '-o', `src/api/esquemas/${servicio}.ts`], { stdio: 'inherit', shell: true });
}
rmSync(temporal, { recursive: true, force: true });
