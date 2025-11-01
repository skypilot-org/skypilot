# 🚀 AIO Sandbox SDK

<div align="center">

[![Version](https://img.shields.io/npm/v/@agent-infra/sandbox?style=for-the-badge&logo=npm&color=cb3837)](https://www.npmjs.com/package/@agent-infra/sandbox)
[![Node.js](https://img.shields.io/badge/node-%3E%3D18.0.0-brightgreen?style=for-the-badge&logo=node.js)](https://nodejs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-%3E%3D5.0.0-blue?style=for-the-badge&logo=typescript)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

**🔧 A powerful Node.js library for seamless AIO Sandbox integration**

</div>

## 📖 Overview

> **🎯 Modern • Modular • TypeScript-First**

The **AIO Sandbox SDK** is a cutting-edge Node.js library engineered for seamless integration with the AIO Sandbox environment. It delivers a comprehensive toolkit for:

🔹 **Shell Execution** - Command automation and process management
🔹 **File Operations** - Complete file system interaction
🔹 **Jupyter Integration** - Notebook code execution and management
🔹 **Browser Automation** - Advanced web interaction capabilities
🔹 **TypeScript Support** - Full type safety and IntelliSense

Built with a **modern modular architecture**, the SDK ensures clean separation of concerns and provides an exceptional developer experience through comprehensive TypeScript support.

## ✨ Features

<table>
<tr>
<td width="50%">

### 🖥️ **Shell Execution**
- ⚡ Synchronous command execution
- 🔄 Asynchronous execution with polling
- 📊 Real-time output monitoring
- ⏱️ Configurable timeout handling

### 📁 **File Management**
- 📋 Directory listing with recursion
- ✏️ File editing capabilities
- 💾 Download and upload operations
- 🗂️ Advanced file system navigation

</td>
<td width="50%">

### 🐍 **Jupyter Integration**
- 🚀 Code execution in multiple kernels
- 📓 Notebook management
- 🔧 Customizable execution parameters
- 📈 Output capture and processing

### 🌐 **Browser Automation**
- 🎯 CDP (Chrome DevTools Protocol) integration
- 📱 Browser information retrieval
- 📸 Screenshot capture
- 🖱️ Mouse and keyboard automation
- 🎬 Advanced browser action execution

</td>
</tr>
</table>

### 🏗️ **Architecture Highlights**
- 🧩 **Modular Design** - Clean separation of concerns
- 📦 **TypeScript First** - Complete type safety
- 🔄 **Async/Await** - Modern promise-based API
- ⚙️ **Configurable** - Flexible client configuration

## 📦 Installation

<div align="center">

### Quick Start Installation

</div>

Install the SDK using your preferred package manager:

```bash
# Using pnpm (recommended)
pnpm install @agent-infra/sandbox

# Using npm
npm install @agent-infra/sandbox

# Using yarn
yarn add @agent-infra/sandbox
```

> 💡 **Tip**: We recommend using `pnpm` for faster installation and better dependency management.

## 🚀 Usage Guide

### ⚙️ Basic Configuration

Start by importing the SDK and configuring the client:

```typescript
import { AioClient } from "@agent-infra/sandbox";

const client = new AioClient({
  baseUrl: `https://{aio.sandbox.example}`, //The Url and Port should consistent with the Aio Sandbox
  timeout: 30000, // Optional: request timeout in milliseconds
  retries: 3, // Optional: number of retry attempts
  retryDelay: 1000, // Optional: delay between retries in milliseconds
});
```

### 🖥️ Shell Execution

Execute shell commands within the sandbox environment:

```typescript
const response = await client.shellExec({
  command: "ls -la",
});

if (response.success) {
  console.log("Command Output:", response.data.output);
} else {
  console.error("Error:", response.message);
}

// Asynchronous rotation training results, suitable for long-term tasks
const response = await client.shellExecWithPolling({
  command: "ls -la",
  maxWaitTime: 60 * 1000,
});
```

### 📁 File Management

Manage files and directories within the sandbox:

```typescript
const fileList = await client.fileList({
  path: "/home/gem",
  recursive: true,
});

if (fileList.success) {
  console.log("Files:", fileList.data.files);
} else {
  console.error("Error:", fileList.message);
}
```

### 🐍 Jupyter Code Execution

Execute code in Jupyter notebooks with multiple kernel support:

```typescript
const jupyterResponse = await client.jupyterExecute({
  code: "print('Hello, Jupyter!')",
  kernel_name: "python3",
});

if (jupyterResponse.success) {
  console.log("Output:", jupyterResponse.data);
} else {
  console.error("Error:", jupyterResponse.message);
}
```

### 🌐 Browser Automation

Advanced browser control, automation, and web interaction capabilities:

```typescript
// Get browser information
const browserInfo = await client.browserInfo();
if (browserInfo.success) {
  console.log("Browser Info:", browserInfo.data);
}

// Capture screenshot
const screenshot = await client.browserScreenshot();
if (screenshot.ok) {
  const buffer = await screenshot.arrayBuffer();
  // Save or process screenshot buffer
}

// Get CDP version information
const cdpVersion = await client.browserCdpVersion();
if (cdpVersion.success) {
  console.log("CDP Version:", cdpVersion.data);
}

// Execute browser actions (click, type, scroll, etc.)
const actionResponse = await client.browserActions({
  actions: [
    { action_type: "MOVE_TO", x: 100, y: 100 },
    { action_type: "CLICK", x: 100, y: 100 },
    { action_type: "TYPING", text: "Hello World" }
  ]
});

if (actionResponse.success) {
  console.log("Actions executed successfully");
}
```

## 🐳 Sandbox Startup Instructions

<div align="center">

### 🚀 Quick Docker Setup

</div>

Follow these simple steps to get your sandbox running locally:

#### Prerequisites
- 🐳 **Docker** installed on your system
- 🌐 **Network access** for pulling the image

#### Step-by-Step Guide

**1️⃣ Pull the AIO Sandbox Image**
```shell
docker pull aio.sandbox:latest
```

**2️⃣ Start the Container**
```shell
docker run --security-opt seccomp=unconfined -it -p 8821:8080 aio.sandbox:latest
```

**3️⃣ Verify Connection**
The sandbox server will be available at:
```
🌐 http://localhost:8821
```

**4️⃣ Update Client Configuration**
```typescript
const client = new AioClient({
  baseUrl: "http://localhost:8821", // ✅ Local sandbox URL
  timeout: 30000,
  retries: 3,
});
```

> ✅ **Ready to go!** Your sandbox environment is now running and ready for integration.

## 🛠️ Development

<div align="center">

### 🔧 Developer Commands

</div>

<table>
<tr>
<td width="33%">

#### 🏗️ **Build**
Compile TypeScript to JavaScript:
```bash
pnpm build
```

</td>
<td width="33%">

#### ✅ **Test**
Run the test suite with Vitest:
```bash
pnpm test
```

</td>
<td width="33%">

#### 🧹 **Clean**
Remove build artifacts:
```bash
pnpm clean
```

</td>
</tr>
</table>

### 📋 **Development Scripts**

| Command | Description | Purpose |
|---------|-------------|---------|
| `pnpm build` | 🏗️ Compile TypeScript | Production builds |
| `pnpm test` | ✅ Run test suite | Quality assurance |
| `pnpm test:watch` | 👀 Watch mode testing | Development testing |
| `pnpm clean` | 🧹 Clean dist folder | Fresh rebuilds |
| `pnpm lint` | 🔍 Code linting | Code quality |
| `pnpm dev` | 🚀 Development mode | Live development |

---

## 🤝 Contributing

<div align="center">

**🎉 We welcome contributions from the community! 🎉**

</div>

### 🌟 **How to Contribute**

1. 🍴 **Fork** the repository
2. 🌿 **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. 💾 **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. 📤 **Push** to the branch (`git push origin feature/amazing-feature`)
5. 🔃 **Open** a Pull Request

### 📋 **Contribution Guidelines**

- ✅ Write clear, concise commit messages
- 🧪 Add tests for new functionality
- 📚 Update documentation as needed
- 🎨 Follow existing code style and conventions
- 🐛 Submit issues for bugs and feature requests

### 💬 **Get Help**

- 📖 Check our [documentation](docs/)
- 🐛 Report bugs via [GitHub Issues](issues/)
- 💡 Suggest features via [GitHub Discussions](discussions/)
- 📧 Contact the maintainers for questions

---

<div align="center">

**Made with ❤️ by the AIO Sandbox Team**

[![GitHub](https://img.shields.io/badge/GitHub-Repository-black?style=for-the-badge&logo=github)](https://github.com/your-repo)
[![NPM](https://img.shields.io/badge/NPM-Package-red?style=for-the-badge&logo=npm)](https://www.npmjs.com/package/@agent-infra/sandbox)
[![TypeScript](https://img.shields.io/badge/Built_with-TypeScript-blue?style=for-the-badge&logo=typescript)](https://www.typescriptlang.org/)

</div>
