import * as path from 'node:path';
import { pluginSass } from '@rsbuild/plugin-sass';
import { pluginLlms } from '@rspress/plugin-llms';
import { pluginSitemap } from '@rspress/plugin-sitemap';
import { pluginTwoslash } from '@rspress/plugin-twoslash';
import {
  transformerNotationDiff,
  transformerNotationErrorLevel,
  transformerNotationFocus,
  transformerNotationHighlight,
} from '@shikijs/transformers';
import { defineConfig } from '@rspress/core';
import { pluginGoogleAnalytics } from 'rsbuild-plugin-google-analytics';
import { pluginOpenGraph } from 'rsbuild-plugin-open-graph';
import { pluginFontOpenSans } from 'rspress-plugin-font-open-sans';

const siteUrl = 'https://sandbox.agent-infra.com';

export default defineConfig({
  root: path.join(__dirname, 'docs'),
  lang: 'en',
  title: 'AIO Sandbox',
  description:
    'All-in-One Agent Sandbox Environment - Browser, Shell, File, VSCode Server, and MCP Hub in One Container',
  icon: '/aio-icon.png',
  logo: {
    dark: '/aio-icon.png',
    light: '/aio-icon.png',
  },
  route: {
    cleanUrls: true,
  },
  markdown: {
    shiki: {
      langAlias: {
        Bash: 'shellscript',
        Shell: 'shellscript',
        Dockerfile: 'docker',
        Python: 'python',
      },
      langs: ['shellscript', 'docker', 'python'],
      transformers: [
        transformerNotationDiff(),
        transformerNotationErrorLevel(),
        transformerNotationHighlight(),
        transformerNotationFocus(),
      ],
    },
    link: {
      checkDeadLinks: false,
    },
  },
  plugins: [
    pluginTwoslash(),
    pluginFontOpenSans(),
    pluginSitemap({
      siteUrl,
    }),
    pluginLlms(),
  ],
  base: process.env.BASE_URL ?? '/',
  outDir: 'doc_build',
  builderConfig: {
    html: {
      template: 'public/index.html',
    },
    plugins: [
      pluginSass(),
      pluginGoogleAnalytics({ id: 'G-VDPJE6PYSN' }),
      pluginOpenGraph({
        url: siteUrl,
        image: 'https://rspress.rs/og-image.png',
        description: 'Rsbuild based static site generator',
        twitter: {
          site: '@rspack_dev',
          card: 'summary_large_image',
        },
      }),
    ],
  },
  locales: [
    {
      lang: 'en',
      label: 'English',
      title: 'Rspress',
      description: 'Static Site Generator',
    },
    {
      lang: 'zh',
      label: '简体中文',
      title: 'Rspress',
      description: '静态网站生成器',
    },
  ],
  themeConfig: {
    // hideNavbar: 'auto',
    socialLinks: [
      {
        icon: 'github',
        mode: 'link',
        content: 'https://github.com/agent-infra/sandbox',
      },
    ],
    footer: {
      message: 'Built with ❤️ for AI Agents · AIO Sandbox © 2025',
    },
    locales: [
      {
        lang: 'zh',
        label: '简体中文',
        editLink: {
          docRepoBaseUrl:
            'https://github.com/agent-infra/sandbox/tree/main/site/docs',
          text: '📝 在 GitHub 上编辑此页',
        },
        overview: {
          filterNameText: '过滤',
          filterPlaceholderText: '输入关键词',
          filterNoResultText: '未找到匹配的 API',
        },
      },
      {
        lang: 'en',
        label: 'English',
        editLink: {
          docRepoBaseUrl:
            'https://github.com/agent-infra/sandbox/tree/main/site/docs',
          text: '📝 Edit this page on GitHub',
        },
      },
    ],
  },
  languageParity: {
    enabled: false,
    include: [],
    exclude: [],
  },
});
