# 小程序端

原生微信小程序，无第三方框架、无 npm 依赖。

## 打开方式

1. 微信开发者工具 → 导入项目
2. 目录选 `C:\RAG\rag-vehicle-system\miniprogram`（**不是**上级目录）
3. AppID 已写在 `project.config.json`：`wxc2a7f19ddd07ea12`

## 两种运行模式（由 `config.js` 自动判断）

| 条件 | 模式 | 说明 |
|---|---|---|
| `CLOUD_ENV` 和 `SERVICE_NAME` 都填了 | `wx.cloud.callContainer` | 云托管私有链路，正式环境 |
| 任一为空（当前状态） | `wx.request` → `BASE_URL` | 本地调试，默认 `http://127.0.0.1:8000` |

本地调试必须做一件事：开发者工具右上角「详情 → 本地设置」勾选
**“不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书”**，
否则 `http://127.0.0.1` 会被直接拦掉。

## 部署到云托管后要改的两行

`config.js` 顶部：

```js
const CLOUD_ENV = 'prod-xxxxxxxx';   // 云托管环境 ID
const SERVICE_NAME = 'rag-backend';  // 云托管服务名
```

改完重新编译即切换到私有链路，**页面代码一行都不用动**（切换成本被收敛在 `utils/request.js`）。

## 文件职责

```
app.js               启动时按配置初始化 wx.cloud
app.json             页面注册、导航栏配色
config.js            ★ 唯一需要改的配置（环境/服务名/文案/示例问题）
utils/request.js     请求层 + blocks→纯文本（复制用）
pages/index/         问答主页：输入 → blocks 渲染 → 来源 → AI 标识
pages/about/         免责声明 / 内容来源 / AI 说明 / 反馈（上架必需）
assets/              头像（144 与 512 两版）
```

## 已知边界

- `textarea` 是原生组件，`position: fixed` 场景会被盖住；本页用 flex 布局，不走 fixed。
- 表格用 `scroll-view scroll-x` 横向滚动，列宽 `min-width: 150rpx`，窄屏不会挤压文字。
- 回答超 12 秒时后端主动降级并回 `degraded: "timeout"`，页面显示黄色提示条。
