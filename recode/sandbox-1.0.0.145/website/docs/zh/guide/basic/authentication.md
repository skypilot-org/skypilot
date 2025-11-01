# 鉴权

支持 JWT 鉴权，通过使用环境变量 `JWT_PUBLIC_KEY`（base64 后的公钥），以下是一个从零开始生成密钥、使用静态密钥模式启动服务并成功请求的完整流程。

## 生成密钥对

```bash
openssl genrsa -out private_key.pem 2048
openssl rsa -in private_key.pem -pubout -out public_key.pem
echo "密钥对生成完毕！"
```

## 启动服务（带公钥开启鉴权）

```bash
export JWT_PUBLIC_KEY=$(cat public_key.pem | base64)
JWT_PUBLIC_KEY="${JWT_PUBLIC_KEY}"
```

## 业务服务用私钥生成 JWT

业务服务用私钥生成一个有效期为1小时的 JWT（模拟业务服务签发）：

```bash
# 这是一个简化的脚本来生成JWT，实际中业务后端应使用成熟的JWT库
base64url_encode() { openssl base64 -e -A | tr '+/' '-_' | tr -d '='; }
header='{"alg":"RS256","typ":"JWT"}'
exp_time=$(($(date +%s) + 3600))
payload="{\"exp\":${exp_time}}"
to_be_signed="$(echo -n "$header" | base64url_encode).$(echo -n "$payload" | base64url_encode)"
signature=$(echo -n "$to_be_signed" | openssl dgst -sha256 -sign private_key.pem | base64url_encode)
jwt="${to_be_signed}.${signature}"
echo "JWT已生成: ${jwt}"
```

## 使用 JWT 访问服务

```bash
curl --silent --show-error -X GET "http://localhost:8080/cdp/json/version" \
     -H "Authorization: Bearer ${jwt}"
```

## 短时票据鉴权示例（以 VNC 为例）

> 不能带 header 的请求就使用 `?ticket`

此示例演示了在一个已认证的环境中，如何获取通用票据并为 VNC 服务构建 URL。

### 前置条件

确保您的服务已通过**静态**或**动态**模式启动并完成鉴权配置。

### 生成长期有效的 JWT

生成一个长期有效的 JWT（模拟已登录的用户）：

```bash
# (生成JWT的脚本与之前的示例相同)
# ...
jwt="..."
```

### 使用 JWT 换取票据

使用 JWT 从通用端点获取票据（默认有效期是 30s，要增加通过 `TICKET_TTL_SECONDS` 环境变量配置）：

```bash
echo "使用JWT换取通用的一次性票据..."

ticket_response=$(curl --silent -X POST "http://localhost:8080/tickets" \
     -H "Authorization: Bearer ${jwt}")

ticket=$(echo "$ticket_response" | jq -r .ticket)
expires=$(echo "$ticket_response" | jq -r .expires_in)

echo "获取成功！票据: ${ticket}, 有效期: ${expires}秒"
```

### 客户端构建并使用 VNC URL

现在，您的前端应用就可以使用获取到的 `${ticket}` 变量来构建VNC URL并发起访问了：

```bash
# Bash脚本模拟客户端拼接URL
vnc_url="http://localhost:8080/vnc/index.html?ticket=${ticket}&path=websockify%3Fticket%3D${ticket}"

echo "客户端构建的最终URL: ${vnc_url}"

# 模拟访问 (实际应在浏览器中进行)
# curl -I "${vnc_url}"
```