# 参与开发

本项目依赖 Windows API。请在 Windows、Python 3.10+ 下开发，推荐使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test_manage.ps1
```

自动测试使用临时配置和测试凭据，不应创建真实计划任务或提交真实校园网认证。页面、代理兼容性及开机未登录行为需要另外实测，并在提交说明中注明测试范围。

提交修改时，请说明触发问题的场景、修改后的行为和验证结果。认证行为或安装步骤变化时，同时更新 README 和 CHANGELOG。

报告问题时，请提供 Windows、Python、Edge 版本，代理客户端模式，复现步骤和脱敏的日志。不要上传校园网账号密码、Windows 密码、`credentials.bin`、本机配置、浏览器配置目录或完整认证请求。`.venv` 和缓存只保留在本机。
