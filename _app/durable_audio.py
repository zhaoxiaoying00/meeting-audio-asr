"""Small independently finalized audio chunks; no video capture."""
from pathlib import Path
import os, json
import numpy as np
import soundfile as sf

def atomic(path, text):
    path=Path(path); tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf-8-sig') as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)

class ChunkWriter:
    def __init__(self, folder, samplerate):
        self.folder=Path(folder); self.folder.mkdir(parents=True,exist_ok=True)
        self.samplerate=samplerate; self.parts=[]; self.frames=0; self.index=0
        if any(self.folder.glob('*.flac')):raise RuntimeError('录音片段目录已存在，拒绝覆盖')
    def write(self, audio):
        while len(audio):
            n=min(len(audio),self.samplerate*3-self.frames)
            self.parts.append(np.array(audio[:n],copy=True));self.frames+=n;audio=audio[n:]
            if self.frames==self.samplerate*3:self.flush()
    def flush(self):
        if not self.frames:return
        dest=self.folder/f'{self.index:08d}.flac';tmp=dest.with_suffix('.partial')
        sf.write(tmp,np.concatenate(self.parts),self.samplerate,format='FLAC',subtype='PCM_16')
        with tmp.open('rb+') as f:os.fsync(f.fileno())
        tmp.replace(dest);self.index+=1;self.parts=[];self.frames=0
    def close(self):self.flush()

def assemble(folder, dest):
    chunks=sorted(Path(folder).glob('*.flac'))
    if not chunks:return None
    rate=sf.info(chunks[0]).samplerate
    with sf.SoundFile(dest,'w',samplerate=rate,channels=1,subtype='PCM_16') as out:
        for index,chunk in enumerate(chunks):
            if chunk.stem!=f'{index:08d}':raise RuntimeError('录音片段缺失，停止合并以免时间错位')
            with sf.SoundFile(chunk) as src:
                if src.samplerate!=rate:raise RuntimeError('录音采样率不一致')
                for block in src.blocks(blocksize=rate):out.write(block)
    return Path(dest)

def pending_sessions(app):
    pending=[]
    for meta in sorted((Path(app)/'recordings').glob('*/*/session.json')):
        raw=meta.parent
        try:
            data=json.loads(meta.read_text(encoding='utf-8-sig'))
            if (raw/'result.json').exists():continue
            if not (raw/'import.json').exists() and not any(raw.glob('*.flac')) and not any((raw/'chunks').glob('*/*.flac')):continue
            pending.append((raw,Path(data['output']),data['title']))
        except (ValueError,OSError,KeyError):continue
    return pending
