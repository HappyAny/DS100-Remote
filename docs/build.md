# 构建与复现

上位机依赖在 `pyproject.toml` 中声明。Windows 打包使用 PyInstaller；源码与第三方许可证随发布包提供。

```powershell
python -m pip install .
python run_app.py --demo
python tools/build_windows_app.py --name DS100-Remote-Compact
```

固件生成工具的 Windows 构建使用目录模式，避免解包到系统临时目录：

```powershell
python -m PyInstaller --noconfirm --onedir --name DS100-Prepare-R11 tools/prepare_firmware.py
```

将 `firmware/releases/r11` 复制为程序同目录中的 `r11`，并保留 `_internal`。发布包随附对应源码与 Python／PyInstaller 许可证。

公开 R11 补丁的生成工具只用 Python 标准库，不需要 ARM 编译器：

```powershell
python tools/prepare_firmware.py --base 路径\atk_dso_v_4_8.bin
```

重新编译原始研究候选需要 `gcc`、`arm-none-eabi-gcc`、`arm-none-eabi-objcopy` 和 `arm-none-eabi-nm`。Windows 研究环境通过 Ubuntu-24.04 WSL 调用 ARM 工具。不要只改编译器版本后忽略文件哈希差异。

```powershell
python -m pip install ".[research]"
python tools/fetch_firmware.py
python tools/decode_firmware.py
python tools/build_candidate.py
python tools/build_candidate.py --background-usb
python tools/build_candidate.py --background-usb --fast-screen
python tools/build_candidate.py --background-usb --profile-draws --controls
python tools/build_draw_candidate.py
python tools/build_follow_candidate.py
python tools/build_bounded_candidate.py
python tools/build_input_ready_candidate.py
python tools/build_input_ack_candidate.py
python tools/run_checks.py
```

构建脚本检查底包与固定原指令。历史候选清单和源快照用于重现旧版本，不表示它们适合当前设备。旧版本的完整 ARM 测试需要相应构建产物；仓库不分发厂商镜像、解码镜像或设备存储备份。

视频工程位于 `media/tutorial-plan.json`、`tools/record_tutorial.py`、`tools/tutorial_audio.ps1` 和 `tools/render_tutorial.py`。录制需要真实 R11 设备、单一 USB 客户端和 FFmpeg；渲染另外需要 OpenCV。旁白使用本机 Windows 合成语音。生成文件不以录制的 30 fps 冒充示波器真实刷新率。
