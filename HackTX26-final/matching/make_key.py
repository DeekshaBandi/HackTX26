"""
Makes a key pair so Python can log into Snowflake without a password.
Run once:  python make_key.py

Creates rsa_key.p8 (private, keep secret, never commit it)
and prints the public key to paste into Snowflake.
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

with open("rsa_key.p8", "wb") as f:
    f.write(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))

pub = key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

# Snowflake wants just the middle part, no header/footer lines
body = "".join(line for line in pub.splitlines() if "-----" not in line)

print("Saved rsa_key.p8\n")
print("Copy everything between the quotes into the Snowflake script:\n")
print(f"'{body}'")