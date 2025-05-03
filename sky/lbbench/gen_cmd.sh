python3 -m sky.lbbench.launch_lb --service-names a140 a141

python3 -m sky.lbbench.gen_cmd --service-names a140 a141 a143 a147 --exp-name arena_syn_motivation_50_30_30_c2000_u110_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 50","ap-northeast-1":"--num-users 30","eu-central-1":"--num-users 30"}'

python3 -m sky.lbbench.gen_cmd --service-names a101 a102 --exp-name ablation_single_region_selective_pushing --extra-args '--workload arena_syn --duration 600 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 120"}'

python3 -m sky.lbbench.gen_cmd --service-names a131 a132 a133 a134 a135 a136 a137 --exp-name arena_syn_120_40_fixed_c2000_u160_d240 --extra-args '--workload arena_syn --duration 240 --num-conv 2000' --region-to-args '{"us-east-2":"--num-users 120","ap-northeast-1":"--num-users 40"}'

python3 -m sky.lbbench.gen_cmd --service-names sp-prefix-5 sp-ll-5 sp-rrb-5 --exp-name tot_ablation_prefix_cross_region_6_replica_45_10_10_u65_b2_d240 --extra-args '--workload tot --duration 240 --num-branches 2' --region-to-args '{"us-east-2":"--num-users 45","ap-northeast-1":"--num-users 10","eu-central-1":"--num-users 10"}'
