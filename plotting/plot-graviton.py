from plot import *
InitMatplotlib(font_size=7)  # for 1-col figures
# InitMatplotlib(font_size=8)  # for .5-col figures

figure_name = 'graviton'

# https://docs.google.com/spreadsheets/d/1cwfFAVMjNk4nkfCH8k18j57jqDxKcR_wq3N-7AVSOvI/edit#gid=973498304

R, C = 1, 1
# fig_width = FigWidth(119.48178)  # 1 of 2 subfigures in a 1-col figure
fig, axes = plt.subplots(
    R,
    C,
    # figsize=[fig_width / 2, .9024167330839185 + .6],
    # figsize=[fig_width / 2.2, .9024167330839185 + .6],
    figsize=[fig_width, .9024167330839185 + .5],
    dpi=400,
)
if C == 1:
    axes = [axes]

ax = axes[0]

labels = ['1K', '2.5K', '10K']
intel = [4.12, 10.31, 41.24]
graviton = [2.99, 7.47, 29.89]
xfr = [2.89, 2.89, 2.89]

x = np.arange(len(labels))  # the label locations
width = 0.3  # the width of the bars

ax.bar(x - width/2, intel, width, label='Intel')
ax.bar(x + width/2, graviton, width, label='Graviton')
ax.bar(x + width/2, xfr, width, bottom=graviton, label='Data Transfer')

ax.set_ylabel('Cost (\$)')
ax.set_xlabel('Number of queries')
ax.set_xticks(x)
ax.set_xticklabels(labels)
ax.grid(axis='y')
ax.legend(frameon=True)

plt.savefig(
    '{}.pdf'.format(figure_name),
    bbox_inches='tight',
    dpi=400,
)
