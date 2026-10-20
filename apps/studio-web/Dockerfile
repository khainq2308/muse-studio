FROM node:20-alpine
# Run as the image's built-in non-root "node" user; the workdir is created with
# that owner so npm and the Vite dev server can write caches and node_modules.
RUN mkdir -p /workspace/apps/studio-web && chown node:node /workspace/apps/studio-web
USER node
WORKDIR /workspace/apps/studio-web
COPY --chown=node:node package.json ./
RUN npm install
COPY --chown=node:node . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
