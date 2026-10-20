FROM node:20-alpine
# Run as the image's built-in non-root "node" user.
RUN mkdir -p /workspace/apps/studio-web && chown node:node /workspace/apps/studio-web
USER node
WORKDIR /workspace/apps/studio-web
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci
COPY --chown=node:node . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
