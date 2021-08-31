from plot import *
figure_name = 'resnet50-time-vs-cost'

InitMatplotlib(font_size=7)  # for 1-col figures
plt.figure(figsize=[fig_width, .9024167330839185 + .5], dpi=400)

# 1x V100; 4x V100
v100_time_hours = [25.7, 10.83]
v100_costs = [79, 133.27]

# 1x V100; 8x V100
# 8x V100 numbers were early and seem wrong.
# v100_time_hours = [25, 3.5]
# v100_costs = [76.5, 85.68]

plt.plot(v100_costs, v100_time_hours, marker='o', ls='--', label='EC2')
plt.plot(32.12, 3.9, label='GCP (compute only)', marker='x')
plt.plot(32.12 + 13, 3.9 + 3 / 60, label='GCP (with egress)', marker='o')

plt.text(v100_costs[0] + 1.5, v100_time_hours[0] + 1.5, '1x V100')
plt.text(v100_costs[1] - 13, v100_time_hours[1] - 4, '4x V100')

# plt.title('Training (data on AWS S3)')

plt.xlim(0, 140)
plt.ylim(0, 30)

plt.grid()

plt.legend(frameon=True)

plt.xlabel('Cost (\$)')
plt.ylabel('Time (hours)')

plt.savefig(
    '{}.pdf'.format(figure_name),
    bbox_inches='tight',
    dpi=400,
)

plt.savefig(
    '{}.png'.format(figure_name),
    bbox_inches='tight',
    dpi=400,
)
