import os,json,threading,ctypes
from ctypes import wintypes
from pathlib import Path
import pystray
from PIL import Image,ImageDraw

KERNEL=ctypes.WinDLL('kernel32',use_last_error=True)
KERNEL.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
KERNEL.CreateMutexW.restype=wintypes.HANDLE
KERNEL.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
KERNEL.CreateEventW.restype=wintypes.HANDLE
KERNEL.OpenEventW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR]
KERNEL.OpenEventW.restype=wintypes.HANDLE
KERNEL.SetEvent.argtypes=[wintypes.HANDLE]
KERNEL.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
KERNEL.CloseHandle.argtypes=[wintypes.HANDLE]
SHOW='Local\\XiaoyingMeetingASRShow'

def single_instance(background):
    mutex=KERNEL.CreateMutexW(None,False,'Local\\XiaoyingMeetingASR')
    duplicate=ctypes.get_last_error()==183
    if duplicate and not background:
        event=KERNEL.OpenEventW(2,False,SHOW)
        if event:KERNEL.SetEvent(event);KERNEL.CloseHandle(event)
        else:ctypes.windll.user32.MessageBoxW(0,'旧版窗口仍在运行。请结束当前录音并关闭旧窗口后，再打开新版。','会议录音',0)
    return mutex,duplicate

def tray_image(recording=False):
    image=Image.new('RGBA',(64,64),(0,0,0,0));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((2,2,62,62),radius=15,fill='#9E392E' if recording else '#22604B')
    draw.rounded_rectangle((25,12,39,36),radius=7,fill='white')
    draw.arc((18,21,46,47),0,180,fill='white',width=4)
    draw.line((32,46,32,53),fill='white',width=4);draw.line((24,53,40,53),fill='white',width=4)
    return image

class Tray:
    def __init__(self,app,appdir):
        self.app=app;self.path=Path(appdir)/'runtime-status.json';self.ready=threading.Event()
        self.show_event=KERNEL.CreateEventW(None,False,False,SHOW)
        def post(command):return lambda icon,item:app.events.put((command,''))
        self.icon=pystray.Icon('xiaoying-meeting-recorder',tray_image(),'会议录音 · 待机',
            pystray.Menu(pystray.MenuItem('打开录音窗口',post('tray_show'),default=True),
                pystray.MenuItem('打开保存文件夹',post('tray_folder')),pystray.Menu.SEPARATOR,
                pystray.MenuItem('退出',post('tray_quit'))))

    def start(self):
        def setup(icon):icon.visible=True;self.ready.set()
        def run():
            try:self.icon.run(setup=setup)
            except Exception as e:self.app.events.put(('tray_failed',str(e)))
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
        return self.ready.wait(3)

    def update(self):
        states={'idle':'待机','starting':'准备中','recording':'录音中','processing':'转写中','error':'转写未完成'}
        self.icon.title='会议录音 · '+states.get(self.app.state,self.app.state)
        self.icon.icon=tray_image(self.app.state=='recording')
        info={'pid':os.getpid(),'state':self.app.state,'window_visible':self.app.root.state()!='withdrawn',
            'tray_ready':self.ready.is_set(),'recording':self.app.recorder is not None and self.app.state=='recording'}
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(info,ensure_ascii=False),encoding='utf-8');temp.replace(self.path)

    def poll(self):
        if self.show_event and KERNEL.WaitForSingleObject(self.show_event,0)==0:self.show()

    def show(self):
        self.app.root.deiconify();self.app.root.lift();self.app.root.focus_force();self.update()

    def hide(self):
        self.app.save();self.app.root.withdraw();self.update()

    def stop(self):
        self.icon.stop()
        if self.show_event:KERNEL.CloseHandle(self.show_event);self.show_event=None
        self.path.write_text(json.dumps({'pid':os.getpid(),'state':'exited','recording':False}),encoding='utf-8')
