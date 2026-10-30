"""Read finalized recording chunks only; commit immutable offline ASR segments."""
from pathlib import Path
import json, math, threading, traceback
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
import audio_core as core

class BackgroundASR:
    def __init__(self, raw):
        self.raw=Path(raw);self.path=self.raw/'后台转写.json'
        self.state={'version':1,'offsets':{},'rows':[],'final':False}
        if self.path.exists():self.state=json.loads(self.path.read_text(encoding='utf-8-sig'))
        self.model=None;self.metadata={};self.error=None
        self.stop_event=threading.Event();self.thread=None
        self.work=self.raw/'background';self.work.mkdir(exist_ok=True)
        self.cleaner=None
        if (self.raw/'audio_processing.json').exists():
            from audio_cleanup import AudioCleanup
            self.cleaner=AudioCleanup(raw)
        self.input_root=self.raw/('cleaned' if self.cleaner else 'chunks')

    def save(self):core.atomic(self.path,json.dumps(self.state,ensure_ascii=False))

    def read(self,label,offset):
        chunks=sorted((self.input_root/label).glob('*.flac'))
        if self.cleaner:chunks=chunks[:self.cleaner.state['index']]
        if not chunks:return None,0
        total=0;parts=[];rate=None;needed=None
        for index,path in enumerate(chunks):
            if path.stem!=f'{index:08d}':raise RuntimeError('录音片段缺失，无法继续转写')
            if path not in self.metadata:self.metadata[path]=sf.info(path)
            info=self.metadata[path]
            if rate is None:rate=info.samplerate;needed=32*rate
            if info.samplerate!=rate:raise RuntimeError('录音采样率变化')
            if total+info.frames>offset and needed:
                start=max(0,offset-total);count=min(info.frames-start,needed)
                with sf.SoundFile(path) as f:f.seek(start);parts.append(f.read(count,dtype='float32'))
                needed-=count
            total+=info.frames
            if not needed:break
        return (np.concatenate(parts) if parts else np.zeros(0,np.float32)),rate

    def decode(self,a):
        rms=float(np.sqrt(np.mean(a*a)))
        if rms<.003:return '【声音过轻，未可靠转写，请回听】'
        if self.model is None:
            self.model=core.sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=str(core.MODELS/'paraformer'/'model.int8.onnx'),
                tokens=str(core.MODELS/'paraformer'/'tokens.txt'),num_threads=2)
        gain=min(4.,.04/max(rms,1e-6)) if rms<.04 else 1.
        def decode_piece(piece):
            stream=self.model.create_stream()
            stream.accept_waveform(16000,np.clip(piece*gain,-1,1))
            self.model.decode_stream(stream)
            return stream.result.text.strip()
        try:
            return decode_piece(a)
        except ValueError as exc:
            # sherpa-onnx can reject an unusually long/noisy VAD span with
            # "string too long". Decode bounded pieces so one bad span does
            # not abort recovery for the whole meeting.
            if 'string too long' not in str(exc).lower():
                raise
            pieces=[]
            step=16000*2
            for start in range(0,len(a),step):
                piece=a[start:start+step]
                try:
                    text=decode_piece(piece)
                except ValueError:
                    text='【片段过长或异常，未可靠转写】'
                if text:pieces.append(text)
            return ' '.join(pieces)

    def step(self,final=False):
        if self.state['final']:return False
        if self.cleaner:self.cleaner.run(final)
        progressed=False
        for folder in sorted(self.input_root.glob('*')):
            if not folder.is_dir():continue
            label=folder.name;base=self.state['offsets'].get(label,0)
            audio,rate=self.read(label,base)
            if audio is None or not len(audio):continue
            if not final and len(audio)<32*rate:continue
            divisor=math.gcd(rate,16000)
            audio16=resample_poly(audio,16000//divisor,rate//divisor) if rate!=16000 else audio
            temp=self.work/(label+'.wav');sf.write(temp,audio16,16000,subtype='PCM_16')
            speech,total=core.intervals(temp)
            tail=final and len(audio)<32*rate
            if tail:cut=total;selected=speech
            else:
                # The flushed final VAD segment may be half a sentence: retain it.
                selected=[(s,e) for s,e in speech if e<=total-16000]
                cut=selected[-1][1] if selected else (max(0,speech[0][0]-2400) if speech else total-16000)
            if cut<=0:continue
            for start,end in selected:
                text=self.decode(audio16[start:end])
                if text:self.state['rows'].append({'start':round(base/rate+start/16000,3),
                    'end':round(base/rate+end/16000,3),'source':label,'text':text})
                self.state['offsets'][label]=base+min(len(audio),round(end*rate/16000))
                self.save()
            self.state['offsets'][label]=base+(len(audio) if tail else min(len(audio),round(cut*rate/16000)))
            self.save();progressed=True
        return progressed

    def start(self):
        def run():
            try:
                while not self.stop_event.is_set():
                    if not self.step():self.stop_event.wait(2)
            except Exception:
                self.error=traceback.format_exc();core.atomic(self.raw/'后台转写错误.txt',self.error)
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()

    def stop_background(self):
        self.stop_event.set()
        if self.thread:self.thread.join()

    def finish(self):
        self.stop_background()
        # Retry a background failure from the last durable offset.
        while self.step(final=True):pass
        self.state['final']=True;self.save();self.model=None
        return sorted(self.state['rows'],key=lambda r:(r['start'],r['end'],r['source']))
