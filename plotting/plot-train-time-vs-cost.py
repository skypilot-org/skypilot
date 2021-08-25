from plot import *
figure_name = 'time-vs-cost'

InitMatplotlib(font_size=7)  # for 1-col figures
plt.figure(figsize=[fig_width, .9024167330839185 + .5], dpi=400)

# 1x V100; 8x V100
v100_time_hours = [25, 3.5]
v100_costs = [76.5, 85.68]

plt.plot(v100_costs,
         v100_time_hours,
         marker='o',
         ls='--',
         label='EC2 (V100 GPUs)')
plt.plot(31.4 + 13, 3.9 + 3 / 60, label='GCP TPU plus egress cost', marker='x')
plt.plot(31.4, 3.9, label='GCP TPU', marker='o')

plt.title('Training (data on AWS S3)')

plt.xlim(0, 100)
plt.ylim(0, 30)

plt.grid()

plt.legend()

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
