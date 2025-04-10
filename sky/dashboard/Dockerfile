FROM node:20.15.1-bullseye

COPY . /app

WORKDIR /app
RUN cd /app && npm install && npm run build
