from plot import *
figure_name = 'infer-time-vs-cost'

InitMatplotlib(font_size=7)  # for 1-col figures
plt.figure(figsize=[fig_width, .9024167330839185 + .5], dpi=400)

# 1x V100
v100_time_secs = [337.3]
v100_costs = [0.287]

# Inferentia 1 device. inf1.2xlarge.
inf1_time_secs = [641.1]
inf1_costs = [0.064]

plt.plot(v100_costs,
         np.array(v100_time_secs) / 60,
         marker='o',
         label='V100 GPU (EC2, GCP)')

plt.plot(inf1_costs,
         np.array(inf1_time_secs) / 60,
         marker='x',
         label='Inferentia (EC2)')

plt.title('Inference: offline batched, 1M images')

plt.xlim(0, 0.3)
plt.ylim(0, 12)

plt.grid()

plt.legend()

plt.xlabel('Cost (\$)')
plt.ylabel('Time (minutes)')

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
