#!/usr/bin/env python3
"""
Encryption utilities for IPOR Fusion CLI
"""

import base64
import os
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class EncryptionManager:
    """Manages encryption and decryption of sensitive data"""

    @staticmethod
    def generate_key_from_password(password: str, salt: Optional[bytes] = None) -> tuple[bytes, bytes]:
        """
        Generate encryption key from password using PBKDF2.
        
        Args:
            password: The password to derive the key from
            salt: Optional salt bytes (if None, generates new salt)
            
        Returns:
            Tuple of (key, salt)
        """
        if salt is None:
            salt = os.urandom(16)
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key, salt

    @staticmethod
    def encrypt_private_key(private_key: str, password: str, salt: Optional[bytes] = None) -> str:
        """
        Encrypt a private key using a password.
        
        Args:
            private_key: The private key to encrypt
            password: The password to use for encryption
            salt: Optional salt bytes (if None, generates new salt)
            
        Returns:
            Encrypted private key as base64 string with salt prepended
        """
        key, salt = EncryptionManager.generate_key_from_password(password, salt)
        fernet = Fernet(key)
        
        # Encrypt the private key
        encrypted_data = fernet.encrypt(private_key.encode())
        
        # Combine salt and encrypted data
        combined = salt + encrypted_data
        
        # Return as base64 string
        return base64.urlsafe_b64encode(combined).decode()

    @staticmethod
    def decrypt_private_key(encrypted_private_key: str, password: str) -> str:
        """
        Decrypt a private key using a password.
        
        Args:
            encrypted_private_key: The encrypted private key as base64 string
            password: The password used for encryption
            
        Returns:
            Decrypted private key
            
        Raises:
            ValueError: If decryption fails (wrong password or corrupted data)
        """
        try:
            # Decode the base64 string
            combined = base64.urlsafe_b64decode(encrypted_private_key.encode())
            
            # Extract salt and encrypted data
            salt = combined[:16]
            encrypted_data = combined[16:]
            
            # Generate key from password and salt
            key, _ = EncryptionManager.generate_key_from_password(password, salt)
            fernet = Fernet(key)
            
            # Decrypt the data
            decrypted_data = fernet.decrypt(encrypted_data)
            return decrypted_data.decode()
            
        except Exception as e:
            raise ValueError(f"Failed to decrypt private key: {e}")

    @staticmethod
    def is_encrypted_private_key(private_key: str) -> bool:
        """
        Check if a private key string is encrypted.
        
        Args:
            private_key: The private key string to check
            
        Returns:
            True if the private key appears to be encrypted
        """
        try:
            # Try to decode as base64
            decoded = base64.urlsafe_b64decode(private_key.encode())
            # Check if it has the expected length (salt + encrypted data)
            return len(decoded) > 16
        except Exception:
            return False

    @staticmethod
    def get_encryption_info() -> dict:
        """
        Get information about the encryption method used.
        
        Returns:
            Dictionary with encryption information
        """
        return {
            "method": "Fernet (AES-128-CBC)",
            "key_derivation": "PBKDF2-HMAC-SHA256",
            "iterations": 100000,
            "salt_length": 16,
            "format": "base64url with salt prepended"
        } 