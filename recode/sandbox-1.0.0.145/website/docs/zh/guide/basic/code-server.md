# Code Server

AIO Sandbox 包含一个功能齐全的 VSCode Server（Code Server），提供可通过 Web 浏览器访问的完整云开发环境。

![](/images/code-server.png)

## 访问 Code Server

Code Server 可通过以下地址访问：
```
http://localhost:8080/code-server/
```

默认无需身份验证 - 可立即访问完整的 VSCode 环境。

## 功能

### 完整的 VSCode 体验
- 浏览器中的完整 VSCode 界面
- 扩展市场访问
- 集成终端
- Git 集成
- IntelliSense 和语法高亮
- 调试功能

### 文件系统集成
访问与其他沙盒组件相同的文件系统：
- 浏览器/Shell 中创建的文件立即显示
- 编辑来自 API 操作的文件
- 立即处理下载的文件
- 更改在所有界面中反映

### 开发工具
预配置的开发环境：
- Node.js 运行时
- Python 解释器
- Git 版本控制
- 包管理器（npm、pip 等）
- 开发服务器

## 工作区设置

### 项目结构
推荐的工作区组织：
```
/home/gem/
├── projects/           # 您的开发项目
├── Downloads/          # 浏览器下载
├── .config/           # 工具配置
└── workspace/         # 当前工作目录
```

### 打开项目
1. 导航到 Code Server 界面
2. 打开文件夹：`/home/gem/projects/myproject`
3. 使用 文件 → 打开文件夹 或 Ctrl+K, Ctrl+O
4. 选择项目目录

### 创建新项目
```bash
# 创建项目结构
mkdir -p /home/gem/projects/new-app
cd /home/gem/projects/new-app

# 初始化项目
npm init -y
git init

# 在 Code Server 中打开
# 导航到 http://localhost:8080/code-server/
# 打开文件夹：/home/gem/projects/new-app
```