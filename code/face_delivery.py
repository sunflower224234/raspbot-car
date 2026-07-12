# -*- coding: UTF-8 -*-
"""人脸识别登录兼容入口（兼容旧接口名）。

识别成功 → 蜂鸣确认
识别失败 → 蜂鸣报警
"""

from face_recognition_only import run_face_login


def run_face_delivery():
    """兼容旧接口名。"""
    return run_face_login()


if __name__ == "__main__":
    run_face_login()
