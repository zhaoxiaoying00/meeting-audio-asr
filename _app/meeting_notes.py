from pathlib import Path
import json
import tkinter as tk
from tkinter import scrolledtext
from durable_audio import atomic

class MeetingNotes:
    def __init__(self,parent,appdir):
        self.parent=parent;self.draft=Path(appdir)/'notes-draft.json'
        self.raw=None;self.out=None;self.title='会议';self.pending=None
        self.window=tk.Toplevel(parent);self.window.withdraw()
        self.window.title('简要记录');self.window.geometry('580x400');self.window.minsize(380,260)
        tk.Label(self.window,text='记录要点、决定和待办，自动保存到本场会议的“纪要.txt”。',anchor='w',
            font=('Microsoft YaHei UI',10),wraplength=540).pack(fill='x',padx=14,pady=(12,8))
        self.editor=scrolledtext.ScrolledText(self.window,wrap='word',undo=True,font=('Microsoft YaHei UI',11),padx=10,pady=8)
        self.editor.pack(fill='both',expand=True,padx=14)
        self.status=tk.StringVar(value='录音前也可先写，开始后归入本场会议。')
        tk.Label(self.window,textvariable=self.status,anchor='w',font=('Microsoft YaHei UI',9)).pack(fill='x',padx=14,pady=10)
        try:
            data=json.loads(self.draft.read_text(encoding='utf-8-sig'))
            if data.get('raw') and Path(data['raw']).is_dir():
                self.raw=Path(data['raw']);self.out=Path(data['out']);self.title=data.get('title','会议')
            text=data.get('text','')
            if self.raw and (self.raw/'手记.txt').exists():text=(self.raw/'手记.txt').read_text(encoding='utf-8-sig')
            self.editor.insert('1.0',text)
        except (OSError,ValueError,KeyError):pass
        self.editor.edit_modified(False);self.editor.bind('<<Modified>>',self.changed)
        if self.raw:self.window.title('简要记录 · '+self.title)
        self.window.protocol('WM_DELETE_WINDOW',self.hide)

    def changed(self,event=None):
        if not self.editor.edit_modified():return
        self.editor.edit_modified(False);self.status.set('正在保存…')
        if self.pending is None:self.pending=self.parent.after(1000,self.flush)

    def flush(self):
        if self.pending is not None:self.parent.after_cancel(self.pending);self.pending=None
        text=self.editor.get('1.0','end-1c')
        try:
            if self.raw:
                atomic(self.raw/'手记.txt',text)
                if text.strip():
                    self.out.mkdir(parents=True,exist_ok=True)
                    atomic(self.out/'纪要.txt',text)
                else:
                    (self.out/'纪要.txt').unlink(missing_ok=True)
            atomic(self.draft,json.dumps({'raw':str(self.raw) if self.raw else None,
                'out':str(self.out) if self.out else None,'title':self.title,'text':text},ensure_ascii=False))
            self.status.set(('已自动保存到纪要.txt' if text.strip() else '未填写纪要，不生成纪要.txt') if self.raw else '草稿已保存，开始后归入本场会议。')
            return True
        except OSError as exc:
            self.status.set('保存失败，请保留窗口并检查磁盘：'+str(exc))
            self.pending=self.parent.after(2000,self.flush)
            return False

    def attach(self,raw,out,title,recover=False):
        if not self.flush():raise RuntimeError('简要记录尚未保存，请检查磁盘后重试。')
        text=self.editor.get('1.0','end-1c') if self.raw is None else ''
        raw=Path(raw)
        if recover:text=(raw/'手记.txt').read_text(encoding='utf-8-sig') if (raw/'手记.txt').exists() else ''
        self.raw=raw;self.out=Path(out);self.title=title
        self.editor.delete('1.0','end');self.editor.insert('1.0',text);self.editor.edit_modified(False)
        self.window.title('简要记录 · '+title)
        if not self.flush():raise RuntimeError('无法保存本场会议的简要记录。')

    def show(self):
        self.window.deiconify();self.window.lift();self.editor.focus_set()

    def hide(self):
        if self.flush():self.window.withdraw()
