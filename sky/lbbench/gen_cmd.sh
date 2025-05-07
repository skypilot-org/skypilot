python3 -m sky.lbbench.launch_lb --service-names a452 a453 a454 a455

python3 -m sky.lbbench.gen_cmd --service-names a404 a405 --exp-name arena_syn_multi_turn_motivation_12_replicas_80_80_80_u240_d600 --extra-args '--workload arena_syn --duration 600' --region-to-args '{"us-east-2":"--num-users 80","ap-northeast-1":"--num-users 80","eu-central-1":"--num-users 80"}' --reload-client

python3 -m sky.lbbench.gen_cmd --service-names a101 a102 --exp-name ablation_single_region_selective_pushing --extra-args '--workload arena_syn --duration 600 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 120"}'

python3 -m sky.lbbench.gen_cmd --service-names a250 a251 a252 a253 a254 a257 --exp-name arena_syn_motivation_12_replicas_200_100_100_c2000_u400_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 300","ap-northeast-1":"--num-users 100"}'

python3 -m sky.lbbench.gen_cmd --service-names a452 a453 a454 a455 --exp-name tot_single_parallel_motivation_12_replicas_trial_2_40_20_20_u80_b2_d600 --extra-args '--workload tot_single --duration 600 --num-branches 2' --region-to-args '{"us-east-2":"--num-users 40","ap-northeast-1":"--num-users 20","eu-central-1":"--num-users 20"}' --reload-client

python3 -m sky.lbbench.gen_cmd --service-names a440 a441 a442 a443 a444 a445 a4410 --exp-name tot_huge_tree_2_1_1_u4_b5_d600 --extra-args '--workload tot_single --duration 600 --num-branches 5' --region-to-args '{"us-east-2":"--num-users 2","ap-northeast-1":"--num-users 1","eu-central-1":"--num-users 1"}' --reload-client

python3 -m sky.lbbench.gen_cmd --service-names a420 a421 a422 a423 a424 a425 a426 a4210 --exp-name mix_part_2_tot_single_16_7_7_u30_b2_d600 --extra-args '--workload tot_single --duration 600 --num-branches 2' --region-to-args '{"us-east-2":"--num-users 16","ap-northeast-1":"--num-users 7","eu-central-1":"--num-users 7"}' --reload-client

python3 -m sky.lbbench.gen_cmd --service-names a420 a421 a422 a423 a424 a425 a426 a4210 --exp-name mix_part_1_wildchat_20_15_15_u50_d600 --extra-args '--workload wildchat --duration 600' --region-to-args '{"us-east-2":"--num-users 20","ap-northeast-1":"--num-users 15","eu-central-1":"--num-users 15"}' --reload-client

python3 -m sky.lbbench.gen_cmd --service-names a452 a453 --exp-name wildchat_multi_turn_motivation_8_replicas_40_30_30_u120_d600_sgl --extra-args '--workload wildchat --duration 600' --region-to-args '{"us-east-2":"--num-users 40","ap-northeast-1":"--num-users 30","eu-central-1":"--num-users 30"}' --reload-client
