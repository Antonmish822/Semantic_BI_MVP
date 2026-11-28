#!/usr/bin/env python3
"""Receive only dev images through a forced SSH command using trusted Compose files."""
import fcntl
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

CONFIG = Path('/etc/semantic-bi/dev')
STATE = Path('/var/lib/semantic-bi-dev')
TARGETS = ('gateway', 'auth', 'analytics', 'ontology', 'agent', 'mcp')
GRAPHDB = 'semantic-bi-graphdb-graphdb-1'
NETWORK = 'semantic_bi_dev_rdf'
MAX_ARCHIVE_BYTES = 2 * 1024**3


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def validate_images(archive, sha):
    expected = {f'semantic-bi-dev/{target}:{sha}' for target in TARGETS}
    manifests = []
    with tarfile.open(archive, 'r|gz') as images:
        for member in images:
            if member.name == 'manifest.json':
                if not member.isfile() or member.size > 128 * 1024:
                    raise ValueError('Invalid image manifest')
                manifests.append(json.load(images.extractfile(member)))
    if len(manifests) != 1 or len(manifests[0]) != len(TARGETS):
        raise ValueError('Expected exactly six application images')
    tags = [tag for item in manifests[0] for tag in item.get('RepoTags', [])]
    if len(tags) != len(expected) or set(tags) != expected:
        raise ValueError('Archive contains unexpected image tags')


def check_database_access():
    values = {}
    for raw in (CONFIG / 'dev.env').read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if key in {'RDF_USERNAME', 'RDF_PASSWORD', 'GRAPHDB_REPOSITORY'}:
                parts = shlex.split(value)
                values[key] = parts[0] if parts else ''
    username = values.get('RDF_USERNAME', '')
    password = values.get('RDF_PASSWORD', '')
    repository = values.get('GRAPHDB_REPOSITORY', '')
    if not username or not password or not re.fullmatch(r'[a-zA-Z0-9_-]+', repository):
        raise ValueError('Dev requires dedicated GraphDB credentials and a repository')
    with urlopen('http://127.0.0.1:7200/rest/security', timeout=5) as response:
        if json.load(response) is not True:
            raise ValueError('GraphDB security must be enabled before dev deployment')
    credentials = base64.b64encode(f'{username}:{password}'.encode()).decode()
    headers = {'Authorization': f'Basic {credentials}',
               'Content-Type': 'application/x-www-form-urlencoded'}
    endpoint = f'http://127.0.0.1:7200/repositories/{repository}'
    query = urlencode({'query': 'ASK {}'}).encode()
    with urlopen(Request(endpoint, data=query, headers=headers), timeout=10) as response:
        if response.status != 200:
            raise ValueError('Dev database read check failed')
    # An empty INSERT changes no triples even if permissions were misconfigured.
    update = urlencode({'update': 'INSERT DATA {}'}).encode()
    anonymous = Request(endpoint + '/statements', data=update,
                        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urlopen(anonymous, timeout=10):
            pass
    except HTTPError as error:
        if error.code not in {401, 403}:
            raise
    else:
        raise ValueError('Anonymous database access allows writes')
    try:
        with urlopen(Request(endpoint + '/statements', data=update, headers=headers), timeout=10):
            pass
    except HTTPError as error:
        if error.code == 403:
            return
        raise
    raise ValueError('Dev database user has write access')


def compose(sha, *args):
    env = {
        'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'HOME': '/root',
        'DEV_RELEASE_SHA': sha,
        'CRM_MONITOR_ENV_FILE': str(CONFIG / 'dev.env'),
        'RDF_URL_DOCKER': 'http://graphdb:7200',
    }
    return run('docker', 'compose', '--project-name', 'semantic_bi_dev',
               '--env-file', str(CONFIG / 'dev.env'),
               '-f', str(CONFIG / 'compose.yaml'),
               '-f', str(CONFIG / 'compose.override.yaml'), *args, env=env)


def wait_ready():
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urlopen('http://127.0.0.1:18088/ready', timeout=5) as response:
                if response.status == 200 and json.load(response).get('status') == 'ready':
                    return
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(3)
    raise RuntimeError('Dev readiness check failed')


def deploy(sha, archive):
    # Loading is allowed only after validating all tags, preserving other projects' tags.
    validate_images(archive, sha)
    check_database_access()
    compose(sha, 'config', '--quiet')
    run('docker', 'load', '--input', str(archive))
    inspection = run('docker', 'inspect', GRAPHDB, capture_output=True, text=True)
    networks = json.loads(inspection.stdout)[0]['NetworkSettings']['Networks']
    if NETWORK not in networks:
        run('docker', 'network', 'connect', '--alias', 'graphdb', NETWORK, GRAPHDB)

    # Seed a separate named volume using the unprivileged ontology image.
    seed = '''
from pathlib import Path
import shutil
source = Path('/app/builtin-ontologies')
target = Path('/app/ontologies')
for path in source.glob('*.ttl'):
    destination = target / path.name
    if not destination.exists():
        shutil.copyfile(path, destination)
'''
    run('docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--memory', '128m', '--pids-limit', '64',
        '--mount', 'type=volume,source=semantic_bi_dev_ontologies,target=/app/ontologies',
        f'semantic-bi-dev/ontology:{sha}', 'python', '-c', seed)

    current = STATE / 'current'
    previous = current.read_text().strip() if current.exists() else None
    if previous and not re.fullmatch(r'[0-9a-f]{40}', previous):
        raise ValueError('Invalid current release marker')
    try:
        compose(sha, 'up', '-d', '--no-build', '--remove-orphans', '--wait', '--wait-timeout', '90')
        wait_ready()
    except Exception:
        if previous:
            compose(previous, 'up', '-d', '--no-build', '--remove-orphans', '--wait', '--wait-timeout', '90')
            wait_ready()
            print(f'Restored dev release {previous}', flush=True)
        else:
            compose(sha, 'stop')
        raise
    marker = STATE / 'current.next'
    marker.write_text(sha + '\n')
    os.replace(marker, current)
    print(f'Deployed dev release {sha}', flush=True)


def main():
    command = sys.argv[1] if len(sys.argv) == 2 else ''
    match = re.fullmatch(r'deploy ([0-9a-f]{40})', command)
    if not match:
        raise ValueError('Only deploy followed by a full commit SHA is allowed')
    sha = match[1]
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix='release-', dir=STATE) as directory:
            archive = Path(directory) / 'images.tar.gz'
            total = 0
            with archive.open('wb') as target:
                while chunk := sys.stdin.buffer.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError('Image archive is too large')
                    target.write(chunk)
            deploy(sha, archive)


if __name__ == '__main__':
    main()
