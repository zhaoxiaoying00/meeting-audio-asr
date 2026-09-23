"""Read-only endpoint selection, with physical fallback after unplugging."""
import warnings
from pycaw.pycaw import AudioUtilities
from pycaw.utils import AudioSession
from pycaw.api.audiopolicy import IAudioSessionControl2

def physical(name):
    lower=name.lower()
    return not any(word in lower for word in ('virtual','虚拟','todesk','vb-audio','voicemeeter',
        'cable input','cable output','display audio','hdmi','digital audio','数字音频'))

def choose_endpoint(devices,communication=None,multimedia=None,playback=False):
    if not devices:return None
    meeting=[d for d in devices if d.get('conference')]
    active=[d for d in devices if d.get('active')]
    if len(meeting)==1:return meeting[0]['name'],'会议设备'
    if playback and len(active)==1:return active[0]['name'],'正在播放声音的设备'
    candidates=meeting or (active if playback and active else [])
    if candidates:
        for default in (communication,multimedia):
            if any(d['name']==default for d in candidates):return default,'活动音频设备'
    hardware=[d for d in devices if physical(d['name'])]
    for default in (communication,multimedia):
        if any(d['name']==default for d in hardware):return default,'系统通信设备' if default==communication else '系统播放设备'
    if hardware:
        preferred=next((d for d in hardware if any(k in d['name'].lower() for k in
            ('麦克风阵列','microphone array','扬声器','speakers'))),hardware[0])
        return preferred['name'],'自动选择实体设备'
    for default in (communication,multimedia):
        if any(d['name']==default for d in devices):return default,'系统默认设备（请检查声音）'
    return devices[0]['name'],'可用设备（请检查声音）'

def auto_devices():
    result={};enumerator=AudioUtilities.GetDeviceEnumerator()
    for label,flow in [('本地',1),('远端',0)]:
        devices=[];defaults={}
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for role in (1,2):
                try:defaults[role]=AudioUtilities.CreateDevice(enumerator.GetDefaultAudioEndpoint(flow,role)).FriendlyName
                except Exception:defaults[role]=None
            for device in AudioUtilities.GetAllDevices(data_flow=flow,device_state=1):
                item={'name':device.FriendlyName,'conference':False,'active':False};devices.append(item)
                try:
                    sessions=device.AudioSessionManager.GetSessionEnumerator()
                    for index in range(sessions.GetCount()):
                        session=AudioSession(sessions.GetSession(index).QueryInterface(IAudioSessionControl2))
                        process=session.Process
                        name=process.name().lower() if process else ''
                        # Do not treat the recorder's silent output clock as real playback.
                        if session.State!=1 or not name or name in ('python.exe','pythonw.exe'):continue
                        item['active']=True
                        if 'wemeet' in name or 'dingtalk' in name:item['conference']=True
                except Exception:continue
        chosen=choose_endpoint(devices,defaults.get(2),defaults.get(1),playback=flow==0)
        if chosen:result[label]=chosen
    return result
