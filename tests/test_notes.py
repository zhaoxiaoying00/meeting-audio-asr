import sys,tempfile,unittest
from pathlib import Path
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'_app'))
from meeting_notes import MeetingNotes

class NotesTests(unittest.TestCase):
    def setUp(self):
        try:self.root=tk.Tk()
        except tk.TclError as exc:self.skipTest(str(exc))
        self.root.withdraw();self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.notes=MeetingNotes(self.root,self.base)

    def tearDown(self):
        self.root.destroy();self.temp.cleanup()

    def attach(self,name):
        raw=self.base/(name+'-raw');out=self.base/(name+'-out');raw.mkdir();out.mkdir()
        self.notes.attach(raw,out,name)
        return raw,out

    def test_empty_notes_omitted_and_cleared(self):
        raw,out=self.attach('first');path=out/'纪要.txt'
        self.assertFalse(path.exists())
        self.notes.editor.insert('end',' \n\t');self.notes.flush()
        self.assertFalse(path.exists())
        self.notes.editor.insert('end','决定：周五完成测试');self.notes.flush()
        self.assertIn('周五',path.read_text(encoding='utf-8-sig'))
        self.notes.editor.delete('1.0','end');self.notes.flush()
        self.assertFalse(path.exists())

    def test_session_isolation_and_recovery(self):
        self.notes.editor.insert('end','会前要点')
        raw,out=self.attach('first')
        self.assertEqual((out/'纪要.txt').read_text(encoding='utf-8-sig'),'会前要点')
        _,second=self.attach('second')
        self.assertFalse((second/'纪要.txt').exists())
        self.notes.attach(raw,out,'first',recover=True)
        self.assertEqual(self.notes.editor.get('1.0','end-1c'),'会前要点')

if __name__=='__main__':unittest.main()
