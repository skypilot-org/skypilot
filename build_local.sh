#!/usr/bin/env bash
docker build -t public.ecr.aws/a9w6z7w5/sky:latest .
docker build -t public.ecr.aws/a9w6z7w5/sky:local -f local/Dockerfile .
