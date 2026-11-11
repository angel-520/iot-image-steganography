"""Download the small Vosk model and repackage it for local browser WASM ASR."""
from __future__ import annotations
import argparse
from io import BytesIO
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request
import zipfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=['cn', 'en'], default='cn')
    parser.add_argument('--rebuild', action='store_true', help='Rebuild an existing generated archive')
    args = parser.parse_args()
    models = {'cn': 'vosk-model-small-cn-0.22', 'en': 'vosk-model-small-en-us-0.15'}
    name = models[args.language]
    root = Path(__file__).resolve().parents[1]
    output = root / 'frontend' / 'public' / 'models' / f'vosk-small-{args.language}.tar.gz'
    if output.exists() and not args.rebuild:
        print(f'Model already present: {output}'); return
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f'Downloading {name} from alphacephei.com (Apache-2.0 model)...', flush=True)
    download = BytesIO()
    with urllib.request.urlopen(f'https://alphacephei.com/vosk/models/{name}.zip', timeout=60) as response:
        while block := response.read(1024 * 1024):
            download.write(block)
            if download.tell() > 128 * 1024 * 1024:
                raise ValueError('Model archive exceeds 128 MiB')
    temporary = output.with_suffix('.partial')
    try:
        with zipfile.ZipFile(download) as archive, tarfile.open(temporary, 'w:gz', compresslevel=1) as target:
            total = 0
            directories = set()
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if member.is_dir():
                    continue
                if path.is_absolute() or '..' in path.parts or path.parts[0] != name:
                    raise ValueError('Unsafe model archive entry')
                total += member.file_size
                if total > 256 * 1024 * 1024:
                    raise ValueError('Uncompressed model exceeds 256 MiB')
                data = archive.read(member)
                relative = PurePosixPath('model', *path.parts[1:])
                for parent in reversed(relative.parents):
                    if str(parent) != '.' and parent not in directories:
                        directory = tarfile.TarInfo(str(parent) + '/')
                        directory.type = tarfile.DIRTYPE; directory.mode = 0o755; directory.mtime = int(time.time())
                        target.addfile(directory); directories.add(parent)
                info = tarfile.TarInfo(str(relative))
                info.size = len(data); info.mode = 0o644; info.mtime = int(time.time())
                target.addfile(info, BytesIO(data))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Ready: {output} ({output.stat().st_size / 1048576:.1f} MiB). Run npm run build in frontend.', flush=True)


if __name__ == '__main__':
    main()
