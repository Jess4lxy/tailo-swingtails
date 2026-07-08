# syntax=docker/dockerfile:1
# =============================================================================
# Dockerfile del FRONTEND de Tailo (Vue 3 + Vite) - entregable semana 06.
#
# Compilacion MULTI-ETAPA:
#   * Etapa "build": Node compila la SPA a archivos estaticos (npm run build).
#   * Etapa "runtime": nginx (imagen alpine, muy liviana) sirve esos estaticos Y
#     hace de reverse proxy hacia el contenedor del backend. Node NO viaja a la
#     imagen final -> queda una imagen de ~50 MB en vez de cientos.
# =============================================================================

# -----------------------------------------------------------------------------
# ETAPA 1 - build de la SPA
# -----------------------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# package.json + lock primero (capa cacheable): si el codigo cambia pero las
# dependencias no, Docker reutiliza el 'npm ci'.
COPY package.json package-lock.json ./
RUN npm ci

# Resto del codigo del front y compilacion a /app/dist.
COPY . .
RUN npm run build

# -----------------------------------------------------------------------------
# ETAPA 2 - runtime: nginx sirve el build + reverse proxy a la API
# -----------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Config de nginx (sirve la SPA y reenvia /chat, /conversations, etc. al backend).
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Estaticos compilados desde la etapa de build.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
