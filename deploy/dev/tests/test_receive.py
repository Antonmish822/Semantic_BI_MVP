import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('receiver', Path(__file__).parents[1] / 'receive.py')
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)
SHA = 'a' * 40


class ReceiverChecks(unittest.TestCase):
    def archive(self, directory, tags=None, duplicate=False, symbolic=False):
        tags = tags if tags is not None else [f'semantic-bi-dev/{target}:{SHA}' for target in receiver.TARGETS]
        manifest = json.dumps([{'RepoTags': [tag]} for tag in tags]).encode()
        archive = Path(directory) / 'images.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for _ in range(2 if duplicate else 1):
                member = tarfile.TarInfo('manifest.json')
                if symbolic:
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                    output.addfile(member)
                else:
                    member.size = len(manifest)
                    output.addfile(member, io.BytesIO(manifest))
        return archive

    def test_accepts_exact_dev_image_set(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver.validate_images(self.archive(directory), SHA)

    def test_rejects_unexpected_tags_missing_images_and_duplicates(self):
        expected = [f'semantic-bi-dev/{target}:{SHA}' for target in receiver.TARGETS]
        for tags in [expected[:-1], expected + ['production:latest'],
                     expected[:-1] + [expected[0]],
                     expected[:-1] + ['semantic-bi-dev/mcp:' + 'b' * 40]]:
            with self.subTest(tags=tags), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    receiver.validate_images(self.archive(directory, tags), SHA)

    def test_rejects_duplicate_or_symbolic_manifests(self):
        for options in [{'duplicate': True}, {'symbolic': True}]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    receiver.validate_images(self.archive(directory, **options), SHA)

    def test_rejects_shell_commands_and_non_sha_arguments(self):
        for command in ['', 'deploy main', 'deploy ' + SHA + '; id', 'rollback ' + SHA]:
            with self.subTest(command=command), patch.object(receiver.sys, 'argv', ['receive.py', command]):
                with self.assertRaises(ValueError):
                    receiver.main()

    def test_disabled_database_security_stops_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / 'dev.env').write_text('RDF_USERNAME=dev\nRDF_PASSWORD=test\nGRAPHDB_REPOSITORY=bitrix24\n')
            with patch.object(receiver, 'CONFIG', config), \
                    patch.object(receiver, 'validate_images'), \
                    patch.object(receiver, 'urlopen', return_value=io.BytesIO(b'false')), \
                    patch.object(receiver, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'security must be enabled'):
                    receiver.deploy(SHA, Path('unused'))
                run.assert_not_called()

    def test_readonly_check_requires_successful_read_and_forbidden_empty_write(self):
        class QueryResponse(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / 'dev.env').write_text('RDF_USERNAME=dev\nRDF_PASSWORD=test\nGRAPHDB_REPOSITORY=bitrix24\n')
            denied = HTTPError('http://test', 403, 'Forbidden', {}, None)
            responses = [io.BytesIO(b'true'), QueryResponse(b'{}'), denied, denied]
            with patch.object(receiver, 'CONFIG', config), patch.object(receiver, 'urlopen', side_effect=responses):
                receiver.check_database_access()

    def test_successful_empty_update_is_rejected(self):
        class QueryResponse(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / 'dev.env').write_text('RDF_USERNAME=dev\nRDF_PASSWORD=test\nGRAPHDB_REPOSITORY=bitrix24\n')
            denied = HTTPError('http://test', 403, 'Forbidden', {}, None)
            responses = [io.BytesIO(b'true'), QueryResponse(b'{}'), denied, QueryResponse(b'')]
            with patch.object(receiver, 'CONFIG', config), patch.object(receiver, 'urlopen', side_effect=responses):
                with self.assertRaisesRegex(ValueError, 'write access'):
                    receiver.check_database_access()

    def test_anonymous_write_bypass_is_rejected(self):
        class QueryResponse(io.BytesIO):
            status = 200
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / 'dev.env').write_text('RDF_USERNAME=dev\nRDF_PASSWORD=test\nGRAPHDB_REPOSITORY=bitrix24\n')
            responses = [io.BytesIO(b'true'), QueryResponse(b'{}'), QueryResponse(b'')]
            with patch.object(receiver, 'CONFIG', config), patch.object(receiver, 'urlopen', side_effect=responses):
                with self.assertRaisesRegex(ValueError, 'Anonymous'):
                    receiver.check_database_access()

    def deployment(self, directory):
        inspection = SimpleNamespace(stdout=json.dumps([{'NetworkSettings': {'Networks': {receiver.NETWORK: {}}}}]))
        patches = [patch.object(receiver, 'STATE', Path(directory)),
                   patch.object(receiver, 'validate_images'),
                   patch.object(receiver, 'check_database_access'),
                   patch.object(receiver, 'run', return_value=inspection)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_failed_readiness_restores_previous_release_and_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            self.deployment(directory)
            previous = 'b' * 40
            (Path(directory) / 'current').write_text(previous + '\n')
            with patch.object(receiver, 'compose') as compose, \
                    patch.object(receiver, 'wait_ready', side_effect=[RuntimeError('not ready'), None]):
                with self.assertRaisesRegex(RuntimeError, 'not ready'):
                    receiver.deploy(SHA, Path('unused'))
                self.assertTrue(any(call.args[:2] == (previous, 'up') for call in compose.call_args_list))
            self.assertEqual((Path(directory) / 'current').read_text().strip(), previous)

    def test_failed_first_release_stops_dev(self):
        with tempfile.TemporaryDirectory() as directory:
            self.deployment(directory)
            with patch.object(receiver, 'compose') as compose, \
                    patch.object(receiver, 'wait_ready', side_effect=RuntimeError('not ready')):
                with self.assertRaises(RuntimeError):
                    receiver.deploy(SHA, Path('unused'))
                compose.assert_any_call(SHA, 'stop')
            self.assertFalse((Path(directory) / 'current').exists())

    def test_successful_release_records_exact_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            self.deployment(directory)
            with patch.object(receiver, 'compose'), patch.object(receiver, 'wait_ready'):
                receiver.deploy(SHA, Path('unused'))
            self.assertEqual((Path(directory) / 'current').read_text().strip(), SHA)


if __name__ == '__main__':
    unittest.main()
