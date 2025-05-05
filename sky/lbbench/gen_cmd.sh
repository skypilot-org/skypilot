python3 -m sky.lbbench.launch_lb --service-names a290 a291 a292

python3 -m sky.lbbench.gen_cmd --service-names a170 a171 a173 a177 --exp-name arena_syn_motivation_12_replicas_2_region_300_100_100_c2000_u500_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 300","ap-northeast-1":"--num-users 100","eu-central-1":"--num-users 100"}'

python3 -m sky.lbbench.gen_cmd --service-names a101 a102 --exp-name ablation_single_region_selective_pushing --extra-args '--workload arena_syn --duration 600 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 120"}'

python3 -m sky.lbbench.gen_cmd --service-names a250 a251 a252 a253 a254 a257 --exp-name arena_syn_motivation_12_replicas_2_region_300_100_c2000_u400_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 300","ap-northeast-1":"--num-users 100"}'

python3 -m sky.lbbench.gen_cmd --service-names a270 a271 a272 a273 a274 a277 --exp-name tot_motivation_12_replicas_2_region_80_40_u120_b2_d600 --extra-args '--workload tot --duration 600 --num-branches 2' --region-to-args '{"us-east-2":"--num-users 80","ap-northeast-1":"--num-users 40"}'

python3 -m sky.lbbench.gen_cmd --service-names a290 a291 a292 a293 a294 a297 --exp-name tot_single_motivation_12_replicas_200_100_100_u400_b2_d600 --extra-args '--workload tot_single --duration 600 --num-branches 2' --region-to-args '{"us-east-2":"--num-users 200","ap-northeast-1":"--num-users 100","eu-central-1":"--num-users 100"}'
