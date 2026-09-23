import json,subprocess,sys,tempfile,unittest
from pathlib import Path
import numpy as np
import soundfile as sf

APP=Path(__file__).resolve().parents[1]/'_app'
sys.path.insert(0,str(APP))
from durable_audio import ChunkWriter,assemble,atomic,pending_sessions

class StorageTests(unittest.TestCase):
    def test_chunks_survive_process_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            code='''import sys,os,numpy as np
sys.path.insert(0,sys.argv[1])
from durable_audio import ChunkWriter
w=ChunkWriter(sys.argv[2],16000)
w.write(np.full(16000*7,.1,dtype=np.float32))
os._exit(19)
'''
            folder=Path(temp)/'chunks'
            result=subprocess.run([sys.executable,'-c',code,str(APP),str(folder)])
            self.assertEqual(result.returncode,19)
            (folder/'00000002.partial').write_bytes(b'unfinished')
            restored=assemble(folder,Path(temp)/'restored.wav')
            self.assertEqual(sf.info(restored).frames,16000*6)

    def test_missing_chunk_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'chunks';writer=ChunkWriter(folder,16000)
            writer.write(np.zeros(16000*6,np.float32));writer.close()
            (folder/'00000000.flac').unlink()
            with self.assertRaises(RuntimeError):assemble(folder,Path(temp)/'restored.wav')

    def test_import_recovery_before_audio_conversion(self):
        with tempfile.TemporaryDirectory() as temp:
            app=Path(temp);raw=app/'recordings'/'2000-01-01'/'test';raw.mkdir(parents=True)
            atomic(raw/'session.json',json.dumps({'title':'test','output':str(app/'output')}))
            atomic(raw/'import.json','{}')
            self.assertEqual(len(pending_sessions(app)),1)
            atomic(raw/'result.json','{}')
            self.assertEqual(pending_sessions(app),[])

if __name__=='__main__':unittest.main()
