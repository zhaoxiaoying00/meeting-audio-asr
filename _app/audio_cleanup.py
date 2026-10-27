"""Conservative reference echo reduction and noise suppression; retain raw audio."""
from pathlib import Path
import json, math, os
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly,correlate,lfilter
from scipy.linalg import solve_toeplitz
from pywebrtc_audio import AudioProcessor
from durable_audio import atomic

class AudioCleanup:
    def __init__(self, raw):
        self.raw=Path(raw);self.path=self.raw/'音频处理进度.json'
        self.state={'frames':0,'index':0};self.catalog={}
        if self.path.exists():self.state=json.loads(self.path.read_text(encoding='utf-8-sig'))
        # Use the recorded computer audio as the far-end reference for WebRTC
        # AEC3. The previous correlation-only subtraction was fragile when
        # playback and microphone paths had different buffering/latency.
        self.processor=AudioProcessor(sample_rate=16000,echo_cancellation=True,
            noise_suppression=True,high_pass_filter=True,auto_gain_control=False,
            ns_level=0,stream_delay_ms=128)
        self.warmed=False

    def scan(self):
        sources={}
        for folder in sorted((self.raw/'chunks').glob('*')):
            if not folder.is_dir():continue
            parts=[];total=0;rate=None
            for index,path in enumerate(sorted(folder.glob('*.flac'))):
                if path.stem!=f'{index:08d}':raise RuntimeError('原始音频片段缺失')
                if path not in self.catalog:self.catalog[path]=sf.info(path)
                info=self.catalog[path]
                if rate is not None and rate!=info.samplerate:raise RuntimeError('音频采样率发生变化')
                rate=info.samplerate;parts.append((path,total,info.frames));total+=info.frames
            sources[folder.name]=(parts,rate,total)
        return sources

    def read(self,source,start,count):
        if source is None or source[1] is None:return np.zeros(count,np.float32)
        parts,rate,total=source
        # Context protects the resampling filter at successive block boundaries.
        left=max(0,start-800);right=start+count+800
        lo=round(left*rate/16000);hi=min(total,round(right*rate/16000))
        signal=np.zeros(max(0,round(right*rate/16000)-lo),np.float32)
        for path,offset,frames in parts:
            a=max(lo,offset);b=min(hi,offset+frames)
            if b>a:
                with sf.SoundFile(path) as f:
                    f.seek(a-offset);signal[a-lo:b-lo]=f.read(b-a,dtype='float32')
        divisor=math.gcd(rate,16000)
        result=resample_poly(signal,16000//divisor,rate//divisor) if rate!=16000 else signal
        begin=start-left;result=result[begin:begin+count]
        return np.pad(result,(0,max(0,count-len(result))))

    def remove_echo(self,near,far):
        energy=float(np.linalg.norm(near)*np.linalg.norm(far))
        if energy<1e-8:return near
        cross=correlate(near,far,method='fft');middle=len(far)-1
        width=min(8000,len(far)-1);section=cross[middle-width:middle+width+1]
        peak=int(np.argmax(abs(section)));lag=peak-width
        # Do not gate the mic when remote speech exists: subtract only a correlated estimate.
        if abs(section[peak])/energy<.25:return near
        shift=lag-64
        reference=np.zeros_like(far)
        if shift>=0:reference[shift:]=far[:len(far)-shift]
        else:reference[:shift]=far[-shift:]
        length=256
        autocorr=correlate(reference,reference,method='fft')[len(far)-1:len(far)-1+length]
        target=correlate(near,reference,method='fft')[len(far)-1:len(far)-1+length]
        if len(autocorr)<length or autocorr[0]<1e-8:return near
        autocorr[0]*=1.01
        taps=solve_toeplitz(autocorr,target)
        residual=near-lfilter(taps,[1.],reference)
        if not np.all(np.isfinite(residual)) or np.linalg.norm(residual)>np.linalg.norm(near)*1.05:return near
        return residual.astype(np.float32)

    def run(self,final=False):
        sources=self.scan()
        if not sources:return
        lengths=[round(total*16000/rate) if rate else 0 for _,rate,total in sources.values()]
        available=max(lengths) if final else min(lengths)
        if not self.warmed:
            start=max(0,self.state['frames']-16000*8)
            if self.state['frames']>start:
                n=self.state['frames']-start
                near=self.read(sources.get('本地'),start,n)
                far=self.read(sources.get('远端'),start,n)
                self.processor.process(near,far)
            self.warmed=True
        while available-self.state['frames']>=(1 if final else 16000*3+800):
            start=self.state['frames'];count=min(16000*3,available-start)
            local=self.read(sources.get('本地'),start,count)
            remote=self.read(sources.get('远端'),start,count)
            # AEC3 is stateful; feed each chunk exactly once so its adaptive
            # filter sees a continuous near/far stream without replaying an
            # overlap from the previous chunk.
            clean=self.processor.process(local,remote)
            # Disabled microphone spans remain exactly silent.
            for pos in range(0,len(local),160):
                if not np.any(local[pos:pos+160]):clean[pos:pos+160]=0
            for label,audio in [('本地',clean),('远端',remote)]:
                if label not in sources:continue
                folder=self.raw/'cleaned'/label;folder.mkdir(parents=True,exist_ok=True)
                dest=folder/f'{self.state["index"]:08d}.flac';temp=dest.with_suffix('.partial')
                sf.write(temp,audio,16000,format='FLAC',subtype='PCM_16')
                with temp.open('rb+') as f:os.fsync(f.fileno())
                temp.replace(dest)
            self.state={'frames':start+count,'index':self.state['index']+1}
            atomic(self.path,json.dumps(self.state))
