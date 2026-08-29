"""
@input: 协议层错误响应（错误码 Code 与异常信息）
@output: WechatAPI 自定义异常族（MarshallingError / UnmarshallingError / MMTLSError / PacketError / ParsePacketError / DatabaseError / LoginError / UserLoggedOut / BanProtection）
@position: WechatAPI 共享异常定义（被 Client/Server 通配 `import *` 使用）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

class MarshallingError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class UnmarshallingError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class MMTLSError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class PacketError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class ParsePacketError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class DatabaseError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class LoginError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class UserLoggedOut(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

class BanProtection(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
