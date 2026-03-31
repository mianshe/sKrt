# 同机部署说明

适用场景：
- 前端静态文件和后端 API 部署在同一台 Linux 服务器
- `nginx` 对外提供访问
- `systemd` 托管后端进程

## 1. 推荐目录

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone https://github.com/mianshe/sKrt.git skrt
sudo chown -R $USER:$USER /opt/skrt
cd /opt/skrt
```

## 2. 初始化环境

```bash
cd /opt/skrt
python3.11 -m venv backend/.venv
source backend/.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
npm --prefix frontend ci
cp .env.example .env
```

按需编辑：
- `.env`
- `deploy/nginx/skrt.conf`
- `deploy/systemd/skrt.service`

至少把这几个占位值换掉：
- `/opt/skrt`
- `your-domain.com`
- `User=www-data`
- `Group=www-data`

## 3. 首次启动验证

```bash
cd /opt/skrt
INSTALL_DEPS=1 BUILD_FRONTEND=1 ./start.sh
```

看到 `Uvicorn: backend.main:app on 0.0.0.0:8000` 基本就说明后端起来了。

## 4. 安装 systemd 服务

```bash
sudo cp /opt/skrt/deploy/systemd/skrt.service /etc/systemd/system/skrt.service
sudo systemctl daemon-reload
sudo systemctl enable skrt
sudo systemctl start skrt
sudo systemctl status skrt --no-pager
```

看日志：

```bash
sudo journalctl -u skrt -f
```

## 5. 安装 nginx 配置

```bash
sudo cp /opt/skrt/deploy/nginx/skrt.conf /etc/nginx/sites-available/skrt.conf
sudo ln -sf /etc/nginx/sites-available/skrt.conf /etc/nginx/sites-enabled/skrt.conf
sudo nginx -t
sudo systemctl reload nginx
```

## 6. 更新发布

同机部署更新前后端，直接执行：

```bash
cd /opt/skrt
git pull origin main
INSTALL_DEPS=0 BUILD_FRONTEND=1 sudo systemctl restart skrt
```

如果这次只改了后端，不想重建前端：

```bash
cd /opt/skrt
git pull origin main
BUILD_FRONTEND=0 sudo systemctl restart skrt
```

## 7. 常用排查

查看后端日志：

```bash
sudo journalctl -u skrt -n 200 --no-pager
```

查看 nginx 错误日志：

```bash
sudo tail -n 200 /var/log/nginx/error.log
```

检查前端构建产物是否更新：

```bash
ls -lah /opt/skrt/frontend/dist
ls -lah /opt/skrt/frontend/dist/assets
```

如果页面看起来没更新，优先排查：
- 是否真的执行了 `npm --prefix frontend run build`
- `nginx` 的 `root` 是否指向 `/opt/skrt/frontend/dist`
- 浏览器是否被旧的 PWA Service Worker 缓存住了
