# 维聚物联 App 与 I.MX6ULL 板端服务

这是维聚物联安卓 App、配对 HTTPS 接口及 I.MX6ULL 板端服务的源码。开发板使用 i.MX6ULL、Linux 4.1.15 和 Python 3.5；安卓 App 支持 Android 8.0 及以上。

源码快照版本：**v0.1.0**。组件版本分别记录：Android App **1.4.0**；板端移动接口 API **v1**；QDesktop 天气界面补丁为 **2026-11-24**。板子上 OneNET OTA 的 `1.0.1` 是设备软件版本，与本仓库版本分开管理。

## 目录

| 路径 | 内容 |
| --- | --- |
| `android/src`, `android/res` | 安卓设备、监控、告警、双向语音界面与网络客户端 |
| `android/board` | 与 App 配套的板端 HTTPS 接口、配对鉴权和语音 PCM 收发 |
| `android/tests` | 接口边界和开发板联机验证 |
| `board/alerts` | ESP32-S3 活动告警、人体检测程序源码 |
| `board/camera` | USB 摄像头局域网预览服务 |
| `board/voice` | 板载 MIC 离线中文指令与提示音服务 |
| `board/onenet` | OneNET 温度、LED 与设备状态接入，含 paho-mqtt 1.6.1 源码及许可证 |
| `board/ota` | 签名包构建、校验、升级与回滚 |
| `board/vpn` | 开发板 Tailscale 用户态服务脚本 |
| `board/desktop` | QDesktop 全屏首页、宣化天气修复和今日天气摘要补丁 |

## 构建与部署

安卓端需要 JDK 17、Android SDK Platform 36 和 Build Tools 34.0.0。在 `android/` 下运行 `python build.py`。已有安装版若需覆盖安装，必须在本机 `android/private/release.jks` 放入原签名密钥；仓库不包含密钥。新建签名只能安装为新的 App 身份，不能覆盖原版。

`android/prepare_device.py` 在本机 `android/private/` 生成配对 Token 和 TLS 证书。先设置 `WEIJU_DEVICE_HOST` 为自己的开发板地址；默认 `192.168.1.100` 只是示例。使用 OneNET 授权和 OTA 签名工具前设置 `WEIJU_PRODUCT_ID` 为自己的产品 ID。OneNET 设备密钥、产品访问密钥、OTA 签名私钥、Tailscale 身份与模型文件均需在自己的环境中单独配置。不要把这些文件提交到 Git。

OTA 的原联机测试依赖已签名的设备专用测试包，这些包未公开，故没有放入仓库。公开版可运行 `android/tests/test_voice_api.py` 与 `board/voice/test_voice_core.py`；OneNET 测试需要 Linux 的 `fcntl`。

板端 `android/board/install.sh` 安装移动接口；`install-intercom.sh` 用于已有安装版增量更新，带备份和失败回滚。其他板端目录的 `install.sh` 和 `.init` 文件分别对应各服务。`board/desktop` 收录可应用到原厂 QDesktop 源码的补丁，不收录整套原厂资源。安装前应核对硬件连接、服务路径和本机配置；人体检测模型、Vosk 模型和编译产物未包含在此仓库。

人体检测交叉编译时需设置 `WEIJU_SDK_ROOT` 和 `WEIJU_DETECTOR_BUILD_ROOT`，并自行准备 ncnn 源码与检测模型。仓库里的构建脚本保留原有目录布局预期，可按 [检测构建文件](board/alerts/detector/build-arm.sh) 检查输入文件位置。

## 验证状态

2026-11-24：板载 MIC 确认为 WM8960 左输入，已同步修复语音服务和 App 对讲服务的录音路由；用户实测离线语音“开灯”成功。新对讲服务已在板上运行，局域网鉴权、播放和收听流测试通过，手机 Tailscale 与板子连通。等待手机端实际收听人声确认后再标记双向对讲通过。

2026-11-23：安卓 App 1.4.0 已在安卓手机覆盖安装；板端接口的 7 项边界测试通过。局域网联机验证了鉴权、1 秒语音播放、板载麦克风流协议、停止后再次收听及原有状态接口。手机向板子说话已实测有声。板载 MIC 的原始录音在启动后几乎静音，板子传回手机这一方向**尚未验收通过**，需要继续排查收音路径。公网访问依赖手机与开发板均连接同一 Tailscale 网络，8443 端口不应直接暴露到公网。

2026-11-24：QDesktop 首页和天气详情页在 1024×600 开发板上实测，今日天气摘要显示在天气图标与实时温度之间；主界面城市名“张家口市 宣化区”完整留在天气块内。ARM 交叉编译、实际天气加载、触摸进入、服务重启和整板重启验证通过。补丁基线及构建方法见 [桌面补丁说明](board/desktop/README.md)。

公开版仅保留源码、公开证书和独立的原厂桌面补丁。真实配对文件、设备密钥、个人 IP 地址、APK、日志、截图和模型二进制文件未上传。本仓库未指定统一开源许可证；第三方代码及补丁的来源见 [NOTICE](NOTICE.md)。
