# El build de React (Vite) en una imagen de Node y el resultado servido por nginx. Las variables VITE_* quedan dentro del
# código del navegador (no son secretos): por defecto, las de desarrollo (Culqi de pruebas y el Client ID de Google con
# localhost); en Google Cloud se pasan las de producción (deploy/gcp/construir_imagenes.py).
FROM node:22.22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_URL=/api
ARG VITE_CULQI_PUBLIC_KEY=pk_test_gGgpDDFDAt5HjYwI
ARG VITE_GOOGLE_CLIENT_ID=546858204347-n72ku2ohsq88er0p297hrd9n2rirsc86.apps.googleusercontent.com
ENV VITE_API_URL=$VITE_API_URL VITE_CULQI_PUBLIC_KEY=$VITE_CULQI_PUBLIC_KEY VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID
# Revisa los tipos y genera dist/ (npm run build = typecheck + vite build).
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Vacía en Kubernetes; docker-compose.yml monta aquí el gateway de /api y /media (deploy/compose/frontend-gateway.conf).
RUN mkdir -p /etc/nginx/extra
EXPOSE 8080
