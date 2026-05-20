# Changelog

## [v1.8.0][v1.8.0] - 2026-05-20

### 验证码识别升级为大模型视觉方案

- 验证码识别从 ONNX 神经网络升级为大模型（LLM）视觉识别方案，使用火山引擎豆包模型解算腾讯点击/滑块验证码
- 浏览器反检测从 undetected-chromedriver 升级为 CloakBrowser（Chromium C++ 源码级反检测）

### 新增传感器

- 新增分时电量传感器（谷/平/峰/尖）
- 新增预付费余额传感器、应交金额传感器
- 支持 Vue 状态直接注入提取数据

## [v1.7.3][v1.7.3] - 2026-03-12

### 新增功能

- 支持 MySQL 数据库存储
- 新增 URL 通知方式
- 登录失败时支持二维码扫码备选方案

## [v1.7.2][v1.7.2] - 2026-03-06

### 修复滑动验证码

- 修改滑动验证码验证逻辑，增加手动模拟

## [v1.7.1][v1.7.1] - 2026-02-04

### 减少无效推送

- 新增"探活"机制，仅在数据变化或 HA 传感器状态异常时才推送，减少 HA 数据库重复写入
- 修复预付费账户余额获取问题
- 优化 republish 逻辑，解决 Windows 下文件锁冲突

## [v1.7.0][v1.7.0] - 2026-01-26

### 本地缓存与自动重发

- 增加本地缓存与自动重发机制，解决 12 小时空白期 HA 重启后数据丢失的问题
- 修复余额获取问题

## [v1.6.9][v1.6.9] - 2026-01-17

### Fixed

- 修复 2026 数据获取问题

## [v1.6.8][v1.6.8] - 2025-07-10

### 更换默认浏览器

- 更换默认浏览器，减小系统空间
- 修复v1.6.7出现不能加载网页的情况

## [v1.6.7][v1.6.7] - 2025-07-06

### 添加随机延迟执行

- 默认时间为07:00,19:00 现在改为这个时间随机加减10分钟，避免同时访问....

## [v1.6.6][v1.6.6] - 2025-03-30

### Fixed

- 修复月数据采集 bug

## [v1.6.5][v1.6.5] - 2025-03-28

### Fixed

- 国网功能恢复，修改部分代码修复之前功能

## [v1.6.4][v1.6.4] - 2025-01-06

### Fixed

- add-on功能优化.

## [v1.6.3][v1.6.3] - 2025-01-05

### Added

- 初步测试了addon功能.

### Fixed

- 修复了一些小bug.

[v1.8.0]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.7.3...v1.8.0
[v1.7.3]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.7.2...v1.7.3
[v1.7.2]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.7.1...v1.7.2
[v1.7.1]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.7.0...v1.7.1
[v1.7.0]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.6.9...v1.7.0
[v1.6.9]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.6.8...v1.6.9
[v1.6.8]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.6.7...v1.6.8
[v1.6.7]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.6.6...v1.6.7
[v1.6.6]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.6.5...v1.6.6
[v1.6.5]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.6.4...v1.6.5
[v1.6.4]: https://github.com/ARC-MX/sgcc_electricity_new/compare/v1.6.3...v1.6.4
[v1.6.3]: https://github.com/ARC-MX/sgcc_electricity_new/releases/tag/v1.6.3
