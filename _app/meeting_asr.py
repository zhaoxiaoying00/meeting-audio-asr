from pathlib import Path
import os,sys,json,time,threading,queue,datetime as dt,ctypes,traceback,shutil
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
import pyaudiowpatch as pa
import numpy as np
import audio_core as core
from capture import Recorder
from background_asr import BackgroundASR
from meeting_notes import MeetingNotes

APP=Path(__file__).resolve().parent
SETTINGS=APP/'audio_settings.json'
BG='#F4F3EF';CARD='#FFFFFF';INK='#202A27';MUTED='#59665F';GREEN='#22604B'

class App:
    def __init__(self,root):
        self.root=root;self.state='idle';self.recorder=None;self.events=queue.Queue();self.folder=core.BASE
        self.raw=None;self.failed_job=None;self.exiting=False;self.asr=None
        try:self.saved=json.loads(SETTINGS.read_text(encoding='utf-8-sig'))
        except Exception:self.saved={}
        root.title('会议录音');root.geometry('750x680');root.minsize(690,660);root.configure(bg=BG)
        style=ttk.Style();style.theme_use('clam')
        style.configure('TCombobox',font=('Microsoft YaHei UI',10),padding=7,fieldbackground='white')
        style.configure('Level.Horizontal.TProgressbar',background=GREEN,troughcolor='#E7EBE7',borderwidth=0,thickness=6)
        body=tk.Frame(root,bg=BG,padx=26,pady=22);body.pack(fill='both',expand=True)
        top=tk.Frame(body,bg=BG);top.pack(fill='x')
        tk.Label(top,text='会议录音',font=('Microsoft YaHei UI',22,'bold'),bg=BG,fg=INK).pack(side='left')
        self.badge=tk.Label(top,text='●  待机',font=('Microsoft YaHei UI',10),bg=BG,fg=MUTED);self.badge.pack(side='right')
        self.import_btn=tk.Button(top,text='导入音频并转写',command=self.import_audio,relief='flat',bg=CARD,fg=GREEN,
            font=('Microsoft YaHei UI',10),cursor='hand2',padx=12,pady=7)
        self.import_btn.pack(side='right',padx=16)
        self.notes=MeetingNotes(root,APP)
        tk.Button(top,text='简要记录',command=self.notes.show,relief='flat',bg=CARD,fg=GREEN,
            font=('Microsoft YaHei UI',10),cursor='hand2',padx=10,pady=7).pack(side='right')
        tk.Label(body,text='录下声音，结束后得到音频和文字。',font=('Microsoft YaHei UI',11),bg=BG,fg=MUTED).pack(anchor='w',pady=(4,18))
        self.title=tk.StringVar(value='会议')
        name=tk.Frame(body,bg=BG);name.pack(fill='x',pady=(0,12))
        tk.Label(name,text='会议名称',font=('Microsoft YaHei UI',10),bg=BG,fg=MUTED).pack(side='left',padx=(0,12))
        self.name=tk.Entry(name,textvariable=self.title,font=('Microsoft YaHei UI',11),relief='flat',highlightthickness=1,highlightbackground='#D4DBD5',highlightcolor=GREEN)
        self.name.pack(side='left',fill='x',expand=True,ipady=7)
        self.local=tk.BooleanVar(value=True);self.remote=tk.BooleanVar(value=True)
        self.cards={};self.boxes={};self.switches={};self.meters={};self.labels={}
        for source,label,hint,var in [('本地','本地麦克风','选择与会议相同的麦克风；已启用回声消除和适度降噪',self.local),('远端','电脑声音','选择与会议相同的耳机或扬声器；录双方声音时两项都开启',self.remote)]:
            card=tk.Frame(body,bg=CARD,padx=14,pady=10,highlightthickness=1,highlightbackground='#DCE1DC');card.pack(fill='x',pady=5)
            row=tk.Frame(card,bg=CARD);row.pack(fill='x')
            check=tk.Checkbutton(row,text=label,variable=var,command=lambda s=source:self.toggle(s),
                font=('Microsoft YaHei UI',12,'bold'),bg=CARD,fg=INK,activebackground=CARD,selectcolor=CARD,highlightthickness=0)
            check.pack(side='left');self.switches[source]=check
            signal=tk.Label(row,text='待录音',bg=CARD,fg=MUTED,font=('Microsoft YaHei UI',9));signal.pack(side='right');self.labels[source]=signal
            tk.Label(card,text=hint,bg=CARD,fg=MUTED,font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(0,5))
            box=ttk.Combobox(card,state='readonly');box.pack(fill='x');self.boxes[source]=box
            meter=ttk.Progressbar(card,style='Level.Horizontal.TProgressbar',maximum=100);meter.pack(fill='x',pady=(9,0));self.meters[source]=meter
        footer=tk.Frame(body,bg=BG);footer.pack(fill='x',pady=(6,0))
        self.refresh_btn=tk.Button(footer,text='刷新设备',command=self.refresh,relief='flat',bg=BG,fg=GREEN,cursor='hand2',font=('Microsoft YaHei UI',9));self.refresh_btn.pack(side='left')
        tk.Label(footer,text='默认录双方声音，可整场录制，也可临时关闭一路。',bg=BG,fg=MUTED,font=('Microsoft YaHei UI',9)).pack(side='right')
        row=tk.Frame(body,bg=BG);row.pack(fill='x',pady=(14,8))
        self.main=tk.Button(row,text='开始录音',command=self.action,bg=GREEN,fg='white',activebackground='#194A39',activeforeground='white',relief='flat',font=('Microsoft YaHei UI',13,'bold'),cursor='hand2',height=2,width=20)
        self.main.pack(side='left')
        self.timer=tk.Label(row,text='00:00:00',font=('Consolas',24),bg=BG,fg=INK);self.timer.pack(side='left',padx=22)
        tk.Button(row,text='打开文件夹',command=lambda:os.startfile(self.folder),relief='flat',bg=BG,fg=GREEN,font=('Microsoft YaHei UI',10),cursor='hand2').pack(side='right')
        self.status=tk.StringVar(value='点击开始后才录音。结束后自动生成音频和转写。')
        tk.Label(body,textvariable=self.status,bg=BG,fg=MUTED,font=('Microsoft YaHei UI',10),wraplength=690,justify='left').pack(anchor='w',pady=(3,0))
        self.recover_btn=tk.Button(footer,text='恢复未完成录音',command=self.recover,relief='flat',bg=BG,fg=GREEN,font=('Microsoft YaHei UI',9))
        self.check_recovery()
        self.devices={};self.refresh();self.root.protocol('WM_DELETE_WINDOW',self.close);self.tick()

    def check_recovery(self):
        self.pending=core.pending_sessions(APP)
        if self.pending:
            self.recover_btn.configure(text=f'恢复未完成录音（{len(self.pending)}）')
            self.recover_btn.pack(side='left')
            self.status.set('发现未完成录音，可点击恢复。已保存的片段会重新生成音频和文字。')
        else:self.recover_btn.pack_forget()

    def recover(self):
        if self.state not in ('idle','error'):return
        self.check_recovery()
        if not self.pending:return
        self.job=self.pending[0];self.raw,self.folder,title=self.job
        try:self.notes.attach(self.raw,self.folder,title,recover=True)
        except Exception as exc:
            self.notes.show();messagebox.showerror('无法恢复记录',str(exc));return
        self.status.set('正在恢复：'+title);self.retry()

    def refresh(self):
        p=pa.PyAudio()
        try:
            try:
                from device_routing import auto_devices
                automatic=auto_devices()
            except Exception:automatic={}
            api=p.get_host_api_info_by_type(pa.paWASAPI)
            self.devices['本地']=[d for d in p.get_device_info_generator() if d['hostApi']==api['index'] and d['maxInputChannels']>0 and not d.get('isLoopbackDevice')]
            self.devices['远端']=list(p.get_loopback_device_info_generator())
            default_loop=p.get_default_wasapi_loopback()['index'] if self.devices['远端'] else -1
            for source in ('本地','远端'):
                choices=self.devices[source];names=[d['name'] for d in choices];box=self.boxes[source]
                old=box.get() or self.saved.get(source)
                if not box.get() and not self.saved.get('routing_v2'):old=None
                default=api['defaultInputDevice'] if source=='本地' else default_loop
                wanted,reason=automatic.get(source,('', '系统默认设备'))
                matched=[d for d in choices if d['name'].replace(' [Loopback]','')==wanted]
                automatic_device=matched[0] if len(matched)==1 else next((d for d in choices if d['index']==default),None)
                if automatic_device:
                    names=['自动 · '+(reason if matched else '系统默认设备')+'：'+automatic_device['name']]+names
                    self.devices[source]=[automatic_device]+choices
                if old is None or old.startswith('自动'):
                    old=names[0] if names else None
                box['values']=names
                if names:box.current(names.index(old) if old in names else 0)
        except Exception as e:self.status.set('设备读取失败：'+str(e))
        finally:p.terminate()

    def save(self):
        core.atomic(SETTINGS,json.dumps({**{s:b.get() for s,b in self.boxes.items()},'routing_v2':True},ensure_ascii=False))
        self.saved['routing_v2']=True

    def toggle(self,source):
        enabled=self.local.get() if source=='本地' else self.remote.get()
        if self.state=='recording' and self.recorder:
            if self.asr and self.asr.error:
                self.status.set('录音仍在保存；后台转写暂时失败，结束后将从已保存的进度重试。')
            if source not in dict(self.recorder.devices):
                (self.local if source=='本地' else self.remote).set(False)
                self.status.set('未连接这一路设备，请结束录音后重新选择。');return
            self.recorder.set_enabled(source,enabled)
            self.status.set(source+('已开启。' if enabled else '已关闭，该声道不保存声音。'))
        self.labels[source].configure(text='待录音' if enabled else '关闭')

    def controls(self,state):
        self.state=state;busy=state not in ('idle','error')
        self.import_btn.configure(state='disabled' if busy else 'normal')
        self.recover_btn.configure(state='disabled' if busy else 'normal')
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if busy else 0x80000000)
        for box in self.boxes.values():box.configure(state='disabled' if busy else 'readonly')
        self.name.configure(state='disabled' if busy else 'normal');self.refresh_btn.configure(state='disabled' if busy else 'normal')
        for switch in self.switches.values():switch.configure(state='normal' if state in ('idle','error','recording') else 'disabled')
        self.main.configure(state='normal' if state in ('idle','error','recording') else 'disabled',
            text={'idle':'开始录音','starting':'正在准备…','recording':'结束并转写','processing':'正在生成文件…','error':'重试转写'}[state],
            bg='#9E392E' if state=='recording' else GREEN)
        if hasattr(self,'tray'):self.tray.update()
        self.badge.configure(text={'idle':'●  待机','starting':'●  准备中','recording':'●  录音中','processing':'●  转写中','error':'●  未完成'}[state],fg='#9E392E' if state=='recording' else MUTED)

    def action(self):
        if self.state=='recording':self.stop()
        elif self.state=='error':self.retry()
        elif self.state=='idle':self.start()

    def import_audio(self):
        if self.state not in ('idle','error'):return
        selected=filedialog.askopenfilename(parent=self.root,title='选择需要转写的音频',filetypes=[
            ('音频文件','*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.aif *.amr'),('所有文件','*.*')])
        if not selected:return
        try:
            source=Path(selected)
            if not source.is_file() or source.stat().st_size==0:raise ValueError('请选择非空的音频文件。')
            self.raw,self.folder=core.new_session(source.stem)
            core.register_import(self.raw,source)
            self.job=(self.raw,self.folder,source.stem);self.title.set(source.stem)
            self.notes.attach(self.raw,self.folder,source.stem)
            self.timer.configure(text='00:00:00');self.status.set('正在导入音频并转写…')
            self.retry()
        except Exception as e:messagebox.showerror('无法导入',str(e))

    def start(self):
        try:
            self.refresh()
            enabled={'本地':self.local.get(),'远端':self.remote.get()}
            if not any(enabled.values()):raise ValueError('至少开启一路声音。')
            if shutil.disk_usage(core.BASE).free<1024**3:raise ValueError('D 盘可用空间不足 1 GB。')
            devices=[]
            for source,box in self.boxes.items():
                i=box.current();choices=self.devices.get(source,[])
                if 0<=i<len(choices):devices.append((source,choices[i]['index']))
                elif enabled[source]:raise ValueError('请选择'+source+'的设备。')
            self.save();self.raw,self.folder=core.new_session(self.title.get());title=self.title.get()
            self.notes.attach(self.raw,self.folder,title)
            core.atomic(self.raw/'设备.json',json.dumps({s:self.boxes[s].get() for s,_ in devices},ensure_ascii=False))
            core.atomic(self.raw/'audio_processing.json',json.dumps({'engine':'Reference linear echo reduction + WebRTC NS','version':2,'ns_level':0}))
            self.job=(self.raw,self.folder,title);self.controls('starting');self.status.set('正在打开录音设备…')
        except Exception as e:messagebox.showerror('无法开始',str(e));return
        def work():
            try:
                self.recorder=Recorder(self.raw,devices,enabled=enabled);self.recorder.start()
                self.asr=BackgroundASR(self.raw);self.asr.start()
                self.started=self.recorder.origin;self.events.put(('recording','正在录音并后台分段转写，完成的文字自动保存。可随时开关两路声音。'))
            except Exception:self.fail()
        threading.Thread(target=work,daemon=True).start()

    def stop(self):
        self.notes.flush()
        self.controls('processing');self.status.set('正在保存音频…')
        def work():
            try:
                self.recorder.stop();self.recorder=None;self.process()
            except Exception:self.fail()
        threading.Thread(target=work,daemon=True).start()

    def process(self):
        raw,out,title=self.job
        if self.asr:
            self.events.put(('status','正在完成剩余语音片段…'))
            self.asr.finish();self.asr=None
        result=core.process(raw,out,title,lambda text:self.events.put(('status',text)))
        self.failed_job=None;self.events.put(('done','已完成：MP3、WAV、转写.txt 和纪要.txt'))

    def retry(self):
        self.controls('processing')
        def work():
            try:self.process()
            except Exception:self.fail()
        threading.Thread(target=work,daemon=True).start()

    def fail(self):
        error=traceback.format_exc()
        if self.recorder:
            self.recorder.stop_event.set();self.recorder.thread.join(15);self.recorder=None
        if self.asr:self.asr.stop_background();self.asr=None
        self.failed_job=getattr(self,'job',None)
        if self.raw:core.atomic(self.raw/'error.txt',error)
        self.events.put(('error',error[-700:]))

    def tick(self):
        if hasattr(self,'tray'):self.tray.poll()
        for _ in range(30):
            try:kind,text=self.events.get_nowait()
            except queue.Empty:break
            if kind.startswith('tray_'):
                if kind=='tray_show':self.tray.show()
                elif kind=='tray_folder':os.startfile(self.folder)
                elif kind=='tray_quit':self.quit_app()
                elif kind=='tray_failed':
                    self.root.deiconify();self.status.set('托盘暂不可用，请保持窗口开启。')
                if self.exiting:return
                continue
            self.status.set(text)
            if kind=='recording':self.controls('recording')
            elif kind=='done':
                saved=self.notes.flush();self.controls('idle');self.check_recovery()
                if saved and not self.pending:
                    self.status.set('已完成：MP3、WAV、转写.txt'+(' 和纪要.txt' if (self.folder/'纪要.txt').exists() else ''))
                if not saved:
                    self.notes.show();self.status.set('音频和转写已完成，纪要保存失败，请检查简要记录窗口。')
            elif kind=='error':
                self.controls('error');self.status.set('未完成转写，原始音频已保留。可点击重试转写。')
                messagebox.showerror('处理未完成',text)
        if self.state=='recording' and self.recorder:
            self.timer.configure(text=core.stamp(time.monotonic()-self.started))
            for source in self.boxes:
                active=self.recorder.enabled.get(source,False);peak=self.recorder.levels.get(source,0) if active else 0
                self.meters[source]['value']=min(100,max(0,100+(20*np.log10(max(peak,1e-6)))/.6)) if active else 0
                self.labels[source].configure(text='关闭' if not active else ('音量过高' if peak>.98 else '录制中'))
            if self.recorder.error or shutil.disk_usage(core.BASE).free<250*1024**2:self.stop()
        self.root.after(150,self.tick)

    def close(self):
        if not self.notes.flush():self.notes.show();return
        if hasattr(self,'tray') and self.tray.ready.is_set():self.tray.hide()
        else:self.quit_app()

    def quit_app(self):
        if self.state in ('starting','recording','processing'):
            if hasattr(self,'tray'):self.tray.show()
            messagebox.showinfo('正在处理','请先结束录音并等待文件保存完成，再从托盘退出。')
        else:
            if not self.notes.flush():self.notes.show();return
            self.save()
            if hasattr(self,'tray'):self.tray.stop()
            self.exiting=True;self.root.destroy()

if __name__=='__main__':
    from tray_support import Tray,single_instance
    background='--background' in sys.argv
    handle,duplicate=single_instance(background)
    if not duplicate:
        try:
            root=tk.Tk()
            if background:root.withdraw()
            app=App(root);app.tray=Tray(app,APP)
            if not app.tray.start():
                root.deiconify();app.status.set('托盘暂不可用，已显示录音窗口。')
            app.tray.update();root.mainloop()
        except Exception:
            core.atomic(APP/'启动错误.txt',traceback.format_exc())
            ctypes.windll.user32.MessageBoxW(0,'无法启动，详情已保存到 _app\\启动错误.txt。','会议录音',16)
