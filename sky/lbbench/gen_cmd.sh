# 12 replicas

# python3 -m sky.lbbench.gen_cmd --exp-name andy_tot_single_parallel_motivation_12_replicas_40_20_20_u80_b2_d600 --extra-args '--workload tot_single --duration 600 --num-branches 2' --region-to-args '{"us-central1":"--num-users 40","asia-northeast1":"--num-users 20","europe-west1":"--num-users 20"}' --reload-client

# kubectl delete pods --all --context=gke_skypilot-375900_us-central1_sglang-us; kubectl delete pods --all --context=gke_skypilot-375900_asia-northeast1_sglang-asia; kubectl delete pods --all --context=gke_skypilot-375900_europe-west1-b_sglang-eu

# python3 -m sky.lbbench.gen_cmd --exp-name andy_tot_huge_tree_2_1_1_u4_b5_d600 --extra-args '--workload tot_single --duration 600 --num-branches 5' --region-to-args '{"us-central1":"--num-users 2","asia-northeast1":"--num-users 1","europe-west1":"--num-users 1"}' --reload-client

# kubectl scale deployment sglang-deployment --replicas=3 -n default --context=gke_skypilot-375900_us-central1_sglang-us
# kubectl scale deployment sglang-deployment --replicas=3 -n default --context=gke_skypilot-375900_asia-northeast1_sglang-asia
# kubectl scale deployment sglang-deployment --replicas=2 -n default --context=gke_skypilot-375900_europe-west1-b_sglang-eu

# kubectl delete pods --all --context=gke_skypilot-375900_us-central1_sglang-us; kubectl delete pods --all --context=gke_skypilot-375900_asia-northeast1_sglang-asia; kubectl delete pods --all --context=gke_skypilot-375900_europe-west1-b_sglang-eu

# python3 -m sky.lbbench.gen_cmd --exp-name andy_arena_syn_multi_turn_motivation_8_replicas_80_80_80_u240_d600 --extra-args '--workload arena_syn --duration 600' --region-to-args '{"us-central1":"--num-users 80","asia-northeast1":"--num-users 80","europe-west1":"--num-users 80"}' --reload-client

kubectl delete pods --all --context=gke_skypilot-375900_us-central1_sglang-us; kubectl delete pods --all --context=gke_skypilot-375900_asia-northeast1_sglang-asia; kubectl delete pods --all --context=gke_skypilot-375900_europe-west1-b_sglang-eu

python3 -m sky.lbbench.gen_cmd --exp-name andy_wildchat_multi_turn_motivation_8_replicas_40_30_30_u100_d600_sgl --extra-args '--workload wildchat --duration 600' --region-to-args '{"us-central1":"--num-users 40","asia-northeast1":"--num-users 30","europe-west1":"--num-users 30"}' --reload-client