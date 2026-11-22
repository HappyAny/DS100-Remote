# rle7 固件补丁与升级

**刷写第三方固件有损坏设备的风险，请谨慎使用。**

实机验证使用一台 DS100，硬件 2.4、Bootloader 4.0、LCD 0x9488。其他硬件版本尚未验证。USB 备份覆盖盘内用户文件。完整 MCU Flash、校准备份和 SWD 恢复均未验证。

## 固件校验

GPL-3.0-only 适用于本项目编写的补丁、追加代码和生成工具。发布包包含 119 个原镜像字节的差异、追加代码和填充。官方底包由生成工具另行获取，原权利人的权利继续适用。

| 项目 | 固定值 |
| --- | --- |
| 官方仓库 | `alientek-tools/Digital-Oscilloscope_DS100` |
| 提交 | `46670a349c54e7b9f07a6f7e832abee5dfeb4e13` |
| 官方文件 | `Firmware/atk_dso_v_4_8.bin`，427752 字节 |
| 官方 SHA-256 | `5030073c8ffe006d3727e4c13b80ca4aa89c3898a539ae7d4e4a2bf8bb216776` |
| 本地生成 rle7 | `atk_dso.bin`，457344 字节 |
| rle7 SHA-256 | `23fa1a0fe59254cc1acc3e6591fd0849d432f62136166651be3f5d63496ec13e` |
| 运行标识 | `background-pc-rle7` |

## 本地生成

1. 从 [rle7 固件发布页](https://github.com/HappyAny/DS100-Remote/releases/tag/v0.5.14-rle7) 下载并完整解压 `DS100-Firmware-rle7-Patch-Windows.zip`，保留 `_internal` 和 `rle7`。
2. 运行 `DS100-Prepare-rle7.exe`。程序下载并校验固定的官方 V4.8 文件。
3. 确认输出 `firmware-output/atk_dso.bin` 和表中的 rle7 SHA-256。

也可指定已下载的官方文件。已有输出内容不同时会拒绝覆盖。

```powershell
.\DS100-Prepare-rle7.exe --base 路径\atk_dso_v_4_8.bin
# 从源码运行
python tools/prepare_firmware.py --revision rle7 --base 路径\atk_dso_v_4_8.bin
```

## 实机升级

1. 确保电量充足，备份用户文件并核对硬件版本。
2. 关机后按住 A，再长按 P 进入独立升级界面，然后连接 USB。
3. 将校验后的 `atk_dso.bin` 复制到升级盘，等待写入完成并安全弹出。
4. 拔下 USB，按 M 执行升级，等待设备提示完成。
5. 开机后在 USB 菜单保存 `PC Remote at startup: ON`，再关机开机。
6. 回到波形界面，连接数据线并打开 0.5.14.dev3 上位机。可在连接信息中核对运行标识。

已有有效的 PC Remote 设置会保留，缺失或无效配置按 OFF 处理。菜单中保存 ON 或 OFF 后需重启生效。

早期视频使用 R11 演示。R11 的补丁、校验值和操作说明见[历史发布页](https://github.com/HappyAny/DS100-Remote/releases/tag/v0.5.6-r11)。
