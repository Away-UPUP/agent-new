# 第三方工具

项目优先使用系统已安装的 `antiword`。本工作区为解决两个真实 DOC 在 LibreOffice 中转换超时的问题，额外包含 Ubuntu focal 提供的 antiword 0.37-16（amd64）解包文件，位于 `tools/antiword/`。通过 `apt-get download antiword` 从 Ubuntu 软件源下载，再由 `dpkg-deb -x` 解包；未修改二进制。

原许可证、版权和上游信息见 `tools/antiword/usr/share/doc/antiword/copyright`；程序使用 GNU GPL。源码可从 Ubuntu 对应源码包取得：

https://launchpad.net/ubuntu/+source/antiword/0.37-16

其他系统可以通过发行版安装 antiword；若没有可运行的 antiword，则使用 LibreOffice 转 DOCX。Python 依赖列于 `requirements.txt`。最终包不包含模型权重、API 密钥、训练输入或标签。
