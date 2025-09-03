#! /bin/bash
echo "Using prefix: $PREFIX; #replicas: $REPLICAS"
if [ -z "$HF_TOKEN" ]; then
  echo "Error: HF_TOKEN is not set."
  exit 1
fi
if [ -z "$STABLE_BUILD_API" ]; then
  echo "Error: STABLE_BUILD_API is not set."
  exit 1
fi

names=()
for i in {0..5}; do
  name="${PREFIX}${i}"
  names+=("$name")

  # Build the argument list safely with an array
  cmd=( sky serve up examples/serve/external-lb/llm.yaml -y -n "$name" --env HF_TOKEN --env STABLE_BUILD_API --env REPLICAS --async )
  (( i >= 4 )) && cmd+=( --env DO_PUSHING_ACROSS_LB=true --env LB_PUSHING_ENABLE_LB=false --env ENABLE_SELECTIVE_PUSHING=true )
  (( i == 4 )) && cmd+=( --env LB_POLICY=prefix_tree --env META_LB_POLICY=prefix_tree )
  (( i == 5 )) && cmd+=( --env LB_POLICY=consistent_hashing --env META_LB_POLICY=consistent_hashing )

  printf '>>> %q ' "${cmd[@]}"; echo         # show the exact command
  # printf '%q ' "${cmd[@]}"; echo             # show the exact command
  "${cmd[@]}"                                # run it
done

# This will be used in the following commands
echo ${names[@]}
