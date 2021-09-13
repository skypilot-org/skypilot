from plot import *
InitMatplotlib(font_size=7)
plt.figure(dpi=300)


y_scale = 400

x = np.array([0, 2, 4, 6, 8, 10])
y1 = np.array([0, 0, 0.4, 0.8, 1.2, 1.6])*y_scale
y2 = np.array([0, 0, 0, 0, 0, 1])*y_scale

x2 = np.array([10, 12, 14])
y1_2 = np.array([1.6, 2.0, 2.4])*y_scale
y2_2 = np.array([1, 2, 3])*y_scale


#ax1.set_title('Race Execution')
plt.plot(x, y1, 'C0-', label='p3.8xlarge (4x V100)')
plt.plot(x, y2, 'C1-', label='Cloud TPU v3-8')

plt.plot(x2, y1_2, 'C0:')
plt.plot(x2, y2_2, 'C1-')

plt.axvline(x=10, linestyle='--', color='#d62728')

plt.xlabel('Time (minutes)')
plt.ylabel('Number of training samples (K)')
plt.legend(frameon=True)

plt.savefig(
    '{}.png'.format('race'),
    bbox_inches='tight',
    dpi=300,
)

