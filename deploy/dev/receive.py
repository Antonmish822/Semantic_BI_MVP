#!/usr/bin/env python3
"""Receive release images through forced SSH commands using trusted Compose files."""
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Deployment:
    name: str
    config: Path
    state: Path
    env_file: Path
    project: str
    image_prefix: str
    port: int
    network: str
    database_writable: bool = False
    ontology_directory: Path | None = None
    legacy_compose: Path | None = None


def dev_profile():
    return Deployment('dev', CONFIG, STATE, CONFIG / 'dev.env', 'semantic_bi_dev',
                      'semantic-bi-dev', 18088, NETWORK)


def prod_profile():
    project = Path('/home/antonmisurov81/Semantic_BI_MVP')
    return Deployment('prod', Path('/etc/semantic-bi/prod'), Path('/var/lib/semantic-bi-prod'),
                      project / '.env', 'semantic_bi_mvp', 'semantic-bi-prod', 8088,
                      'graphdb_default', database_writable=True,
                      ontology_directory=project / 'ontologies', legacy_compose=project / 'compose.yaml')


def run(*args, check=True, **kwargs):
    return subprocess.run(args, check=check, **kwargs)


def validate_images(archive, sha, profile=None):
    profile = profile or dev_profile()
    expected = {f'{profile.image_prefix}/{target}:{sha}' for target in TARGETS}
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


def load_images(archive, sha, profile):
    tags = [f'{profile.image_prefix}/{target}:{sha}' for target in TARGETS]
    cached = run('docker', 'image', 'inspect', *tags, check=False, capture_output=True)
    if cached.returncode == 0:
        print(f'Using cached {profile.name} images for {sha}', flush=True)
        return
    run('docker', 'load', '--input', str(archive))


def check_database_access(profile=None):
    profile = profile or dev_profile()
    values = {}
    for raw in profile.env_file.read_text().splitlines():
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
        raise ValueError('Deployment requires dedicated GraphDB credentials and a repository')
    with urlopen('http://127.0.0.1:7200/rest/security', timeout=5) as response:
        if json.load(response) is not True:
            raise ValueError('GraphDB security must be enabled before deployment')
    credentials = base64.b64encode(f'{username}:{password}'.encode()).decode()
    headers = {'Authorization': f'Basic {credentials}',
               'Content-Type': 'application/x-www-form-urlencoded'}
    endpoint = f'http://127.0.0.1:7200/repositories/{repository}'
    query = urlencode({'query': 'ASK {}'}).encode()
    with urlopen(Request(endpoint, data=query, headers=headers), timeout=10) as response:
        if response.status != 200:
            raise ValueError('Database read check failed')
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
        if error.code == 403 and not profile.database_writable:
            return
        raise
    if not profile.database_writable:
        raise ValueError('Read-only database user has write access')


def compose(sha, *args, profile=None):
    profile = profile or dev_profile()
    env = {
        'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'HOME': '/root',
        profile.name.upper() + '_RELEASE_SHA': sha,
        'CRM_MONITOR_ENV_FILE': str(profile.env_file),
        'RDF_URL_DOCKER': 'http://graphdb:7200',
    }
    return run('docker', 'compose', '--project-name', profile.project,
               '--env-file', str(profile.env_file),
               '-f', str(profile.config / 'compose.yaml'),
               '-f', str(profile.config / 'compose.override.yaml'), *args, env=env)


def wait_ready(profile=None):
    profile = profile or dev_profile()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urlopen(f'http://127.0.0.1:{profile.port}/ready', timeout=5) as response:
                if response.status == 200 and json.load(response).get('status') == 'ready':
                    return
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(3)
    raise RuntimeError('Readiness check failed for ' + profile.name)


def restore_legacy(profile):
    run('docker', 'compose', '--project-name', profile.project,
        '--env-file', str(profile.env_file), '-f', str(profile.legacy_compose),
        'up', '-d', '--no-build', '--pull', 'never', '--remove-orphans')
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urlopen(f'http://127.0.0.1:{profile.port}/auth/status', timeout=5):
                return
        except HTTPError as error:
            if error.code == 401:
                return
        except (URLError, TimeoutError):
            pass
        time.sleep(3)
    raise RuntimeError('Legacy production rollback did not become available')


def deploy(sha, archive, profile=None):
    profile = profile or dev_profile()
    # Loading is allowed only after validating all tags, preserving other projects' tags.
    validate_images(archive, sha, profile=profile)
    check_database_access(profile=profile)
    compose(sha, 'config', '--quiet', profile=profile)
    load_images(archive, sha, profile)
    inspection = run('docker', 'inspect', GRAPHDB, capture_output=True, text=True)
    networks = json.loads(inspection.stdout)[0]['NetworkSettings']['Networks']
    if profile.network not in networks:
        run('docker', 'network', 'connect', '--alias', 'graphdb', profile.network, GRAPHDB)

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
    mount = f'type=volume,source={profile.project}_ontologies,target=/app/ontologies'
    groups = []
    if profile.ontology_directory:
        mount = f'type=bind,source={profile.ontology_directory},target=/app/ontologies'
        groups = ['--group-add', str(profile.ontology_directory.stat().st_gid)]
    run('docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--memory', '128m', '--pids-limit', '64',
        '--mount', mount, *groups,
        f'{profile.image_prefix}/ontology:{sha}', 'python', '-c', seed)

    current = profile.state / 'current'
    previous = current.read_text().strip() if current.exists() else None
    if previous and not re.fullmatch(r'[0-9a-f]{40}', previous):
        raise ValueError('Invalid current release marker')
    try:
        compose(sha, 'up', '-d', '--no-build', '--remove-orphans', '--wait', '--wait-timeout', '90', profile=profile)
        compose(sha, 'exec', '-T', 'analytics-service', 'python', '-c',
                'from common.rdf_transport import create_rdf_transport; '
                'rows = create_rdf_transport().select("SELECT (1 AS ?health) WHERE {}"); '
                'assert rows and rows[0]["health"]["value"] == "1"', profile=profile)
        wait_ready(profile=profile)
        marker = profile.state / 'current.next'
        with marker.open('w') as output:
            output.write(sha + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(marker, current)
    except Exception:
        if previous:
            compose(previous, 'up', '-d', '--no-build', '--remove-orphans', '--wait', '--wait-timeout', '90', profile=profile)
            wait_ready(profile=profile)
            print(f'Restored {profile.name} release {previous}', flush=True)
        elif profile.legacy_compose:
            restore_legacy(profile)
        else:
            compose(sha, 'stop', profile=profile)
        raise
    print(f'Deployed {profile.name} release {sha}', flush=True)


def main(profile=None):
    profile = profile or dev_profile()
    command = sys.argv[1] if len(sys.argv) == 2 else ''
    match = re.fullmatch(r'(deploy|verify) ([0-9a-f]{40})', command)
    if not match:
        raise ValueError('Only deploy or verify followed by a full commit SHA is allowed')
    mode, sha = match.groups()
    profile.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (profile.state / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix='release-', dir=profile.state) as directory:
            archive = Path(directory) / 'images.tar.gz'
            total = 0
            with archive.open('wb') as target:
                while chunk := sys.stdin.buffer.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError('Image archive is too large')
                    target.write(chunk)
            if mode == 'verify':
                validate_images(archive, sha, profile=profile)
                print(f'Verified {profile.name} delivery {sha}', flush=True)
            else:
                deploy(sha, archive, profile=profile)


if __name__ == '__main__':
    main()
