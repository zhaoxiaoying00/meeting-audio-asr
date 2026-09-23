from pathlib import Path
import datetime as dt,json,subprocess,math,gc,re,shutil,os
import numpy as np
import soundfile as sf
import sherpa_onnx
import imageio_ffmpeg
import hashlib
from durable_audio import atomic,assemble,pending_sessions

APP=Path(__file__).resolve().parent
BASE=APP.parent
MODELS=APP/'models'

def stamp(t):
    s=max(0,int(t));h,s=divmod(s,3600);m,s=divmod(s,60)
    return f'{h:02}:{m:02}:{s:02}'

def new_session(title):
    now=dt.datetime.now();safe=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',title).strip(' .')[:60] or '会议'
    key=now.strftime('%H-%M-%S-%f')[:12]+'_'+safe
    out=BASE/now.strftime('%Y-%m-%d')/key
    raw=APP/'recordings'/now.strftime('%Y-%m-%d')/key
    out.mkdir(parents=True);raw.mkdir(parents=True)
    atomic(raw/'session.json',json.dumps({'title':safe,'started':now.isoformat(),'output':str(out)},ensure_ascii=False,indent=2))
    return raw,out

def ffmpeg(args):
    r=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-nostdin',*args],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:raise RuntimeError(r.stderr.decode('utf-8',errors='replace')[-1400:])

def register_import(raw,source):
    source=Path(source).resolve()
    if not source.is_file() or source.stat().st_size==0:raise ValueError('请选择非空的音频文件。')
    atomic(Path(raw)/'import.json',json.dumps({'source':str(source),'name':source.name,
        'backup':'source'+source.suffix.lower()},ensure_ascii=False))

def prepare_import(raw,progress):
    info=json.loads((raw/'import.json').read_text(encoding='utf-8-sig'))
    backup_dir=raw/'import';backup_dir.mkdir(exist_ok=True)
    backup=backup_dir/info['backup']
    if not backup.exists():
        source=Path(info['source'])
        if not source.is_file():raise RuntimeError('原音频文件已移动或删除，无法完成导入。请重新选择文件。')
        before=source.stat()
        if shutil.disk_usage(raw).free<before.st_size+512*1024**2:raise RuntimeError('磁盘空间不足，无法导入音频。')
        progress('正在导入音频副本…')
        temp=backup.with_name(backup.name+'.partial');shutil.copyfile(source,temp)
        after=source.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise RuntimeError('原文件仍在写入，请等待录制完成后重试。')
        with temp.open('rb+') as f:os.fsync(f.fileno())
        temp.replace(backup)
    audio=raw/'导入音频.flac'
    if not audio.exists():
        progress('正在读取音频…')
        temp=raw/'导入音频_生成中.flac'
        ffmpeg(['-y','-i',str(backup),'-map','0:a:0','-vn','-ar','16000','-ac','1','-c:a','flac',str(temp)])
        if sf.info(temp).frames==0:raise RuntimeError('文件中没有可转写的音频。')
        with temp.open('rb+') as f:os.fsync(f.fileno())
        temp.replace(audio)
    return audio

def prepare(path,dest,label,playback):
    args=['-y','-i',str(path),'-ar','16000','-ac','1']
    if playback:
        filters='highpass=f=70'
        if label=='本地':filters+=',afftdn=nr=12:nf=-35:tn=1:gs=5'
        filters+=',alimiter=limit=0.89:level=0:latency=1'
        args+=['-af',filters]
    # ASR gets original speech, not the denoised playback signal.
    ffmpeg(args+['-c:a','pcm_s16le',str(dest)])

def intervals(path):
    cfg=sherpa_onnx.VadModelConfig();cfg.sample_rate=16000;cfg.num_threads=1
    cfg.silero_vad.model=str(MODELS/'silero_vad.onnx')
    cfg.silero_vad.threshold=.6;cfg.silero_vad.min_speech_duration=.3
    cfg.silero_vad.min_silence_duration=.5;cfg.silero_vad.max_speech_duration=18
    vad=sherpa_onnx.VoiceActivityDetector(cfg,buffer_size_in_seconds=40)
    found=[]
    def drain():
        while not vad.empty():
            front=vad.front;found.append((int(front.start),int(front.start+len(front.samples))));vad.pop()
    with sf.SoundFile(path) as f:
        total=f.frames
        for block in f.blocks(blocksize=512,dtype='float32'):
            if len(block)<512:block=np.pad(block,(0,512-len(block)))
            vad.accept_waveform(block);drain()
    vad.flush();drain()
    # Small context padding protects quiet consonants; merge only overlapping padding.
    result=[]
    for start,end in found:
        start=max(0,start-2400);end=min(total,end+2400)
        if result and start<=result[-1][1] and end-result[-1][0]<=320000:
            result[-1]=(result[-1][0],end)
        elif end>start:result.append((start,end))
    return result,total

def transcribe_files(files,progress,checkpoint=None):
    cache={}
    if checkpoint and Path(checkpoint).exists():
        try:cache=json.loads(Path(checkpoint).read_text(encoding='utf-8-sig'))
        except (ValueError,OSError):pass
    def save_segment(key,items):
        cache[key]=items
        if checkpoint:atomic(checkpoint,json.dumps(cache,ensure_ascii=False))
    model_dir=MODELS/'paraformer'
    model=sherpa_onnx.OfflineRecognizer.from_paraformer(paraformer=str(model_dir/'model.int8.onnx'),
        tokens=str(model_dir/'tokens.txt'),num_threads=2)
    rows=[]
    try:
        for label,path in files:
            digest=hashlib.sha256()
            with open(path,'rb') as inp:
                for block in iter(lambda:inp.read(1024*1024),b''):digest.update(block)
            identity=label+':'+digest.hexdigest()+':paraformer-v1:'
            speech,total=intervals(path)
            with sf.SoundFile(path) as f:
                for start,end in speech:
                    key=identity+f'{start}:{end}'
                    if key in cache:
                        rows.extend(cache[key]);continue
                    before=len(rows)
                    f.seek(start);a=f.read(end-start,dtype='float32')
                    rms=float(np.sqrt(np.mean(a*a)))
                    if rms<.003:
                        rows.append({'start':round(start/16000,3),'end':round(end/16000,3),
                            'source':label,'text':'【声音过轻，未可靠转写，请回听】'})
                        save_segment(key,rows[before:])
                        continue
                    # Only normalize a VAD-confirmed speech segment, never a whole noise track.
                    gain=min(4.,.04/max(rms,1e-6)) if rms<.04 else 1.
                    stream=model.create_stream();stream.accept_waveform(16000,np.clip(a*gain,-1,1))
                    model.decode_stream(stream);text=stream.result.text.strip()
                    if text:
                        rows.append({'start':round(start/16000,3),'end':round(end/16000,3),'source':label,'text':text})
                    save_segment(key,rows[before:])
                    progress(f'正在转写{label}：{stamp(end/16000)} / {stamp(total/16000)}')
    finally:
        del model;gc.collect()
    return sorted(rows,key=lambda r:(r['start'],r['end'],r['source']))

def mix_and_encode(paths,dest,work):
    mixed=work/'mix.wav'
    handles=[sf.SoundFile(p) for p in paths]
    try:
        with sf.SoundFile(mixed,'w',samplerate=16000,channels=1,subtype='PCM_16') as out:
            while True:
                blocks=[f.read(16000,dtype='float32') for f in handles];n=max(map(len,blocks),default=0)
                if not n:break
                a=np.zeros(n,np.float32)
                for b in blocks:a[:len(b)]+=b
                peak=np.max(abs(a),initial=0)
                if peak>.89:a*=.89/peak
                out.write(a)
    finally:
        for f in handles:f.close()
    wav_dest=dest.with_suffix('.wav')
    wav_tmp=dest.with_name('会议音频_生成中.wav')
    shutil.copy2(mixed,wav_tmp);wav_tmp.replace(wav_dest)
    temp=dest.with_name('会议音频_生成中.mp3')
    ffmpeg(['-y','-i',str(mixed),'-c:a','libmp3lame','-b:a','64k',str(temp)])
    temp.replace(dest)

def process(raw,out,title,progress=lambda s:None):
    raw,out=Path(raw),Path(out);out.mkdir(parents=True,exist_ok=True)
    work=raw/'processing';work.mkdir(exist_ok=True)
    cleaned=(raw/'audio_processing.json').exists()
    final_rows=None
    if cleaned:
        from background_asr import BackgroundASR
        progress('正在完成音频清理和剩余转写…')
        final_rows=BackgroundASR(raw).finish()
    try:timeline=json.loads((raw/'录音时间轴.json').read_text(encoding='utf-8-sig'))
    except Exception:timeline={}
    sources=[]
    if (raw/'import.json').exists():sources.append(('导入音频',prepare_import(raw,progress)))
    for label,names in [('本地',['本地.flac','麦克风.flac']),('远端',['远端.flac','电脑声音.flac'])]:
        path=assemble(raw/('cleaned' if cleaned else 'chunks')/label,work/(label+'_原始.wav'))
        if path is None:path=next((raw/n for n in names if (raw/n).exists()),None)
        if path:
            events=[e for e in timeline.get('switches',[]) if e['source']==label]
            # A crash may precede the latest switch checkpoint. Keep all new chunk tracks.
            if not (raw/'chunks'/label).exists() and events and not any(e['record'] for e in events):continue
            sources.append((label,path))
    if not sources:raise RuntimeError('没有找到音频文件')
    asr_files=[];play_files=[];durations={};notes=[]
    for label,path in sources:
        progress('正在保存音频…')
        play=work/(label+'_回听.wav');asr=work/(label+'_识别.wav')
        prepare(path,play,label,not cleaned)
        if not (raw/'chunks').exists():
            prepare(path,asr,label,False);asr_files.append((label,asr))
        durations[label]=sf.info(play).duration
        play_files.append(play)
    mix_and_encode(play_files,out/'会议音频.mp3',work)
    if len(durations)>1 and max(durations.values())-min(durations.values())>.5:
        notes.append('原始声道长度不一致，部分时间戳可能偏移。缺失录音无法通过转写补回。')
    progress('音频已保存，正在识别语音…')
    if final_rows is not None:rows=final_rows
    elif (raw/'chunks').exists():
        from background_asr import BackgroundASR
        rows=BackgroundASR(raw).finish()
    else:rows=transcribe_files(asr_files,progress,raw/'转写进度.json')
    lines=[title,f'日期：{out.parent.name}','']
    if notes:lines+=notes+['']
    if not rows:lines.append('未检测到可识别语音。请回听音频并检查录音来源。')
    for row in rows:
        lines.append(f'[{stamp(row["start"])}–{stamp(row["end"])}] [{row["source"]}] {row["text"]}')
    atomic(out/'转写.txt','\n'.join(lines)+'\n')
    atomic(raw/'transcript.json',json.dumps(rows,ensure_ascii=False,indent=2))
    atomic(raw/'result.json',json.dumps({'engine':'Paraformer offline int8 + Silero VAD','output':str(out),'notes':notes},ensure_ascii=False))
    # These regenerable WAVs are large. Keep original FLACs for recovery, and MP3, WAV, and transcript.
    for path in work.glob('*.wav'):path.unlink()
    return out
