# Windows 启动与模型验证

本次在 GitHub 托管 Windows x64 环境、Python 3.12 上验证，使用同一份交付 BAT 和模型原代码，工作目录包含中文和空格。

- 验证提交：`fa73bc857afc53938471aff9116abb93335f2182`。
- [完整测试运行与日志](https://github.com/lkx15501261539-cyber/JammersSimulator-Tool/actions/runs/34689209732)。
- `start_q3_v2.bat`、`start_q4.bat`、`启动界面.bat` 的环境检查、模型校验及正常 GUI 启动均通过。
- 正常启动时没有注入测试启动分支或使用 offscreen；检查实际可见原生窗口的标题、所属进程、非空窗口尺寸，并发送正常关闭消息，三个启动器均正常退出。
- 独立的 `--check-only` 会创建 offscreen 窗口；它与上述正常窗口测试分别记录。
- 已有依赖满足版本时不会调用 pip；首次仍需要安装 Python 及联网下载依赖。

36 项模拟器/Q3 v2 测试及 18 项独立 Q4 核心测试全部通过（共 54 项），三个正常窗口入口全部通过。新交付包另通过 15 项本地打包检查。结构化记录见 [windows-validation.json](windows-validation.json)，完整日志与附件在上面的测试运行中。

首次 Windows 检查发现 Git 自动换行转换改变了冻结 Q4 文件的字节；已用仓库文件属性保留原始格式，保留原校验值，并在本次完整复跑中通过。

实际测试系统为 Windows Server 2025 x64（10.0.26100）、Python 3.12.10。测试不代表所有 Windows 版本、ARM 原生 Python 或用户特定安装均已验证；Windows 11 本机虚拟机未作为成功依据。本次没有调用官方比赛模拟器。
