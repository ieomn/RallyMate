# RallyMate 前端依赖安全验收

2026-11-17｜范围：React Server Components、Sentry、Vinext Node 生产入口和 npm 高危依赖调用边界。

**已修复 critical。** React、React DOM、`react-server-dom-webpack` 从 `19.2.6` 同步固定为 `19.2.9`，只改变这三个包。官方将 `19.2.0–19.2.8` 列为 CVE-2026-126187 的受影响版本；`19.2.9` 修复其 RSC 传输协议问题。[React 官方公告](https://github.com/react/react/security/advisories/GHSA-xqc2-xf4c-x3h3)、[补丁发布说明](https://github.com/react/react/releases/tag/v19.2.9)。

Vinext `1.0.0-beta.2` 和 `@vitejs/plugin-rsc 0.5.26` 保持原版本。实际解析测试确认，RSC 服务端、SSR 和浏览器三个入口都使用项目的 `19.2.9`，没有进入插件内置 `19.2.6` 回退副本；无需嵌套 override。[插件官方版本选择说明](https://raw.githubusercontent.com/vitejs/vite-plugin-react/plugin-rsc%400.5.26/packages/plugin-rsc/README.md)。

| npm audit 包级条目 | Critical | High | Moderate | Low | 合计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 修复前 | 1 | 17 | 15 | 1 | 34 |
| 修复后 | 0 | 17 | 15 | 1 | 33 |

**生产 Node 基线为 `24.22.3`，内置 Undici `7.30.2`。** MIMO 使用全局 `fetch`，因此必须单独验收 Node 内置库，不能以 npm 中 Undici 来自开发链为由排除运行时风险。Docker 构建与运行阶段固定该安全版；本机 Node `24.14.0` 未修改，不作为生产基线。[Node 官方安全发布](https://nodejs.org/en/blog/release/v24.22.3)。

**剩余 High 按实际输入路径分为四组。** 这些包级条目包含向上层包传递的评级；当前生产命令为 `vinext start`，下面所列开发接口不向公网提供。

| 依赖链及当前版本 | 当前生产边界与修复选择 |
| --- | --- |
| Vinext → `image-size 2.0.2`；两项 High 为 `image-size` 及其上层 `vinext` | 唯一调用是构建期读取仓库静态 metadata 图片。生产图片路由没有图片变换器，用户视频与任务图片交给 Python API。若单独消除此告警，可限定 Vinext 使用 `image-size 2.0.3` 补丁，无需升级框架。[图片解析公告](https://github.com/advisories/GHSA-5p2g-fcmc-qvqq) |
| `@cloudflare/vite-plugin 1.37.1` / `wrangler 4.92.0` → `miniflare 4.20260515.0` → `sharp 0.34.5`、`undici 7.24.8`、`ws 8.18.0`；另有 `vite 8.0.13` | 这七项属于开发模拟器、图片变换、WebSocket/HMR、编辑器和开发文件服务。生产没有启动这些服务，也没有 Sharp 优化器；不能公开 Vite/Miniflare/Wrangler 端口。可独立升级 Cloudflare/Vite 工具链并验证兼容；Node 全局 fetch 按上文更新。[Sharp](https://github.com/advisories/GHSA-wq5f-xc86-pv6w)、[Undici](https://github.com/advisories/GHSA-xjj6-f33q-929c)、[WS](https://github.com/advisories/GHSA-96hv-2xvq-fx4p)、[Vite](https://github.com/advisories/GHSA-587h-7wmj-h5pm) |
| Vite/Tailwind → `postcss 8.5.14` → `nanoid 3.3.12`、`source-map-js 1.2.1`；Babel/Webpack → `browserslist 4.28.2` | 四项只处理仓库 CSS、source map 和浏览器目标配置。生产不接收自定义 CSS、source map 或 Browserslist 输入；可定向更新兼容补丁并做构建回归。[PostCSS](https://github.com/advisories/GHSA-r28c-9q8g-f849)、[source-map-js](https://github.com/advisories/GHSA-68fv-2mgg-jv7q) |
| ESLint/TypeScript ESLint → `brace-expansion 1.1.14/5.0.6`、`js-yaml 4.1.1`、`zod 4.4.3`；RSC 的 Webpack peer → schema-utils/Ajv → `fast-uri 3.1.2` | 四项处理 lint 与构建配置。生产不执行 lint，也不使用 Webpack 校验用户配置；业务外发地址使用平台 URL 和现有域名校验。可逐包更新兼容版本。Sentry 自身的 `brace-expansion 5.0.13` 不在告警范围内。[YAML](https://github.com/advisories/GHSA-2883-xcg3-v3hh)、[Zod](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)、[URI](https://github.com/advisories/GHSA-fph4-wmhf-6fwf) |

新增 Sentry 两端 SDK 固定 `11.13.0`，不引入其他 React/RSC 副本；Node 关闭运行时模块注入。事件保留 UUID、安全代码栈和规范路由，去除异常原文、凭据、正文与文件内容，使用假传输验收。当前应用也没有 `ImageResponse/next/og` 路由。增加动态图片、用户代码/配置输入或公开开发服务时，应连同上述依赖重新验收。

**验证：** 补丁后 18 项专项测试、TypeScript、相关 ESLint、干净生产构建全部通过；RSC 解析与 Sentry Chromium 测试实际执行。新构建产物只有 `19.2.9` 标记。临时生产服务 `/welcome`、`/login` 均返回 200，登录完成 hydration，浏览器无异常；临时服务已关闭，原 `3017` 开发服务未受影响。全量测试与 Linux 成品容器由阶段 5 联合验收。

审计快照：`scoring-demo-web/.codex_tmp/sentry-npm-audit.json`、`rsc-security-audit-after.json`；干净构建日志：同目录 `rsc-security-clean-build.log`。诊断文件不进入生产构建上下文。
