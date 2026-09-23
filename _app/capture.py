from pathlib import Path
import json, queue, threading, time, traceback
import numpy as np
import soundfile as sf
import pyaudiowpatch as pa
from durable_audio import ChunkWriter,atomic

class TimelineWriter:
    def __init__(self, file, origin):
        self.file=file; self.origin=origin; self.written=0; self.gaps=[]
    def write(self,a,packet_start):
        target=max(0,round((packet_start-self.origin)*self.file.samplerate))
        # Ignore sub-buffer callback jitter; preserve significant stalls/muted intervals.
        gap=target-self.written
        if gap>self.file.samplerate*.15:
            self.gaps.append({'at':self.written/self.file.samplerate,'seconds':gap/self.file.samplerate})
            self.pad_to(target)
        elif gap<-self.file.samplerate*.15:
            # A late-delivered old packet must not stretch the audio timeline.
            a=a[min(len(a),-gap):]
        self.file.write(a); self.written+=len(a)
    def pad_to(self,target):
        while self.written<target:
            n=min(self.file.samplerate,target-self.written)
            self.file.write(np.zeros(n,np.float32));self.written+=n

class Recorder:
    def __init__(self, folder, devices, on_audio=None, enabled=None):
        self.folder, self.devices = Path(folder), devices
        self.on_audio = on_audio
        self.enabled = dict(enabled) if enabled is not None else {label:True for label,_ in devices}
        self.events=[]
        self.origin=None
        self.stopped_at=None
        self.state_lock=threading.Lock()
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.levels = {}
        self.paths = []
        self.overflows = 0
        self.clipped = {}
        self.samples = {}
        self.thread = threading.Thread(target=self.run, daemon=True)

    def set_enabled(self,label,value):
        with self.state_lock:
            self.enabled[label]=bool(value)
            if self.origin is not None:
                self.events.append({'seconds':round(time.monotonic()-self.origin,3),'source':label,'record':bool(value)})

    def start(self):
        self.thread.start()
        if not self.ready.wait(20):
            self.stop_event.set()
            raise RuntimeError('音频设备启动超时')
        if self.error:
            raise RuntimeError(self.error)

    def stop(self):
        self.stopped_at=time.monotonic()
        self.stop_event.set()
        self.thread.join(20)
        if self.thread.is_alive():
            raise RuntimeError('设备停止超时，录音仍在保存，请勿关闭窗口')
        if self.error:
            raise RuntimeError(self.error)

    def run(self):
        streams, files = [], {}
        input_streams={}; writers={}; channel_choices={}
        anchors={}
        current_states={}
        pending = queue.Queue(maxsize=4096)
        manager = pa.PyAudio()
        try:
            # WASAPI loopback otherwise omits silent periods, shifting the timeline.
            # A zero-valued shared output stream keeps the selected endpoint clock active.
            for label, idx in self.devices:
                dev = manager.get_device_info_by_index(idx)
                if dev.get('isLoopbackDevice'):
                    output = next(d for d in manager.get_device_info_generator()
                        if d['hostApi']==dev['hostApi'] and d['maxOutputChannels']>0
                        and manager.get_wasapi_loopback_analogue_by_dict(d)['index']==idx)
                    channels = min(2,int(output['maxOutputChannels']))
                    def silence(data, frames, timing, status, channels=channels):
                        return (bytes(frames*channels*2), pa.paContinue)
                    streams.append(manager.open(format=pa.paInt16, channels=channels,
                        rate=int(output['defaultSampleRate']), output=True,
                        output_device_index=output['index'], frames_per_buffer=2048,
                        stream_callback=silence, start=False))
            for label, idx in self.devices:
                dev = manager.get_device_info_by_index(idx)
                channels = min(2, int(dev['maxInputChannels']))
                rate = int(dev['defaultSampleRate'])
                path = self.folder / 'chunks' / label
                files[label] = ChunkWriter(path,rate)
                self.paths.append(path)
                def callback(data, frames, timing, status, label=label, channels=channels, rate=rate):
                    if status:
                        self.overflows += 1
                    try:
                        now=time.monotonic()
                        adc=timing.get('input_buffer_adc_time',0)
                        current=timing.get('current_time',0)
                        # Device timestamp locates the first sample; callback time is a fallback.
                        packet_start=now-(current-adc) if adc>0 and 0<=current-adc<5 else now-frames/rate
                        active=self.enabled.get(label,False)
                        if not active:return (None,pa.paContinue)
                        pending.put_nowait((label,data,channels,packet_start))
                    except queue.Full:
                        self.error = '磁盘写入过慢，录音缓冲区已满。已保存的音频保留。'
                        self.stop_event.set()
                        return (None, pa.paAbort)
                    return (None, pa.paContinue)
                stream=manager.open(format=pa.paInt16, channels=channels, rate=rate,
                    input=True, input_device_index=idx, frames_per_buffer=2048,
                    stream_callback=callback, start=False)
                input_streams[label]=stream
                streams.append(stream)
            self.origin=time.monotonic()
            for label,file in files.items():writers[label]=TimelineWriter(file,self.origin)
            for stream in streams:
                if stream not in input_streams.values():stream.start_stream()
            for label,stream in input_streams.items():
                active=self.enabled.get(label,False)
                if active:stream.start_stream()
                current_states[label]=active
                self.events.append({'seconds':0,'source':label,'record':active})
            self.ready.set()
            def write_packet(packet):
                label,data,channels,packet_start=packet
                values=np.frombuffer(data,dtype=np.int16).reshape(-1,channels).astype(np.float32)/32768
                if label in ('本地','麦克风') and channels>1:
                    if label not in channel_choices:channel_choices[label]=int(np.argmax(np.mean(values*values,axis=0)))
                    audio=values[:,channel_choices[label]]
                else:audio=values.mean(axis=1)
                writers[label].write(audio,packet_start)
                if self.on_audio:self.on_audio(label,audio,files[label].samplerate)
                self.levels[label]=float(np.max(np.abs(audio),initial=0))
                self.clipped[label]=self.clipped.get(label,0)+int(np.count_nonzero(abs(audio)>.999))
                self.samples[label]=self.samples.get(label,0)+len(audio)
            last_flush=time.monotonic()
            def checkpoint():
                with self.state_lock:events=list(self.events)
                atomic(self.folder/'录音时间轴.json',json.dumps({
                    'duration':round(time.monotonic()-self.origin,3),'switches':events,
                    'gaps':{label:w.gaps for label,w in writers.items()},
                    'microphone_channel':channel_choices},ensure_ascii=False))
            checkpoint()
            while not self.stop_event.is_set():
                for label,stream in input_streams.items():
                    active=self.enabled.get(label,False)
                    if active!=current_states[label]:
                        if active:stream.start_stream()
                        else:stream.stop_stream();self.levels[label]=0
                        current_states[label]=active
                    if active and not stream.is_active():raise RuntimeError(label+'设备已断开，请检查连接。')
                try:write_packet(pending.get(timeout=.1))
                except queue.Empty:pass
                if time.monotonic()-last_flush>3:
                    for label,file in files.items():
                        if not current_states[label]:writers[label].pad_to(round((time.monotonic()-self.origin)*file.samplerate))
                        file.flush()
                    last_flush=time.monotonic()
                    checkpoint()
            stop_time=self.stopped_at or time.monotonic()
            for stream in streams:
                if stream.is_active():stream.stop_stream()
            while not pending.empty():write_packet(pending.get_nowait())
            for label,file in files.items():writers[label].pad_to(round((stop_time-self.origin)*file.samplerate))
            timeline={'duration':round(stop_time-self.origin,3),'switches':self.events,
                'gaps':{label:w.gaps for label,w in writers.items()},'microphone_channel':channel_choices}
            atomic(self.folder/'录音时间轴.json',json.dumps(timeline,ensure_ascii=False,indent=2))
        except Exception:
            self.error = traceback.format_exc()
        finally:
            for stream in streams:
                try:
                    stream.close()
                except Exception:
                    pass
            for f in files.values():
                try:f.close()
                except Exception:self.error=traceback.format_exc()
            manager.terminate()
            self.ready.set()
