"""Select the host's trust store for the relocatable Linux installer only."""
import os
from pathlib import Path
import ssl
import urllib.request

# Frozen Python/OpenSSL can retain build-machine certificate paths. Use the
# distribution-managed stores, including CachyOS/Arch, instead of shipping roots.
CA_FILES = (
    '/etc/ssl/cert.pem',
    '/etc/ssl/certs/ca-certificates.crt',
    '/etc/pki/tls/certs/ca-bundle.crt',
    '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem',
    '/etc/ssl/ca-bundle.pem',
)


def configure():
    """Configure urllib's default verified context, respecting explicit overrides.

    Called only in the installer/download worker. Never configure game launches,
    alter the system trust store, or retry using an unverified TLS context.
    """
    explicit = any(key in os.environ for key in ('SSL_CERT_FILE', 'SSL_CERT_DIR'))
    if explicit:
        for key, directory in (('SSL_CERT_FILE', False), ('SSL_CERT_DIR', True)):
            if key not in os.environ:
                continue
            value = os.environ[key]
            paths = value.split(os.pathsep) if directory else [value]
            if not value or any(not p or not (Path(p).is_dir() if directory else Path(p).is_file()) for p in paths):
                raise ValueError(f'{key} does not identify an existing certificate {"directory" if directory else "file"}. '
                                 'Correct this override before retrying setup.')
    else:
        candidate = next((p for p in CA_FILES if Path(p).is_file()), None)
        if candidate:
            os.environ['SSL_CERT_FILE'] = candidate
    try:
        context = ssl.create_default_context()
        # OpenSSL's default-store loader can silently ignore a malformed file.
        # Explicitly parse the selected PEM so an invalid override is actionable.
        if os.environ.get('SSL_CERT_FILE'):
            context.load_verify_locations(cafile=os.environ['SSL_CERT_FILE'])
    except (OSError, ssl.SSLError) as error:
        raise ValueError('Cannot load the selected system CA certificates. Check SSL_CERT_FILE/SSL_CERT_DIR '
                         'and your distribution\'s ca-certificates package. TLS verification remains enabled.') from error
    defaults = ssl.get_default_verify_paths()
    if not context.get_ca_certs() and not defaults.capath:
        raise ValueError('No system CA certificates found. Install or repair your distribution\'s '
                         'ca-certificates package, or set SSL_CERT_FILE to its PEM bundle.')
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ValueError('Secure TLS verification is required.')
    selected = os.environ.get('SSL_CERT_FILE') or defaults.cafile or '(certificate directory)'
    print('HTTPS certificate verification: system trust store ' + selected, flush=True)
    return context


def check(urls):
    configure()
    for url in urls:
        if not url.startswith('https://'):
            raise ValueError('The TLS diagnostic accepts HTTPS URLs only.')
        request = urllib.request.Request(url, headers={'User-Agent': 'D2R-PCVR-installer/0.1'})
        # Same urllib default context as install.download_verified. Read only a
        # small prefix: this diagnostic does not install or cache dependencies.
        with urllib.request.urlopen(request, timeout=30) as response:
            if not response.geturl().startswith('https://'):
                raise ValueError('Dependency redirected outside HTTPS.')
            response.read(1024)
        print('Verified HTTPS: ' + url, flush=True)
