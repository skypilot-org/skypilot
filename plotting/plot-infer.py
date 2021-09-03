from plot import *
# InitMatplotlib(font_size=7)  # for 1-col figures
InitMatplotlib(font_size=8)  # for .5-col figures

plot_offline = False
plot_offline = True

figure_name = 'resnet50-infer-{}'.format(
    'offline' if plot_offline else 'online')

# https://docs.google.com/spreadsheets/d/1cwfFAVMjNk4nkfCH8k18j57jqDxKcR_wq3N-7AVSOvI/edit#gid=973498304


results_offline = StringIO("""
hardware,cost,duration
GCP - TPU,0.37,160.8
AWS - V100,0.46,540.0
AWS - Inferentia,0.06,641.1
""")

results_online = StringIO("""
hardware,cost,duration
AWS - V100,1.11,1305.8
AWS - Inferentia,0.12,1233.1
""")

if plot_offline:
    results = results_offline
else:
    results = results_online

########################################################

df = pd.read_csv(results, sep=',')

# Seconds -> minutes.
df['duration'] = df['duration'] / 60

R, C = 1, 1
# fig_width = FigWidth(119.48178)  # 1 of 2 subfigures in a 1-col figure
fig, axes = plt.subplots(
    R,
    C,
    # figsize=[fig_width / 2, .9024167330839185 + .6],
    # figsize=[fig_width / 2.2, .9024167330839185 + .6],
    figsize=[fig_width / 2, .9024167330839185 + .5],
    dpi=400,
)
if C == 1:
    axes = [axes]

for i, ax in enumerate(axes):
    ax = sns.barplot(
        data=df,
        x='hardware',
        y='cost',
        # palette=None,
        palette=['#43a2ca'],
        # palette=['#a8ddb5'],
        # palette=['#43a2ca', '#a8ddb5'],
        ax=ax,
    )

    for p in ax.patches:
        _x = p.get_x() + p.get_width() / 2
        if plot_offline:
            _y = p.get_y() + p.get_height() - .045
        else:
            _y = p.get_y() + p.get_height() - .095
        value = '{:.2f}'.format(p.get_height())
        ax.text(_x, _y, value, ha='center', color='white')

    ax.yaxis.label.set_color('#43a2ca')
    ax.tick_params(axis='y', colors='#43a2ca')

    # Right y-axis
    ax2 = ax.twinx()
    # print(df)
    # print(df['hardware'], df['duration'])
    # print(df['duration'])
    # import ipdb
    # ipdb.set_trace()

    sns.lineplot(
        x=np.arange(len(df)),
        y=df['duration'],
        # y=[160.8, 540.0, 641.1],
        # y=[100.8, 100.0, 100.1],
        # x='hardware',
        # y='duration',
        # data=df,
        color='black',
        alpha=0.5,
        ax=ax2,
        marker='o',
        clip_on=False,
    )
    ax2.set_xticklabels([])

    if plot_offline:
        ax.set_xticklabels([
            'GCP\nTPU',
            'AWS\nV100',
            'AWS\nInferentia',
        ])
    else:
        ax.set_xticklabels([
            'AWS\nV100',
            'AWS\nInferentia',
        ])

    ###

    # Stying.
    ax.xaxis.set_tick_params(length=0)
    ax.yaxis.set_tick_params(length=0)
    ax.tick_params(which='minor', length=0)
    ax2.xaxis.set_tick_params(length=0)
    ax2.yaxis.set_tick_params(length=0)
    ax2.tick_params(which='minor', length=0)

    ax2.set_ylim(0, df['duration'].max() + 1)
    # ax.set_ylim(0, df['cost'].max())
    if not plot_offline:
        ax.set_ylim(0, 1.2)

    ax.set_xlabel('')
    ax.set_ylabel('Cost (\$)' if i == 0 else '')
    # ax2.set_ylabel('Time (s)' if i == 0 else '')
    ax2.set_ylabel('Time (minutes)' if i == 0 else '')

    ax.set_axisbelow(True)
    # ax.grid(axis='y', color='#dedddd', clip_on=False)

    # ax.set_title('Offline (large batch)' if i == 0 else 'Online (batch size 1)')

    if i == 1:
        ax.set_xticklabels([])
        ax.set_yticklabels([])

plt.savefig(
    '{}.pdf'.format(figure_name),
    bbox_inches='tight',
    dpi=400,
)
