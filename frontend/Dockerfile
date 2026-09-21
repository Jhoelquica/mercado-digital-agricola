FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY . /usr/share/nginx/html

# Lo que no es del sitio no se sirve (ni la receta de la imagen ni el script de versiones de caché).
RUN cd /usr/share/nginx/html && rm -f Dockerfile nginx.conf security-headers.conf bump_cache_version.py .dockerignore

EXPOSE 8080
