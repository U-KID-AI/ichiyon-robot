"""Real discord.py transport encryption against the patched PyNaCl backend."""
import struct
import unittest
from types import SimpleNamespace
import discord.voice_client
import nacl.secret
import nacl.exceptions
import nacl.bindings


class VoiceCryptoTests(unittest.TestCase):
    def test_aead_transport_roundtrip_and_tamper_rejection(self):
        self.assertTrue(discord.voice_client.has_nacl)
        voice = discord.voice_client.VoiceClient.__new__(discord.voice_client.VoiceClient)
        voice._connection = SimpleNamespace(secret_key=bytes(range(32)))
        voice._incr_nonce = 0
        header = bytes(range(12))
        audio = b'disposable synthetic opus payload'
        packet = voice._encrypt_aead_xchacha20_poly1305_rtpsize(header, audio)
        nonce = packet[-4:] + bytes(20)
        self.assertEqual(nacl.secret.Aead(bytes(voice.secret_key)).decrypt(packet[12:-4], header, nonce), audio)
        self.assertEqual(voice._incr_nonce, 1)
        self.assertEqual(packet[-4:], struct.pack('>I', 0))
        with self.assertRaises(nacl.exceptions.CryptoError):
            nacl.secret.Aead(bytes(voice.secret_key)).decrypt(packet[12:-4], b'changed' + header, nonce)
        second = voice._encrypt_aead_xchacha20_poly1305_rtpsize(header, audio)
        self.assertNotEqual(packet, second)

    def test_invalid_ed25519_points_rejected(self):
        for point in (bytes(32), bytes([1]) + bytes(31), bytes([255]) * 32):
            self.assertFalse(nacl.bindings.crypto_core_ed25519_is_valid_point(point))


if __name__ == '__main__':
    unittest.main()
