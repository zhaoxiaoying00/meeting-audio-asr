"""Read-only routing: active conference endpoints, else Windows communications defaults."""
import warnings
from pycaw.pycaw import AudioUtilities
from pycaw.utils import AudioSession
from pycaw.api.audiopolicy import IAudioSessionControl2

def auto_devices():
    result={};enumerator=AudioUtilities.GetDeviceEnumerator()
    for label,flow in [('本地',1),('远端',0)]:
        active=[]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for device in AudioUtilities.GetAllDevices(data_flow=flow,device_state=1):
                try:
                    sessions=device.AudioSessionManager.GetSessionEnumerator()
                    for index in range(sessions.GetCount()):
                        session=AudioSession(sessions.GetSession(index).QueryInterface(IAudioSessionControl2))
                        process=session.Process
                        name=process.name().lower() if process else ''
                        if session.State==1 and ('wemeet' in name or 'dingtalk' in name):
                            active.append(device.FriendlyName);break
                except Exception:continue
            if len(set(active))==1:result[label]=(active[0],'会议设备')
            else:
                endpoint=enumerator.GetDefaultAudioEndpoint(flow,2)
                result[label]=(AudioUtilities.CreateDevice(endpoint).FriendlyName,'系统通信设备')
    return result
