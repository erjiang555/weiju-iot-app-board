# QDesktop 天气界面补丁

`patches/0001-weiju-home-weather-20261124.patch` 包含 9 个代码文件的差异：全屏首页、宣化区天气数据修复、今日天气摘要及主界面城市名左移。补丁基线为本地 `QDesktop-voice-source.tar.gz` 归档中的 `QDesktop/` 源码（2026-11-22 语音页面版本）。该原厂源码归档及图片、音乐没有上传。

在包含 `QDesktop/` 目录的源码根目录执行：

```sh
git apply --check /path/to/0001-weiju-home-weather-20261124.patch
git apply /path/to/0001-weiju-home-weather-20261124.patch
```

补丁经过干净基线的 `git apply --check --whitespace=error` 验证。天气解析测试源码位于 `tests/`；把该目录放到与 `src/QDesktop/` 同级的构建目录后，可用原厂 ARM SDK 和 Qt qmake 编译。测试样例见 `tests/xuanhua-sample.json`。

2026-11-24 的开发板实测使用 1024×600 屏，已从线上天气服务显示“张家口市 宣化区”的当前温度、今日天气、最高最低温、风况和五日预报。对应板端 `/opt/QDesktop` SHA-256：`406e7ce341e060a4199727751efc5c2b457c7237beaac219cf3343e48fa5bc98`。此哈希是特定构建环境的校验值，不是跨平台版本号。
