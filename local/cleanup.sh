#!/usr/bin/env bash
set -x
docker compose down
sky down -py docker
rm -f ~/.sky/local/docker.yml
