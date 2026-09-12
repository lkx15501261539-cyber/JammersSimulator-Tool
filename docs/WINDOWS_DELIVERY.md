# Windows 统一交付包

使用新交付包 `JammersLab_Q3v2_Q4_Windows.zip`。过去下载的旧包不会自动更新。
原始 `Baseline_v2.0_七点六边形.zip` 仅是模型交付，模拟器窗口和启动脚本在这份
统一包中。仓库里的相同启动文件也可直接使用，见 `MODEL_LAUNCHERS.md`。

完整解压后，在 `JammersLab_Q3v2_Q4_Windows` 文件夹中双击 `启动界面.bat`，
默认进入 Q3 v2.0；`start_q3_v2.bat` 打开相同模型，`start_q4.bat` 打开 Q4。
首次请安装 Python 3.12（64 位）并联网。这是可运行的 Python 源码交付，
不是免安装 EXE。启动文件必须与 `enhanced/`、`models/` 等目录放在一起。

Q3 v2 原 ZIP 和 Q4 源码始终内置。旧 v1 ZIP 和已经完成的 Q3 v2 回放为打包时
可选项；有回放时通过窗口打开 `examples/q3-v2-demo` 目录即可播放，无需重新计算。

## 重新生成

在模拟器仓库根目录执行（Python 3.10+ 标准库即可打包）：

```sh
python tools/build_delivery.py --output ../JammersLab_Q3v2_Q4_Windows.zip
```

也可加入原始 v1 模型包和已经完成的 Q3 v2 场景：

```sh
python tools/build_delivery.py --output ../JammersLab_Q3v2_Q4_Windows.zip --v1-archive "PATH/TO/Baseline_v1.0_两模型完整交付.zip" --demo-run runs/q3-v2-clustered-47
```

输出文件已存在时会报错，避免覆盖已有交付物；请改用新文件名。工具保留模型 ZIP
原始字节，先检查模型内部的哈希清单。纳入 Git 跟踪的源码、文档、资源、测试和
明确列出的交付工具文件；不纳入 `.git`、`.venv`、`.cache`、`__pycache__`、
`runs` 或临时产物。可选回放仅包含播放器需要的五个文件及时间核对报告，
不带工作日志、原模型执行目录或开发机环境。

ZIP 内的 `DELIVERY_MANIFEST.json` 记录全部交付文件的字节数和 SHA-256；工具在
写完后重新读取整个 ZIP 验证这些值，最后输出 ZIP 自身的 SHA-256。固定文件顺序、
时间戳及权限，使相同输入在相同 Python/zlib 环境下可重复得到相同 ZIP。
这是完整性校验，不是数字签名。

解压后的完整交付包也可运行同一命令再次打包。没有 `.git` 时，工具采用并核对
交付清单；原文件被改动时会报错，需要回到 Git 源码仓库重新生成交付清单。
