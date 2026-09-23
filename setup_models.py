"""Fetch public model artifacts and validate their pinned SHA-256 digests."""
from pathlib import Path
import hashlib,json,urllib.request

ROOT=Path(__file__).resolve().parent

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    manifest=json.loads((ROOT/'models-manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        target=ROOT/'_app'/'models'/item['path']
        if target.exists() and digest(target)==item['sha256']:
            print('Verified:',item['path']);continue
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(target.name+'.partial')
        print('Downloading:',item['path'])
        urllib.request.urlretrieve(item['url'],temp)
        if digest(temp)!=item['sha256']:
            raise RuntimeError('Model checksum mismatch: '+item['path'])
        temp.replace(target)
    print('Models ready.')

if __name__=='__main__':main()
