#!/usr/bin/env bash
set -e
# Run after running docker compose up
sky admin deploy docker-cluster-cfg.yaml
cp docker-cluster-cfg.yaml ~/.sky/local/docker.yml
# sky launch -y -c local examples/minimal.yaml
