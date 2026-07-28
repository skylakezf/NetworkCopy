"""
TLS / 鉴权辅助模块
  - 生成随机 4 位英文数字验证码 (源设备显示, 目标设备输入)
  - 为当前网卡 IP 自动生成自签名 SSL 证书 (SAN = IP), 并缓存复用
  - 提供服务器 / 客户端 SSL 上下文
WinPE 兼容: 仅依赖 cryptography (需打包进 PyInstaller)
"""
import os
import ipaddress
import datetime
import secrets
import string

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import ssl


# 验证码字符集: 去除易混淆字符 (0/O/1/I)
_AUTH_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_auth_code(length: int = 4) -> str:
    """生成随机 4 位英文数字验证码 (排除易混淆字符)"""
    return "".join(secrets.choice(_AUTH_CHARSET) for _ in range(length))


def _certs_dir() -> str:
    """证书缓存目录 (位于本模块旁边 certs/)"""
    base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "certs")
    os.makedirs(d, exist_ok=True)
    return d


def generate_self_signed_cert(ip: str, cert_path: str, key_path: str):
    """为指定 IP 生成自签名证书 (RSA 2048, SHA256, SAN=IP)"""
    # 校验 IP 合法性
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError(f"非法 IP: {ip}")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, ip),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DiskCopyTool"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_obj)]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))


def get_or_create_cert(ip: str) -> tuple:
    """
    获取 (或生成) 指定 IP 的自签名证书
    返回 (cert_path, key_path)
    """
    d = _certs_dir()
    safe_ip = ip.replace(":", "_")
    cert_path = os.path.join(d, f"{safe_ip}.pem")
    key_path = os.path.join(d, f"{safe_ip}.key")
    if not (os.path.isfile(cert_path) and os.path.isfile(key_path)):
        generate_self_signed_cert(ip, cert_path, key_path)
    return cert_path, key_path


def make_server_ssl_context(cert_path: str, key_path: str) -> ssl.SSLContext:
    """构建服务器 SSL 上下文"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1  # 仅 TLS1.2/1.3
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx


def make_client_ssl_context() -> ssl.SSLContext:
    """
    构建客户端 SSL 上下文
    自签名证书无法被系统信任, 故关闭主机名/证书校验。
    传输安全性由随机验证码 (pwd) 保证; SSL 仅提供链路加密, 防止被动窃听。
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
