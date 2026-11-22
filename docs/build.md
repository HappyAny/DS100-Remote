# 构建与复现

上位机依赖在 `pyproject.toml` 中声明。Windows 打包使用 PyInstaller；源码与第三方许可证随发布包提供。

```powershell
python -m pip install .
python run_app.py --demo
python tools/build_windows_app.py --name DS100-Remote
```

从低完整性工作区启动带低完整性标签的 EXE，进程也会以低完整性运行，打开 USB 卷的设备句柄可能被 Windows 拒绝。构建时可用普通 Windows 用户账户把输出写到用户应用目录。[Windows 完整性机制](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control)

```powershell
python tools/build_windows_app.py --name DS100-Remote --output-root "$env:LOCALAPPDATA/DS100-Remote-Build"
```

固件生成工具的 Windows 构建使用目录模式，避免解包到系统临时目录：

```powershell
python -m PyInstaller --noconfirm --onedir --name DS100-Prepare-rle7 tools/prepare_rle7.py
Copy-Item -Recurse firmware/releases/rle7 dist/DS100-Prepare-rle7/rle7
```

运行时保留程序旁的 `rle7` 和 `_internal`。生成器读取 `rle7/patch.json` 和同目录中的 `rle7-payload.bin`。发布包随附对应源码与 Python／PyInstaller 许可证。

## rle7 固件源码复现

使用已编译的补丁生成固件只需 Python 标准库。

```powershell
python tools/prepare_firmware.py --revision rle7 --base atk_dso_v_4_8.bin
```

通过固定的官方底包和完整的 rle7 源码也可逐阶段重编。已验证工具链为 Ubuntu 24.04 的 ARM GCC 13.2.1 和 Binutils 2.42。把官方底包放在仓库根目录，在同一目录执行下列命令。

```powershell
wsl.exe -d Ubuntu-24.04 -- python3 tools/rebuild_rle7_payload.py --base atk_dso_v_4_8.bin
```

Linux 可直接运行命令中的 `python3` 部分。脚本验证 `firmware/releases/rle7/source/build.json` 中的源文件、链接产物及每阶段镜像哈希，最后将源码构建结果与发布补丁结果逐字节比较。输出为 `build/rle7-from-source/atk_dso.bin`。构建清单保存固定链接地址、原字节和补丁字节，可检查每次编辑。改动源码后需重新核对受影响的链接地址、修改点和哈希。

## 历史 R11 构建

R11 的 Python 生成入口仍为 `python tools/prepare_firmware.py --base atk_dso_v_4_8.bin`。为 R11 打包时以 `tools/prepare_firmware.py` 为 PyInstaller 入口，并将 `firmware/releases/r11` 复制到 EXE 旁。

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
