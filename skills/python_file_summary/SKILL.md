---
name: python-file-summary
description: 当用户要求分析或概览 Python 文件时使用这个 Skill。
---

# Python File Summary

读取用户指定的 Python 文件，并按照下面的步骤进行分析：

1. 找出所有 import。
2. 找出所有 class。
3. 找出所有 function。
4. 用一句话总结这个 Python 文件的主要功能。
5. 不允许修改文件。

最终按照下面格式返回：

文件：
Imports：
Classes：
Functions：
功能总结：