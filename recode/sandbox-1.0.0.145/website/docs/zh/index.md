---
pageType: home

hero:
  name: AIO Sandbox
  text: |
    面向 AI Agents 的
    一体化沙盒环境
  tagline: |
    🌐 浏览器 | 💻 终端 | 📁 文件

    🔧 VSCode | 📊 Jupyter | 🤖 MCP
  actions:
    - theme: brand
      text: 开始使用
      link: /zh/guide/start/introduction
    - theme: alt
      text: GitHub
      link: https://github.com/agent-infra/sandbox
  image:
    src: /aio-icon.png
    alt: AIO Sandbox Logo

features:
  - title: 统一环境
    details: 单一 Docker 容器，共享文件系统。浏览器下载的文件可在终端和 VSCode 中即时访问。
    icon: 🌐
  - title: 开箱即用
    details: 内置 VNC 浏览器、VS Code、Jupyter、文件管理器和终端——均可通过 API/SDK 直接访问。
    icon: ⚡
  - title: 安全执行
    details: 隔离的 Python 和 Node.js 沙盒。安全执行代码，无系统风险。
    icon: 🔐
  - title: AI Agent 就绪
    details: 预配置 MCP 服务器，包含浏览器、文件、终端、Markdown。为 AI Agent 准备就绪。
    icon: 🤖
  - title: 开发者友好
    details: 基于云的 VSCode，具有持久终端、智能端口转发（通过 `${Port}-${domain}/` 或 `/proxy`）和即时前端/后端预览。
    icon: 🔧
  - title: 生产就绪
    details: 企业级 Docker 部署。轻量级、可扩展。
    icon: 🚀
---
