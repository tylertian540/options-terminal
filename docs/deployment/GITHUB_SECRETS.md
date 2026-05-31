# GitHub Actions Secrets 配置说明

在 https://github.com/tylertian540/options-terminal/settings/secrets/actions 添加以下 Secrets：

| Secret 名称 | 说明 | 示例 |
|-------------|------|------|
| `POLYGON_API_KEY` | Polygon.io API密钥 | `pk_xxx...` |
| `PROD_HOST` | 生产服务器IP | `1.2.3.4` |
| `PROD_USER` | SSH用户名 | `ubuntu` |
| `PROD_SSH_KEY` | SSH私钥（完整内容） | `-----BEGIN...` |
| `GRAFANA_PASSWORD` | Grafana管理员密码 | `your_password` |

## 快速部署到云服务器

```bash
# 在服务器上（Ubuntu 22.04）：
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
git clone https://tylertian540:ghp_jD3B...@github.com/tylertian540/options-terminal.git
cd options-terminal
cp .env.example .env && nano .env    # 填入 POLYGON_API_KEY
docker-compose up -d
```

访问: http://YOUR_SERVER_IP:3000
