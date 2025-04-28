# ----- CONFIG -----
# PREFIX="thr-fixed"        # ← change to whatever prefix you like, e.g. "svc" in the above example
# ------------------
echo "Using prefix: $PREFIX"

names=()
for i in {1..6}; do
  name="${PREFIX}${i}"
  names+=("$name")

  # Build the argument list safely with an array
  cmd=( sky serve up examples/serve/external-lb/llm.yaml -y -n "$name" --env HF_TOKEN )
  (( i >= 4 )) && cmd+=( --env DO_PUSHING_ACROSS_LB=true )
  (( i == 5 )) && cmd+=( --env DO_PUSHING_TO_REPLICA=true )
  (( i == 6 )) && cmd+=( --env LB_PUSHING_ENABLE_LB=false )

  printf '>>> %q ' "${cmd[@]}"; echo         # show the exact command
  "${cmd[@]}"                                # run it
done

# This will be used in the following commands
echo ${names[@]}
