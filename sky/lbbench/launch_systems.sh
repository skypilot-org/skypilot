#! /bin/bash
# ----- CONFIG -----
# PREFIX="thr-fixed"        # ← change to whatever prefix you like, e.g. "svc" in the above example
# ------------------
echo "Using prefix: $PREFIX"

names=()
# for i in {0..7}; do
# for i in 0 1 2 3 4 5 10; do
# for i in 0 3 5 7 13 16; do
for i in 11 14; do
  name="${PREFIX}${i}"
  names+=("$name")

  # Build the argument list safely with an array
  cmd=( sky serve up examples/serve/external-lb/llm.yaml -y -n "$name" --env HF_TOKEN )
  (( i == 10 )) && cmd+=( --env USE_V2_STEALING=true )
  (( i == 11 )) && cmd+=( --env FORCE_DISABLE_STEALING=true )
  (( i >= 12 )) && cmd+=( --env DO_PUSHING_ACROSS_LB=true )
  (( i == 13 )) && cmd+=( --env DO_PUSHING_TO_REPLICA=true )
  (( i >= 14 )) && cmd+=( --env LB_PUSHING_ENABLE_LB=false )
  (( i == 14 )) && cmd+=( --env LB_POLICY=prefix_tree --env META_LB_POLICY=prefix_tree )
  (( i == 15 )) && cmd+=( --env LB_POLICY=least_load --env META_LB_POLICY=least_load )
  (( i == 16 )) && cmd+=( --env LB_POLICY=round_robin --env META_LB_POLICY=round_robin )
  (( i == 17 )) && cmd+=( --env LB_POLICY=consistent_hashing --env META_LB_POLICY=consistent_hashing )
  (( i == 18 )) && cmd+=( --env LB_POLICY=prefix_tree --env META_LB_POLICY=prefix_tree --env DISABLE_LEAST_LOAD_IN_PREFIX=true )

  printf '>>> %q ' "${cmd[@]}"; echo         # show the exact command
  # printf '%q ' "${cmd[@]}"; echo             # show the exact command
  "${cmd[@]}"                                # run it
done

# This will be used in the following commands
echo ${names[@]}
