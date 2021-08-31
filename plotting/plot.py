from io import StringIO
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
import pandas as pd
import glob
import pickle

import re
import os

# Purples.
colors = list(reversed(sns.color_palette("ch:2.5,+.2,dark=.3")))[:2]
colors = sns.color_palette("ch:2.5,+.2,dark=.3")[:2]
darker_purple = colors[-1]
light_purple = colors[0]
light_blue = '#a2d4ec'
set2 = sns.color_palette('Set2')
paired = sns.color_palette('Paired')

###############

RESEARCH = 1  # For research presentation.
RESEARCH = 0  # For paper pdf.

# font_size = {0: 10, 1: 8}[RESEARCH]
# font_size = 6.5  # Manual fix for 1-full-col figures.
# font_size = 8  # Manual fix for 0.5-full-col figures.
TEXT_USETEX = bool(1 - RESEARCH)

# Get from LaTeX using "The column width is: \the\columnwidth"
vldb_col_width_pt = 240
icml_col_width_pt = 234.8775
sigmod20_col_width_pt = 241.14749
vldb21_col_width_pt = 241.14749
nsdi22_col_width_pt = 241.02039
fig_width_pt = nsdi22_col_width_pt

inches_per_pt = 1.0 / 72.27  # Convert pt to inch
golden_mean = (np.sqrt(5) - 1.0) / 2.0  # Aesthetic ratio


def FigWidth(pt):
    return pt * inches_per_pt  # width in inches


fig_width = FigWidth(fig_width_pt)  # width in inches
fig_height = fig_width * golden_mean  # height in inches
fig_size = [fig_width, fig_height]


def InitMatplotlib(font_size):
    print('fig_size', fig_size, '\nuse_tex', TEXT_USETEX, '\nfont_size',
          font_size)
    # https://matplotlib.org/3.2.1/tutorials/introductory/customizing.html
    params = {
        'backend': 'ps',
        'figure.figsize': fig_size,
        'text.usetex': TEXT_USETEX,
        # 'font.family': 'serif',
        # 'font.serif': ['Times'],
        # 'font.family': 'sans-serif',
        'font.sans-serif': [
            # 'Lato',
            # 'DejaVu Sans', 'Bitstream Vera Sans',
            # 'Computer Modern Sans Serif', 'Lucida Grande', 'Verdana', 'Geneva',
            # 'Lucid',
            # 'Arial',
            'Helvetica',
            'Avant Garde',
            'sans-serif'
        ],
        # Make math fonts (e.g., tick labels) sans-serif.
        # https://stackoverflow.com/a/20709149/1165051
        'text.latex.preamble': [
            r'\usepackage{siunitx}',  # i need upright \micro symbols, but you need...
            r'\sisetup{detect-all}',  # ...this to force siunitx to actually use your fonts
            r'\usepackage{helvet}',  # set the normal font here
            r'\usepackage{sansmath}',  # load up the sansmath so that math -> helvet
            r'\sansmath'  # <- tricky! -- gotta actually tell tex to use!
        ],

        # axes.titlesize      : large   # fontsize of the axes title
        'axes.titlesize': font_size,
        'axes.labelsize': font_size,
        'font.size': font_size,
        'legend.fontsize': font_size,
        'legend.fancybox': False,
        'legend.framealpha': 1.0,
        'legend.edgecolor': '0.1',  # ~black border.
        'legend.shadow': False,
        'legend.frameon': False,
        'xtick.labelsize': font_size,
        'ytick.labelsize': font_size,
        'xtick.direction': 'in',
        'ytick.direction': 'in',

        # http://phyletica.org/matplotlib-fonts/
        # Important for cam-ready (otherwise some fonts are not embedded):
        'pdf.fonttype': 42,
        'lines.linewidth': 2,

        # Styling.
        'grid.color': '#dedddd',
        'grid.linewidth': .5,
        'axes.grid.axis': 'y',
        'xtick.bottom': False,
        'xtick.top': False,
        'ytick.left': False,
        'ytick.right': False,
        'axes.spines.left': False,
        'axes.spines.bottom': True,
        'axes.spines.right': False,
        'axes.spines.top': False,
        'axes.axisbelow': True,
    }

    plt.style.use('seaborn-colorblind')
    plt.rcParams.update(params)


def PlotCdf(data, label=None, bins=2000, **kwargs):
    counts, bin_edges = np.histogram(data, bins=bins)
    cdf = np.cumsum(counts)
    plt.plot(bin_edges[1:], cdf / cdf[-1], label=label, lw=2, **kwargs)
