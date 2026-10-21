import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch

import linux_tls


class LinuxTLSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ca = Path(self.temp.name) / 'ca.pem'
        # Use real host roots, never alter trust or global SSL factories in tests.
        source = next((Path(p) for p in linux_tls.CA_FILES if Path(p).is_file()), None)
        if source is None:
            self.skipTest('No system PEM certificate bundle on this test host')
        self.ca.write_bytes(source.read_bytes())

    def test_discovers_host_bundle_when_build_defaults_are_missing(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(linux_tls, 'CA_FILES', (str(self.ca),)):
            context = linux_tls.configure()
            self.assertEqual(os.environ['SSL_CERT_FILE'], str(self.ca))
            self.assertTrue(context.get_ca_certs())
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_explicit_bundle_is_respected(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.ca)}, clear=True), patch.object(linux_tls, 'CA_FILES', ()):
            linux_tls.configure()
            self.assertEqual(os.environ['SSL_CERT_FILE'], str(self.ca))

    def test_invalid_explicit_override_is_not_silently_ignored(self):
        for value in ('', '/does/not/exist'):
            with self.subTest(value=value), patch.dict(os.environ, {'SSL_CERT_FILE': value}, clear=True):
                with self.assertRaisesRegex(ValueError, 'SSL_CERT_FILE'):
                    linux_tls.configure()

    def test_invalid_certificate_contents_fail_closed(self):
        self.ca.write_text('not a certificate')
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.ca)}, clear=True):
            with self.assertRaisesRegex(ValueError, 'TLS verification remains enabled'):
                linux_tls.configure()


if __name__ == '__main__':
    unittest.main()
