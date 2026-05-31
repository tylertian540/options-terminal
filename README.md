# Goldman Sachs-Level Options Analysis Terminal

> 机构级期权分析终端 — 毫秒级计算 · 实时数据 · 智能资金追踪

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![C++](https://img.shields.io/badge/C++-17-blue.svg)](core/)
[![Python](https://img.shields.io/badge/Python-3.11+-green.svg)](backend/)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](frontend/)

---

## 仓库结构

```
options-terminal/
├── frontend/                    # React 18 + D3.js 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── charts/          # 可视化：3D IV曲面、Gamma热力图、流量地图
│   │   │   ├── panels/          # 面板：Greeks仪表盘、Smart Money、打分模型
│   │   │   ├── modals/          # 弹窗：报告导出、策略模板、风险预警
│   │   │   └── layout/          # 布局：多屏响应式、拖拽工作区
│   │   ├── hooks/               # 自定义Hook：WebSocket数据流、键盘快捷键
│   │   ├── store/               # Zustand状态管理
│   │   ├── utils/               # 工具函数：数据格式化、颜色映射
│   │   └── types/               # TypeScript类型定义
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
│
├── backend/                     # Python FastAPI 后端
│   ├── api/                     # REST API路由
│   ├── websocket/               # 实时数据推送服务
│   ├── services/                # 业务逻辑：计算调度、缓存、告警
│   ├── middleware/              # 认证、限流、日志
│   └── models/                  # Pydantic数据模型
│
├── core/                        # C++17 核心计算引擎（毫秒级响应）
│   ├── include/                 # 头文件
│   │   ├── options_math.hpp     # Black-Scholes、SABR模型
│   │   ├── greeks.hpp           # 18个Greeks指标
│   │   ├── gamma_squeeze.hpp    # Gamma挤压算法
│   │   └── vol_surface.hpp      # 波动率曲面构建
│   ├── src/                     # 实现文件
│   ├── tests/                   # Google Test单元测试
│   └── bindings/                # pybind11 Python绑定
│
├── data/                        # 数据层
│   ├── collectors/              # OPRA/DTCC数据采集器
│   ├── cleaners/                # 数据清洗规则引擎
│   ├── models/                  # 数据库ORM模型
│   └── cache/                   # Redis缓存策略
│
├── backtest/                    # 回测框架
│   ├── engine/                  # 事件驱动回测引擎
│   ├── strategies/              # 策略模板库
│   ├── analyzers/               # 绩效分析：Sharpe、最大回撤、VaR
│   └── data/                    # 历史数据管理（5年）
│
├── scripts/                     # 运维脚本：部署、数据初始化、监控
├── docs/
│   ├── manual/                  # 分析师操作手册
│   ├── api/                     # API文档
│   └── deployment/              # 部署指南
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── .github/workflows/           # CI/CD：GitHub Actions
├── docker-compose.yml
├── CMakeLists.txt
└── .env.example
```

---

## 快速启动

### 环境要求
- OS: Ubuntu 22.04 / macOS 13+ / Windows WSL2
- C++17 编译器 (GCC 11+ / Clang 14+)
- Python 3.11+
- Node.js 20+
- Redis 7+
- PostgreSQL 15+ (TimescaleDB扩展)
- CMake 3.25+

### 一键启动（Docker）

```bash
git clone https://github.com/YOUR_ORG/options-terminal.git
cd options-terminal
cp .env.example .env          # 填入API密钥
docker-compose up -d
```

访问 http://localhost:3000

### 本地开发启动

```bash
# 1. 编译C++核心引擎
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)

# 2. 安装Python依赖
pip install -r backend/requirements.txt

# 3. 安装前端依赖
cd frontend && npm install

# 4. 启动所有服务
./scripts/dev-start.sh
```

---

## 核心功能模块

| 模块 | 技术栈 | 描述 |
|------|--------|------|
| 实时IV曲面 | C++17 + WebGL | SABR校准，毫秒级更新 |
| 18指标引擎 | C++/Python双版本 | Delta~Ultima完整Greeks链 |
| Smart Money | Python ML | 投机/套保/做市三分类 |
| Gamma挤压 | C++ | 实时GEX计算+触发预警 |
| 概率分布 | Python scipy | 股价路径蒙特卡洛模拟 |
| 回测框架 | Python Vectorbt | 5年数据，自定义参数 |

---

## 许可证

MIT License — 仅供研究与教育用途，不构成投资建议。
