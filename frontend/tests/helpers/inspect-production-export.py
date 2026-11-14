"""Inspeção do ZIP baixado pelo E2E; somente arquivos temporários e ffprobe local."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile


def inspect(archive, ffprobe):
    with ZipFile(archive) as package:
        names = package.namelist()
        assert len(names) == len(set(names)), 'Entradas duplicadas no ZIP'
        for name in names:
            assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
            assert '\\' not in name and ':' not in name, 'Caminho inseguro no ZIP'
        expected = {'video.mp4', 'subtitles.srt', 'script.json', 'script.txt', 'quality-report.json',
                    'licenses.json', 'review.json', 'metadata.json', 'thumbnail.png', 'thumbnail.jpg'}
        assert set(names) == expected | {'manifest.json'}
        manifest = json.loads(package.read('manifest.json'))
        assert manifest['publishable'] is False
        listed = {item['name']: item for item in manifest['files']}
        assert len(listed) == len(manifest['files']) and set(listed) == expected
        for name, item in listed.items():
            with package.open(name) as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256'], f'Hash divergente: {name}'
            assert package.getinfo(name).file_size == item['size_bytes']
        srt = package.read('subtitles.srt').decode('utf-8')
        assert srt.strip() and ' --> ' in srt
        metadata = json.loads(package.read('metadata.json'))
        assert metadata['publishable'] is False
        assert json.loads(package.read('licenses.json'))['publishable'] is False
        review = json.loads(package.read('review.json'))
        assert review['production_review']['decision'] == 'approve' and review['publishable'] is False
        with tempfile.TemporaryDirectory(prefix='horus-export-probe-') as folder:
            video = Path(folder) / 'video.mp4'
            with package.open('video.mp4') as source, video.open('xb') as target:
                shutil.copyfileobj(source, target)
            probe = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-show_entries', 'stream=codec_type,width,height:format=duration', '-of', 'json', str(video)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10)
            media = json.loads(probe.stdout)
            stream = next(s for s in media['streams'] if s['codec_type'] == 'video')
        return {'manifest': manifest, 'script': json.loads(package.read('script.json')),
                'script_text': package.read('script.txt').decode('utf-8'), 'metadata': metadata,
                'video': {'width': stream['width'], 'height': stream['height'],
                          'duration_seconds': float(media['format']['duration'])}}


if __name__ == '__main__':
    print(json.dumps(inspect(sys.argv[1], sys.argv[2]), ensure_ascii=True))
