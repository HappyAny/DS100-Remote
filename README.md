# DS100 Remote

**Attention：刷写第三方固件有损坏设备的风险，请谨慎使用。**

正点原子 DS100 的 Windows 上位机，以及基于官方 V4.8 的实验固件补丁。当前发布组合为 **rle7 + 客户端 0.5.14.dev1**，已在硬件 2.4、Bootloader 4.0、LCD 0x9488 的一台设备上验证。

[下载 rle7 发布包](https://github.com/HappyAny/DS100-Remote/releases/tag/v0.5.14-rle7) · [R11 历史发布包](https://github.com/HappyAny/DS100-Remote/releases/tag/v0.5.6-r11) · [B站完整教程与实机演示](https://www.bilibili.com/video/BV1tHsW6kEsE/) · [操作说明](docs/usage.md) · [固件准备与升级](docs/firmware-release.md)

[![B站：给正点原子 DS100 做了个开源上位机](docs/images/bilibili-cover.jpg)](https://www.bilibili.com/video/BV1tHsW6kEsE/)

**[给正点原子 DS100 做了个开源上位机](https://www.bilibili.com/video/BV1tHsW6kEsE/)**（约 8 分 07 秒）：下载安装、固件准备、直接 USB 连接，以及菜单、M/E 拨轮组合、快捷连续移动、切换通道和截图的实机演示。[按章节观看](docs/video.md)。

本次 B 站录制使用 **R11 + 0.5.6.dev1**，展示真实 USB 设备响应，保留原速和实际等待。鼠标事件由程序内 Qt 自动化执行，光标与点击圈用于提示操作位置。旁白为 AI 合成，开头与结尾均强调第三方固件刷写风险。

[GitHub 早期教程 MP4](https://github.com/HappyAny/DS100-Remote/releases/download/v0.5.6-r11/DS100-Remote-R11-Usage-1080p.mp4)仍可下载；该旧版视频约 4 分 12 秒，录制使用 0.5.5。

## 功能

- 480 × 320 原屏镜像，刷新跟随硬件。
- P/A/M/E、双拨轮、保持与双击、M/E 拨轮组合。
- 快捷按钮直接调节时基、量程和位置；支持排队与按住连续移动。
- 在示波器的 USB 菜单保存 `PC Remote at startup: ON`，重启后可在波形界面直接连接。首次使用前的默认设置为 OFF。
- 一键保存原始 PNG，查看设备保存的 CSV/BMP；离线演示始终标明测试数据。

原生实时波形采集和绝对运行／停止 API 尚未绑定。P 沿用原机运行／暂停切换规则。

## 下载与运行

发布包与用途

| 文件 | 用途 |
| --- | --- |
| `DS100-Remote-0.5.14-Windows.zip` | 免安装上位机，完整解压并保留 `_internal` |
| `DS100-Firmware-rle7-Patch-Windows.zip` | 固件补丁与 Windows 本地镜像生成工具 |
| `DS100-Remote-0.5.14-rle7-source.zip` | 对应源码、固件补丁与构建脚本 |
| `SHA256SUMS.txt` | 发布文件校验值 |

固件以补丁形式发布。生成工具从固定来源获取官方 V4.8 底包，校验后在本地生成 `atk_dso.bin`。也可手动指定官方文件。

```powershell
python tools/prepare_firmware.py --revision rle7 --base atk_dso_v_4_8.bin
```

生成的 rle7 镜像 SHA-256

```text
23fa1a0fe59254cc1acc3e6591fd0849d432f62136166651be3f5d63496ec13e
```

生成工具不访问或刷写设备。升级步骤和已知风险见[固件说明](docs/firmware-release.md)。

## 从源码运行

Python 3.10 或更新版本：

```powershell
python -m pip install .
python run_app.py
```

离线演示：`python run_app.py --demo`。演示数据不代表实机性能。

Windows EXE 使用本机 PyInstaller 构建：

```powershell
python tools/build_windows_app.py --name DS100-Remote-0.5.14
```

rle7 固件补丁源码位于 `firmware/releases/rle7/source/`。本地生成工具使用已编译的补丁。重编固件补丁需 `arm-none-eabi-gcc`。[构建说明](docs/build.md)

## 实测范围

在 **250 MHz、40 ns/div** 的稳定镜像对照中，平均采集空窗由 **36.59 ms 降至 23.46 ms**，最长空窗由 **40.12 ms 降至 36.54 ms**。固件在 200 MHz、初始同步和重同步时选择原编码。USB 优先使用独立 48 MHz 时钟，独立时钟不可用时回退到 168 MHz USB 兼容档。[验证记录](docs/validation.md)

实机完成 **42 轮有效采集计时**和 **153,600 像素**逐点一致性检查。另一次约 60 秒的日常镜像收到 **2,257 个完整帧**，重同步 0 次。独立漏触发率、绝对时基精度和长期稳定性仍待验证。[实验摘要](evidence/rle7-validation.json)

## 许可证

我们编写的上位机、固件补丁、构建工具及视频工程采用 **GPL-3.0-only**，见 [LICENSE](LICENSE)。

官方固件底包保留原权利人的权利，未被本项目重新许可，也不随发布包分发。字体与依赖分别保留自己的许可证，见 [第三方说明](THIRD_PARTY_NOTICES.md)。本项目与设备厂商无隶属关系。
