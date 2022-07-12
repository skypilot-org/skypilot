#!/usr/bin/env bash
set -ex
docker compose up -d
sky admin deploy docker-cluster-cfg.yaml
cp docker-cluster-cfg.yaml ~/.sky/local/docker.yml
# sky launch -y -c docker examples/minimal.yaml
