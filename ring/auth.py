"""App-level auth: the ring sends a 15-byte nonce, the client answers with
AES-128-ECB(key, PKCS7(nonce)). The key is installed once on a factory-reset ring."""
import enum
import secrets

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class AuthResult(enum.IntEnum):
    SUCCESS = 0x00
    AUTHENTICATION_ERROR = 0x01
    IN_FACTORY_RESET = 0x02
    NOT_ORIGINAL_ONBOARDED_DEVICE = 0x03


def encrypt_nonce(key: bytes, nonce: bytes) -> bytes:
    n = min(len(nonce), 16)
    block = nonce[:n] + bytes([16 - n]) * (16 - n)
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(block) + enc.finalize()


def generate_key() -> bytes:
    return secrets.token_bytes(16)
