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
import socket

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID
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


def generate_self_signed_cert(ip: str, cert_path: str, key_path: str, san_list=None):
    """生成自签名证书 (RSA 2048, SHA256)。

    san_list: x509 GeneralName 列表。为 None 时退化成单 IP SAN (向后兼容)。
    """
    # 校验 IP 合法性 (仅当使用默认 SAN)
    if san_list is None:
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError(f"非法 IP: {ip}")
        san_list = [x509.IPAddress(ip_obj)]

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
            x509.SubjectAlternativeName(san_list),
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


# 固定证书文件名: 无论本机 IP 是多少, 服务器统一使用这一份证书。
# SAN 包含 127.0.0.1 / localhost / 主机名, 使本地浏览器 https://127.0.0.1 不再因
# 主机名不匹配被拒; 跨机传输时接收端用 CERT_NONE 忽略证书错误, 故 SAN 不含对端 IP 也无碍。
FIXED_CERT_BASENAME = "server"


def get_or_create_fixed_cert() -> tuple:
    """获取 (或生成) 固定自签名证书, 返回 (cert_path, key_path)。

    该证书 SAN = [127.0.0.1, localhost, 本机主机名], 与 IP 无关,
    服务器启动一律使用它, 无需为每个 IP 单独生成。
    """
    d = _certs_dir()
    cert_path = os.path.join(d, f"{FIXED_CERT_BASENAME}.pem")
    key_path = os.path.join(d, f"{FIXED_CERT_BASENAME}.key")
    if not (os.path.isfile(cert_path) and os.path.isfile(key_path)):
        sans = [
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.DNSName("localhost"),
            x509.DNSName(socket.gethostname() or "localhost"),
        ]
        generate_self_signed_cert("DiskCopyTool", cert_path, key_path, san_list=sans)
    return cert_path, key_path


def cert_san_info(cert_path: str) -> str:
    """读取证书 SAN 便于日志诊断; 读取失败返回原因。"""
    try:
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        return ", ".join(str(g) for g in san)
    except Exception as e:
        return f"(无法读取 SAN: {e})"


def get_or_create_cert(ip: str) -> tuple:
    """
    获取 (或生成) 指定 IP 的自签名证书 (旧接口, 仅供兼容)。
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
