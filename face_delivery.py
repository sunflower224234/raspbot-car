# -*- coding: UTF-8 -*-
"""兼容旧文件名：人脸识别登录入口。

现在本文件用于“人脸识别登录”：
- 识别成功：返回 True
- 识别失败：蜂鸣器报警并发送邮件告警，返回 False
"""

from face_recognition_only import run_face_login, run_face_recognition_only


def run_face_delivery():
    return run_face_login()


if __name__ == "__main__":
    run_face_login()
